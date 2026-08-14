import itertools
import os

import pytest
from fastapi.testclient import TestClient


os.environ["CL_APP_ENV"] = "test"
os.environ["CL_SECRET_KEY"] = "test-only-secret-not-for-production"
os.environ["CL_DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CL_DEVELOPMENT_FALLBACK_MILES"] = "7.5"

from backend.database import Base, engine  # noqa: E402
from backend.main import app  # noqa: E402
from backend.services.tracking import tracking_service  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_database():
    tracking_service.reset()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    tracking_service.reset()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user_factory(client):
    sequence = itertools.count(1)

    def create_user(
        *,
        role="customer",
        email=None,
        password="secure-pass",
        transportation_mode=None,
        capabilities=None,
        **profile_overrides,
    ):
        email = email or f"{role}-{next(sequence)}@example.com"
        payload = {
            "email": email,
            "password": password,
            "role": role,
        }
        if transportation_mode is not None:
            payload["transportation_mode"] = transportation_mode
        if capabilities is not None:
            payload["capabilities"] = capabilities
        payload.update(profile_overrides)

        register_response = client.post("/auth/register", json=payload)
        assert register_response.status_code == 200, register_response.text
        login_response = client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        assert login_response.status_code == 200, login_response.text
        token = login_response.json()["access_token"]
        return {
            "user": register_response.json(),
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return create_user
