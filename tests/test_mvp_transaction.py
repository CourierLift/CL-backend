from fastapi.testclient import TestClient

from backend.main import app
from backend.quote_engine import PRICING_ENGINE_VERSION
from backend.services.object_storage import get_object_storage


SENDER_EMAIL = "sender-a@example.com"
COURIER_B_EMAIL = "courier-b@example.com"
COURIER_C_EMAIL = "courier-c@example.com"
PASSWORD = "secure-pass"


def register(client, *, email, role, transportation_mode=None):
    payload = {"email": email, "password": PASSWORD, "role": role}
    if transportation_mode:
        payload["transportation_mode"] = transportation_mode
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def login(client, email):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_full_cross_user_mvp_transaction_survives_fresh_sessions(client):
    sender = register(client, email=SENDER_EMAIL, role="customer")
    courier_b = register(
        client,
        email=COURIER_B_EMAIL,
        role="courier",
        transportation_mode="car",
    )
    register(
        client,
        email=COURIER_C_EMAIL,
        role="courier",
        transportation_mode="suv",
    )
    sender_headers = login(client, SENDER_EMAIL)
    courier_b_headers = login(client, COURIER_B_EMAIL)
    courier_c_headers = login(client, COURIER_C_EMAIL)

    payload = {
        "origin": "100 Main Street",
        "destination": "200 Oak Avenue",
        "vehicle": "car",
        "item_type": "standard",
        "weight_kg": 8,
        "quantity": 2,
        "length_in": 24,
        "width_in": 18,
        "height_in": 14,
        "weather": "clear",
        "traffic": "medium",
        "surge": 1.0,
        "delivery_requirements": [],
    }

    quote = client.post("/quote/estimate", json=payload)
    assert quote.status_code == 200, quote.text

    created = client.post(
        "/orders/create_compat",
        json=payload,
        headers=sender_headers,
    )
    assert created.status_code == 201, created.text
    order = created.json()
    order_id = order["id"]
    assert order["user_id"] == sender["id"]
    assert order["status"] == "pending"
    assert order["price"] == quote.json()["price_total"]
    assert order["pricing_engine_version"] == PRICING_ENGINE_VERSION
    assert order["pricing_snapshot"]["customer_total"] == order["price"]

    available_b = client.get("/orders/available", headers=courier_b_headers)
    available_c = client.get("/orders/available", headers=courier_c_headers)
    assert order_id in [item["id"] for item in available_b.json()]
    assert order_id in [item["id"] for item in available_c.json()]

    claim_b = client.post(f"/orders/{order_id}/claim", headers=courier_b_headers)
    claim_c = client.post(f"/orders/{order_id}/claim", headers=courier_c_headers)
    assert claim_b.status_code == 200
    assert claim_b.json()["assigned_courier_id"] == courier_b["id"]
    assert claim_c.status_code == 409

    # Simulate sender and courier reloading on independent sessions/devices.
    with TestClient(app) as sender_device, TestClient(app) as courier_device:
        sender_device_headers = login(sender_device, SENDER_EMAIL)
        courier_device_headers = login(courier_device, COURIER_B_EMAIL)

        sender_assigned = sender_device.get(
            f"/orders/{order_id}",
            headers=sender_device_headers,
        )
        courier_assigned = courier_device.get(
            f"/orders/{order_id}",
            headers=courier_device_headers,
        )
        assert sender_assigned.status_code == 200
        assert sender_assigned.json() == courier_assigned.json()
        assert sender_assigned.json()["status"] == "assigned"

        picked_up = courier_device.patch(
            f"/orders/{order_id}/status",
            json={"status": "picked_up"},
            headers=courier_device_headers,
        )
        assert picked_up.status_code == 200

        in_transit = courier_device.patch(
            f"/orders/{order_id}/status",
            json={"status": "in_transit"},
            headers=courier_device_headers,
        )
        assert in_transit.status_code == 200

        no_proof = courier_device.patch(
            f"/orders/{order_id}/status",
            json={"status": "delivered"},
            headers=courier_device_headers,
        )
        assert no_proof.status_code == 409

        proof = courier_device.post(
            f"/orders/{order_id}/proof",
            headers=courier_device_headers,
            files={"file": ("proof.jpg", b"mvp-delivery-proof", "image/jpeg")},
        )
        assert proof.status_code == 201, proof.text

        delivered = courier_device.patch(
            f"/orders/{order_id}/status",
            json={"status": "delivered"},
            headers=courier_device_headers,
        )
        assert delivered.status_code == 200
        assert delivered.json()["completed_at"] is not None

    # Start fresh authenticated sessions again after completion.
    with TestClient(app) as fresh_sender, TestClient(app) as fresh_courier:
        fresh_sender_headers = login(fresh_sender, SENDER_EMAIL)
        fresh_courier_headers = login(fresh_courier, COURIER_B_EMAIL)

        sender_detail = fresh_sender.get(
            f"/orders/{order_id}",
            headers=fresh_sender_headers,
        )
        courier_detail = fresh_courier.get(
            f"/orders/{order_id}",
            headers=fresh_courier_headers,
        )
        assert sender_detail.status_code == 200
        assert courier_detail.status_code == 200
        assert sender_detail.json() == courier_detail.json()

        final = sender_detail.json()
        assert final["status"] == "delivered"
        assert final["user_id"] == sender["id"]
        assert final["assigned_courier_id"] == courier_b["id"]
        assert final["proof"] is not None
        assert final["proof"]["storage_key"] == proof.json()["storage_key"]
        assert final["pricing_engine_version"] == PRICING_ENGINE_VERSION
        assert final["pricing_snapshot"] == order["pricing_snapshot"]

        sender_history = fresh_sender.get("/orders/mine", headers=fresh_sender_headers)
        courier_history = fresh_courier.get("/orders/assigned", headers=fresh_courier_headers)
        assert order_id in [item["id"] for item in sender_history.json()]
        assert order_id in [item["id"] for item in courier_history.json()]
        assert next(item for item in sender_history.json() if item["id"] == order_id)["status"] == "delivered"
        assert next(item for item in courier_history.json() if item["id"] == order_id)["status"] == "delivered"

    get_object_storage().delete(proof.json()["storage_key"])
