#!/usr/bin/python3
"""Real GNOME 42 headless Wayland test, on a private D-Bus and temporary config.

No settings, extensions, input events or lock calls reach the user's desktop.
"""
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(os.environ.get("SCREENSAVER4WAYLAND_APP_ROOT", ROOT)).resolve()
ARTIFACTS = ROOT / ".artifacts"

if os.environ.get("SCREENSAVER4WAYLAND_INTEGRATION_BUS") != "1":
    env = dict(os.environ, SCREENSAVER4WAYLAND_INTEGRATION_BUS="1")
    os.execvpe("dbus-run-session", ["dbus-run-session", "--config-file=" + str(ROOT / "tests/private-session.conf"),
                                  "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env)

from gi.repository import Gio, GLib
import cairo


def wait_for(function, timeout=15):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            value = function()
            if value:
                return value
        except Exception as e:
            error = e
        time.sleep(0.2)
    raise AssertionError(f"Timed out: {function}; last error: {error}")


def dbus(name, path, iface, method, signature=None, args=()):
    return Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
        name, path, iface, method, GLib.Variant(signature, args) if signature else None,
        None, Gio.DBusCallFlags.NONE, 2000, None).unpack()


def shell_eval(script):
    ok, result = dbus("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval", "(s)", (script,))
    if not ok:
        raise AssertionError(f"Shell eval failed: {result}")
    return json.loads(result) if result else None


def cli(*args):
    result = subprocess.run([sys.executable, str(APP_ROOT / "screensaver4wayland"), *args], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout


def status():
    return json.loads(cli("--status"))


def set_state(locked=False, inhibitors=0, active=True):
    dbus("org.gnome.SessionManager", "/org/gnome/SessionManager", "io.github.sunmaple.Screensaver4Wayland.TestSession",
         "SetState", "(bub)", (locked, inhibitors, active))


def screenshot(name):
    result = dbus("org.gnome.Shell", "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot",
                  "Screenshot", "(bbs)", (False, False, str(ARTIFACTS / name)))
    assert result[0], result


def activity():
    shell_eval("global.screensaver4waylandPointer.notify_relative_motion(imports.gi.GLib.get_monotonic_time(), 12, 3); true")


ARTIFACTS.mkdir(exist_ok=True)
processes = []
logs = []
with tempfile.TemporaryDirectory(prefix="screensaver4wayland-test-") as directory:
    temp = Path(directory)
    for name in ("config", "data", "cache", "run"):
        (temp / name).mkdir(mode=0o700)
    os.environ.update(XDG_CONFIG_HOME=str(temp / "config"), XDG_DATA_HOME=str(temp / "data"),
                      XDG_CACHE_HOME=str(temp / "cache"), XDG_RUNTIME_DIR=str(temp / "run"),
                      WAYLAND_DISPLAY="screensaver4wayland-test", GDK_BACKEND="wayland", LIBGL_ALWAYS_SOFTWARE="1",
                      XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_TYPE="wayland", GSETTINGS_BACKEND="keyfile",
                      GNOME_SHELL_SESSION_MODE="user", GIO_USE_VFS="local", GIO_USE_VOLUME_MONITOR="unix",
                      GTK_IM_MODULE="simple", GTK_MODULES="", NO_AT_BRIDGE="1")
    os.environ.pop("DISPLAY", None)
    # Activated services inherit the bus environment, not this Python process's.
    # Update that private bus BEFORE any activation, and never use the real dconf database.
    names = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY",
             "GDK_BACKEND", "LIBGL_ALWAYS_SOFTWARE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
             "GSETTINGS_BACKEND", "GNOME_SHELL_SESSION_MODE", "GIO_USE_VFS", "GIO_USE_VOLUME_MONITOR",
             "GTK_IM_MODULE", "GTK_MODULES", "NO_AT_BRIDGE")
    dbus("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "UpdateActivationEnvironment",
         "(a{ss})", ({name: os.environ[name] for name in names},))
    extension = temp / "data/gnome-shell/extensions/screensaver4wayland-test@local"
    extension.mkdir(parents=True)
    (extension / "metadata.json").write_text(json.dumps({"uuid": "screensaver4wayland-test@local", "name": "Isolated test controls", "description": "Private test bus only", "shell-version": ["42"]}))
    (extension / "extension.js").write_text("function init() {}\nfunction enable() { global.context.unsafe_mode = true; }\nfunction disable() { global.context.unsafe_mode = false; }\n")
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", "['screensaver4wayland-test@local']"], check=True)
    pictures = temp / "pictures"
    pictures.mkdir()
    for name, colors in (("a.png", (0.18, 0.36, 0.48)), ("b.png", (0.56, 0.25, 0.18))):
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 800, 600)
        context = cairo.Context(surface)
        context.set_source_rgb(*colors)
        context.paint()
        context.set_source_rgb(0.94, 0.83, 0.64)
        context.arc(600, 140, 65, 0, 7)
        context.fill()
        surface.write_to_png(str(pictures / name))
    (pictures / "c-corrupt.jpg").write_text("not an image")
    config = {"enabled": True, "idle_seconds": 5, "source": "folder", "image_path": "", "folder_path": str(pictures),
              "slideshow_seconds": 5, "fit": "cover", "respect_inhibitors": True}
    (temp / "config/screensaver4wayland").mkdir()
    (temp / "config/screensaver4wayland/config.json").write_text(json.dumps(config))
    # Install only the desktop identity and artwork inside the private desktop.
    # This lets us verify GNOME's real window-to-launcher matching without using
    # the live user's icon theme, launchers or session services.
    app_id = "io.github.sunmaple.Screensaver4Wayland"
    applications = temp / "data/applications"
    applications.mkdir()
    (applications / (app_id + ".desktop")).write_text(
        "[Desktop Entry]\nType=Application\nName=静景\n"
        f"Exec=/usr/bin/python3 {APP_ROOT / 'screensaver4wayland'} --settings\n"
        f"Icon={app_id}\nStartupWMClass={app_id}\n")
    icons = temp / "data/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True)
    icon = ROOT / "assets" / (app_id + ".svg") if APP_ROOT == ROOT else APP_ROOT.parent / "icons/hicolor/scalable/apps" / (app_id + ".svg")
    shutil.copy2(icon, icons)

    def start(args, log_name):
        log = (ARTIFACTS / log_name).open("w")
        logs.append(log)
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        return process

    try:
        start([sys.executable, str(ROOT / "tests/session_fixture.py")], "fixture.log")
        wait_for(lambda: dbus("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", "(s)", ("org.gnome.ScreenSaver",))[0])
        wait_for(lambda: dbus("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "GetActive") == (False,))
        shell = start(["gnome-shell", "--headless", "--wayland", "--no-x11", "--mode=user", "--wayland-display=screensaver4wayland-test",
                       "--virtual-monitor=1280x960", "--virtual-monitor=960x960"], "gnome.log")
        wait_for(lambda: shell_eval("global.context.unsafe_mode"), 25)
        wait_for(lambda: shell_eval("!imports.ui.main.layoutManager._startingUp"))
        # Close the initial Activities overview after startup animation finishes.
        shell_eval("imports.ui.main.overview.hide(); global.screensaver4waylandPointer = imports.gi.Clutter.get_default_backend().get_default_seat().create_virtual_device(imports.gi.Clutter.InputDeviceType.POINTER_DEVICE); true")
        shell_eval("global.screensaver4waylandPointer.notify_absolute_motion(imports.gi.GLib.get_monotonic_time(), 300, 300); true")
        wait_for(lambda: shell_eval("!imports.ui.main.overview.visible"))
        activity()
        app = start([sys.executable, "-c", "import faulthandler,signal,runpy,sys; faulthandler.register(signal.SIGUSR1); sys.argv=['screensaver4wayland','--daemon']; runpy.run_path(" + repr(str(APP_ROOT / "screensaver4wayland")) + ",run_name='__main__')"], "app.log")
        try:
            wait_for(lambda: status()["connected"])
        except Exception:
            if app.poll() is None:
                app.send_signal(signal.SIGUSR1)
                time.sleep(0.3)
            raise
        if "--icon-only" in sys.argv:
            cli("--pause")
            cli("--settings")
            association_script = "global.get_window_actors().filter(a => a.meta_window.get_pid() === " + str(app.pid) + ").map(a => { const w = a.meta_window; const app = imports.gi.Shell.WindowTracker.get_default().get_window_app(w); const info = app ? app.get_app_info() : null; return {title:w.get_title(),wm_class:w.get_wm_class(),gtk_app_id:w.get_gtk_application_id(),desktop_id:app ? app.get_id() : null,icon:info && info.get_icon() ? info.get_icon().to_string() : null}; })"
            association = wait_for(lambda: shell_eval(association_script))
            (ARTIFACTS / "icon-association.json").write_text(json.dumps(association, ensure_ascii=False, indent=2))
            print(json.dumps(association, ensure_ascii=False), flush=True)
            assert all(w["wm_class"] == app_id and w["gtk_app_id"] == app_id
                       and w["desktop_id"] == app_id + ".desktop" and w["icon"] == app_id
                       for w in association), association
            shell_eval("imports.ui.main.overview.show(); true")
            time.sleep(0.8)
            screenshot("icon-in-overview.png")
            print("PASS: Wayland window, GTK application, desktop entry and SVG icon all match", flush=True)
            cli("--quit")
            app.wait(timeout=5)
            raise SystemExit(0)
        current = wait_for(lambda: (s if (s := status())["showing"] else None), 15)
        assert current["backend"] == "GdkWaylandDisplay", current
        assert current["monitor_count"] == 2, current
        wait_for(lambda: len((s := status())["windows"]) == 2 and all(w["fullscreen"] for w in s["windows"]))
        current = status()
        (ARTIFACTS / "initial-status.json").write_text(json.dumps(current, indent=2, ensure_ascii=False))
        geometry = shell_eval("global.get_window_actors().filter(a => a.meta_window.get_title() === '静景 · Screensaver4Wayland').map(a => { const r = a.meta_window.get_frame_rect(); return {monitor:a.meta_window.get_monitor(),fullscreen:a.meta_window.is_fullscreen(),rect:{x:r.x,y:r.y,width:r.width,height:r.height}}; })")
        assert len(geometry) == 2 and len({w["monitor"] for w in geometry}) == 2, geometry
        time.sleep(0.5)
        screenshot("screensaver.png")
        surface = cairo.ImageSurface.create_from_png(str(ARTIFACTS / "screensaver.png"))
        pixels = surface.get_data()
        for window in geometry:
            rect = window["rect"]
            offset = (rect["y"] + 24) * surface.get_stride() + (rect["x"] + 24) * 4
            b, g, r = pixels[offset:offset + 3]
            assert abs(r - 46) <= 2 and abs(g - 92) <= 2 and abs(b - 122) <= 2, (r, g, b, window)
        first = current["last_image"]
        wait_for(lambda: status()["last_image"] != first, 10)
        wait_for(lambda: status()["last_image"] == first, 10)
        assert not status()["last_error"]
        print("PASS: native Wayland, idle activation, both monitors fullscreen, slideshow skips corrupt image", flush=True)
        activity()
        wait_for(lambda: not status()["showing"])
        print("PASS: global pointer activity dismisses both surfaces", flush=True)
        set_state(inhibitors=8)
        time.sleep(6)
        assert not status()["showing"], status()
        print("PASS: media/presentation inhibition suppresses automatic activation", flush=True)
        set_state()
        cli("--preview")
        wait_for(lambda: status()["showing"])
        set_state(locked=True)
        wait_for(lambda: not status()["showing"])
        cli("--preview")
        time.sleep(0.5)
        assert not status()["showing"]
        print("PASS: lock signal closes screen saver; locked session rejects preview", flush=True)
        set_state()
        cli("--preview")
        wait_for(lambda: status()["showing"])
        shell_eval("global.screensaver4waylandKeyboard = imports.gi.Clutter.get_default_backend().get_default_seat().create_virtual_device(imports.gi.Clutter.InputDeviceType.KEYBOARD_DEVICE); global.screensaver4waylandKeyboard.notify_keyval(imports.gi.GLib.get_monotonic_time(), imports.gi.Clutter.KEY_Escape, imports.gi.Clutter.KeyState.PRESSED); global.screensaver4waylandKeyboard.notify_keyval(imports.gi.GLib.get_monotonic_time(), imports.gi.Clutter.KEY_Escape, imports.gi.Clutter.KeyState.RELEASED); true")
        wait_for(lambda: not status()["showing"])
        print("PASS: keyboard input dismisses preview", flush=True)
        cli("--preview", "--duration", "2")
        wait_for(lambda: status()["showing"])
        wait_for(lambda: not status()["showing"])
        assert status()["last_event"] == "preview-timeout"
        cli("--pause")
        assert not status()["config"]["enabled"]
        cli("--settings")
        time.sleep(1)
        screenshot("settings.png")
        print("PASS: timed preview, pause, and native settings window", flush=True)
        cli("--quit")
        app.wait(timeout=5)
        assert app.returncode == 0
        print("PASS: clean shutdown", flush=True)
        ui = start([sys.executable, str(ROOT / "tests/ui_scenario.py")], "ui.log")
        ui.wait(timeout=75)
        print((ARTIFACTS / "ui.log").read_text(), flush=True)
        assert ui.returncode == 0, "Native UI scenario failed; see .artifacts/ui.log"
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for log in logs:
            log.close()
