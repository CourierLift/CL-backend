import pytest
from pydantic import ValidationError

from backend.main import cors_origins
from backend.settings import Settings, settings as runtime_settings


PRODUCTION_BASE = {
    "CL_APP_ENV": "production",
    "CL_SECRET_KEY": "x" * 48,
    "CL_DATABASE_URL": "postgresql+psycopg://user:pass@db.example.com/courier_lifts",
    "CL_OBJECT_STORAGE_BACKEND": "s3",
    "CL_S3_BUCKET": "courier-lifts-proofs",
}


def test_production_settings_accept_secure_launch_posture():
    settings = Settings(
        **PRODUCTION_BASE,
        CL_FRONTEND_ORIGIN="https://app.courierlifts.com/",
    )

    assert settings.CL_FRONTEND_ORIGIN == "https://app.courierlifts.com"


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


def test_production_address_pricing_fails_closed_without_real_distance_source(
    client, monkeypatch
):
    monkeypatch.setattr(runtime_settings, "CL_APP_ENV", "production")

    response = client.post(
        "/quote/estimate",
        json={
            "origin": "100 Main Street",
            "destination": "200 Oak Avenue",
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
        },
    )

    assert response.status_code == 503
    assert "real distance source" in response.json()["detail"]
