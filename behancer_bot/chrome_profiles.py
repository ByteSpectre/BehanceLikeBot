from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ChromeProfile:
    directory: str
    name: str

    @property
    def label(self) -> str:
        if self.name and self.name != self.directory:
            return f"{self.name} — {self.directory}"
        return self.directory


def discover_chrome_profiles(user_data_dir: str) -> list[ChromeProfile]:
    root = Path(user_data_dir).expanduser()
    if not root.is_dir():
        return []

    info_cache: dict[str, Any] = {}
    local_state = _read_json(root / "Local State")
    if isinstance(local_state, dict):
        profile_section = local_state.get("profile", {})
        if isinstance(profile_section, dict):
            raw_cache = profile_section.get("info_cache", {})
            if isinstance(raw_cache, dict):
                info_cache = raw_cache

    directories = {
        path.name
        for path in root.iterdir()
        if path.is_dir() and _is_user_profile_directory(path.name)
    }
    directories.update(name for name in info_cache if _is_user_profile_directory(name))

    profiles: list[ChromeProfile] = []
    for directory in sorted(directories, key=_profile_sort_key):
        if not (root / directory).is_dir():
            continue
        cached = info_cache.get(directory, {})
        cached_name = cached.get("name", "") if isinstance(cached, dict) else ""
        preferences = _read_json(root / directory / "Preferences")
        preference_name = ""
        if isinstance(preferences, dict):
            profile_data = preferences.get("profile", {})
            if isinstance(profile_data, dict):
                preference_name = str(profile_data.get("name", ""))
        name = str(cached_name or preference_name or directory)
        profiles.append(ChromeProfile(directory=directory, name=name))
    return profiles


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _is_user_profile_directory(name: str) -> bool:
    return name == "Default" or (
        name.startswith("Profile ") and name.removeprefix("Profile ").isdigit()
    )


def _profile_sort_key(name: str) -> tuple[int, int | str]:
    if name == "Default":
        return (0, 0)
    suffix = name.removeprefix("Profile ")
    return (1, int(suffix) if suffix.isdigit() else name)
