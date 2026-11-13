"""Event-driven screensaver daemon and single-instance command line."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import logging
from pathlib import Path
import signal
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from .config import Config, config_path, discover_images, file_uri_path
from .desktop import Desktop
from .image_io import decode_image
from .monitors import read_monitors
from .view import SaverWindow, SettingsWindow

LOG = logging.getLogger(__name__)
APP_ID = "io.github.sunmaple.Screensaver4Wayland"


def argument_parser():
    parser = argparse.ArgumentParser(prog="screensaver4wayland", description="静景：GNOME Wayland 图片屏保")
    actions = parser.add_mutually_exclusive_group()
    for name, help_ in (("daemon", "后台运行"), ("settings", "打开设置"), ("preview", "立即预览"),
                        ("dismiss", "退出当前屏保"), ("status", "输出 JSON 状态"),
                        ("pause", "暂停自动屏保"), ("resume", "恢复自动屏保"), ("quit", "退出程序")):
        actions.add_argument("--" + name, action="store_true", help=help_)
    parser.add_argument("--duration", type=float, default=0, help="预览自动退出秒数，0 为等待操作")
    return parser


def wallpaper_path():
    settings = Gio.Settings.new("org.gnome.desktop.background")
    interface = Gio.Settings.new("org.gnome.desktop.interface")
    key = "picture-uri-dark" if interface.get_string("color-scheme") == "prefer-dark" else "picture-uri"
    path = file_uri_path(settings.get_string(key))
    if path and path.is_file():
        return path
    # Ubuntu's packaged fallback keeps a fresh account usable without bundling artwork.
    return Path("/usr/share/backgrounds/jj_light_by_Hiking93.jpg")


def load_image(config, width, height, images=None, start=0):
    images = list(images) if images is not None else discover_images(config, wallpaper_path())
    if not images:
        raise ValueError("没有找到可用图片，请在设置中选择 JPEG、PNG、WebP 或 BMP 图片")
    errors = []
    for offset in range(min(len(images), 100)):
        index = (start + offset) % len(images)
        try:
            pixbuf = decode_image(images[index], width, height)
            return pixbuf, images, index
        except (GLib.Error, OSError, ValueError) as e:
            errors.append(f"{images[index].name}: {getattr(e, 'message', str(e))}")
    raise ValueError("图片无法读取：" + errors[0])


def load_monitor_images(config, monitors):
    cache = {}
    loaded = []
    for monitor in monitors:
        picture = config.for_monitor(monitor.key)
        width, height = monitor.image_size
        key = (tuple(picture.image_settings().items()), width, height)
        if key not in cache:
            try:
                cache[key] = load_image(picture, width, height)
            except Exception as e:
                raise ValueError(f"{monitor.name}：{e}") from e
        loaded.append((monitor, picture, cache[key]))
    return loaded


def validate_picture_files(config, monitors):
    # Keep profiles for disconnected displays, without requiring their files to
    # be accessible while configuring the displays that are currently connected.
    checked = set()
    choices = [("默认设置", config.for_monitor())]
    choices += [(monitor.name, config.for_monitor(monitor.key)) for monitor in monitors
                if monitor.key in config.monitor_profiles]
    for name, picture in choices:
        key = tuple(picture.image_settings().items())
        if key in checked:
            continue
        try:
            load_image(picture, 960, 600)
        except Exception as e:
            raise ValueError(f"{name}：{e}") from e
        checked.add(key)


class Screensaver4Wayland(Gtk.Application):
    def __init__(self):
        # GTK 3 uses the program name as the Wayland app_id / WM_CLASS.
        # Keep it identical to the desktop entry so docks and task switchers
        # resolve our artwork even before GTK's D-Bus properties arrive.
        GLib.set_prgname(APP_ID)
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.windows = []
        self.monitors = []
        self.slides = {}
        self.settings_window = None
        self.desktop = None
        self.pending = False
        self.preview = False
        self.generation = 0
        self.images = []
        self.image_index = 0
        self.last_image = ""
        self.last_error = ""
        self.last_event = "starting"
        self.activations = 0
        self.suppress_until = 0
        self.timers = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="screensaver4wayland-image")
        self.shutting_down = False
        self.config = Config()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        GLib.set_application_name("静景")
        Gtk.Window.set_default_icon_name(APP_ID)
        try:
            self.config = Config.load()
        except (ValueError, TypeError, OSError) as e:
            self.config = replace(Config(), enabled=False)
            self.last_error = f"配置无法读取，已暂停自动屏保：{e}"
            LOG.error(self.last_error)
        try:
            self.desktop = Desktop(self._idle, self._user_active, self._desktop_changed)
            self.desktop.configure(self.config.idle_seconds)
        except GLib.Error as e:
            self.last_error = f"连接 GNOME 失败：{e.message}"
            LOG.error(self.last_error)
        display = Gdk.Display.get_default()
        self.monitors = read_monitors()
        display.connect("monitor-added", self._monitors_changed)
        display.connect("monitor-removed", self._monitors_changed)
        Gdk.Screen.get_default().connect("monitors-changed", self._monitors_changed)
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._quit_signal)
        self.last_event = "ready"
        LOG.info("静景已启动：%s，闲置 %s 秒后启动", type(display).__name__, self.config.idle_seconds)

    def do_command_line(self, command_line):
        parser = argument_parser()
        try:
            args = parser.parse_args(command_line.get_arguments()[1:])
            if not 0 <= args.duration <= 3600:
                raise ValueError("预览时间必须在 0～3600 秒之间")
        except (ValueError, SystemExit) as e:
            return e.code if isinstance(e, SystemExit) else 2
        if args.status:
            command_line.do_print_literal(command_line, json.dumps(self.status(), ensure_ascii=False, indent=2) + "\n")
        elif args.quit:
            self.quit()
        elif args.dismiss:
            self.dismiss("command")
        elif args.pause or args.resume:
            self.set_enabled(args.resume)
        elif args.preview:
            self.show_saver(preview=True, duration=args.duration)
        elif args.settings or not args.daemon:
            self.open_settings()
        return 0

    def do_activate(self):
        self.open_settings()

    def open_settings(self):
        self.dismiss("settings")
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
            self.settings_window.connect("destroy", self._settings_destroyed)
        self.settings_window.show_all()
        self.settings_window.present()
        if self.last_error:
            self.settings_window.show_message(self.last_error, error=True)

    def _settings_destroyed(self, *_):
        self.settings_window = None

    def _monitors_changed(self, *_):
        self.dismiss("monitors-changed")
        self._timer("monitors", 200, self._refresh_monitors)

    def _refresh_monitors(self):
        self.monitors = read_monitors()
        if self.settings_window:
            self.settings_window.refresh_monitors()

    def save_config(self, config, check_images=True):
        config.validate()
        if check_images and config.source != "wallpaper" and not discover_images(config):
            raise ValueError("所选位置没有可读取的图片")
        config.save()
        self.config = config
        self.dismiss("settings-saved")
        self.last_error = ""
        if self.desktop:
            try:
                self.desktop.configure(config.idle_seconds)
            except GLib.Error as e:
                self.last_error = e.message
        if self.settings_window:
            self.settings_window.refresh_runtime()

    def set_enabled(self, enabled):
        # Pausing must work even if a previously selected image has been removed.
        self.save_config(replace(self.config, enabled=bool(enabled)), check_images=False)

    def apply_config_async(self, config, callback):
        """Decode before committing, so a corrupt image cannot replace good settings."""
        config.validate()
        future = self.executor.submit(validate_picture_files, config, list(self.monitors))
        def finished():
            if self.shutting_down:
                return GLib.SOURCE_REMOVE
            try:
                future.result()
                # The immediate pause/resume switch may have changed during validation.
                self.save_config(replace(config, enabled=self.config.enabled), check_images=False)
            except Exception as e:
                callback(str(e))
            else:
                callback(None)
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(finished))

    def _idle(self):
        if not self.config.enabled or time.monotonic() < self.suppress_until:
            return
        if self.settings_window and self.settings_window.busy:
            return
        self.show_saver()

    def _desktop_changed(self):
        if not self.desktop:
            return
        if self.desktop.blocked_reason(self.config.respect_inhibitors and not self.preview):
            self.dismiss("desktop-blocked")
        elif not self.windows and not self.pending:
            try:
                if self.desktop.idletime() >= self.config.idle_seconds * 1000:
                    self._idle()
            except GLib.Error:
                pass

    def _timer(self, name, milliseconds, callback):
        self._cancel_timer(name)
        def run():
            self.timers.pop(name, None)
            callback()
            return GLib.SOURCE_REMOVE
        self.timers[name] = GLib.timeout_add(max(1, int(milliseconds)), run)

    def _cancel_timer(self, name):
        timer = self.timers.pop(name, 0)
        if timer:
            GLib.source_remove(timer)

    def _watch_activity(self):
        if not self.windows and not self.pending:
            return
        try:
            self.desktop.watch_activity()
        except GLib.Error as e:
            self.last_error = e.message
            self.dismiss("activity-monitor-unavailable")

    def _user_active(self):
        # Give GTK time to consume the wake-up key/button before removing the
        # windows. Pointer movement is caught even outside the focused surface.
        if self.windows or self.pending:
            self._timer("wake", 120, lambda: self.dismiss("user-active"))

    def show_saver(self, preview=False, duration=0):
        if self.windows or self.pending:
            return
        reason = self.desktop.blocked_reason(self.config.respect_inhibitors and not preview) if self.desktop else "GNOME 未连接"
        if reason:
            self.last_event = reason
            LOG.info("暂缓显示：%s", reason)
            return
        self.preview = preview
        if not self.monitors:
            self.last_error = "没有可用屏幕"
            return
        self.pending = True
        self.generation += 1
        self.preview_duration = duration
        self._watch_activity()
        if not self.pending:
            return
        generation = self.generation
        future = self.executor.submit(load_monitor_images, self.config, list(self.monitors))
        future.add_done_callback(lambda f: GLib.idle_add(self._initial_loaded, f, generation))

    def _image_error(self, error):
        self.last_error = str(error)
        LOG.error("读取图片失败：%s", error)
        self.dismiss("image-error")
        if self.settings_window:
            self.settings_window.show_message(self.last_error, error=True)

    def _initial_loaded(self, future, generation):
        if self.shutting_down or generation != self.generation:
            return GLib.SOURCE_REMOVE
        try:
            loaded = future.result()
        except Exception as e:
            self._image_error(e)
            return GLib.SOURCE_REMOVE
        if self.desktop.blocked_reason(self.config.respect_inhibitors and not self.preview):
            self.dismiss("desktop-blocked")
            return GLib.SOURCE_REMOVE
        if not self.preview:
            try:
                if self.desktop.idletime() < self.config.idle_seconds * 1000:
                    self.dismiss("activity-during-load")
                    return GLib.SOURCE_REMOVE
            except GLib.Error:
                self.dismiss("idle-monitor-unavailable")
                return GLib.SOURCE_REMOVE
        self.last_error = ""
        try:
            # Map the primary output last so it receives focus for wake-up keys.
            for monitor, picture, (pixbuf, images, index) in sorted(loaded, key=lambda item: item[0].primary):
                window = SaverWindow(self, monitor.index, pixbuf, picture.fit, monitor.key)
                window.image_path = str(images[index])
                self.slides[monitor.key] = {"monitor": monitor, "config": picture, "images": images,
                                            "index": index, "window": window}
                self.windows.append(window)
                window.show_all()
            if self.windows:
                self.windows[-1].present()
            self.pending = False
            self.activations += 1
            self._sync_first_slide()
            self.last_event = "preview" if self.preview else "idle-activated"
            LOG.info("显示屏保：%s 块屏幕", len(self.windows))
            for key in self.slides:
                self._schedule_slide(key)
            if self.preview_duration:
                self._timer("duration", self.preview_duration * 1000, lambda: self.dismiss("preview-timeout"))
        except Exception:
            self.dismiss("display-error")
            LOG.exception("创建屏保窗口失败")
        return GLib.SOURCE_REMOVE

    def _sync_first_slide(self):
        if self.monitors and self.monitors[0].key in self.slides:
            slide = self.slides[self.monitors[0].key]
            self.images, self.image_index = slide["images"], slide["index"]
            self.last_image = str(self.images[self.image_index])

    def _schedule_slide(self, key):
        slide = self.slides[key]
        if len(slide["images"]) > 1:
            self._timer("slide:" + key, slide["config"].slideshow_seconds * 1000, lambda: self._request_slide(key))

    def _request_slide(self, key):
        if key not in self.slides:
            return
        slide = self.slides[key]
        width, height = slide["monitor"].image_size
        generation = self.generation
        future = self.executor.submit(load_image, slide["config"], width, height, slide["images"], slide["index"] + 1)
        future.add_done_callback(lambda f: GLib.idle_add(self._slide_loaded, f, generation, key))

    def _slide_loaded(self, future, generation, key):
        if self.shutting_down or generation != self.generation or key not in self.slides:
            return GLib.SOURCE_REMOVE
        try:
            pixbuf, images, index = future.result()
        except Exception as e:
            self._image_error(e)
            return GLib.SOURCE_REMOVE
        slide = self.slides[key]
        slide["images"], slide["index"] = images, index
        slide["window"].canvas.set_image(pixbuf, slide["config"].fit)
        slide["window"].image_path = str(images[index])
        self._sync_first_slide()
        self.last_event = "slide-changed"
        self._schedule_slide(key)
        return GLib.SOURCE_REMOVE

    def load_thumbnail(self, target, config=None):
        config = config or self.config
        generation = target.preview_generation
        future = self.executor.submit(load_image, config, 960, 600)
        def loaded():
            if target is not self.settings_window or self.shutting_down or generation != target.preview_generation:
                return GLib.SOURCE_REMOVE
            try:
                pixbuf, images, index = future.result()
                target.thumbnail_ready(pixbuf, images[index], len(images), config.fit)
            except Exception as e:
                target.thumbnail_failed(str(e))
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(loaded))

    def dismiss(self, reason="dismissed"):
        had_saver = bool(self.windows or self.pending)
        self.generation += 1
        self.pending = False
        for name in list(self.timers):
            self._cancel_timer(name)
        if self.desktop:
            self.desktop.stop_activity_watch()
        windows, self.windows = self.windows, []
        for window in windows:
            window.destroy()
        self.images = []
        self.slides = {}
        self.preview = False
        self.suppress_until = time.monotonic() + 1
        self.last_event = reason
        if had_saver:
            LOG.info("退出屏保：%s", reason)

    def status(self):
        display = Gdk.Display.get_default()
        try:
            idle = self.desktop.idletime() if self.desktop and self.desktop.available() else None
        except GLib.Error:
            idle = None
        return {"application": APP_ID, "backend": type(display).__name__, "config_file": str(config_path()),
                "config": asdict(self.config), "connected": bool(self.desktop and self.desktop.available()),
                "blocked_reason": self.desktop.blocked_reason(self.config.respect_inhibitors) if self.desktop else "GNOME 未连接",
                "idle_ms": idle, "monitor_count": display.get_n_monitors(), "loading": self.pending,
                "showing": bool(self.windows), "preview": self.preview, "activations": self.activations,
                "settings_open": bool(self.settings_window and self.settings_window.get_visible()),
                "settings_active": bool(self.settings_window and self.settings_window.is_active()),
                "last_image": self.last_image, "last_event": self.last_event, "last_error": self.last_error,
                "monitors": [{"key": m.key, "name": m.name, "index": m.index, "width": m.width,
                              "height": m.height, "primary": m.primary} for m in self.monitors],
                "windows": [{"monitor": w.monitor_index, "size": list(w.get_size()),
                             "monitor_key": w.monitor_key, "image_path": w.image_path, "fit": w.canvas.fit,
                             "fullscreen": bool(w.get_window() and w.get_window().get_state() & Gdk.WindowState.FULLSCREEN)}
                            for w in self.windows]}

    def _quit_signal(self):
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_shutdown(self):
        self.shutting_down = True
        self.dismiss("shutdown")
        if self.desktop:
            self.desktop.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        Gtk.Application.do_shutdown(self)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Parse locally: help/invalid arguments must not start a background instance.
    parser = argument_parser()
    args = parser.parse_args()
    if not 0 <= args.duration <= 3600:
        parser.error("预览时间必须在 0～3600 秒之间")
    if args.status or args.dismiss or args.quit or args.pause or args.resume:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        running = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "NameHasOwner", GLib.Variant("(s)", (APP_ID,)), None,
                                Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
        if not running:
            if args.pause or args.resume:
                replace(Config.load(), enabled=args.resume).save()
            elif args.status:
                print(json.dumps({"running": False, "application": APP_ID}, ensure_ascii=False))
            return 0
    return Screensaver4Wayland().run(sys.argv)
