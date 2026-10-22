#!/usr/bin/env python3
"""Prepare/verify version-pinned S3 proof checkpoints; never deletes cloud data.

No AWS calls occur on import. Upload is a separate, explicitly gated operation.
All examples and production invocation require the combined release approval.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any


MAX_PROOF_BYTES = 10 * 1024 * 1024
CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
CHECKPOINT_KIND = "courierlifts-s3-proof-checkpoint"


class ProofStorageError(ValueError):
    """A preservation/recovery prerequisite was not met."""


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_records(records: Any, *, pinned: bool = False) -> list[dict]:
    if not isinstance(records, list):
        raise ProofStorageError("Manifest must contain a proofs array")
    keys, ids, orders = set(), set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise ProofStorageError("Invalid proof metadata")
        key = record.get("storage_key")
        if (not isinstance(key, str) or not key.startswith("proofs/") or "\\" in key
                or "\x00" in key or any(part in {"", ".", ".."} for part in key.split("/"))
                or PurePosixPath(key).is_absolute()):
            raise ProofStorageError("Proof key must be a safe relative path under proofs/")
        for field in ("id", "order_id", "uploaded_by_user_id", "size_bytes"):
            if type(record.get(field)) is not int or record[field] <= 0:
                raise ProofStorageError(f"Invalid proof {field}")
        if record["size_bytes"] > MAX_PROOF_BYTES:
            raise ProofStorageError("Proof exceeds the reviewed 10 MiB pilot limit")
        if record.get("content_type") not in CONTENT_TYPES or not _valid_hash(record.get("sha256")):
            raise ProofStorageError("Invalid proof media type or SHA256")
        if key in keys or record["id"] in ids or record["order_id"] in orders:
            raise ProofStorageError("Duplicate proof identity in manifest")
        if pinned and (not isinstance(record.get("object_version_id"), str)
                       or record["object_version_id"] in {"", "null"}):
            raise ProofStorageError("Recovery requires an immutable S3 VersionId")
        keys.add(key)
        ids.add(record["id"])
        orders.add(record["order_id"])
    return records


def _source_records(source: dict) -> list[dict]:
    if source.get("format_version") != 1:
        raise ProofStorageError("Unsupported source manifest format")
    if not _valid_hash(source.get("database_sha256", source.get("sqlite_sha256"))):
        raise ProofStorageError("Source manifest must bind an exact database backup SHA256")
    return _validate_records(source.get("proofs"))


def _validate_target(bucket: str, expected_account_id: str, region: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ProofStorageError("An approved concrete bucket name is required")
    if not re.fullmatch(r"\d{12}", expected_account_id):
        raise ProofStorageError("An approved 12-digit bucket owner account is required")
    if region != "us-west-2":
        raise ProofStorageError("This reviewed plan targets us-west-2; review another region separately")


def _versioning(client: Any, bucket: str, owner: str) -> None:
    result = client.get_bucket_versioning(Bucket=bucket, ExpectedBucketOwner=owner)
    if result.get("Status") != "Enabled":
        raise ProofStorageError("Bucket versioning must be Enabled before a checkpoint or import")


def _get_verified(client: Any, bucket: str, owner: str, record: dict,
                  version_id: str | None = None) -> tuple[bytes, str]:
    request = {"Bucket": bucket, "Key": record["storage_key"], "ExpectedBucketOwner": owner}
    if version_id is not None:
        request["VersionId"] = version_id
    response = client.get_object(**request)
    body = response["Body"]
    try:
        data = body.read(record["size_bytes"] + 1)
    finally:
        body.close()
    returned_version = response.get("VersionId")
    if not isinstance(returned_version, str) or returned_version in {"", "null"}:
        raise ProofStorageError("Object has no immutable S3 VersionId")
    if version_id is not None and returned_version != version_id:
        raise ProofStorageError("S3 returned a different object version")
    if response.get("ServerSideEncryption") != "AES256":
        raise ProofStorageError("Object must use the reviewed SSE-S3 encryption setting")
    if response.get("ContentType") != record["content_type"]:
        raise ProofStorageError("Object content type differs from database metadata")
    if (response.get("ContentLength") != record["size_bytes"]
            or len(data) != record["size_bytes"]
            or sha256(data).hexdigest() != record["sha256"]):
        raise ProofStorageError("Object bytes differ from the frozen database proof metadata")
    return data, returned_version


def _checkpoint(source: dict, bucket: str, owner: str, region: str, records: list[dict]) -> dict:
    return {
        "format_version": 1,
        "kind": CHECKPOINT_KIND,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_canonical_sha256": _digest(source),
        "source_database_sha256": source.get("database_sha256", source.get("sqlite_sha256")),
        "bucket": bucket,
        "bucket_owner_account_id": owner,
        "region": region,
        "proof_count": len(records),
        "proofs": records,
    }


def capture_current(client: Any, source: dict, *, bucket: str, expected_account_id: str,
                    region: str = "us-west-2") -> dict:
    """Read current objects, verify DB hashes, and pin returned immutable versions."""
    records = _source_records(source)
    _validate_target(bucket, expected_account_id, region)
    _versioning(client, bucket, expected_account_id)
    pinned = []
    for record in records:
        _, version = _get_verified(client, bucket, expected_account_id, record)
        pinned.append({**record, "object_version_id": version})
    return _checkpoint(source, bucket, expected_account_id, region, pinned)


def _local_proof(root: Path, record: dict) -> bytes:
    if root.is_symlink() or not root.is_dir():
        raise ProofStorageError("Proof bundle root must be a real directory")
    target = root
    for part in record["storage_key"].split("/"):
        target = target / part
        if target.is_symlink():
            raise ProofStorageError("Symlinks are forbidden in a proof bundle")
    with target.open("rb") as stream:
        data = stream.read(record["size_bytes"] + 1)
    if len(data) != record["size_bytes"] or sha256(data).hexdigest() != record["sha256"]:
        raise ProofStorageError("Local proof bytes differ from the frozen database metadata")
    return data


def upload_bundle(client: Any, source: dict, proof_root: Path, *, bucket: str,
                  expected_account_id: str, region: str = "us-west-2",
                  allow_object_writes: bool = False) -> dict:
    """Copy legacy objects without overwriting existing current keys.

    The flag records an operator's already-obtained release approval; it does not
    obtain approval. All local proof files are checked before the first AWS call.
    Unreferenced local files remain in the encrypted backup bundle, not S3 proofs/.
    """
    if not allow_object_writes:
        raise ProofStorageError("Object upload requires explicit approved --allow-object-writes")
    records = _source_records(source)
    _validate_target(bucket, expected_account_id, region)
    for record in records:
        _local_proof(proof_root, record)
    _versioning(client, bucket, expected_account_id)
    pinned = []
    for record in records:
        data = _local_proof(proof_root, record)
        try:
            result = client.put_object(
                Bucket=bucket, Key=record["storage_key"], ExpectedBucketOwner=expected_account_id,
                Body=data, ContentType=record["content_type"], ServerSideEncryption="AES256",
                IfNoneMatch="*",
            )
            version = result.get("VersionId")
            if not isinstance(version, str) or version in {"", "null"}:
                raise ProofStorageError("Uploaded object returned no immutable S3 VersionId")
        except Exception as error:
            response = getattr(error, "response", {})
            if response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                raise
            # A retry is safe only if the already-present object has exact bytes.
            version = None
        _, verified_version = _get_verified(client, bucket, expected_account_id, record, version)
        pinned.append({**record, "object_version_id": verified_version})
    return _checkpoint(source, bucket, expected_account_id, region, pinned)


def _recovery_records(checkpoint: dict, expected_account_id: str) -> list[dict]:
    if checkpoint.get("format_version") != 1 or checkpoint.get("kind") != CHECKPOINT_KIND:
        raise ProofStorageError("Unsupported recovery checkpoint")
    _validate_target(checkpoint.get("bucket", ""), expected_account_id, checkpoint.get("region", ""))
    if checkpoint.get("bucket_owner_account_id") != expected_account_id:
        raise ProofStorageError("Checkpoint belongs to a different AWS account")
    if not _valid_hash(checkpoint.get("source_database_sha256")):
        raise ProofStorageError("Checkpoint must bind an exact database backup SHA256")
    records = _validate_records(checkpoint.get("proofs"), pinned=True)
    if checkpoint.get("proof_count") != len(records):
        raise ProofStorageError("Checkpoint proof count does not match its records")
    return records


def verify_pinned(client: Any, checkpoint: dict, *, expected_account_id: str) -> int:
    """Verify all original versions, even after later current-object changes."""
    records = _recovery_records(checkpoint, expected_account_id)
    for record in records:
        _get_verified(client, checkpoint["bucket"], expected_account_id, record,
                      record["object_version_id"])
    return len(records)


def restore_local(client: Any, checkpoint: dict, destination: Path, *, expected_account_id: str) -> int:
    """Recover pinned objects into a new local directory; no cloud writes/deletes."""
    records = _recovery_records(checkpoint, expected_account_id)
    if os.path.lexists(destination):
        raise ProofStorageError("Recovery destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".cl-proof-restore-", dir=destination.parent))
    reserved = False
    try:
        for record in records:
            data, _ = _get_verified(client, checkpoint["bucket"], expected_account_id, record,
                                    record["object_version_id"])
            target = stage / record["storage_key"]
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # Reserve exclusively so an existing recovery destination is never erased.
        destination.mkdir(mode=0o700)
        reserved = True
        stage.replace(destination)
        reserved = False
        return len(records)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if reserved:
            destination.rmdir()


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ProofStorageError("Manifest must be a JSON object")
    return result


def _write_new(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("capture", "upload", "verify", "restore-local"):
        command = commands.add_parser(operation)
        command.add_argument("--expected-account-id", required=True)
        if operation in {"capture", "upload"}:
            command.add_argument("--bucket", required=True)
            command.add_argument("--region", default="us-west-2")
            command.add_argument("--output", type=Path, required=True)
            if operation == "capture":
                command.add_argument("--source-manifest", type=Path, required=True)
            else:
                command.add_argument("--bundle", type=Path, required=True)
                command.add_argument("--allow-object-writes", action="store_true")
        else:
            command.add_argument("--manifest", type=Path, required=True)
            if operation == "restore-local":
                command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "upload" and not args.allow_object_writes:
            raise ProofStorageError("Obtain release approval before using --allow-object-writes")
        if args.operation in {"capture", "upload"}:
            _validate_target(args.bucket, args.expected_account_id, args.region)
            if os.path.lexists(args.output):
                raise ProofStorageError("Checkpoint output must not already exist")
            source = _load(args.source_manifest if args.operation == "capture" else args.bundle / "manifest.json")
            _source_records(source)
            region = args.region
        else:
            checkpoint = _load(args.manifest)
            _recovery_records(checkpoint, args.expected_account_id)
            region = checkpoint["region"]
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", region_name=region, config=Config(retries={"mode": "standard", "max_attempts": 3}))
        if args.operation == "capture":
            result = capture_current(client, source, bucket=args.bucket,
                                     expected_account_id=args.expected_account_id, region=region)
            _write_new(args.output, result)
            print(json.dumps({"operation": "capture", "verified_proofs": result["proof_count"]}))
        elif args.operation == "upload":
            result = upload_bundle(client, source, args.bundle / "proof-files", bucket=args.bucket,
                                   expected_account_id=args.expected_account_id, region=region,
                                   allow_object_writes=args.allow_object_writes)
            _write_new(args.output, result)
            print(json.dumps({"operation": "upload", "verified_proofs": result["proof_count"]}))
        elif args.operation == "verify":
            count = verify_pinned(client, checkpoint, expected_account_id=args.expected_account_id)
            print(json.dumps({"operation": "verify", "verified_proofs": count}))
        else:
            count = restore_local(client, checkpoint, args.destination, expected_account_id=args.expected_account_id)
            print(json.dumps({"operation": "restore-local", "verified_proofs": count}))
        return 0
    except ProofStorageError as error:
        parser.exit(2, f"Preservation check failed: {error}\n")
    except Exception as error:
        # Provider exception text can contain environment/account detail; keep logs bounded.
        parser.exit(2, f"Operation failed ({type(error).__name__}); preserve existing backup/object versions and investigate securely.\n")


if __name__ == "__main__":
    raise SystemExit(main())
