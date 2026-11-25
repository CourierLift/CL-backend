import time

from backend.database import SessionLocal
from backend.models import User
from backend.services import password_recovery as recovery


def configure_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(recovery, "mail_configured", lambda: True)
    monkeypatch.setattr(recovery, "send_code", lambda email, code: sent.append((email, code)))
    return sent


def test_email_setup_is_private_and_password_change_revokes_old_access(client, user_factory, monkeypatch):
    sent = configure_mail(monkeypatch)
    user = user_factory(email="pilot@example.com")
    known = client.post("/auth/password/request", json={"email": "PILOT@example.com"})
    unknown = client.post("/auth/password/request", json={"email": "other@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(sent) == 1 and sent[0][0] == "pilot@example.com"
    code = sent[0][1]
    assert code not in known.text
    new = {"email": "pilot@example.com", "code": code, "new_password": "new pilot password"}
    assert client.post("/auth/password/confirm", json={**new, "email": "other@example.com"}).status_code == 400
    assert client.post("/auth/password/confirm", json=new).status_code == 200
    assert client.post("/auth/password/confirm", json=new).status_code == 400
    assert client.get("/orders/mine", headers=user["headers"]).status_code == 401
    assert client.post("/auth/login", json={"email": new["email"], "password": "secure-pass"}).status_code == 401
    login = client.post("/auth/login", json={"email": new["email"], "password": new["new_password"]})
    assert login.status_code == 200
    assert client.get("/orders/mine", headers={"Authorization": "Bearer " + login.json()["access_token"]}).status_code == 200


def test_misconfigured_mail_and_expired_codes_do_not_change_account(client, user_factory, monkeypatch):
    email = "pilot@example.com"
    user_factory(email=email)
    monkeypatch.setattr(recovery, "mail_configured", lambda: False)
    assert client.post("/auth/password/request", json={"email": email}).status_code == 503
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).one()
        old = recovery.make_code(user, int(time.time()) - recovery.LIFETIME)
    result = client.post("/auth/password/confirm", json={"email": email, "code": old, "new_password": "new pilot password"})
    assert result.status_code == 400
    assert client.post("/auth/login", json={"email": email, "password": "secure-pass"}).status_code == 200


def test_mailbox_suppression_and_validation_redaction(client, user_factory, monkeypatch):
    email = "pilot@example.com"
    user_factory(email=email)
    sent = configure_mail(monkeypatch)
    for _ in range(4):
        assert client.post("/auth/password/request", json={"email": email}).status_code == 202
    assert len(sent) == 3
    bad = client.post("/auth/password/confirm", json={"email": email, "code": sent[0][1], "new_password": "private"})
    assert bad.status_code == 422 and "private" not in bad.text and sent[0][1] not in bad.text


def test_operator_web_setup_requires_mail_and_preserves_existing_role(client, monkeypatch):
    from backend import provision_invite
    from backend.settings import settings
    monkeypatch.setenv("CL_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(settings, "CL_REGISTRATION_MODE", "manual-invite")
    monkeypatch.setattr(recovery, "mail_configured", lambda: False)
    args = ["--web-setup", "--email", "invite@example.com", "--role", "customer"]
    assert provision_invite.main(args) == 2
    sent = configure_mail(monkeypatch)
    assert provision_invite.main(args) == 0
    assert provision_invite.main(args) != 0
    assert client.post("/auth/password/request", json={"email": "invite@example.com"}).status_code == 202
    payload = {"email": "invite@example.com", "code": sent[0][1], "new_password": "first pilot password"}
    assert client.post("/auth/password/confirm", json=payload).status_code == 200
    with SessionLocal() as db:
        assert db.query(User).filter_by(email="invite@example.com").one().role.value == "customer"


def test_delivery_uses_configured_sender_and_calls_ses_for_one_recipient(monkeypatch):
    from backend.settings import settings
    captured = {}
    class MailClient:
        def send_email(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(recovery.boto3, "client", lambda *args, **kwargs: MailClient())
    monkeypatch.setattr(settings, "CL_PASSWORD_MAIL_FROM", "pilot@example.com")
    monkeypatch.setattr(settings, "CL_FRONTEND_ORIGIN", "https://courierlifts.com")
    recovery.send_code("recipient@example.com", "LOCAL-ONLY")
    assert captured["Destination"] == {"ToAddresses": ["recipient@example.com"]}
    assert captured["FromEmailAddress"] == "pilot@example.com"
    body = captured["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "LOCAL-ONLY" in body and "https://courierlifts.com/#recover" in body


def test_recovery_does_not_create_accounts_or_issue_admin_codes(client, user_factory, monkeypatch):
    from backend.models import UserRole
    sent = configure_mail(monkeypatch)
    email = "admin@example.com"
    user_factory(email=email)
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).one()
        user.role = UserRole.admin
        db.commit()
    assert client.post("/auth/password/request", json={"email": email}).status_code == 202
    assert client.post("/auth/password/request", json={"email": "unknown@example.com"}).status_code == 202
    assert not sent
    with SessionLocal() as db:
        assert db.query(User).count() == 1


def test_reset_closes_open_tracking_connection(client, user_factory, monkeypatch):
    from tests.test_rewards_tracking import create_delivery
    email = "tracking@example.com"
    sent = configure_mail(monkeypatch)
    user = user_factory(email=email)
    order = create_delivery(client, user["headers"]).json()
    with client.websocket_connect(f"/ws/track?order_id={order['id']}", subprotocols=["bearer", user["token"]]) as socket:
        assert socket.receive_json()["type"] == "connection.ready"
        client.post("/auth/password/request", json={"email": email})
        result = client.post("/auth/password/confirm", json={
            "email": email, "code": sent[0][1], "new_password": "tracking pilot password"})
        assert result.status_code == 200
        assert socket.receive()["code"] == 1008


def test_concurrent_postgres_confirmations_spend_one_code(client, user_factory, monkeypatch):
    import pytest
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from backend.database import engine
    if engine.dialect.name != "postgresql":
        pytest.skip("Uses PostgreSQL row update serialization")
    email = "concurrent@example.com"
    user_factory(email=email)
    sent = configure_mail(monkeypatch)
    client.post("/auth/password/request", json={"email": email})
    start = Barrier(2)
    def confirm(password):
        start.wait(timeout=10)
        return client.post("/auth/password/confirm", json={
            "email": email, "code": sent[0][1], "new_password": password}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(confirm, ["first test password", "second test password"]))
    assert sorted(statuses) == [200, 400]
