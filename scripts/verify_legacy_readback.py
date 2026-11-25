"""Offline restore drill for a version-pinned legacy backup downloaded off-host."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import postgres_cutover as cutover
from scripts import recovery_artifacts as artifacts
from scripts import start_sqlite_rollback as rollback


def verify(ciphertext: Path, reference: dict, seal: dict, identity: Path, *, age_binary: str = "age") -> dict:
    ref = artifacts.object_reference(reference)
    if (seal.get("kind") != "cl-encrypted-legacy-checkpoint"
            or seal.get("legacy_commit") != rollback.EXPECTED_OLD_COMMIT
            or seal.get("sha256") != ref["sha256"] or seal.get("size_bytes") != ref["size_bytes"]):
        raise cutover.CutoverError("Sealing receipt does not match the pinned object")
    if ciphertext.is_symlink() or not ciphertext.is_file() or ciphertext.stat().st_size != ref["size_bytes"]:
        raise cutover.CutoverError("Downloaded backup must be a regular file of the pinned size")
    with tempfile.TemporaryDirectory(prefix="cl-offline-readback-") as directory:
        work = Path(directory)
        # Verify the exact private copy handed to age, even if the download path changes.
        sealed = work / "verified.age"
        digest = hashlib.sha256()
        remaining = ref["size_bytes"]
        with ciphertext.open("rb") as source, sealed.open("xb") as dest:
            while chunk := source.read(1024 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise cutover.CutoverError("Downloaded backup grew during verification")
                digest.update(chunk)
                dest.write(chunk)
        if remaining or digest.hexdigest() != ref["sha256"]:
            raise cutover.CutoverError("Downloaded backup checksum differs from the pinned object")
        archive = work / "legacy.tar"
        artifacts.backup_module().Age(age_binary).decrypt(sealed, archive, identity)
        extracted = work / "contents"
        artifacts.extract_private_tar(archive, extracted)
        bundle = extracted / "bundle"
        if cutover.sha256_file(bundle / "manifest.json") != seal.get("bundle_manifest_sha256"):
            raise cutover.CutoverError("Recovered bundle differs from the original sealing receipt")
        rollback.original_environment(extracted / "original-environment.json")
        restored = cutover.restore_sqlite(bundle, work / "restored.sqlite3", work / "restored-photos")
        return {
            "kind": "cl-offline-legacy-restore",
            "ciphertext_sha256": ref["sha256"],
            "sqlite_restored": restored["sqlite_restored"],
            "order_count": restored["tables"]["orders"]["rows"],
            "proof_file_count": restored["proof_files"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("ciphertext", "object-reference", "seal-receipt", "identity"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.ciphertext, artifacts.private_json(args.object_reference),
                        artifacts.private_json(args.seal_receipt), args.identity)
    except Exception as error:
        print(f"STOP: {type(error).__name__}. Offline restore was not verified.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
