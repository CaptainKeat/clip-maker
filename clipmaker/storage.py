from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, unquote


_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_vod_folder_name(value: str | None, video_name: str) -> str:
    """Return a stable Windows-safe folder name for one VOD's rendered clips."""
    fallback = Path(str(video_name or "VOD")).stem or "VOD"
    name = str(value or "").strip() or fallback
    name = _INVALID_WINDOWS_CHARS.sub(" - ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "VOD"
    if name.upper() in _RESERVED_WINDOWS_NAMES:
        name += " clips"
    return name[:100].rstrip(" .") or "VOD"


def vod_output_directory(output_root: str | Path, video_name: str, folder_name: str | None = None) -> Path:
    destination = Path(output_root) / safe_vod_folder_name(folder_name, video_name)
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def export_media_url(output_root: str | Path, output_path: str | Path) -> str:
    root = Path(output_root).resolve()
    path = Path(output_path).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    return "/media/export/" + quote(relative.as_posix(), safe="/")


def export_media_path(output_root: str | Path, encoded_relative: str) -> Path:
    root = Path(output_root).resolve()
    relative = Path(unquote(encoded_relative.replace("/", "\\")))
    if relative.is_absolute():
        raise ValueError("Invalid export path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid export path") from exc
    return path