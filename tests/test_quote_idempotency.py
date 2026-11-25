"""Regression tests for B-1 (server-controlled pricing), B-2 (quote-to-order
binding) and B-3 (idempotent order creation). Added on qa/quote-idempotency.

Every create-count assertion counts database rows, not HTTP responses.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

import pytest
from sqlalchemy import create_engine, text

from backend.database import DATABASE_URL, SessionLocal, engine
from backend.models import Order, Quote


ADDRESS = {
    "origin": "100 Main Street",
    "destination": "200 Oak Avenue",
    "vehicle": "car",
    "item_type": "standard",
    "weight_kg": 8,
    "quantity": 2,
    "length_in": 24,
    "width_in": 18,
    "height_in": 14,
    "delivery_requirements": [],
}
COORD = {
    "pickup_lat": 30.2672,
    "pickup_lng": -97.7431,
    "dropoff_lat": 30.5083,
    "dropoff_lng": -97.6789,
    "vehicle": "car",
    "item_type": "standard",
    "weight_lb": 20,
}
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
requires_postgres = pytest.mark.skipif(
    not IS_POSTGRES, reason="real concurrency requires PostgreSQL (CL_DATABASE_URL)"
)


def order_rows(user_id=None):
    with SessionLocal() as session:
        query = session.query(Order)
        if user_id is not None:
            query = query.filter(Order.user_id == user_id)
        return query.count()


def get_quote(client, headers, body=ADDRESS, path="/quote/estimate"):
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def create(client, headers, key, body, path="/orders/create_compat"):
    return client.post(path, json=body, headers={**headers, "Idempotency-Key": key})


def expire_quote(quote_id):
    with SessionLocal() as session:
        quote = session.get(Quote, quote_id)
        quote.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()


def assert_error(response, status_code, code):
    assert response.status_code == status_code, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code, detail
    assert isinstance(detail["message"], str) and detail["message"]
    assert isinstance(detail["fields"], list)
    return detail


# --- Quote issuance -------------------------------------------------------


def test_unauthenticated_estimate_is_display_only(client):
    body = client.post("/quote/estimate", json=ADDRESS).json()
    assert "quote_id" not in body and "expires_at" not in body
    assert set(body) == {"price_total", "eta_min", "miles", "tier", "estimated", "distance_source"}


def test_authenticated_estimate_returns_orderable_quote(client, user_factory):
    customer = user_factory(role="customer")
    body = get_quote(client, customer["headers"])
    assert body["quote_id"].startswith("q_")
    assert 0 < body["expires_in_seconds"] <= 900
    assert body["expires_at"].endswith("Z") or body["expires_at"].endswith("+00:00")
    assert body["pricing_conditions"] == {"weather": "clear", "traffic": "medium", "surge": 1.0}
    with SessionLocal() as session:
        stored = session.get(Quote, body["quote_id"])
        assert stored.user_id == customer["user"]["id"]
        assert stored.price_total == body["price_total"]


def test_courier_estimate_gets_no_orderable_quote(client, user_factory):
    courier = user_factory(role="courier", transportation_mode="car")
    assert "quote_id" not in get_quote(client, courier["headers"])


def test_client_conditions_do_not_change_price(client, user_factory):
    customer = user_factory(role="customer")
    baseline = get_quote(client, customer["headers"])["price_total"]
    for override in ({"surge": 3.0}, {"weather": "snow"}, {"traffic": "low"}, {"traffic": "zzz"}):
        assert get_quote(client, customer["headers"], {**ADDRESS, **override})["price_total"] == baseline
        assert client.post("/quote/estimate", json={**ADDRESS, **override}).json()["price_total"] == baseline
    coord = client.post("/quote", json=COORD).json()["price"]
    assert client.post("/quote", json={**COORD, "surge": 3.0, "traffic": "high"}).json()["price"] == coord


# --- Quote-to-order binding ----------------------------------------------


def test_order_price_and_snapshot_are_copied_from_quote(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"], {**ADDRESS, "surge": 2.5})
    response = create(client, customer["headers"], "bind-attempt-1",
                      {**ADDRESS, "surge": 1.0, "quote_id": quote["quote_id"]})
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["price"] == quote["price_total"]
    assert order["quote_id"] == quote["quote_id"]
    with SessionLocal() as session:
        stored_quote = session.get(Quote, quote["quote_id"])
        assert order["pricing_snapshot"] == stored_quote.pricing_snapshot
    assert order_rows(customer["user"]["id"]) == 1


def test_coordinate_route_binds_coordinate_quote(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"], COORD, path="/quote")
    response = create(client, customer["headers"], "coord-attempt-1",
                      {**COORD, "quote_id": quote["quote_id"]}, path="/orders")
    assert response.status_code == 201, response.text
    assert response.json()["price"] == quote["price"]


def test_quote_kind_cannot_cross_routes(client, user_factory):
    customer = user_factory(role="customer")
    address_quote = get_quote(client, customer["headers"])
    response = create(client, customer["headers"], "cross-route-1",
                      {**COORD, "quote_id": address_quote["quote_id"]}, path="/orders")
    assert_error(response, 409, "quote_details_mismatch")
    assert order_rows() == 0


def test_other_customers_quote_is_not_found(client, user_factory):
    owner = user_factory(role="customer")
    other = user_factory(role="customer")
    quote = get_quote(client, owner["headers"])
    response = create(client, other["headers"], "steal-attempt-1", {**ADDRESS, "quote_id": quote["quote_id"]})
    detail = assert_error(response, 404, "quote_not_found")
    assert "order_id" not in detail
    unknown = create(client, other["headers"], "unknown-attempt", {**ADDRESS, "quote_id": "q_doesnotexist"})
    assert_error(unknown, 404, "quote_not_found")
    assert order_rows() == 0


def test_other_customer_gets_404_even_after_quote_used(client, user_factory):
    owner = user_factory(role="customer")
    other = user_factory(role="customer")
    quote = get_quote(client, owner["headers"])
    assert create(client, owner["headers"], "owner-key-001", {**ADDRESS, "quote_id": quote["quote_id"]}).status_code == 201
    response = create(client, other["headers"], "other-key-001", {**ADDRESS, "quote_id": quote["quote_id"]})
    detail = assert_error(response, 404, "quote_not_found")
    assert "order_id" not in detail
    assert order_rows() == 1


def test_changed_details_are_rejected(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    for i, change in enumerate(({"destination": "999 Far Road"}, {"weight_kg": 80}, {"vehicle": "suv"},
                                {"delivery_requirements": ["fragile"]})):
        response = create(client, customer["headers"], f"changed-key-{i}", {**ADDRESS, **change, "quote_id": quote["quote_id"]})
        assert_error(response, 409, "quote_details_mismatch")
    assert order_rows() == 0


def test_expired_unused_quote_is_rejected(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    expire_quote(quote["quote_id"])
    response = create(client, customer["headers"], "expired-key-1", {**ADDRESS, "quote_id": quote["quote_id"]})
    assert_error(response, 409, "quote_expired")
    assert order_rows() == 0


def test_reused_quote_with_different_key_conflicts_with_order_id(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    first = create(client, customer["headers"], "first-key-01", {**ADDRESS, "quote_id": quote["quote_id"]})
    assert first.status_code == 201
    second = create(client, customer["headers"], "second-key-02", {**ADDRESS, "quote_id": quote["quote_id"]})
    detail = assert_error(second, 409, "quote_already_used")
    assert detail["order_id"] == first.json()["id"]
    assert order_rows(customer["user"]["id"]) == 1


# --- Idempotency ---------------------------------------------------------


def test_same_key_same_payload_returns_original(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    body = {**ADDRESS, "quote_id": quote["quote_id"]}
    first = create(client, customer["headers"], "attempt-same-1", body)
    second = create(client, customer["headers"], "attempt-same-1", body)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json() == first.json()
    assert order_rows(customer["user"]["id"]) == 1


def test_same_key_different_payload_conflicts(client, user_factory):
    customer = user_factory(role="customer")
    q1 = get_quote(client, customer["headers"])
    q2 = get_quote(client, customer["headers"], {**ADDRESS, "destination": "300 Pine Road"})
    assert create(client, customer["headers"], "attempt-diff-1", {**ADDRESS, "quote_id": q1["quote_id"]}).status_code == 201
    response = create(client, customer["headers"], "attempt-diff-1",
                      {**ADDRESS, "destination": "300 Pine Road", "quote_id": q2["quote_id"]})
    assert_error(response, 409, "idempotency_key_reused")
    assert order_rows(customer["user"]["id"]) == 1


def test_keys_are_scoped_per_customer(client, user_factory):
    a = user_factory(role="customer")
    b = user_factory(role="customer")
    qa = get_quote(client, a["headers"])
    qb = get_quote(client, b["headers"])
    assert create(client, a["headers"], "shared-key-1", {**ADDRESS, "quote_id": qa["quote_id"]}).status_code == 201
    response = create(client, b["headers"], "shared-key-1", {**ADDRESS, "quote_id": qb["quote_id"]})
    assert response.status_code == 201
    assert response.json()["user_id"] == b["user"]["id"]
    assert order_rows() == 2


def test_replay_after_quote_expiry_returns_original(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    body = {**ADDRESS, "quote_id": quote["quote_id"]}
    first = create(client, customer["headers"], "attempt-expiry", body)
    assert first.status_code == 201
    expire_quote(quote["quote_id"])
    replay = create(client, customer["headers"], "attempt-expiry", body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert order_rows(customer["user"]["id"]) == 1


@pytest.mark.parametrize("key", [None, "", "short", "x" * 129, "has space key"])
def test_idempotency_key_required(client, user_factory, key):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    headers = dict(customer["headers"])
    if key is not None:
        headers["Idempotency-Key"] = key
    response = client.post("/orders/create_compat", json={**ADDRESS, "quote_id": quote["quote_id"]}, headers=headers)
    assert_error(response, 400, "idempotency_key_required")
    assert order_rows() == 0


@pytest.mark.parametrize("path,body", [("/orders/create_compat", ADDRESS), ("/orders", COORD)])
def test_both_create_routes_are_protected(client, user_factory, path, body):
    customer = user_factory(role="customer")
    no_key = client.post(path, json=body, headers=customer["headers"])
    assert_error(no_key, 400, "idempotency_key_required")
    no_quote = create(client, customer["headers"], "no-quote-key", body, path=path)
    detail = assert_error(no_quote, 422, "quote_required")
    assert detail["fields"][0]["loc"] == ["body", "quote_id"]
    assert order_rows() == 0


def test_error_shape_for_validation_auth_and_role(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    malformed = client.post("/orders/create_compat", json={"origin": "", "quantity": "many"},
                            headers={**customer["headers"], "Idempotency-Key": "malformed-1"})
    detail = assert_error(malformed, 422, "invalid_request")
    assert detail["fields"]
    assert_error(client.post("/orders/create_compat", json=ADDRESS), 401, "not_authenticated")
    assert_error(create(client, courier["headers"], "courier-key-1", ADDRESS), 403, "forbidden")
    # Other routes keep their existing error contract.
    assert client.post("/auth/login", json={}).json()["detail"][0]["type"] == "missing"


def test_cors_preflight_allows_idempotency_key(client):
    response = client.options(
        "/orders/create_compat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


# --- Durability and concurrency -----------------------------------------


@pytest.mark.skipif(DATABASE_URL in {"sqlite://", "sqlite:///:memory:"},
                    reason="restart simulation needs a persistent database")
def test_idempotency_survives_restart(user_factory):
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as first_client:
        customer = user_factory(role="customer")
        quote = get_quote(first_client, customer["headers"])
        body = {**ADDRESS, "quote_id": quote["quote_id"]}
        original = create(first_client, customer["headers"], "restart-key-1", body)
        assert original.status_code == 201
    # Simulated process restart: drop every pooled connection and verify through
    # a brand-new engine that the row is durable, then replay on a new client.
    engine.dispose()
    fresh = create_engine(DATABASE_URL)
    with fresh.connect() as conn:
        rows = conn.execute(text("SELECT count(*) FROM orders WHERE idempotency_key = 'restart-key-1'")).scalar()
    fresh.dispose()
    assert rows == 1
    with TestClient(app) as second_client:
        replay = create(second_client, customer["headers"], "restart-key-1", body)
    assert replay.status_code == 200
    assert replay.json()["id"] == original.json()["id"]
    assert order_rows() == 1


def _parallel(n, fn):
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(run, range(n)))


@requires_postgres
def test_concurrent_same_key_creates_exactly_one_row(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    body = {**ADDRESS, "quote_id": quote["quote_id"]}
    responses = _parallel(8, lambda _i: create(client, customer["headers"], "concurrent-same", body))
    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 7 + [201], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert order_rows(customer["user"]["id"]) == 1


@requires_postgres
def test_concurrent_different_keys_same_quote_one_row(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    body = {**ADDRESS, "quote_id": quote["quote_id"]}
    responses = _parallel(8, lambda i: create(client, customer["headers"], f"concurrent-key-{i}", body))
    created = [r for r in responses if r.status_code == 201]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(created) == 1 and len(conflicts) == 7, [r.text for r in responses]
    assert all(r.json()["detail"]["code"] == "quote_already_used" for r in conflicts)
    assert all(r.json()["detail"]["order_id"] == created[0].json()["id"] for r in conflicts)
    assert order_rows(customer["user"]["id"]) == 1


def test_sequential_retries_create_one_row(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"])
    body = {**ADDRESS, "quote_id": quote["quote_id"]}
    codes = [create(client, customer["headers"], "sequential-key", body).status_code for _ in range(5)]
    assert codes == [201, 200, 200, 200, 200]
    assert order_rows(customer["user"]["id"]) == 1


# --- Product-owner additions: merchant role and coordinate flow -----------


@pytest.mark.parametrize("role", ["customer", "merchant"])
@pytest.mark.parametrize(
    "quote_path,create_path,body,price_field",
    [("/quote/estimate", "/orders/create_compat", ADDRESS, "price_total"), ("/quote", "/orders", COORD, "price")],
)
def test_every_creator_role_gets_orderable_quote_and_idempotent_order(
    client, user_factory, role, quote_path, create_path, body, price_field
):
    creator = user_factory(role=role)
    quote = get_quote(client, creator["headers"], {**body, "surge": 3.0}, path=quote_path)
    assert quote["quote_id"].startswith("q_")
    assert 0 < quote["expires_in_seconds"] <= 900 and quote["expires_at"]
    assert quote["pricing_conditions"] == {"weather": "clear", "traffic": "medium", "surge": 1.0}
    request_body = {**body, "quote_id": quote["quote_id"]}
    first = create(client, creator["headers"], f"{role}-attempt-01", request_body, path=create_path)
    assert first.status_code == 201, first.text
    assert first.json()["price"] == quote[price_field]
    replay = create(client, creator["headers"], f"{role}-attempt-01", request_body, path=create_path)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    other_key = create(client, creator["headers"], f"{role}-attempt-02", request_body, path=create_path)
    assert_error(other_key, 409, "quote_already_used")
    assert order_rows(creator["user"]["id"]) == 1


def test_coordinate_route_rejects_changed_expired_and_reused_key(client, user_factory):
    customer = user_factory(role="customer")
    quote = get_quote(client, customer["headers"], COORD, path="/quote")
    changed = create(client, customer["headers"], "coord-changed",
                     {**COORD, "dropoff_lat": 31.0, "quote_id": quote["quote_id"]}, path="/orders")
    assert_error(changed, 409, "quote_details_mismatch")
    body = {**COORD, "quote_id": quote["quote_id"]}
    assert create(client, customer["headers"], "coord-key-1", body, path="/orders").status_code == 201
    expire_quote(quote["quote_id"])
    assert create(client, customer["headers"], "coord-key-1", body, path="/orders").status_code == 200
    q2 = get_quote(client, customer["headers"], {**COORD, "weight_lb": 5}, path="/quote")
    reused = create(client, customer["headers"], "coord-key-1",
                    {**COORD, "weight_lb": 5, "quote_id": q2["quote_id"]}, path="/orders")
    assert_error(reused, 409, "idempotency_key_reused")
    q3 = get_quote(client, customer["headers"], {**COORD, "weight_lb": 6}, path="/quote")
    expire_quote(q3["quote_id"])
    assert_error(create(client, customer["headers"], "coord-key-3",
                        {**COORD, "weight_lb": 6, "quote_id": q3["quote_id"]}, path="/orders"), 409, "quote_expired")
    assert order_rows(customer["user"]["id"]) == 1


@requires_postgres
def test_concurrent_same_key_coordinate_route_one_row(client, user_factory):
    merchant = user_factory(role="merchant")
    quote = get_quote(client, merchant["headers"], COORD, path="/quote")
    body = {**COORD, "quote_id": quote["quote_id"]}
    responses = _parallel(8, lambda _i: create(client, merchant["headers"], "coord-concurrent", body, path="/orders"))
    assert sorted(r.status_code for r in responses) == [200] * 7 + [201], [r.text for r in responses]
    assert order_rows(merchant["user"]["id"]) == 1
