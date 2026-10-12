from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


SENTENCE_END = re.compile(r"[.!?]$")


@dataclass(frozen=True, slots=True)
class CaptionPreset:
    key: str
    max_chars: int
    max_words: int
    max_seconds: float
    preferred_seconds: float
    break_gap_seconds: float


# The original three timing modes remain for older variants. Punch, Clean,
# and Chaos adapt FreeCut's disciplined phrase rules for short-form captions.
CAPTION_PRESETS = {
    "balanced": CaptionPreset("balanced", 42, 8, 2.2, 1.4, 0.18),
    "compact": CaptionPreset("compact", 32, 5, 1.65, 1.0, 0.14),
    "relaxed": CaptionPreset("relaxed", 48, 8, 2.6, 1.65, 0.24),
    "punch": CaptionPreset("punch", 28, 4, 1.55, 0.9, 0.16),
    "clean": CaptionPreset("clean", 38, 6, 2.0, 1.2, 0.20),
    "chaos": CaptionPreset("chaos", 22, 3, 1.25, 0.72, 0.12),
}


def timestamp_seconds(value, fallback: float = 0.0) -> float:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric / 1000 if numeric > 1000 else numeric
    parts = str(value).replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return fallback


def _whisper_token_words(segment: dict) -> list[dict]:
    words: list[dict] = []
    for token in segment.get("tokens") or []:
        raw = str(token.get("text") or "")
        if not raw or raw.startswith("[_"):
            continue
        piece = raw.strip()
        if not piece:
            continue
        offsets = token.get("offsets") or {}
        start = float(offsets.get("from") or 0) / 1000
        end = float(offsets.get("to") or offsets.get("from") or 0) / 1000
        probability = float(token.get("p") or 0)
        if raw.startswith(" ") or not words:
            words.append({"text": piece, "start": start, "end": end, "probabilities": [probability]})
        else:
            words[-1]["text"] += piece
            words[-1]["end"] = max(words[-1]["end"], end)
            words[-1]["probabilities"].append(probability)
    for word in words:
        probabilities = word.pop("probabilities")
        word["confidence"] = sum(probabilities) / max(1, len(probabilities))
    return words


def _provided_words(segment: dict) -> list[dict]:
    result = []
    for raw in segment.get("words") or []:
        text = str(raw.get("text") or raw.get("word") or "").strip()
        start = timestamp_seconds(raw.get("start"), 0)
        end = timestamp_seconds(raw.get("end"), start)
        if text and end > start:
            result.append({
                "text": text,
                "start": start,
                "end": end,
                "confidence": raw.get("confidence"),
                "speaker": raw.get("speaker"),
                "track_ordinal": raw.get("track_ordinal"),
            })
    return result


def _repair_word_timings(words: list[dict], segment_start: float, segment_end: float) -> list[dict]:
    if not words:
        return []
    repaired = [dict(word) for word in words]
    reliable = []
    for index, word in enumerate(repaired):
        confidence = word.get("confidence")
        duration = float(word["end"]) - float(word["start"])
        confidence_ok = confidence is None or float(confidence) >= 0.5
        if confidence_ok and 0.04 <= duration <= 1.5:
            reliable.append(index)

    # Only synthesize a narrow leading window when Whisper emitted obviously
    # stretched, low-confidence tokens. Never spread guesses across a long gap.
    if reliable:
        first = reliable[0]
        anchor = float(repaired[first]["start"])
        if first:
            backfill = min(1.05, 0.30 * first + 0.08)
            estimated_start = max(segment_start, anchor - backfill)
            step = backfill / first
            for index in range(first):
                original_start = float(repaired[index]["start"])
                confidence = repaired[index].get("confidence")
                if confidence is not None and float(confidence) >= 0.5 and original_start < anchor - 0.70:
                    continue
                start = estimated_start + index * step
                repaired[index]["start"] = start
                repaired[index]["end"] = min(anchor - 0.04, start + max(0.16, step - 0.04))

        last = reliable[-1]
        cursor = float(repaired[last]["end"])
        for index in range(last + 1, len(repaired)):
            start = cursor + 0.04
            repaired[index]["start"] = start
            repaired[index]["end"] = min(segment_end, start + 0.28)
            cursor = float(repaired[index]["end"])

    previous_end = segment_start
    for index, word in enumerate(repaired):
        start = max(segment_start, float(word["start"]), previous_end - 0.02)
        raw_end = min(segment_end, max(start + 0.04, float(word["end"])))
        next_start = (
            float(repaired[index + 1]["start"])
            if index + 1 < len(repaired)
            else segment_end
        )
        natural_duration = min(0.95, max(0.24, 0.13 + len(str(word["text"])) * 0.052))
        end = min(raw_end, start + natural_duration, max(start + 0.04, next_start - 0.015))
        word["start"] = start
        word["end"] = max(start + 0.04, end)
        previous_end = float(word["end"])
    return repaired


def transcript_words(payload: dict) -> list[dict]:
    segments = payload.get("transcription") or payload.get("segments") or []
    words: list[dict] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        timestamps = segment.get("timestamps") or {}
        segment_start = timestamp_seconds(timestamps.get("from", segment.get("start")), 0)
        segment_end = timestamp_seconds(timestamps.get("to", segment.get("end")), segment_start + 3)
        raw_words = _provided_words(segment) or _whisper_token_words(segment)
        words.extend(_repair_word_timings(raw_words, segment_start, segment_end))
    return sorted(words, key=lambda word: (word["start"], word["end"]))


def _join_words(words: Iterable[dict]) -> str:
    text = " ".join(str(word["text"]).strip() for word in words).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def _group_fits(words: list[dict], preset: CaptionPreset) -> bool:
    if not words:
        return False
    gaps = [
        float(words[index]["start"]) - float(words[index - 1]["end"])
        for index in range(1, len(words))
    ]
    return (
        len(words) <= preset.max_words
        and len(_join_words(words)) <= preset.max_chars
        and float(words[-1]["end"]) - float(words[0]["start"]) <= preset.max_seconds
        and not any(gap >= preset.break_gap_seconds for gap in gaps)
    )


def _should_break(current: list[dict], next_word: dict, preset: CaptionPreset) -> bool:
    if not current:
        return False
    first = current[0]
    previous = current[-1]
    current_duration = float(previous["end"]) - float(first["start"])
    next_duration = float(next_word["end"]) - float(first["start"])
    gap = float(next_word["start"]) - float(previous["end"])
    next_text = _join_words([*current, next_word])
    if gap >= max(0.70, preset.break_gap_seconds * 3):
        return True
    if len(current) >= 2 and gap >= preset.break_gap_seconds:
        return True
    if len(current) >= 2 and (
        len(current) >= preset.max_words
        or len(next_text) > preset.max_chars
        or next_duration > preset.max_seconds
    ):
        return True
    if current_duration >= preset.preferred_seconds and SENTENCE_END.search(str(previous["text"])):
        return True
    return False


def caption_word_groups(payload: dict, preset_key: str = "balanced") -> list[list[dict]]:
    preset = CAPTION_PRESETS.get(preset_key, CAPTION_PRESETS["balanced"])
    words = transcript_words(payload)
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        speaker_changed = bool(current and current[-1].get("speaker") and word.get("speaker") and current[-1].get("speaker") != word.get("speaker"))
        if current and (speaker_changed or _should_break(current, word, preset)):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)

    trailing = groups[-1] if groups else None
    previous = groups[-2] if len(groups) >= 2 else None
    if trailing and len(trailing) == 1 and previous:
        if len(previous) >= 3:
            shortened = previous[:-1]
            rebalanced = [previous[-1], *trailing]
            if _group_fits(shortened, preset) and _group_fits(rebalanced, preset):
                groups[-2:] = [shortened, rebalanced]
        elif _group_fits([*previous, *trailing], preset):
            groups[-2:] = [[*previous, *trailing]]
    return groups


def caption_phrases(payload: dict, preset_key: str = "balanced") -> list[tuple[float, float, str]]:
    groups = caption_word_groups(payload, preset_key)
    if not groups:
        phrases = []
        for segment in payload.get("transcription") or payload.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            timestamps = segment.get("timestamps") or {}
            start = timestamp_seconds(timestamps.get("from", segment.get("start")), 0)
            end = timestamp_seconds(timestamps.get("to", segment.get("end")), start + 2)
            text = str(segment.get("text") or "").strip()
            if text and end > start:
                phrases.append((start, end, text))
        return phrases

    phrases = [
        (float(group[0]["start"]), float(group[-1]["end"]), _join_words(group))
        for group in groups
        if group and _join_words(group)
    ]
    for index in range(1, len(phrases)):
        prev_start, prev_end, prev_text = phrases[index - 1]
        start, end, text = phrases[index]
        if start < prev_end:
            boundary = (start + prev_end) / 2
            phrases[index - 1] = (prev_start, boundary, prev_text)
            phrases[index] = (boundary + 0.01, max(end, boundary + 0.08), text)
    return phrases
