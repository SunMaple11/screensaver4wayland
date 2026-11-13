#!/usr/bin/python3
"""Install or remove Screensaver4Wayland for the current user, without sudo."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess

APP_ID = "io.github.sunmaple.Screensaver4Wayland"
SOURCE = Path(__file__).resolve().parent
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DEST = DATA_HOME / "screensaver4wayland"
BIN = Path.home() / ".local/bin/screensaver4wayland"
DESKTOP = DATA_HOME / "applications" / f"{APP_ID}.desktop"
ICON = DATA_HOME / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
UNIT = CONFIG_HOME / "systemd/user/screensaver4wayland.service"
LEGACY_NAME = "quietframe"
LEGACY_APP_ID = "io.github.sunmaple.QuietFrame"
LEGACY_DIR = DATA_HOME / LEGACY_NAME
LEGACY_BIN = BIN.with_name(LEGACY_NAME)
LEGACY_UNIT = UNIT.with_name(LEGACY_NAME + ".service")


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check)


def quoted_exec(path):
    # Both desktop entries and systemd use quoted arguments (not shell syntax).
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def migrate_preferences(config_home=CONFIG_HOME):
    """Copy the old preferences once; keep both backups and newer preferences intact."""
    original = config_home / LEGACY_NAME / "config.json"
    destination = config_home / "screensaver4wayland" / "config.json"
    if destination.exists() or not original.is_file():
        return False
    from screensaver4wayland_lib.config import Config
    Config.load(original)  # Validate before replacing the running installation.
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, destination)
    return True


def legacy_is_ours():
    marker = LEGACY_DIR / "quietframe_lib/app.py"
    return marker.is_file() and LEGACY_APP_ID in marker.read_text(encoding="utf-8")


def finish_legacy_migration():
    if LEGACY_UNIT.exists():
        systemctl("disable", "--now", LEGACY_NAME + ".service", check=False)
    for path in (LEGACY_UNIT, DATA_HOME / "applications" / f"{LEGACY_APP_ID}.desktop",
                 DATA_HOME / "icons/hicolor/scalable/apps" / f"{LEGACY_APP_ID}.svg"):
        path.unlink(missing_ok=True)
    if LEGACY_BIN.is_symlink() and LEGACY_BIN.resolve() in (LEGACY_DIR / LEGACY_NAME, DEST / "screensaver4wayland"):
        LEGACY_BIN.unlink()
        LEGACY_BIN.symlink_to(BIN)
    # Retain old application data too: users may have placed images there.
    systemctl("daemon-reload")


def refresh_desktop_caches():
    commands = (("gtk-update-icon-cache", "-f", "-t", "-q", str(DATA_HOME / "icons/hicolor")),
                ("update-desktop-database", str(DATA_HOME / "applications")))
    for command in commands:
        executable = shutil.which(command[0])
        if executable and Path(command[-1]).is_dir():
            result = subprocess.run([executable, *command[1:]], capture_output=True, text=True)
            if result.returncode:
                print(f"桌面缓存刷新失败（{command[0]}）：{result.stderr.strip()}")


def main():
    parser = argparse.ArgumentParser(description="安装静景图片屏保（当前用户）")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        systemctl("disable", "--now", "screensaver4wayland.service", check=False)
        if LEGACY_BIN.is_symlink() and LEGACY_BIN.resolve() == DEST / "screensaver4wayland":
            LEGACY_BIN.unlink()
        for path in (BIN, DESKTOP, ICON, UNIT):
            path.unlink(missing_ok=True)
        if DEST.exists():
            shutil.rmtree(DEST)
        refresh_desktop_caches()
        systemctl("daemon-reload")
        print("已卸载静景。个人配置保留在 " + str(CONFIG_HOME / "screensaver4wayland"))
        return
    if BIN.exists() and not (BIN.is_symlink() and BIN.resolve() == DEST / "screensaver4wayland"):
        raise SystemExit(f"{BIN} 已存在且不是静景的安装链接；请先另行处理该文件")
    legacy = legacy_is_ours()
    migrate_preferences()
    legacy_was_active = False
    if legacy and LEGACY_UNIT.exists():
        legacy_was_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", LEGACY_NAME + ".service"]).returncode == 0
        systemctl("stop", LEGACY_NAME + ".service")
    if UNIT.exists():
        systemctl("stop", "screensaver4wayland.service", check=False)
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "screensaver4wayland", DEST / "screensaver4wayland")
    (DEST / "screensaver4wayland").chmod(0o755)
    shutil.copytree(SOURCE / "screensaver4wayland_lib", DEST / "screensaver4wayland_lib", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for path in (BIN, DESKTOP, ICON, UNIT):
        path.parent.mkdir(parents=True, exist_ok=True)
    BIN.unlink(missing_ok=True)
    BIN.symlink_to(DEST / "screensaver4wayland")
    shutil.copy2(SOURCE / "assets" / f"{APP_ID}.svg", ICON)
    executable = quoted_exec(DEST / "screensaver4wayland")
    DESKTOP.write_text(f"""[Desktop Entry]
Type=Application
Name=screensaver4wayland · 静景
Name[zh_CN]=静景 · 屏保控制台
Comment=Image screensaver for GNOME Wayland
Comment[zh_CN]=选择屏保图片、设置闲置时间和预览
Exec={executable} --settings
Icon={APP_ID}
Terminal=false
Categories=Settings;
Keywords=screensaver4wayland;Screensaver;Wayland;屏保;静景;
StartupNotify=true
StartupWMClass={APP_ID}
Actions=Preview;Pause;Resume;

[Desktop Action Preview]
Name=Preview
Name[zh_CN]=立即预览
Exec={executable} --preview

[Desktop Action Pause]
Name=Pause
Name[zh_CN]=暂停自动屏保
Exec={executable} --pause

[Desktop Action Resume]
Name=Resume
Name[zh_CN]=恢复自动屏保
Exec={executable} --resume
""", encoding="utf-8")
    UNIT.write_text(f"""[Unit]
Description=Screensaver4Wayland GNOME Wayland image screensaver
PartOf=graphical-session.target
After=graphical-session-pre.target

[Service]
Type=simple
ExecStart={executable} --daemon
Environment=GDK_BACKEND=wayland
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
""", encoding="utf-8")
    systemctl("daemon-reload")
    try:
        systemctl("enable", "--now", "screensaver4wayland.service")
    except subprocess.CalledProcessError:
        if legacy_was_active:
            systemctl("start", LEGACY_NAME + ".service", check=False)
        raise
    if legacy:
        finish_legacy_migration()
    refresh_desktop_caches()
    print("已安装并启动静景。应用列表搜索“静景”或执行：" + str(BIN) + " --settings")


if __name__ == "__main__":
    main()
