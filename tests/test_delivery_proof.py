from backend.models import DeliveryProof
from backend.services.object_storage import get_object_storage


def create_and_claim_in_transit(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    order = client.post(
        "/orders/create_compat",
        headers=customer["headers"],
        json={
            "origin": "100 Main Street",
            "destination": "200 Oak Avenue",
            "vehicle": "car",
            "item_type": "standard",
            "weight_kg": 2,
        },
    ).json()
    client.post(f"/orders/{order['id']}/claim", headers=courier["headers"])
    client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "picked_up"},
        headers=courier["headers"],
    )
    client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "in_transit"},
        headers=courier["headers"],
    )
    return customer, courier, order


def submit_proof(client, order_id, headers, content=b"proof-image-bytes"):
    return client.post(
        f"/orders/{order_id}/proof",
        headers=headers,
        files={"file": ("proof.jpg", content, "image/jpeg")},
    )


def test_delivery_cannot_complete_without_proof(client, user_factory):
    _, courier, order = create_and_claim_in_transit(client, user_factory)

    response = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "delivered"},
        headers=courier["headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Delivery proof is required before completion"


def test_only_assigned_courier_can_submit_proof(client, user_factory):
    customer, courier, order = create_and_claim_in_transit(client, user_factory)
    other = user_factory(role="courier", transportation_mode="car")

    customer_attempt = submit_proof(client, order["id"], customer["headers"])
    other_attempt = submit_proof(client, order["id"], other["headers"])

    assert customer_attempt.status_code == 403
    assert other_attempt.status_code == 403
    assert other_attempt.json()["detail"] == "Only the assigned courier may submit proof"


def test_proof_must_be_submitted_in_transit(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    order = client.post(
        "/orders/create_compat",
        headers=customer["headers"],
        json={"origin": "A", "destination": "B", "vehicle": "car", "item_type": "standard"},
    ).json()
    client.post(f"/orders/{order['id']}/claim", headers=courier["headers"])

    response = submit_proof(client, order["id"], courier["headers"])

    assert response.status_code == 409
    assert "in transit" in response.json()["detail"].lower()


def test_proof_is_durable_metadata_and_allows_completion(client, user_factory):
    _, courier, order = create_and_claim_in_transit(client, user_factory)
    content = b"canonical-proof-image"

    uploaded = submit_proof(client, order["id"], courier["headers"], content=content)

    assert uploaded.status_code == 201, uploaded.text
    proof = uploaded.json()
    assert proof["order_id"] == order["id"]
    assert proof["uploaded_by_user_id"] == courier["user"]["id"]
    assert proof["content_type"] == "image/jpeg"
    assert proof["size_bytes"] == len(content)
    assert len(proof["sha256"]) == 64
    assert proof["storage_key"].startswith(f"proofs/{order['id']}/")

    from backend.database import SessionLocal

    with SessionLocal() as db:
        row = db.query(DeliveryProof).filter(DeliveryProof.order_id == order["id"]).one()
        assert row.storage_key == proof["storage_key"]
        assert not hasattr(row, "data")

    duplicate = submit_proof(client, order["id"], courier["headers"])
    assert duplicate.status_code == 409

    delivered = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "delivered"},
        headers=courier["headers"],
    )
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "delivered"

    get_object_storage().delete(proof["storage_key"])
