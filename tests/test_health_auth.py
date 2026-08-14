import pytest


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
