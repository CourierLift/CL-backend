#!/usr/bin/env python3
"""Restore a selected checkpoint to fresh PG/S3 targets and publish a start gate.

Run as an approved one-off job using the backup cron's reviewed Docker artifact.
No service is configured or deployed by this script. The compatible web artifact
must verify the pinned receipt with start_pilot.py before importing the app.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import postgres_cutover as cutover
from scripts import recovery_artifacts as artifacts


def empty_photo_target(client, bucket: str, owner: str, excluded: set[str]) -> None:
    if bucket in excluded or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise cutover.CutoverError("Recovery photo bucket must be distinct from source and backup buckets")
    if not re.fullmatch(r"\d{12}", owner):
        raise cutover.CutoverError("Explicit recovery bucket owner is required")
    if client.get_bucket_location(Bucket=bucket, ExpectedBucketOwner=owner).get("LocationConstraint") != "us-west-2":
        raise cutover.CutoverError("Recovery photo bucket must actually be in the approved Oregon region")
    if client.get_bucket_versioning(Bucket=bucket, ExpectedBucketOwner=owner).get("Status") != "Enabled":
        raise cutover.CutoverError("Recovery photo bucket versioning must be Enabled")
    public = client.get_public_access_block(Bucket=bucket, ExpectedBucketOwner=owner)["PublicAccessBlockConfiguration"]
    if not all(public.get(name) is True for name in (
        "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"
    )):
        raise cutover.CutoverError("All recovery photo public-access blocks must be enabled")
    page = client.list_object_versions(Bucket=bucket, ExpectedBucketOwner=owner, MaxKeys=1)
    if page.get("Versions") or page.get("DeleteMarkers") or page.get("IsTruncated"):
        raise cutover.CutoverError("Recovery photo bucket is not empty; no overwrite or deletion is permitted")


def publish_recovery(config, checkpoint_id: str, complete_reference: dict, backup_store, client,
                     age, postgres, target_engine, identity: Path, *,
                     photo_bucket: str, photo_owner: str, release_sha: str,
                     source_engine=None, acknowledge_disaster_rpo: bool = False) -> dict:
    backup = artifacts.backup_module()
    if not re.fullmatch(r"[a-f0-9]{40}", release_sha):
        raise cutover.CutoverError("Exact approved compatible release SHA is required")
    if source_engine is None and not acknowledge_disaster_rpo:
        raise cutover.CutoverError("Fresh-source recovery must compare the frozen current database")
    if source_engine is not None and (source_engine.url.host != config.source_host
                                      or source_engine.url.database != config.source_database
                                      or (source_engine.url.port or 5432) != config.source_port):
        raise cutover.CutoverError("Recovery comparison source differs from the current approved database")
    empty_photo_target(client, photo_bucket, photo_owner, {config.source_bucket, config.backup_bucket})
    with tempfile.TemporaryDirectory(prefix="cl-durable-restore-") as directory:
        temporary = Path(directory)
        receipt = backup.restore(
            config, checkpoint_id, backup_store, age, postgres, target_engine,
            identity, temporary / "photos", temporary / "local-receipt.json",
            complete_reference=complete_reference,
        )
        # A planned rollback must retain every committed write in the frozen
        # source. If a stray writer committed since the checkpoint, STOP and
        # take another checkpoint; never make this stale recovery live.
        if source_engine is not None:
            backup.verify_database(source_engine, receipt)
        objects = []
        for proof in receipt["proofs"]:
            source = temporary / "photos" / cutover.safe_key(proof["storage_key"])
            with source.open("rb") as stream:
                uploaded = client.put_object(
                    Bucket=photo_bucket, ExpectedBucketOwner=photo_owner, Key=proof["storage_key"],
                    Body=stream, ContentLength=proof["size_bytes"], ContentType=proof["content_type"],
                    ServerSideEncryption="AES256", IfNoneMatch="*",
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(proof["sha256"])).decode(),
                )
            objects.append({"key": proof["storage_key"], "version_id": uploaded.get("VersionId"),
                            "size_bytes": proof["size_bytes"], "sha256": proof["sha256"]})
        receipt.update({
            "kind": "cl-pilot-durable-recovery", "release_sha": release_sha,
            "source_database": config.source_database,
            "recovery_policy": "fresh-frozen-source" if source_engine is not None else "explicit-disaster-checkpoint",
            "photo_target": {"bucket": photo_bucket, "owner": photo_owner,
                             "region": config.region, "objects": objects},
            "durable_verified_at": datetime.now(timezone.utc).isoformat(),
        })
        receipt["photos"].pop("path", None)
        artifacts.verify_durable_photos(client, receipt, current_required=True)
        backup.verify_database(target_engine, receipt)
        if source_engine is not None:
            backup.verify_database(source_engine, receipt)
        path = temporary / "durable-receipt.json"
        backup.private_write(path, backup.encoded(receipt))
        # The recovery operator may write only this additional receipt prefix;
        # the daily backup identity has no permission to publish a start gate.
        publisher = backup.Store(client, config.backup_bucket, config.backup_owner, config.prefix)
        key = f"{config.prefix}/recovery-receipts/{checkpoint_id}/{target_engine.url.database}.json"
        reference = publisher.upload(path, key)
        return {"bucket": config.backup_bucket, "owner": config.backup_owner,
                "region": config.region, **reference}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--complete-reference", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--photo-bucket", required=True)
    parser.add_argument("--photo-owner", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--writers-stopped", action="store_true", required=True)
    parser.add_argument("--allow-writes", action="store_true", required=True)
    parser.add_argument("--acknowledge-disaster-checkpoint-rpo", action="store_true")
    args = parser.parse_args()
    engines = []
    try:
        backup = artifacts.backup_module()
        config = backup.load_config(args.config)
        _reader, store = backup.clients(config, "recovery")
        writer = artifacts.recovery_client(config.region)
        target = cutover.target_engine(os.environ.get("CL_BACKUP_RESTORE_DATABASE_URL", ""),
                                      args.target_host, args.target_database)
        engines.append(target)
        source = None
        if not args.acknowledge_disaster_checkpoint_rpo:
            source = cutover.target_engine(os.environ.get("CL_RECOVERY_SOURCE_DATABASE_URL", ""),
                                          config.source_host, config.source_database)
            engines.append(source)
        reference = publish_recovery(
            config, args.checkpoint_id, artifacts.private_json(args.complete_reference), store, writer,
            backup.Age(), backup.Postgres(), target, args.identity,
            photo_bucket=args.photo_bucket, photo_owner=args.photo_owner, release_sha=args.release_sha,
            source_engine=source, acknowledge_disaster_rpo=args.acknowledge_disaster_checkpoint_rpo,
        )
        print(json.dumps({"durable_recovery_verified": True, "application_started": False,
                          "startup_reference": reference}, sort_keys=True))
        return 0
    except Exception as error:
        print(f"STOP: {type(error).__name__}; durable restore incomplete, keep targets quarantined", file=sys.stderr)
        return 1
    finally:
        for engine in engines:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
