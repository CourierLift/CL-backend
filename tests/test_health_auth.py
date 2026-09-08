from __future__ import annotations

import pytest

from backend.auth_jwt import create_access_token
from backend.settings import Settings


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_register_login_and_me(client):
    register = client.post(
        "/auth/register",
        json={
            "email": "owner@example.com",
            "password": "secure-pass",
            "role": "customer",
        },
    )
    assert register.status_code == 200, register.text
    user = register.json()
    assert user["email"] == "owner@example.com"
    assert user["role"] == "customer"

    login = client.post(
        "/auth/login",
        json={"email": "owner@example.com", "password": "secure-pass"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]


def test_courier_registration_creates_profile(client):
    register = client.post(
        "/auth/register",
        json={
            "email": "courier@example.com",
            "password": "secure-pass",
            "role": "courier",
            "transportation_mode": "car",
        },
    )
    assert register.status_code == 200, register.text

    login = client.post(
        "/auth/login",
        json={"email": "courier@example.com", "password": "secure-pass"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    profile = client.get(
        "/auth/courier-profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile.status_code == 200
    assert profile.json()["transportation_mode"] == "car"


def test_me_rejects_invalid_token(client):
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert response.status_code == 401


def test_me_rejects_token_without_subject(client):
    token = create_access_token({"role": "customer"})
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_login_rejects_wrong_password(client, user_factory):
    user_factory(email="wrong-password@example.com")

    login = client.post(
        "/auth/login",
        json={"email": "wrong-password@example.com", "password": "incorrect"},
    )
    assert login.status_code == 401


@pytest.mark.parametrize(
    "secret",
    [
        "local-development-only-change-before-deploy",
        "replace-with-a-long-random-secret",
        "too-short",
    ],
)
def test_production_rejects_insecure_secret(monkeypatch, secret):
    monkeypatch.setenv("CL_APP_ENV", "production")
    monkeypatch.setenv("CL_SECRET_KEY", secret)

    with pytest.raises(ValueError, match="new production secret"):
        Settings()


def configure_production(monkeypatch):
    monkeypatch.setenv("CL_APP_ENV", "production")
    monkeypatch.setenv("CL_SECRET_KEY", "f" * 64)
    monkeypatch.setenv(
        "CL_DATABASE_URL",
        "postgresql+psycopg://courier_lifts:password@db/courier_lifts",
    )
    monkeypatch.setenv("CL_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CL_S3_BUCKET", "courier-lifts-proofs")
    monkeypatch.setenv("CL_FRONTEND_ORIGIN", "https://app.courierlifts.com")


def test_production_accepts_hardened_runtime_posture(monkeypatch):
    configure_production(monkeypatch)

    production = Settings()

    assert production.CL_APP_ENV == "production"
    assert production.CL_SECRET_KEY == "f" * 64
    assert production.CL_DATABASE_URL.startswith("postgresql+psycopg://")
    assert production.CL_OBJECT_STORAGE_BACKEND == "s3"
    assert production.CL_S3_BUCKET == "courier-lifts-proofs"
    assert production.CL_FRONTEND_ORIGIN == "https://app.courierlifts.com"


def test_production_rejects_sqlite_database(monkeypatch):
    configure_production(monkeypatch)
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///./courier_lifts.db")

    with pytest.raises(ValueError, match="must use PostgreSQL"):
        Settings()


def test_production_rejects_local_proof_storage(monkeypatch):
    configure_production(monkeypatch)
    monkeypatch.setenv("CL_OBJECT_STORAGE_BACKEND", "local")

    with pytest.raises(ValueError, match="requires S3-compatible"):
        Settings()


def test_production_requires_s3_bucket(monkeypatch):
    configure_production(monkeypatch)
    monkeypatch.delenv("CL_S3_BUCKET", raising=False)

    with pytest.raises(ValueError, match="requires S3-compatible"):
        Settings()
