#!/usr/bin/env python3
"""Single native-Render start boundary: verify recovery BEFORE app import.

Default serve mode never restores or migrates. Recovery modes require a pinned
reference and maintenance acknowledgement. Both refuse mismatched data. After
PG receives new writes, legacy-checkpoint cannot pass its full-table comparison.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import postgres_cutover as cutover
from scripts import recovery_artifacts as artifacts
from scripts import recovery_inputs
from scripts import prepare_release_runtime as release_runtime
from scripts import start_sqlite_rollback as rollback

ROOT = Path(__file__).resolve().parents[1]


def check_release_identity() -> None:
    actual = os.environ.get("RENDER_GIT_COMMIT")
    expected = os.environ.get("CL_RELEASE_SHA")
    if actual and (not expected or not re.fullmatch(r"[a-f0-9]{40}", expected) or expected != actual):
        raise cutover.CutoverError("Render artifact does not match the explicitly approved release SHA")


def legacy_checkpoint(reference: dict, client, identity: Path, runtime: Path, state_parent: Path) -> tuple[dict, Path, Path]:
    if (reference.get("format_version") != 1 or reference.get("kind") != "cl-legacy-recovery-reference"
            or reference.get("legacy_commit") != rollback.EXPECTED_OLD_COMMIT
            or reference.get("phase") != "before-new-postgres-writes"):
        raise cutover.CutoverError("This reference cannot authorize legacy SQLite recovery")
    # The original code and its interpreter were assembled into this same
    # candidate artifact during build, not fetched from a moving runtime branch.
    checkout = runtime / "old-checkout"
    python = runtime / "old-python/bin/python"
    if not python.is_file():
        raise cutover.CutoverError("Reviewed legacy runtime was not built into this artifact")
    release_runtime.verify_runtime(runtime)
    with tempfile.TemporaryDirectory(prefix="cl-recovery-fetch-", dir=state_parent) as directory:
        temporary = Path(directory)
        cipher, archive, extracted = temporary / "legacy.age", temporary / "legacy.tar", temporary / "extracted"
        artifacts.download(client, reference["artifact"], cipher)
        artifacts.backup_module().Age(str(runtime / "bin/age")).decrypt(cipher, archive, identity)
        artifacts.extract_private_tar(archive, extracted)
        bundle = extracted / "bundle"
        cutover.verify_bundle(bundle)
        target = reference["postgres_target"]
        url = os.environ.get("CL_RECOVERY_VERIFY_DATABASE_URL", "")
        engine = cutover.target_engine(url, target["host"], target["database"])
        try:
            if (engine.url.port or 5432) != target["port"]:
                raise cutover.CutoverError("Legacy recovery comparison port differs from the approved target")
            # Mandatory read-only comparison. New users, quotes, orders, proof
            # metadata, status/history or reward writes close this path.
            cutover.verify_target(engine, bundle)
        finally:
            engine.dispose()
        environment, state = rollback.prepare_rollback(
            checkout, bundle, extracted / "original-environment.json", state_parent,
        )
    # prepare_rollback clears ALL candidate CL_/AWS_/PG settings. Only the
    # protected original environment plus restored absolute SQLite/photo paths
    # is supplied to the old process.
    return environment, checkout, python


def recovered_postgres(receipt: dict, client) -> dict[str, str]:
    if (receipt.get("format_version") != 1 or receipt.get("kind") != "cl-pilot-durable-recovery"
            or receipt.get("release_sha") != os.environ.get("CL_RELEASE_SHA")):
        raise cutover.CutoverError("Durable recovery receipt does not match this approved compatible release")
    target = receipt["database_target"]
    source = receipt["source_database"]
    if target["database"] == source:
        raise cutover.CutoverError("Recovery must not overwrite the original source database")
    url = os.environ.get("CL_DATABASE_URL", "")
    engine = cutover.target_engine(url, target["host"], target["database"])
    try:
        if (engine.url.port or 5432) != target["port"]:
            raise cutover.CutoverError("Restored database port differs")
        artifacts.backup_module().verify_database(engine, receipt)
    finally:
        engine.dispose()
    photos = receipt["photo_target"]
    if (os.environ.get("CL_OBJECT_STORAGE_BACKEND") != "s3"
            or os.environ.get("CL_S3_BUCKET") != photos["bucket"]
            or os.environ.get("CL_S3_REGION") != photos["region"]
            or os.environ.get("CL_S3_ENDPOINT_URL")):
        raise cutover.CutoverError("Application photo configuration differs from the verified recovery target")
    artifacts.verify_durable_photos(client, receipt, current_required=True)
    return artifacts.application_environment()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("serve", "legacy-checkpoint", "recovered-postgres"), default="serve")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".pilot-recovery")
    parser.add_argument("--state-parent", type=Path, default=Path("/tmp"))
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--no-new-postgres-writes-confirmed", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535:
            raise cutover.CutoverError("Invalid web port")
        check_release_identity()
        checkout, python = ROOT, Path(sys.executable)
        if args.mode == "serve":
            if not os.environ.get("CL_DATABASE_URL", "").startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
                raise cutover.CutoverError("Normal pilot start requires the compatible PostgreSQL runtime")
            environment = artifacts.application_environment()
        else:
            if not args.maintenance_confirmed:
                raise cutover.CutoverError("Recovery requires the reviewed reference and active maintenance")
            with recovery_inputs.materialized_inputs({
                "reference.json": (args.reference, "CL_RECOVERY_REFERENCE_JSON", 64 * 1024),
                "identity.agekey": (args.identity, "CL_RECOVERY_AGE_IDENTITY", 8192),
            }) as inputs:
                if not inputs["reference.json"]:
                    raise cutover.CutoverError("A pinned recovery reference is required")
                reference = artifacts.private_json(inputs["reference.json"])
                client = artifacts.recovery_client()
                if args.mode == "legacy-checkpoint":
                    if not args.no_new_postgres_writes_confirmed or not inputs["identity.agekey"]:
                        raise cutover.CutoverError("Legacy checkpoint requires its protected identity and a pre-write recovery decision")
                    environment, checkout, python = legacy_checkpoint(reference, client, inputs["identity.agekey"], args.runtime, args.state_parent)
                else:
                    with tempfile.TemporaryDirectory(prefix="cl-recovery-receipt-") as temporary:
                        local = Path(temporary) / "receipt.json"
                        artifacts.download(client, reference, local)
                        environment = recovered_postgres(artifacts.private_json(local), client)
        environment["WEB_CONCURRENCY"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        print(json.dumps({"startup_verified": True, "mode": args.mode, "app_imported": False}), flush=True)
        if args.prepare_only:
            return 0
        os.chdir(checkout.resolve())
        os.execve(str(python), [str(python), "-m", "uvicorn", "backend.main:app",
                              "--host", "0.0.0.0", "--port", str(args.port), "--workers", "1"], environment)
        return 0
    except Exception as error:
        print(f"STOP: {type(error).__name__}; recovery/start verification failed before application import", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
