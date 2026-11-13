from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from screensaver4wayland_lib.config import Config
from screensaver4wayland_lib.monitors import match_monitors, Monitor
from screensaver4wayland_lib.app import load_monitor_images


def snapshot(index, x, model):
    return {"index": index, "vendor": "Vendor", "model": model, "x": x, "y": 0,
            "width": 1920, "height": 1080, "scale": 1, "primary": index == 0}


def logical(x, connector, model, serial):
    return (x, 0, 1.0, 0, x == 0, [(connector, "Vendor", model, serial)], {})


class MonitorIdentityTests(unittest.TestCase):
    def test_id_survives_reordering_repositioning_and_port_change(self):
        original = match_monitors([snapshot(0, 0, "A"), snapshot(1, 1920, "B")],
                                  [logical(0, "DP-1", "A", "serial-a"), logical(1920, "HDMI-1", "B", "serial-b")])
        changed = match_monitors([snapshot(0, 0, "B"), snapshot(1, 1920, "A")],
                                 [logical(0, "DP-2", "B", "serial-b"), logical(1920, "HDMI-2", "A", "serial-a")])
        self.assertEqual(original[0].key, changed[1].key)
        self.assertEqual(original[1].key, changed[0].key)
        self.assertNotEqual(original[0].key, original[1].key)

    def test_duplicate_or_missing_serial_uses_connector(self):
        for serial in ("", "0", "duplicate"):
            with self.subTest(serial=serial):
                monitors = match_monitors([snapshot(0, 0, "Same"), snapshot(1, 1920, "Same")],
                                          [logical(0, "DP-1", "Same", serial), logical(1920, "DP-2", "Same", serial)])
                self.assertNotEqual(monitors[0].key, monitors[1].key)


class MonitorConfigTests(unittest.TestCase):
    def test_old_config_still_loads_and_new_displays_inherit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"source": "image", "image_path": "/tmp/saved.webp", "idle_seconds": 900}))
            config = Config.load(path)
            self.assertEqual(config.monitor_profiles, {})
            self.assertEqual(config.for_monitor("new-screen").image_path, "/tmp/saved.webp")
            self.assertEqual(config.idle_seconds, 900)

    def test_profiles_roundtrip_without_changing_default_or_other_displays(self):
        config = Config(source="image", image_path="/tmp/default.webp", idle_seconds=900,
                        monitor_profiles={"left": {"image_path": "/tmp/left.png", "fit": "contain"},
                                          "right": {"source": "folder", "folder_path": "/tmp/photos", "slideshow_seconds": 15}})
        config.validate()
        self.assertEqual(config.for_monitor("left").image_path, "/tmp/left.png")
        self.assertEqual(config.for_monitor("right").source, "folder")
        self.assertEqual(config.image_path, "/tmp/default.webp")
        self.assertEqual(config.for_monitor("left").idle_seconds, 900)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config.save(path)
            self.assertEqual(Config.load(path), config)

    def test_rejects_invalid_profile_fields_and_values(self):
        for profiles in ([1], {"a": []}, {"a": {"idle_seconds": 5}}, {"a": {"source": "image", "image_path": ""}},
                         {"a": {"fit": "stretch"}}, {"a": {"slideshow_seconds": 0}}):
            with self.subTest(profiles=profiles), self.assertRaises(ValueError):
                Config(monitor_profiles=profiles).validate()

    def test_different_images_are_loaded_by_identity_not_list_order(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.png", Path(directory) / "b.webp"
            Image.new("RGB", (40, 30), "red").save(a)
            Image.new("RGB", (40, 30), "blue").save(b, "WEBP", lossless=True)
            config = Config(source="image", image_path=str(a), monitor_profiles={"B": {"image_path": str(b), "fit": "contain"}})
            left = Monitor("A", 1, "Left", 0, 0, 40, 30, 1, False)
            right = Monitor("B", 0, "Right", 40, 0, 40, 30, 1, True)
            result = load_monitor_images(config, [right, left])
            pixels = {monitor.key: tuple(data[0].get_pixels()[:3]) for monitor, _, data in result}
            self.assertEqual(pixels, {"A": (255, 0, 0), "B": (0, 0, 255)})


if __name__ == "__main__":
    unittest.main()
