"""Real PostgreSQL16 + age recovery drill, with synthetic data and fake S3 only.

Opt in with CL_BACKUP_TEST_ADMIN_URL (loopback /postgres only),
CL_BACKUP_TEST_PG_BIN and CL_BACKUP_TEST_AGE_BIN. Never accepts a hosted URL.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest
import sqlalchemy as sa

from test_pilot_backup import backup, FakeS3
from test_postgres_cutover import source_factory  # Existing synthetic all-table fixture.


@pytest.fixture
def real_age(tmp_path):
    binary = os.environ.get("CL_BACKUP_TEST_AGE_BIN")
    if not binary:
        pytest.skip("Opt-in native age test binary is not configured")
    keygen = str(Path(binary).with_name("age-keygen"))
    identity = tmp_path / "synthetic-age-identity.txt"
    result = subprocess.run([keygen, "--output", str(identity)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    identity.chmod(0o600)
    public = subprocess.run([keygen, "--y", str(identity)], capture_output=True, text=True, timeout=10)
    assert public.returncode == 0
    return backup.Age(binary), identity, public.stdout.strip()


@pytest.fixture
def native_pair(source_factory, real_age, tmp_path):
    url = os.environ.get("CL_BACKUP_TEST_ADMIN_URL")
    binaries = os.environ.get("CL_BACKUP_TEST_PG_BIN")
    if not url or not binaries:
        pytest.skip("Opt-in isolated PostgreSQL16 backup/restore URL/tools are not configured")
    parsed = sa.engine.make_url(url)
    if (parsed.get_backend_name() != "postgresql" or parsed.host not in {"127.0.0.1", "localhost", "::1"}
            or parsed.database != "postgres" or parsed.query or any(key.startswith("PG") for key in os.environ)):
        pytest.fail("Native backup drill requires an explicit loopback /postgres admin URL without overrides")
    parsed = parsed.set(drivername="postgresql+psycopg")
    admin = sa.create_engine(parsed, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    suffix = uuid.uuid4().hex[:12]
    names = [f"pilot_backup_source_{suffix}", f"pilot_backup_restore_{suffix}"]
    engines = []
    try:
        with admin.connect() as connection:
            for name in names:
                connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
        engines = [backup.cutover.target_engine(parsed.set(database=name).render_as_string(hide_password=False), parsed.host, name)
                   for name in names]
        source, photos = source_factory(current=True, history=True)
        bundle = tmp_path / "source-bundle"
        backup.cutover.snapshot(source, photos, bundle)
        backup.cutover.migrate_empty_target(engines[0])
        backup.cutover.import_bundle(engines[0], bundle)
        config = backup.Config(source_host=parsed.host, source_port=parsed.port or 5432, source_database=names[0], source_bucket="synthetic-proofs",
                               source_owner="111111111111", backup_bucket="synthetic-backups", backup_owner="111111111111",
                               age_recipient=real_age[2], recovery_owner="Synthetic Recovery Owner")
        client = FakeS3()
        manifest, _ = backup.cutover.verify_bundle(bundle)
        for proof in manifest["proofs"]:
            client.seed(config.source_bucket, proof["storage_key"], (photos / proof["storage_key"]).read_bytes(),
                        content_type=proof["content_type"])
        store = backup.Store(client, config.backup_bucket, config.backup_owner, config.prefix)
        def another_target():
            name = f"pilot_backup_restore_{uuid.uuid4().hex[:12]}"
            with admin.connect() as connection:
                connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
            names.append(name)
            target = backup.cutover.target_engine(parsed.set(database=name).render_as_string(hide_password=False), parsed.host, name)
            engines.append(target)
            return target
        yield config, engines[0], engines[1], client, store, backup.Postgres(binaries), real_age[0], real_age[1], another_target
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as connection:
            for name in names:
                connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_real_age_encrypts_and_authenticates_ciphertext(real_age, tmp_path):
    age, identity, recipient = real_age
    plain, cipher, recovered = tmp_path / "plain", tmp_path / "cipher.age", tmp_path / "recovered"
    plain.write_bytes(b"synthetic private database and photograph bytes")
    age.encrypt(plain, cipher, recipient)
    assert plain.read_bytes() not in cipher.read_bytes()
    age.decrypt(cipher, recovered, identity)
    assert recovered.read_bytes() == plain.read_bytes()
    data = cipher.read_bytes()
    cipher.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(backup.BackupError, match="authenticated"):
        age.decrypt(cipher, tmp_path / "corrupt-result", identity)
    assert not (tmp_path / "corrupt-result").exists()


def test_native_exported_snapshot_incremental_encrypted_backup_and_complete_restore(native_pair, tmp_path, monkeypatch):
    config, source, target, client, store, postgres, age, identity, another_target = native_pair
    real_run = subprocess.run
    def checked_run(command, *args, **kwargs):
        result = real_run(command, *args, **kwargs)
        if command[0] == postgres.restore:
            assert result.returncode == 0, result.stderr.decode()
        return result
    monkeypatch.setattr(backup.subprocess, "run", checked_run)
    def concurrent_write():
        with source.begin() as connection:
            connection.execute(sa.text("UPDATE public.reward_events SET points=333 WHERE id=101"))
    result = backup.run_backup(config, source, client, store, age, postgres, after_snapshot=concurrent_write)
    with source.begin() as connection:
        connection.execute(sa.text("UPDATE public.reward_events SET points=444 WHERE id=101"))
    source_gets = len([call for call in client.calls if call[:2] == ("get", config.source_bucket)])
    second = backup.run_backup(config, source, client, store, age, postgres)
    assert result["checkpoint_id"] != second["checkpoint_id"]
    assert len([call for call in client.calls if call[:2] == ("get", config.source_bucket)]) == source_gets == 1
    for (bucket, key, _), record in client.objects.items():
        if bucket == config.backup_bucket and key.endswith(".age"):
            assert record["data"].startswith(b"age-encryption.org/v1\n")
            assert b"synthetic.customer@example.com" not in record["data"]
    with pytest.raises(backup.BackupError, match="checksum"):
        backup.restore(config, result["checkpoint_id"], store, age, postgres, target, identity,
                       tmp_path / "wrong-reference-photos", tmp_path / "wrong-reference-receipt",
                       complete_reference={**result["complete_marker"], "sha256": "0" * 64})
    backup.assert_empty_target(target)
    client.seed(config.backup_bucket, result["complete_marker"]["key"], b"untrusted newer current marker")
    receipt = backup.restore(config, result["checkpoint_id"], store, age, postgres, target, identity,
                             tmp_path / "recovered-photos", tmp_path / "verified-receipt.json",
                             complete_reference=result["complete_marker"])
    assert receipt["kind"] == "cl-pilot-verified-restore"
    assert receipt["tables"] == result["tables"]
    assert receipt["photos"]["count"] == 1
    assert receipt["proofs"][0]["order_id"] == 82
    with source.connect() as connection:
        assert connection.scalar(sa.text("SELECT points FROM public.reward_events WHERE id=101")) == 444
    with target.connect() as connection:
        # pg_dump and the manifest BOTH saw the exported snapshot before the
        # concurrent source mutation, not two different transaction snapshots.
        assert connection.scalar(sa.text("SELECT points FROM public.reward_events WHERE id=101")) == -6
        assert connection.scalar(sa.text("SELECT price FROM public.orders WHERE id=82")) == 27.63
        assert connection.scalar(sa.text("SELECT quote_id FROM public.orders WHERE id=82")) == "synthetic-quote-55"
        assert connection.scalar(sa.text("SELECT status FROM public.orders WHERE id=82")) == "delivered"
    assert json.loads((tmp_path / "verified-receipt.json").read_text()) == receipt
    assert backup.verify_database(target, receipt) == receipt["tables"]
    with pytest.raises((backup.BackupError, backup.cutover.CutoverError)):
        backup.restore(config, result["checkpoint_id"], store, age, postgres, target, identity,
                       tmp_path / "another-photo-root", tmp_path / "another-receipt",
                       complete_reference=result["complete_marker"])
    newer_target = another_target()
    second_receipt = backup.restore(config, second["checkpoint_id"], store, age, postgres, newer_target, identity,
                                    tmp_path / "newer-photos", tmp_path / "newer-receipt.json",
                                    complete_reference=second["complete_marker"])
    assert second_receipt["tables"] == second["tables"]
    with newer_target.connect() as connection:
        assert connection.scalar(sa.text("SELECT points FROM public.reward_events WHERE id=101")) == 444


def test_native_corrupt_photo_never_restores_database_or_writes_success_receipt(native_pair, tmp_path):
    config, source, target, client, store, postgres, age, identity, _ = native_pair
    result = backup.run_backup(config, source, client, store, age, postgres)
    marker, _ = store.markers()[0]
    photo = marker["photos"][0]
    record = client.objects[(config.backup_bucket, photo["key"], photo["version_id"])]
    record["data"] = b"x" * len(record["data"])
    with pytest.raises(backup.BackupError):
        backup.restore(config, result["checkpoint_id"], store, age, postgres, target, identity,
                       tmp_path / "unusable-photos", tmp_path / "must-not-exist.json",
                       complete_reference=result["complete_marker"])
    backup.assert_empty_target(target)
    assert not (tmp_path / "must-not-exist.json").exists()
    assert not (tmp_path / "unusable-photos").exists()


def test_native_restore_refuses_live_name_and_existing_output_without_cloud_reads(native_pair, tmp_path):
    config, source, target, client, store, postgres, age, identity, _ = native_pair
    before = len(client.calls)
    with pytest.raises(backup.BackupError, match="live source"):
        backup.restore(config, "unused", store, age, postgres, source, identity,
                       tmp_path / "photos", tmp_path / "receipt", complete_reference={})
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(backup.BackupError, match="new path"):
        backup.restore(config, "unused", store, age, postgres, target, identity,
                       occupied, tmp_path / "receipt", complete_reference={})
    assert len(client.calls) == before


def test_native_snapshot_succeeds_with_separate_read_only_database_role(native_pair):
    config, source, _target, client, store, postgres, age, _identity, _ = native_pair
    role = "pilot_backup_reader_" + uuid.uuid4().hex[:12]
    with source.begin() as connection:
        connection.execute(sa.text(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'))
        connection.execute(sa.text(f'GRANT CONNECT ON DATABASE "{config.source_database}" TO "{role}"'))
        connection.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(sa.text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
        connection.execute(sa.text(f'GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"'))
        connection.execute(sa.text(f'ALTER ROLE "{role}" SET default_transaction_read_only=on'))
    reader = backup.cutover.target_engine(source.url.set(username=role).render_as_string(hide_password=False),
                                          config.source_host, config.source_database)
    try:
        result = backup.run_backup(config, reader, client, store, age, postgres)
        assert result["complete"] is True
        assert result["proof_count"] == 1
        with reader.connect() as connection:
            assert connection.scalar(sa.text("SHOW default_transaction_read_only")) == "on"
        # The grant boundary must still reject writes if a client overrides its
        # default transaction setting; read-only is not merely a client promise.
        with pytest.raises(sa.exc.DBAPIError):
            with reader.begin() as connection:
                connection.execute(sa.text("SET TRANSACTION READ WRITE"))
                connection.execute(sa.text("UPDATE public.orders SET price=0 WHERE id=82"))
        with source.connect() as connection:
            assert connection.scalar(sa.text("SELECT price FROM public.orders WHERE id=82")) == 27.63
    finally:
        reader.dispose()
        with source.begin() as connection:
            connection.execute(sa.text(f'DROP OWNED BY "{role}"'))
            connection.execute(sa.text(f'DROP ROLE "{role}"'))
