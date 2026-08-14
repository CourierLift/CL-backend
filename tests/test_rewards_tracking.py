from datetime import datetime


def create_delivery(client, headers):
    return client.post(
        "/orders/create_compat",
        headers=headers,
        json={
            "origin": "100 Main Street",
            "destination": "200 Oak Avenue",
            "vehicle": "car",
            "item_type": "standard",
            "weight_kg": 2,
        },
    )


def assert_event_shape(event, expected_type, order_id):
    assert set(event) == {"event_id", "type", "order_id", "timestamp", "data"}
    assert event["type"] == expected_type
    assert event["order_id"] == order_id
    assert event["event_id"]
    datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    assert isinstance(event["data"], dict)


def test_rewards_balance_and_events(client, user_factory):
    customer = user_factory(role="customer")

    earn = client.post(
        "/rewards/event",
        headers=customer["headers"],
        json={"type": "earn", "points": 25, "reason": "order_created"},
    )
    redeem = client.post(
        "/rewards/event",
        headers=customer["headers"],
        json={"type": "redeem", "points": -5, "reason": "credit_used"},
    )
    balance = client.get("/rewards/balance", headers=customer["headers"])

    assert earn.status_code == 200
    assert earn.json()["type"] == "earn"
    assert redeem.status_code == 200
    assert balance.status_code == 200
    assert balance.json() == 20


def test_websocket_connection_and_marketplace_event_shapes(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    expected_order_id = 1

    with client.websocket_connect(
        f"/ws/track?order_id={expected_order_id}&role=customer"
    ) as websocket:
        ready = websocket.receive_json()
        assert_event_shape(ready, "connection.ready", expected_order_id)

        created_response = create_delivery(client, customer["headers"])
        assert created_response.status_code == 201
        order_id = created_response.json()["id"]
        assert order_id == expected_order_id
        assert_event_shape(websocket.receive_json(), "order.created", order_id)

        claimed_response = client.post(
            f"/orders/{order_id}/claim",
            headers=courier["headers"],
        )
        assert claimed_response.status_code == 200
        assert_event_shape(websocket.receive_json(), "order.claimed", order_id)

        picked_up_response = client.patch(
            f"/orders/{order_id}/status",
            json={"status": "picked_up"},
            headers=courier["headers"],
        )
        assert picked_up_response.status_code == 200
        status_event = websocket.receive_json()
        assert_event_shape(status_event, "order.status_changed", order_id)
        assert status_event["data"]["previous_status"] == "assigned"
        assert status_event["data"]["status"] == "picked_up"

        delivered_response = client.patch(
            f"/orders/{order_id}/status",
            json={"status": "delivered"},
            headers=courier["headers"],
        )
        assert delivered_response.status_code == 200
        assert_event_shape(websocket.receive_json(), "order.status_changed", order_id)
        assert_event_shape(websocket.receive_json(), "order.completed", order_id)
