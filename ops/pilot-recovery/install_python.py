#!/usr/bin/env python3
"""Install the checksum-pinned legacy Python, independently of the new app."""
from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import tarfile
import tempfile
from urllib.request import urlopen

VERSION = "3.13.4"
RELEASE = "20250610"
URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
       "20250610/cpython-3.13.4%2B20250610-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz")
# Official release asset digest, read from GitHub's release API on 2026-10-22.
SHA256 = "ffd0b484e40e4fffdfcac265560e109456f802485f1f27e3cd314763b2b1587c"
SIZE = 35447894


def install(destination: Path, downloaded_archive: Path | None = None) -> Path:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise ValueError("The reviewed legacy interpreter supports Linux amd64 only")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Legacy interpreter destination must be new")
    with tempfile.TemporaryDirectory(prefix="cl-python-download-") as temporary:
        archive = downloaded_archive or Path(temporary) / "python.tar.gz"
        if downloaded_archive is None:
            with urlopen(URL, timeout=120) as response, archive.open("xb") as output:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > SIZE:
                        raise ValueError("Legacy interpreter download exceeds the pinned size")
                    output.write(chunk)
        if archive.stat().st_size != SIZE or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise ValueError("Legacy interpreter archive does not match the official pinned digest")
        destination.mkdir(mode=0o700)
        # Only the checksum-pinned release is accepted. Python's data filter
        # additionally rejects archive paths/links outside this private root.
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(destination, filter="data")
    return destination / "python/bin/python3.13"
