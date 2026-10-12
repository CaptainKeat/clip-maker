from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .captions import caption_phrases, caption_word_groups, transcript_words
from .db import database
from .media import CREATE_NO_WINDOW
from .settings import Settings
from .storage import vod_output_directory


AUDIO_FILTERS = {
    "natural": (
        "highpass=f=65,lowpass=f=16000,"
        "alimiter=limit=0.93:attack=5:release=80,"
        "loudnorm=I=-16:LRA=11:TP=-1.5,aresample=48000"
    ),
    "soft": (
        "highpass=f=70,lowpass=f=15500,deesser=i=0.14:m=0.28:f=0.5,"
        "acompressor=threshold=0.28:ratio=1.6:attack=7:release=150:"
        "makeup=1.03:knee=2.2:detection=peak,"
        "loudnorm=I=-16:LRA=11:TP=-1.5,aresample=48000"
    ),
    "restored": (
        "adeclip,highpass=f=70,lowpass=f=15000,deesser=i=0.22:m=0.35:f=0.5,"
        "acompressor=threshold=0.20:ratio=2.2:attack=5:release=120:"
        "makeup=1.10:knee=2.8:detection=peak,"
        "loudnorm=I=-16:LRA=11:TP=-1.5,aresample=48000"
    ),
}


@dataclass(frozen=True, slots=True)
class VariantRecipe:
    key: str
    label: str
    window_mode: str
    caption_preset: str
    audio_preset: str
    audio_mode: str = "primary"


VARIANT_RECIPES = (
    VariantRecipe("punch", "Punch captions · full moment", "full", "punch", "natural"),
    VariantRecipe("chaos", "Chaos captions · focused moment", "tight", "chaos", "soft", "speech"),
    VariantRecipe("clean", "Clean captions · full setup", "roomy", "clean", "natural"),
    VariantRecipe("restore", "Punch captions · focused cleanup", "tight", "punch", "restored"),
)

# FreeCut-inspired short-form typography translated to libass. Impact is the
# installed local fallback for FreeCut's Anton preset.
CAPTION_STYLES = {
    "punch": {
        "font": "Impact", "size": 108, "accent": "&H0000D4FF",
        "outline": 7, "shadow": 3, "margin_v": 500,
        "uppercase": True, "active_scale": 116,
    },
    "clean": {
        "font": "Arial", "size": 82, "accent": "&H005AFFB9",
        "outline": 5, "shadow": 2, "margin_v": 455,
        "uppercase": False, "active_scale": 108,
    },
    "chaos": {
        "font": "Impact", "size": 116, "accent": "&H00449EFF",
        "outline": 8, "shadow": 4, "margin_v": 520,
        "uppercase": True, "active_scale": 122,
    },
}

REACTION_TERMS = re.compile(
    r"^(?:fuck|fucking|shit|damn|what|why|bro|dude|holy|no|wtf|bitch|ow|fat|red|drop|h+a+h+a*)[.!?,]*$",
    re.I,
)


def _candidate(settings: Settings, candidate_id: int):
    with database(settings.db_path) as connection:
        row = connection.execute(
            """
            SELECT c.*, v.path AS video_path, v.name AS video_name,
                   v.audio_streams_json, v.clip_folder_name
            FROM candidates c JOIN videos v ON v.id = c.video_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Candidate {candidate_id} does not exist")
    return row


def _audio_filter_args(preset: str) -> list[str]:
    filter_value = AUDIO_FILTERS.get(preset, AUDIO_FILTERS["natural"])
    return ["-af", filter_value]


def generate_preview(settings: Settings, candidate_id: int) -> Path:
    row = _candidate(settings, candidate_id)
    destination = settings.preview_dir / f"candidate-{candidate_id}.mp4"
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    duration = float(row["end_sec"]) - float(row["start_sec"])
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{float(row['start_sec']):.3f}",
        "-i",
        row["video_path"],
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=-2:720",
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p4",
        "-cq",
        "25",
        *_audio_filter_args("natural"),
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
    with database(settings.db_path) as connection:
        connection.execute(
            "UPDATE candidates SET preview_path = ?, updated_at=CURRENT_TIMESTAMP WHERE id = ?",
            (str(destination), candidate_id),
        )
    return destination


def _ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    return f"{hours}:{minutes:02d}:{remainder:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _caption_style(preset: str) -> dict:
    return CAPTION_STYLES.get(preset, CAPTION_STYLES["punch"])


def _styled_caption_text(group: list[dict], active_index: int, preset: str) -> str:
    style = _caption_style(preset)
    pieces: list[str] = []
    for index, word in enumerate(group):
        raw = str(word["text"]).strip()
        if style["uppercase"]:
            raw = raw.upper()
        spacer = "" if index == 0 or re.match(r"^[,.;:!?]", raw) else " "
        escaped = _ass_escape(raw)
        if index == active_index:
            accent = style["accent"]
            scale = style["active_scale"]
            reaction = bool(REACTION_TERMS.match(raw))
            color = "&H00645FFF" if reaction and preset == "chaos" else accent
            pieces.append(
                f"{spacer}{{\\c{color}\\fscx{scale}\\fscy{scale}}}{escaped}"
                r"{\c&H00FFFFFF\fscx100\fscy100}"
            )
        else:
            pieces.append(f"{spacer}{escaped}")
    return "".join(pieces)


def _write_ass(
    row,
    destination: Path,
    clip_offset: float = 0,
    clip_duration: float | None = None,
    caption_preset: str = "punch",
    font_scale: float = 1.0,
    margin_offset: int = 0,
) -> bool:
    if not row["transcript_json"]:
        return False
    payload = json.loads(row["transcript_json"])
    style = dict(_caption_style(caption_preset))
    style["size"] = round(style["size"] * max(0.6, min(1.6, float(font_scale))))
    style["margin_v"] = max(80, min(1600, style["margin_v"] + int(margin_offset)))
    timed_events: list[tuple[float, float, str]] = []
    groups = caption_word_groups(payload, caption_preset)

    for group in groups:
        if not group:
            continue
        group_end = float(group[-1]["end"]) + 0.12
        for index, word in enumerate(group):
            source_start = float(word["start"])
            source_end = (
                float(group[index + 1]["start"])
                if index + 1 < len(group)
                else group_end
            )
            start = source_start - clip_offset
            end = source_end - clip_offset
            if clip_duration is not None:
                if end <= 0 or start >= clip_duration:
                    continue
                start = max(0, start)
                end = min(clip_duration, end)
            if end <= start:
                continue
            intro = (
                r"{\fscx88\fscy88\t(0,110,\fscx100\fscy100)}"
                if index == 0
                else ""
            )
            tilt = (
                r"{\frz-1.2}" if caption_preset == "chaos" and index % 2 == 0
                else r"{\frz1.2}" if caption_preset == "chaos"
                else ""
            )
            text = intro + tilt + _styled_caption_text(group, index, caption_preset)
            timed_events.append((start, end, text))

    # Segment-only transcripts cannot animate per word, but still receive the
    # modern typography and phrase-level pop instead of the legacy Arial look.
    if not timed_events:
        for phrase_start, phrase_end, text in caption_phrases(payload, caption_preset):
            start = phrase_start - clip_offset
            end = phrase_end - clip_offset
            if clip_duration is not None:
                if end <= 0 or start >= clip_duration:
                    continue
                start = max(0, start)
                end = min(clip_duration, end)
            if end <= start or not text.strip():
                continue
            display = text.upper() if style["uppercase"] else text
            timed_events.append((
                start,
                end,
                r"{\fscx88\fscy88\t(0,110,\fscx100\fscy100)}" f"{_ass_escape(display)}",
            ))

    normalized: list[tuple[float, float, str]] = []
    for start, end, text in sorted(timed_events, key=lambda item: (item[0], item[1])):
        while normalized and normalized[-1][1] > start:
            previous_start, _previous_end, previous_text = normalized[-1]
            if start - previous_start >= 0.04:
                normalized[-1] = (previous_start, start, previous_text)
                break
            normalized.pop()
        if end - start >= 0.04:
            normalized.append((start, end, text))
    events = [
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}"
        for start, end, text in normalized
    ]
    if not events:
        return False

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style["font"]},{style["size"]},&H00FFFFFF,{style["accent"]},&H00101010,&H90000000,-1,0,0,0,100,100,0.5,0,1,{style["outline"]},{style["shadow"]},2,72,72,{style["margin_v"]},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return True

def _reaction_anchor(words: list[dict], duration: float) -> float:
    reactions = [
        (float(word["start"]) + float(word["end"])) / 2
        for word in words
        if REACTION_TERMS.match(str(word["text"]).strip())
    ]
    if not reactions:
        return duration * 0.5
    reactions.sort()
    return reactions[len(reactions) // 2]


def _bounded_window(start: float, length: float, duration: float) -> tuple[float, float]:
    length = min(duration, max(8.0, length))
    start = max(0.0, min(start, duration - length))
    return start, min(duration, start + length)


def _variant_window(row, recipe: VariantRecipe, attempt: int) -> tuple[float, float]:
    duration = float(row["end_sec"]) - float(row["start_sec"])
    if not row["transcript_json"]:
        return 0.0, duration
    payload = json.loads(row["transcript_json"])
    refinement = (payload.get("clipmaker") or {}).get("moment_refinement") or {}
    if refinement.get("status") in {"ai", "combined"}:
        base_start, base_end = _bounded_window(
            float(refinement.get("start", 0.0)),
            float(refinement.get("end", duration))
            - float(refinement.get("start", 0.0)),
            duration,
        )
    else:
        base_start, base_end = 0.0, duration
    if recipe.window_mode in {"full", "roomy"}:
        return base_start, base_end
    words = transcript_words(payload)
    if not words:
        return base_start, base_end
    anchor = _reaction_anchor(words, duration)
    cycle = (attempt - 1) // len(VARIANT_RECIPES)
    drift = ((cycle % 3) - 1) * 0.65 if cycle else 0.0
    if recipe.window_mode == "tight":
        # Tighten inside Qwen's complete moment without losing its boundaries.
        base_duration = base_end - base_start
        length = max(30.0, base_duration - min(10.0, base_duration * 0.14))
        length = min(base_duration, length)
        start = max(
            base_start,
            min(anchor - length * 0.38 + drift, base_end - length),
        )
        return start, start + length
    return base_start, base_end


def _transcript_audio_ordinal(row) -> int:
    if not row["transcript_json"]:
        return 0
    payload = json.loads(row["transcript_json"])
    metadata = payload.get("clipmaker") or {}
    return int(metadata.get("audio_ordinal", metadata.get("primary_transcript_ordinal", 0)))


def _transcript_audio_ordinals(row) -> list[int]:
    streams = json.loads(row["audio_streams_json"] or "[]")
    available = {int(stream.get("ordinal", index)) for index, stream in enumerate(streams)}
    if not available:
        return [0]
    selected: list[int] = []
    if row["transcript_json"]:
        payload = json.loads(row["transcript_json"])
        for word in transcript_words(payload):
            ordinal = word.get("track_ordinal")
            if ordinal is not None and int(ordinal) in available and int(ordinal) not in selected:
                selected.append(int(ordinal))
        metadata = payload.get("clipmaker") or {}
        for choice in metadata.get("track_choices") or []:
            ordinal = int(choice.get("audio_ordinal", -1))
            if int(choice.get("word_count", 0)) > 0 and ordinal in available and ordinal not in selected:
                selected.append(ordinal)
        primary = int(metadata.get("audio_ordinal", metadata.get("primary_transcript_ordinal", 0)))
        if primary in available and primary not in selected:
            selected.append(primary)
    return selected or [min(available)]


def _render_filter_graph(ass_path: Path | None, audio_ordinals: list[int], audio_preset: str) -> tuple[str, str, str]:
    graph = (
        "[0:v]fps=30,split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:4,scale=1080:1920,"
        "eq=brightness=-0.10:saturation=0.72[bg];"
        "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
    )
    if ass_path is None:
        video_map = "[base]"
    else:
        escaped = ass_path.as_posix().replace(":", r"\:")
        graph += f";[base]subtitles=filename='{escaped}'[outv]"
        video_map = "[outv]"
    inputs = []
    for index, ordinal in enumerate(audio_ordinals):
        graph += (
            f";[0:a:{ordinal}]aresample=48000,"
            "loudnorm=I=-22:LRA=15:TP=-3"
            f"[ain{index}]"
        )
        inputs.append(f"[ain{index}]")
    if len(inputs) == 1:
        audio_source = inputs[0]
    else:
        graph += f";{''.join(inputs)}amix=inputs={len(inputs)}:duration=longest:dropout_transition=0:normalize=0[conversation]"
        audio_source = "[conversation]"
    graph += f";{audio_source}{AUDIO_FILTERS.get(audio_preset, AUDIO_FILTERS['natural'])}[outa]"
    return graph, video_map, "[outa]"

def generate_variant(
    settings: Settings,
    candidate_id: int,
    progress: Callable[[str], None] | None = None,
) -> Path:
    from .transcribe import transcribe_candidate

    row = _candidate(settings, candidate_id)
    engine = None
    did_transcribe = False
    if row["transcript_json"]:
        try:
            engine = json.loads(row["transcript_json"]).get("engine")
        except json.JSONDecodeError:
            engine = None
    if not row["transcript"] or (
        settings.transcription_engine == "parakeet" and engine != "parakeet"
    ):
        transcribe_candidate(settings, candidate_id, progress)
        did_transcribe = True
        row = _candidate(settings, candidate_id)

    if not did_transcribe:
        from .moment_refine import ensure_stored_refinement

        ensure_stored_refinement(settings, row, progress)
        row = _candidate(settings, candidate_id)

    with database(settings.db_path) as connection:
        next_attempt = int(
            connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM candidate_variants WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()[0]
        )
    recipe = VARIANT_RECIPES[(next_attempt - 1) % len(VARIANT_RECIPES)]
    clip_offset_start, clip_offset_end = _variant_window(row, recipe, next_attempt)
    clip_duration = clip_offset_end - clip_offset_start
    audio_ordinals = _transcript_audio_ordinals(row)
    audio_ordinal = audio_ordinals[0]

    destination = vod_output_directory(
        settings.output_dir, str(row["video_name"]), str(row["clip_folder_name"] or "")
    ) / f"clip-{candidate_id:04d}-v{next_attempt:02d}-{recipe.key}.mp4"
    ass_path = settings.work_dir / f"candidate-{candidate_id}-v{next_attempt:02d}.ass"
    has_captions = _write_ass(
        row,
        ass_path,
        clip_offset=clip_offset_start,
        clip_duration=clip_duration,
        caption_preset=recipe.caption_preset,
    )
    filter_graph, video_map, audio_map = _render_filter_graph(
        ass_path if has_captions else None, audio_ordinals, recipe.audio_preset
    )
    if progress:
        progress(f"rendering attempt {next_attempt}: {recipe.label}")
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{float(row['start_sec']) + clip_offset_start:.3f}",
        "-i",
        row["video_path"],
        "-t",
        f"{clip_duration:.3f}",
        "-filter_complex",
        filter_graph,
        "-map",
        video_map,
        "-map",
        audio_map,
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p4",
        "-cq",
        "22",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
    with database(settings.db_path) as connection:
        connection.execute(
            "UPDATE candidate_variants SET decision='superseded', updated_at=CURRENT_TIMESTAMP WHERE candidate_id=? AND decision='pending'",
            (candidate_id,),
        )
        connection.execute(
            """
            INSERT INTO candidate_variants (
                candidate_id, attempt, recipe_key, recipe_label, clip_start_sec,
                clip_end_sec, caption_preset, audio_preset, audio_ordinal,
                output_path, decision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                candidate_id,
                next_attempt,
                recipe.key,
                recipe.label,
                float(row["start_sec"]) + clip_offset_start,
                float(row["start_sec"]) + clip_offset_end,
                recipe.caption_preset,
                recipe.audio_preset,
                audio_ordinal,
                str(destination),
            ),
        )
        connection.execute(
            """
            UPDATE candidates SET export_path=?, status='pending', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (str(destination), candidate_id),
        )
    if progress:
        progress(f"attempt {next_attempt} ready for review")
    return destination


HATE_REASON_TAGS = {
    "not_funny",
    "weak_moment",
    "bad_audio",
    "bad_captions",
    "bad_timing",
    "too_long",
    "too_short",
}


def _hate_qualities(
    tags: list[str],
    reason: str,
) -> tuple[int | None, int | None, int]:
    chosen = set(tags)
    edit_problem = bool(
        chosen
        & {"bad_audio", "bad_captions", "bad_timing", "too_long", "too_short"}
    )
    moment_problem = bool(chosen & {"not_funny", "weak_moment"})
    if not chosen and reason.strip():
        moment_problem = True
    return (0 if edit_problem else None, 0 if moment_problem else None, 0)


def decide_candidate(
    settings: Settings,
    candidate_id: int,
    decision: str,
    reason: str = "",
    reason_tags: list[str] | None = None,
) -> None:
    if decision not in {"like", "hate", "better_moment"}:
        raise ValueError("Decision must be like, hate, or better_moment")
    with database(settings.db_path) as connection:
        variant = connection.execute(
            """
            SELECT id FROM candidate_variants
            WHERE candidate_id=? AND output_path IS NOT NULL
            ORDER BY attempt DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        if not variant:
            raise ValueError("Generate a cut before deciding")

        variant_decision = {
            "like": "liked",
            "hate": "hated",
            "better_moment": "better_moment",
        }[decision]
        connection.execute(
            "UPDATE candidate_variants SET decision=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (variant_decision, int(variant["id"])),
        )
        connection.execute(
            "UPDATE candidates SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ("approved" if decision == "like" else "rejected", candidate_id),
        )

        clean_tags = [
            tag for tag in (reason_tags or []) if tag in HATE_REASON_TAGS
        ]
        clean_reason = reason.strip()[:600]
        qualities = {
            "like": (1, 1, 1),
            "hate": _hate_qualities(clean_tags, clean_reason),
            "better_moment": (1, 0, 0),
        }[decision]
        connection.execute(
            """
            INSERT INTO review_feedback(
                candidate_id, variant_id, verdict, edit_quality,
                moment_quality, social_ready, reason_text, reason_tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, variant_id) DO UPDATE SET
                verdict=excluded.verdict,
                edit_quality=excluded.edit_quality,
                moment_quality=excluded.moment_quality,
                social_ready=excluded.social_ready,
                reason_text=excluded.reason_text,
                reason_tags_json=excluded.reason_tags_json,
                created_at=CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                int(variant["id"]),
                decision,
                *qualities,
                clean_reason if decision == "hate" else "",
                json.dumps(clean_tags if decision == "hate" else []),
            ),
        )

def retry_candidate(
    settings: Settings,
    candidate_id: int,
    progress: Callable[[str], None] | None = None,
) -> Path:
    with database(settings.db_path) as connection:
        connection.execute(
            """
            UPDATE candidate_variants SET decision='retry', updated_at=CURRENT_TIMESTAMP
            WHERE id=(SELECT id FROM candidate_variants WHERE candidate_id=? ORDER BY attempt DESC LIMIT 1)
            """,
            (candidate_id,),
        )
        connection.execute(
            "UPDATE candidates SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (candidate_id,),
        )
    return generate_variant(settings, candidate_id, progress)



def _combined_window(windows: list[dict], video_duration: float) -> tuple[float, float]:
    """Fit selected context/payoff windows into one natural reel of at most 90 seconds."""
    if len(windows) < 2:
        raise ValueError("Select at least two clips to combine")
    desired_start = min(float(item["start"]) for item in windows)
    desired_end = max(float(item["end"]) for item in windows)
    peaks = [
        min(float(item["end"]), max(float(item["start"]), float(item["peak"])))
        for item in windows
    ]
    core_start, core_end = min(peaks), max(peaks)
    if core_end - core_start > 90.0:
        raise ValueError("Those clips are too far apart to make one clean 90-second reel")
    if desired_end - desired_start <= 90.0:
        start, end = desired_start, desired_end
    else:
        spare = 90.0 - (core_end - core_start)
        start = max(desired_start, core_start - spare * 0.55)
        end = start + 90.0
        if end < core_end:
            end = core_end
            start = end - 90.0
    duration = end - start
    if duration < 30.0:
        padding = 30.0 - duration
        start -= padding * 0.55
        end += padding * 0.45
    start = max(0.0, start)
    end = min(float(video_duration), end)
    if end - start < 30.0:
        start = max(0.0, end - 30.0)
    return round(start, 3), round(end, 3)


def combine_candidates(
    settings: Settings,
    candidate_ids: list[int],
    progress: Callable[[str], None] | None = None,
) -> Path:
    ids = list(dict.fromkeys(int(item) for item in candidate_ids))
    if not 2 <= len(ids) <= 3:
        raise ValueError("Select 2 or 3 clips to combine")
    placeholders = ",".join("?" for _ in ids)
    with database(settings.db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT c.*, v.duration_sec AS video_duration,
                   cv.clip_start_sec AS selected_start,
                   cv.clip_end_sec AS selected_end
            FROM candidates c
            JOIN videos v ON v.id=c.video_id
            LEFT JOIN candidate_variants cv ON cv.id=(
                SELECT id FROM candidate_variants latest
                WHERE latest.candidate_id=c.id AND latest.output_path IS NOT NULL
                ORDER BY latest.attempt DESC LIMIT 1
            )
            WHERE c.id IN ({placeholders})
            ORDER BY c.start_sec
            """,
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("One of those clips no longer exists")
        if len({int(row["video_id"]) for row in rows}) != 1:
            raise ValueError("Combined clips must come from the same VOD")
        if any(row["selected_start"] is None for row in rows):
            raise ValueError("Build each selected clip before combining them")
        windows = [
            {
                "start": float(row["selected_start"]),
                "end": float(row["selected_end"]),
                "peak": float(row["peak_sec"] or 0.0),
            }
            for row in rows
        ]
        start, end = _combined_window(windows, float(rows[0]["video_duration"]))
        source_ids = [int(row["id"]) for row in rows]
        combined_score = max(float(row["score"]) for row in rows)
        reason = "Combined context + payoff from clips " + ", ".join(str(item) for item in source_ids)
        cursor = None
        actual_start = start
        for millisecond in range(10):
            actual_start = start + millisecond / 1000.0
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO candidates(
                        video_id,start_sec,end_sec,score,energy_score,peak_sec,
                        transcript_score,reason,track_scores_json,status
                    ) VALUES (?,?,?,?,?,?,?,?,?,'pending')
                    """,
                    (
                        int(rows[0]["video_id"]), actual_start, end,
                        combined_score, max(float(row["energy_score"]) for row in rows),
                        float(rows[-1]["peak_sec"] or rows[-1]["selected_end"]),
                        max(float(row["transcript_score"]) for row in rows),
                        reason, str(rows[-1]["track_scores_json"] or "{}"),
                    ),
                )
                break
            except Exception as exc:
                if "UNIQUE constraint" not in str(exc) or millisecond == 9:
                    raise
        combined_id = int(cursor.lastrowid)
    if progress:
        progress(f"combining {len(ids)} clips into one {end - actual_start:.0f}-second context and payoff reel")
    from .transcribe import transcribe_candidate

    transcribe_candidate(settings, combined_id, progress, refine=False)
    with database(settings.db_path) as connection:
        stored = connection.execute("SELECT transcript_json FROM candidates WHERE id=?", (combined_id,)).fetchone()
        payload = json.loads(stored["transcript_json"] or "{}")
        duration = end - actual_start
        payload.setdefault("clipmaker", {})["moment_refinement"] = {
            "version": 1, "status": "combined", "start": 0.0,
            "end": round(duration, 3), "duration": round(duration, 3),
            "quality": combined_score,
            "reason": "Kept the selected context and payoff together",
        }
        payload["clipmaker"]["combined_from"] = source_ids
        connection.execute(
            "UPDATE candidates SET transcript_json=?, reason=? WHERE id=?",
            (json.dumps(payload), reason, combined_id),
        )
    return generate_variant(settings, combined_id, progress)

def prepare_batch(
    settings: Settings,
    limit: int = 3,
    video_id: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> int:
    with database(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT c.id FROM candidates c
            LEFT JOIN (
                SELECT source.video_id, COUNT(*) AS promising_signals
                FROM review_feedback feedback
                JOIN candidates source ON source.id=feedback.candidate_id
                WHERE feedback.verdict='better_moment'
                GROUP BY source.video_id
            ) learned ON learned.video_id=c.video_id
            WHERE c.status='pending'
              AND (? IS NULL OR c.video_id = ?)
              AND NOT EXISTS (
                  SELECT 1 FROM candidate_variants v
                  WHERE v.candidate_id=c.id AND v.output_path IS NOT NULL
              )
            ORDER BY COALESCE(learned.promising_signals, 0) DESC, c.score DESC, c.id
            LIMIT ?
            """,
            (video_id, video_id, limit),
        ).fetchall()
    for index, row in enumerate(rows, start=1):
        if progress:
            progress(f"building cut {index}/{len(rows)}: candidate {int(row['id'])}")
        generate_variant(settings, int(row["id"]), progress)
    return len(rows)


def export_vertical(settings: Settings, candidate_id: int) -> Path:
    return generate_variant(settings, candidate_id)
