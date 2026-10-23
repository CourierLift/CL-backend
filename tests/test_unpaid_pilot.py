from hashlib import sha256
from io import BytesIO

import pytest
from pydantic import ValidationError

from backend.services.object_storage import LocalObjectStorage, S3ObjectStorage, get_object_storage
from backend.settings import Settings, settings
from tests.test_delivery_proof import create_and_claim_in_transit, submit_proof
from tests.image_helpers import photo_bytes


def test_unpaid_production_uses_no_google_and_binds_sample_quote(client, user_factory, monkeypatch):
    customer = user_factory(role="customer")
    monkeypatch.setattr(settings, "CL_APP_ENV", "production")
    monkeypatch.setattr(settings, "CL_UNPAID_PILOT", True)
    monkeypatch.setattr(settings, "CL_REGISTRATION_MODE", "manual-invite")
    async def forbidden(**kwargs):
        pytest.fail("Unpaid pilot must never call Google")
    monkeypatch.setattr("backend.orders.google_route_distance", forbidden)
    body = {"origin": "Fictional shop A, Midland", "destination": "Fictional dropoff B, Midland", "vehicle": "car"}
    quote = client.post("/quote/estimate", json=body, headers=customer["headers"]).json()
    assert quote["estimated"] is True
    assert quote["distance_source"] == "unpaid_pilot_sample"
    headers = {**customer["headers"], "Idempotency-Key": "unpaid-test-attempt"}
    booking = {**body, "quote_id": quote["quote_id"]}
    first = client.post("/orders/create_compat", json=booking, headers=headers)
    replay = client.post("/orders/create_compat", json=booking, headers=headers)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert first.json()["id"] == replay.json()["id"]
    assert first.json()["price"] == quote["price_total"]
    assert client.get("/pilot-info").json() == {
        "unpaid": True, "registration": "manual-invite", "payments_enabled": False,
        "payouts_enabled": False, "location_sharing_enabled": False,
        "quote_binding": True, "proof_retrieval": True,
    }
    assert client.post("/auth/register", json={"email": "stranger@example.com", "password": "secure-pass", "role": "customer"}).status_code == 403


def test_unpaid_settings_keep_production_database_and_storage_safety():
    valid = dict(CL_APP_ENV="production", CL_UNPAID_PILOT=True,
                 CL_REGISTRATION_MODE="manual-invite", CL_SECRET_KEY="x" * 48,
                 CL_DATABASE_URL="postgresql://user:dummy@localhost/pilot",
                 CL_OBJECT_STORAGE_BACKEND="s3", CL_S3_BUCKET="fictional-pilot",
                 CL_GOOGLE_MAPS_API_KEY=None, CL_FRONTEND_ORIGIN="https://courierlifts.com")
    assert Settings(**valid).CL_UNPAID_PILOT is True
    for change in ({"CL_DATABASE_URL": "sqlite://"}, {"CL_OBJECT_STORAGE_BACKEND": "local"},
                   {"CL_SECRET_KEY": "short"}, {"CL_REGISTRATION_MODE": "public"}):
        with pytest.raises(ValidationError):
            Settings(**{**valid, **change})


def test_proof_photo_is_private_and_survives_new_storage_instance(client, user_factory, tmp_path):
    from backend.main import app
    storage = LocalObjectStorage(str(tmp_path))
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        customer, courier, order = create_and_claim_in_transit(client, user_factory)
        other_customer = user_factory(role="customer")
        other_courier = user_factory(role="courier", transportation_mode="car")
        uploaded = submit_proof(client, order["id"], courier["headers"], photo_bytes(orientation=6))
        assert uploaded.status_code == 201
        url = f"/orders/{order['id']}/proof"
        app.dependency_overrides[get_object_storage] = lambda: LocalObjectStorage(str(tmp_path))
        assert client.get(url).status_code == 401
        for other in (other_customer, other_courier):
            assert client.get(url, headers=other["headers"]).status_code == 403
        for authorized in (customer, courier):
            viewed = client.get(url, headers=authorized["headers"])
            assert viewed.status_code == 200
            assert len(viewed.content) == uploaded.json()["size_bytes"]
            assert viewed.headers["cache-control"] == "private, no-store"
            assert sha256(viewed.content).hexdigest() == uploaded.json()["sha256"]
        storage._path(uploaded.json()["storage_key"]).write_bytes(b"tampered")
        assert client.get(url, headers=customer["headers"]).status_code == 503
        storage.delete(uploaded.json()["storage_key"])
        assert client.get(url, headers=customer["headers"]).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_s3_read_uses_private_bucket_and_closes_stream():
    # No external calls or credentials: exercises the actual S3 adapter contract.
    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "fictional-private-bucket"
    stream = BytesIO(b"sample-image")
    class FakeClient:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "fictional-private-bucket", "Key": "proofs/1/photo.png"}
            return {"Body": stream}
    storage.client = FakeClient()
    assert storage.read("proofs/1/photo.png", 100) == b"sample-image"
    assert stream.closed


def test_production_readiness_rejects_unmigrated_database(client, monkeypatch):
    from backend.database import engine
    from sqlalchemy import inspect, text
    monkeypatch.setattr(settings, "CL_APP_ENV", "production")
    # CI applies Alembic before pytest, while an empty local fixture has no
    # revision table. Exercise both states explicitly and restore CI metadata.
    had_version_table = inspect(engine).has_table("alembic_version")
    with engine.begin() as connection:
        versions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) if had_version_table else []
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    assert client.get("/ready").status_code == 503
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0004_pricing_snapshot')"))
    try:
        assert client.get("/ready").status_code == 503
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='0005_quotes_idempotency'"))
        assert client.get("/ready").status_code == 200
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
            if had_version_table:
                connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
                for version in versions:
                    connection.execute(text("INSERT INTO alembic_version VALUES (:version)"), {"version": version})
