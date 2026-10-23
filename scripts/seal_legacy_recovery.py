#!/usr/bin/env python3
"""Encrypt the frozen pre-PG SQLite/photos/original environment together."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import postgres_cutover as cutover
from scripts import recovery_artifacts as artifacts
from scripts import start_sqlite_rollback as rollback


def seal(bundle: Path, environment: Path, recipient: str, output: Path, *, age_binary: str = "age") -> dict:
    manifest, _ = cutover.verify_bundle(bundle)
    if manifest["source_schema"] != "legacy_0004" or manifest["source_revision"] is not None:
        raise cutover.CutoverError("Only the frozen original legacy checkpoint can be sealed for old-code recovery")
    original = rollback.original_environment(environment)
    environment_hash = cutover.sha256_file(environment)
    if output.exists() or output.is_symlink():
        raise cutover.CutoverError("Encrypted legacy output must be a new path")
    age = artifacts.backup_module().Age(age_binary)
    with tempfile.TemporaryDirectory(prefix="cl-seal-legacy-") as temporary:
        archive = Path(temporary) / "legacy.tar"
        with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as target:
            files = [(p, "bundle/" + p.relative_to(bundle).as_posix())
                     for p in sorted(bundle.rglob("*")) if p.is_file()]
            files.append((environment, "original-environment.json"))
            total = 0
            for source, name in files:
                if source.is_symlink():
                    raise cutover.CutoverError("Legacy archive refuses symlink files")
                info = target.gettarinfo(str(source), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                total += info.size
                if total > artifacts.MAX_ARCHIVE_BYTES or len(files) > artifacts.MAX_ENTRIES:
                    raise cutover.CutoverError("Legacy checkpoint exceeds the reviewed archive limits")
                with source.open("rb") as stream:
                    target.addfile(info, stream)
        archive.chmod(0o600)
        # Recheck the frozen source after packaging; the maintenance freeze
        # remains necessary to prevent later writes outside this tool.
        if cutover.verify_bundle(bundle)[0] != manifest:
            raise cutover.CutoverError("Legacy bundle changed while sealing")
        if (cutover.sha256_file(environment) != environment_hash
                or rollback.original_environment(environment) != original):
            raise cutover.CutoverError("Original environment changed while sealing")
        inspected = Path(temporary) / "inspected"
        artifacts.extract_private_tar(archive, inspected)
        if (cutover.verify_bundle(inspected / "bundle")[0] != manifest
                or cutover.sha256_file(inspected / "original-environment.json") != environment_hash
                or rollback.original_environment(inspected / "original-environment.json") != original):
            raise cutover.CutoverError("Sealed archive differs from the frozen checkpoint")
        age.encrypt(archive, output, recipient)
    return {"kind": "cl-encrypted-legacy-checkpoint", "legacy_commit": rollback.EXPECTED_OLD_COMMIT,
            "sha256": cutover.sha256_file(output), "size_bytes": output.stat().st_size,
            "bundle_manifest_sha256": cutover.sha256_file(bundle / "manifest.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--original-environment", type=Path, required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--output-new", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(seal(args.bundle, args.original_environment, args.recipient, args.output_new)))
        return 0
    except Exception as error:
        print(f"STOP: {type(error).__name__}; legacy artifact was not approved for recovery", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
