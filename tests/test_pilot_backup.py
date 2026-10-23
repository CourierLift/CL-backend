"""Offline backup boundary/retention tests; all S3 behavior is an injected fake."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from botocore.exceptions import ClientError
import pytest
import sqlalchemy as sa


MODULE_PATH = Path(__file__).resolve().parents[1] / "ops/pilot-backup/pilot_backup.py"
SPEC = importlib.util.spec_from_file_location("pilot_backup", MODULE_PATH)
backup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup
SPEC.loader.exec_module(backup)

PHOTO = b"synthetic-private-photo-bytes"
PROOF = {"id": 3, "order_id": 7, "uploaded_by_user_id": 11,
         "storage_key": "proofs/7/synthetic.jpg", "content_type": "image/jpeg",
         "size_bytes": len(PHOTO), "sha256": hashlib.sha256(PHOTO).hexdigest(),
         "created_at": "2026-10-22T00:00:00.000000+00:00"}
# A valid native recipient taken from the age project's public documentation.
# Only synthetic tests use this key, and tests that decrypt generate their own.
RECIPIENT = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"


class FakeS3:
    def __init__(self):
        self.objects, self.current, self.calls = {}, {}, []
        self.versioning_enabled = True
        self.bucket_locations = {}
        self.put_failure = None
        self.counter = 0

    def seed(self, bucket, key, data, *, metadata=None, content_type="application/octet-stream", version=True):
        self.counter += 1
        version_id = f"synthetic-version-{self.counter}" if version else "null"
        self.objects[(bucket, key, version_id)] = {
            "data": data, "VersionId": version_id, "ContentLength": len(data),
            "ContentType": content_type, "ServerSideEncryption": "AES256", "Metadata": metadata or {},
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
            "ChecksumType": "FULL_OBJECT",
        }
        self.current[(bucket, key)] = version_id
        return version_id

    def get_bucket_versioning(self, **kwargs):
        assert kwargs["ExpectedBucketOwner"] == "111111111111"
        self.calls.append(("versioning", kwargs["Bucket"], None))
        return {"Status": "Enabled" if self.versioning_enabled else "Suspended"}

    def get_bucket_location(self, **kwargs):
        assert kwargs["ExpectedBucketOwner"] == "111111111111"
        self.calls.append(("location", kwargs["Bucket"], None))
        return {"LocationConstraint": self.bucket_locations.get(kwargs["Bucket"], "us-west-2")}

    def _record(self, arguments):
        assert arguments["ExpectedBucketOwner"] == "111111111111"
        bucket, key = arguments["Bucket"], arguments["Key"]
        version = arguments.get("VersionId", self.current.get((bucket, key)))
        record = self.objects.get((bucket, key, version))
        if record is None:
            raise ClientError({"Error": {"Code": "404"}}, "SyntheticOnly")
        return record

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs["Bucket"], kwargs["Key"]))
        return {key: value for key, value in self._record(kwargs).items() if key != "data"}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs["Bucket"], kwargs["Key"]))
        record = self._record(kwargs)
        return {**{key: value for key, value in record.items() if key != "data"}, "Body": io.BytesIO(record["data"])}

    def put_object(self, **kwargs):
        assert kwargs["ExpectedBucketOwner"] == "111111111111"
        assert kwargs["IfNoneMatch"] == "*"
        assert kwargs["ServerSideEncryption"] == "AES256"
        self.calls.append(("put", kwargs["Bucket"], kwargs["Key"]))
        if self.put_failure and self.put_failure in kwargs["Key"]:
            raise RuntimeError("synthetic upload failure")
        if (kwargs["Bucket"], kwargs["Key"]) in self.current:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "SyntheticOnly")
        data = kwargs["Body"].read()
        assert base64.b64encode(hashlib.sha256(data).digest()).decode() == kwargs["ChecksumSHA256"]
        assert len(data) == kwargs["ContentLength"]
        return {"VersionId": self.seed(kwargs["Bucket"], kwargs["Key"], data, metadata=kwargs.get("Metadata"))}

    def list_objects_v2(self, **kwargs):
        assert kwargs["ExpectedBucketOwner"] == "111111111111"
        keys = sorted(key for bucket, key in self.current if bucket == kwargs["Bucket"] and key.startswith(kwargs["Prefix"]))
        start = int(kwargs.get("ContinuationToken", "0"))
        batch = keys[start:start + 5]
        return {"Contents": [{"Key": key} for key in batch], "IsTruncated": start + 5 < len(keys),
                "NextContinuationToken": str(start + 5)}

    def delete_object(self, **kwargs):
        assert kwargs["VersionId"] not in {None, "null"}
        self.calls.append(("delete", kwargs["Bucket"], kwargs["Key"]))
        self.objects.pop((kwargs["Bucket"], kwargs["Key"], kwargs["VersionId"]))
        if self.current.get((kwargs["Bucket"], kwargs["Key"])) == kwargs["VersionId"]:
            del self.current[(kwargs["Bucket"], kwargs["Key"])]
        return {}


class FakeAge:
    """Only a boundary fake. Real cryptographic verification is in the native drill."""
    def encrypt(self, source, destination, recipient):
        backup.private_write(destination, b"UNIT-TEST-NOT-ENCRYPTION:" + source.read_bytes())


class FakePostgres:
    def __init__(self, proofs=None):
        self.proofs = [dict(PROOF)] if proofs is None else proofs

    def capture(self, engine, destination, *, after_snapshot=None):
        backup.private_write(destination, b"synthetic database dump")
        return {"created_at": datetime.now(timezone.utc).isoformat(),
                "tables": {name: {"rows": 0, "sha256": "a" * 64} for name in backup.cutover.TABLE_COLUMNS},
                "proofs": self.proofs, "database_plaintext_sha256": backup.cutover.sha256_file(destination),
                "alembic_revision": backup.cutover.HEAD}


@pytest.fixture
def config():
    return backup.Config(source_host="127.0.0.1", source_database="pilot_backup_source",
                         source_bucket="synthetic-proofs", source_owner="111111111111",
                         backup_bucket="synthetic-backups", backup_owner="111111111111",
                         age_recipient=RECIPIENT, recovery_owner="Synthetic Recovery Owner")


@pytest.fixture
def objects(config):
    client = FakeS3()
    client.seed(config.source_bucket, PROOF["storage_key"], PHOTO, content_type=PROOF["content_type"])
    return client, backup.Store(client, config.backup_bucket, config.backup_owner, config.prefix)


@pytest.fixture
def fake_engine(config):
    engine = sa.create_engine(f"postgresql+psycopg://synthetic@{config.source_host}/{config.source_database}")
    yield engine
    engine.dispose()


def perform(config, objects, fake_engine, **kwargs):
    client, store = objects
    return backup.run_backup(config, fake_engine, client, store, kwargs.pop("age", FakeAge()),
                             kwargs.pop("postgres", FakePostgres()), **kwargs)


def test_completion_is_last_and_all_payloads_have_pinned_verified_versions(config, objects, fake_engine):
    result = perform(config, objects, fake_engine)
    client, store = objects
    marker, reference = store.read_marker(backup.checkpoint_key(config, result["checkpoint_id"], "complete.json"))
    backup.validate_marker(marker, config)
    puts = [key for action, _, key in client.calls if action == "put"]
    assert puts[-1].endswith("/complete.json")
    assert len(puts) == 4  # one encrypted photo, dump, encrypted manifest, complete marker
    assert reference["version_id"] != "null"
    assert all(ref["version_id"] != "null" for ref in [marker["database"], marker["manifest"], *marker["photos"]])


def test_second_backup_reuses_verified_photo_without_source_download(config, objects, fake_engine):
    first = perform(config, objects, fake_engine)
    client, store = objects
    old_get_count = len([call for call in client.calls if call[:2] == ("get", config.source_bucket)])
    # Current object damage/deletion must not invalidate the already-archived,
    # hash-matching immutable old version needed by a retained DB checkpoint.
    del client.current[(config.source_bucket, PROOF["storage_key"])]
    second = perform(config, objects, fake_engine)
    assert first["checkpoint_id"] != second["checkpoint_id"]
    assert len([call for call in client.calls if call[:2] == ("get", config.source_bucket)]) == old_get_count == 1
    assert len([key for bucket, key in client.current if bucket == config.backup_bucket and "/photos/" in key]) == 1


def test_incremental_backup_downloads_only_new_photo(config, objects, fake_engine):
    perform(config, objects, fake_engine)
    client, _ = objects
    other = {**PROOF, "id": 4, "order_id": 8, "storage_key": "proofs/8/second.jpg"}
    client.seed(config.source_bucket, other["storage_key"], PHOTO, content_type=other["content_type"])
    perform(config, objects, fake_engine, postgres=FakePostgres([PROOF, other]))
    source_gets = [key for action, bucket, key in client.calls if action == "get" and bucket == config.source_bucket]
    assert source_gets == [PROOF["storage_key"], other["storage_key"]]


@pytest.mark.parametrize("mutation", ["wrong_bytes", "no_version", "wrong_content_type", "wrong_size", "wrong_encryption", "missing"])
def test_inconsistent_source_photo_never_produces_complete_marker(config, objects, fake_engine, mutation):
    client, _ = objects
    version = client.current[(config.source_bucket, PROOF["storage_key"])]
    record = client.objects[(config.source_bucket, PROOF["storage_key"], version)]
    if mutation == "wrong_bytes":
        record["data"] = b"x" * len(PHOTO)
    elif mutation == "no_version":
        record["VersionId"] = "null"
    elif mutation == "wrong_content_type":
        record["ContentType"] = "text/plain"
    elif mutation == "wrong_size":
        record["ContentLength"] += 1
    elif mutation == "wrong_encryption":
        record["ServerSideEncryption"] = "aws:kms"
    else:
        del client.current[(config.source_bucket, PROOF["storage_key"])]
    with pytest.raises((backup.BackupError, ClientError)):
        perform(config, objects, fake_engine)
    assert not any(key.endswith("/complete.json") for _, key in client.current)


@pytest.mark.parametrize("failure", ["database.dump.age", "manifest.json.age", "complete.json"])
def test_partial_write_failure_is_not_a_completed_checkpoint(config, objects, fake_engine, failure):
    client, store = objects
    client.put_failure = failure
    with pytest.raises(RuntimeError):
        perform(config, objects, fake_engine)
    assert store.markers() == []


def test_missing_versioning_blocks_all_writes(config, objects, fake_engine):
    client, _ = objects
    client.versioning_enabled = False
    with pytest.raises(backup.BackupError, match="versioning"):
        perform(config, objects, fake_engine)
    assert not any(call[0] == "put" for call in client.calls)


@pytest.mark.parametrize("field", ["source_bucket", "backup_bucket"])
def test_wrong_actual_bucket_region_blocks_all_writes(config, objects, fake_engine, field):
    client, _ = objects
    client.bucket_locations[getattr(config, field)] = "eu-west-1"
    with pytest.raises(backup.BackupError, match="approved region"):
        perform(config, objects, fake_engine)
    assert not any(call[0] == "put" for call in client.calls)


def test_monitor_refuses_wrong_backup_region_before_reading_markers(config, objects):
    client, store = objects
    client.bucket_locations[config.backup_bucket] = None  # AWS uses null for us-east-1.
    with pytest.raises(backup.BackupError, match="approved region"):
        store.latest_marker()
    assert not any(call[0] == "get" for call in client.calls)


def test_existing_archive_metadata_tampering_prevents_new_checkpoint(config, objects, fake_engine):
    perform(config, objects, fake_engine)
    client, store = objects
    key = backup.photo_key(config, PROOF)
    version = client.current[(config.backup_bucket, key)]
    client.objects[(config.backup_bucket, key, version)]["Metadata"]["plain-sha256"] = "f" * 64
    with pytest.raises(backup.BackupError, match="metadata"):
        perform(config, objects, fake_engine)
    assert len(store.markers()) == 1


def test_prefix_scoped_list_permission_resolves_missing_head_403(config, objects, fake_engine, monkeypatch):
    client, _ = objects
    original = client.head_object
    def head(**kwargs):
        try:
            return original(**kwargs)
        except ClientError as error:
            if error.response["Error"]["Code"] == "404":
                raise ClientError({"Error": {"Code": "403"}}, "SyntheticOnly") from None
            raise
    monkeypatch.setattr(client, "head_object", head)
    assert perform(config, objects, fake_engine)["complete"] is True


def test_existing_object_head_permission_error_is_not_treated_as_missing(config, objects, fake_engine, monkeypatch):
    perform(config, objects, fake_engine)
    client, store = objects
    def denied(**kwargs):
        raise ClientError({"Error": {"Code": "403"}}, "SyntheticOnly")
    monkeypatch.setattr(client, "head_object", denied)
    with pytest.raises(ClientError):
        store.head(backup.photo_key(config, PROOF))


def test_download_detects_corrupt_ciphertext_even_when_head_checksum_is_unchanged(config, objects, fake_engine, tmp_path):
    perform(config, objects, fake_engine)
    client, store = objects
    marker, _ = store.markers()[0]
    ref = marker["database"]
    client.objects[(config.backup_bucket, ref["key"], ref["version_id"])]["data"] = b"X" * ref["size_bytes"]
    with pytest.raises(backup.BackupError, match="SHA256"):
        store.download(ref, tmp_path / "download")


def test_conditional_upload_refuses_overwrite(config, objects, tmp_path):
    _, store = objects
    path = tmp_path / "data"
    path.write_bytes(b"first synthetic payload")
    key = config.prefix + "/example"
    reference = store.upload(path, key)
    path.write_bytes(b"replacement forbidden")
    with pytest.raises(ClientError):
        store.upload(path, key)
    store.verify(reference)


def marker_for(config, date, number):
    checkpoint_id = date.strftime("%Y%m%dT%H%M%S%fZ-") + f"{number:016x}"
    def ref(artifact):
        return {"key": backup.checkpoint_key(config, checkpoint_id, artifact),
                "version_id": "synthetic-version", "sha256": "b" * 64, "size_bytes": 3}
    return {"format_version": 1, "kind": backup.KIND, "checkpoint_id": checkpoint_id,
            "created_at": date.isoformat(), "recipient_sha256": config.recipient_hash,
            "database": ref("database.dump.age"), "manifest": ref("manifest.json.age"), "photos": []}


def test_retention_selects_seven_daily_four_weekly_three_monthly_union(config):
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    markers = [marker_for(config, now - timedelta(days=offset), offset) for offset in range(120)]
    older_same_day = marker_for(config, now - timedelta(hours=2), 999)
    plan = backup.retention_plan([*markers, older_same_day], config, now=now)
    expected_offsets = {0, 1, 2, 3, 4, 5, 6, 11, 18, 22, 52}
    assert set(plan["keep"]) == {markers[offset]["checkpoint_id"] for offset in expected_offsets}
    assert older_same_day["checkpoint_id"] in plan["prune"]
    assert plan["photo_archives_deleted"] == 0


@pytest.mark.parametrize("offset", [-timedelta(minutes=6), timedelta(hours=27)])
def test_stale_or_future_latest_checkpoint_blocks_retention(config, offset):
    now = datetime.now(timezone.utc)
    with pytest.raises(backup.BackupError, match="stale or in the future"):
        backup.retention_plan([marker_for(config, now - offset, 1)], config, now=now)


def test_retention_requires_a_completed_checkpoint(config):
    with pytest.raises(backup.BackupError, match="No completed"):
        backup.retention_plan([], config)


def seed_checkpoint(config, client, marker):
    for field in ("database", "manifest"):
        key = marker[field]["key"]
        version = client.seed(config.backup_bucket, key, b"synthetic ciphertext")
        marker[field] = {"key": key, "version_id": version, "sha256": backup.digest(b"synthetic ciphertext"), "size_bytes": 20}
    key = backup.checkpoint_key(config, marker["checkpoint_id"], "complete.json")
    client.seed(config.backup_bucket, key, backup.encoded(marker))


def test_automated_retention_deletes_only_expired_checkpoint_versions(config, objects):
    client, store = objects
    now = datetime.now(timezone.utc)
    for offset in range(40):
        seed_checkpoint(config, client, marker_for(config, now - timedelta(days=offset), offset))
    orphan_photo_key = config.prefix + "/photos/keep-even-with-no-retained-checkpoint.age"
    client.seed(config.backup_bucket, orphan_photo_key, b"never age-delete a photo")
    dry_run = backup.retention(config, store, now=now)
    assert not any(call[0] == "delete" for call in client.calls)
    applied = backup.retention(config, store, apply=True, now=now)
    assert applied["keep"] == dry_run["keep"]
    deleted = [key for action, _, key in client.calls if action == "delete"]
    assert len(deleted) == 3 * len(applied["prune"])
    assert all("/checkpoints/" in key for key in deleted)
    assert (config.backup_bucket, orphan_photo_key) in client.current
    assert all(deleted[index].endswith("/complete.json") for index in range(0, len(deleted), 3))


def test_retention_stops_before_deletion_if_retained_payload_is_missing(config, objects):
    client, store = objects
    now = datetime.now(timezone.utc)
    markers = [marker_for(config, now - timedelta(days=offset), offset) for offset in range(20)]
    for marker in markers:
        seed_checkpoint(config, client, marker)
    ref = markers[0]["database"]
    del client.objects[(config.backup_bucket, ref["key"], ref["version_id"])]
    with pytest.raises(backup.BackupError):
        backup.retention(config, store, apply=True, now=now)
    assert not any(call[0] == "delete" for call in client.calls)


@pytest.mark.parametrize("field,value", [("recovery_owner", "UNASSIGNED"), ("age_recipient", "passphrase"),
                                         ("backup_bucket", "synthetic-proofs"), ("region", "unreviewed-region"),
                                         ("source_port", 0), ("source_port", True), ("source_port", "5432")])
def test_unreviewed_configuration_fails_closed(config, field, value):
    with pytest.raises(backup.BackupError):
        replace(config, **{field: value}).validate()


@pytest.mark.parametrize("key", ["../escape", "/absolute", "proofs/../escape", "proofs//file"])
def test_unsafe_proof_key_is_rejected(key):
    with pytest.raises((backup.BackupError, backup.cutover.CutoverError)):
        backup.validate_proofs([{**PROOF, "storage_key": key}])


def test_existing_or_symlink_restore_output_is_rejected(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(backup.BackupError):
        backup.new_path(existing)
    (tmp_path / "link").symlink_to(existing)
    with pytest.raises(backup.BackupError, match="symlink"):
        backup.new_path(tmp_path / "link/output")


def test_cli_failure_is_nonzero_and_does_not_log_driver_secrets(config, tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.__dict__))
    monkeypatch.setattr(sys, "argv", ["pilot_backup.py", "freshness", "--config", str(path)])
    def fail(*args):
        raise RuntimeError("postgresql://user:secret-password@host/db; synthetic personal data")
    monkeypatch.setattr(backup, "clients", fail)
    assert backup.main() == 1
    captured = capsys.readouterr()
    assert "backup_freshness_failed" in captured.err
    assert "secret-password" not in captured.err
    assert "personal data" not in captured.err


@pytest.mark.parametrize("state", ["missing", "stale", "future"])
def test_freshness_cli_exits_nonzero_for_missing_or_stale_checkpoint(config, objects, tmp_path, monkeypatch, capsys, state):
    client, store = objects
    if state != "missing":
        offset = -timedelta(hours=27) if state == "stale" else timedelta(minutes=6)
        seed_checkpoint(config, client, marker_for(config, datetime.now(timezone.utc) + offset, 1))
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.__dict__))
    monkeypatch.setattr(sys, "argv", ["pilot_backup.py", "freshness", "--config", str(path)])
    monkeypatch.setattr(backup, "clients", lambda *_: (client, store))
    assert backup.main() == 1
    assert "backup_freshness_failed" in capsys.readouterr().err


def test_freshness_fetches_only_newest_marker_after_listing_all_pages(config, objects, tmp_path, monkeypatch):
    client, store = objects
    now = datetime.now(timezone.utc)
    markers = [marker_for(config, now - timedelta(days=offset), offset) for offset in range(14)]
    for marker in markers:
        seed_checkpoint(config, client, marker)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.__dict__))
    monkeypatch.setattr(sys, "argv", ["pilot_backup.py", "freshness", "--config", str(path)])
    monkeypatch.setattr(backup, "clients", lambda *_: (client, store))
    assert backup.main() == 0
    assert [call[2] for call in client.calls if call[0] == "get"] == [
        backup.checkpoint_key(config, markers[0]["checkpoint_id"], "complete.json")
    ]


@pytest.mark.parametrize("mutation", ["invalid_content", "different_id", "stale_timestamp"])
def test_freshness_never_falls_back_past_broken_newest_marker(config, objects, tmp_path, monkeypatch, mutation):
    client, store = objects
    now = datetime.now(timezone.utc)
    older = marker_for(config, now - timedelta(hours=1), 1)
    newest = marker_for(config, now, 2)
    seed_checkpoint(config, client, older)
    seed_checkpoint(config, client, newest)
    key = backup.checkpoint_key(config, newest["checkpoint_id"], "complete.json")
    if mutation == "different_id":
        newest["checkpoint_id"] = older["checkpoint_id"]
    elif mutation == "stale_timestamp":
        newest["created_at"] = (now - timedelta(hours=27)).isoformat()
    data = b"invalid marker json" if mutation == "invalid_content" else backup.encoded(newest)
    client.seed(config.backup_bucket, key, data)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.__dict__))
    monkeypatch.setattr(sys, "argv", ["pilot_backup.py", "freshness", "--config", str(path)])
    monkeypatch.setattr(backup, "clients", lambda *_: (client, store))
    assert backup.main() == 1
    assert [call[2] for call in client.calls if call[0] == "get"] == [key]


def test_freshness_refuses_malformed_completion_key_before_payload_download(config, objects):
    client, store = objects
    seed_checkpoint(config, client, marker_for(config, datetime.now(timezone.utc), 1))
    client.seed(config.backup_bucket, config.prefix + "/checkpoints/unreviewed/complete.json", b"{}")
    with pytest.raises(backup.BackupError, match="Unreviewed completion"):
        store.latest_marker()
    assert not any(call[0] == "get" for call in client.calls)


def test_backup_source_port_mismatch_fails_before_cloud_calls(config, objects, fake_engine):
    before = list(objects[0].calls)
    with pytest.raises(backup.BackupError, match="source identity"):
        perform(replace(config, source_port=5433), objects, fake_engine)
    assert objects[0].calls == before


def test_checkpoint_read_uses_approved_version_not_current_marker(config, objects, fake_engine):
    result = perform(config, objects, fake_engine)
    client, store = objects
    reference = result["complete_marker"]
    client.seed(config.backup_bucket, reference["key"], b"untrusted newer current marker")
    marker, actual = store.read_marker(reference["key"], reference)
    assert actual == reference
    assert marker["checkpoint_id"] == result["checkpoint_id"]
    with pytest.raises(backup.BackupError, match="checksum"):
        store.read_marker(reference["key"], {**reference, "sha256": "0" * 64})


def test_app_aws_credentials_are_never_a_backup_fallback(config, monkeypatch):
    for key in list(os.environ):
        if key.startswith("CL_BACKUP_WRITER_AWS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-app-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-app-secret")
    with pytest.raises(backup.BackupError, match="role-specific"):
        backup.clients(config, "writer")


def test_pg_hostaddr_override_cannot_retarget_backup(fake_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    with pytest.raises(backup.BackupError, match=r"PG\*"):
        with backup.postgres_environment(fake_engine, tmp_path):
            pytest.fail("Override must be rejected before a subprocess can connect")


def test_database_name_cannot_be_interpreted_as_libpq_connection_string(tmp_path):
    url = sa.engine.URL.create("postgresql+psycopg", username="synthetic", host="127.0.0.1",
                               database="host=203.0.113.1 dbname=unreviewed")
    engine = sa.create_engine(url)
    try:
        with pytest.raises(backup.BackupError, match="plain reviewed"):
            with backup.postgres_environment(engine, tmp_path):
                pytest.fail("Connection-string-shaped database name must never reach pg_restore")
    finally:
        engine.dispose()


def test_checkpoint_cannot_reference_another_checkpoint_database(config):
    marker = marker_for(config, datetime.now(timezone.utc), 1)
    other = marker_for(config, datetime.now(timezone.utc), 2)
    marker["database"] = other["database"]
    with pytest.raises(backup.BackupError, match="outside"):
        backup.validate_marker(marker, config)


def test_iam_templates_keep_writer_read_only_on_source_and_retention_away_from_photos():
    root = MODULE_PATH.parent
    writer = json.loads((root / "writer-policy.template.json").read_text())
    retention = json.loads((root / "retention-policy.template.json").read_text())
    for statement in writer["Statement"]:
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        assert not any("Delete" in action for action in actions)
        if "s3:PutObject" in actions:
            assert set(statement["Resource"]) == {
                "arn:aws:s3:::__BACKUP_BUCKET__/courierlifts-backups/v1/checkpoints/*",
                "arn:aws:s3:::__BACKUP_BUCKET__/courierlifts-backups/v1/photos/*",
            }
            assert statement["Condition"]["StringEquals"]["s3:if-none-match"] == "*"
        resources = statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]]
        assert "arn:aws:s3:::__BACKUP_BUCKET__/courierlifts-backups/v1/*" not in resources
        assert not any("recovery-receipts" in resource or "legacy" in resource for resource in resources)
    deletion = [statement for statement in retention["Statement"] if statement["Action"] == "s3:DeleteObjectVersion"]
    assert len(deletion) == 1
    assert deletion[0]["Resource"].endswith("/courierlifts-backups/v1/checkpoints/*")
    settings = json.loads((root / "bucket-settings.json").read_text())
    assert settings["LifecycleConfiguration"]["Rules"] == []
    assert settings["VersioningConfiguration"]["Status"] == "Enabled"
