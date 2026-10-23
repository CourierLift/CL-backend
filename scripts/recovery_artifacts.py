"""Private, immutable recovery artifacts; deliberately independent of the app."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
from typing import Any

from scripts import postgres_cutover as cutover

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 2 * 1024 ** 3
MAX_ENTRIES = 100_000


def backup_module():
    name = "cl_reviewed_pilot_backup"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "ops/pilot-backup/pilot_backup.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def private_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise cutover.CutoverError("Recovery reference must be a private regular file")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise cutover.CutoverError("Recovery reference is too large")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise cutover.CutoverError("Invalid recovery reference")
    return value


def object_reference(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "bucket", "owner", "region", "key", "version_id", "sha256", "size_bytes"
    }:
        raise cutover.CutoverError("Invalid pinned recovery object reference")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value["bucket"]):
        raise cutover.CutoverError("A concrete recovery bucket is required")
    if not re.fullmatch(r"\d{12}", value["owner"]) or value["region"] != "us-west-2":
        raise cutover.CutoverError("An approved AWS owner and Oregon region are required")
    if str(cutover.safe_key(value["key"])) != value["key"]:
        raise cutover.CutoverError("Unsafe recovery object key")
    if not isinstance(value["version_id"], str) or value["version_id"] in {"", "null"}:
        raise cutover.CutoverError("An immutable recovery VersionId is required")
    if not re.fullmatch(r"[a-f0-9]{64}", value["sha256"]):
        raise cutover.CutoverError("An independently recorded recovery SHA256 is required")
    if type(value["size_bytes"]) is not int or not 0 < value["size_bytes"] <= MAX_ARCHIVE_BYTES:
        raise cutover.CutoverError("Recovery artifact exceeds its reviewed size bound")
    return value


def recovery_client(region: str = "us-west-2"):
    import boto3
    from botocore.config import Config

    # Never silently fall back to the application's AWS identity or an SDK
    # credentials file. The recovery identity is configured only when approved.
    access = os.environ.get("CL_RECOVERY_AWS_ACCESS_KEY_ID")
    secret = os.environ.get("CL_RECOVERY_AWS_SECRET_ACCESS_KEY")
    if not access or not secret:
        raise cutover.CutoverError("Explicit recovery-only S3 credentials are required")
    return boto3.client(
        "s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
        aws_access_key_id=access, aws_secret_access_key=secret,
        aws_session_token=os.environ.get("CL_RECOVERY_AWS_SESSION_TOKEN"),
        config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
    )


def download(client: Any, reference: dict, destination: Path) -> None:
    ref = object_reference(reference)
    if destination.exists() or destination.is_symlink():
        raise cutover.CutoverError("Recovery download destination already exists")
    response = client.get_object(
        Bucket=ref["bucket"], ExpectedBucketOwner=ref["owner"],
        Key=ref["key"], VersionId=ref["version_id"],
    )
    body = response["Body"]
    try:
        if response.get("VersionId") != ref["version_id"] or response.get("ContentLength") != ref["size_bytes"]:
            raise cutover.CutoverError("Recovery object version or size differs")
        count, digest = 0, hashlib.sha256()
        with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            while chunk := body.read(1024 * 1024):
                count += len(chunk)
                if count > ref["size_bytes"]:
                    raise cutover.CutoverError("Recovery download exceeds its pinned size")
                digest.update(chunk)
                output.write(chunk)
        if count != ref["size_bytes"] or digest.hexdigest() != ref["sha256"]:
            raise cutover.CutoverError("Recovery object checksum differs from the approved reference")
    finally:
        body.close()


def extract_private_tar(archive: Path, destination: Path) -> None:
    """No extractall: reject links, devices, duplicates, traversal and bombs."""
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise cutover.CutoverError("Decrypted recovery archive is too large")
    destination.mkdir(mode=0o700, exist_ok=False)
    seen, total = set(), 0
    with tarfile.open(archive, "r:") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts
                    or str(path) != member.name or member.name in seen
                    or not member.name.startswith(("bundle/", "original-environment.json"))
                    or (path.parts[0] != "bundle" and member.name != "original-environment.json")):
                raise cutover.CutoverError("Recovery archive contains an unsafe or unexpected entry")
            seen.add(member.name)
            total += member.size
            if member.size < 0 or total > MAX_ARCHIVE_BYTES or len(seen) > MAX_ENTRIES:
                raise cutover.CutoverError("Recovery archive exceeds its reviewed extraction limits")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with source.extractfile(member) as input_file, os.fdopen(
                os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as output:
                remaining = member.size
                while remaining:
                    chunk = input_file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise cutover.CutoverError("Recovery archive is truncated")
                    output.write(chunk)
                    remaining -= len(chunk)
    if "bundle/manifest.json" not in seen or "original-environment.json" not in seen:
        raise cutover.CutoverError("Recovery archive is missing the bundle or original environment")


def application_environment() -> dict[str, str]:
    """Recovery credentials and libpq overrides never reach the web process."""
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith(("CL_RECOVERY_", "CL_BACKUP_", "CL_RETENTION_", "CL_CUTOVER_", "PG"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "DATABASE_URL"}
    }


def verify_durable_photos(client: Any, receipt: dict, *, current_required: bool) -> None:
    backup = backup_module()
    photo_target = receipt["photo_target"]
    if (set(photo_target) != {"bucket", "owner", "region", "objects"}
            or not re.fullmatch(r"\d{12}", photo_target["owner"])
            or photo_target["region"] != "us-west-2"):
        raise cutover.CutoverError("Invalid durable recovery photo target")
    proofs = receipt["proofs"]
    backup.validate_proofs(proofs)
    by_key = {item["key"]: item for item in photo_target["objects"]}
    if len(by_key) != len(photo_target["objects"]) or set(by_key) != {p["storage_key"] for p in proofs}:
        raise cutover.CutoverError("Recovery photos do not exactly match preserved proof relationships")
    for proof in proofs:
        ref = object_reference({
            "bucket": photo_target["bucket"], "owner": photo_target["owner"],
            "region": photo_target["region"], **by_key[proof["storage_key"]],
        })
        if ref["sha256"] != proof["sha256"] or ref["size_bytes"] != proof["size_bytes"]:
            raise cutover.CutoverError("Recovery photo reference differs from database metadata")
        if current_required:
            current = client.head_object(Bucket=ref["bucket"], Key=ref["key"], ExpectedBucketOwner=ref["owner"])
            if current.get("VersionId") != ref["version_id"]:
                raise cutover.CutoverError("Current photo version differs from the verified recovered version")
        response = client.get_object(Bucket=ref["bucket"], Key=ref["key"],
                                     VersionId=ref["version_id"], ExpectedBucketOwner=ref["owner"])
        body = response["Body"]
        try:
            data = body.read(proof["size_bytes"] + 1)
        finally:
            body.close()
        if (response.get("VersionId") != ref["version_id"] or len(data) != proof["size_bytes"]
                or hashlib.sha256(data).hexdigest() != proof["sha256"]):
            raise cutover.CutoverError("Durable recovery photo bytes do not match preserved history")
