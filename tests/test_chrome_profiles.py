import json
import tempfile
import unittest
from pathlib import Path

from behancer_bot.chrome_profiles import discover_chrome_profiles


class ChromeProfileDiscoveryTests(unittest.TestCase):
    def test_discovers_and_names_profiles_from_local_state(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            (root / "Default").mkdir()
            (root / "Profile 2").mkdir()
            (root / "System Profile").mkdir()
            state = {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Основной"},
                        "Profile 2": {"name": "Рабочий"},
                        "System Profile": {"name": "Системный"},
                    }
                }
            }
            (root / "Local State").write_text(json.dumps(state), encoding="utf-8")

            profiles = discover_chrome_profiles(str(root))

        self.assertEqual(
            [(profile.directory, profile.name) for profile in profiles],
            [("Default", "Основной"), ("Profile 2", "Рабочий")],
        )
        self.assertEqual(profiles[1].label, "Рабочий — Profile 2")

    def test_uses_preferences_name_when_local_state_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            profile_dir = Path(temporary_dir) / "Profile 1"
            profile_dir.mkdir()
            (profile_dir / "Preferences").write_text(
                json.dumps({"profile": {"name": "Личный"}}), encoding="utf-8"
            )

            profiles = discover_chrome_profiles(temporary_dir)

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Личный")


if __name__ == "__main__":
    unittest.main()
