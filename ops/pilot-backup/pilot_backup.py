#!/usr/bin/env python3
"""Encrypted incremental pilot checkpoints. No cloud calls occur on import.

run writes immutable encrypted artifacts and a completion marker last. retention
uses a separate identity and never deletes photos. restore refuses occupied
targets and emits a verified receipt; it never starts an application. See
docs/AUTOMATED_BACKUPS.md for approval and operational acceptance gates.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterator
import uuid

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import postgres_cutover as cutover


KIND = "cl-pilot-encrypted-checkpoint"
MANIFEST_KIND = "cl-pilot-snapshot-manifest"
RECEIPT_KIND = "cl-pilot-verified-restore"
MAX_PROOF = 10 * 1024 * 1024
MAX_JSON = 64 * 1024 * 1024
MAX_OBJECT = 5 * 1024 ** 3  # Deliberately no multipart ETag/checksum ambiguity.
ID_PATTERN = r"\d{8}T\d{12}Z-[a-f0-9]{16}"
HASH_PATTERN = r"[a-f0-9]{64}"


class BackupError(RuntimeError):
    """Safe diagnostic that does not contain credentials or customer values."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: Any) -> bytes:
    return cutover.json_bytes(value)


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise BackupError("Checkpoint timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def private_write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)


def new_path(path: Path) -> Path:
    """Refuse existing destinations and symlink ancestors before any restore."""
    path = path.absolute()
    if os.path.lexists(path):
        raise BackupError("Recovery output must be a new path")
    for parent in path.parents:
        if parent.is_symlink():
            raise BackupError("Recovery output has a symlink ancestor")
    if not path.parent.is_dir():
        raise BackupError("Recovery output parent must already exist")
    return path


@dataclass(frozen=True)
class Config:
    source_host: str
    source_database: str
    source_bucket: str
    source_owner: str
    backup_bucket: str
    backup_owner: str
    age_recipient: str
    recovery_owner: str
    source_port: int = 5432
    region: str = "us-west-2"
    prefix: str = "courierlifts-backups/v1"

    def validate(self) -> None:
        for bucket in (self.source_bucket, self.backup_bucket):
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
                raise BackupError("Concrete approved S3 bucket names are required")
        if self.source_bucket == self.backup_bucket:
            raise BackupError("Backup and runtime photo buckets must be separate")
        if any(not re.fullmatch(r"\d{12}", owner) for owner in (self.source_owner, self.backup_owner)):
            raise BackupError("Concrete approved AWS owner account IDs are required")
        if self.region != "us-west-2" or self.prefix != "courierlifts-backups/v1":
            raise BackupError("Unreviewed backup region or key prefix")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.source_database):
            raise BackupError("An explicit source database name is required")
        if not self.source_host or any(c in self.source_host for c in "/,?@ "):
            raise BackupError("An explicit source host is required")
        if type(self.source_port) is not int or not 1 <= self.source_port <= 65535:
            raise BackupError("An explicit valid source PostgreSQL port is required")
        # Native X25519 recipients only; no executable age plugins or passphrases.
        if not re.fullmatch(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}", self.age_recipient):
            raise BackupError("An approved native age public recipient is required")
        if (len(self.recovery_owner.strip()) < 3 or any(token in self.recovery_owner.upper()
                for token in ("REQUIRED", "UNASSIGNED", "PLACEHOLDER", "TBD"))):
            raise BackupError("A named recovery owner must be assigned before scheduling")

    @property
    def recipient_hash(self) -> str:
        return digest(self.age_recipient.encode())


def load_config(path: Path) -> Config:
    config = Config(**json.loads(path.read_text()))
    config.validate()
    return config


class Age:
    def __init__(self, binary: str = "age"):
        self.binary = binary
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode or result.stdout.strip().removeprefix("v") != "1.3.2":
            raise BackupError("The reviewed age v1.3.2 binary is required")

    def encrypt(self, source: Path, destination: Path, recipient: str) -> None:
        if os.path.lexists(destination):
            raise BackupError("Encryption output already exists")
        result = subprocess.run([self.binary, "--encrypt", "--recipient", recipient,
                                 "--output", str(destination), str(source)],
                                capture_output=True, timeout=900)
        if result.returncode:
            raise BackupError("age encryption failed")
        destination.chmod(0o600)

    def decrypt(self, source: Path, destination: Path, identity: Path) -> None:
        if (identity.is_symlink() or not identity.is_file()
                or stat.S_IMODE(identity.stat().st_mode) & 0o077):
            raise BackupError("Recovery identity must be a private regular file")
        if os.path.lexists(destination):
            raise BackupError("Decryption output already exists")
        result = subprocess.run([self.binary, "--decrypt", "--identity", str(identity),
                                 "--output", str(destination), str(source)],
                                capture_output=True, timeout=900)
        if result.returncode:
            destination.unlink(missing_ok=True)
            raise BackupError("age authenticated decryption failed")
        destination.chmod(0o600)


def valid_ref(value: Any, prefix: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"key", "version_id", "sha256", "size_bytes"}:
        raise BackupError("Invalid immutable object reference")
    key = value["key"]
    if (not isinstance(key, str) or not key.startswith(prefix + "/")
            or str(cutover.safe_key(key)) != key):
        raise BackupError("Object reference is outside the approved backup prefix")
    if not isinstance(value["version_id"], str) or value["version_id"] in {"", "null"}:
        raise BackupError("A non-null immutable S3 VersionId is required")
    if not re.fullmatch(HASH_PATTERN, value["sha256"]):
        raise BackupError("An exact encrypted-object SHA256 is required")
    if type(value["size_bytes"]) is not int or not 0 < value["size_bytes"] <= MAX_OBJECT:
        raise BackupError("Encrypted object is outside the reviewed size limit")
    return value


def assert_bucket_region(client: Any, bucket: str, owner: str, region: str) -> None:
    result = client.get_bucket_location(Bucket=bucket, ExpectedBucketOwner=owner)
    if result.get("LocationConstraint") != region:
        raise BackupError("S3 bucket is outside the approved region")


class Store:
    """S3 boundary: pinned versions, checksums, expected owner, conditional writes."""
    def __init__(self, client: Any, bucket: str, owner: str, prefix: str):
        self.client, self.bucket, self.owner, self.prefix = client, bucket, owner, prefix
        self._region_checked = False

    def region(self) -> None:
        if not self._region_checked:
            assert_bucket_region(self.client, self.bucket, self.owner, "us-west-2")
            self._region_checked = True

    def versioning(self) -> None:
        self.region()
        result = self.client.get_bucket_versioning(Bucket=self.bucket, ExpectedBucketOwner=self.owner)
        if result.get("Status") != "Enabled":
            raise BackupError("S3 versioning must be Enabled")

    def _args(self, key: str) -> dict:
        self.region()
        return {"Bucket": self.bucket, "ExpectedBucketOwner": self.owner, "Key": key}

    def head(self, key: str, version: str | None = None) -> dict | None:
        arguments = self._args(key)
        if version is not None:
            arguments["VersionId"] = version
        try:
            return self.client.head_object(**arguments, ChecksumMode="ENABLED")
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            if code in {"403", "AccessDenied"} and version is None:
                # Prefix-scoped ListBucket grants need not give HEAD permission
                # to distinguish an absent key from a forbidden one. An exact
                # authorized listing resolves absence without swallowing a real
                # permission error on an existing object or a pinned version.
                listing = self.client.list_objects_v2(Bucket=self.bucket, ExpectedBucketOwner=self.owner,
                                                       Prefix=key, MaxKeys=2)
                if not any(item["Key"] == key for item in listing.get("Contents", [])):
                    return None
            raise

    def ref_from_head(self, key: str, head: dict) -> dict:
        try:
            checksum = base64.b64decode(head["ChecksumSHA256"], validate=True).hex()
        except (KeyError, ValueError):
            raise BackupError("Backup object has no full SHA256 checksum") from None
        if head.get("ServerSideEncryption") != "AES256" or head.get("ChecksumType", "FULL_OBJECT") != "FULL_OBJECT":
            raise BackupError("Backup object encryption/checksum policy differs")
        return valid_ref({"key": key, "version_id": head.get("VersionId"),
                          "size_bytes": head.get("ContentLength"), "sha256": checksum}, self.prefix)

    def verify(self, reference: dict) -> dict:
        reference = valid_ref(reference, self.prefix)
        head = self.head(reference["key"], reference["version_id"])
        if not head or self.ref_from_head(reference["key"], head) != reference:
            raise BackupError("Pinned backup object is missing or its checksum differs")
        return head

    def upload(self, path: Path, key: str, metadata: dict | None = None) -> dict:
        if not key.startswith(self.prefix + "/") or str(cutover.safe_key(key)) != key:
            raise BackupError("Upload key is outside the approved backup prefix")
        if path.stat().st_size > MAX_OBJECT:
            raise BackupError("Backup exceeds the single-object pilot limit")
        hashed = cutover.sha256_file(path)
        with path.open("rb") as source:
            result = self.client.put_object(
                **self._args(key), Body=source, ContentLength=path.stat().st_size,
                ContentType="application/octet-stream", ServerSideEncryption="AES256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(hashed)).decode(),
                Metadata=metadata or {}, IfNoneMatch="*",
            )
        reference = valid_ref({"key": key, "version_id": result.get("VersionId"),
                               "sha256": hashed, "size_bytes": path.stat().st_size}, self.prefix)
        self.verify(reference)
        # Verify freshly written bytes end to end, not merely a reported ETag.
        with tempfile.TemporaryDirectory(prefix="cl-backup-upload-check-") as temporary:
            self.download(reference, Path(temporary) / "ciphertext")
        return reference

    def download(self, reference: dict, destination: Path) -> None:
        self.verify(reference)
        response = self.client.get_object(**self._args(reference["key"]), VersionId=reference["version_id"])
        body = response["Body"]
        try:
            if response.get("VersionId") != reference["version_id"]:
                raise BackupError("S3 returned a different object version")
            count, hashed = 0, hashlib.sha256()
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as output:
                while chunk := body.read(1024 * 1024):
                    count += len(chunk)
                    if count > reference["size_bytes"]:
                        raise BackupError("Backup object exceeds its recorded size")
                    hashed.update(chunk)
                    output.write(chunk)
            if count != reference["size_bytes"] or hashed.hexdigest() != reference["sha256"]:
                raise BackupError("Downloaded backup ciphertext failed SHA256 verification")
        finally:
            body.close()

    def read_marker(self, key: str, expected_reference: dict | None = None) -> tuple[dict, dict]:
        if expected_reference is None:
            head = self.head(key)
            if not head:
                raise BackupError("No complete checkpoint exists at the selected key")
            reference = self.ref_from_head(key, head)
        else:
            reference = valid_ref(expected_reference, self.prefix)
            if reference["key"] != key:
                raise BackupError("Approved complete marker belongs to another checkpoint")
        if reference["size_bytes"] > MAX_JSON:
            raise BackupError("Checkpoint marker is too large")
        with tempfile.TemporaryDirectory(prefix="cl-backup-marker-") as temporary:
            path = Path(temporary) / "marker"
            self.download(reference, path)
            return json.loads(path.read_bytes()), reference

    def marker_keys(self) -> Iterator[str]:
        self.region()
        token = None
        while True:
            arguments = {"Bucket": self.bucket, "ExpectedBucketOwner": self.owner,
                         "Prefix": self.prefix + "/checkpoints/"}
            if token:
                arguments["ContinuationToken"] = token
            page = self.client.list_objects_v2(**arguments)
            for item in page.get("Contents", []):
                if item["Key"].endswith("/complete.json"):
                    key = item["Key"]
                    match = re.fullmatch(re.escape(self.prefix) + r"/checkpoints/(" + ID_PATTERN + r")/complete\.json", key)
                    if not match:
                        raise BackupError("Unreviewed completion marker key")
                    try:
                        datetime.strptime(match[1].split("-")[0], "%Y%m%dT%H%M%S%fZ")
                    except ValueError:
                        raise BackupError("Completion marker has an invalid UTC checkpoint ID") from None
                    yield key
            if not page.get("IsTruncated"):
                return
            token = page["NextContinuationToken"]

    def markers(self) -> list[tuple[dict, dict]]:
        return [self.read_marker(key) for key in self.marker_keys()]

    def latest_marker(self) -> tuple[dict, dict]:
        # UTC IDs have fixed-width chronological timestamps. List every page,
        # then fetch only the newest completion marker, never every photo list.
        # Malformed/newer records must fail monitoring, not fall back to an old
        # successful checkpoint and conceal a broken run or changed object.
        key = max(self.marker_keys(), default=None)
        if key is None:
            raise BackupError("No completed checkpoint exists")
        marker, reference = self.read_marker(key)
        if marker.get("checkpoint_id") != key.split("/")[-2]:
            raise BackupError("Completion marker ID differs from its immutable key")
        return marker, reference

    def delete_checkpoint_version(self, reference: dict, checkpoint_id: str) -> None:
        allowed = f"{self.prefix}/checkpoints/{checkpoint_id}/"
        if not reference["key"].startswith(allowed) or reference["key"].removeprefix(allowed) not in {
            "database.dump.age", "manifest.json.age", "complete.json"
        }:
            raise BackupError("Retention may delete only the selected checkpoint's three artifacts")
        self.client.delete_object(**self._args(reference["key"]), VersionId=reference["version_id"])


def clients(config: Config, role: str) -> tuple[Any, Store]:
    """Never reuse the app's AWS credentials, SDK defaults, profiles or endpoints."""
    import boto3
    from botocore.config import Config as BotoConfig
    prefix = f"CL_BACKUP_{role.upper()}_AWS_"
    key, secret = os.environ.get(prefix + "ACCESS_KEY_ID"), os.environ.get(prefix + "SECRET_ACCESS_KEY")
    if not key or not secret:
        raise BackupError("Explicit role-specific backup credentials are required")
    client = boto3.client(
        "s3", region_name=config.region, endpoint_url=f"https://s3.{config.region}.amazonaws.com",
        aws_access_key_id=key, aws_secret_access_key=secret,
        aws_session_token=os.environ.get(prefix + "SESSION_TOKEN"),
        config=BotoConfig(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3, "mode": "standard"}),
    )
    return client, Store(client, config.backup_bucket, config.backup_owner, config.prefix)


def photo_key(config: Config, proof: dict) -> str:
    key_hash = digest(proof["storage_key"].encode())
    return f"{config.prefix}/photos/{config.recipient_hash}/{key_hash}/{proof['sha256']}.age"


def validate_proofs(proofs: list[dict]) -> None:
    ids, orders, keys = set(), set(), set()
    for proof in proofs:
        key = proof.get("storage_key", "")
        if not key.startswith("proofs/") or str(cutover.safe_key(key)) != key:
            raise BackupError("Invalid proof storage key")
        if (type(proof.get("size_bytes")) is not int or not 0 < proof["size_bytes"] <= MAX_PROOF
                or not re.fullmatch(HASH_PATTERN, proof.get("sha256", ""))
                or proof.get("content_type") not in {"image/jpeg", "image/png", "image/webp"}):
            raise BackupError("Invalid proof byte metadata")
        if any(type(proof.get(field)) is not int or proof[field] <= 0
               for field in ("id", "order_id", "uploaded_by_user_id")):
            raise BackupError("Invalid proof relationship identity")
        for field, values in (("id", ids), ("order_id", orders), ("storage_key", keys)):
            if proof.get(field) in values:
                raise BackupError("Duplicate proof identity")
            values.add(proof.get(field))


def archive_photo(client: Any, store: Store, config: Config, proof: dict, age: Age, temporary: Path) -> dict:
    """Download each unchanged historical photo only on first archive creation."""
    key = photo_key(config, proof)
    expected = {"plain-sha256": proof["sha256"], "plain-size": str(proof["size_bytes"]),
                "source-key-sha256": digest(proof["storage_key"].encode()),
                "source-bucket": config.source_bucket, "source-owner": config.source_owner,
                "recipient-sha256": config.recipient_hash}

    def existing(head: dict) -> dict:
        metadata = head.get("Metadata", {})
        if any(metadata.get(k) != v for k, v in expected.items()):
            raise BackupError("Existing incremental photo archive metadata differs")
        version = metadata.get("source-version-id")
        if not version or version == "null":
            raise BackupError("Photo archive lacks original source VersionId")
        reference = store.ref_from_head(key, head)
        store.verify(reference)
        return {**proof, "source_version_id": version, "archive": reference}

    head = store.head(key)
    if head:
        return existing(head)
    response = client.get_object(Bucket=config.source_bucket, ExpectedBucketOwner=config.source_owner,
                                 Key=proof["storage_key"])
    body = response["Body"]
    try:
        data = body.read(proof["size_bytes"] + 1)
    finally:
        body.close()
    version = response.get("VersionId")
    if not isinstance(version, str) or version in {"", "null"}:
        raise BackupError("Source proof has no immutable VersionId")
    if (response.get("ContentLength") != proof["size_bytes"] or len(data) != proof["size_bytes"]
            or digest(data) != proof["sha256"] or response.get("ContentType") != proof["content_type"]
            or response.get("ServerSideEncryption") != "AES256"):
        raise BackupError("Source proof does not match the exported database snapshot")
    plaintext, ciphertext = temporary / "photo", temporary / "photo.age"
    private_write(plaintext, data)
    try:
        age.encrypt(plaintext, ciphertext, config.age_recipient)
        try:
            reference = store.upload(ciphertext, key, {**expected, "source-version-id": version})
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                raise
            winner = store.head(key)
            if not winner:
                raise BackupError("Concurrent archive write could not be verified") from None
            return existing(winner)
        return {**proof, "source_version_id": version, "archive": reference}
    finally:
        plaintext.unlink(missing_ok=True)
        ciphertext.unlink(missing_ok=True)


@contextmanager
def postgres_environment(engine: sa.Engine, directory: Path) -> Iterator[dict]:
    if any(key.startswith("PG") for key in os.environ):
        raise BackupError("Inherited PG* environment overrides are forbidden")
    parsed = engine.url
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", parsed.database or ""):
        raise BackupError("PostgreSQL database names must be plain reviewed identifiers")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update(PGHOST=parsed.host or "", PGPORT=str(parsed.port or 5432),
                       PGDATABASE=parsed.database or "", PGUSER=parsed.username or "",
                       PGAPPNAME="courierlifts-encrypted-backup", PGCONNECT_TIMEOUT="10",
                       PGOPTIONS="-c timezone=UTC -c search_path=public,pg_catalog")
    if "sslmode" in parsed.query:
        environment["PGSSLMODE"] = parsed.query["sslmode"]
    password_file = directory / "postgres-password"
    escape = lambda value: str(value).replace("\\", "\\\\").replace(":", "\\:")
    parts = (parsed.host, parsed.port or 5432, parsed.database, parsed.username, parsed.password or "")
    if any("\n" in str(part) or "\r" in str(part) for part in parts):
        raise BackupError("Invalid PostgreSQL connection field")
    private_write(password_file, (":".join(escape(part or "") for part in parts) + "\n").encode())
    environment["PGPASSFILE"] = str(password_file)
    try:
        yield environment
    finally:
        password_file.unlink(missing_ok=True)


class Postgres:
    def __init__(self, binary_directory: str | None = None):
        self.dump = str(Path(binary_directory) / "pg_dump") if binary_directory else "pg_dump"
        self.restore = str(Path(binary_directory) / "pg_restore") if binary_directory else "pg_restore"
        for binary in (self.dump, self.restore):
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode or not re.search(r"\(PostgreSQL\) 16\.", result.stdout):
                raise BackupError("PostgreSQL 16 client tools are required")

    def capture(self, engine: sa.Engine, destination: Path, *, after_snapshot=None) -> dict:
        with engine.connect() as connection, connection.begin():
            connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            connection.execute(sa.text("SET LOCAL statement_timeout = '5min'"))
            connection.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
            if connection.dialect.server_version_info[0] != 16:
                raise BackupError("Reviewed backups require PostgreSQL 16")
            tables = cutover.inspect_target(connection)
            assert_reviewed_namespace(connection)
            rows = cutover.database_rows(connection, tables)
            validate_proofs(rows["delivery_proofs"])
            snapshot = connection.scalar(sa.text("SELECT pg_export_snapshot()"))
            created = connection.scalar(sa.text("SELECT transaction_timestamp()"))
            if after_snapshot:
                after_snapshot()  # Test-only hook proving concurrent writes are excluded.
            with postgres_environment(engine, destination.parent) as environment:
                result = subprocess.run([
                    self.dump, "--format=custom", "--no-owner", "--no-acl",
                    "--no-tablespaces", "--no-security-labels",
                    "--lock-wait-timeout=10s", f"--snapshot={snapshot}", f"--file={destination}",
                ], env=environment, capture_output=True, timeout=900)
            if result.returncode:
                raise BackupError("pg_dump failed; no checkpoint can be completed")
            destination.chmod(0o600)
            return {"created_at": created.astimezone(timezone.utc).isoformat(),
                    "tables": cutover.table_manifest(rows), "proofs": rows["delivery_proofs"],
                    "database_plaintext_sha256": cutover.sha256_file(destination),
                    "alembic_revision": cutover.HEAD}

    def restore_dump(self, engine: sa.Engine, path: Path) -> None:
        assert_empty_target(engine)
        with postgres_environment(engine, path.parent) as environment:
            result = subprocess.run([self.restore, "--exit-on-error", "--single-transaction",
                                     "--no-owner", "--no-acl", "--no-tablespaces",
                                     "--dbname=" + (engine.url.database or ""), str(path)],
                                    env=environment, capture_output=True, timeout=900)
        if result.returncode:
            raise BackupError("pg_restore failed; target remains closed for investigation")


def assert_reviewed_namespace(connection: sa.Connection) -> None:
    extras = connection.scalar(sa.text(
        "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public', 'information_schema') "
        "AND nspname NOT LIKE 'pg_%'"
    ))
    routines = connection.scalar(sa.text(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='public'"
    ))
    if extras or routines:
        raise BackupError("Unreviewed database schemas or public routines require reconciliation")


def assert_empty_target(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        cutover.inspect_target(connection, empty_schema=True)
        assert_reviewed_namespace(connection)
        other_sessions = connection.scalar(sa.text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"
        ))
        if other_sessions:
            raise BackupError("Recovery requires an empty isolated database with no other sessions")


def checkpoint_key(config: Config, checkpoint_id: str, artifact: str) -> str:
    if not re.fullmatch(ID_PATTERN, checkpoint_id):
        raise BackupError("Invalid checkpoint identifier")
    return f"{config.prefix}/checkpoints/{checkpoint_id}/{artifact}"


def validate_marker(marker: dict, config: Config) -> None:
    if marker.get("format_version") != 1 or marker.get("kind") != KIND:
        raise BackupError("Unsupported checkpoint format")
    if marker.get("recipient_sha256") != config.recipient_hash:
        raise BackupError("Checkpoint belongs to a different recovery recipient")
    timestamp(marker["created_at"])
    for field, artifact in (("database", "database.dump.age"), ("manifest", "manifest.json.age")):
        reference = valid_ref(marker[field], config.prefix)
        if reference["key"] != checkpoint_key(config, marker["checkpoint_id"], artifact):
            raise BackupError("Checkpoint payload points outside its immutable directory")
    keys = set()
    for photo in marker["photos"]:
        reference = valid_ref(photo, config.prefix)
        if not reference["key"].startswith(f"{config.prefix}/photos/{config.recipient_hash}/"):
            raise BackupError("Checkpoint photo points outside the encrypted photo archive")
        if reference["key"] in keys:
            raise BackupError("Duplicate checkpoint photo archive")
        keys.add(reference["key"])


def run_backup(config: Config, engine: sa.Engine, client: Any, store: Store, age: Age, postgres: Postgres,
               *, after_snapshot=None) -> dict:
    config.validate()
    if (engine.url.host != config.source_host or engine.url.database != config.source_database
            or (engine.url.port or 5432) != config.source_port):
        raise BackupError("Backup engine differs from the configured source identity")
    store.versioning()
    assert_bucket_region(client, config.source_bucket, config.source_owner, config.region)
    source_versioning = client.get_bucket_versioning(Bucket=config.source_bucket, ExpectedBucketOwner=config.source_owner)
    if source_versioning.get("Status") != "Enabled":
        raise BackupError("Source photo bucket versioning must be Enabled")
    checkpoint_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:16]
    with tempfile.TemporaryDirectory(prefix="cl-encrypted-backup-") as directory:
        temporary = Path(directory)
        dump = temporary / "database.dump"
        snapshot = postgres.capture(engine, dump, after_snapshot=after_snapshot)
        proofs = [archive_photo(client, store, config, proof, age, temporary) for proof in snapshot.pop("proofs")]
        age.encrypt(dump, temporary / "database.dump.age", config.age_recipient)
        database = store.upload(temporary / "database.dump.age", checkpoint_key(config, checkpoint_id, "database.dump.age"))
        dump.unlink()
        manifest = {"format_version": 1, "kind": MANIFEST_KIND, "checkpoint_id": checkpoint_id,
                    **snapshot, "database": database, "proofs": proofs,
                    "source": {"host": config.source_host, "port": config.source_port, "database": config.source_database,
                               "photo_bucket": config.source_bucket, "photo_owner": config.source_owner},
                    "recipient_sha256": config.recipient_hash, "recovery_owner": config.recovery_owner}
        private_write(temporary / "manifest.json", encoded(manifest))
        age.encrypt(temporary / "manifest.json", temporary / "manifest.json.age", config.age_recipient)
        encrypted_manifest = store.upload(temporary / "manifest.json.age", checkpoint_key(config, checkpoint_id, "manifest.json.age"))
        marker = {"format_version": 1, "kind": KIND, "checkpoint_id": checkpoint_id,
                  "created_at": snapshot["created_at"], "recipient_sha256": config.recipient_hash,
                  "database": database, "manifest": encrypted_manifest,
                  "photos": sorted((proof["archive"] for proof in proofs), key=lambda ref: ref["key"])}
        validate_marker(marker, config)
        for reference in [database, encrypted_manifest, *marker["photos"]]:
            store.verify(reference)
        private_write(temporary / "complete.json", encoded(marker))
        complete = store.upload(temporary / "complete.json", checkpoint_key(config, checkpoint_id, "complete.json"))
        return {"checkpoint_id": checkpoint_id, "complete": True, "proof_count": len(proofs),
                "tables": snapshot["tables"], "manifest_ciphertext_sha256": encrypted_manifest["sha256"],
                "complete_marker": complete}


def verify_database(engine: sa.Engine, manifest: dict) -> dict:
    with engine.connect() as connection, connection.begin():
        connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        tables = cutover.inspect_target(connection)
        rows = cutover.database_rows(connection, tables)
        actual = cutover.table_manifest(rows)
        if actual != manifest["tables"]:
            raise BackupError("Restored table digests differ from the exported snapshot")
        proofs = [{key: proof[key] for key in cutover.TABLE_COLUMNS["delivery_proofs"]} for proof in manifest["proofs"]]
        if rows["delivery_proofs"] != proofs:
            raise BackupError("Restored proof relationships differ from the encrypted manifest")
        for name in tables:
            if name == "quotes":
                continue
            sequence = connection.scalar(sa.text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": f"public.{name}"})
            if not sequence:
                raise BackupError("Restored ID sequence is missing")
            # Sequence state is not MVCC. pg_dump may legitimately capture a
            # later high-water mark; it must never generate a duplicate row ID.
            safe = sequence.replace('"', '""').split(".")
            quoted = ".".join('"' + part + '"' for part in safe)
            last, called = connection.execute(sa.text(f"SELECT last_value, is_called FROM {quoted}")).one()
            next_id = last + 1 if called else last
            if next_id <= max((row["id"] for row in rows[name]), default=0):
                raise BackupError("Restored ID sequence would collide with preserved rows")
        return actual


def restore(config: Config, checkpoint_id: str, store: Store, age: Age, postgres: Postgres,
            engine: sa.Engine, identity: Path, photos_new: Path, receipt_new: Path, *, complete_reference: dict) -> dict:
    config.validate()
    if engine.url.database == config.source_database:
        raise BackupError("Restore must use a new database name, never the live source name")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", engine.url.database or ""):
        raise BackupError("Recovery database name must be a plain reviewed identifier")
    photos_new, receipt_new = new_path(photos_new), new_path(receipt_new)
    assert_empty_target(engine)
    marker, marker_reference = store.read_marker(checkpoint_key(config, checkpoint_id, "complete.json"), complete_reference)
    validate_marker(marker, config)
    if marker["checkpoint_id"] != checkpoint_id:
        raise BackupError("Selected checkpoint identity differs")
    with tempfile.TemporaryDirectory(prefix="cl-backup-restore-") as directory:
        temporary = Path(directory)
        for field, name in (("manifest", "manifest.json"), ("database", "database.dump")):
            store.download(marker[field], temporary / (name + ".age"))
            age.decrypt(temporary / (name + ".age"), temporary / name, identity)
        if (temporary / "manifest.json").stat().st_size > MAX_JSON:
            raise BackupError("Decrypted manifest exceeds the reviewed size limit")
        manifest = json.loads((temporary / "manifest.json").read_bytes())
        if (manifest.get("format_version") != 1 or manifest.get("kind") != MANIFEST_KIND
                or manifest.get("checkpoint_id") != checkpoint_id or manifest.get("database") != marker["database"]
                or manifest.get("recipient_sha256") != config.recipient_hash
                or manifest.get("alembic_revision") != cutover.HEAD):
            raise BackupError("Encrypted manifest is not bound to this checkpoint")
        if manifest.get("source") != {"host": config.source_host, "port": config.source_port, "database": config.source_database,
                                     "photo_bucket": config.source_bucket, "photo_owner": config.source_owner}:
            raise BackupError("Encrypted manifest belongs to a different source dataset")
        if cutover.sha256_file(temporary / "database.dump") != manifest["database_plaintext_sha256"]:
            raise BackupError("Decrypted database dump SHA256 differs")
        validate_proofs(manifest["proofs"])
        if sorted((proof["archive"] for proof in manifest["proofs"]), key=lambda ref: ref["key"]) != marker["photos"]:
            raise BackupError("Manifest photo references differ from the complete marker")
        # Prepare every photo before changing even the empty recovery database.
        staged_photos = temporary / "photos"
        staged_photos.mkdir(mode=0o700)
        for index, proof in enumerate(manifest["proofs"]):
            if proof["archive"]["key"] != photo_key(config, proof) or not proof.get("source_version_id"):
                raise BackupError("Proof archive identity differs from its metadata")
            cipher = temporary / f"photo-{index}.age"
            store.download(proof["archive"], cipher)
            destination = staged_photos / cutover.safe_key(proof["storage_key"])
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            age.decrypt(cipher, destination, identity)
            if destination.stat().st_size != proof["size_bytes"] or cutover.sha256_file(destination) != proof["sha256"]:
                raise BackupError("Restored proof bytes differ from the snapshot metadata")
            cipher.unlink()
        # Reserve the new namespace without an overwriting rename. A failed DB
        # restore leaves it quarantined and deliberately prevents a blind retry.
        photos_new.mkdir(mode=0o700, exist_ok=False)
        for source in staged_photos.rglob("*"):
            relative = source.relative_to(staged_photos)
            target = photos_new / relative
            if source.is_dir():
                target.mkdir(mode=0o700, exist_ok=False)
            else:
                private_write(target, source.read_bytes())
        postgres.restore_dump(engine, temporary / "database.dump")
        actual = verify_database(engine, manifest)
        files = cutover.file_manifest(photos_new)
        cutover.verify_proofs(manifest["proofs"], files)
        receipt = {"format_version": 1, "kind": RECEIPT_KIND, "checkpoint_id": checkpoint_id,
                   "complete_marker": marker_reference,
                   "manifest_sha256": digest(encoded(manifest)),
                   "database_target": {"host": engine.url.host, "port": engine.url.port or 5432,
                                       "database": engine.url.database},
                   "tables": actual,
                   "proofs": [{column: proof[column] for column in cutover.TABLE_COLUMNS["delivery_proofs"]}
                              for proof in manifest["proofs"]],
                   "photos": {"path": str(photos_new), "count": len(files), "manifest_sha256": digest(encoded(files))},
                   "photo_files": files,
                   "verified_at": datetime.now(timezone.utc).isoformat()}
        private_write(receipt_new, encoded(receipt))
        return receipt


def retention_plan(markers: list[dict], config: Config, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not markers:
        raise BackupError("No completed checkpoint exists; retention must not run")
    for marker in markers:
        validate_marker(marker, config)
    ordered = sorted(markers, key=lambda marker: (timestamp(marker["created_at"]), marker["checkpoint_id"]), reverse=True)
    latest = timestamp(ordered[0]["created_at"])
    if latest > now + timedelta(minutes=5) or now - latest > timedelta(hours=26):
        raise BackupError("Latest completed backup is missing, stale or in the future")
    keep = set()
    for limit, period in ((7, lambda date: date.date()),
                          (4, lambda date: date.isocalendar()[:2]),
                          (3, lambda date: (date.year, date.month))):
        selected = set()
        for marker in ordered:
            group = period(timestamp(marker["created_at"]))
            if group not in selected and len(selected) < limit:
                keep.add(marker["checkpoint_id"])
                selected.add(group)
    return {"keep": sorted(keep), "prune": sorted(marker["checkpoint_id"] for marker in ordered if marker["checkpoint_id"] not in keep),
            "photo_archives_deleted": 0, "policy": {"daily": 7, "weekly": 4, "monthly": 3}}


def retention(config: Config, store: Store, *, apply: bool = False, now: datetime | None = None) -> dict:
    checkpoints = store.markers()
    plan = retention_plan([marker for marker, _ in checkpoints], config, now=now)
    # Prove kept checkpoints' ciphertext is available before deleting any older
    # DB/manifest. Actual age decryption remains the offline restore drill gate.
    verified = set()
    for marker, _ in checkpoints:
        if marker["checkpoint_id"] in plan["keep"]:
            for reference in [marker["database"], marker["manifest"], *marker["photos"]]:
                identity = (reference["key"], reference["version_id"], reference["sha256"], reference["size_bytes"])
                if identity not in verified:
                    store.verify(reference)
                    verified.add(identity)
    if apply:
        for marker, reference in checkpoints:
            if marker["checkpoint_id"] in plan["prune"]:
                # Remove discoverability first; an interrupted prune cannot leave
                # a valid-looking complete marker pointing to missing payloads.
                for item in (reference, marker["database"], marker["manifest"]):
                    store.delete_checkpoint_version(item, marker["checkpoint_id"])
    return {**plan, "applied": apply}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "retention", "restore", "freshness"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name in {"run", "restore"}:
            command.add_argument("--age-binary", default="age")
            command.add_argument("--pg-bin")
        if name == "run":
            command.add_argument("--allow-writes", action="store_true", required=True)
        if name in {"retention", "restore"}:
            command.add_argument("--apply", action="store_true", required=name == "restore")
        if name == "restore":
            command.add_argument("--checkpoint-id", required=True)
            command.add_argument("--complete-reference", type=Path, required=True,
                                 help="Independently recorded key/VersionId/SHA256/size JSON from successful run output")
            command.add_argument("--identity", type=Path, required=True)
            command.add_argument("--target-host", required=True)
            command.add_argument("--target-database", required=True)
            command.add_argument("--photos-new", type=Path, required=True)
            command.add_argument("--receipt-new", type=Path, required=True)
    arguments = parser.parse_args()
    engine = None
    old_umask = os.umask(0o077)
    try:
        config = load_config(arguments.config)
        role = {"run": "writer", "retention": "retention", "restore": "recovery", "freshness": "monitor"}[arguments.command]
        client, store = clients(config, role)
        if arguments.command == "run":
            url = os.environ.get("CL_BACKUP_DATABASE_URL")
            if not url:
                raise BackupError("CL_BACKUP_DATABASE_URL is required; app credentials are never reused")
            engine = cutover.target_engine(url, config.source_host, config.source_database)
            result = run_backup(config, engine, client, store, Age(arguments.age_binary), Postgres(arguments.pg_bin))
        elif arguments.command == "retention":
            result = retention(config, store, apply=arguments.apply)
        elif arguments.command == "freshness":
            marker, _ = store.latest_marker()
            retention_plan([marker], config)
            result = {"latest_completed_backup_within_26_hours": True, "checkpoint_id": marker["checkpoint_id"]}
        else:
            url = os.environ.get("CL_BACKUP_RESTORE_DATABASE_URL")
            if not url:
                raise BackupError("CL_BACKUP_RESTORE_DATABASE_URL is required")
            engine = cutover.target_engine(url, arguments.target_host, arguments.target_database)
            result = restore(config, arguments.checkpoint_id, store, Age(arguments.age_binary), Postgres(arguments.pg_bin),
                             engine, arguments.identity, arguments.photos_new, arguments.receipt_new,
                             complete_reference=json.loads(arguments.complete_reference.read_text()))
            # The private receipt file carries proof paths/relationships for the
            # before-start verifier. Do not publish those records in job logs.
            result = {"checkpoint_id": result["checkpoint_id"], "verified": True,
                      "receipt_sha256": digest(encoded(result)), "tables": result["tables"]}
        print(json.dumps({"event": "backup_" + arguments.command + "_passed", **result}, sort_keys=True))
        return 0
    except (BackupError, cutover.CutoverError) as error:
        print(json.dumps({"event": "backup_" + arguments.command + "_failed", "reason": str(error)}), file=sys.stderr)
        return 1
    except Exception as error:
        # Driver exceptions can contain SQL row values and credentials.
        print(json.dumps({"event": "backup_" + arguments.command + "_failed", "reason": type(error).__name__}), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
