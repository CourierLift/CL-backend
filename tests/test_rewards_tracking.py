from datetime import datetime

import pytest
from starlette.websockets import WebSocketDisconnect


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


def test_normal_users_cannot_create_reward_events(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")

    for user in (customer, courier):
        response = client.post(
            "/rewards/event",
            headers=user["headers"],
            json={"type": "earn", "points": 25, "reason": "client_request"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Reward events may only be created by a trusted administrator"
        )

    balance = client.get("/rewards/balance", headers=customer["headers"])

    assert balance.status_code == 200
    assert balance.json() == 0


def test_websocket_connection_and_marketplace_event_shapes(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    created_response = create_delivery(client, customer["headers"])
    assert created_response.status_code == 201
    order_id = created_response.json()["id"]

    with client.websocket_connect(
        f"/ws/track?order_id={order_id}&role=admin",
        subprotocols=["bearer", customer["token"]],
    ) as customer_websocket:
        assert customer_websocket.accepted_subprotocol == "bearer"
        ready = customer_websocket.receive_json()
        assert_event_shape(ready, "connection.ready", order_id)
        assert ready["data"]["role"] == "customer"

        claimed_response = client.post(
            f"/orders/{order_id}/claim",
            headers=courier["headers"],
        )
        assert claimed_response.status_code == 200
        assert_event_shape(customer_websocket.receive_json(), "order.claimed", order_id)

        with client.websocket_connect(
            f"/ws/track?order_id={order_id}",
            subprotocols=["bearer", courier["token"]],
        ) as courier_websocket:
            courier_ready = courier_websocket.receive_json()
            assert_event_shape(courier_ready, "connection.ready", order_id)
            assert courier_ready["data"]["role"] == "courier"

            picked_up_response = client.patch(
                f"/orders/{order_id}/status",
                json={"status": "picked_up"},
                headers=courier["headers"],
            )
            assert picked_up_response.status_code == 200
            customer_status = customer_websocket.receive_json()
            courier_status = courier_websocket.receive_json()
            assert_event_shape(customer_status, "order.status_changed", order_id)
            assert_event_shape(courier_status, "order.status_changed", order_id)
            assert customer_status["data"]["previous_status"] == "assigned"
            assert customer_status["data"]["status"] == "picked_up"

            delivered_response = client.patch(
                f"/orders/{order_id}/status",
                json={"status": "delivered"},
                headers=courier["headers"],
            )
            assert delivered_response.status_code == 200
            assert_event_shape(
                customer_websocket.receive_json(), "order.status_changed", order_id
            )
            assert_event_shape(
                customer_websocket.receive_json(), "order.completed", order_id
            )
            assert_event_shape(
                courier_websocket.receive_json(), "order.status_changed", order_id
            )
            assert_event_shape(
                courier_websocket.receive_json(), "order.completed", order_id
            )


def test_websocket_rejects_missing_token(client, user_factory):
    customer = user_factory(role="customer")
    order_id = create_delivery(client, customer["headers"]).json()["id"]

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(f"/ws/track?order_id={order_id}"):
            pass

    assert rejected.value.code == 1008


def test_websocket_rejects_unapproved_browser_origin(client, user_factory):
    customer = user_factory(role="customer")
    order_id = create_delivery(client, customer["headers"]).json()["id"]

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            f"/ws/track?order_id={order_id}",
            subprotocols=["bearer", customer["token"]],
            headers={"origin": "https://attacker.example"},
        ):
            pass

    assert rejected.value.code == 1008


def test_websocket_rejects_unrelated_user_and_unassigned_courier(
    client,
    user_factory,
):
    owner = user_factory(role="customer")
    stranger = user_factory(role="merchant")
    courier = user_factory(role="courier", transportation_mode="car")
    order_id = create_delivery(client, owner["headers"]).json()["id"]

    for user in (stranger, courier):
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                f"/ws/track?order_id={order_id}",
                subprotocols=["bearer", user["token"]],
            ):
                pass
        assert rejected.value.code == 1008


def test_websocket_rejects_client_messages(client, user_factory):
    customer = user_factory(role="customer")
    order_id = create_delivery(client, customer["headers"]).json()["id"]

    with client.websocket_connect(
        f"/ws/track?order_id={order_id}",
        subprotocols=["bearer", customer["token"]],
    ) as websocket:
        websocket.receive_json()
        websocket.send_text("forged tracking update")
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()

    assert rejected.value.code == 1008
