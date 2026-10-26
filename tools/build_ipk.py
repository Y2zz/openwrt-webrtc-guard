#!/usr/bin/env python3
"""Build an architecture-independent opkg package; no SDK or compiler required."""
from __future__ import annotations

import argparse
import gzip
import io
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package/luci-app-webrtc-guard"


def archive(files: dict[str, tuple[bytes, int]]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for name, (data, mode) in sorted(files.items()):
                item = tarfile.TarInfo("./" + name)
                item.size, item.mode, item.mtime = len(data), mode, 0
                item.uid = item.gid = 0
                item.uname = item.gname = "root"
                tar.addfile(item, io.BytesIO(data))
    return output.getvalue()


def build(output: Path) -> Path:
    makefile = (PACKAGE / "Makefile").read_text()
    version = re.search(r"^PKG_VERSION:=(.+)$", makefile, re.M)[1]
    release = re.search(r"^PKG_RELEASE:=(.+)$", makefile, re.M)[1]
    dependencies = re.search(r"^  DEPENDS:=(.+)$", makefile, re.M)[1]
    dependencies = ", ".join(dependencies.replace("+", "").split())
    payload = {}
    for source, prefix in [(PACKAGE / "root", ""), (PACKAGE / "htdocs", "www/")]:
        for path in source.rglob("*"):
            if path.is_file():
                name = prefix + path.relative_to(source).as_posix()
                mode = 0o755 if name.startswith(("etc/init.d/", "etc/uci-defaults/", "usr/libexec/")) else 0o644
                payload[name] = (path.read_bytes(), mode)
    size = sum(len(data) for data, _ in payload.values())
    control = (
        "Package: luci-app-webrtc-guard\n"
        f"Version: {version}-{release}\n"
        f"Depends: {dependencies}\n"
        "Architecture: all\n"
        "Section: luci\n"
        "Priority: optional\n"
        f"Installed-Size: {size}\n"
        "License: GPL-2.0-only\n"
        "Description: LuCI STUN/TURN and UDP traffic guard for firewall4\n"
    )
    controls = {
        "control": (control.encode(), 0o644),
        "conffiles": (b"/etc/config/webrtc-guard\n", 0o644),
    }
    for hook in ("postinst", "prerm"):
        script = re.search(
            rf"define Package/luci-app-webrtc-guard/{hook}\n(.*?)\nendef",
            makefile, re.S,
        )[1].replace("$$", "$") + "\n"
        controls[hook] = (script.encode(), 0o755)
    result = archive({
        "debian-binary": (b"2.0\n", 0o644),
        "control.tar.gz": (archive(controls), 0o644),
        "data.tar.gz": (archive(payload), 0o644),
    })
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"luci-app-webrtc-guard_{version}-{release}_all.ipk"
    target.write_bytes(result)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    print(build(parser.parse_args().output))
