from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .db import database
from .settings import Settings


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".ts", ".m4v", ".flv"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(slots=True)
class InventoryResult:
    discovered: int
    probed: int
    duplicates: int
    errors: int
    total_bytes: int
    unique_bytes: int
    total_duration_sec: float
    unique_duration_sec: float


def iter_videos(root: Path) -> Iterable[Path]:
    for current_root, _, filenames in os.walk(root):
        for filename in filenames:
            path = Path(current_root) / filename
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                yield path


def _path_is_within(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def quick_fingerprint(path: Path, block_size: int = 1024 * 1024) -> str:
    stat = path.stat()
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(stat.st_size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(block_size))
        if stat.st_size > block_size:
            handle.seek(max(0, stat.st_size - block_size))
            digest.update(handle.read(block_size))
    return digest.hexdigest()


def _parse_fraction(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator or 1)
    return float(value)


def normalize_chapters(payload: dict, duration_sec: float = 0.0) -> list[dict]:
    """Return stable OBS/QuickTime chapter metadata from ffprobe JSON."""
    chapters = []
    for index, chapter in enumerate(payload.get("chapters") or []):
        try:
            start = float(chapter.get("start_time", chapter.get("start", 0)) or 0)
            end = float(chapter.get("end_time", chapter.get("end", start)) or start)
        except (TypeError, ValueError):
            continue
        if duration_sec > 0 and (start < 0 or start > duration_sec + 1):
            continue
        title = str((chapter.get("tags") or {}).get("title") or f"Marker {index + 1}").strip()
        chapters.append({
            "index": index,
            "start_sec": round(max(0.0, start), 3),
            "end_sec": round(max(start, end), 3),
            "title": title[:160],
        })
    return chapters

def probe_video(path: Path, ffprobe: str) -> dict:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,format_name:"
            "chapter=id,start,start_time,end,end_time:"
            "chapter_tags=title:"
            "stream=index,codec_type,codec_name,channels,channel_layout,"
            "sample_rate,width,height,avg_frame_rate,r_frame_rate:"
            "stream_tags=title"
        ),
        "-of",
        "json",
        "--",
        str(path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_streams = []
    for ordinal, stream in enumerate(
        stream for stream in streams if stream.get("codec_type") == "audio"
    ):
        audio_streams.append(
            {
                "ordinal": ordinal,
                "index": stream.get("index"),
                "codec": stream.get("codec_name"),
                "channels": stream.get("channels"),
                "layout": stream.get("channel_layout"),
                "sample_rate": int(stream.get("sample_rate") or 0),
                "title": (stream.get("tags") or {}).get("title", ""),
            }
        )
    format_info = payload.get("format", {})
    duration_sec = float(format_info.get("duration") or 0)
    return {
        "duration_sec": duration_sec,
        "container": format_info.get("format_name", ""),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": _parse_fraction(
            video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        ),
        "video_codec": video_stream.get("codec_name", ""),
        "audio_streams": audio_streams,
        "chapters": normalize_chapters(payload, duration_sec),
    }


def scan_library(
    settings: Settings,
    progress: Callable[[str], None] | None = None,
) -> InventoryResult:
    root = Path(settings.source_root)
    if not root.exists():
        raise FileNotFoundError(f"Source folder does not exist: {root}")
    paths = sorted(iter_videos(root), key=lambda item: str(item).casefold())
    probed = 0
    errors = 0
    with database(settings.db_path) as connection:
        for index, path in enumerate(paths, start=1):
            stat = path.stat()
            existing = connection.execute(
                "SELECT id, size_bytes, mtime_ns, status, chapters_json FROM videos WHERE path = ?", (str(path),)
            ).fetchone()
            if (
                existing
                and existing["status"] == "ready"
                and existing["size_bytes"] == stat.st_size
                and existing["mtime_ns"] == stat.st_mtime_ns
                and existing["chapters_json"] is not None
            ):
                if progress:
                    progress(f"[{index}/{len(paths)}] unchanged: {path.name}")
                continue
            try:
                metadata = probe_video(path, settings.ffprobe)
                fingerprint = quick_fingerprint(path)
                connection.execute(
                    """
                    INSERT INTO videos(
                        path, rel_path, name, size_bytes, mtime_ns, quick_hash,
                        duration_sec, container, width, height, fps, video_codec,
                        audio_streams_json, chapters_json, status, last_error, probed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', NULL, CURRENT_TIMESTAMP)
                    ON CONFLICT(path) DO UPDATE SET
                        rel_path=excluded.rel_path,
                        name=excluded.name,
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        quick_hash=excluded.quick_hash,
                        duration_sec=excluded.duration_sec,
                        container=excluded.container,
                        width=excluded.width,
                        height=excluded.height,
                        fps=excluded.fps,
                        video_codec=excluded.video_codec,
                        audio_streams_json=excluded.audio_streams_json,
                        chapters_json=excluded.chapters_json,
                        status='ready',
                        last_error=NULL,
                        probed_at=CURRENT_TIMESTAMP
                    """,
                    (
                        str(path),
                        str(path.relative_to(root)),
                        path.name,
                        stat.st_size,
                        stat.st_mtime_ns,
                        fingerprint,
                        metadata["duration_sec"],
                        metadata["container"],
                        metadata["width"],
                        metadata["height"],
                        metadata["fps"],
                        metadata["video_codec"],
                        json.dumps(metadata["audio_streams"]),
                        json.dumps(metadata["chapters"]),
                    ),
                )
                probed += 1
                if progress:
                    progress(
                        f"[{index}/{len(paths)}] probed: {path.name} "
                        f"({len(metadata['audio_streams'])} audio, {len(metadata['chapters'])} markers)"
                    )
            except Exception as exc:
                errors += 1
                connection.execute(
                    """
                    INSERT INTO videos(
                        path, rel_path, name, size_bytes, mtime_ns, status, last_error
                    ) VALUES (?, ?, ?, ?, ?, 'error', ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        status='error',
                        last_error=excluded.last_error
                    """,
                    (
                        str(path),
                        str(path.relative_to(root)),
                        path.name,
                        stat.st_size,
                        stat.st_mtime_ns,
                        str(exc),
                    ),
                )
                if progress:
                    progress(f"[{index}/{len(paths)}] ERROR: {path.name}: {exc}")

        # Only reconcile rows belonging to the currently selected source root.
        # Libraries from other configured roots stay intact and reappear when
        # the user switches back to that folder.
        live_paths = {str(path) for path in paths}
        root_resolved = root.resolve(strict=False)
        stored_rows = connection.execute(
            "SELECT id, path FROM videos"
        ).fetchall()
        active_rows = [row for row in stored_rows if _path_is_within(row["path"], root_resolved)]
        missing_ids = [int(row["id"]) for row in active_rows if row["path"] not in live_paths]
        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            # Old global duplicate detection could point rows from another
            # library at a VOD being removed. Clear those references first.
            connection.execute(
                f"UPDATE videos SET duplicate_of=NULL WHERE duplicate_of IN ({placeholders})",
                missing_ids,
            )
            connection.execute(
                f"DELETE FROM videos WHERE id IN ({placeholders})",
                missing_ids,
            )

        active_rows = [
            row for row in connection.execute(
                "SELECT id, path, quick_hash, status FROM videos"
            ).fetchall()
            if _path_is_within(row["path"], root_resolved)
        ]
        active_ids = [int(row["id"]) for row in active_rows]
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            connection.execute(
                f"UPDATE videos SET duplicate_of=NULL WHERE id IN ({placeholders})",
                active_ids,
            )
            groups: dict[str, list] = {}
            for row in active_rows:
                if row["quick_hash"] and row["status"] == "ready":
                    groups.setdefault(str(row["quick_hash"]), []).append(row)
            for rows in groups.values():
                if len(rows) < 2:
                    continue
                rows.sort(key=lambda item: (len(str(item["path"])), str(item["path"]).casefold()))
                canonical_id = int(rows[0]["id"])
                for duplicate in rows[1:]:
                    connection.execute(
                        "UPDATE videos SET duplicate_of=? WHERE id=?",
                        (canonical_id, int(duplicate["id"])),
                    )

            summary = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS discovered,
                    SUM(CASE WHEN duplicate_of IS NOT NULL THEN 1 ELSE 0 END) AS duplicates,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                    COALESCE(SUM(size_bytes), 0) AS total_bytes,
                    COALESCE(SUM(CASE WHEN duplicate_of IS NULL THEN size_bytes ELSE 0 END), 0) AS unique_bytes,
                    COALESCE(SUM(duration_sec), 0) AS total_duration,
                    COALESCE(SUM(CASE WHEN duplicate_of IS NULL THEN duration_sec ELSE 0 END), 0) AS unique_duration
                FROM videos WHERE id IN ({placeholders})
                """,
                active_ids,
            ).fetchone()
        else:
            summary = {
                "discovered": 0, "duplicates": 0, "errors": 0,
                "total_bytes": 0, "unique_bytes": 0,
                "total_duration": 0.0, "unique_duration": 0.0,
            }
    return InventoryResult(
        discovered=summary["discovered"],
        probed=probed,
        duplicates=summary["duplicates"] or 0,
        errors=summary["errors"] or 0,
        total_bytes=summary["total_bytes"],
        unique_bytes=summary["unique_bytes"],
        total_duration_sec=summary["total_duration"],
        unique_duration_sec=summary["unique_duration"],
    )
