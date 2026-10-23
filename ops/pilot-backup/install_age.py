#!/usr/bin/env python3
"""Install the reviewed official age release into a new directory (no root needed)."""
import argparse
import hashlib
from pathlib import Path
import platform
import tarfile
import tempfile
from urllib.request import urlopen


VERSION = "1.3.2"
URL = f"https://github.com/FiloSottile/age/releases/download/v{VERSION}/age-v{VERSION}-linux-amd64.tar.gz"
SHA256 = "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10"


def install(destination: Path, downloaded_archive: Path | None = None) -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit("This reviewed installer supports Linux amd64 only")
    if destination.exists() or destination.is_symlink():
        raise SystemExit("Installation destination must not exist")
    with tempfile.TemporaryDirectory(prefix="cl-age-download-") as temporary:
        archive = downloaded_archive or Path(temporary) / "age.tar.gz"
        if downloaded_archive is None:
            with urlopen(URL, timeout=120) as response, archive.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise SystemExit("Official release checksum mismatch; nothing installed")
        destination.mkdir(mode=0o755, parents=False)
        with tarfile.open(archive, "r:gz") as source:
            for binary in ("age", "age-keygen"):
                member = source.getmember(f"age/{binary}")
                if not member.isfile():
                    raise SystemExit("Unexpected release archive member")
                with source.extractfile(member) as stream:
                    (destination / binary).write_bytes(stream.read())
                (destination / binary).chmod(0o755)
    print(f"Installed age v{VERSION}; official archive SHA256 verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="Optional already-downloaded official archive; same checksum enforced")
    arguments = parser.parse_args()
    install(arguments.destination, arguments.archive)
