import pytest

from backend.settings import Settings, settings


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "env": "test"}


@pytest.mark.parametrize(
    ("role", "extra"),
    [
        ("customer", {}),
        ("merchant", {}),
        ("courier", {"transportation_mode": "cargo bike"}),
    ],
)
def test_customer_merchant_and_courier_registration_and_login(client, role, extra):
    email = f"{role}@example.com"
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-pass",
            "role": role,
            **extra,
        },
    )

    assert response.status_code == 200
    assert response.json()["role"] == role

    login = client.post(
        "/auth/login",
        json={"email": email, "password": "secure-pass"},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    assert login.json()["access_token"]


def test_invalid_login_is_rejected(client, user_factory):
    user_factory(role="customer", email="login@example.com")

    response = client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "wrong-pass"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_public_registration_rejects_admin_role(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "not-an-admin@example.com",
            "password": "secure-pass",
            "role": "admin",
        },
    )

    assert response.status_code == 422

    login = client.post(
        "/auth/login",
        json={
            "email": "not-an-admin@example.com",
            "password": "secure-pass",
        },
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


def test_production_accepts_hardened_runtime_posture(monkeypatch):
    configure_production(monkeypatch)

    production = Settings()

    assert production.CL_APP_ENV == "production"
    assert production.CL_SECRET_KEY == "f" * 64
    assert production.CL_DATABASE_URL.startswith("postgresql+psycopg://")
    assert production.CL_OBJECT_STORAGE_BACKEND == "s3"
    assert production.CL_S3_BUCKET == "courier-lifts-proofs"


def test_production_rejects_sqlite_database(monkeypatch):
    configure_production(monkeypatch)
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///./courier_lifts.db")

    with pytest.raises(ValueError, match="must use PostgreSQL"):
        Settings()


def test_production_rejects_local_proof_storage(monkeypatch):
    configure_production(monkeypatch)
    monkeypatch.setenv("CL_OBJECT_STORAGE_BACKEND", "local")

    with pytest.raises(ValueError, match="requires S3-compatible object storage"):
        Settings()


def test_registration_rate_limit_returns_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "CL_AUTH_REGISTER_RATE_LIMIT", 2)

    for index in range(2):
        response = client.post(
            "/auth/register",
            json={
                "email": f"limited-{index}@example.com",
                "password": "secure-pass",
                "role": "customer",
            },
        )
        assert response.status_code == 200

    limited = client.post(
        "/auth/register",
        json={
            "email": "limited-2@example.com",
            "password": "secure-pass",
            "role": "customer",
        },
    )

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_login_rate_limit_returns_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "CL_AUTH_LOGIN_RATE_LIMIT", 2)
    client.post(
        "/auth/register",
        json={
            "email": "login-limited@example.com",
            "password": "secure-pass",
            "role": "customer",
        },
    )

    for _index in range(2):
        response = client.post(
            "/auth/login",
            json={
                "email": "login-limited@example.com",
                "password": "wrong-pass",
            },
        )
        assert response.status_code == 401

    limited = client.post(
        "/auth/login",
        json={
            "email": "login-limited@example.com",
            "password": "wrong-pass",
        },
    )

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
