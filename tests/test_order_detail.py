from backend.auth_jwt import create_access_token, hash_password
from backend.database import SessionLocal
from backend.models import User, UserRole


def create_order(client, headers):
    response = client.post(
        "/orders/create_compat",
        headers=headers,
        json={
            "origin": "100 Main Street",
            "destination": "200 Oak Avenue",
            "vehicle": "car",
            "item_type": "fragile",
            "weight_kg": 3,
            "delivery_requirements": ["fragile"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_admin_headers():
    with SessionLocal() as db:
        admin = User(
            email="admin-detail@example.com",
            password_hash=hash_password("secure-pass"),
            role=UserRole.admin,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        token = create_access_token(admin.id, admin.role.value)
    return {"Authorization": f"Bearer {token}"}


def test_owner_assigned_courier_and_admin_share_authoritative_detail(client, user_factory):
    owner = user_factory(role="customer")
    courier = user_factory(
        role="courier",
        transportation_mode="car",
        capabilities=["fragile"],
    )
    order = create_order(client, owner["headers"])

    owner_pending = client.get(f"/orders/{order['id']}", headers=owner["headers"])
    assert owner_pending.status_code == 200
    assert owner_pending.json()["proof"] is None

    claim = client.post(f"/orders/{order['id']}/claim", headers=courier["headers"])
    assert claim.status_code == 200
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
    proof = client.post(
        f"/orders/{order['id']}/proof",
        headers=courier["headers"],
        files={"file": ("proof.jpg", b"authoritative-proof", "image/jpeg")},
    )
    assert proof.status_code == 201
    delivered = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "delivered"},
        headers=courier["headers"],
    )
    assert delivered.status_code == 200

    owner_detail = client.get(f"/orders/{order['id']}", headers=owner["headers"])
    courier_detail = client.get(f"/orders/{order['id']}", headers=courier["headers"])
    admin_detail = client.get(f"/orders/{order['id']}", headers=create_admin_headers())

    assert owner_detail.status_code == 200
    assert courier_detail.status_code == 200
    assert admin_detail.status_code == 200
    assert owner_detail.json() == courier_detail.json() == admin_detail.json()

    detail = owner_detail.json()
    assert detail["status"] == "delivered"
    assert detail["assigned_courier_id"] == courier["user"]["id"]
    assert detail["assigned_at"] is not None
    assert detail["completed_at"] is not None
    assert detail["proof"]["storage_key"] == proof.json()["storage_key"]
    assert detail["pricing_snapshot"]["customer_total"] == detail["price"]
    assert detail["pricing_engine_version"] == detail["pricing_snapshot"]["engine_version"]


def test_unrelated_users_and_unassigned_couriers_cannot_view_order_detail(client, user_factory):
    owner = user_factory(role="merchant")
    unrelated_customer = user_factory(role="customer")
    unassigned_courier = user_factory(role="courier", transportation_mode="car")
    order = create_order(client, owner["headers"])

    for user in (unrelated_customer, unassigned_courier):
        response = client.get(f"/orders/{order['id']}", headers=user["headers"])
        assert response.status_code == 403
        assert response.json()["detail"] == "Not authorized to view this order"


def test_missing_order_detail_returns_not_found(client, user_factory):
    customer = user_factory(role="customer")

    response = client.get("/orders/999999", headers=customer["headers"])

    assert response.status_code == 404
