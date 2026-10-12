from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .db import database
from .media import CREATE_NO_WINDOW
from .settings import Settings


PREVIEW_SECONDS = 45.0


def preview_window(duration_sec: float, sample_seconds: float = PREVIEW_SECONDS) -> tuple[float, float]:
    duration = max(0.0, float(duration_sec))
    length = min(duration, sample_seconds)
    return max(0.0, (duration - length) / 2.0), length


def preview_path(settings: Settings, video_id: int) -> Path:
    return settings.vod_preview_dir / f"vod-{video_id}.mp4"


def ready_preview(settings: Settings, video_id: int, source_path: str, source_mtime_ns: int) -> Path | None:
    path = preview_path(settings, video_id)
    if not path.exists() or path.stat().st_size == 0:
        return None
    # The library scan already records source mtime. Avoid touching every large
    # VOD on E: during /api/videos; that made the entire library request stall.
    if path.stat().st_mtime_ns < int(source_mtime_ns):
        return None
    return path


def generate_vod_preview(
    settings: Settings,
    video_id: int,
    progress: Callable[[str], None] | None = None,
) -> Path:
    with database(settings.db_path) as connection:
        row = connection.execute(
            "SELECT path, duration_sec, mtime_ns FROM videos WHERE id=? AND duplicate_of IS NULL",
            (video_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"VOD {video_id} was not found")

    cached = ready_preview(settings, video_id, row["path"], row["mtime_ns"])
    if cached:
        return cached

    source = Path(row["path"])
    if not source.exists():
        raise FileNotFoundError(source)
    start, length = preview_window(row["duration_sec"])
    if length <= 0:
        raise ValueError("This VOD has no playable duration")

    settings.vod_preview_dir.mkdir(parents=True, exist_ok=True)
    destination = preview_path(settings, video_id)
    temporary = destination.with_suffix(".working.mp4")
    if progress:
        progress("Preparing VOD preview: extracting the middle 45 seconds")
    command = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{length:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:720",
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "27",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(temporary),
    ]
    try:
        subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    if progress:
        progress("VOD preview ready")
    return destination
