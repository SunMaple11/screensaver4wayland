"""Read images on Ubuntu 22.04, including systems without a WebP pixbuf loader."""
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib


def decode_image(path, width, height):
    path = Path(path)
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
        return pixbuf.apply_embedded_orientation() or pixbuf
    except GLib.Error:
        # A WebP can also have a misleading .jpg name after being downloaded.
        with path.open("rb") as stream:
            header = stream.read(12)
        is_webp = path.suffix.lower() == ".webp" or (header[:4] == b"RIFF" and header[8:12] == b"WEBP")
        if not is_webp:
            raise
    return _decode_webp(path, width, height)


def _decode_webp(path, width, height):
    try:
        from PIL import Image, ImageOps, features
    except ImportError as e:
        raise ValueError("此系统缺少 WebP 解码支持，请安装 python3-pil") from e
    if not features.check("webp"):
        raise ValueError("当前 Pillow 未包含 WebP 解码支持")
    try:
        with Image.open(path) as original:
            # Screensaver4Wayland is a still-image screensaver; animated WebP uses frame 0.
            original.seek(0)
            with ImageOps.exif_transpose(original) as oriented:
                resampling = getattr(Image, "Resampling", Image)  # Ubuntu ships Pillow 9.0.
                oriented.thumbnail((width, height), resampling.LANCZOS)
                with oriented.convert("RGBA") as rgba:
                    pixels = GLib.Bytes.new(rgba.tobytes())
                    # GBytes remains owned by the pixbuf after the PIL image is closed.
                    return GdkPixbuf.Pixbuf.new_from_bytes(
                        pixels, GdkPixbuf.Colorspace.RGB, True, 8,
                        rgba.width, rgba.height, rgba.width * 4)
    except Image.DecompressionBombError as e:
        raise ValueError("WebP 图片尺寸过大，无法读取") from e
    except OSError as e:
        raise ValueError("WebP 文件无法读取，文件可能不完整或已损坏") from e
