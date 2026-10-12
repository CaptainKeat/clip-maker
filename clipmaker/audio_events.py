from __future__ import annotations

import json
import math
import subprocess
import threading
import wave
from pathlib import Path
from typing import Callable

from .media import CREATE_NO_WINDOW
from .settings import Settings

MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
_MODEL = None
_EXTRACTOR = None
_LOCK = threading.Lock()

EVENT_GROUPS = {
    "laughter": ("laughter", "giggle", "snicker", "belly laugh", "chuckle", "chortle"),
    "yelling": ("screaming", "scream", "shout", "yell", "whoop", "battle cry"),
    "impact": ("explosion", "gunshot", "gunfire", "bang", "slam", "crash", "thump", "thud", "smash"),
    "applause": ("applause", "clapping"),
    "crying": ("crying", "sobbing", "wail", "moan"),
}

def _load_model(progress: Callable[[str], None] | None = None):
    global _MODEL, _EXTRACTOR
    with _LOCK:
        if _MODEL is None or _EXTRACTOR is None:
            if progress:
                progress("loading local AudioSet listener (first use may download the model)")
            from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
            _EXTRACTOR = AutoFeatureExtractor.from_pretrained(MODEL_ID)
            _MODEL = AutoModelForAudioClassification.from_pretrained(MODEL_ID)
            _MODEL.eval()
        return _EXTRACTOR, _MODEL

def _read_pcm16(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate

def _windows(samples, rate: int, duration: float, peak_inside: float):
    import numpy as np
    size = rate * 10
    centers = [5.0, duration / 2.0, max(0.0, peak_inside), max(0.0, duration - 5.0)]
    used = set()
    for center in centers:
        start = max(0, min(max(0, len(samples) - size), int((center - 5.0) * rate)))
        key = round(start / rate, 1)
        if key in used:
            continue
        used.add(key)
        chunk = samples[start : start + size]
        if len(chunk) < size:
            chunk = np.pad(chunk, (0, size - len(chunk)))
        yield key, chunk

def _semantic_events(samples, rate: int, duration: float, peak_inside: float, progress=None):
    import torch
    extractor, model = _load_model(progress)
    found: dict[str, dict] = {}
    for start, chunk in _windows(samples, rate, duration, peak_inside):
        inputs = extractor(chunk, sampling_rate=rate, return_tensors="pt")
        with torch.inference_mode():
            scores = torch.sigmoid(model(**inputs).logits)[0]
        top = torch.topk(scores, k=min(18, scores.numel()))
        for raw_score, raw_index in zip(top.values.tolist(), top.indices.tolist()):
            label = str(model.config.id2label.get(int(raw_index), raw_index))
            low = label.lower()
            for kind, needles in EVENT_GROUPS.items():
                if any(needle in low for needle in needles):
                    threshold = 0.19 if kind == "laughter" else 0.24
                    if raw_score >= threshold and raw_score > found.get(kind, {}).get("score", 0):
                        found[kind] = {
                            "type": kind,
                            "score": round(float(raw_score), 4),
                            "time_sec": round(start + 5.0, 2),
                            "label": label,
                            "source": "audioset",
                        }
    return list(found.values())

def _signal_events(samples, rate: int, peak_inside: float, track_scores: dict) -> list[dict]:
    import numpy as np
    events = []
    numeric = [float(value) for key, value in track_scores.items() if str(key).isdigit()]
    active = sum(value >= 0.28 for value in numeric)
    if active >= 2:
        events.append({
            "type": "speech_overlap",
            "score": round(min(1.0, 0.48 + active * 0.12), 4),
            "time_sec": round(peak_inside, 2),
            "label": f"{active} OBS tracks reacted together",
            "source": "multitrack",
        })
    center = max(0, min(len(samples) - 1, int(peak_inside * rate)))
    before = samples[max(0, center - int(1.2 * rate)) : center]
    after = samples[center : min(len(samples), center + int(1.8 * rate))]
    if len(before) and len(after):
        before_rms = math.sqrt(float(np.mean(before * before)) + 1e-12)
        after_rms = math.sqrt(float(np.mean(after * after)) + 1e-12)
        drop_db = 20 * math.log10(max(before_rms, 1e-6) / max(after_rms, 1e-6))
        if drop_db >= 12:
            events.append({
                "type": "sudden_silence",
                "score": round(min(1.0, drop_db / 24.0), 4),
                "time_sec": round(peak_inside + 0.8, 2),
                "label": "Sudden silence after a reaction",
                "source": "signal",
            })
    return events

def detect_candidate_events(
    settings: Settings,
    row,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    start = float(row["start_sec"])
    duration = max(0.1, float(row["end_sec"]) - start)
    peak_inside = max(0.0, min(duration, float(row["peak_sec"] or start) - start))
    wav_path = settings.work_dir / f"audio-events-{int(row['id'])}.wav"
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(row["video_path"]),
        "-t", f"{duration:.3f}", "-map", "0:a:0", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(wav_path),
    ]
    subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
    try:
        samples, rate = _read_pcm16(wav_path)
        track_scores = json.loads(row["track_scores_json"] or "{}")
        events = _signal_events(samples, rate, peak_inside, track_scores)
        try:
            events.extend(_semantic_events(samples, rate, duration, peak_inside, progress))
        except Exception as exc:
            if progress:
                progress(f"AudioSet listener unavailable; using signal events ({str(exc)[-120:]})")
        best = {}
        for event in events:
            kind = event["type"]
            if event["score"] > best.get(kind, {}).get("score", 0):
                best[kind] = event
        return sorted(best.values(), key=lambda item: item["score"], reverse=True)
    finally:
        wav_path.unlink(missing_ok=True)

def event_bonus(events: list[dict]) -> float:
    weights = {
        "laughter": 0.18,
        "yelling": 0.10,
        "speech_overlap": 0.09,
        "impact": 0.07,
        "sudden_silence": 0.05,
        "applause": 0.04,
    }
    return min(0.26, sum(weights.get(str(item.get("type")), 0) * float(item.get("score", 0)) for item in events))
