from pathlib import Path
import tempfile
import unittest

from install import migrate_preferences
from screensaver4wayland_lib.config import Config


class MigrationTests(unittest.TestCase):
    def test_keeps_image_timer_and_original_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "quietframe/config.json"
            config = Config(source="image", image_path="/tmp/我的 图片.webp", idle_seconds=900, enabled=False)
            config.save(original)
            contents = original.read_bytes()
            self.assertTrue(migrate_preferences(root))
            destination = root / "screensaver4wayland/config.json"
            self.assertEqual(destination.read_bytes(), contents)
            self.assertEqual(original.read_bytes(), contents)
            self.assertEqual(Config.load(destination), config)

    def test_preserves_newer_settings_on_repeat_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Config(idle_seconds=900).save(root / "quietframe/config.json")
            newest = Config(idle_seconds=600, enabled=False)
            newest.save(root / "screensaver4wayland/config.json")
            self.assertFalse(migrate_preferences(root))
            self.assertEqual(Config.load(root / "screensaver4wayland/config.json"), newest)

    def test_fresh_install_does_not_require_old_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(migrate_preferences(Path(directory)))


if __name__ == "__main__":
    unittest.main()
