import pytest
from pydantic import ValidationError

import backend.orders as orders_module
from backend.main import cors_origins
from backend.services.route_distance import RouteDistance, RouteDistanceError
from backend.settings import Settings, settings as runtime_settings


PRODUCTION_BASE = {
    "CL_APP_ENV": "production",
    "CL_SECRET_KEY": "x" * 48,
    "CL_DATABASE_URL": "postgresql+psycopg://user:pass@db.example.com/courier_lifts",
    "CL_OBJECT_STORAGE_BACKEND": "s3",
    "CL_S3_BUCKET": "courier-lifts-proofs",
    "CL_GOOGLE_MAPS_API_KEY": "test-google-routes-key",
}


def test_production_settings_accept_secure_launch_posture():
    settings = Settings(
        **PRODUCTION_BASE,
        CL_FRONTEND_ORIGIN="https://app.courierlifts.com/",
    )

    assert settings.CL_FRONTEND_ORIGIN == "https://app.courierlifts.com"
    assert settings.CL_GOOGLE_MAPS_API_KEY == "test-google-routes-key"


@pytest.mark.parametrize(
    "origin",
    [
        "http://app.courierlifts.com",
        "http://localhost:5173",
        "https://localhost",
        "https://app.courierlifts.com/path",
    ],
)
def test_production_settings_reject_unsafe_frontend_origin(origin):
    with pytest.raises(ValidationError):
        Settings(**PRODUCTION_BASE, CL_FRONTEND_ORIGIN=origin)


def test_production_cors_does_not_include_localhost():
    assert cors_origins("production", "https://app.courierlifts.com") == [
        "https://app.courierlifts.com"
    ]


def test_development_cors_keeps_localhost():
    assert "http://localhost:5173" in cors_origins(
        "development", "http://localhost:8080"
    )


def test_readiness_probe_checks_database(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": "reachable"}


def address_payload():
    return {
        "origin": "100 Main Street, Austin, TX",
        "destination": "200 Oak Avenue, Bastrop, TX",
        "vehicle": "car",
        "item_type": "standard",
        "quantity": 1,
        "weight_kg": 5,
        "length_in": 12,
        "width_in": 8,
        "height_in": 6,
        "weather": "clear",
        "traffic": "medium",
        "surge": 1.0,
        "delivery_requirements": [],
    }


def configure_runtime_google(monkeypatch):
    monkeypatch.setattr(runtime_settings, "CL_APP_ENV", "production")
    monkeypatch.setattr(runtime_settings, "CL_GOOGLE_MAPS_API_KEY", "test-key")


def test_production_address_quote_uses_google_route_distance(client, monkeypatch):
    configure_runtime_google(monkeypatch)
    captured = {}

    async def fake_route_distance(**kwargs):
        captured.update(kwargs)
        return RouteDistance(miles=12.5, source="google_routes")

    monkeypatch.setattr(orders_module, "google_route_distance", fake_route_distance)

    response = client.post("/quote/estimate", json=address_payload())

    assert response.status_code == 200
    assert response.json()["miles"] == 12.5
    assert response.json()["distance_source"] == "google_routes"
    assert response.json()["estimated"] is False
    assert captured["origin"] == "100 Main Street, Austin, TX"
    assert captured["destination"] == "200 Oak Avenue, Bastrop, TX"
    assert captured["api_key"] == "test-key"


def test_production_created_lift_persists_google_distance_snapshot(
    client, monkeypatch, user_factory
):
    configure_runtime_google(monkeypatch)

    async def fake_route_distance(**_kwargs):
        return RouteDistance(miles=12.5, source="google_routes")

    monkeypatch.setattr(orders_module, "google_route_distance", fake_route_distance)
    sender = user_factory(role="customer")

    response = client.post(
        "/orders/create_compat",
        json=address_payload(),
        headers=sender["headers"],
    )

    assert response.status_code == 201
    order = response.json()
    assert order["distance_miles"] == 12.5
    assert order["distance_source"] == "google_routes"
    assert order["distance_estimated"] is False
    assert order["pricing_snapshot"]["mileage"] == 12.5
    assert order["pricing_snapshot"]["inputs"]["distance_source"] == "google_routes"


def test_production_address_pricing_fails_closed_when_google_is_unavailable(
    client, monkeypatch
):
    configure_runtime_google(monkeypatch)

    async def unavailable(**_kwargs):
        raise RouteDistanceError("provider unavailable")

    monkeypatch.setattr(orders_module, "google_route_distance", unavailable)

    response = client.post("/quote/estimate", json=address_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "Production route distance is temporarily unavailable"
