"""Synthetic preservation and failure tests; never use customer or hosted data."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import sqlalchemy as sa

from backend.database import engine
from database_safety import validate_disposable_database_url
from scripts import postgres_cutover as cutover
from scripts import start_sqlite_rollback as rollback


PHOTO = b"\x89PNG\r\n\x1a\nsynthetic-preservation-photo"
CREATED = datetime(2026, 10, 20, 10, 15, 30, 123456, tzinfo=timezone.utc)


@pytest.fixture
def source_factory(tmp_path):
    def create(*, current=False, history=False):
        source = tmp_path / ("current.sqlite3" if current else "legacy.sqlite3")
        environment = {**os.environ, "CL_APP_ENV": "test", "CL_DATABASE_URL": f"sqlite:///{source}"}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", cutover.HEAD if current else cutover.LEGACY_HEAD],
            cwd=cutover.ROOT, env=environment, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        source_engine = sa.create_engine(f"sqlite:///{source}")
        metadata = sa.MetaData()
        metadata.reflect(bind=source_engine)
        with source_engine.begin() as connection:
            connection.execute(metadata.tables["users"].insert(), [
                {"id": 7, "email": "synthetic.customer@example.com", "password_hash": "$2b$synthetic-unchanged", "role": "customer", "created_at": CREATED},
                {"id": 42, "email": "synthetic.courier@example.com", "password_hash": "$2b$synthetic-courier", "role": "courier", "created_at": CREATED},
                {"id": 99, "email": "synthetic.admin@example.com", "password_hash": "$2b$synthetic-admin", "role": "admin", "created_at": CREATED},
            ])
            connection.execute(metadata.tables["courier_profiles"].insert(), {
                "id": 17, "user_id": 42, "transportation_mode": "car", "max_weight_lb": 150.5,
                "max_length_in": 48.0, "max_width_in": 40.0, "max_height_in": 32.0,
                "max_volume_cu_ft": 35.25, "capabilities": ["fragile", "refrigerated"], "is_active": True,
            })
            price_snapshot = {"original_total": 27.63, "conditions": {"surge": 1.15}, "memo": "Apostrophe's café"}
            if current:
                connection.execute(metadata.tables["quotes"].insert(), {
                    "id": "synthetic-quote-55", "user_id": 7, "kind": "address", "details_hash": "a" * 64,
                    "pricing_conditions": {"weather": "clear"}, "pricing_snapshot": price_snapshot,
                    "price_total": 27.63, "eta_min": 14, "distance_miles": 4.562,
                    "distance_estimated": True, "distance_source": "pilot_sample",
                    "tier": "standard", "pricing_engine_version": "synthetic-v1",
                    "created_at": CREATED, "expires_at": CREATED,
                })
            order = {
                "id": 82, "user_id": 7, "assigned_courier_id": 42, "origin": "Synthetic pickup",
                "destination": "Synthetic dropoff", "pickup_lat": 31.99, "pickup_lng": -102.08,
                "dropoff_lat": None, "dropoff_lng": None, "vehicle": "car", "item_type": "standard",
                "delivery_requirements": ["fragile"], "quantity": 2, "weight_lb": 3.5,
                "length_in": 12.0, "width_in": 8.0, "height_in": 6.0, "price": 27.63,
                "eta_min": 14, "distance_miles": 4.562, "distance_estimated": True,
                "distance_source": "pilot_sample", "weather": "clear", "traffic": "medium",
                "surge_multiplier": 1.15, "pricing_engine_version": "synthetic-v1",
                "pricing_snapshot": price_snapshot, "status": "delivered", "created_at": CREATED,
                "assigned_at": CREATED, "completed_at": CREATED,
            }
            if current:
                order.update(quote_id="synthetic-quote-55", idempotency_key="preserved-key", request_hash="b" * 64)
            connection.execute(metadata.tables["orders"].insert(), order)
            second = {**order, "id": 83, "status": "in_transit", "completed_at": None}
            if current:
                second.update(quote_id=None, idempotency_key=None, request_hash=None)
            connection.execute(metadata.tables["orders"].insert(), second)
            connection.execute(metadata.tables["delivery_proofs"].insert(), {
                "id": 90, "order_id": 82, "uploaded_by_user_id": 42,
                "storage_key": "proofs/82/original.png", "content_type": "image/png",
                "size_bytes": len(PHOTO), "sha256": hashlib.sha256(PHOTO).hexdigest(), "created_at": CREATED,
            })
            connection.execute(metadata.tables["reward_events"].insert(), {
                "id": 101, "user_id": 7, "order_id": 82, "type": "adjust", "points": -6,
                "reason": "Synthetic preservation adjustment", "created_at": CREATED,
            })
            if not history:
                connection.execute(sa.text("DROP TABLE alembic_version"))
        source_engine.dispose()
        photos = tmp_path / "photos"
        (photos / "proofs/82").mkdir(parents=True, exist_ok=True)
        (photos / "proofs/82/original.png").write_bytes(PHOTO)
        (photos / "unreferenced.jpg").write_bytes(b"preserve unreferenced files too")
        return source, photos
    return create


def test_snapshot_preserves_legacy_rows_all_photo_files_and_original_source(source_factory, tmp_path):
    source, photos = source_factory()
    before = cutover.sha256_file(source)
    bundle = tmp_path / "bundle"
    manifest = cutover.snapshot(source, photos, bundle)
    verified, rows = cutover.verify_bundle(bundle)
    assert verified == manifest
    assert cutover.sha256_file(source) == before
    assert manifest["source_schema"] == "legacy_0004"
    assert manifest["source_revision"] is None
    assert len(manifest["files"]) == 2
    assert rows["users"][0]["password_hash"] == "$2b$synthetic-unchanged"
    assert rows["orders"][0]["price"] == 27.63
    assert rows["orders"][0]["pricing_snapshot"]["memo"] == "Apostrophe's café"
    assert rows["orders"][0]["quote_id"] is None
    assert rows["orders"][1]["status"] == "in_transit"
    assert rows["users"][0]["created_at"] == "2026-10-20T10:15:30.123456+00:00"
    assert (bundle / "proof-files/proofs/82/original.png").read_bytes() == PHOTO
    assert (bundle.stat().st_mode & 0o777) == 0o700
    assert ((bundle / "source.sqlite3").stat().st_mode & 0o777) == 0o600


def test_snapshot_includes_committed_wal_data(source_factory, tmp_path):
    source, photos = source_factory()
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("UPDATE reward_events SET points=37 WHERE id=101")
        connection.commit()
        assert Path(str(source) + "-wal").exists()
        cutover.snapshot(source, photos, tmp_path / "bundle")
        _, rows = cutover.verify_bundle(tmp_path / "bundle")
        assert rows["reward_events"][0]["points"] == 37


@pytest.mark.parametrize("failure", ["missing", "corrupt", "symlink", "extra_column", "extra_table", "foreign_key"])
def test_snapshot_fails_closed_on_unpreservable_source(source_factory, tmp_path, failure):
    source, photos = source_factory()
    proof = photos / "proofs/82/original.png"
    if failure == "missing":
        proof.unlink()
    elif failure == "corrupt":
        proof.write_bytes(b"wrong content")
    elif failure == "symlink":
        (photos / "linked-file").symlink_to(proof)
    else:
        with sqlite3.connect(source) as connection:
            if failure == "extra_column":
                connection.execute("ALTER TABLE users ADD COLUMN unreviewed_secret TEXT")
            elif failure == "extra_table":
                connection.execute("CREATE TABLE unreviewed_records(id INTEGER PRIMARY KEY)")
            else:
                connection.execute("UPDATE delivery_proofs SET order_id=99999")
    with pytest.raises(cutover.CutoverError):
        cutover.snapshot(source, photos, tmp_path / "bundle")
    assert not (tmp_path / "bundle/manifest.json").exists()


@pytest.mark.parametrize("artifact", ["database", "proof"])
def test_bundle_verification_detects_tampering(source_factory, tmp_path, artifact):
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    cutover.snapshot(source, photos, bundle)
    if artifact == "database":
        with sqlite3.connect(bundle / "source.sqlite3") as connection:
            connection.execute("UPDATE orders SET price=0")
    else:
        (bundle / "proof-files/proofs/82/original.png").write_bytes(b"damaged")
    with pytest.raises(cutover.CutoverError):
        cutover.verify_bundle(bundle)


def test_snapshot_refuses_to_overwrite_existing_bundle(source_factory, tmp_path):
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    cutover.snapshot(source, photos, bundle)
    with pytest.raises(FileExistsError):
        cutover.snapshot(source, photos, bundle)


def test_sqlite_rollback_restores_exact_database_and_all_proofs_to_new_paths(source_factory, tmp_path):
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    manifest = cutover.snapshot(source, photos, bundle)
    restored_db = tmp_path / "restored.sqlite3"
    restored_photos = tmp_path / "restored-photos"
    result = cutover.restore_sqlite(bundle, restored_db, restored_photos)
    assert result["sqlite_restored"] is True
    assert cutover.sha256_file(restored_db) == manifest["sqlite_sha256"]
    assert cutover.file_manifest(restored_photos) == manifest["files"]
    assert cutover.read_sqlite_snapshot(restored_db)[1] == cutover.verify_bundle(bundle)[1]
    with pytest.raises(cutover.CutoverError, match="must both be new"):
        cutover.restore_sqlite(bundle, restored_db, restored_photos)


@pytest.mark.parametrize("key", ["../escape", "/absolute", "proofs/../escape", "a\\b", "./a", "a//b", ""])
def test_proof_paths_cannot_escape_or_alias_the_bundle(key):
    with pytest.raises(cutover.CutoverError):
        cutover.safe_key(key)


@pytest.mark.parametrize("url", [
    "postgresql://fake:fake@db.example.com/app",
    "postgresql://fake:fake@localhost/production",
    "postgresql://fake:fake@localhost/pilot_test?host=db.example.com",
    "sqlite:///preserved.sqlite3",
    "sqlite:///file:preserved?uri=true",
])
def test_destructive_pytest_fixture_rejects_preserved_and_hosted_databases(url):
    with pytest.raises(ValueError):
        validate_disposable_database_url(url)


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "postgresql://fake@127.0.0.1/pilot_test", "postgresql://fake@localhost/courier_lifts_test"])
def test_destructive_pytest_fixture_accepts_only_recognized_disposable_targets(url):
    validate_disposable_database_url(url)


@pytest.mark.parametrize("url,host,database", [
    ("postgresql://fake@unexpected.example/db", "expected.example", "db"),
    ("postgresql://fake@expected.example/other", "expected.example", "db"),
    ("postgresql://fake@expected.example/db?host=unexpected.example", "expected.example", "db"),
    ("sqlite:///:memory:", "expected.example", "db"),
])
def test_cutover_requires_exact_target_identity_and_no_libpq_overrides(url, host, database):
    with pytest.raises(cutover.CutoverError):
        cutover.target_engine(url, host, database)


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPORT"])
def test_cutover_and_destructive_tests_reject_inherited_libpq_overrides(monkeypatch, variable):
    monkeypatch.setenv(variable, "unreviewed-value")
    with pytest.raises(cutover.CutoverError, match="Inherited libpq"):
        cutover.target_engine("postgresql://fake@127.0.0.1/pilot_test", "127.0.0.1", "pilot_test")
    with pytest.raises(ValueError):
        validate_disposable_database_url("postgresql://fake@127.0.0.1/pilot_test")


@pytest.fixture
def migrated_postgres():
    if engine.dialect.name != "postgresql":
        pytest.skip("requires disposable loopback PostgreSQL")
    validate_disposable_database_url(engine.url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    cutover.migrate_empty_target(engine)
    yield engine
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))


@pytest.mark.parametrize("current", [False, True])
def test_full_migration_and_atomic_import_preserve_every_field_and_sequences(migrated_postgres, source_factory, tmp_path, current):
    source, photos = source_factory(current=current, history=current)
    bundle = tmp_path / "bundle"
    manifest = cutover.snapshot(source, photos, bundle)
    result = cutover.import_bundle(migrated_postgres, bundle)
    assert result["tables"] == manifest["tables"]
    assert cutover.verify_target(migrated_postgres, bundle)["data_matches_bundle"] is True
    with migrated_postgres.connect() as connection:
        assert connection.scalar(sa.text("SELECT nextval('public.users_id_seq')")) == 100
        assert connection.scalar(sa.text("SELECT nextval('public.orders_id_seq')")) == 84
        assert connection.scalar(sa.text("SELECT password_hash FROM users WHERE id=7")) == "$2b$synthetic-unchanged"
        assert connection.scalar(sa.text("SELECT status::text FROM orders WHERE id=83")) == "in_transit"
        if current:
            assert connection.scalar(sa.text("SELECT idempotency_key FROM orders WHERE id=82")) == "preserved-key"
    with pytest.raises(cutover.CutoverError, match="contains application data"):
        cutover.import_bundle(migrated_postgres, bundle)
    assert cutover.verify_target(migrated_postgres, bundle)["data_matches_bundle"] is True


def test_failed_import_rolls_back_all_rows_and_sequence_restarts(migrated_postgres, source_factory, tmp_path, monkeypatch):
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    cutover.snapshot(source, photos, bundle)
    original = cutover.database_rows
    def mismatch(connection, tables):
        rows = original(connection, tables)
        rows["orders"][0]["price"] = -1
        return rows
    monkeypatch.setattr(cutover, "database_rows", mismatch)
    with pytest.raises(cutover.CutoverError, match="entire import transaction rolled back"):
        cutover.import_bundle(migrated_postgres, bundle)
    assert set(cutover.preflight(migrated_postgres)["row_counts"].values()) == {0}
    with migrated_postgres.connect() as connection:
        assert connection.scalar(sa.text("SELECT nextval('public.users_id_seq')")) == 1
        assert connection.scalar(sa.text("SELECT nextval('public.orders_id_seq')")) == 1


def test_migration_refuses_existing_schema_and_target_verifier_detects_new_writes(migrated_postgres, source_factory, tmp_path):
    with pytest.raises(cutover.CutoverError, match="empty public schema"):
        cutover.migrate_empty_target(migrated_postgres)
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    cutover.snapshot(source, photos, bundle)
    cutover.import_bundle(migrated_postgres, bundle)
    with migrated_postgres.begin() as connection:
        connection.execute(sa.text("UPDATE orders SET price=price+1 WHERE id=82"))
    with pytest.raises(cutover.CutoverError, match="differs from the frozen source"):
        cutover.verify_target(migrated_postgres, bundle)


def test_import_rejects_incorrect_target_migration_history(migrated_postgres, source_factory, tmp_path):
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    cutover.snapshot(source, photos, bundle)
    with migrated_postgres.begin() as connection:
        connection.execute(sa.text("UPDATE alembic_version SET version_num='0004_pricing_snapshot'"))
    with pytest.raises(cutover.CutoverError, match="reviewed 0005 head"):
        cutover.import_bundle(migrated_postgres, bundle)


@pytest.fixture
def synthetic_old_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "old-checkout"
    checkout.mkdir()
    (checkout / "marker.txt").write_text("reviewed fixture source\n")
    (checkout / ".gitignore").write_text(".env\n")
    for arguments in (["init", "-q"], ["add", "marker.txt", ".gitignore"],
                      ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "Synthetic fixture"]):
        subprocess.run(["git", "-C", str(checkout), *arguments], check=True, capture_output=True)
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(rollback, "EXPECTED_OLD_COMMIT", revision)
    return checkout


@pytest.mark.parametrize("override", [
    {"CL_DATABASE_URL": "postgresql://fake@db.example.com/app"},
    {"DATABASE_URL": "postgresql://fake@db.example.com/app"},
    {"CL_APP_ENV": "production"},
    {"CL_SECRET_KEY": ""},
    {"CL_OBJECT_STORAGE_BACKEND": "s3"},
    {"PYTHONPATH": "/unreviewed/code"},
])
def test_rollback_rejects_non_original_or_unsafe_environment_snapshots(tmp_path, override):
    path = tmp_path / "original-environment.json"
    path.write_text(json.dumps({"CL_APP_ENV": "development", "CL_SECRET_KEY": "fictional-preserved-secret", **override}))
    path.chmod(0o600)
    with pytest.raises(cutover.CutoverError):
        rollback.original_environment(path)


def test_rollback_rejects_environment_snapshot_exposed_to_other_users(tmp_path):
    path = tmp_path / "original-environment.json"
    path.write_text(json.dumps({"CL_SECRET_KEY": "fictional-preserved-secret"}))
    path.chmod(0o644)
    with pytest.raises(cutover.CutoverError, match="private regular file"):
        rollback.original_environment(path)


def test_rollback_checkout_requires_clean_exact_source_and_ignores_git_overrides(synthetic_old_checkout, monkeypatch):
    checkout = synthetic_old_checkout
    monkeypatch.setenv("GIT_DIR", "/unreviewed/override")
    rollback.verify_old_checkout(checkout)
    (checkout / "marker.txt").write_text("modified\n")
    with pytest.raises(cutover.CutoverError, match="exact approved old commit"):
        rollback.verify_old_checkout(checkout)
    (checkout / "marker.txt").write_text("reviewed fixture source\n")
    (checkout / ".env").write_text("CL_DATABASE_URL=postgresql://unreviewed\n")
    with pytest.raises(cutover.CutoverError, match="exact approved old commit"):
        rollback.verify_old_checkout(checkout)


def test_prestart_rollback_restores_before_launch_and_removes_candidate_configuration(source_factory, synthetic_old_checkout, tmp_path, monkeypatch):
    source, photos = source_factory()
    bundle = tmp_path / "bundle"
    manifest = cutover.snapshot(source, photos, bundle)
    environment_file = tmp_path / "original-environment.json"
    environment_file.write_text(json.dumps({
        "CL_APP_ENV": "development", "CL_SECRET_KEY": "fictional-preserved-original-secret",
        "CL_FRONTEND_ORIGIN": "https://courierlifts-web.onrender.com",
    }))
    environment_file.chmod(0o600)
    monkeypatch.setenv("CL_DATABASE_URL", "postgresql://candidate@db.example.com/app")
    monkeypatch.setenv("CL_UNPAID_PILOT", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fictional-candidate-key")
    monkeypatch.setenv("PGHOSTADDR", "unreviewed")
    monkeypatch.setenv("PYTHONPATH", "/candidate/code")
    environment, state = rollback.prepare_rollback(synthetic_old_checkout, bundle, environment_file, tmp_path)
    assert environment["CL_DATABASE_URL"] == f"sqlite:///{state / 'courier_lifts.db'}"
    assert environment["CL_OBJECT_STORAGE_PATH"] == str(state / "object_storage")
    assert environment["CL_SECRET_KEY"] == "fictional-preserved-original-secret"
    assert environment["CL_OBJECT_STORAGE_BACKEND"] == "local"
    assert not {"CL_UNPAID_PILOT", "AWS_ACCESS_KEY_ID", "PGHOSTADDR", "PYTHONPATH"} & environment.keys()
    assert cutover.sha256_file(state / "courier_lifts.db") == manifest["sqlite_sha256"]
    assert cutover.file_manifest(state / "object_storage") == manifest["files"]


def test_old_code_rollback_refuses_new_quote_schema_bundle(source_factory, synthetic_old_checkout, tmp_path):
    source, photos = source_factory(current=True, history=True)
    bundle = tmp_path / "bundle"
    cutover.snapshot(source, photos, bundle)
    environment_file = tmp_path / "original-environment.json"
    environment_file.write_text(json.dumps({"CL_APP_ENV": "test", "CL_SECRET_KEY": "fictional-preserved-original-secret"}))
    environment_file.chmod(0o600)
    with pytest.raises(cutover.CutoverError, match="original legacy SQLite bundle"):
        rollback.prepare_rollback(synthetic_old_checkout, bundle, environment_file, tmp_path)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_rollback_checks_actual_file_bytes_when_git_index_hides_changes(synthetic_old_checkout, flag):
    checkout = synthetic_old_checkout
    subprocess.run(["git", "-C", str(checkout), "update-index", flag, "marker.txt"], check=True)
    (checkout / "marker.txt").write_text("unreviewed source hidden by an index flag\n")
    assert subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"], text=True) == ""
    with pytest.raises(cutover.CutoverError, match="file content differs from the pinned tree"):
        rollback.verify_old_checkout(checkout)


@pytest.mark.parametrize("symlink", [False, True])
def test_rollback_refuses_dotenv_injection_from_outside_the_checkout(synthetic_old_checkout, symlink):
    checkout = synthetic_old_checkout
    ancestor_dotenv = checkout.parent / ".env"
    if symlink:
        settings_file = checkout.parent / "unreviewed-environment.txt"
        settings_file.write_text("CL_FRONTEND_ORIGIN=https://unreviewed.example\n")
        ancestor_dotenv.symlink_to(settings_file)
    else:
        ancestor_dotenv.write_text("CL_FRONTEND_ORIGIN=https://unreviewed.example\n")
    with pytest.raises(cutover.CutoverError, match="ancestor .env"):
        rollback.verify_old_checkout(checkout)
