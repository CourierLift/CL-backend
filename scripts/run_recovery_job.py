#!/usr/bin/env python3
"""Run one approved recovery from Render's snapshotted temporary environment.

Use only as a one-off job from the reviewed backup image. The operator must
freeze writers and approve fresh target resources first. This script never
creates accounts, credentials, buckets, databases, services or deployments.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import postgres_cutover as cutover
from scripts import recovery_artifacts as artifacts
from scripts import recovery_inputs
from scripts.start_pilot import check_release_identity

ROOT = Path(__file__).resolve().parents[1]


def command(job: dict, config: Path, identity: Path, complete: Path) -> list[str]:
    required = {"checkpoint_id", "complete_reference", "target_host", "target_database",
                "photo_bucket", "photo_owner", "release_sha", "writers_stopped", "allow_writes",
                "acknowledge_disaster_checkpoint_rpo"}
    if (set(job) != required or job["writers_stopped"] is not True or job["allow_writes"] is not True
            or type(job["acknowledge_disaster_checkpoint_rpo"]) is not bool
            or not isinstance(job["complete_reference"], dict)
            or not isinstance(job["release_sha"], str)
            or not re.fullmatch(r"[a-f0-9]{40}", job["release_sha"])
            or job["release_sha"] != os.environ.get("CL_RELEASE_SHA")):
        raise cutover.CutoverError("Recovery job choices must exactly match the approved artifact and maintenance decision")
    for key in ("checkpoint_id", "target_host", "target_database", "photo_bucket", "photo_owner"):
        if not isinstance(job[key], str) or not job[key] or job[key].startswith("-") or len(job[key]) > 255:
            raise cutover.CutoverError("Invalid recovery job resource identifier")
    # Paths and resource identifiers are arguments, never a shell program.
    args = [sys.executable, str(ROOT / "scripts/restore_durable_checkpoint.py"),
            "--config", str(config), "--identity", str(identity), "--complete-reference", str(complete),
            "--writers-stopped", "--allow-writes"]
    for key in ("checkpoint_id", "target_host", "target_database", "photo_bucket", "photo_owner", "release_sha"):
        args.extend(["--" + key.replace("_", "-"), job[key]])
    if job["acknowledge_disaster_checkpoint_rpo"]:
        args.append("--acknowledge-disaster-checkpoint-rpo")
    return args


def main() -> int:
    try:
        check_release_identity()
        with recovery_inputs.materialized_inputs({
            "config.json": (None, "CL_BACKUP_CONFIG_JSON", 64 * 1024),
            "job.json": (None, "CL_RECOVERY_JOB_JSON", 64 * 1024),
            "identity.agekey": (None, "CL_RECOVERY_AGE_IDENTITY", 8192),
        }) as inputs:
            if not all(inputs.values()):
                raise cutover.CutoverError("Approved recovery job, configuration and protected identity are required")
            job = artifacts.private_json(inputs["job.json"])
            reference = inputs["job.json"].parent / "complete-reference.json"
            args = command(job, inputs["config.json"], inputs["identity.agekey"], reference)
            with os.fdopen(os.open(reference, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                output.write(cutover.json_bytes(job["complete_reference"]))
            # Preserve restore-only credentials for this child; consumed secret
            # input values are already absent from its environment. Do not
            # capture or print commands containing credentials (none are used).
            result = subprocess.run(args, cwd=ROOT, env=dict(os.environ), timeout=7200)
            return result.returncode
    except Exception as error:
        print(f"STOP: {type(error).__name__}; recovery job failed, keep targets quarantined", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
