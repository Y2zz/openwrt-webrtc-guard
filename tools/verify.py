#!/usr/bin/env python3
"""Check source syntax, package contents, lifecycle hooks and reproducibility."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

from build_ipk import PACKAGE, ROOT, build


def verify(tag=None):
    for directory in (ROOT / "tools", ROOT / "tests"):
        for path in directory.glob("*.py"):
            ast.parse(path.read_text(), filename=str(path))
    for path in (PACKAGE / "root").rglob("*.json"):
        json.loads(path.read_text())
    subprocess.run(["node", "--check", str(PACKAGE / "htdocs/luci-static/resources/view/webrtc-guard.js")], check=True)
    for path in (PACKAGE / "root").rglob("*"):
        if path.is_file() and path.read_bytes().startswith(b"#!/bin/sh"):
            subprocess.run(["sh", "-n", str(path)], check=True)
            if not path.stat().st_mode & 0o111:
                raise ValueError(f"Executable mode missing: {path}")

    version = re.search(r"^PKG_VERSION:=(.+)$", (PACKAGE / "Makefile").read_text(), re.M)[1]
    if tag is not None:
        if tag != f"v{version}":
            raise ValueError(f"Tag {tag!r} does not match package v{version}")
        notes = ROOT / "docs/releases" / f"{tag}.md"
        if not notes.is_file() or not notes.read_text().strip():
            raise ValueError(f"Missing release notes: {notes}")

    result = build(ROOT / "dist")
    content = result.read_bytes()
    with tempfile.TemporaryDirectory() as scratch:
        if build(Path(scratch)).read_bytes() != content:
            raise ValueError("IPK build is not reproducible")
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as outer:
            if outer.extractfile("./debian-binary").read() != b"2.0\n":
                raise ValueError("Invalid opkg archive marker")
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile("./control.tar.gz").read()), mode="r:gz") as control:
                metadata = control.extractfile("./control").read().decode()
                if "Architecture: all\n" not in metadata or f"Version: {version}-" not in metadata:
                    raise ValueError("Incorrect package metadata")
                if control.extractfile("./conffiles").read() != b"/etc/config/webrtc-guard\n":
                    raise ValueError("UCI configuration is not marked for preservation")
                for hook in ("postinst", "prerm"):
                    script = control.extractfile(f"./{hook}").read()
                    if not control.getmember(f"./{hook}").mode & 0o111:
                        raise ValueError(f"Package hook is not executable: {hook}")
                    subprocess.run(["sh", "-n"], input=script, check=True)
            with tarfile.open(fileobj=io.BytesIO(outer.extractfile("./data.tar.gz").read()), mode="r:gz") as payload:
                for directory, prefix in [(PACKAGE / "root", ""), (PACKAGE / "htdocs", "www/")]:
                    for source in directory.rglob("*"):
                        if not source.is_file():
                            continue
                        name = "./" + prefix + source.relative_to(directory).as_posix()
                        if payload.extractfile(name).read() != source.read_bytes():
                            raise ValueError(f"Packaged content differs: {name}")
                        if source.read_bytes().startswith(b"#!/bin/sh") and not payload.getmember(name).mode & 0o111:
                            raise ValueError(f"Packaged script is not executable: {name}")
    digest = hashlib.sha256(content).hexdigest()
    (ROOT / "dist/SHA256SUMS").write_text(f"{digest}  {result.name}\n")
    print(f"Source checks, package checks and reproducibility passed: {result.name}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Require this release tag to match PKG_VERSION")
    verify(parser.parse_args().tag)
