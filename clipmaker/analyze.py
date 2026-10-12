from __future__ import annotations

import json
import math
import re
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import database
from .media import CREATE_NO_WINDOW
from .settings import Settings


FRAME_PATTERN = re.compile(
    r"frame:(?P<frame>\d+).*?pts_time:(?P<time>-?\d+(?:\.\d+)?)"
    r".*?lavfi\.astats\.Overall\.RMS_level=(?P<db>-?inf|-?\d+(?:\.\d+)?)",
    re.DOTALL,
)

MIN_CLIP_SECONDS = 30.0
MAX_CLIP_SECONDS = 90.0


@dataclass(slots=True)
class TrackFeatures:
    ordinal: int
    stream_index: int
    frames: list[float]
    median_db: float
    p95_db: float
    active_ratio: float


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return -96.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def analyze_track(
    settings: Settings,
    source: Path,
    audio_ordinal: int,
    stream_index: int,
    duration_sec: float,
    progress: Callable[[str], None] | None = None,
) -> TrackFeatures:
    filter_graph = (
        "aresample=8000,asetnsamples=n=8000:p=1,"
        "astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-nostats",
        "-v",
        "error",
        "-i",
        str(source),
        "-map",
        f"0:a:{audio_ordinal}",
        "-af",
        filter_graph,
        "-f",
        "null",
        "-",
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
    frames = [-96.0] * max(1, math.ceil(duration_sec))
    for match in FRAME_PATTERN.finditer(result.stdout):
        second = max(0, int(float(match.group("time"))))
        if second >= len(frames):
            frames.extend([-96.0] * (second - len(frames) + 1))
        value = match.group("db")
        frames[second] = -96.0 if "inf" in value else max(-96.0, float(value))
    finite = [value for value in frames if value > -95.5]
    median_db = statistics.median(finite) if finite else -96.0
    p95_db = _percentile(finite, 0.95)
    active_ratio = (
        sum(value > max(-55.0, median_db - 8.0) for value in frames) / len(frames)
        if frames
        else 0.0
    )
    if progress:
        progress(
            f"track {audio_ordinal}: median {median_db:.1f} dB, "
            f"p95 {p95_db:.1f} dB, active {active_ratio:.0%}"
        )
    return TrackFeatures(
        ordinal=audio_ordinal,
        stream_index=stream_index,
        frames=frames,
        median_db=median_db,
        p95_db=p95_db,
        active_ratio=active_ratio,
    )


def _track_score(track: TrackFeatures, second: int) -> float:
    value = track.frames[second] if second < len(track.frames) else -96.0
    deviations = [abs(frame - track.median_db) for frame in track.frames if frame > -95.5]
    mad = statistics.median(deviations) if deviations else 1.0
    scale = max(2.5, mad * 1.4826)
    relative = max(0.0, (value - track.median_db - 3.0) / scale)
    absolute = max(0.0, min(1.0, (value + 42.0) / 24.0))
    return min(1.0, 0.68 * (relative / 4.0) + 0.32 * absolute)


def _candidate_timeline(
    tracks: list[TrackFeatures],
    duration_sec: float,
) -> list[tuple[int, float, dict[str, float]]]:
    seconds = max(1, math.ceil(duration_sec))
    combined: list[tuple[int, float, dict[str, float]]] = []
    for second in range(seconds):
        scores = {
            str(track.ordinal): round(_track_score(track, second), 4) for track in tracks
        }
        values = sorted(scores.values(), reverse=True)
        best = values[0] if values else 0.0
        support = values[1] if len(values) > 1 else 0.0
        combined.append((second, min(1.0, best + support * 0.18), scores))

    smoothed: list[tuple[int, float, dict[str, float]]] = []
    for second, _, scores in combined:
        left = max(0, second - 3)
        right = min(seconds, second + 4)
        neighborhood = [combined[index][1] for index in range(left, right)]
        score = max(neighborhood) * 0.7 + statistics.fmean(neighborhood) * 0.3
        smoothed.append((second, score, scores))
    return smoothed


def _candidate_peaks(
    tracks: list[TrackFeatures],
    duration_sec: float,
    max_candidates: int,
    timeline: list[tuple[int, float, dict[str, float]]] | None = None,
) -> list[tuple[int, float, dict[str, float]]]:
    smoothed = timeline or _candidate_timeline(tracks, duration_sec)
    desired = min(max_candidates, max(6, math.ceil(duration_sec / 3600) * 18))
    chosen: list[tuple[int, float, dict[str, float]]] = []
    for peak in sorted(smoothed, key=lambda item: item[1], reverse=True):
        if peak[1] < 0.18:
            break
        if any(abs(peak[0] - existing[0]) < 45 for existing in chosen):
            continue
        chosen.append(peak)
        if len(chosen) >= desired:
            break
    return sorted(chosen, key=lambda item: item[1], reverse=True)


def _dynamic_moment_window(
    scores: list[float],
    peak_second: int,
    duration_sec: float,
) -> tuple[float, float]:
    """Find the setup and landing around a reaction, bounded to a reel-safe length."""
    if duration_sec <= MIN_CLIP_SECONDS:
        return 0.0, max(0.0, duration_sec)

    last_second = min(len(scores) - 1, max(0, math.ceil(duration_sec) - 1))
    peak = max(0, min(peak_second, last_second))
    local_peak = max(scores[max(0, peak - 2) : min(len(scores), peak + 3)] or [0.0])
    active_threshold = max(0.16, local_peak * 0.36)

    # Join nearby bursts into one comedic beat. Brief pauses often separate the
    # setup, reaction, and friends piling on, so they should not split the clip.
    left = peak
    quiet = 0
    for second in range(peak - 1, max(-1, peak - 61), -1):
        if scores[second] >= active_threshold:
            left = second
            quiet = 0
        else:
            quiet += 1
            if quiet > 8:
                break

    right = peak
    quiet = 0
    for second in range(peak + 1, min(len(scores), peak + 71)):
        if scores[second] >= active_threshold:
            right = second
            quiet = 0
        else:
            quiet += 1
            if quiet > 10:
                break

    # Laughter is usually the landing, not the beginning. Keep a larger handle
    # before the reaction so the line or mistake that caused it survives.
    start = max(0.0, float(left) - 20.0)
    end = min(duration_sec, float(right) + 16.0)
    natural_length = end - start
    target_length = min(MAX_CLIP_SECONDS, max(MIN_CLIP_SECONDS, natural_length))

    if natural_length > MAX_CLIP_SECONDS:
        # Keep enough context before the peak while favoring the aftermath.
        start = float(peak) - 34.0
        end = start + MAX_CLIP_SECONDS
    elif natural_length < MIN_CLIP_SECONDS:
        missing = MIN_CLIP_SECONDS - natural_length
        start -= missing * 0.45
        end += missing * 0.55

    # Shift at the ends of a VOD instead of silently shortening the result.
    start = max(0.0, start)
    end = min(duration_sec, end)
    if end - start < target_length:
        if start <= 0.0:
            end = min(duration_sec, target_length)
        elif end >= duration_sec:
            start = max(0.0, duration_sec - target_length)
    return round(start, 3), round(end, 3)


def _chapter_window(chapter_time: float, duration_sec: float) -> tuple[float, float]:
    """Keep context before a user flag and a short reaction tail after it."""
    if duration_sec <= 0:
        return 0.0, 0.0
    target = min(60.0, duration_sec)
    start = max(0.0, float(chapter_time) - 45.0)
    end = min(duration_sec, float(chapter_time) + 15.0)
    if end - start < target:
        if start <= 0:
            end = min(duration_sec, target)
        elif end >= duration_sec:
            start = max(0.0, duration_sec - target)
    return round(start, 3), round(end, 3)


def _selected_chapters(chapters: list[dict], indices: list[int] | None) -> list[dict]:
    if indices is None:
        return chapters[:100]
    selected = {int(item) for item in indices}
    return [chapter for chapter in chapters if int(chapter.get("index", -1)) in selected][:100]

def analyze_video(
    settings: Settings,
    video_id: int,
    max_candidates: int = 50,
    progress: Callable[[str], None] | None = None,
    chapter_indices: list[int] | None = None,
) -> int:
    with database(settings.db_path) as connection:
        row = connection.execute(
            "SELECT * FROM videos WHERE id = ? AND duplicate_of IS NULL", (video_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Video {video_id} does not exist or is a duplicate")
        audio_streams = json.loads(row["audio_streams_json"])
        chapters = _selected_chapters(json.loads(row["chapters_json"] or "[]"), chapter_indices)
        if not audio_streams:
            raise ValueError(f"Video {video_id} has no audio streams")
        connection.execute(
            "UPDATE videos SET status = 'analyzing', last_error = NULL WHERE id = ?",
            (video_id,),
        )

    try:
        tracks = []
        for stream in audio_streams:
            track = analyze_track(
                settings,
                Path(row["path"]),
                int(stream["ordinal"]),
                int(stream["index"]),
                float(row["duration_sec"]),
                progress,
            )
            tracks.append(track)
            with database(settings.db_path) as connection:
                connection.execute(
                    """
                    INSERT INTO audio_features(
                        video_id, audio_ordinal, stream_index, frames_json,
                        median_db, p95_db, active_ratio, analyzed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(video_id, audio_ordinal) DO UPDATE SET
                        stream_index=excluded.stream_index,
                        frames_json=excluded.frames_json,
                        median_db=excluded.median_db,
                        p95_db=excluded.p95_db,
                        active_ratio=excluded.active_ratio,
                        analyzed_at=CURRENT_TIMESTAMP
                    """,
                    (
                        video_id,
                        track.ordinal,
                        track.stream_index,
                        json.dumps(track.frames, separators=(",", ":")),
                        track.median_db,
                        track.p95_db,
                        track.active_ratio,
                    ),
                )

        duration_sec = float(row["duration_sec"])
        timeline = _candidate_timeline(tracks, duration_sec)
        peaks = _candidate_peaks(tracks, duration_sec, max_candidates, timeline)
        moment_scores = [item[1] for item in timeline]
        with database(settings.db_path) as connection:
            connection.execute(
                "DELETE FROM candidates WHERE video_id = ? AND status = 'pending'",
                (video_id,),
            )
            chapter_windows = []
            for chapter in chapters:
                marker_time = max(0.0, min(duration_sec, float(chapter["start_sec"])))
                start, end = _chapter_window(marker_time, duration_sec)
                local = [
                    item for item in timeline
                    if start <= float(item[0]) <= end
                ]
                reaction = max(local, key=lambda item: item[1]) if local else (
                    int(marker_time), 0.0, {}
                )
                chapter_scores = dict(reaction[2])
                chapter_scores["chapter_marker"] = {
                    "index": int(chapter["index"]),
                    "title": str(chapter.get("title") or "OBS marker"),
                    "time_sec": round(marker_time, 3),
                }
                connection.execute(
                    """
                    INSERT OR IGNORE INTO candidates(
                        video_id, start_sec, end_sec, score, energy_score, peak_sec,
                        reason, track_scores_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        start,
                        end,
                        round(min(1.0, float(reaction[1]) + 0.25), 4),
                        round(float(reaction[1]), 4),
                        float(reaction[0]),
                        "OBS-flagged moment: " + str(chapter.get("title") or "chapter marker"),
                        json.dumps(chapter_scores, separators=(",", ":")),
                    ),
                )
                chapter_windows.append((start, end))

            for second, score, track_scores in peaks:
                if any(start <= float(second) <= end for start, end in chapter_windows):
                    continue
                start, end = _dynamic_moment_window(moment_scores, second, duration_sec)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO candidates(
                        video_id, start_sec, end_sec, score, energy_score, peak_sec,
                        reason, track_scores_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        round(start, 3),
                        round(end, 3),
                        round(score, 4),
                        round(score, 4),
                        float(second),
                        "Dynamic setup, reaction, and landing around an audio spike",
                        json.dumps(track_scores, separators=(",", ":")),
                    ),
                )
            candidate_ids = [
                int(item["id"])
                for item in connection.execute(
                    """
                    SELECT id FROM candidates
                    WHERE video_id=? AND status='pending'
                    ORDER BY score DESC, id
                    """,
                    (video_id,),
                ).fetchall()
            ]

        if progress:
            progress(f"created {len(candidate_ids)} candidate windows ({len(chapters)} OBS markers prioritized)")

        # A specialist AudioSet model now literally listens to the strongest
        # candidate windows for laughter, screaming, impacts, and other events.
        # Failures remain non-fatal so transcript/audio-peak ranking still works.
        from .audio_events import detect_candidate_events
        with database(settings.db_path) as connection:
            listener_rows = connection.execute(
                """
                SELECT c.*, v.path AS video_path
                FROM candidates c JOIN videos v ON v.id=c.video_id
                WHERE c.video_id=? AND c.status='pending'
                ORDER BY c.score DESC, c.id LIMIT 24
                """,
                (video_id,),
            ).fetchall()
        for listen_index, listen_row in enumerate(listener_rows, start=1):
            if progress:
                progress(f"listening for laughter/yelling {listen_index}/{len(listener_rows)}")
            try:
                events = detect_candidate_events(settings, listen_row, progress)
                with database(settings.db_path) as connection:
                    connection.execute(
                        "UPDATE candidates SET audio_events_json=? WHERE id=?",
                        (json.dumps(events, separators=(",", ":")), int(listen_row["id"])),
                    )
            except Exception as exc:
                if progress:
                    progress(f"audio-event warning {listen_index}: {str(exc)[-160:]}")
        # Read every plausible moment before choosing Best 3/5. This makes the
        # shortlist reflect dialogue, laughter, learned taste, and audio together.
        from .transcribe import transcribe_candidate

        transcript_failures = 0
        for index, candidate_id in enumerate(candidate_ids, start=1):
            if progress:
                progress(
                    f"ranking transcript {index}/{len(candidate_ids)}: candidate {candidate_id}"
                )
            try:
                transcribe_candidate(
                    settings, candidate_id, progress=progress, refine=False
                )
            except Exception as exc:
                transcript_failures += 1
                if progress:
                    progress(
                        f"transcript warning {index}/{len(candidate_ids)}: {str(exc)[-180:]}"
                    )

        # Spend the slower visual/Qwen pass only on the strongest transcript-ranked
        # moments. These scores are stored before any batch-render button selects IDs.
        with database(settings.db_path) as connection:
            review_rows = connection.execute(
                """
                SELECT c.*, v.path AS video_path, v.audio_streams_json
                FROM candidates c JOIN videos v ON v.id=c.video_id
                WHERE c.video_id=? AND c.status='pending' AND trim(c.transcript) <> ''
                ORDER BY c.score DESC, c.id
                LIMIT 10
                """,
                (video_id,),
            ).fetchall()

        from .moment_refine import ensure_stored_refinement

        for index, candidate in enumerate(review_rows, start=1):
            if progress:
                progress(
                    f"AI reviewing moment {index}/{len(review_rows)}: candidate {int(candidate['id'])}"
                )
            ensure_stored_refinement(settings, candidate, progress)

        with database(settings.db_path) as connection:
            connection.execute(
                "UPDATE videos SET status = 'analyzed', last_error = NULL WHERE id = ?",
                (video_id,),
            )
        if progress:
            progress(
                f"transcript-first ranking complete: {len(candidate_ids) - transcript_failures}/"
                f"{len(candidate_ids)} transcripts, {len(review_rows)} AI-reviewed"
            )
        return len(candidate_ids)
    except Exception as exc:
        with database(settings.db_path) as connection:
            connection.execute(
                "UPDATE videos SET status = 'error', last_error = ? WHERE id = ?",
                (str(exc), video_id),
            )
        raise


def analyze_batch(
    settings: Settings,
    limit: int = 10,
    max_candidates_per_video: int = 50,
    progress: Callable[[str], None] | None = None,
) -> int:
    with database(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, name, duration_sec
            FROM videos
            WHERE duplicate_of IS NULL
              AND status = 'ready'
              AND duration_sec >= 300
              AND instr(rel_path, '\\') = 0
              AND name GLOB '20??-??-?? ??-??-??.*'
            ORDER BY duration_sec ASC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    completed = 0
    for index, row in enumerate(rows, start=1):
        if progress:
            progress(
                f"batch {index}/{len(rows)}: {row['name']} "
                f"({row['duration_sec'] / 60:.1f} min)"
            )
        try:
            analyze_video(
                settings,
                int(row["id"]),
                max_candidates=max_candidates_per_video,
                progress=progress,
            )
            completed += 1
        except Exception as exc:
            if progress:
                progress(f"batch warning: {row['name']}: {exc}")
    return completed
