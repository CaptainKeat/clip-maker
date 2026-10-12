from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Callable

from .captions import transcript_words
from .db import database
from .media import CREATE_NO_WINDOW
from .motion_captions import append_motion_ass, normalize_motion
from .render import AUDIO_FILTERS, _candidate, _write_ass
from .settings import Settings
from .storage import export_media_url, vod_output_directory

CAPTION_PRESETS = {"punch", "clean", "chaos"}
AUDIO_PRESETS = {"natural", "soft", "restored"}


def _editor_options(raw: dict | None = None) -> dict:
    raw = raw or {}
    mode = str(raw.get("mode") or "fit")
    return {
        "mode": mode if mode in {"fit", "fill"} else "fit",
        "zoom": round(max(1.0, min(2.5, float(raw.get("zoom") or 1.0))), 2),
        "x": round(max(-100.0, min(100.0, float(raw.get("x") or 0))), 1),
        "y": round(max(-100.0, min(100.0, float(raw.get("y") or 0))), 1),
        "caption_scale": round(max(0.6, min(1.6, float(raw.get("caption_scale") or 1.0))), 2),
        "caption_y": int(max(-500, min(500, int(raw.get("caption_y") or 0)))),
        "fade_in": round(max(0.0, min(5.0, float(raw.get("fade_in") or 0))), 2),
        "fade_out": round(max(0.0, min(5.0, float(raw.get("fade_out") or 0))), 2),
    }



SEGMENT_TRANSITIONS = {"none", "fade", "dissolve"}

def _normalize_segments(raw_segments: list, start: float, end: float) -> list[dict]:
    normalized = []
    for index, raw in enumerate((raw_segments or [])[:80]):
        if not isinstance(raw, dict):
            continue
        source_start = max(0.0, min(end, float(raw.get("source_start", start))))
        source_end = max(source_start, min(end, float(raw.get("source_end", source_start))))
        if source_end - source_start < 0.08:
            continue
        transition = str(raw.get("transition") or "none")
        normalized.append({
            "id": str(raw.get("id") or f"segment-{index + 1}")[:80],
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "speed": round(max(0.25, min(4.0, float(raw.get("speed") or 1.0))), 3),
            "freeze": round(max(0.0, min(10.0, float(raw.get("freeze") or 0.0))), 3),
            "transition": transition if transition in SEGMENT_TRANSITIONS else "none",
        })
    if not normalized:
        normalized = [{
            "id": "segment-1", "source_start": round(start, 3), "source_end": round(end, 3),
            "speed": 1.0, "freeze": 0.0, "transition": "none",
        }]
    return normalized

def _segments_from_cuts(start: float, end: float, cuts: list[dict]) -> list[dict]:
    segments = []
    cursor = start
    for cut in cuts:
        cut_start, cut_end = float(cut["start"]), float(cut["end"])
        if cut_start > cursor + 0.001:
            segments.append({"source_start": cursor, "source_end": cut_start})
        cursor = max(cursor, cut_end)
    if end > cursor + 0.001:
        segments.append({"source_start": cursor, "source_end": end})
    return _normalize_segments(segments, start, end)

def _segment_output_duration(segment: dict) -> float:
    return (float(segment["source_end"]) - float(segment["source_start"])) / float(segment.get("speed") or 1.0) + float(segment.get("freeze") or 0.0)

def _output_duration(segments: list[dict]) -> float:
    return sum(_segment_output_duration(segment) for segment in segments)

def _caption_after_segments(item: dict, segments: list[dict]) -> dict | None:
    source_start, source_end = float(item["start"]), float(item["end"])
    output_cursor = 0.0
    for segment in segments:
        left, right = float(segment["source_start"]), float(segment["source_end"])
        speed = float(segment.get("speed") or 1.0)
        if source_start >= left and source_end <= right + 0.04:
            return {
                **item,
                "start": round(output_cursor + (source_start - left) / speed, 3),
                "end": round(output_cursor + (source_end - left) / speed, 3),
            }
        output_cursor += _segment_output_duration(segment)
    return None

def _normalize_color(raw: dict | None = None) -> dict:
    raw = raw or {}
    return {
        "brightness": round(max(-1.0, min(1.0, float(raw.get("brightness") or 0.0))), 3),
        "contrast": round(max(0.0, min(2.0, float(raw.get("contrast") or 1.0))), 3),
        "saturation": round(max(0.0, min(3.0, float(raw.get("saturation") or 1.0))), 3),
        "temperature": round(max(-1.0, min(1.0, float(raw.get("temperature") or 0.0))), 3),
    }

def _normalize_assets(items: list, kind: str, output_duration: float) -> list[dict]:
    result = []
    for index, raw in enumerate((items or [])[:30]):
        if not isinstance(raw, dict) or not str(raw.get("path") or ""):
            continue
        start = max(0.0, min(output_duration, float(raw.get("start") or 0.0)))
        duration = max(0.1, min(output_duration - start, float(raw.get("duration") or 3.0)))
        base = {
            "id": str(raw.get("id") or f"{kind}-{index + 1}")[:80],
            "name": str(raw.get("name") or Path(str(raw["path"])).name)[:160],
            "path": str(raw["path"]),
            "start": round(start, 3),
            "duration": round(duration, 3),
        }
        if kind == "overlay":
            base.update({
                "x": round(max(-100.0, min(100.0, float(raw.get("x") or 0))), 1),
                "y": round(max(-100.0, min(100.0, float(raw.get("y") or 0))), 1),
                "scale": round(max(0.1, min(2.5, float(raw.get("scale") or 0.55))), 2),
            })
        else:
            base["gain_db"] = round(max(-36.0, min(12.0, float(raw.get("gain_db") or 0))), 1)
        result.append(base)
    return result
def _normalize_cuts(raw_cuts: list, start: float, end: float) -> list[dict]:
    spans = []
    for raw in raw_cuts[:50]:
        if not isinstance(raw, dict):
            continue
        cut_start = max(start, min(end, float(raw.get("start", start))))
        cut_end = max(start, min(end, float(raw.get("end", cut_start))))
        if cut_end - cut_start >= 0.1:
            spans.append([cut_start, cut_end])
    spans.sort()
    merged = []
    for cut_start, cut_end in spans:
        if merged and cut_start <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], cut_end)
        else:
            merged.append([cut_start, cut_end])
    removed = sum(cut_end - cut_start for cut_start, cut_end in merged)
    if end - start - removed < 3:
        raise ValueError("Cuts must leave at least 3 seconds of video")
    return [{"start": round(cut_start, 3), "end": round(cut_end, 3)} for cut_start, cut_end in merged]


def _keep_segments(start: float, end: float, cuts: list[dict]) -> list[tuple[float, float]]:
    cursor = start
    segments = []
    for cut in cuts:
        cut_start, cut_end = float(cut["start"]), float(cut["end"])
        if cut_start > cursor + 0.001:
            segments.append((cursor - start, cut_start - start))
        cursor = max(cursor, cut_end)
    if end > cursor + 0.001:
        segments.append((cursor - start, end - start))
    return segments


def _caption_after_cuts(item: dict, start: float, cuts: list[dict]) -> dict | None:
    word_start, word_end = float(item["start"]), float(item["end"])
    if any(word_start < float(cut["end"]) and word_end > float(cut["start"]) for cut in cuts):
        return None
    removed_before = sum(
        max(0.0, float(cut["end"]) - float(cut["start"]))
        for cut in cuts if float(cut["end"]) <= word_start
    )
    return {
        **item,
        "start": max(0.0, word_start - start - removed_before),
        "end": max(0.04, word_end - start - removed_before),
    }


def _editor_row(settings: Settings, candidate_id: int) -> dict:
    with database(settings.db_path) as connection:
        row = connection.execute(
            """
            SELECT c.*, v.path AS video_path, v.name AS video_name,
                   v.duration_sec AS video_duration, v.container,
                   v.audio_streams_json, v.clip_folder_name
            FROM candidates c JOIN videos v ON v.id=c.video_id WHERE c.id=?
            """, (candidate_id,),
        ).fetchone()
        variant = connection.execute(
            "SELECT * FROM candidate_variants WHERE candidate_id=? ORDER BY attempt DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        draft = connection.execute(
            "SELECT * FROM clip_edit_drafts WHERE candidate_id=?", (candidate_id,),
        ).fetchone()
        features = connection.execute(
            "SELECT audio_ordinal, frames_json FROM audio_features WHERE video_id=? ORDER BY audio_ordinal",
            (int(row["video_id"]) if row else -1,),
        ).fetchall()
    if not row:
        raise ValueError(f"Candidate {candidate_id} does not exist")
    result = dict(row)
    result["active_variant"] = dict(variant) if variant else None
    result["draft"] = dict(draft) if draft else None
    result["audio_features"] = {int(item["audio_ordinal"]): json.loads(item["frames_json"]) for item in features}
    return result


def _default_captions(row: dict) -> list[dict]:
    if not row.get("transcript_json"):
        return []
    base = float(row["start_sec"])
    return [{
        "text": str(word.get("text") or "").strip(),
        "start": round(base + float(word["start"]), 3),
        "end": round(base + float(word["end"]), 3),
        "speaker": word.get("speaker"),
        "track_ordinal": word.get("track_ordinal"),
    } for word in transcript_words(json.loads(row["transcript_json"]))
      if str(word.get("text") or "").strip()]


def _default_audio_mix(row: dict, captions: list[dict]) -> list[dict]:
    streams = json.loads(row.get("audio_streams_json") or "[]")
    spoken = {int(item["track_ordinal"]) for item in captions if item.get("track_ordinal") is not None}
    if not spoken and streams:
        spoken = {0}
    return [{
        "ordinal": int(stream.get("ordinal", index)),
        "enabled": int(stream.get("ordinal", index)) in spoken,
        "gain_db": 0.0,
    } for index, stream in enumerate(streams)]


def editor_payload(settings: Settings, candidate_id: int) -> dict:
    row = _editor_row(settings, candidate_id)
    variant, draft = row["active_variant"], row["draft"]
    captions = json.loads(draft["captions_json"]) if draft else _default_captions(row)
    audio_mix = json.loads(draft["audio_mix_json"]) if draft else _default_audio_mix(row, captions)
    if draft:
        start, end = float(draft["source_start_sec"]), float(draft["source_end_sec"])
        caption_preset, audio_preset = str(draft["caption_preset"]), str(draft["audio_preset"])
        crop = _editor_options(json.loads(draft["crop_json"] or "{}"))
        cuts = _normalize_cuts(json.loads(draft["cuts_json"] or "[]"), start, end)
    else:
        start = float(variant["clip_start_sec"]) if variant else float(row["start_sec"])
        end = float(variant["clip_end_sec"]) if variant else float(row["end_sec"])
        caption_preset = str(variant["caption_preset"]) if variant else "punch"
        audio_preset = str(variant["audio_preset"]) if variant else "natural"
        crop, cuts = _editor_options(), []
    if draft:
        raw_segments = json.loads(draft.get("segments_json") or "[]")
        segments = (_normalize_segments(raw_segments, start, end) if raw_segments
                    else _segments_from_cuts(start, end, cuts))
        color = _normalize_color(json.loads(draft.get("color_json") or "{}"))
        output_duration = _output_duration(segments)
        overlays = _normalize_assets(json.loads(draft.get("overlays_json") or "[]"), "overlay", output_duration)
        sfx = _normalize_assets(json.loads(draft.get("sfx_json") or "[]"), "sfx", output_duration)
    else:
        segments = _normalize_segments([], start, end)
        color, overlays, sfx = _normalize_color(), [], []
    streams = json.loads(row.get("audio_streams_json") or "[]")
    edit_min = max(0.0, start - 120.0)
    edit_max = min(float(row["video_duration"]), end + 120.0)
    mix_by_ordinal = {int(item["ordinal"]): item for item in audio_mix}
    tracks = []
    for index, stream in enumerate(streams):
        ordinal = int(stream.get("ordinal", index))
        title = str(stream.get("title") or "").strip()
        mix = mix_by_ordinal.get(ordinal, {"enabled": False, "gain_db": 0})
        tracks.append({
            **stream, "ordinal": ordinal,
            "label": "Mic" if ordinal == int(settings.mic_audio_ordinal) else (title or f"Audio track {ordinal + 1}"),
            "enabled": bool(mix.get("enabled")),
            "gain_db": float(mix.get("gain_db") or 0),
            "keyframes": mix.get("keyframes") or [],
            "waveform_start": int(edit_min),
            "waveform": row["audio_features"].get(ordinal, [])[int(edit_min):int(edit_max) + 1],
        })
    return {
        "candidate_id": candidate_id, "video_id": int(row["video_id"]),
        "video_name": row["video_name"], "video_duration": float(row["video_duration"]),
        "source_url": f"/media/source/{int(row['video_id'])}",
        "preview_url": export_media_url(settings.output_dir, variant["output_path"]) if variant and variant.get("output_path") else None,
        "base_variant_id": int(variant["id"]) if variant else None,
        "source_start_sec": start, "source_end_sec": end,
        "edit_min_sec": edit_min, "edit_max_sec": edit_max,
        "caption_preset": caption_preset, "audio_preset": audio_preset,
        "captions": captions, "audio_tracks": tracks, "crop": crop, "cuts": cuts,
        "segments": segments, "overlays": overlays, "sfx": sfx, "color": color,
        "has_draft": bool(draft), "source_container": row.get("container") or "",
    }


def save_editor_draft(settings: Settings, candidate_id: int, body: dict) -> dict:
    row = _editor_row(settings, candidate_id)
    video_duration = float(row["video_duration"])
    start = max(0.0, min(video_duration, float(body.get("source_start_sec", row["start_sec"]))))
    end = max(start, min(video_duration, float(body.get("source_end_sec", row["end_sec"]))))
    if end - start < 3:
        raise ValueError("The edited clip must be at least 3 seconds long")
    if end - start > 180:
        raise ValueError("The edited clip cannot be longer than 3 minutes")
    captions = []
    for raw in (body.get("captions") or [])[:3000]:
        text = str(raw.get("text") or "").strip()[:160]
        word_start = max(start, min(end, float(raw.get("start", start))))
        word_end = max(word_start + 0.04, min(end, float(raw.get("end", word_start + 0.3))))
        if text and word_end > word_start:
            captions.append({
                "text": text, "start": round(word_start, 3), "end": round(word_end, 3),
                "speaker": raw.get("speaker"), "track_ordinal": raw.get("track_ordinal"),
                "motion": normalize_motion(raw.get("motion"), word_start, word_end),
            })
    captions.sort(key=lambda item: (item["start"], item["end"]))
    streams = json.loads(row.get("audio_streams_json") or "[]")
    available = {int(stream.get("ordinal", index)) for index, stream in enumerate(streams)}
    audio_mix = []
    for raw in body.get("audio_mix") or []:
        ordinal = int(raw.get("ordinal", -1))
        if ordinal in available:
            audio_mix.append({
                "ordinal": ordinal, "enabled": bool(raw.get("enabled")),
                "gain_db": round(max(-24.0, min(12.0, float(raw.get("gain_db") or 0))), 1),
                "keyframes": [
                    {"time": round(max(start, min(end, float(key.get("time", start)))), 3),
                     "gain_db": round(max(-36.0, min(12.0, float(key.get("gain_db") or 0))), 1)}
                    for key in (raw.get("keyframes") or [])[:40] if isinstance(key, dict)
                ],
            })
    if available and not any(item["enabled"] for item in audio_mix):
        raise ValueError("Keep at least one audio track enabled")
    caption_preset = str(body.get("caption_preset") or "punch")
    audio_preset = str(body.get("audio_preset") or "natural")
    if caption_preset not in CAPTION_PRESETS or audio_preset not in AUDIO_PRESETS:
        raise ValueError("Invalid editor preset")
    crop = _editor_options(body.get("crop") if isinstance(body.get("crop"), dict) else {})
    cuts = _normalize_cuts(body.get("cuts") if isinstance(body.get("cuts"), list) else [], start, end)
    segments = _normalize_segments(body.get("segments") if isinstance(body.get("segments"), list) else [], 0.0, video_duration)
    start = min(float(segment["source_start"]) for segment in segments)
    end = max(float(segment["source_end"]) for segment in segments)
    output_duration = _output_duration(segments)
    if output_duration < 3 or output_duration > 240:
        raise ValueError("The finished edit must be between 3 seconds and 4 minutes")
    overlays = _normalize_assets(body.get("overlays") if isinstance(body.get("overlays"), list) else [], "overlay", output_duration)
    sfx = _normalize_assets(body.get("sfx") if isinstance(body.get("sfx"), list) else [], "sfx", output_duration)
    color = _normalize_color(body.get("color") if isinstance(body.get("color"), dict) else {})
    base_variant_id = body.get("base_variant_id")
    with database(settings.db_path) as connection:
        connection.execute(
            """
            INSERT INTO clip_edit_drafts (
                candidate_id, base_variant_id, source_start_sec, source_end_sec,
                caption_preset, audio_preset, captions_json, audio_mix_json,
                crop_json, cuts_json, segments_json, overlays_json, sfx_json, color_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(candidate_id) DO UPDATE SET
                base_variant_id=excluded.base_variant_id,
                source_start_sec=excluded.source_start_sec, source_end_sec=excluded.source_end_sec,
                caption_preset=excluded.caption_preset, audio_preset=excluded.audio_preset,
                captions_json=excluded.captions_json, audio_mix_json=excluded.audio_mix_json,
                crop_json=excluded.crop_json, cuts_json=excluded.cuts_json,
                segments_json=excluded.segments_json, overlays_json=excluded.overlays_json,
                sfx_json=excluded.sfx_json, color_json=excluded.color_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (candidate_id, int(base_variant_id) if base_variant_id else None, start, end,
             caption_preset, audio_preset, json.dumps(captions), json.dumps(audio_mix),
             json.dumps(crop), json.dumps(cuts), json.dumps(segments),
             json.dumps(overlays), json.dumps(sfx), json.dumps(color)),
        )
    return editor_payload(settings, candidate_id)


def save_editor_asset(settings: Settings, candidate_id: int, body: dict) -> dict:
    name = Path(str(body.get("name") or "asset.bin")).name
    suffix = Path(name).suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".mov", ".webm", ".mp3", ".wav", ".m4a", ".ogg"}
    if suffix not in allowed:
        raise ValueError("Use an image, short video, or audio file")
    encoded = str(body.get("data") or "")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    payload = base64.b64decode(encoded, validate=True)
    if not payload or len(payload) > 80 * 1024 * 1024:
        raise ValueError("Editor assets must be between 1 byte and 80 MB")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(name).stem).strip("-")[:60] or "asset"
    asset_dir = Path(settings.data_dir) / "editor-assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    serial = len(list(asset_dir.glob(f"candidate-{candidate_id}-*"))) + 1
    destination = asset_dir / f"candidate-{candidate_id}-{serial:03d}-{safe_stem}{suffix}"
    destination.write_bytes(payload)
    requested = str(body.get("kind") or "")
    media_type = requested if requested in {"overlay", "sfx"} else ("overlay" if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".mov", ".webm"} else "sfx")
    return {"name": name, "path": str(destination), "url": f"/media/editor-asset/{destination.name}", "media_type": media_type}

def regenerate_editor_captions(settings: Settings, candidate_id: int, progress: Callable[[str], None] | None = None) -> str:
    from .transcribe import transcribe_candidate
    data = editor_payload(settings, candidate_id)
    with database(settings.db_path) as connection:
        original = connection.execute(
            "SELECT score, transcript_score, taste_score, reason FROM candidates WHERE id=?", (candidate_id,),
        ).fetchone()
        connection.execute(
            "UPDATE candidates SET start_sec=?, end_sec=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (data["source_start_sec"], data["source_end_sec"], candidate_id),
        )
    transcribe_candidate(settings, candidate_id, progress=progress, refine=False)
    with database(settings.db_path) as connection:
        refreshed = connection.execute("SELECT transcript_json FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        connection.execute(
            "UPDATE candidates SET score=?, transcript_score=?, taste_score=?, reason=? WHERE id=?",
            (original["score"], original["transcript_score"], original["taste_score"], original["reason"], candidate_id),
        )
        words = transcript_words(json.loads(refreshed["transcript_json"]))
        captions = [{
            "text": str(word.get("text") or "").strip(),
            "start": round(data["source_start_sec"] + float(word["start"]), 3),
            "end": round(data["source_start_sec"] + float(word["end"]), 3),
            "speaker": word.get("speaker"), "track_ordinal": word.get("track_ordinal"),
        } for word in words if str(word.get("text") or "").strip()]
        connection.execute(
            "UPDATE clip_edit_drafts SET captions_json=?, updated_at=CURRENT_TIMESTAMP WHERE candidate_id=?",
            (json.dumps(captions), candidate_id),
        )
    if progress:
        progress("editor subtitles ready")
    return f"{len(captions)} subtitle words ready"


def _editor_filter_graph(
    ass_path: Path | None,
    audio_mix: list[dict],
    audio_preset: str,
    segments: list[tuple[float, float]],
    options: dict,
    output_duration: float,
) -> tuple[str, str, str]:
    enabled = [item for item in audio_mix if item.get("enabled")] or [{"ordinal": 0, "gain_db": 0}]
    parts = []
    inputs = []
    for index, item in enumerate(enabled):
        gain = math.pow(10.0, float(item.get("gain_db") or 0) / 20.0)
        parts.append(
            f"[0:a:{int(item['ordinal'])}]aresample=48000,volume={gain:.6f},"
            f"loudnorm=I=-22:LRA=15:TP=-3[ain{index}]"
        )
        inputs.append(f"[ain{index}]")
    if len(inputs) > 1:
        parts.append(
            f"{''.join(inputs)}amix=inputs={len(inputs)}:duration=longest:"
            "dropout_transition=0:normalize=0[mixed]"
        )
        mixed = "[mixed]"
    else:
        mixed = inputs[0]

    if len(segments) > 1:
        video_sources = "".join(f"[vsrc{index}]" for index in range(len(segments)))
        audio_sources = "".join(f"[asrc{index}]" for index in range(len(segments)))
        parts.append(f"[0:v]split={len(segments)}{video_sources}")
        parts.append(f"{mixed}asplit={len(segments)}{audio_sources}")
        for index, (seg_start, seg_end) in enumerate(segments):
            parts.append(
                f"[vsrc{index}]trim=start={seg_start:.3f}:end={seg_end:.3f},"
                f"setpts=PTS-STARTPTS[vcut{index}]"
            )
            parts.append(
                f"[asrc{index}]atrim=start={seg_start:.3f}:end={seg_end:.3f},"
                f"asetpts=PTS-STARTPTS[acut{index}]"
            )
        concat_inputs = "".join(
            f"[vcut{index}][acut{index}]" for index in range(len(segments))
        )
        parts.append(
            f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[cutv][cuta]"
        )
    else:
        seg_start, seg_end = segments[0]
        parts.append(
            f"[0:v]trim=start={seg_start:.3f}:end={seg_end:.3f},"
            "setpts=PTS-STARTPTS[cutv]"
        )
        parts.append(
            f"{mixed}atrim=start={seg_start:.3f}:end={seg_end:.3f},"
            "asetpts=PTS-STARTPTS[cuta]"
        )

    mode = options.get("mode", "fit")
    zoom = float(options.get("zoom", 1.0))
    pos_x = float(options.get("x", 0.0))
    pos_y = float(options.get("y", 0.0))
    target_w, target_h = round(1080 * zoom), round(1920 * zoom)
    if mode == "fill":
        x_ratio = (pos_x + 100.0) / 200.0
        y_ratio = (pos_y + 100.0) / 200.0
        parts.append(
            f"[cutv]fps=30,scale={target_w}:{target_h}:"
            "force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='(iw-ow)*{x_ratio:.4f}':"
            f"y='(ih-oh)*{y_ratio:.4f}'[base]"
        )
    else:
        parts.append("[cutv]fps=30,split=2[bgsrc][fgsrc]")
        parts.append(
            "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,"
            "crop=270:480,boxblur=12:4,scale=1080:1920,"
            "eq=brightness=-0.10:saturation=0.72[bg]"
        )
        parts.append(
            f"[fgsrc]scale={target_w}:{target_h}:"
            "force_original_aspect_ratio=decrease[fg]"
        )
        offset_x, offset_y = pos_x * 4.0, pos_y * 7.0
        parts.append(
            f"[bg][fg]overlay=x='(W-w)/2+{offset_x:.2f}':"
            f"y='(H-h)/2+{offset_y:.2f}'[base]"
        )

    if ass_path:
        escaped = ass_path.as_posix().replace(":", r"\:")
        parts.append(f"[base]subtitles=filename='{escaped}'[outv]")
        video_map = "[outv]"
    else:
        video_map = "[base]"

    final_audio = AUDIO_FILTERS.get(audio_preset, AUDIO_FILTERS["natural"])
    fade_in = min(float(options.get("fade_in", 0.0)), output_duration / 2)
    fade_out = min(float(options.get("fade_out", 0.0)), output_duration / 2)
    if fade_in > 0:
        final_audio += f",afade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        final_audio += (
            f",afade=t=out:st={max(0.0, output_duration - fade_out):.3f}:"
            f"d={fade_out:.3f}"
        )
    parts.append(f"[cuta]{final_audio}[outa]")
    return ";".join(parts), video_map, "[outa]"



def _atempo_filters(speed: float) -> str:
    values = []
    remaining = max(0.25, min(4.0, float(speed)))
    while remaining > 2.0:
        values.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    values.append(remaining)
    return ",".join(f"atempo={value:.6f}" for value in values)

def _track_volume_filter(item: dict, input_offset: float) -> str:
    base = float(item.get("gain_db") or 0.0)
    points = sorted(
        (
            max(0.0, float(point.get("time", input_offset)) - input_offset),
            base + float(point.get("gain_db") or 0.0),
        )
        for point in (item.get("keyframes") or [])
        if isinstance(point, dict)
    )
    if not points:
        return f"volume={math.pow(10.0, base / 20.0):.6f}"
    expression = f"{math.pow(10.0, points[-1][1] / 20.0):.6f}"
    for index in range(len(points) - 2, -1, -1):
        t0, db0 = points[index]
        t1, db1 = points[index + 1]
        v0, v1 = math.pow(10.0, db0 / 20.0), math.pow(10.0, db1 / 20.0)
        span = max(0.001, t1 - t0)
        ramp = f"({v0:.6f}+({v1 - v0:.6f})*(t-{t0:.3f})/{span:.3f})"
        expression = f"if(lt(t,{t0:.3f}),{v0:.6f},if(lt(t,{t1:.3f}),{ramp},{expression}))"
    return f"volume='{expression}':eval=frame"

def _editor_filter_graph_v2(
    ass_path: Path | None,
    audio_mix: list[dict],
    audio_preset: str,
    segments: list[dict],
    options: dict,
    color: dict,
    output_duration: float,
    input_offset: float,
    overlays: list[dict],
    sfx: list[dict],
) -> tuple[str, str, str]:
    enabled = [item for item in audio_mix if item.get("enabled")] or [{"ordinal": 0, "gain_db": 0, "keyframes": []}]
    parts, audio_inputs = [], []
    for index, item in enumerate(enabled):
        parts.append(
            f"[0:a:{int(item['ordinal'])}]aresample=48000,{_track_volume_filter(item, input_offset)},"
            f"loudnorm=I=-22:LRA=15:TP=-3[ain{index}]"
        )
        audio_inputs.append(f"[ain{index}]")
    if len(audio_inputs) > 1:
        parts.append(f"{''.join(audio_inputs)}amix=inputs={len(audio_inputs)}:duration=longest:dropout_transition=0:normalize=0[mixed]")
        mixed = "[mixed]"
    else:
        mixed = audio_inputs[0]

    count = len(segments)
    if count > 1:
        parts.append(f"[0:v]split={count}" + "".join(f"[vsrc{i}]" for i in range(count)))
        parts.append(f"{mixed}asplit={count}" + "".join(f"[asrc{i}]" for i in range(count)))
    for index, segment in enumerate(segments):
        source_start = float(segment["source_start"]) - input_offset
        source_end = float(segment["source_end"]) - input_offset
        speed = float(segment.get("speed") or 1.0)
        freeze = float(segment.get("freeze") or 0.0)
        raw_duration = source_end - source_start
        played = raw_duration / speed
        video_source = f"[vsrc{index}]" if count > 1 else "[0:v]"
        audio_source = f"[asrc{index}]" if count > 1 else mixed
        video_filters = (
            f"trim=start={source_start:.3f}:end={source_end:.3f},"
            f"setpts=(PTS-STARTPTS)/{speed:.6f}"
        )
        if freeze > 0:
            video_filters += f",tpad=stop_mode=clone:stop_duration={freeze:.3f}"
        total = played + freeze
        if str(segment.get("transition")) in {"fade", "dissolve"} and total >= 0.5:
            duration = min(0.35, total / 4)
            video_filters += f",fade=t=in:st=0:d={duration:.3f},fade=t=out:st={max(0, total-duration):.3f}:d={duration:.3f}"
        parts.append(f"{video_source}{video_filters}[vcut{index}]")
        audio_filters = (
            f"atrim=start={source_start:.3f}:end={source_end:.3f},"
            f"asetpts=PTS-STARTPTS,{_atempo_filters(speed)}"
        )
        if freeze > 0:
            audio_filters += f",apad=pad_dur={freeze:.3f},atrim=duration={total:.3f}"
        parts.append(f"{audio_source}{audio_filters}[acut{index}]")
    if count > 1:
        concat_inputs = "".join(f"[vcut{i}][acut{i}]" for i in range(count))
        parts.append(f"{concat_inputs}concat=n={count}:v=1:a=1[cutv][cuta]")
    else:
        parts.extend(["[vcut0]null[cutv]", "[acut0]anull[cuta]"])

    mode = options.get("mode", "fit")
    zoom = float(options.get("zoom", 1.0))
    pos_x, pos_y = float(options.get("x", 0.0)), float(options.get("y", 0.0))
    target_w, target_h = round(1080 * zoom), round(1920 * zoom)
    if mode == "fill":
        x_ratio, y_ratio = (pos_x + 100.0) / 200.0, (pos_y + 100.0) / 200.0
        parts.append(
            f"[cutv]fps=30,scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='(iw-ow)*{x_ratio:.4f}':y='(ih-oh)*{y_ratio:.4f}'[framed]"
        )
    else:
        parts.append("[cutv]fps=30,split=2[bgsrc][fgsrc]")
        parts.append("[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:4,scale=1080:1920,eq=brightness=-0.10:saturation=0.72[bg]")
        parts.append(f"[fgsrc]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg]")
        parts.append(f"[bg][fg]overlay=x='(W-w)/2+{pos_x * 4.0:.2f}':y='(H-h)/2+{pos_y * 7.0:.2f}'[framed]")

    temperature = float(color.get("temperature") or 0.0)
    parts.append(
        f"[framed]eq=brightness={float(color.get('brightness', 0)):.3f}:"
        f"contrast={float(color.get('contrast', 1)):.3f}:"
        f"saturation={float(color.get('saturation', 1)):.3f},"
        f"colorbalance=rm={temperature * 0.12:.3f}:bm={-temperature * 0.12:.3f}[colored]"
    )
    video_label = "[colored]"
    for index, item in enumerate(overlays, start=1):
        duration = float(item["duration"])
        scale = float(item.get("scale") or 0.55)
        parts.append(
            f"[{index}:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS+{float(item['start']):.3f}/TB,"
            f"scale='min(iw,{max(80, round(1080 * scale))})':-1[overlay{index}]"
        )
        x = float(item.get("x") or 0) * 5.0
        y = float(item.get("y") or 0) * 8.0
        next_label = f"[video{index}]"
        parts.append(
            f"{video_label}[overlay{index}]overlay=x='(W-w)/2+{x:.2f}':y='(H-h)/2+{y:.2f}':"
            f"enable='between(t,{float(item['start']):.3f},{float(item['start']) + duration:.3f})'{next_label}"
        )
        video_label = next_label

    if ass_path:
        escaped = ass_path.as_posix().replace(":", r"\:")
        parts.append(f"{video_label}subtitles=filename='{escaped}'[outv]")
        video_map = "[outv]"
    else:
        video_map = video_label

    final_audio = AUDIO_FILTERS.get(audio_preset, AUDIO_FILTERS["natural"])
    fade_in = min(float(options.get("fade_in", 0.0)), output_duration / 2)
    fade_out = min(float(options.get("fade_out", 0.0)), output_duration / 2)
    if fade_in > 0:
        final_audio += f",afade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        final_audio += f",afade=t=out:st={max(0.0, output_duration - fade_out):.3f}:d={fade_out:.3f}"
    parts.append(f"[cuta]{final_audio}[mainaudio]")
    audio_labels = ["[mainaudio]"]
    first_sfx_input = 1 + len(overlays)
    for index, item in enumerate(sfx):
        input_index = first_sfx_input + index
        gain = math.pow(10.0, float(item.get("gain_db") or 0) / 20.0)
        delay = max(0, round(float(item["start"]) * 1000))
        parts.append(
            f"[{input_index}:a]atrim=duration={float(item['duration']):.3f},asetpts=PTS-STARTPTS,"
            f"volume={gain:.6f},adelay={delay}|{delay}[sfx{index}]"
        )
        audio_labels.append(f"[sfx{index}]")
    if len(audio_labels) > 1:
        parts.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=first:dropout_transition=0:normalize=0[outa]")
    else:
        parts.append("[mainaudio]anull[outa]")
    return ";".join(parts), video_map, "[outa]"
def render_editor_variant(settings: Settings, candidate_id: int, progress: Callable[[str], None] | None = None) -> Path:
    data = editor_payload(settings, candidate_id)
    if not data["has_draft"]:
        raise ValueError("Save the editor draft before rendering")
    row = dict(_candidate(settings, candidate_id))
    start, end = float(data["source_start_sec"]), float(data["source_end_sec"])
    segments = _normalize_segments(data.get("segments") or [], start, end)
    output_duration = _output_duration(segments)
    input_offset = min(float(segment["source_start"]) for segment in segments)
    input_end = max(float(segment["source_end"]) for segment in segments)
    source_duration = input_end - input_offset
    words = []
    for item in data["captions"]:
        if normalize_motion(item.get("motion"), item["start"], item["end"]):
            continue
        mapped = _caption_after_segments(item, segments)
        if mapped and mapped["start"] < output_duration:
            mapped["end"] = min(output_duration, mapped["end"])
            words.append(mapped)
    row["transcript_json"] = json.dumps({
        "engine": "clipmaker-editor",
        "transcription": [{"text": " ".join(item["text"] for item in words), "start": 0, "end": output_duration, "words": words}],
    }) if words else None
    with database(settings.db_path) as connection:
        attempt = int(connection.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM candidate_variants WHERE candidate_id=?", (candidate_id,),
        ).fetchone()[0])
    destination = vod_output_directory(
        settings.output_dir, str(row["video_name"]), str(row.get("clip_folder_name") or "")
    ) / f"clip-{candidate_id:04d}-v{attempt:02d}-edited.mp4"
    ass_path = settings.work_dir / f"candidate-{candidate_id}-v{attempt:02d}-edited.ass"
    # A failed render retry must not append to an earlier ASS recipe.
    ass_path.unlink(missing_ok=True)
    has_captions = _write_ass(
        row, ass_path, clip_offset=0, clip_duration=output_duration,
        caption_preset=data["caption_preset"],
        font_scale=float(data["crop"].get("caption_scale", 1.0)),
        margin_offset=int(data["crop"].get("caption_y", 0)),
    )
    has_captions = append_motion_ass(
        ass_path, data["captions"], segments,
        font_scale=float(data["crop"].get("caption_scale", 1.0)),
    ) or has_captions
    audio_mix = [{"ordinal": track["ordinal"], "enabled": track["enabled"], "gain_db": track["gain_db"], "keyframes": track.get("keyframes") or []} for track in data["audio_tracks"]]
    overlays = data.get("overlays") or []
    sfx = data.get("sfx") or []
    graph, video_map, audio_map = _editor_filter_graph_v2(
        ass_path if has_captions else None, audio_mix, data["audio_preset"],
        segments, data["crop"], data.get("color") or _normalize_color(),
        output_duration, input_offset, overlays, sfx,
    )
    if progress:
        progress(f"rendering attempt {attempt}: Manual timeline edit")
    command = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{input_offset:.3f}", "-i", row["video_path"],
    ]
    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    for item in overlays:
        if Path(item["path"]).suffix.lower() in image_extensions:
            command.extend(["-loop", "1"])
        else:
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", item["path"]])
    for item in sfx:
        command.extend(["-i", item["path"]])
    command.extend([
        "-filter_complex", graph, "-map", video_map, "-map", audio_map,
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{output_duration:.3f}",
        "-movflags", "+faststart", str(destination),
    ])
    subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
    enabled = [track["ordinal"] for track in data["audio_tracks"] if track["enabled"]]
    with database(settings.db_path) as connection:
        connection.execute(
            "UPDATE candidate_variants SET decision='superseded', updated_at=CURRENT_TIMESTAMP WHERE candidate_id=? AND decision='pending'", (candidate_id,),
        )
        connection.execute(
            """
            INSERT INTO candidate_variants (
                candidate_id, attempt, recipe_key, recipe_label, clip_start_sec,
                clip_end_sec, caption_preset, audio_preset, audio_ordinal,
                output_path, decision
            ) VALUES (?, ?, 'edited', 'Manual timeline edit', ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (candidate_id, attempt, start, end, data["caption_preset"], data["audio_preset"],
             int(enabled[0] if enabled else 0), str(destination)),
        )
        connection.execute(
            "UPDATE candidates SET start_sec=?, end_sec=?, export_path=?, status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (start, end, str(destination), candidate_id),
        )
    if progress:
        progress(f"attempt {attempt} ready for review")
    return destination
