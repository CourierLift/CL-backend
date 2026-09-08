from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from backend.database import engine
from backend.main import app


pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="requires the PostgreSQL production-style test database",
)


def create_delivery(client, headers, suffix: int):
    response = client.post(
        "/orders/create_compat",
        headers=headers,
        json={
            "origin": f"100 Main Street #{suffix}",
            "destination": f"200 Oak Avenue #{suffix}",
            "vehicle": "car",
            "item_type": "standard",
            "weight_kg": 5,
            "quantity": 1,
            "length_in": 12,
            "width_in": 8,
            "height_in": 6,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_simultaneous_claims_assign_exactly_one_courier(client, user_factory):
    sender = user_factory(role="customer")
    courier_b = user_factory(role="courier", transportation_mode="car")
    courier_c = user_factory(role="courier", transportation_mode="suv")

    # Repeat the race a few times so the contract is exercised under actual
    # concurrent PostgreSQL transactions rather than one lucky scheduling run.
    for attempt in range(5):
        order = create_delivery(client, sender["headers"], attempt)
        barrier = Barrier(3)

        def claim(headers):
            with TestClient(app) as independent_client:
                barrier.wait(timeout=10)
                response = independent_client.post(
                    f"/orders/{order['id']}/claim",
                    headers=headers,
                )
                return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(claim, courier_b["headers"])
            second = pool.submit(claim, courier_c["headers"])
            barrier.wait(timeout=10)
            outcomes = [first.result(timeout=20), second.result(timeout=20)]

        statuses = sorted(status for status, _body in outcomes)
        assert statuses == [200, 409], outcomes

        winner = next(body for status, body in outcomes if status == 200)
        loser = next(body for status, body in outcomes if status == 409)
        assert winner["status"] == "assigned"
        assert winner["assigned_courier_id"] in {
            courier_b["user"]["id"],
            courier_c["user"]["id"],
        }
        assert loser["detail"] in {
            "Order is no longer available",
            "Order was claimed by another courier",
        }

        authoritative = client.get(
            f"/orders/{order['id']}",
            headers=sender["headers"],
        )
        assert authoritative.status_code == 200
        assert authoritative.json()["status"] == "assigned"
        assert authoritative.json()["assigned_courier_id"] == winner["assigned_courier_id"]
