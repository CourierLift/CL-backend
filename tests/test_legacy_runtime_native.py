"""Actual pinned Python 3.13.4 old-backend recovery, with synthetic data only.

Opt in with CL_LEGACY_TEST_RUNTIME pointing to an artifact produced by
scripts/prepare_release_runtime.py, plus the loopback PG/age drill variables.
No runtime verifier is mocked. Old application processes receive only SQLite
and local photos, and their outbound socket connections are prohibited.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import bcrypt
import pytest
import sqlalchemy as sa

from scripts import postgres_cutover as cutover
from scripts import prepare_release_runtime as runtime_builder
from scripts import seal_legacy_recovery as seal
from scripts import start_pilot as start
from scripts import start_sqlite_rollback as rollback
from test_pilot_backup_native import real_age, native_pair
from test_postgres_cutover import PHOTO, source_factory


SYNTHETIC_PASSWORD = "Synthetic-Restore-Only-Password-42!"
ORIGINAL_SECRET = "synthetic-original-preserved-secret-for-local-drill"


@pytest.fixture
def reviewed_runtime():
    configured = os.environ.get("CL_LEGACY_TEST_RUNTIME")
    if not configured:
        pytest.skip("Actual prepared legacy runtime is not configured")
    runtime = Path(configured).resolve(strict=True)
    receipt = runtime_builder.verify_runtime(runtime)
    assert receipt["legacy_commit"] == rollback.EXPECTED_OLD_COMMIT
    assert receipt["inventory"]["python"] == "3.13.4"
    return runtime, receipt


def test_actual_legacy_build_receipt_interpreter_packages_and_source(reviewed_runtime):
    runtime, receipt = reviewed_runtime
    assert receipt["inventory"]["packages"] == runtime_builder.expected_packages()
    assert len(receipt["inventory"]["packages"]) == 54
    assert receipt["legacy_lock_sha256"] == cutover.sha256_file(runtime_builder.LEGACY_LOCK)
    assert receipt["python_binary_sha256"] == cutover.sha256_file(runtime / "old-python/bin/python")
    assert receipt["age_binary_sha256"] == cutover.sha256_file(runtime / "bin/age")
    assert receipt["age_version"] == "1.3.2"
    rollback.verify_old_checkout(runtime / "old-checkout")


OLD_APP_PROBE = r'''
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

expected = json.loads(sys.stdin.read())
assert sys.version.split()[0] == "3.13.4"
assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
assert not any(key.startswith(("AWS_", "PG", "CL_RECOVERY_", "CL_BACKUP_", "CL_RETENTION_"))
               for key in os.environ)
assert os.environ["CL_DATABASE_URL"].startswith("sqlite:////")
assert "postgres" not in os.environ.get("DATABASE_URL", "").lower()

def forbidden_network(*args, **kwargs):
    raise AssertionError("Legacy app must not open an outbound socket")

socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
socket.create_connection = forbidden_network

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import engine
from backend.settings import settings

assert engine.dialect.name == "sqlite"
assert settings.CL_APP_ENV == "development"
assert settings.CL_OBJECT_STORAGE_BACKEND == "local"
assert settings.CL_SECRET_KEY == expected["original_secret"]
histories = {}
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 404
    for role, history_path in (("customer", "/orders/mine"), ("courier", "/orders/assigned")):
        login = client.post("/auth/login", json={
            "email": f"synthetic.{role}@example.com", "password": expected["password"],
        })
        assert login.status_code == 200, (role, login.status_code)
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        history = client.get(history_path, headers=headers)
        assert history.status_code == 200
        rows = {row["id"]: row for row in history.json()}
        assert set(rows) == {82, 83}
        assert rows[82]["status"] == "delivered"
        assert rows[83]["status"] == "in_transit"
        assert rows[82]["price"] == 27.63
        assert rows[82]["pricing_snapshot"]["original_total"] == 27.63
        detail = client.get("/orders/82", headers=headers)
        assert detail.status_code == 200
        order = detail.json()
        assert order["user_id"] == 7
        assert order["assigned_courier_id"] == 42
        assert order["price"] == 27.63
        assert order["pricing_snapshot"]["memo"] == "Apostrophe's café"
        assert order["proof"]["id"] == 90
        assert order["proof"]["order_id"] == 82
        assert order["proof"]["uploaded_by_user_id"] == 42
        assert order["proof"]["storage_key"] == "proofs/82/original.png"
        assert order["proof"]["sha256"] == expected["photo_sha256"]
        assert order["proof"]["size_bytes"] == expected["photo_size"]
        # Exact legacy behavior: bytes were stored locally, but this revision
        # exposes no GET proof endpoint. Do not mistake candidate behavior for
        # an old-code rollback guarantee.
        assert client.get("/orders/82/proof", headers=headers).status_code == 405
        histories[role] = {"order_ids": sorted(rows), "delivered_order": 82, "original_price": order["price"]}
    root = Path(settings.CL_OBJECT_STORAGE_PATH)
    proof = (root / "proofs/82/original.png").read_bytes()
    assert hashlib.sha256(proof).hexdigest() == expected["photo_sha256"]
    assert len(proof) == expected["photo_size"]
    assert (root / "unreferenced.jpg").read_bytes() == b"preserve unreferenced files too"
engine.dispose()
print(json.dumps({"python": sys.version.split()[0], "database_backend": "sqlite", "histories": histories,
                  "health_status": 200, "ready_status": 404, "legacy_photo_get_status": 405,
                  "proof_sha256": hashlib.sha256(proof).hexdigest(), "proof_size": len(proof),
                  "outbound_connections_allowed": False}, sort_keys=True))
'''


def test_actual_legacy_restore_auth_history_and_photos_survive_fresh_processes(
    reviewed_runtime, native_pair, source_factory, real_age, tmp_path, monkeypatch,
):
    runtime, _receipt = reviewed_runtime
    _config, source_pg, target, client, _store, _postgres, _age, identity, _another = native_pair
    source_sqlite, photos = source_factory()
    with sqlite3.connect(source_sqlite) as connection:
        for user_id in (7, 42, 99):
            password_hash = bcrypt.hashpw(SYNTHETIC_PASSWORD.encode(), bcrypt.gensalt()).decode()
            connection.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
    bundle = tmp_path / "frozen-legacy"
    manifest = cutover.snapshot(source_sqlite, photos, bundle)
    cutover.migrate_empty_target(target)
    cutover.import_bundle(target, bundle)
    original = tmp_path / "original-environment.json"
    original.write_text(json.dumps({"CL_APP_ENV": "development", "CL_SECRET_KEY": ORIGINAL_SECRET,
                                    "CL_FRONTEND_ORIGIN": "http://localhost:5173"}))
    original.chmod(0o600)
    cipher = tmp_path / "frozen-legacy.age"
    sealed = seal.seal(bundle, original, real_age[2], cipher, age_binary=str(runtime / "bin/age"))
    assert ORIGINAL_SECRET.encode() not in cipher.read_bytes()
    assert SYNTHETIC_PASSWORD.encode() not in cipher.read_bytes()
    key = "courierlifts-backups/v1/legacy/synthetic-frozen.age"
    version = client.seed("synthetic-backups", key, cipher.read_bytes())
    artifact = {"bucket": "synthetic-backups", "owner": "111111111111", "region": "us-west-2",
                "key": key, "version_id": version, "sha256": sealed["sha256"], "size_bytes": sealed["size_bytes"]}
    # Prove the complete startup boundary uses the independently pinned version.
    client.seed("synthetic-backups", key, b"changed current object must not be trusted")
    reference = {"format_version": 1, "kind": "cl-legacy-recovery-reference", "phase": "before-new-postgres-writes",
                 "legacy_commit": rollback.EXPECTED_OLD_COMMIT, "artifact": artifact,
                 "postgres_target": {"host": target.url.host, "port": target.url.port or 5432,
                                     "database": target.url.database}}
    monkeypatch.setenv("CL_RECOVERY_VERIFY_DATABASE_URL", target.url.render_as_string(hide_password=False))
    monkeypatch.setenv("CL_DATABASE_URL", source_pg.url.render_as_string(hide_password=False))
    monkeypatch.setenv("DATABASE_URL", source_pg.url.render_as_string(hide_password=False))
    monkeypatch.setenv("CL_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-candidate-secret")
    monkeypatch.setenv("CL_BACKUP_WRITER_AWS_SECRET_ACCESS_KEY", "synthetic-backup-secret")
    monkeypatch.setenv("CL_RELEASE_SHA", "a" * 40)
    environment, checkout, python = start.legacy_checkpoint(reference, client, identity, runtime, tmp_path)
    assert checkout == runtime / "old-checkout"
    assert python == runtime / "old-python/bin/python"
    assert environment["CL_SECRET_KEY"] == ORIGINAL_SECRET
    assert environment["CL_OBJECT_STORAGE_BACKEND"] == "local"
    assert environment["CL_DATABASE_URL"].startswith("sqlite:////")
    assert "DATABASE_URL" not in environment
    assert not any(key.startswith(("AWS_", "PG", "CL_RECOVERY_", "CL_BACKUP_")) for key in environment)
    restored = Path(environment["CL_DATABASE_URL"].removeprefix("sqlite:///"))
    assert cutover.sha256_file(restored) == manifest["sqlite_sha256"]
    assert cutover.file_manifest(Path(environment["CL_OBJECT_STORAGE_PATH"])) == manifest["files"]
    expected = {"original_secret": ORIGINAL_SECRET, "password": SYNTHETIC_PASSWORD,
                "photo_sha256": hashlib.sha256(PHOTO).hexdigest(), "photo_size": len(PHOTO)}
    results = []
    for _fresh_process in range(2):
        result = subprocess.run([str(python), "-B", "-c", OLD_APP_PROBE], cwd=checkout, env=environment,
                                input=json.dumps(expected), capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr
        results.append(json.loads(result.stdout))
        assert cutover.sha256_file(restored) == manifest["sqlite_sha256"]
        assert cutover.file_manifest(Path(environment["CL_OBJECT_STORAGE_PATH"])) == manifest["files"]
        rollback.verify_old_checkout(checkout)
    assert results[0] == results[1]
    assert results[1]["proof_sha256"] == hashlib.sha256(PHOTO).hexdigest()
    assert results[1]["histories"]["customer"]["original_price"] == 27.63
    assert results[1]["histories"]["courier"]["original_price"] == 27.63
    # A later source PostgreSQL write closes the legacy path even though the
    # reviewed old artifact and its frozen SQLite checkpoint remain available.
    with target.begin() as connection:
        connection.execute(sa.text("UPDATE public.reward_events SET points=points+1 WHERE id=101"))
    with pytest.raises(cutover.CutoverError, match="differs from the frozen source"):
        start.legacy_checkpoint(reference, client, identity, runtime, tmp_path)
