import pytest

from backend.quote_engine import estimate_quote


def create_delivery(client, headers, **overrides):
    payload = {
        "origin": "100 Main Street",
        "destination": "200 Oak Avenue",
        "vehicle": "car",
        "item_type": "standard",
        "weight_kg": 5,
        "quantity": 1,
        "length_in": 12,
        "width_in": 8,
        "height_in": 6,
    }
    payload.update(overrides)
    return client.post("/orders/create_compat", json=payload, headers=headers)


def test_address_quote_generation_is_explicitly_estimated(client):
    response = client.post(
        "/quote/estimate",
        json={
            "origin": "100 Main Street",
            "destination": "200 Oak Avenue",
            "vehicle": "EV",
            "item_type": "electronics",
            "weight_kg": 4,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["price_total"] > 0
    assert body["eta_min"] > 0
    assert body["estimated"] is True
    assert body["distance_source"] == "development_fallback"
    assert body["miles"] == 7.5


@pytest.mark.parametrize(
    "mode",
    [
        "foot",
        "bike",
        "cargo bike",
        "e-bike",
        "scooter",
        "motorcycle",
        "car",
        "EV",
        "SUV",
        "van",
        "light truck",
        "box truck",
    ],
)
def test_quote_supports_required_transportation_modes(client, mode):
    response = client.post(
        "/quote/estimate",
        json={
            "origin": "A",
            "destination": "B",
            "vehicle": mode,
            "item_type": "standard",
        },
    )

    assert response.status_code == 200, response.text


def test_quote_engine_accounts_for_required_pricing_factors():
    result = estimate_quote(
        transportation_mode="EV",
        item_type="fragile",
        quantity=3,
        weight_lb=90,
        length_in=36,
        width_in=24,
        height_in=18,
        weather="rain",
        traffic="high",
        surge=1.4,
        pickup=(30.2672, -97.7431),
        dropoff=(30.5083, -97.6789),
    )

    assert result.price_total > 0
    assert result.estimated is False
    assert result.distance_source == "coordinate_haversine"
    assert set(result.breakdown) == {
        "base_fee",
        "distance_charge",
        "weight_charge",
        "volume_charge",
        "quantity_charge",
        "item_multiplier",
        "weather_multiplier",
        "traffic_multiplier",
        "surge_multiplier",
        "environmental_adjustment",
    }
    assert result.breakdown["weight_charge"] > 0
    assert result.breakdown["volume_charge"] > 0
    assert result.breakdown["quantity_charge"] > 0
    assert result.breakdown["environmental_adjustment"] < 0


def test_order_creation_and_customer_history(client, user_factory):
    customer = user_factory(role="customer")

    created = create_delivery(client, customer["headers"])

    assert created.status_code == 201
    order = created.json()
    assert order["status"] == "pending"
    assert order["user_id"] == customer["user"]["id"]
    assert order["assigned_courier_id"] is None
    assert order["distance_estimated"] is True

    history = client.get("/orders/mine", headers=customer["headers"])
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [order["id"]]


def test_eligible_courier_sees_and_claims_order(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    order = create_delivery(client, customer["headers"]).json()

    available = client.get("/orders/available", headers=courier["headers"])
    assert available.status_code == 200
    assert [item["id"] for item in available.json()] == [order["id"]]
    assert available.json()[0]["origin"] is None
    assert available.json()[0]["destination"] is None
    assert available.json()[0]["pickup_lat"] is None
    assert available.json()[0]["dropoff_lat"] is None

    claim = client.post(
        f"/orders/{order['id']}/claim",
        headers=courier["headers"],
    )
    assert claim.status_code == 200
    assert claim.json()["status"] == "assigned"
    assert claim.json()["assigned_courier_id"] == courier["user"]["id"]
    assert claim.json()["origin"] == "100 Main Street"
    assert claim.json()["destination"] == "200 Oak Avenue"


def test_ineligible_courier_is_filtered_and_rejected(client, user_factory):
    customer = user_factory(role="customer")
    bike_courier = user_factory(role="courier", transportation_mode="bike")
    order = create_delivery(
        client,
        customer["headers"],
        vehicle="bike",
        weight_kg=25,
    ).json()

    available = client.get("/orders/available", headers=bike_courier["headers"])
    assert available.status_code == 200
    assert available.json() == []

    claim = client.post(
        f"/orders/{order['id']}/claim",
        headers=bike_courier["headers"],
    )
    assert claim.status_code == 403
    assert claim.json()["detail"]["message"] == "Courier is not eligible for this delivery"


def test_eligibility_enforces_dimensions_and_delivery_requirements(
    client,
    user_factory,
):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    order = create_delivery(
        client,
        customer["headers"],
        vehicle="car",
        length_in=100,
        delivery_requirements=["fragile"],
    ).json()

    available = client.get("/orders/available", headers=courier["headers"])
    claim = client.post(
        f"/orders/{order['id']}/claim",
        headers=courier["headers"],
    )

    assert available.json() == []
    assert claim.status_code == 403
    reasons = claim.json()["detail"]["reasons"]
    assert "Item length exceeds courier capacity" in reasons
    assert "Missing delivery capabilities: fragile" in reasons


def test_double_claim_is_prevented(client, user_factory):
    customer = user_factory(role="customer")
    first = user_factory(role="courier", transportation_mode="car")
    second = user_factory(role="courier", transportation_mode="SUV")
    order = create_delivery(client, customer["headers"]).json()

    first_claim = client.post(
        f"/orders/{order['id']}/claim",
        headers=first["headers"],
    )
    second_claim = client.post(
        f"/orders/{order['id']}/claim",
        headers=second["headers"],
    )

    assert first_claim.status_code == 200
    assert second_claim.status_code == 409
    assert second_claim.json()["detail"] == "Order is no longer available"


def test_courier_cannot_update_another_couriers_order(client, user_factory):
    customer = user_factory(role="customer")
    assigned = user_factory(role="courier", transportation_mode="car")
    other = user_factory(role="courier", transportation_mode="SUV")
    order = create_delivery(client, customer["headers"]).json()
    client.post(f"/orders/{order['id']}/claim", headers=assigned["headers"])

    response = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "picked_up"},
        headers=other["headers"],
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Order is assigned to another courier"


def test_legal_status_progression_and_terminal_state(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    order = create_delivery(client, customer["headers"]).json()
    claimed = client.post(
        f"/orders/{order['id']}/claim",
        headers=courier["headers"],
    )
    assert claimed.status_code == 200

    picked_up = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "picked_up"},
        headers=courier["headers"],
    )
    delivered = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "delivered"},
        headers=courier["headers"],
    )
    illegal = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "canceled"},
        headers=courier["headers"],
    )

    assert picked_up.status_code == 200
    assert picked_up.json()["status"] == "picked_up"
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "delivered"
    assert delivered.json()["completed_at"] is not None
    assert illegal.status_code == 409


def test_customer_or_merchant_ownership_is_enforced(client, user_factory):
    owner = user_factory(role="merchant")
    stranger = user_factory(role="customer")
    order = create_delivery(client, owner["headers"]).json()

    forbidden = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "canceled"},
        headers=stranger["headers"],
    )
    canceled = client.patch(
        f"/orders/{order['id']}/status",
        json={"status": "canceled"},
        headers=owner["headers"],
    )

    assert forbidden.status_code == 403
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
