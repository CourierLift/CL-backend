import pytest

from backend.quote_engine import PRICING_ENGINE_VERSION


def test_address_order_persists_complete_pricing_snapshot(client, user_factory):
    customer = user_factory(role="customer")
    payload = {
        "origin": "100 Main Street",
        "destination": "200 Oak Avenue",
        "vehicle": "EV",
        "item_type": "fragile",
        "weight_kg": 12.5,
        "quantity": 3,
        "length_in": 30,
        "width_in": 20,
        "height_in": 16,
        "weather": "rain",
        "traffic": "high",
        "surge": 1.4,
        "delivery_requirements": ["fragile"],
    }

    response = client.post(
        "/orders/create_compat",
        json=payload,
        headers=customer["headers"],
    )

    assert response.status_code == 201, response.text
    order = response.json()
    snapshot = order["pricing_snapshot"]
    inputs = snapshot["inputs"]

    assert order["pricing_engine_version"] == PRICING_ENGINE_VERSION
    assert snapshot["engine_version"] == PRICING_ENGINE_VERSION
    assert snapshot["customer_total"] == order["price"]
    assert snapshot["mileage"] == order["distance_miles"]
    assert snapshot["eta_min"] == order["eta_min"]
    assert snapshot["vehicle_type"] == order["vehicle"] == "ev"
    assert snapshot["tier"] in {"Saver", "Standard", "Priority", "Pro Load"}
    assert set(snapshot["breakdown"]) == {
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
    assert inputs == {
        "vehicle": "ev",
        "item_type": "fragile",
        "quantity": 3,
        "weight_lb": pytest.approx(12.5 * 2.2046226218),
        "length_in": 30.0,
        "width_in": 20.0,
        "height_in": 16.0,
        "weather": "rain",
        "traffic": "high",
        "surge": 1.4,
        "distance_miles": order["distance_miles"],
        "distance_estimated": True,
        "distance_source": "development_fallback",
    }
    assert "origin" not in inputs
    assert "destination" not in inputs


def test_coordinate_order_also_snapshots_authoritative_pricing(client, user_factory):
    customer = user_factory(role="customer")
    response = client.post(
        "/orders",
        json={
            "pickup_lat": 30.2672,
            "pickup_lng": -97.7431,
            "dropoff_lat": 30.5083,
            "dropoff_lng": -97.6789,
            "vehicle": "car",
            "item_type": "standard",
            "weight_lb": 20,
            "quantity": 1,
            "length_in": 24,
            "width_in": 18,
            "height_in": 12,
            "weather": "clear",
            "traffic": "medium",
            "surge": 1.0,
        },
        headers=customer["headers"],
    )

    assert response.status_code == 201, response.text
    order = response.json()
    snapshot = order["pricing_snapshot"]

    assert order["pricing_engine_version"] == PRICING_ENGINE_VERSION
    assert snapshot["engine_version"] == PRICING_ENGINE_VERSION
    assert snapshot["customer_total"] == order["price"]
    assert snapshot["inputs"]["distance_source"] == "coordinate_haversine"
    assert snapshot["inputs"]["distance_estimated"] is False


def test_historical_pricing_snapshot_is_not_recomputed_on_retrieval(
    client,
    user_factory,
    monkeypatch,
):
    customer = user_factory(role="customer")
    created = client.post(
        "/orders/create_compat",
        json={
            "origin": "100 Main Street",
            "destination": "200 Oak Avenue",
            "vehicle": "car",
            "item_type": "standard",
            "weight_kg": 5,
        },
        headers=customer["headers"],
    ).json()
    original_version = created["pricing_engine_version"]
    original_snapshot = created["pricing_snapshot"]

    # Simulate a future application version. History must return the persisted
    # values from creation rather than deriving a new quote during reads.
    monkeypatch.setattr("backend.orders.PRICING_ENGINE_VERSION", "future-pricing-v2")

    history = client.get("/orders/mine", headers=customer["headers"])

    assert history.status_code == 200
    recovered = history.json()[0]
    assert recovered["pricing_engine_version"] == original_version
    assert recovered["pricing_snapshot"] == original_snapshot
