"""Fictional local PostgreSQL restore fixture. Refuses any hosted database.

Run `seed manifest.json`, pg_dump/restore into a second pilot_* database, then
`verify manifest.json`. Use the same disposable object storage and signing key.
This verifies local database/photo continuity, not hosted S3 or Render backups.
"""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

url = urlparse(os.environ.get("CL_DATABASE_URL", ""))
if (url.scheme not in {"postgresql", "postgresql+psycopg"}
    or url.hostname != "127.0.0.1" or not url.path.startswith("/pilot_")
    or url.query or url.fragment or any(key.startswith("PG") for key in os.environ)):
    raise SystemExit("Requires an explicit loopback pilot_* database; refusing hosted data")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import SessionLocal
from backend.models import Order, Quote, DeliveryProof, User, CourierProfile, RewardEvent
from backend.schemas import RegisterIn
from backend.services.account_provisioning import provision_account
from PIL import Image

action, manifest_path = sys.argv[1:]
password = "fictional-local-only-password"
with Image.new("RGB", (32, 16), (31, 110, 180)) as fixture, BytesIO() as output:
    fixture.save(output, "PNG")
    image = output.getvalue()

with TestClient(app) as client:
    if action == "seed":
        with SessionLocal() as db:
            assert all(db.query(model).count() == 0 for model in (
                User, CourierProfile, Quote, Order, DeliveryProof, RewardEvent,
            )), "Seed requires an empty disposable database"
            for role in ("customer", "courier"):
                provision_account(db, RegisterIn(
                    email=f"restore-{role}@example.com", password=password, role=role,
                    transportation_mode="car", max_weight_lb=20, max_length_in=24,
                    max_width_in=18, max_height_in=18, max_volume_cu_ft=6,
                ))
    def login(role):
        response = client.post("/auth/login", json={"email": f"restore-{role}@example.com", "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}
    customer, courier = login("customer"), login("courier")
    if action == "seed":
        body = {"origin": "Fictional Midland store", "destination": "Fictional Midland delivery", "vehicle": "car", "weight_kg": 1}
        quote = client.post("/quote/estimate", json=body, headers=customer).json()
        body["quote_id"] = quote["quote_id"]
        key = "local-restore-attempt"
        response = client.post("/orders/create_compat", json=body, headers={**customer, "Idempotency-Key": key})
        assert response.status_code == 201, response.text
        order = response.json(); oid = order["id"]
        assert client.post(f"/orders/{oid}/claim", headers=courier).status_code == 200
        for status in ("picked_up", "in_transit"):
            assert client.patch(f"/orders/{oid}/status", json={"status": status}, headers=courier).status_code == 200
        proof = client.post(f"/orders/{oid}/proof", headers=courier, files={"file": ("fixture.png", image, "image/png")})
        assert proof.status_code == 201, proof.text
        assert client.patch(f"/orders/{oid}/status", json={"status": "delivered"}, headers=courier).status_code == 200
        manifest = {"order_id": oid, "quote_id": quote["quote_id"], "price": order["price"], "body": body, "key": key, "photo_sha256": proof.json()["sha256"]}
        Path(manifest_path).write_text(json.dumps(manifest, indent=2))
    else:
        manifest = json.loads(Path(manifest_path).read_text()); oid = manifest["order_id"]
    history = client.get("/orders/mine", headers=customer).json()
    assert len(history) == 1 and history[0]["id"] == oid
    assert history[0]["status"] == "delivered" and history[0]["price"] == manifest["price"]
    assert len(client.get("/orders/assigned", headers=courier).json()) == 1
    photo = client.get(f"/orders/{oid}/proof", headers=customer)
    assert photo.status_code == 200
    assert hashlib.sha256(photo.content).hexdigest() == manifest["photo_sha256"]
    replay = client.post("/orders/create_compat", json=manifest["body"], headers={**customer, "Idempotency-Key": manifest["key"]})
    assert replay.status_code == 200 and replay.json()["id"] == oid
    with SessionLocal() as db:
        assert db.query(Order).count() == db.query(Quote).count() == db.query(DeliveryProof).count() == 1
    print(json.dumps({"action": action, "order_id": oid, "price": manifest["price"], "orders": 1, "quotes": 1, "proofs": 1, "photo_sha256": manifest["photo_sha256"], "fresh_login": "PASS", "same_key_replay": "PASS"}))
