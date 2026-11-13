#!/usr/bin/python3
"""Build a .deb and clean, deterministic source archives without root or pip."""
import argparse
import ast
from datetime import timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "screensaver4wayland"
APP_ID = "io.github.sunmaple.Screensaver4Wayland"
SOURCE_FILES = ("README.md", "RELEASING.md", "LICENSE", ".gitignore", "install.py", NAME)
SOURCE_DIRS = (NAME + "_lib", "assets", "tests", "scripts", "packaging", "releases", ".github")


def version():
    tree = ast.parse((ROOT / (NAME + "_lib/__init__.py")).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            value = ast.literal_eval(node.value)
            if not re.fullmatch(r"\d+\.\d+\.\d+", value):
                raise ValueError("Expected a stable MAJOR.MINOR.PATCH version")
            return value
    raise ValueError("Application version not found")


def metadata():
    changelog = (ROOT / "packaging/debian/changelog").read_text()
    package_version = re.search(r"^screensaver4wayland \(([^)]+)\)", changelog).group(1)
    maintainer, date = re.search(r"^ -- (.+)  (.+)$", changelog, re.MULTILINE).groups()
    stamp = int(parsedate_to_datetime(date).timestamp())
    return package_version, maintainer, int(os.environ.get("SOURCE_DATE_EPOCH", stamp))


def sources():
    paths = [ROOT / name for name in SOURCE_FILES]
    for directory in SOURCE_DIRS:
        paths += [p for p in (ROOT / directory).rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Source must be a regular file: {path}")
    return sorted(set(paths), key=lambda p: p.relative_to(ROOT).as_posix())


def source_mode(path):
    relative = path.relative_to(ROOT).as_posix()
    return 0o755 if relative in (NAME, "install.py") or relative.startswith("scripts/") and path.suffix == ".py" else 0o644


def source_archives(output, app_version, epoch):
    prefix = f"{NAME}-{app_version}"
    files = sources()
    tar_path = output / f"{prefix}-source.tar.gz"
    with tar_path.open("wb") as stream, gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in files:
                contents = path.read_bytes()
                entry = tarfile.TarInfo(prefix + "/" + path.relative_to(ROOT).as_posix())
                entry.size, entry.mode, entry.mtime = len(contents), source_mode(path), epoch
                entry.uid = entry.gid = 0
                entry.uname = entry.gname = "root"
                archive.addfile(entry, io.BytesIO(contents))
    zip_path = output / f"{prefix}-source.zip"
    date = time.gmtime(max(epoch, 315532800))[:6]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            entry = zipfile.ZipInfo(prefix + "/" + path.relative_to(ROOT).as_posix(), date_time=date)
            entry.create_system = 3
            entry.external_attr = (0o100000 | source_mode(path)) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes())
    return tar_path, zip_path


def build_deb(output, package_version, maintainer, epoch, homepage):
    with tempfile.TemporaryDirectory(prefix="screensaver4wayland-build-") as directory:
        stage = Path(directory) / NAME
        stage.mkdir()

        def put(source, destination, mode=0o644):
            path = stage / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, path)
            path.chmod(mode)
            return path

        put(NAME, f"usr/share/{NAME}/{NAME}", 0o755)
        for path in (ROOT / (NAME + "_lib")).rglob("*.py"):
            put(path.relative_to(ROOT), f"usr/share/{NAME}/" + path.relative_to(ROOT).as_posix())
        (stage / "usr/bin").mkdir(parents=True)
        (stage / "usr/bin" / NAME).symlink_to(f"../share/{NAME}/{NAME}")
        put("packaging/start-session", f"usr/libexec/{NAME}-start-session", 0o755)
        put(f"assets/{APP_ID}.svg", f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg")
        put(f"packaging/{APP_ID}.desktop", f"usr/share/applications/{APP_ID}.desktop")
        put(f"packaging/{NAME}.service", f"usr/lib/systemd/user/{NAME}.service")
        autostart = f"etc/xdg/autostart/{APP_ID}.desktop"
        put(f"packaging/{NAME}-autostart.desktop", autostart)
        doc = stage / "usr/share/doc" / NAME
        doc.mkdir(parents=True)
        (doc / "changelog.Debian.gz").write_bytes(gzip.compress((ROOT / "packaging/debian/changelog").read_bytes(), mtime=0))
        (doc / "README.md.gz").write_bytes(gzip.compress((ROOT / "README.md").read_bytes(), mtime=0))
        license_text = (ROOT / "LICENSE").read_text()
        copyright_text = ("Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
                          f"Upstream-Name: {NAME}\n\nFiles: *\nCopyright: 2026 SunMaple\nLicense: MIT\n")
        copyright_text += "\n".join(" " + line if line else " ." for line in license_text.splitlines()) + "\n"
        (doc / "copyright").write_text(copyright_text)
        control_dir = stage / "DEBIAN"
        control_dir.mkdir()
        size = math.ceil(sum(p.stat().st_size for p in stage.rglob("*") if p.is_file() and not p.is_symlink()) / 1024)
        control = (ROOT / "packaging/debian/control.in").read_text()
        for old, new in (("@VERSION@", package_version), ("@MAINTAINER@", maintainer), ("@INSTALLED_SIZE@", str(size))):
            control = control.replace(old, new)
        if homepage:
            if not homepage.startswith("https://") or any(c in homepage for c in "\r\n"):
                raise ValueError("Homepage must be an HTTPS URL without line breaks")
            control = "Homepage: " + homepage + "\n" + control
        (control_dir / "control").write_text(control)
        (control_dir / "conffiles").write_text("/" + autostart + "\n")
        for script in ("postinst", "prerm", "postrm"):
            put("packaging/debian/" + script, "DEBIAN/" + script, 0o755)
        hashes = []
        for path in sorted(stage.rglob("*")):
            if path.is_file() and not path.is_symlink() and control_dir not in path.parents:
                relative = path.relative_to(stage).as_posix()
                if relative != autostart:
                    hashes.append(hashlib.md5(path.read_bytes()).hexdigest() + "  " + relative)
        (control_dir / "md5sums").write_text("\n".join(hashes) + "\n")
        for path in sorted(stage.rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o755)
            elif not path.is_symlink() and not path.stat().st_mode & 0o111:
                path.chmod(0o644)
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        stage.chmod(0o755)
        os.utime(stage, (epoch, epoch))
        result = output / f"{NAME}_{package_version}_all.deb"
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch))
        subprocess.run(["dpkg-deb", "--root-owner-group", "--uniform-compression", "-Zxz", "-z9", "--build", str(stage), str(result)],
                       check=True, env=env)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--homepage", default="")
    parser.add_argument("--maintainer")
    parser.add_argument("--expect-tag")
    parser.add_argument("--print-version", action="store_true")
    args = parser.parse_args()
    app_version = version()
    if args.print_version:
        print(app_version)
        return
    if args.expect_tag and args.expect_tag != "v" + app_version:
        parser.error("Tag must match application version: v" + app_version)
    package_version, maintainer, epoch = metadata()
    if package_version.rsplit("-", 1)[0] != app_version:
        parser.error("Debian changelog and application versions differ")
    maintainer = args.maintainer or maintainer
    if any(c in maintainer for c in "\r\n") or not re.search(r".+ <[^<>\s]+@[^<>\s]+>$", maintainer):
        parser.error("Maintainer must be 'Name <email>'")
    if not shutil.which("dpkg-deb"):
        parser.error("dpkg-deb is required (Ubuntu package: dpkg)")
    output = (args.output_dir or ROOT / "dist" / ("v" + app_version)).resolve()
    output.mkdir(parents=True, exist_ok=True)
    deb = build_deb(output, package_version, maintainer, epoch, args.homepage)
    archives = source_archives(output, app_version, epoch)
    notes = output / "RELEASE_NOTES.md"
    notes.write_bytes((ROOT / "releases" / ("v" + app_version + ".md")).read_bytes())
    build_info = output / "BUILD_INFO.json"
    build_info.write_text(json.dumps({"application": NAME, "version": app_version, "debian_version": package_version,
                                     "architecture": "all", "license": "MIT", "source_date_epoch": epoch,
                                     "tested_desktop": "Ubuntu 22.04 / GNOME 42 / Wayland / amd64",
                                     "maintainer": maintainer, "homepage": args.homepage}, ensure_ascii=False, indent=2) + "\n")
    artifacts = [deb, *archives, notes, build_info]
    checksum = output / "SHA256SUMS"
    checksum.write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in sorted(artifacts)))
    for artifact in [*artifacts, checksum]:
        print(artifact)


if __name__ == "__main__":
    main()
