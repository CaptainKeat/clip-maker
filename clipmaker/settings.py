from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(os.environ.get("CLIPMAKER_CONFIG", str(PROJECT_ROOT / "clipmaker.json")))


@dataclass(slots=True)
class Settings:
    source_root: str = str(Path.home() / "Videos")
    data_dir: str = str(PROJECT_ROOT / "data")
    output_dir: str = str(Path.home() / "Videos" / "Clip Maker Exports")
    ffmpeg: str = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe: str = shutil.which("ffprobe") or "ffprobe"
    whisper_cli: str = shutil.which("whisper-cli") or "whisper-cli"
    whisper_model: str = (
        str(Path.home() / ".claude-video-vision" / "models" / "ggml-large-v3.bin")
    )
    language: str = "en"
    transcription_engine: str = "whisper"
    parakeet_model: str = "nemo-parakeet-tdt-0.6b-v3"
    parakeet_model_dir: str = str(PROJECT_ROOT / "data" / "models" / "parakeet-v3")
    parakeet_quantization: str = "int8"
    max_transcription_tracks: int = 3
    mic_audio_ordinal: int = -1
    audio_repair: bool = True
    lmstudio_control_url: str = "http://127.0.0.1:11436"
    lmstudio_api_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = "council-analyst"
    postiz_env_file: str = ""
    host: str = "127.0.0.1"
    tailscale_host: str = ""
    port: int = 8765
    ai_enabled: bool = False

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "clipmaker.db"

    @property
    def preview_dir(self) -> Path:
        return Path(self.data_dir) / "previews"

    @property
    def vod_preview_dir(self) -> Path:
        return Path(self.data_dir) / "vod-previews"

    @property
    def work_dir(self) -> Path:
        return Path(self.data_dir) / "work"

    def ensure_directories(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.vod_preview_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    if not path.exists():
        settings = Settings()
        save_settings(settings, path)
        return settings
    values = json.loads(path.read_text(encoding="utf-8"))
    settings = Settings(**values)
    settings.ensure_directories()
    return settings


def save_settings(settings: Settings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
    settings.ensure_directories()


def update_storage_folders(
    settings: Settings,
    source_root: str,
    output_dir: str,
    path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, str]:
    """Validate and persist the two user-selectable media folders."""
    source = Path(str(source_root).strip()).expanduser()
    output = Path(str(output_dir).strip()).expanduser()
    if not str(source_root).strip() or not str(output_dir).strip():
        raise ValueError("Both the VOD folder and clip folder are required")
    if not source.exists() or not source.is_dir():
        raise ValueError(f"VOD folder does not exist: {source}")
    source = source.resolve()
    output = output.resolve(strict=False)
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("The clip folder cannot be inside the VOD folder, or finished clips would be scanned as new VODs")
    output.mkdir(parents=True, exist_ok=True)
    settings.source_root = str(source)
    settings.output_dir = str(output)
    save_settings(settings, path)
    return {"source_root": settings.source_root, "output_dir": settings.output_dir}

