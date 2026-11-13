"""Exercise native GTK controls inside the integration test's private desktop."""
import os
from pathlib import Path
import sys
import time
import traceback

if os.environ.get("SCREENSAVER4WAYLAND_INTEGRATION_BUS") != "1":
    raise SystemExit("Run via tests/integration.py")
ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(os.environ.get("SCREENSAVER4WAYLAND_APP_ROOT", ROOT)).resolve()
sys.path.insert(0, str(APP_ROOT))
from screensaver4wayland_lib.app import Screensaver4Wayland
from screensaver4wayland_lib.config import Config
from gi.repository import Gio, GLib

app = Screensaver4Wayland()
result = 0


def choose(window, path):
    window.source.set_active_id("image")
    window.image.set_filename(str(path))
    window.image.emit("file-set")


def screenshot(name):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    answer = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot",
                          "Screenshot", GLib.Variant("(bbs)", (False, False, str(ROOT / ".artifacts" / name))),
                          None, Gio.DBusCallFlags.NONE, 2000, None).unpack()
    assert answer[0]


def scenario():
    yield lambda: app.settings_window is not None, "open settings"
    window = app.settings_window
    pictures = Path(app.config.folder_path)
    a, b, corrupt = pictures / "a.png", pictures / "b.png", pictures / "c-corrupt.jpg"
    original = app.config
    yield lambda: bool(window.preview_path), "initial thumbnail"
    assert window.source_stack.get_visible_child_name() == original.source

    choose(window, b)
    yield lambda: window.preview_path == str(b), "live image preview"
    assert app.config == original and Config.load() == original
    assert window.dirty and window.apply_button.get_sensitive()
    window.set_idle_seconds(90)
    assert window.idle_unit.get_active_id() == "seconds"
    window.idle_unit.set_active_id("minutes")
    assert window.draft().idle_seconds == 120
    window.set_idle_seconds(90)
    window.fit.set_active_id("contain")
    window.apply_button.emit("clicked")
    yield lambda: not window.busy, "apply image and timer"
    assert app.config.image_path == str(b) and app.config.idle_seconds == 90
    assert app.config.fit == "contain" and Config.load() == app.config
    assert not window.dirty
    print("PASS UI: live image preview, exact seconds/minutes, apply and persist without restart", flush=True)

    # A damaged JPEG must not replace the previously applied good image.
    good = app.config
    choose(window, corrupt)
    yield lambda: bool(window.preview_error), "invalid image feedback"
    window.apply_button.emit("clicked")
    yield lambda: not window.busy, "reject damaged image"
    assert app.config == good and Config.load() == good
    assert "无法读取" in window.message.get_text()
    print("PASS UI: corrupt image reports an error and preserves applied settings", flush=True)

    # The immediate switch works while there is an invalid, unsaved draft.
    window.enabled.set_active(True)
    assert app.config.enabled
    b.rename(pictures / "b-hidden.bin")
    try:
        window.enabled.set_active(False)
        assert not app.config.enabled and not Config.load().enabled
    finally:
        (pictures / "b-hidden.bin").rename(b)
    assert window.image.get_filename() == str(corrupt)
    window.revert_button.emit("clicked")
    yield lambda: window.preview_path == str(b), "revert draft"
    assert window.draft() == app.config and not window.dirty
    print("PASS UI: immediate pause works with missing image; unsaved draft and undo behave correctly", flush=True)

    choose(window, a)
    window.set_idle_seconds(30)
    window.preview_button.emit("clicked")
    yield lambda: bool(app.windows), "apply and fullscreen preview"
    assert len(app.windows) == 2 and app.preview
    assert app.config.image_path == str(a) and app.config.idle_seconds == 30
    app.dismiss("ui-test-preview-finished")
    yield lambda: not app.windows and window.preview_path == str(a), "return to controls"
    print("PASS UI: apply-and-preview button immediately displays both monitors", flush=True)

    # Changing a source selects only that source's controls and updates the preview.
    window.source.set_active_id("folder")
    window.folder.set_filename(str(pictures))
    window.folder.emit("file-set")
    window.interval.set_value(10)
    window.apply_button.emit("clicked")
    yield lambda: not window.busy, "apply folder slideshow"
    assert app.config.source == "folder" and app.config.slideshow_seconds == 10
    assert window.source_stack.get_visible_child_name() == "folder"
    window.source.set_active_id("image")
    window.set_idle_seconds(300)
    window.preview_button.emit("clicked")
    yield lambda: bool(app.windows), "second preview"
    app.dismiss("ui-test-preview-finished")
    yield lambda: not window.busy and not app.windows, "settle controls"
    settled_after = time.monotonic() + 0.6
    yield lambda: time.monotonic() >= settled_after, "finish desktop window animation"
    screenshot("control-center.png")
    window.destroy()
    yield lambda: app.settings_window is None, "close settings"
    app.open_settings()
    yield lambda: app.settings_window is not None and bool(app.settings_window.preview_path), "reopen settings"
    assert app.settings_window.draft() == Config.load()
    print("PASS UI: folder controls, repeated preview, and reopen preserve the applied configuration", flush=True)

    # Ubuntu 22.04 commonly has no WebP GdkPixbuf loader. Exercise the full
    # thumbnail -> validation -> saved configuration -> fullscreen path anyway.
    from PIL import Image
    from gi.repository import Gdk
    webp = pictures / "屏保 WebP 测试.webp"
    with Image.open(a) as picture:
        picture.save(webp, "WEBP", lossless=True)
    window = app.settings_window
    choose(window, webp)
    yield lambda: window.preview_path == str(webp), "WebP live thumbnail"
    assert not window.preview_error
    window.preview_button.emit("clicked")
    yield lambda: bool(app.windows), "WebP fullscreen preview"
    assert app.config.image_path == str(webp) and app.last_image == str(webp)
    assert len(app.windows) == 2 and not app.last_error
    settled_after = time.monotonic() + 0.6
    yield lambda: time.monotonic() >= settled_after, "WebP window animation"
    assert all(w.get_window().get_state() & Gdk.WindowState.FULLSCREEN for w in app.windows)
    screenshot("webp-screensaver.png")
    app.dismiss("webp-test-complete")
    print("PASS UI: WebP thumbnail, apply, persistence, and native fullscreen on both monitors", flush=True)

    left, right = app.monitors
    common_image = app.config.image_path
    slide_folder = pictures / "right-screen"
    slide_folder.mkdir()
    for name, color in (("one.png", (190, 60, 35)), ("two.png", (35, 150, 65))):
        Image.new("RGB", (600, 400), color).save(slide_folder / name)

    window.monitor_selector.set_active_id(left.key)
    assert window.source.get_active_id() == "inherit"
    choose(window, a)
    window.fit.set_active_id("cover")
    window.monitor_selector.set_active_id(right.key)
    assert window.source.get_active_id() == "inherit"
    window.source.set_active_id("folder")
    window.folder.set_filename(str(slide_folder))
    window.folder.emit("file-set")
    window.interval.set_value(5)
    window.fit.set_active_id("contain")
    assert window.draft().image_path == common_image
    window.monitor_selector.set_active_id(left.key)
    yield lambda: window.preview_path == str(a), "left screen draft survives switching"
    window.monitor_selector.set_active_id(right.key)
    yield lambda: window.preview_path == str(slide_folder / "one.png"), "right screen folder thumbnail"
    window.preview_button.emit("clicked")
    yield lambda: len(app.windows) == 2, "independent screens fullscreen"
    windows = {w.monitor_key: w for w in app.windows}
    assert windows[left.key].image_path == str(a)
    assert windows[right.key].image_path == str(slide_folder / "one.png")
    assert windows[left.key].canvas.fit == "cover"
    assert windows[right.key].canvas.fit == "contain"
    settled_after = time.monotonic() + 0.6
    yield lambda: time.monotonic() >= settled_after, "independent screens animation"
    screenshot("per-monitor-screensaver.png")
    yield lambda: windows[right.key].image_path == str(slide_folder / "two.png"), "independent slideshow advances"
    assert windows[left.key].image_path == str(a)
    assert Config.load() == app.config
    app.dismiss("per-monitor-test-complete")
    window.monitor_selector.set_active_id(left.key)
    assert not window.dirty
    settled_after = time.monotonic() + 0.6
    yield lambda: time.monotonic() >= settled_after and window.preview_path == str(a), "compact controls settle"
    screenshot("per-monitor-settings.png")
    print("PASS UI: stable per-screen drafts, different simultaneous images, independent scaling and slideshow", flush=True)

    window.source.set_active_id("inherit")
    assert window.fit.get_active_id() == app.config.fit
    window.apply_button.emit("clicked")
    yield lambda: not window.busy, "restore default for one screen"
    assert left.key not in app.config.monitor_profiles
    assert right.key in app.config.monitor_profiles
    assert app.config.for_monitor(left.key).image_path == common_image
    window.destroy()
    app.open_settings()
    window = app.settings_window
    window.monitor_selector.set_active_id(right.key)
    yield lambda: window.preview_path == str(slide_folder / "one.png"), "per-screen configuration survives reopen"
    assert window.source.get_active_id() == "folder"
    assert window.interval.get_value_as_int() == 5
    print("PASS UI: inherit-default removes only that screen's override; profiles survive reopening", flush=True)


steps = scenario()
waiting = None


def tick():
    global result, waiting
    try:
        if waiting is not None:
            predicate, label, deadline = waiting
            if not predicate():
                if time.monotonic() > deadline:
                    raise AssertionError("UI timed out: " + label)
                return GLib.SOURCE_CONTINUE
        predicate, label = next(steps)
        waiting = predicate, label, time.monotonic() + 12
    except StopIteration:
        app.quit()
        return GLib.SOURCE_REMOVE
    except Exception:
        traceback.print_exc()
        result = 1
        app.quit()
        return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE


GLib.timeout_add(100, tick)
app.run(["screensaver4wayland", "--settings"])
raise SystemExit(result)
