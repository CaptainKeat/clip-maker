from __future__ import annotations

import json
import math
import re
import subprocess
import threading
import wave
from collections import Counter
from pathlib import Path
from typing import Callable

from .captions import transcript_words
from .db import database
from .media import CREATE_NO_WINDOW
from .settings import Settings
from .speakers import label_words


LAUGHTER_WORDS = re.compile(r"\b(?:(?:ha){2,}|hah+|lol|lmao|laugh(?:ing|s|ed)?)\b", re.I)
REACTION_WORDS = re.compile(
    r"\b(?:fuck|shit|damn|what|why|bro|dude|holy|no way|oh my god|wtf|bitch)\b", re.I
)
_PARakeet_MODEL = None
_PARAKEET_LOCK = threading.Lock()


def setup_landing_bonus(text: str) -> float:
    """Reward a spoken setup that is followed by laughter or a strong reaction."""
    anchors = list(LAUGHTER_WORDS.finditer(text))
    if not anchors:
        return 0.0
    best_setup_words = max(
        len(re.findall(r"[a-z']+", text[max(0, match.start() - 180) : match.start()], re.I))
        for match in anchors
    )
    return min(0.18, max(0.0, (best_setup_words - 3) * 0.018))


def transcript_humor_score(text: str) -> float:
    if not text.strip():
        return 0.0
    words = text.split()
    laughter = len(LAUGHTER_WORDS.findall(text))
    reactions = len(REACTION_WORDS.findall(text))
    questions = text.count("?")
    exclamations = text.count("!")
    density = min(1.0, len(words) / 90.0)
    return min(
        1.0,
        laughter * 0.28
        + reactions * 0.055
        + questions * 0.04
        + exclamations * 0.04
        + density * 0.12
        + setup_landing_bonus(text),
    )


def music_repetition_penalty(text: str) -> float:
    tokens = re.findall(r"[a-z']+", text.lower())
    if len(tokens) < 12:
        return 0.0
    trigrams = [tuple(tokens[index : index + 3]) for index in range(len(tokens) - 2)]
    counts = Counter(trigrams)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    repetition = repeated / max(1, len(trigrams))
    marker = 0.45 if re.search(r"\[(?:music|singing)\]|\u266a", text, re.I) else 0.0
    scaled_repetition = max(0.0, min(1.0, (repetition - 0.14) / 0.16))
    return max(marker, scaled_repetition)


def _extract_audio(
    settings: Settings,
    source: Path,
    start_sec: float,
    duration_sec: float,
    destination: Path,
    audio_ordinal: int = 0,
) -> None:
    command = [
        settings.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration_sec:.3f}",
        "-map",
        f"0:a:{audio_ordinal}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)


def _load_parakeet(settings: Settings, progress: Callable[[str], None] | None = None):
    global _PARakeet_MODEL
    with _PARAKEET_LOCK:
        if _PARakeet_MODEL is not None:
            return _PARakeet_MODEL
        if progress:
            progress("loading local Parakeet model")
        import onnx_asr

        providers = ["CPUExecutionProvider"]
        model = onnx_asr.load_model(
            settings.parakeet_model,
            settings.parakeet_model_dir,
            quantization=settings.parakeet_quantization,
            providers=providers,
        )
        _PARakeet_MODEL = model.with_timestamps()
        return _PARakeet_MODEL


def _read_pcm16(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("Parakeet input must be 16-bit PCM")
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def gate_words_by_audio(words: list[dict], wav_path: Path, minimum_rms_db: float = -54.0, minimum_peak_db: float = -44.0) -> list[dict]:
    """Drop ASR words whose source track is effectively silent at that time."""
    if not words:
        return []
    samples, sample_rate = _read_pcm16(wav_path)
    audible: list[dict] = []
    for raw_word in words:
        word = dict(raw_word)
        left = max(0, int((float(word["start"]) - 0.08) * sample_rate))
        right = min(len(samples), int((float(word["end"]) + 0.08) * sample_rate))
        window = samples[left:right]
        if len(window) == 0:
            continue
        rms = math.sqrt(float((window * window).mean()) + 1e-12)
        peak = float(abs(window).max())
        rms_db = 20.0 * math.log10(max(rms, 1e-6))
        peak_db = 20.0 * math.log10(max(peak, 1e-6))
        word["audio_db"] = round(rms_db, 2)
        if rms_db >= minimum_rms_db or peak_db >= minimum_peak_db:
            audible.append(word)
    return audible


def _parakeet_words(result, offset_seconds: float, chunk_index: int) -> list[dict]:
    tokens = list(result.tokens or [])
    timestamps = list(result.timestamps or [])
    logprobs = list(result.logprobs or [])
    words: list[dict] = []
    current = None
    for index, token in enumerate(tokens):
        raw = str(token or "")
        piece = raw.strip()
        if not piece or piece.startswith("<"):
            continue
        start = offset_seconds + float(timestamps[index] if index < len(timestamps) else 0)
        next_time = timestamps[index + 1] if index + 1 < len(timestamps) else None
        end = offset_seconds + max(
            float(next_time) if next_time is not None else start - offset_seconds + 0.08,
            start - offset_seconds + 0.08,
        )
        confidence = None
        if index < len(logprobs):
            confidence = max(0.0, min(1.0, math.exp(float(logprobs[index]))))
        starts_word = raw.startswith(" ") or current is None
        if starts_word:
            if current:
                words.append(current)
            current = {
                "text": piece,
                "start": start,
                "end": end,
                "confidences": [] if confidence is None else [confidence],
                "_chunk": chunk_index,
            }
        else:
            current["text"] += piece
            current["end"] = max(float(current["end"]), end)
            if confidence is not None:
                current["confidences"].append(confidence)
    if current:
        words.append(current)
    for word in words:
        values = word.pop("confidences")
        word["confidence"] = min(values) if values else None
    return words


def _dedupe_words(words: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    for word in sorted(words, key=lambda item: (item["start"], item["end"])):
        normalized = re.sub(r"\W+", "", str(word["text"]).lower())
        duplicate = next(
            (
                previous
                for previous in reversed(deduped[-8:])
                if re.sub(r"\W+", "", str(previous["text"]).lower()) == normalized
                and previous.get("_chunk") != word.get("_chunk")
                and abs(float(previous["start"]) - float(word["start"])) <= 0.75
            ),
            None,
        )
        if duplicate is None:
            deduped.append(word)
    for word in deduped:
        word.pop("_chunk", None)
    return deduped


def _join_words(words: list[dict]) -> str:
    text = " ".join(str(word["text"]) for word in words)
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


def _transcribe_parakeet(
    settings: Settings,
    wav_path: Path,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, str]:
    model = _load_parakeet(settings, progress)
    samples, sample_rate = _read_pcm16(wav_path)
    chunk_seconds = 22.0
    overlap_seconds = 1.0
    chunk_size = int(chunk_seconds * sample_rate)
    step = int((chunk_seconds - overlap_seconds) * sample_rate)
    all_words: list[dict] = []
    total_chunks = max(1, math.ceil(max(1, len(samples) - chunk_size) / step) + 1)
    for chunk_index, start_sample in enumerate(range(0, len(samples), step), start=1):
        chunk = samples[start_sample : start_sample + chunk_size]
        if len(chunk) < int(0.25 * sample_rate):
            break
        if progress:
            progress(f"Parakeet chunk {chunk_index}/{total_chunks}")
        result = model.recognize(chunk, sample_rate=sample_rate)
        all_words.extend(_parakeet_words(result, start_sample / sample_rate, chunk_index))
        if start_sample + chunk_size >= len(samples):
            break
    words = _dedupe_words(all_words)
    text = _join_words(words)
    payload = {
        "engine": "parakeet",
        "transcription": [
            {
                "text": text,
                "start": words[0]["start"] if words else 0,
                "end": words[-1]["end"] if words else len(samples) / sample_rate,
                "words": words,
            }
        ],
    }
    return payload, text


def _transcribe_whisper(
    settings: Settings,
    wav_path: Path,
    output_base: Path,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, str]:
    from .whisper_setup import command as whisper_command, executable
    cli = executable(settings)
    output_json = output_base.with_suffix(".json")
    if progress: progress("Transcribing with whisper.cpp")
    command = whisper_command(settings,wav_path,output_base)
    subprocess.run(command, check=True, cwd=cli.parent, creationflags=CREATE_NO_WINDOW)
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    payload["engine"] = "whisper"
    segments = payload.get("transcription") or payload.get("segments") or []
    text = " ".join(
        str(segment.get("text") or "").strip()
        for segment in segments
        if isinstance(segment, dict)
    ).strip()
    output_json.unlink(missing_ok=True)
    return payload, text


def _candidate_audio_ordinals(row, settings: Settings) -> list[int]:
    streams = json.loads(row["audio_streams_json"] or "[]")
    available = [int(stream.get("ordinal", index)) for index, stream in enumerate(streams)]
    if not available:
        return [0]
    track_scores = json.loads(row["track_scores_json"] or "{}")
    ranked = sorted(available, key=lambda ordinal: float(track_scores.get(str(ordinal), 0)), reverse=True)
    selected = []
    for ordinal in [0, int(settings.mic_audio_ordinal), *ranked]:
        if ordinal in available and ordinal not in selected:
            selected.append(ordinal)
        if len(selected) >= settings.max_transcription_tracks:
            break
    return selected


def _normalized_word(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def merge_track_words(choices: list[dict]) -> list[dict]:
    """Merge separately transcribed tracks into one deduplicated conversation."""
    candidates: list[dict] = []
    for choice in choices:
        for word in choice.get("labeled_words") or []:
            candidates.append(
                dict(
                    word,
                    track_ordinal=int(choice["audio_ordinal"]),
                    source_score=float(choice["selection_score"]),
                )
            )
    merged: list[dict] = []
    for word in sorted(candidates, key=lambda item: (float(item["start"]), -float(item["source_score"]))):
        normalized = _normalized_word(str(word.get("text", "")))
        if not normalized:
            continue
        duplicate_index = next(
            (
                index
                for index in range(max(0, len(merged) - 14), len(merged))
                if _normalized_word(str(merged[index].get("text", ""))) == normalized
                and abs(float(merged[index]["start"]) - float(word["start"])) <= 0.7
            ),
            None,
        )
        if duplicate_index is None:
            merged.append(word)
            continue
        existing = merged[duplicate_index]
        existing_priority = (
            1 if existing.get("speaker") == "Me" else 0,
            float(existing.get("confidence") or 0),
            float(existing.get("source_score") or 0),
        )
        new_priority = (
            1 if word.get("speaker") == "Me" else 0,
            float(word.get("confidence") or 0),
            float(word.get("source_score") or 0),
        )
        if new_priority > existing_priority:
            merged[duplicate_index] = word
    return sorted(merged, key=lambda item: (float(item["start"]), float(item["end"])))


def _conversation_payload(choices: list[dict], best: dict) -> tuple[dict, str]:
    words = merge_track_words(choices)
    if not words:
        return best["payload"], best["text"]
    text = _join_words(words)
    return {
        "engine": best["payload"].get("engine", "parakeet"),
        "transcription": [{
            "text": text,
            "start": float(words[0]["start"]),
            "end": float(words[-1]["end"]),
            "words": words,
        }],
    }, text


def transcribe_candidate(
    settings: Settings,
    candidate_id: int,
    progress: Callable[[str], None] | None = None,
    refine: bool = True,
) -> str:
    with database(settings.db_path) as connection:
        row = connection.execute(
            """
            SELECT c.*, v.path AS video_path, v.audio_streams_json
            FROM candidates c JOIN videos v ON v.id = c.video_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Candidate {candidate_id} does not exist")

    settings.work_dir.mkdir(parents=True, exist_ok=True)
    duration = float(row["end_sec"]) - float(row["start_sec"])
    track_scores = json.loads(row["track_scores_json"] or "{}")
    chapter_marker = track_scores.get("chapter_marker") if isinstance(track_scores, dict) else None
    audio_events = json.loads(row["audio_events_json"] or "[]")
    choices = []
    errors = []
    next_person = 1
    for audio_ordinal in _candidate_audio_ordinals(row, settings):
        wav_path = settings.work_dir / f"candidate-{candidate_id}-track-{audio_ordinal}.wav"
        output_base = settings.work_dir / f"candidate-{candidate_id}-track-{audio_ordinal}-transcript"
        try:
            if progress:
                progress(f"extracting audio track {audio_ordinal + 1}")
            _extract_audio(
                settings,
                Path(row["video_path"]),
                float(row["start_sec"]),
                duration,
                wav_path,
                audio_ordinal,
            )
            if settings.transcription_engine == "parakeet":
                payload, text = _transcribe_parakeet(settings, wav_path, progress)
            else:
                payload, text = _transcribe_whisper(settings, wav_path, output_base, progress)
            words = gate_words_by_audio(transcript_words(payload), wav_path)
            if not words:
                if progress:
                    progress(f"track {audio_ordinal + 1}: ignored transcript over silence")
                continue
            text = _join_words(words)
            payload = {
                **payload,
                "transcription": [{"text": text, "start": float(words[0]["start"]), "end": float(words[-1]["end"]), "words": words}],
            }
            fixed_label = (
                "Me" if int(settings.mic_audio_ordinal) == int(audio_ordinal) else None
            )
            labeled_words, next_person = label_words(
                words, wav_path, fixed_label=fixed_label, person_start=next_person
            )
            humor = transcript_humor_score(text)
            penalty = music_repetition_penalty(text)
            density = min(1.0, len(text.split()) / 80.0)
            energy = float(track_scores.get(str(audio_ordinal), 0))
            selection_score = (humor * 0.62 + density * 0.20 + energy * 0.18) * (1 - penalty * 0.85)
            choices.append(
                {
                    "audio_ordinal": audio_ordinal,
                    "payload": payload,
                    "text": text,
                    "humor": humor,
                    "music_penalty": penalty,
                    "selection_score": selection_score,
                    "labeled_words": labeled_words,
                }
            )
        except Exception as exc:
            errors.append(f"track {audio_ordinal + 1}: {exc}")
        finally:
            wav_path.unlink(missing_ok=True)

    if not choices and settings.transcription_engine == "parakeet":
        wav_path = settings.work_dir / f"candidate-{candidate_id}-fallback.wav"
        output_base = settings.work_dir / f"candidate-{candidate_id}-fallback-transcript"
        try:
            _extract_audio(settings, Path(row["video_path"]), float(row["start_sec"]), duration, wav_path, 0)
            payload, text = _transcribe_whisper(settings, wav_path, output_base, progress)
            choices.append(
                {
                    "audio_ordinal": 0,
                    "payload": payload,
                    "text": text,
                    "humor": transcript_humor_score(text),
                    "music_penalty": music_repetition_penalty(text),
                    "selection_score": transcript_humor_score(text),
                }
            )
        finally:
            wav_path.unlink(missing_ok=True)
    if not choices:
        raise RuntimeError("Transcription failed: " + "; ".join(errors))

    best = max(choices, key=lambda choice: choice["selection_score"])
    payload, text = _conversation_payload(choices, best)
    payload["clipmaker"] = {
        # OBS track 1 is conventionally the complete stream mix. Keep that for
        # finished audio while using every isolated track to understand dialogue.
        "audio_ordinal": 0,
        "primary_transcript_ordinal": best["audio_ordinal"],
        "mic_audio_ordinal": int(settings.mic_audio_ordinal),
        "conversation_fused": len(choices) > 1,
        "engine": payload.get("engine", settings.transcription_engine),
        "obs_chapter": chapter_marker if isinstance(chapter_marker, dict) else None,
        "audio_events": audio_events,
        "track_choices": [
            {
                "audio_ordinal": choice["audio_ordinal"],
                "selection_score": round(choice["selection_score"], 4),
                "word_count": len(choice["text"].split()),
            }
            for choice in choices
        ],
    }
    humor_score = transcript_humor_score(text)
    music_penalty = music_repetition_penalty(text)
    with database(settings.db_path) as connection:
        from .taste import taste_match

        taste_score, taste_evidence = taste_match(connection, text)
        if taste_evidence:
            combined_score = min(
                1.0,
                (
                    float(row["energy_score"]) * 0.36
                    + humor_score * 0.34
                    + taste_score * 0.30
                )
                * (1.0 - music_penalty * 0.82),
            )
        else:
            combined_score = min(
                1.0,
                (float(row["energy_score"]) * 0.45 + humor_score * 0.55)
                * (1.0 - music_penalty * 0.82),
            )
        if isinstance(chapter_marker, dict):
            combined_score = min(1.0, combined_score + 0.16)
        from .audio_events import event_bonus
        combined_score = min(1.0, combined_score + event_bonus(audio_events))
        reason = (
            "Likely music or repetitive speech; downranked"
            if music_penalty > 0.25
            else (
                "Matches your comedy taste plus strong reaction/dialogue signals"
                if taste_evidence and taste_score >= 0.58
                else "Excited audio plus transcript reaction/laughter signals"
                if humor_score > 0.2
                else "Excited audio; transcript has limited humor signals"
            )
        )
        if isinstance(chapter_marker, dict):
            reason = "OBS-flagged moment - " + reason
        if refine:
            try:
                from .moment_refine import refine_moment

                refinement = refine_moment(settings, row, payload, progress)
                payload["clipmaker"]["moment_refinement"] = refinement
                combined_score = min(
                    1.0, combined_score * 0.78 + refinement["quality"] * 0.22
                )
                reason = ("OBS-flagged moment - " if isinstance(chapter_marker, dict) else "") + "Qwen: " + refinement["reason"]
            except Exception as exc:
                payload["clipmaker"]["moment_refinement"] = {
                    "version": 1,
                    "status": "fallback",
                    "start": 0.0,
                    "end": round(duration, 3),
                    "duration": round(duration, 3),
                    "error": str(exc)[:240],
                }
                if progress:
                    progress(
                        "Qwen refinement unavailable; keeping the audio-selected moment"
                    )
        else:
            payload["clipmaker"]["moment_refinement"] = {
                "version": 1,
                "status": "pending",
                "start": 0.0,
                "end": round(duration, 3),
                "duration": round(duration, 3),
            }

        connection.execute(
            """
            UPDATE candidates SET
                transcript = ?, transcript_json = ?, transcript_score = ?, taste_score = ?, score = ?,
                reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                text,
                json.dumps(payload),
                humor_score,
                taste_score,
                combined_score,
                reason,
                candidate_id,
            ),
        )
    return text
