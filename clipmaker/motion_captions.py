"""Source-timed caption paths rendered into the final 1080 x 1920 frame.

Positions use percentages of the output frame. Keyframe times use source seconds,
so trims, reordering and speed changes can remap paths without losing the edit.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

EFFECTS = {"none", "pop", "shake", "grow", "fall"}


def _number(value, default: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not math.isfinite(result):
        result = default
    return round(max(low, min(high, result)), 4)


def normalize_motion(raw, start: float, end: float) -> dict | None:
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    color = str(raw.get("color") or "#ffffff")
    effect = str(raw.get("effect") or "none")
    result = {
        "enabled": True,
        "x": _number(raw.get("x"), 50, 0, 100),
        "y": _number(raw.get("y"), 30, 0, 100),
        "size": _number(raw.get("size"), 1, .4, 3),
        "rotation": _number(raw.get("rotation"), 0, -180, 180),
        "color": color.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#ffffff",
        "effect": effect if effect in EFFECTS else "none",
        "keyframes": [],
    }
    # Keep out-of-cue keys: a shared speaker path can cover several cues, and
    # extending a cue later should not destroy its original motion recipe.
    frames = raw.get("keyframes")
    by_time = {}
    for frame in (frames if isinstance(frames, list) else [])[:2000]:
        if not isinstance(frame, dict):
            continue
        time = _number(frame.get("time"), start, 0, 1_000_000)
        by_time[time] = {"time": time,
                         "x": _number(frame.get("x"), result["x"], 0, 100),
                         "y": _number(frame.get("y"), result["y"], 0, 100)}
    result["keyframes"] = [by_time[time] for time in sorted(by_time)]
    return result


def motion_position(motion: dict, time: float) -> tuple[float, float]:
    frames = motion.get("keyframes") or []
    if not frames:
        return motion["x"], motion["y"]
    if time <= frames[0]["time"]:
        return frames[0]["x"], frames[0]["y"]
    for left, right in zip(frames, frames[1:]):
        if time <= right["time"]:
            ratio = (time - left["time"]) / (right["time"] - left["time"])
            return (left["x"] + (right["x"] - left["x"]) * ratio,
                    left["y"] + (right["y"] - left["y"]) * ratio)
    return frames[-1]["x"], frames[-1]["y"]


def motion_pose(motion: dict, time: float, cue_start: float, cue_end: float) -> dict:
    x, y = motion_position(motion, time)
    age = max(0.0, time - cue_start)
    duration = max(.04, cue_end - cue_start)
    size = motion["size"]
    effect = motion["effect"]
    if effect == "pop":
        size *= .72 + .28 * min(1.0, age / .12)
    elif effect == "shake":
        x += .65 * math.sin(age * 52)
        y += .35 * math.sin(age * 71)
    elif effect == "grow":
        size *= 1 + .5 * min(1.0, age / duration)
    elif effect == "fall":
        y += 35 * min(1.0, age / duration) ** 2
    return {"x": x, "y": y, "size": size, "rotation": motion["rotation"]}


def motion_events(captions: list[dict], segments: list[dict], font_scale: float = 1, frame=(1080,1920)) -> list[str]:
    """Bake at output frame rate, including paths through speed changes/freezes.

    Unlike word-group karaoke, these independent cues can overlap for two speakers.
    A cue crossing a split is clipped into each kept segment rather than discarded.
    """
    from .render import _ass_time
    events = []
    cursor = 0.0
    for segment in segments:
        left, right = float(segment["source_start"]), float(segment["source_end"])
        speed = float(segment.get("speed") or 1)
        freeze = float(segment.get("freeze") or 0)
        for cue in captions:
            motion = normalize_motion(cue.get("motion"), cue["start"], cue["end"])
            if not motion:
                continue
            a, b = max(left, float(cue["start"])), min(right, float(cue["end"]))
            if b <= a:
                continue
            start, end = cursor + (a - left) / speed, cursor + (b - left) / speed
            if freeze and float(cue["start"]) < right < float(cue["end"]):
                end += freeze
            color = motion["color"][1:]
            ass_color = "&H00" + color[4:6] + color[2:4] + color[0:2]
            # Neutralize ASS override syntax supplied as caption text.
            text = str(cue["text"]).replace("\\", "⧵").replace("{", "｛").replace("}", "｝").replace("\n", r"\N")
            for index in range(max(1, math.ceil((end - start) * 30))):
                t = start + index / 30
                stop = min(end, t + 1 / 30)
                # Quantize adjacent boundaries identically to avoid ASS overlaps.
                if _ass_time(t) == _ass_time(stop):
                    continue
                source_t = min(right, a + (t - start) * speed)
                pose = motion_pose(motion, source_t, cue["start"], cue["end"])
                tags = (f"\\an5\\q2\\pos({pose['x'] * frame[0]/100:.2f},{pose['y'] * frame[1]/100:.2f})"
                        f"\\fnArial\\fs{64 * pose['size'] * font_scale * frame[0]/1080:.2f}"
                        f"\\frz{-pose['rotation']:.2f}\\c{ass_color}\\bord3\\shad1")
                events.append(f"Dialogue: 1,{_ass_time(t)},{_ass_time(stop)},Default,,0,0,0,,{{{tags}}}{text}")
        cursor += (right - left) / speed + freeze
    return events


def append_motion_ass(destination: Path, captions: list[dict], segments: list[dict], font_scale: float = 1, frame=(1080,1920)) -> bool:
    events = motion_events(captions, segments, font_scale, frame)
    if not events:
        return False
    if not destination.exists():
        destination.write_text(
            f"[Script Info]\nScriptType: v4.00+\nPlayResX: {frame[0]}\nPlayResY: {frame[1]}\n"
            "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,64,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,5,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
            encoding="utf-8-sig",
        )
    with destination.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(events) + "\n")
    return True
