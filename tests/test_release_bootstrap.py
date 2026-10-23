"""Recovery boundary tests. Native tests opt in to synthetic loopback PG/age."""
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
import sqlalchemy as sa

from scripts import postgres_cutover as cutover
from scripts import recovery_artifacts as artifacts
from scripts import restore_durable_checkpoint as durable
from scripts import seal_legacy_recovery as seal
from scripts import start_pilot as start
from scripts import start_sqlite_rollback as rollback
from scripts import recovery_inputs
from scripts import run_recovery_job as job_runner
from test_pilot_backup import backup, FakeS3, PHOTO, PROOF
from test_pilot_backup_native import real_age, native_pair
from test_postgres_cutover import source_factory, synthetic_old_checkout


@pytest.fixture(autouse=True)
def shared_backup_module(monkeypatch):
    monkeypatch.setattr(artifacts, "backup_module", lambda: backup)


def pinned(client, data=b"synthetic encrypted payload", key="legacy/frozen.age"):
    version = client.seed("synthetic-backups", key, data)
    return {"bucket": "synthetic-backups", "owner": "111111111111", "region": "us-west-2",
            "key": key, "version_id": version, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def test_download_uses_recorded_version_even_if_current_key_changes(tmp_path):
    client = FakeS3()
    ref = pinned(client)
    client.seed(ref["bucket"], ref["key"], b"untrusted replacement current object")
    artifacts.download(client, ref, tmp_path / "download")
    assert (tmp_path / "download").read_bytes() == b"synthetic encrypted payload"
    assert (tmp_path / "download").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("field,value", [
    ("version_id", "null"), ("sha256", ""), ("owner", "unknown"),
    ("region", "us-east-1"), ("key", "../unsafe"), ("size_bytes", 0),
])
def test_unpinned_or_wrong_account_reference_is_refused_before_network(field, value, tmp_path):
    client = FakeS3()
    ref = pinned(client)
    client.calls.clear()
    with pytest.raises(cutover.CutoverError):
        artifacts.download(client, {**ref, field: value}, tmp_path / "download")
    assert client.calls == []


def test_download_detects_damaged_encrypted_artifact(tmp_path):
    client = FakeS3()
    ref = pinned(client)
    client.objects[(ref["bucket"], ref["key"], ref["version_id"])]["data"] = b"x" * ref["size_bytes"]
    with pytest.raises(cutover.CutoverError, match="checksum"):
        artifacts.download(client, ref, tmp_path / "bad")


@pytest.mark.parametrize("kind,name", [
    (tarfile.SYMTYPE, "bundle/link"), (tarfile.LNKTYPE, "bundle/hardlink"),
    (tarfile.REGTYPE, "../escaped"), (tarfile.REGTYPE, "/absolute"),
    (tarfile.REGTYPE, "bundle/../escaped"), (tarfile.REGTYPE, "unexpected.txt"),
    (tarfile.CHRTYPE, "bundle/device"),
])
def test_decrypted_tar_refuses_unsafe_entries(tmp_path, kind, name):
    path = tmp_path / "unsafe.tar"
    with tarfile.open(path, "w") as output:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "../../escaped"
        output.addfile(member, io.BytesIO())
    with pytest.raises(cutover.CutoverError, match="unsafe"):
        artifacts.extract_private_tar(path, tmp_path / "new")
    assert not (tmp_path.parent / "escaped").exists()


def test_recovery_secrets_do_not_reach_normal_app_environment(monkeypatch):
    for key in ("CL_RECOVERY_AWS_SECRET_ACCESS_KEY", "CL_BACKUP_DATABASE_URL", "CL_RETENTION_KEY",
                "CL_CUTOVER_DATABASE_URL", "PGHOSTADDR", "DATABASE_URL", "PYTHONPATH"):
        monkeypatch.setenv(key, "synthetic-secret")
    monkeypatch.setenv("CL_SECRET_KEY", "synthetic-app-signing-secret")
    monkeypatch.setenv("CL_DATABASE_URL", "postgresql://synthetic@localhost/current")
    env = artifacts.application_environment()
    assert env["CL_SECRET_KEY"] == "synthetic-app-signing-secret"
    assert env["CL_DATABASE_URL"].endswith("/current")
    assert not any(key.startswith(("CL_RECOVERY_", "CL_BACKUP_", "CL_RETENTION_", "CL_CUTOVER_", "PG")) for key in env)
    assert "PYTHONPATH" not in env and "DATABASE_URL" not in env


def test_serve_refuses_old_sqlite_without_importing_app():
    env = {**os.environ, "CL_DATABASE_URL": "sqlite:///old.db"}
    code = "import sys; from scripts import start_pilot; sys.argv=['start_pilot']; result=start_pilot.main(); assert 'backend.main' not in sys.modules; raise SystemExit(result)"
    result = subprocess.run([sys.executable, "-c", code], cwd=start.ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "before application import" in result.stderr


def test_wrong_render_commit_prevents_start(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("CL_RELEASE_SHA", "b" * 40)
    with pytest.raises(cutover.CutoverError, match="release SHA"):
        start.check_release_identity()


def test_temporary_render_secret_inputs_are_private_removed_and_not_in_child_environment(monkeypatch):
    monkeypatch.setenv("CL_RECOVERY_REFERENCE_JSON", '{"synthetic":"reference"}')
    monkeypatch.setenv("CL_RECOVERY_AGE_IDENTITY", "synthetic-test-identity-not-a-real-key")
    with recovery_inputs.materialized_inputs({
        "reference.json": (None, "CL_RECOVERY_REFERENCE_JSON", 65536),
        "identity.agekey": (None, "CL_RECOVERY_AGE_IDENTITY", 8192),
    }) as paths:
        observed = list(paths.values())
        assert all(path.is_file() and not path.stat().st_mode & 0o077 for path in observed)
        assert not observed[0].parent.stat().st_mode & 0o077
        assert paths["identity.agekey"].read_text() == "synthetic-test-identity-not-a-real-key"
        assert "CL_RECOVERY_AGE_IDENTITY" not in os.environ
        assert "CL_RECOVERY_REFERENCE_JSON" not in os.environ
    assert all(not path.exists() for path in observed)


def test_private_inputs_cleanup_on_failed_restore(monkeypatch):
    monkeypatch.setenv("CL_RECOVERY_AGE_IDENTITY", "synthetic-test-key")
    with pytest.raises(RuntimeError):
        with recovery_inputs.materialized_inputs({"key": (None, "CL_RECOVERY_AGE_IDENTITY", 8192)}) as paths:
            path = paths["key"]
            raise RuntimeError("synthetic failed restore")
    assert not path.exists() and "CL_RECOVERY_AGE_IDENTITY" not in os.environ


@pytest.mark.parametrize("value", ["", "x" * 33])
def test_invalid_environment_inputs_are_rejected_without_echoing_value(monkeypatch, value):
    monkeypatch.setenv("CL_RECOVERY_AGE_IDENTITY", value)
    with pytest.raises(cutover.CutoverError, match="size limit or is empty"):
        with recovery_inputs.materialized_inputs({"key": (None, "CL_RECOVERY_AGE_IDENTITY", 32)}):
            pytest.fail("Invalid secret input accepted")
    assert "CL_RECOVERY_AGE_IDENTITY" not in os.environ


def test_input_paths_refuse_world_readable_or_ambiguous_secret(tmp_path, monkeypatch):
    path = tmp_path / "identity"
    path.write_text("synthetic-test-key")
    path.chmod(0o644)
    with pytest.raises(cutover.CutoverError, match="private regular"):
        with recovery_inputs.materialized_inputs({"key": (path, "CL_RECOVERY_AGE_IDENTITY", 8192)}):
            pytest.fail("Public-readable input accepted")
    path.chmod(0o600)
    monkeypatch.setenv("CL_RECOVERY_AGE_IDENTITY", "a second synthetic value")
    with pytest.raises(cutover.CutoverError, match="Ambiguous"):
        with recovery_inputs.materialized_inputs({"key": (path, "CL_RECOVERY_AGE_IDENTITY", 8192)}):
            pytest.fail("Ambiguous input accepted")


def test_oneoff_job_uses_private_inputs_without_secrets_in_command_or_output(monkeypatch, capsys):
    job = {"checkpoint_id": "20261022T070000Z-0123456789abcdef", "complete_reference": {"synthetic": True},
           "target_host": "127.0.0.1", "target_database": "synthetic_recovery",
           "photo_bucket": "synthetic-recovery", "photo_owner": "111111111111", "release_sha": "a" * 40,
           "writers_stopped": True, "allow_writes": True, "acknowledge_disaster_checkpoint_rpo": False}
    monkeypatch.setenv("CL_RELEASE_SHA", "a" * 40)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("CL_BACKUP_CONFIG_JSON", '{"synthetic":"config"}')
    monkeypatch.setenv("CL_RECOVERY_JOB_JSON", json.dumps(job))
    secret = "synthetic-private-identity-never-print"
    monkeypatch.setenv("CL_RECOVERY_AGE_IDENTITY", secret)
    seen = []
    def child(args, **kwargs):
        for flag in ("--config", "--identity", "--complete-reference"):
            path = Path(args[args.index(flag) + 1])
            assert path.is_file() and not path.stat().st_mode & 0o077
            seen.append(path)
        assert secret not in " ".join(args)
        assert "CL_RECOVERY_AGE_IDENTITY" not in kwargs["env"]
        assert "CL_BACKUP_CONFIG_JSON" not in kwargs["env"]
        assert "CL_RECOVERY_JOB_JSON" not in kwargs["env"]
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(job_runner.subprocess, "run", child)
    assert job_runner.main() == 0
    assert len(seen) == 3 and all(not path.exists() for path in seen)
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize("variable", ["CL_RECOVERY_AGE_IDENTITY", "CL_BACKUP_CONFIG_JSON", "CL_RECOVERY_JOB_JSON"])
def test_oneoff_missing_inputs_fail_before_restore(variable, monkeypatch, capsys):
    for name in ("CL_RECOVERY_AGE_IDENTITY", "CL_BACKUP_CONFIG_JSON", "CL_RECOVERY_JOB_JSON"):
        monkeypatch.setenv(name, "synthetic-value-never-print")
    monkeypatch.delenv(variable)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setattr(job_runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("Restore started"))
    assert job_runner.main() == 1
    output = capsys.readouterr()
    assert "synthetic-value-never-print" not in output.out + output.err


def test_container_entry_removes_only_known_image_metadata_and_rejects_connection_override():
    script = start.ROOT / "ops/pilot-recovery/runtime_entry.py"
    clean = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env = {**clean, "PG_MAJOR": "16", "PG_VERSION": "16.15", "PGDATA": "/unused/image/path"}
    code = "import os; assert not any(k.startswith('PG') for k in os.environ); print('metadata stripped')"
    result = subprocess.run([sys.executable, str(script), sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and "metadata stripped" in result.stdout
    result = subprocess.run([sys.executable, str(script), sys.executable, "-c", code],
                            env={**env, "PGHOSTADDR": "unapproved"}, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and "overrides" in result.stderr


def test_legacy_start_refuses_missing_build_receipt_before_app_import(tmp_path):
    with pytest.raises(FileNotFoundError):
        start.release_runtime.verify_runtime(tmp_path)


class RecoveryS3(FakeS3):
    def get_public_access_block(self, **kwargs):
        assert kwargs["ExpectedBucketOwner"] == "111111111111"
        return {"PublicAccessBlockConfiguration": dict.fromkeys(
            ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"), True)}

    def list_object_versions(self, **kwargs):
        assert kwargs["ExpectedBucketOwner"] == "111111111111"
        return {"Versions": [{"Key": key, "VersionId": version}
                             for bucket, key, version in self.objects if bucket == kwargs["Bucket"]][:1]}


def test_recovery_never_overwrites_existing_photo_bucket():
    client = RecoveryS3()
    client.seed("synthetic-recovery", "proofs/existing.jpg", PHOTO)
    with pytest.raises(cutover.CutoverError, match="not empty"):
        durable.empty_photo_target(client, "synthetic-recovery", "111111111111", {"source", "backups"})
    assert not any(call[0] == "put" for call in client.calls)


def test_bootstrap_rejects_new_current_photo_version():
    client = RecoveryS3()
    version = client.seed("synthetic-recovery", PROOF["storage_key"], PHOTO)
    receipt = {"proofs": [PROOF], "photo_target": {
        "bucket": "synthetic-recovery", "owner": "111111111111", "region": "us-west-2",
        "objects": [{"key": PROOF["storage_key"], "version_id": version,
                     "sha256": PROOF["sha256"], "size_bytes": len(PHOTO)}]}}
    artifacts.verify_durable_photos(client, receipt, current_required=True)
    client.seed("synthetic-recovery", PROOF["storage_key"], b"different photo")
    with pytest.raises(cutover.CutoverError, match="Current photo"):
        artifacts.verify_durable_photos(client, receipt, current_required=True)


def test_native_legacy_seal_retrieval_and_restore_before_old_app_parameters(
    source_factory, synthetic_old_checkout, real_age, native_pair, tmp_path, monkeypatch
):
    _config, source_pg, _target, _client, _store, _postgres, age, identity, _another = native_pair
    # This test uses the synthetic source fixture. The separate native-build CI
    # smoke exercises the real pinned source, interpreter and package inventory.
    monkeypatch.setattr(start.release_runtime, "verify_runtime", lambda runtime: rollback.verify_old_checkout(runtime / "old-checkout"))
    # The native_pair fixture already imported a current-schema fixture. Use a
    # separate new PG target for this original legacy checkpoint comparison.
    target = _another()
    source_sqlite, photos = source_factory()
    bundle = tmp_path / "legacy-bundle"
    cutover.snapshot(source_sqlite, photos, bundle)
    cutover.migrate_empty_target(target)
    cutover.import_bundle(target, bundle)
    original = tmp_path / "original.json"
    original.write_text(json.dumps({"CL_APP_ENV": "development", "CL_SECRET_KEY": "synthetic-original-preserved-secret"}))
    original.chmod(0o600)
    cipher = tmp_path / "legacy.age"
    seal.seal(bundle, original, real_age[2], cipher, age_binary=age.binary)
    assert b"synthetic-original-preserved-secret" not in cipher.read_bytes()
    client = RecoveryS3()
    ref = pinned(client, cipher.read_bytes())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "old-checkout").symlink_to(synthetic_old_checkout, target_is_directory=True)
    (runtime / "old-python/bin").mkdir(parents=True)
    (runtime / "old-python/bin/python").symlink_to(sys.executable)
    (runtime / "bin").mkdir()
    (runtime / "bin/age").symlink_to(age.binary)
    reference = {"format_version": 1, "kind": "cl-legacy-recovery-reference", "phase": "before-new-postgres-writes",
                 "legacy_commit": rollback.EXPECTED_OLD_COMMIT, "artifact": ref,
                 "postgres_target": {"host": target.url.host, "port": target.url.port or 5432, "database": target.url.database}}
    monkeypatch.setenv("CL_RECOVERY_VERIFY_DATABASE_URL", target.url.render_as_string(hide_password=False))
    monkeypatch.setenv("CL_DATABASE_URL", source_pg.url.render_as_string(hide_password=False))
    monkeypatch.setenv("CL_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-candidate-secret")
    env, checkout, python = start.legacy_checkpoint(reference, client, identity, runtime, tmp_path)
    assert env["CL_DATABASE_URL"].startswith("sqlite:////")
    assert env["CL_SECRET_KEY"] == "synthetic-original-preserved-secret"
    assert env["CL_OBJECT_STORAGE_BACKEND"] == "local"
    assert not any(key.startswith(("AWS_", "PG", "CL_RECOVERY_")) for key in env)
    restored = Path(env["CL_DATABASE_URL"].removeprefix("sqlite:///"))
    assert cutover.sha256_file(restored) == cutover.verify_bundle(bundle)[0]["sqlite_sha256"]
    assert cutover.file_manifest(Path(env["CL_OBJECT_STORAGE_PATH"])) == cutover.file_manifest(photos)
    with target.begin() as connection:
        connection.execute(sa.text("UPDATE public.orders SET price=price+1 WHERE id=82"))
    with pytest.raises(cutover.CutoverError, match="differs from the frozen source"):
        start.legacy_checkpoint(reference, client, identity, runtime, tmp_path)


def test_native_fresh_postgres_checkpoint_to_durable_photos_and_start_gate(native_pair, tmp_path, monkeypatch):
    config, source, target, old_client, _store, postgres, age, identity, another = native_pair
    client = RecoveryS3()
    client.objects, client.current, client.counter = old_client.objects, old_client.current, old_client.counter
    store = backup.Store(client, config.backup_bucket, config.backup_owner, config.prefix)
    with source.begin() as connection:
        connection.execute(sa.text("UPDATE public.reward_events SET points=777 WHERE id=101"))
    checkpoint = backup.run_backup(config, source, client, store, age, postgres)
    ref = durable.publish_recovery(
        config, checkpoint["checkpoint_id"], checkpoint["complete_marker"], store, client, age, postgres,
        target, identity, photo_bucket="synthetic-recovery", photo_owner="111111111111",
        release_sha="a" * 40, source_engine=source,
    )
    artifacts.download(client, ref, tmp_path / "durable.json")
    receipt = json.loads((tmp_path / "durable.json").read_text())
    monkeypatch.setenv("CL_RELEASE_SHA", "a" * 40)
    monkeypatch.setenv("CL_DATABASE_URL", target.url.render_as_string(hide_password=False))
    monkeypatch.setenv("CL_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CL_S3_BUCKET", "synthetic-recovery")
    monkeypatch.setenv("CL_S3_REGION", "us-west-2")
    monkeypatch.delenv("CL_S3_ENDPOINT_URL", raising=False)
    env = start.recovered_postgres(receipt, client)
    assert env["CL_DATABASE_URL"] == target.url.render_as_string(hide_password=False)
    with target.connect() as connection:
        assert connection.scalar(sa.text("SELECT points FROM public.reward_events WHERE id=101")) == 777
    assert receipt["recovery_policy"] == "fresh-frozen-source"
    assert len(receipt["photo_target"]["objects"]) == 1
    # A fresh source write after the selected checkpoint must prevent publication
    # of any second start receipt. The restored target stays quarantined.
    with source.begin() as connection:
        connection.execute(sa.text("UPDATE public.reward_events SET points=888 WHERE id=101"))
    with pytest.raises(backup.BackupError, match="digests"):
        durable.publish_recovery(
            config, checkpoint["checkpoint_id"], checkpoint["complete_marker"], store, client, age, postgres,
            another(), identity, photo_bucket="synthetic-recovery-new", photo_owner="111111111111",
            release_sha="a" * 40, source_engine=source,
        )
    assert not any(bucket == "synthetic-recovery-new" for bucket, _key in client.current)
