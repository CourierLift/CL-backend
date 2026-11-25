"""Manual-invite enforcement with disposable users, no real invitation delivery."""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from backend.auth_jwt import verify_password
from backend.database import SessionLocal, get_db
from backend.main import app
from backend.models import CourierProfile, Order, User
from backend.routes.auth_routes_jwt import REGISTRATION_CLOSED_MESSAGE
from backend.schemas import RegisterIn
from backend.services import account_provisioning
from backend.services.account_provisioning import DuplicateAccountError, provision_account
from backend.settings import Settings, settings


@pytest.fixture
def closed(monkeypatch):
    monkeypatch.setattr(settings, "CL_REGISTRATION_MODE", "manual-invite")


def private_user(role="customer", email="invited@example.com", **extra):
    payload = RegisterIn(email=email, password="disposable-test-password", role=role, **extra)
    with SessionLocal() as db:
        user = provision_account(db, payload)
        return user.id


@pytest.mark.parametrize("role", ["customer", "merchant", "courier"])
@pytest.mark.parametrize("email", ["new@example.com", "existing@example.com"])
def test_closed_registration_has_no_lookup_hash_or_write(client, closed, monkeypatch, role, email):
    private_user(email="existing@example.com")
    db_spy = MagicMock()
    app.dependency_overrides[get_db] = lambda: db_spy
    hash_spy = MagicMock(side_effect=AssertionError("Gate must run before hashing"))
    monkeypatch.setattr(account_provisioning, "hash_password", hash_spy)
    try:
        response = client.post("/auth/register", json={
            "email": email, "password": "disposable-test-password", "role": role,
        })
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 403
    assert response.json() == {"detail": REGISTRATION_CLOSED_MESSAGE}
    assert db_spy.mock_calls == []
    hash_spy.assert_not_called()
    with SessionLocal() as db:
        assert db.query(User).count() == 1
        assert db.query(CourierProfile).count() == 0


@pytest.mark.parametrize("role", ["customer", "merchant", "courier"])
@pytest.mark.parametrize("bypass", [
    {"headers": {"X-Registration-Mode": "public", "X-Invite-Token": "fake", "X-Admin": "true"}},
    {"headers": {"Origin": "http://localhost:5173", "X-Forwarded-For": "127.0.0.1"}},
    {"params": {"mode": "public", "invite": "true", "role": "admin"}},
    {"extra": {"registration_mode": "public", "invited": True, "is_admin": True, "invite_token": "fake"}},
])
def test_client_cannot_bypass_server_gate(client, closed, role, bypass):
    payload = {"email": "bypass@example.com", "password": "disposable-test-password", "role": role}
    kwargs = dict(bypass)
    payload.update(kwargs.pop("extra", {}))
    response = client.post("/auth/register", json=payload, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"] == REGISTRATION_CLOSED_MESSAGE
    with SessionLocal() as db:
        assert db.query(User).count() == 0
        assert db.query(CourierProfile).count() == 0


@pytest.mark.parametrize("bad", [
    {"role": "admin"}, {"role": "owner"}, {"email": "not-an-email"},
    {"password": "short"}, {"max_weight_lb": -1}, {"transportation_mode": "spaceship"},
])
def test_validation_and_public_admin_ban_remain(client, closed, bad):
    response = client.post("/auth/register", json={
        "email": "validation@example.com", "password": "disposable-test-password",
        "role": "customer", **bad,
    })
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.query(User).count() == 0


def test_closed_gate_keeps_registration_and_login_rate_limits(client, closed, monkeypatch):
    monkeypatch.setattr(settings, "CL_AUTH_REGISTER_RATE_LIMIT", 2)
    monkeypatch.setattr(settings, "CL_AUTH_LOGIN_RATE_LIMIT", 2)
    payload = {"email": "rate@example.com", "password": "disposable-test-password", "role": "customer"}
    for _ in range(2):
        assert client.post("/auth/register", json=payload).status_code == 403
    limited = client.post("/auth/register", json=payload)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    for _ in range(2):
        assert client.post("/auth/login", json=payload).status_code == 401
    limited = client.post("/auth/login", json=payload)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_missing_mode_defaults_closed(monkeypatch):
    monkeypatch.delenv("CL_REGISTRATION_MODE", raising=False)
    assert Settings().CL_REGISTRATION_MODE == "manual-invite"


@pytest.mark.parametrize("value", ["", "PUBLIC", "open", "manual_invite", " public ", "true"])
def test_invalid_mode_fails_configuration_and_startup(monkeypatch, value):
    monkeypatch.setenv("CL_REGISTRATION_MODE", value)
    with pytest.raises(ValidationError, match="CL_REGISTRATION_MODE"):
        Settings()
    env = {
        "PATH": os.environ["PATH"], "CL_APP_ENV": "test",
        "CL_DATABASE_URL": "sqlite:///:memory:", "CL_REGISTRATION_MODE": value,
    }
    result = subprocess.run([sys.executable, "-c", "import backend.main"], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "CL_REGISTRATION_MODE" in result.stderr


@pytest.mark.parametrize("role", ["customer", "merchant", "courier"])
def test_explicit_public_mode_keeps_legacy_development_registration(client, monkeypatch, role):
    monkeypatch.setenv("CL_REGISTRATION_MODE", "public")
    assert Settings().CL_REGISTRATION_MODE == "public"
    monkeypatch.setattr(settings, "CL_REGISTRATION_MODE", "public")
    response = client.post("/auth/register", json={
        "email": f"public-{role}@example.com", "password": "disposable-test-password", "role": role,
    })
    assert response.status_code == 200
    assert response.json()["role"] == role


@pytest.mark.parametrize("role", ["customer", "merchant", "courier"])
def test_private_provisioning_login_and_existing_jwt_work_while_closed(client, closed, role):
    uid = private_user(role)
    credentials = {"email": "invited@example.com", "password": "disposable-test-password"}
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    # Closing registration is not a token rotation and must not mutate an account.
    assert client.post("/auth/register", json={**credentials, "role": role}).status_code == 403
    endpoint = "/orders/assigned" if role == "courier" else "/orders/mine"
    response = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == []
    assert client.post("/auth/login", json=credentials).status_code == 200
    with SessionLocal() as db:
        user = db.get(User, uid)
        assert user.role.value == role
        assert verify_password(credentials["password"], user.password_hash)
        assert db.query(User).count() == 1
        assert db.query(CourierProfile).count() == (1 if role == "courier" else 0)


def test_closed_customer_quote_create_same_key_replay_and_fresh_login_recovery(client, closed):
    uid = private_user()
    credentials = {"email": "invited@example.com", "password": "disposable-test-password"}
    headers = {"Authorization": "Bearer " + client.post("/auth/login", json=credentials).json()["access_token"]}
    draft = {"origin": "Synthetic pickup", "destination": "Synthetic dropoff", "vehicle": "car"}
    anonymous = client.post("/quote/estimate", json=draft)
    assert anonymous.status_code == 200 and not anonymous.json().get("quote_id")
    quote = client.post("/quote/estimate", json=draft, headers=headers).json()
    assert quote["quote_id"]
    body = {**draft, "quote_id": quote["quote_id"]}
    headers["Idempotency-Key"] = "closed-gate-recovery"
    created = client.post("/orders/create_compat", json=body, headers=headers)
    assert created.status_code == 201
    replay = client.post("/orders/create_compat", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["id"] == created.json()["id"]
    assert replay.json()["price"] == quote["price_total"] == created.json()["price"]
    refreshed = client.post("/auth/login", json=credentials).json()["access_token"]
    history = client.get("/orders/mine", headers={"Authorization": f"Bearer {refreshed}"}).json()
    assert len(history) == 1 and history[0]["quote_id"] == quote["quote_id"]
    private_user(email="other@example.com")
    other_token = client.post("/auth/login", json={**credentials, "email": "other@example.com"}).json()["access_token"]
    assert client.get("/orders/mine", headers={"Authorization": f"Bearer {other_token}"}).json() == []
    with SessionLocal() as db:
        assert db.query(Order).filter(Order.user_id == uid).count() == 1


def test_private_duplicate_never_hashes_or_overwrites(monkeypatch, closed):
    uid = private_user("merchant")
    with SessionLocal() as db:
        original = db.get(User, uid).password_hash
        hash_spy = MagicMock()
        monkeypatch.setattr(account_provisioning, "hash_password", hash_spy)
        with pytest.raises(DuplicateAccountError):
            provision_account(db, RegisterIn(email="INVITED@example.com", password="different-password", role="courier"))
        hash_spy.assert_not_called()
        assert db.get(User, uid).password_hash == original
        assert db.get(User, uid).role.value == "merchant"
        assert db.query(User).count() == 1
        assert db.query(CourierProfile).count() == 0


def test_private_courier_profile_reuses_normalization_and_overrides(closed):
    uid = private_user("courier", transportation_mode="cargo bike",
                       max_weight_lb=33, capabilities=[" Two person ", "two person", " FRAGILE "])
    with SessionLocal() as db:
        profile = db.query(CourierProfile).filter(CourierProfile.user_id == uid).one()
        assert profile.transportation_mode == "cargo_bike"
        assert profile.max_weight_lb == 33
        assert profile.capabilities == ["fragile", "two_person"]


def test_private_provisioning_revalidates_mutated_admin_model(closed):
    payload = RegisterIn(email="not-admin@example.com", password="disposable-test-password")
    payload.role = "admin"
    with SessionLocal() as db, pytest.raises(ValidationError):
        provision_account(db, payload)


def test_operator_cli_hides_password_and_refuses_duplicates(closed, monkeypatch, capsys):
    from backend import provision_invite
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    password = "fictional-cli-only-password"
    monkeypatch.setattr(provision_invite.getpass, "getpass", lambda _prompt: password)
    assert provision_invite.main(["--email", "cli@example.com", "--role", "courier"]) == 0
    assert provision_invite.main(["--email", "cli@example.com", "--role", "merchant"]) == 2
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert "nothing was overwritten" in output.err
    with SessionLocal() as db:
        assert db.query(User).count() == 1
        assert db.query(CourierProfile).count() == 1


def test_operator_cli_refuses_missing_database_and_nonterminal(closed, monkeypatch, capsys):
    from backend import provision_invite
    monkeypatch.delenv("CL_DATABASE_URL", raising=False)
    args = ["--email", "cli@example.com", "--role", "customer"]
    assert provision_invite.main(args) == 2
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert provision_invite.main(args) == 2
    with SessionLocal() as db:
        assert db.query(User).count() == 0


def test_operator_cli_errors_never_echo_password_arguments(capsys):
    from backend import provision_invite
    with pytest.raises(SystemExit) as error:
        provision_invite.main(["--email", "cli@example.com", "--role", "customer", "--password", "DO-NOT-ECHO"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "DO-NOT-ECHO" not in output.out + output.err


def test_existing_admin_login_is_preserved_without_operator_admin_creation(client, closed):
    from backend.auth_jwt import decode_access_token, hash_password
    from backend.models import UserRole
    # Synthetic pre-existing account fixture; NOT operator provisioning.
    with SessionLocal() as db:
        original = User(email="existing-admin@example.com",
                        password_hash=hash_password("disposable-test-password"), role=UserRole.admin)
        db.add(original)
        db.commit()
        uid = original.id
        saved_hash = original.password_hash
    response = client.post("/auth/login", json={
        "email": "existing-admin@example.com", "password": "disposable-test-password",
    })
    assert response.status_code == 200
    assert decode_access_token(response.json()["access_token"]) == (uid, "admin")
    with SessionLocal() as db:
        assert db.get(User, uid).password_hash == saved_hash
        assert db.get(User, uid).role == UserRole.admin


def test_private_racing_duplicate_rolls_back(closed, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.commit.side_effect = IntegrityError("synthetic duplicate", {}, Exception("unique constraint"))
    monkeypatch.setattr(account_provisioning, "hash_password", lambda _: "fictional-hash")
    with pytest.raises(DuplicateAccountError):
        provision_account(db, RegisterIn(email="race@example.com", password="disposable-test-password"))
    db.rollback.assert_called_once()
    db.refresh.assert_not_called()


def test_operator_password_mismatch_creates_nothing(closed, monkeypatch, capsys):
    from backend import provision_invite
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter(["disposable-one", "disposable-two"])
    monkeypatch.setattr(provision_invite.getpass, "getpass", lambda _: next(replies))
    assert provision_invite.main(["--email", "mismatch@example.com", "--role", "customer"]) == 2
    output = capsys.readouterr()
    assert "disposable-one" not in output.out + output.err
    with SessionLocal() as db:
        assert db.query(User).count() == 0


def test_operator_validation_errors_do_not_leak_password(closed, monkeypatch, capsys):
    from backend import provision_invite
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(provision_invite.getpass, "getpass", lambda _: "short")
    assert provision_invite.main(["--email", "invalid@example.com", "--role", "customer"]) == 2
    output = capsys.readouterr()
    assert "short" not in output.out + output.err
    with SessionLocal() as db:
        assert db.query(User).count() == 0


def test_operator_cli_cannot_create_admin(capsys):
    from backend import provision_invite
    with pytest.raises(SystemExit) as error:
        provision_invite.main(["--email", "admin@example.com", "--role", "admin"])
    assert error.value.code == 2
    with SessionLocal() as db:
        assert db.query(User).count() == 0


def test_operator_real_tty_uses_hidden_password_on_disposable_migrated_db(tmp_path):
    import pty
    import select
    import time
    from sqlalchemy import create_engine, text
    env = {
        "PATH": os.environ["PATH"], "CL_APP_ENV": "development",
        "CL_REGISTRATION_MODE": "manual-invite",
        "CL_DATABASE_URL": "sqlite:///" + str(tmp_path / "operator.sqlite"),
    }
    migration = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                               env=env, capture_output=True, timeout=20)
    assert migration.returncode == 0
    master, slave = pty.openpty()
    child = subprocess.Popen(
        [sys.executable, "-m", "backend.provision_invite",
         "--email", "tty-fictional@example.com", "--role", "courier"],
        env=env, stdin=slave, stdout=slave, stderr=slave,
    )
    os.close(slave)
    output = b""
    deadline = time.monotonic() + 15
    def read_until(marker):
        nonlocal output
        while marker not in output and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], .1)
            if readable:
                output += os.read(master, 4096)
        assert marker in output, "CLI hidden prompt was not reached"
    try:
        password = b"fictional-tty-password"
        read_until(b"Pilot account password (hidden):")
        os.write(master, password + b"\n")
        read_until(b"Confirm password:")
        os.write(master, password + b"\n")
        read_until(b"No invitation sent.")
        assert child.wait(timeout=5) == 0
        assert password not in output
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
    disposable = create_engine(env["CL_DATABASE_URL"])
    with disposable.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM courier_profiles")).scalar() == 1
    disposable.dispose()
