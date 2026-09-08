def create_delivery(client, headers, **overrides):
    payload = {
        "origin": "100 Main Street",
        "destination": "200 Oak Avenue",
        "vehicle": "car",
        "item_type": "standard",
        "weight_kg": 5,
        "quantity": 1,
        "length_in": 12,
        "width_in": 8,
        "height_in": 6,
    }
    payload.update(overrides)
    return client.post("/orders/create_compat", json=payload, headers=headers)


def test_assigned_orders_recover_claimed_and_completed_work(client, user_factory):
    customer = user_factory(role="customer")
    courier = user_factory(role="courier", transportation_mode="car")
    other_courier = user_factory(role="courier", transportation_mode="SUV")

    first = create_delivery(client, customer["headers"]).json()
    second = create_delivery(
        client,
        customer["headers"],
        destination="300 Pine Road",
    ).json()

    client.post(f"/orders/{first['id']}/claim", headers=courier["headers"])
    client.post(f"/orders/{second['id']}/claim", headers=other_courier["headers"])

    client.patch(
        f"/orders/{first['id']}/status",
        json={"status": "picked_up"},
        headers=courier["headers"],
    )
    client.patch(
        f"/orders/{first['id']}/status",
        json={"status": "in_transit"},
        headers=courier["headers"],
    )
    proof = client.post(
        f"/orders/{first['id']}/proof",
        headers=courier["headers"],
        files={"file": ("proof.jpg", b"proof", "image/jpeg")},
    )
    assert proof.status_code == 201
    client.patch(
        f"/orders/{first['id']}/status",
        json={"status": "delivered"},
        headers=courier["headers"],
    )

    recovered = client.get("/orders/assigned", headers=courier["headers"])

    assert recovered.status_code == 200
    assert [order["id"] for order in recovered.json()] == [first["id"]]
    assert recovered.json()[0]["status"] == "delivered"
    assert recovered.json()[0]["origin"] == "100 Main Street"
    assert recovered.json()[0]["destination"] == "200 Oak Avenue"


def test_assigned_orders_requires_courier_role(client, user_factory):
    customer = user_factory(role="customer")

    response = client.get("/orders/assigned", headers=customer["headers"])

    assert response.status_code == 403
    assert response.json()["detail"] == "Courier role required"
