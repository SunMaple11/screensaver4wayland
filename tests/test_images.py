import gc
from pathlib import Path
import tempfile
import unittest

from PIL import Image, features
from screensaver4wayland_lib.config import Config
from screensaver4wayland_lib.image_io import decode_image
from screensaver4wayland_lib.app import load_image


@unittest.skipUnless(features.check("webp"), "Pillow WebP support required")
class WebPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_webp_unicode_path_scales_and_keeps_pixels_alive(self):
        path = self.root / "屏保 测试.WEBP"
        Image.new("RGB", (800, 400), (32, 96, 160)).save(path, "WEBP", lossless=True)
        pixbuf = decode_image(path, 200, 200)
        gc.collect()
        self.assertEqual((pixbuf.get_width(), pixbuf.get_height()), (200, 100))
        self.assertEqual(tuple(pixbuf.get_pixels()[:3]), (32, 96, 160))

    def test_alpha_is_preserved(self):
        path = self.root / "transparent.webp"
        Image.new("RGBA", (20, 20), (100, 50, 25, 128)).save(path, "WEBP", lossless=True)
        pixbuf = decode_image(path, 20, 20)
        self.assertTrue(pixbuf.get_has_alpha())
        self.assertEqual(tuple(pixbuf.get_pixels()[:4]), (100, 50, 25, 128))

    def test_webp_content_with_jpeg_extension(self):
        path = self.root / "download.jpg"
        Image.new("RGB", (40, 30), (20, 80, 120)).save(path, "WEBP", lossless=True)
        pixbuf = decode_image(path, 40, 30)
        self.assertEqual(tuple(pixbuf.get_pixels()[:3]), (20, 80, 120))

    @unittest.skipUnless(features.check("webp_anim"), "Animated WebP support required")
    def test_animated_webp_uses_first_frame(self):
        path = self.root / "animated.webp"
        first = Image.new("RGB", (40, 30), "red")
        second = Image.new("RGB", (40, 30), "blue")
        first.save(path, "WEBP", lossless=True, save_all=True, append_images=[second], duration=200, loop=0)
        pixbuf = decode_image(path, 40, 30)
        self.assertEqual(tuple(pixbuf.get_pixels()[:3]), (255, 0, 0))

    def test_slideshow_skips_corrupt_webp(self):
        (self.root / "a-broken.webp").write_bytes(b"broken image")
        Image.new("RGB", (40, 30), "green").save(self.root / "b-good.webp", "WEBP", lossless=True)
        _, paths, index = load_image(Config(source="folder", folder_path=str(self.root)), 40, 30)
        self.assertEqual(paths[index].name, "b-good.webp")

    def test_corrupt_webp_gives_clear_error(self):
        path = self.root / "broken.webp"
        path.write_bytes(b"RIFFxxxxWEBPbroken")
        with self.assertRaisesRegex(ValueError, "WebP.*无法读取"):
            decode_image(path, 100, 100)


if __name__ == "__main__":
    unittest.main()
