from __future__ import annotations

import json
import re
import statistics
import subprocess
from pathlib import Path
from typing import Callable

from .captions import transcript_words
from .db import database
from .lmstudio import lmstudio_chat
from .media import CREATE_NO_WINDOW
from .settings import Settings


FRAME_LINE = re.compile(r"pts_time:(?P<time>-?\d+(?:\.\d+)?)")
METADATA_LINE = re.compile(
    r"lavfi\.(?P<key>scd\.score|signalstats\.YDIF)=(?P<value>-?\d+(?:\.\d+)?)"
)
REACTION_ANCHOR = re.compile(
    r"^(?:(?:ha){2,}|hah+|lol|lmao|laugh(?:ing|s|ed)?|fuck|shit|damn|bro|dude|holy|what|why)$",
    re.I,
)


def _bounded_refinement(start: float, end: float, duration: float) -> tuple[float, float]:
    if duration <= 30.0:
        return 0.0, max(0.0, duration)
    start = max(0.0, min(float(start), duration))
    end = max(start, min(float(end), duration))
    requested = min(90.0, max(30.0, end - start))
    center = (start + end) / 2.0
    start = center - requested / 2.0
    start = max(0.0, min(start, duration - requested))
    return round(start, 3), round(start + requested, 3)


def _json_object(raw: str) -> dict:
    cleaned = raw.strip().replace("~~~json", "").replace("~~~", "").strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("Qwen did not return JSON")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Qwen returned the wrong JSON shape")
    return payload


def _timed_transcript(payload: dict) -> str:
    words = transcript_words(payload)
    lines: list[str] = []
    group: list[dict] = []
    for word in words:
        if group and (
            len(group) >= 9
            or float(word["start"]) - float(group[-1]["end"]) > 0.75
            or word.get("speaker") != group[-1].get("speaker")
        ):
            speaker = str(group[0].get("speaker") or "Unknown")
            lines.append(
                f"[{float(group[0]['start']):.1f}-{float(group[-1]['end']):.1f}] "
                + f"{speaker}: "
                + " ".join(str(item["text"]) for item in group)
            )
            group = []
        group.append(word)
    if group:
        speaker = str(group[0].get("speaker") or "Unknown")
        lines.append(
            f"[{float(group[0]['start']):.1f}-{float(group[-1]['end']):.1f}] "
            + f"{speaker}: "
            + " ".join(str(item["text"]) for item in group)
        )
    return "\n".join(lines)[:5000]


def _reaction_anchors(payload: dict) -> list[float]:
    anchors: list[float] = []
    for word in transcript_words(payload):
        clean = re.sub(r"[^a-z']", "", str(word.get("text", "")).lower())
        if not REACTION_ANCHOR.match(clean):
            continue
        stamp = round(float(word.get("start", 0.0)), 1)
        if not anchors or stamp - anchors[-1] >= 0.8:
            anchors.append(stamp)
    return anchors[:12]


def _visual_summary(
    settings: Settings,
    source: Path,
    start_sec: float,
    duration: float,
) -> dict:
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-an",
        "-vf",
        "fps=2,scale=320:-2,scdet=t=8,signalstats,metadata=print:file=-",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError):
        return {"available": False, "motion_peaks": [], "scene_changes": []}

    current_time = 0.0
    motion: list[tuple[float, float]] = []
    scenes: list[tuple[float, float]] = []
    for line in result.stdout.splitlines():
        frame = FRAME_LINE.search(line)
        if frame:
            current_time = float(frame.group("time"))
            continue
        metadata = METADATA_LINE.search(line)
        if not metadata:
            continue
        item = (round(current_time, 1), round(float(metadata.group("value")), 2))
        if metadata.group("key") == "signalstats.YDIF":
            motion.append(item)
        elif item[1] > 0:
            scenes.append(item)

    motion_values = [value for _, value in motion]
    return {
        "available": bool(motion or scenes),
        "motion_median": round(statistics.median(motion_values), 2) if motion_values else 0.0,
        "motion_peaks": sorted(motion, key=lambda item: item[1], reverse=True)[:8],
        "scene_changes": sorted(scenes, key=lambda item: item[1], reverse=True)[:8],
    }


def refine_moment(
    settings: Settings,
    row,
    transcript_payload: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    duration = float(row["end_sec"]) - float(row["start_sec"])
    if progress:
        progress("checking visual action and scene changes")
    visual = _visual_summary(
        settings, Path(row["video_path"]), float(row["start_sec"]), duration
    )
    transcript = _timed_transcript(transcript_payload) or "(no reliable dialogue)"
    anchors = _reaction_anchors(transcript_payload)
    absolute_peak = float(row["peak_sec"] or 0.0)
    candidate_start = float(row["start_sec"])
    candidate_end = float(row["end_sec"])
    audio_peak = (
        round(absolute_peak - candidate_start, 1)
        if candidate_start <= absolute_peak <= candidate_end
        else None
    )
    audio_events = (transcript_payload.get("clipmaker") or {}).get("audio_events") or []
    chapter = (transcript_payload.get("clipmaker") or {}).get("obs_chapter") or {}
    chapter_inside = (
        round(float(chapter.get("time_sec", 0.0)) - candidate_start, 1)
        if chapter else None
    )
    prompt = f"""You are selecting a funny gaming reel from one {duration:.1f}-second candidate.
Return ONLY compact JSON with keys start, end, quality, hook, reason, and platform_scores.
start/end are seconds inside this candidate. Keep the complete setup, escalation, punchline,
and reaction landing. Choose a natural duration from 30 to 90 seconds; do not pad dead air.
quality is 0..1. platform_scores is an object with tiktok, reels, and shorts values 0..1.
Do not invent events. Prefer funny dialogue, surprise, chaos, failure, callbacks, and group reactions.
Speaker labels are anonymous except Me, which is the configured microphone track. Follow the
conversation across speakers instead of judging one track in isolation. The audio peak often
marks people laughing or yelling AFTER the funny thing. Identify the
line, mistake, or visual event that caused that reaction and start before it. Never begin
on the laugh if its setup is available. Keep the friends piling on and the final landing.

HEARD AUDIO EVENTS (specialist listener; seconds inside candidate):
{json.dumps(audio_events, separators=(', ', ':'))}
Treat laughter, yelling, overlapping reactions, impacts, and sudden silence as evidence;
use the transcript and visuals to determine what caused them.

STRONGEST AUDIO REACTION (seconds inside candidate):
{audio_peak if audio_peak is not None else "unknown"}

USER OBS CHAPTER FLAG (seconds inside candidate):
{json.dumps({"time": chapter_inside, "title": chapter.get("title")}) if chapter else "none"}
The user usually presses this hotkey just after something happened. Treat it as a high-priority
lead, then search earlier for the setup and keep the reaction afterward.

TRANSCRIPT REACTION ANCHORS (seconds inside candidate):
{json.dumps(anchors)}

TIMED TRANSCRIPT:
{transcript}

VISUAL SIGNALS (times are seconds; YDIF means motion/change, not necessarily quality):
{json.dumps(visual, separators=(',', ':'))}
"""
    if progress:
        progress("Qwen is judging the setup, punchline, and landing")
    answer = _json_object(
        lmstudio_chat(settings, prompt, temperature=0.25, max_tokens=240, timeout=90.0)
    )
    start, end = _bounded_refinement(
        answer.get("start", 0), answer.get("end", duration), duration
    )
    quality = max(0.0, min(1.0, float(answer.get("quality", 0.5))))
    platform_scores = answer.get("platform_scores") or {}
    return {
        "version": 1,
        "status": "ai",
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "quality": round(quality, 4),
        "hook": str(answer.get("hook") or "")[:160],
        "reason": str(answer.get("reason") or "Qwen selected the complete funny beat")[:240],
        "platform_scores": {
            key: round(
                max(0.0, min(1.0, float(platform_scores.get(key, quality)))),
                4,
            )
            for key in ("tiktok", "reels", "shorts")
        },
        "visual": visual,
    }


def ensure_stored_refinement(
    settings: Settings,
    row,
    progress: Callable[[str], None] | None = None,
) -> dict:
    payload = json.loads(row["transcript_json"] or "{}")
    existing = (payload.get("clipmaker") or {}).get("moment_refinement") or {}
    if existing.get("status") in {"ai", "combined"}:
        return existing
    try:
        refinement = refine_moment(settings, row, payload, progress)
        reason = "Qwen: " + refinement["reason"]
        score = min(1.0, float(row["score"]) * 0.78 + refinement["quality"] * 0.22)
    except Exception as exc:
        duration = float(row["end_sec"]) - float(row["start_sec"])
        refinement = {
            "version": 1,
            "status": "fallback",
            "start": 0.0,
            "end": round(duration, 3),
            "duration": round(duration, 3),
            "error": str(exc)[:240],
        }
        reason = str(row["reason"])
        score = float(row["score"])
        if progress:
            progress("Qwen refinement unavailable; keeping the audio-selected moment")
    payload.setdefault("clipmaker", {})["moment_refinement"] = refinement
    with database(settings.db_path) as connection:
        connection.execute(
            """
            UPDATE candidates
            SET transcript_json=?, reason=?, score=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (json.dumps(payload), reason, score, int(row["id"])),
        )
    return refinement
