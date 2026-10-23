"""Recovery failures are tested against a versioned in-memory S3 contract.

No real credentials, account operations, or network requests are made here.
These tests do not certify the eventual hosted bucket or IAM configuration.
"""

from hashlib import sha256
import importlib.util
from io import BytesIO
from pathlib import Path

from botocore.exceptions import ClientError
import pytest


_path = Path(__file__).resolve().parents[1] / "ops" / "pilot-storage" / "proof_objects.py"
_spec = importlib.util.spec_from_file_location("pilot_proof_objects", _path)
proofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proofs)

BUCKET = "fictional-private-pilot-bucket"
OWNER = "000000000000"
DATA = b"fictional-proof-bytes"


def metadata(data=DATA, number=1):
    return {"id": number, "order_id": number, "uploaded_by_user_id": 2,
            "storage_key": f"proofs/{number}/sample.jpg", "content_type": "image/jpeg",
            "size_bytes": len(data), "sha256": sha256(data).hexdigest(),
            "created_at": "2026-10-22T00:00:00+00:00"}


def source_manifest(records=None):
    return {"format_version": 1, "sqlite_sha256": "a" * 64,
            "proofs": records if records is not None else [metadata()]}


class VersionedS3:
    def __init__(self):
        self.versions = {}
        self.current = {}
        self.calls = []
        self.streams = []
        self.versioning = "Enabled"

    def add_version(self, key, data, *, content_type="image/jpeg", encryption="AES256", version=None):
        version = version or f"version-{len(self.versions) + 1}"
        self.versions[(key, version)] = (data, content_type, encryption)
        self.current[key] = version
        return version

    def _record(self, method, request):
        assert request["Bucket"] == BUCKET
        assert request["ExpectedBucketOwner"] == OWNER
        self.calls.append((method, request))

    def get_bucket_versioning(self, **request):
        self._record("get_bucket_versioning", request)
        return {"Status": self.versioning}

    def get_object(self, **request):
        self._record("get_object", request)
        key = request["Key"]
        version = request.get("VersionId", self.current.get(key))
        data, content_type, encryption = self.versions[(key, version)]
        body = BytesIO(data)
        self.streams.append(body)
        return {"VersionId": version, "ContentLength": len(data), "ContentType": content_type,
                "ServerSideEncryption": encryption, "Body": body}

    def put_object(self, **request):
        self._record("put_object", request)
        assert request["IfNoneMatch"] == "*"
        assert request["ServerSideEncryption"] == "AES256"
        if request["Key"] in self.current:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        version = self.add_version(request["Key"], request["Body"], content_type=request["ContentType"])
        return {"VersionId": version}


def capture(client, source=None):
    return proofs.capture_current(client, source or source_manifest(), bucket=BUCKET,
                                  expected_account_id=OWNER)


def make_bundle(tmp_path, records=None, blobs=None):
    root = tmp_path / "proof-files"
    root.mkdir()
    for record, blob in zip(records or [metadata()], blobs or [DATA]):
        path = root / record["storage_key"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return root


def upload(client, source, root, *, approved=True):
    return proofs.upload_bundle(client, source, root, bucket=BUCKET,
                                expected_account_id=OWNER, allow_object_writes=approved)


def test_capture_pins_exact_bytes_and_database_checkpoint():
    client = VersionedS3()
    version = client.add_version(metadata()["storage_key"], DATA)
    checkpoint = capture(client)
    assert checkpoint["source_database_sha256"] == "a" * 64
    assert checkpoint["proof_count"] == 1
    assert checkpoint["proofs"] == [{**metadata(), "object_version_id": version}]
    assert all(stream.closed for stream in client.streams)
    assert all(call[0] != "put_object" for call in client.calls)


def test_upload_is_explicit_and_checks_all_local_proofs_before_any_remote_call(tmp_path):
    client = VersionedS3()
    second = metadata(b"different-proof", number=2)
    records = [metadata(), second]
    root = make_bundle(tmp_path, records, [DATA, b"corrupt"])
    with pytest.raises(proofs.ProofStorageError, match="explicit approved"):
        upload(client, source_manifest(records), root, approved=False)
    with pytest.raises(proofs.ProofStorageError, match="Local proof bytes"):
        upload(client, source_manifest(records), root)
    assert client.calls == []


def test_upload_preserves_keys_and_retry_does_not_overwrite(tmp_path):
    client = VersionedS3()
    root = make_bundle(tmp_path)
    first = upload(client, source_manifest(), root)
    second = upload(client, source_manifest(), root)
    assert first["proofs"] == second["proofs"]
    assert len(client.versions) == 1
    assert client.current == {metadata()["storage_key"]: first["proofs"][0]["object_version_id"]}


def test_existing_collision_with_different_bytes_stops_without_overwrite(tmp_path):
    client = VersionedS3()
    version = client.add_version(metadata()["storage_key"], b"newer-different-data")
    with pytest.raises(proofs.ProofStorageError, match="Object bytes differ"):
        upload(client, source_manifest(), make_bundle(tmp_path))
    assert len(client.versions) == 1
    assert client.current[metadata()["storage_key"]] == version


def test_original_version_restores_after_current_object_changes(tmp_path):
    client = VersionedS3()
    original = client.add_version(metadata()["storage_key"], DATA)
    checkpoint = capture(client)
    client.add_version(metadata()["storage_key"], b"later-changed-current-object")
    assert proofs.verify_pinned(client, checkpoint, expected_account_id=OWNER) == 1
    destination = tmp_path / "recovered"
    assert proofs.restore_local(client, checkpoint, destination, expected_account_id=OWNER) == 1
    assert (destination / metadata()["storage_key"]).read_bytes() == DATA
    assert client.calls[-1][1]["VersionId"] == original
    assert not any(method == "put_object" for method, _ in client.calls)


def test_corrupt_restore_publishes_no_partial_directory(tmp_path):
    client = VersionedS3()
    version = client.add_version(metadata()["storage_key"], DATA)
    checkpoint = capture(client)
    client.versions[(metadata()["storage_key"], version)] = (b"corrupted", "image/jpeg", "AES256")
    destination = tmp_path / "recovered"
    with pytest.raises(proofs.ProofStorageError, match="Object bytes differ"):
        proofs.restore_local(client, checkpoint, destination, expected_account_id=OWNER)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
    assert all(stream.closed for stream in client.streams)


def test_restore_never_erases_existing_directory(tmp_path):
    client = VersionedS3()
    client.add_version(metadata()["storage_key"], DATA)
    checkpoint = capture(client)
    destination = tmp_path / "already-exists"
    destination.mkdir()
    marker = destination / "keep"
    marker.write_text("preserved")
    count = len(client.calls)
    with pytest.raises(proofs.ProofStorageError, match="must not already exist"):
        proofs.restore_local(client, checkpoint, destination, expected_account_id=OWNER)
    assert marker.read_text() == "preserved"
    assert len(client.calls) == count


@pytest.mark.parametrize("change,reason", [
    ({"version": "null"}, "VersionId"),
    ({"encryption": "aws:kms"}, "SSE-S3"),
    ({"content_type": "text/html"}, "content type"),
])
def test_wrong_provider_metadata_is_not_accepted_as_durable(change, reason):
    client = VersionedS3()
    client.add_version(metadata()["storage_key"], DATA, **change)
    with pytest.raises(proofs.ProofStorageError, match=reason):
        capture(client)
    assert all(stream.closed for stream in client.streams)


def test_versioning_disabled_stops_before_upload(tmp_path):
    client = VersionedS3()
    client.versioning = "Suspended"
    with pytest.raises(proofs.ProofStorageError, match="must be Enabled"):
        upload(client, source_manifest(), make_bundle(tmp_path))
    assert [method for method, _ in client.calls] == ["get_bucket_versioning"]


@pytest.mark.parametrize("key", ["../escape", "proofs/../escape", "proofs//photo.jpg", "proofs/a\\..\\b"])
def test_unsafe_keys_are_rejected_before_remote_access(key):
    client = VersionedS3()
    record = {**metadata(), "storage_key": key}
    with pytest.raises(proofs.ProofStorageError, match="safe relative path"):
        capture(client, source_manifest([record]))
    assert client.calls == []


def test_symlink_local_photo_is_rejected_before_upload(tmp_path):
    client = VersionedS3()
    root = make_bundle(tmp_path)
    photo = root / metadata()["storage_key"]
    photo.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(DATA)
    photo.symlink_to(outside)
    with pytest.raises(proofs.ProofStorageError, match="Symlinks"):
        upload(client, source_manifest(), root)
    assert client.calls == []


def test_checkpoint_wrong_owner_or_missing_version_fails_before_read():
    client = VersionedS3()
    client.add_version(metadata()["storage_key"], DATA)
    checkpoint = capture(client)
    count = len(client.calls)
    with pytest.raises(proofs.ProofStorageError, match="different AWS account"):
        proofs.verify_pinned(client, checkpoint, expected_account_id="111111111111")
    checkpoint["proofs"][0]["object_version_id"] = "null"
    with pytest.raises(proofs.ProofStorageError, match="immutable S3 VersionId"):
        proofs.verify_pinned(client, checkpoint, expected_account_id=OWNER)
    assert len(client.calls) == count


def test_empty_checkpoint_is_recorded_as_zero_not_a_photo_durability_test():
    client = VersionedS3()
    checkpoint = capture(client, source_manifest([]))
    assert checkpoint["proof_count"] == 0
    assert proofs.verify_pinned(client, checkpoint, expected_account_id=OWNER) == 0
    assert [method for method, _ in client.calls] == ["get_bucket_versioning"]
