"""Configuration and image discovery, independent of GTK."""
from dataclasses import asdict, dataclass, field, replace
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlparse

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
IMAGE_FIELDS = ("source", "image_path", "folder_path", "slideshow_seconds", "fit")


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "screensaver4wayland" / "config.json"


@dataclass(frozen=True)
class Config:
    enabled: bool = True
    idle_seconds: int = 300
    source: str = "wallpaper"
    image_path: str = ""
    folder_path: str = ""
    slideshow_seconds: int = 60
    fit: str = "cover"
    respect_inhibitors: bool = True
    monitor_profiles: dict = field(default_factory=dict)

    def validate(self):
        for key in ("enabled", "respect_inhibitors"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} 必须为 true 或 false")
        for key, minimum, maximum in (("idle_seconds", 5, 86400), ("slideshow_seconds", 5, 86400)):
            value = getattr(self, key)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{key} 必须是 {minimum}～{maximum} 之间的整数秒数")
        if self.source not in ("wallpaper", "image", "folder"):
            raise ValueError("图片来源必须为 wallpaper、image 或 folder")
        if self.fit not in ("cover", "contain"):
            raise ValueError("缩放方式必须为 cover 或 contain")
        for key in ("image_path", "folder_path"):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f"{key} 必须是路径字符串")
        if self.source == "image" and not self.image_path.strip():
            raise ValueError("请先选择一张图片")
        if self.source == "folder" and not self.folder_path.strip():
            raise ValueError("请先选择图片文件夹")
        if not isinstance(self.monitor_profiles, dict):
            raise ValueError("屏幕配置必须为对象")
        for key, profile in self.monitor_profiles.items():
            if not isinstance(key, str) or not key or not isinstance(profile, dict):
                raise ValueError("屏幕配置格式无效")
            if set(profile) - set(IMAGE_FIELDS):
                raise ValueError("屏幕配置包含未知选项")
            self.for_monitor(key).validate()
        return self

    def image_settings(self):
        return {key: getattr(self, key) for key in IMAGE_FIELDS}

    def for_monitor(self, key=None):
        return replace(self, **self.monitor_profiles.get(key, {}), monitor_profiles={})

    @classmethod
    def load(cls, path=None):
        path = path or config_path()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("配置文件应为 JSON 对象")
        return cls(**data).validate()

    def save(self, path=None):
        self.validate()
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                temporary = Path(f.name)
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def file_uri_path(uri):
    parsed = urlparse(uri)
    if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
        return Path(unquote(parsed.path))
    return None


def discover_images(config, wallpaper=None):
    """Only local static image formats; a folder is deliberately non-recursive."""
    if config.source == "wallpaper":
        candidates = [wallpaper] if wallpaper else []
    elif config.source == "image":
        candidates = [Path(config.image_path).expanduser()]
    else:
        folder = Path(config.folder_path).expanduser()
        # Bound memory for very large directories. Files are sorted for stable playback.
        candidates = []
        with os.scandir(folder) as entries:
            for entry in entries:
                if Path(entry.name).suffix.lower() in IMAGE_SUFFIXES and entry.is_file():
                    candidates.append(Path(entry.path))
                    if len(candidates) >= 2000:
                        break
        candidates.sort(key=lambda p: p.name.casefold())
    return [Path(p) for p in candidates if p and Path(p).suffix.lower() in IMAGE_SUFFIXES and Path(p).is_file()]


def image_geometry(image_width, image_height, width, height, fit):
    """Return scale and centered offsets, preserving aspect ratio."""
    if min(image_width, image_height, width, height) <= 0:
        raise ValueError("图片和画布尺寸必须大于零")
    choose = max if fit == "cover" else min
    scale = choose(width / image_width, height / image_height)
    return scale, (width - image_width * scale) / 2, (height - image_height * scale) / 2
