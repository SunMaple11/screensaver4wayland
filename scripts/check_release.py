#!/usr/bin/python3
"""Check release contents and optionally install/remove in a read-only-root sandbox."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

from build_release import APP_ID, NAME, ROOT, metadata, sources, version


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def check_archives(directory):
    prefix = f"{NAME}-{version()}/"
    expected = {prefix + p.relative_to(ROOT).as_posix(): p.read_bytes() for p in sources()}
    with zipfile.ZipFile(directory / f"{NAME}-{version()}-source.zip") as archive:
        assert set(archive.namelist()) == set(expected), "Unexpected files in source zip"
        for name, data in expected.items():
            assert archive.read(name) == data, name
    with tarfile.open(directory / f"{NAME}-{version()}-source.tar.gz") as archive:
        assert set(archive.getnames()) == set(expected), "Unexpected files in source tarball"
        for member in archive:
            assert member.isfile() and member.uid == member.gid == 0
            assert archive.extractfile(member).read() == expected[member.name]
    for name in expected:
        assert not any(part in {".git", ".artifacts", "dist", "__pycache__", ".env"} for part in PurePosixPath(name).parts)
    print(f"PASS: source archives contain exactly {len(expected)} approved files")


def check_deb(deb):
    assert output("dpkg-deb", "-f", str(deb), "Package") == NAME
    assert output("dpkg-deb", "-f", str(deb), "Version") == metadata()[0]
    assert output("dpkg-deb", "-f", str(deb), "Architecture") == "all"
    data = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(deb)])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = {m.name.removeprefix("./").rstrip("/"): m for m in archive}
        for name, member in members.items():
            assert not name.startswith("/") and ".." not in PurePosixPath(name).parts
            assert member.uid == member.gid == 0, name
            if not member.issym():
                assert not member.mode & 0o022, name
        required = {f"usr/bin/{NAME}", f"usr/share/{NAME}/{NAME}",
                    f"usr/libexec/{NAME}-start-session",
                    f"usr/share/{NAME}/{NAME}_lib/app.py", f"usr/share/{NAME}/{NAME}_lib/image_io.py",
                    f"usr/share/applications/{APP_ID}.desktop", f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
                    f"usr/lib/systemd/user/{NAME}.service", f"etc/xdg/autostart/{APP_ID}.desktop",
                    f"usr/share/doc/{NAME}/copyright"}
        assert required <= members.keys(), required - members.keys()
        launcher = members[f"usr/bin/{NAME}"]
        assert launcher.issym() and launcher.linkname == f"../share/{NAME}/{NAME}"
        for name in members:
            assert not name.endswith((".pyc", ".pyo"))
            assert not name.startswith(("home/", "root/"))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["dpkg-deb", "-R", str(deb), str(root)], check=True)
        for desktop in (root / "usr/share/applications").glob("*.desktop"):
            subprocess.run(["desktop-file-validate", str(desktop)], check=True)
        subprocess.run(["desktop-file-validate", str(root / f"etc/xdg/autostart/{APP_ID}.desktop")], check=True)
        for script in ("postinst", "prerm", "postrm"):
            subprocess.run(["sh", "-n", str(root / "DEBIAN" / script)], check=True)
        starter = root / f"usr/libexec/{NAME}-start-session"
        subprocess.run(["sh", "-n", str(starter)], check=True)
        fake_bin = root / "test-bin"
        fake_bin.mkdir()
        calls = root / "session-calls.log"
        systemctl = fake_bin / "systemctl"
        systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SCREENSAVER_TEST_CALL_LOG"\n')
        systemctl.chmod(0o755)
        env = dict(os.environ, PATH=str(fake_bin), SCREENSAVER_TEST_CALL_LOG=str(calls), XDG_SESSION_TYPE="x11")
        subprocess.run([str(starter)], env=env, check=True)
        assert not calls.exists(), "Autostart must not start on an X11 session"
        env["XDG_SESSION_TYPE"] = "wayland"
        subprocess.run([str(starter)], env=env, check=True)
        assert calls.read_text().splitlines() == ["--user daemon-reload", "--user start screensaver4wayland.service"]
        for line in (root / "DEBIAN/md5sums").read_text().splitlines():
            digest, relative = line.split("  ", 1)
            assert hashlib.md5((root / relative).read_bytes()).hexdigest() == digest, relative
        assert (root / "DEBIAN/conffiles").read_text().strip() == f"/etc/xdg/autostart/{APP_ID}.desktop"
    print("PASS: .deb metadata, paths, ownership, launchers, scripts and file hashes")


def install_smoke(deb):
    if not shutil.which("bwrap"):
        raise SystemExit("Install smoke test requires bubblewrap; no host installation was attempted")
    dependencies = [entry.strip().split(" ", 1)[0] for entry in output("dpkg-deb", "-f", str(deb), "Depends").split(",")]
    with tempfile.TemporaryDirectory(prefix="screensaver4wayland-dpkg-test-") as directory:
        root = Path(directory)
        admin = root / "var/lib/dpkg"
        for path in (admin / "info", admin / "updates", admin / "triggers", root / "var/log", root / "tmp",
                     root / "config", root / "cache", root / "run"):
            path.mkdir(parents=True, exist_ok=True)
        (root / "run").chmod(0o700)
        status = []
        for package in dependencies:
            name, ver, arch, state = output("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\t${Status}", package).split("\t")
            if state != "install ok installed":
                raise SystemExit(f"Runtime dependency unavailable for test: {package}")
            status.append(f"Package: {name}\nStatus: install ok installed\nArchitecture: {arch}\nVersion: {ver}\nMaintainer: Isolated test <test@example.invalid>\nDescription: Host dependency used by isolated test\n")
            (admin / "info" / (name + ".list")).write_text("")
        (admin / "status").write_text("\n".join(status))
        (admin / "available").touch()
        preferences = root / "home/test-user/.config" / NAME / "config.json"
        preferences.parent.mkdir(parents=True)
        preferences.write_text('{"idle_seconds":900}\n')
        before = preferences.read_bytes()
        base = ["bwrap", "--unshare-user", "--uid", "0", "--gid", "0", "--unshare-pid", "--unshare-net",
                "--die-with-parent", "--ro-bind", "/", "/", "--bind", str(root), str(root),
                "--proc", "/proc", "--dev", "/dev", "--setenv", "TMPDIR", str(root / "tmp"),
                "--setenv", "XDG_CONFIG_HOME", str(root / "config"),
                "--setenv", "XDG_CACHE_HOME", str(root / "cache"),
                "--setenv", "XDG_RUNTIME_DIR", str(root / "run"),
                "--setenv", "GSETTINGS_BACKEND", "memory",
                "--unsetenv", "DISPLAY", "--unsetenv", "WAYLAND_DISPLAY", "--unsetenv", "DBUS_SESSION_BUS_ADDRESS"]

        def run(*command):
            subprocess.run([*base, *command], check=True)

        dpkg = ["dpkg", "--root", str(root), "--force-script-chrootless", "--log=" + str(root / "var/log/dpkg.log")]
        run(*dpkg, "--install", str(deb))
        assert output("dpkg-query", "--admindir", str(admin), "-W", "-f=${Status}", NAME) == "install ok installed"
        assert list((root / f"usr/share/{NAME}/{NAME}_lib/__pycache__").glob("*.pyc")), "postinst did not compile Python files"
        run(str(root / "usr/bin" / NAME), "--help")
        run("env", "DPKG_ROOT=" + str(root), str(admin / "info" / (NAME + ".postinst")), "configure", metadata()[0])
        run(*dpkg, "--install", str(deb))
        run(*dpkg, "--remove", NAME)
        assert not (root / "usr/bin" / NAME).exists()
        assert not (root / f"usr/share/{NAME}/{NAME}_lib").exists(), "Bytecode was not cleaned"
        assert not (root / f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg").exists()
        run(*dpkg, "--purge", NAME)
        assert not (root / f"etc/xdg/autostart/{APP_ID}.desktop").exists()
        assert preferences.read_bytes() == before, "Personal configuration changed"
    print("PASS: isolated dpkg install, reconfigure, reinstall, remove and purge; user data preserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "dist" / ("v" + version()))
    parser.add_argument("--install-smoke", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        expected, filename = line.split("  ", 1)
        assert Path(filename).name == filename
        assert hashlib.sha256((directory / filename).read_bytes()).hexdigest() == expected, filename
    print("PASS: SHA-256 release checksums")
    check_archives(directory)
    deb = directory / f"{NAME}_{metadata()[0]}_all.deb"
    check_deb(deb)
    if args.install_smoke:
        install_smoke(deb)


if __name__ == "__main__":
    main()
