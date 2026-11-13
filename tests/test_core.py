from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from screensaver4wayland_lib.config import Config, discover_images, file_uri_path, image_geometry


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_atomic_validation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "settings/config.json"
            config = Config(source="image", image_path="/tmp/图片 with spaces.jpg", idle_seconds=600)
            config.save(path)
            self.assertEqual(Config.load(path), config)
            with self.assertRaises(ValueError):
                replace(config, idle_seconds=True).save(path)
            self.assertEqual(Config.load(path), config)
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_invalid_config_is_not_silently_accepted(self):
        for change in ({"enabled": "false"}, {"idle_seconds": -1}, {"source": "internet"},
                       {"source": "image"}, {"fit": "stretch"}, {"folder_path": 123}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(Config(), **change).validate()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            path.write_text("[1, 2]")
            with self.assertRaises(ValueError):
                Config.load(path)

    def test_uri_unicode_and_remote_host(self):
        self.assertEqual(file_uri_path("file:///tmp/%E5%9B%BE%E7%89%87%20a.jpg"), Path("/tmp/图片 a.jpg"))
        self.assertIsNone(file_uri_path("https://example.com/a.jpg"))
        self.assertIsNone(file_uri_path("file://other-host/a.jpg"))

    def test_folder_limits_to_local_image_files_and_stable_order(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            for name in ("b.jpg", "A.PNG", "notes.txt", "movie.mp4"):
                (folder / name).write_bytes(b"fixture")
            (folder / "subdir").mkdir()
            (folder / "subdir/hidden.jpg").write_bytes(b"fixture")
            config = Config(source="folder", folder_path=root)
            self.assertEqual([p.name for p in discover_images(config)], ["A.PNG", "b.jpg"])
            self.assertEqual(discover_images(Config(source="image", image_path=str(folder / "missing.jpg"))), [])


class GeometryTests(unittest.TestCase):
    def test_cover_crops_symmetrically_without_stretching(self):
        self.assertEqual(image_geometry(400, 200, 100, 100, "cover"), (0.5, -50, 0))

    def test_contain_letterboxes_symmetrically(self):
        self.assertEqual(image_geometry(400, 200, 100, 100, "contain"), (0.25, 0, 25))

    def test_portrait(self):
        scale, x, y = image_geometry(200, 400, 1600, 900, "cover")
        self.assertEqual((scale, x, y), (8, 0, -1150))


if __name__ == "__main__":
    unittest.main()
