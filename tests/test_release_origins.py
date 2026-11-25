"""The published custom domain and the existing Render URL both need API access."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import cors_origins
from backend.settings import Settings


def production_settings(**overrides):
    values = {
        "CL_APP_ENV": "production",
        "CL_UNPAID_PILOT": True,
        "CL_REGISTRATION_MODE": "manual-invite",
        "CL_SECRET_KEY": "synthetic-release-origin-secret-48-characters-long",
        "CL_DATABASE_URL": "postgresql+psycopg://test:test@localhost/cl_test_origins",
        "CL_OBJECT_STORAGE_BACKEND": "s3",
        "CL_S3_BUCKET": "synthetic-test-bucket",
        "CL_FRONTEND_ORIGIN": "https://courierlifts.com",
        "CL_ADDITIONAL_FRONTEND_ORIGINS": ["https://courierlifts-web.onrender.com"],
    }
    values.update(overrides)
    return Settings(**values)


def test_actual_release_origins_receive_preflight_and_unrelated_origins_do_not():
    settings = production_settings()
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(
            settings.CL_APP_ENV,
            settings.CL_FRONTEND_ORIGIN,
            settings.CL_ADDITIONAL_FRONTEND_ORIGINS,
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    with TestClient(app) as client:
        for origin in ["https://courierlifts.com", "https://courierlifts-web.onrender.com"]:
            response = client.options(
                "/orders/create_compat",
                headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                         "Access-Control-Request-Headers": "authorization,content-type"},
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
        for origin in ["https://unrelated.example", "http://localhost:5173"]:
            response = client.options(
                "/orders/create_compat",
                headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
            )
            assert response.status_code == 400
            assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", [
    "*", "https://*.example.com", "http://courierlifts.com", "https://localhost",
    "https://courierlifts.com/path", "https://user:pass@courierlifts.com",
    "https://courierlifts.com:99999", "https://courierlifts.com?tracking=1",
])
def test_extra_production_origin_rejects_unsafe_or_non_origin_values(origin):
    with pytest.raises(ValidationError):
        production_settings(CL_ADDITIONAL_FRONTEND_ORIGINS=[origin])


def test_extra_origins_are_read_from_environment_and_deduplicated(monkeypatch):
    monkeypatch.setenv(
        "CL_ADDITIONAL_FRONTEND_ORIGINS",
        " https://courierlifts-web.onrender.com/, https://courierlifts.com, ",
    )
    values = production_settings().model_dump()
    values.pop("CL_ADDITIONAL_FRONTEND_ORIGINS")
    settings = Settings(**values)
    assert settings.CL_ADDITIONAL_FRONTEND_ORIGINS == ["https://courierlifts-web.onrender.com"]
