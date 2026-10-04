"""設定の保存と読み込み (%APPDATA%\\M4aToMp3\\settings.json)。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import APP_NAME


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


@dataclass
class Settings:
    folders: list[str] = field(default_factory=list)
    last_dir: str = ""
    recursive: bool = True
    rename: bool = True
    convert: bool = True
    bitrate: int = 320
    overwrite: bool = False
    ffmpeg_path: str = ""


def load() -> Settings:
    try:
        data = json.loads((app_data_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    defaults = asdict(Settings())
    known = {k: v for k, v in data.items() if k in defaults and isinstance(v, type(defaults[k]))}
    return Settings(**known)


def save(settings: Settings) -> None:
    path = app_data_dir() / "settings.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
