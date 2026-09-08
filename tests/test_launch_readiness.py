import pytest
from pydantic import ValidationError

from backend.main import cors_origins
from backend.settings import Settings


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
