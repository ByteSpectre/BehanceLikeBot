from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


def _application_dir() -> Path:
    """Return a persistent directory beside the executable when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _application_dir()
CONFIG_PATH = ROOT_DIR / "config.json"
DATA_DIR = ROOT_DIR / ".data"


def _default_chrome_data_dir() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return ""
    return str(Path(local_app_data) / "Google" / "Chrome" / "User Data")


@dataclass(slots=True)
class AppConfig:
    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    bot_username: str = "behancer_bot"
    chrome_binary: str = ""
    chrome_user_data_dir: str = ""
    chrome_profile: str = "Default"
    chrome_debug_port: int = 9222

    @classmethod
    def load(cls) -> AppConfig:
        defaults = cls(chrome_user_data_dir=_default_chrome_data_dir())
        if not CONFIG_PATH.exists():
            return defaults
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            allowed = {key: raw[key] for key in asdict(defaults) if key in raw}
            return cls(**{**asdict(defaults), **allowed})
        except (OSError, ValueError, TypeError):
            return defaults

    def validate(self) -> None:
        if not self.api_id or not self.api_hash.strip():
            raise ValueError("Укажите Telegram API ID и API Hash")
        if not self.phone.strip():
            raise ValueError("Укажите номер телефона Telegram")
        if not self.chrome_user_data_dir.strip():
            raise ValueError("Укажите папку данных Google Chrome")

    def save(self) -> None:
        self.validate()
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def session_path(self) -> Path:
        safe_phone = (
            "".join(char for char in self.phone if char.isdigit()) or "telegram"
        )
        path = DATA_DIR / "sessions" / safe_phone
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
