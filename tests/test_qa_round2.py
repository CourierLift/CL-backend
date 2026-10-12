"""QA round 2 reproductions.

CONVERTED (qa/quote-idempotency): the original versions of these tests asserted
the DEFECTIVE behavior found at a320a65 (client-controlled price, quote not bound
to order, duplicate orders on retry). The unchanged originals are kept in
qa_evidence/original_repros/ with their output in qa_evidence/qa_round2_*.txt.
Here the same reproductions now assert the CORRECTED behavior.
"""
import json
from tests.order_helpers import quoted_create
P = {"origin":"100 Main Street","destination":"200 Oak Avenue","vehicle":"car","item_type":"standard",
     "weight_kg":8,"quantity":2,"length_in":24,"width_in":18,"height_in":14,
     "weather":"clear","traffic":"medium","surge":1.0,"delivery_requirements":[]}
def log(k,v): print(f"QA {k}: {json.dumps(v)}")

def test_price_inputs_client_controlled(client, user_factory):
    c = user_factory(role="customer")
    prices = {}
    for label, over in [("baseline",{}),("surge3",{"surge":3.0}),("weather_bogus",{"weather":"zzz"}),
                        ("traffic_bogus",{"traffic":"zzz"}),("traffic_low",{"traffic":"low"})]:
        r = quoted_create(client, "/orders/create_compat", json={**P, **over}, headers=c["headers"])
        assert r.status_code == 201, r.text
        prices[label] = r.json()["price"]
    log("prices_by_client_input", prices)
    # CONVERTED: was `> 1` (defect). Client conditions must not change price.
    assert len(set(prices.values())) == 1

def test_quote_not_bound_to_order(client, user_factory):
    c = user_factory(role="customer")
    qr = client.post("/quote/estimate", json={**P, "surge":2.5}, headers=c["headers"]).json()
    q = qr["price_total"]
    o = client.post("/orders/create_compat", json={**P, "surge":1.0, "quote_id": qr["quote_id"]},
                    headers={**c["headers"], "Idempotency-Key": "qa-bind-0001"})
    log("quote_vs_order", {"quoted":q, "created_status":o.status_code, "order_price":o.json().get("price")})
    # CONVERTED: was `price != q` (defect). The order must carry the quoted price.
    assert o.status_code == 201 and o.json()["price"] == q

def test_retry_creates_duplicate_records(client, user_factory):
    c = user_factory(role="customer")
    qid = client.post("/quote/estimate", json=P, headers=c["headers"]).json()["quote_id"]
    codes = []
    for _ in range(2):
        # key lengthened from "k1" to satisfy the 8-128 char contract
        codes.append(client.post("/orders/create_compat", json={**P, "quote_id": qid},
                                 headers={**c["headers"],"Idempotency-Key":"k1-retry-key"}).status_code)
    mine = client.get("/orders/mine", headers=c["headers"]).json()
    log("orders_after_same_key_retry", len(mine))
    # CONVERTED: was `len(mine) == 2` (defect). Retry returns the original order.
    assert codes == [201, 200]
    assert len(mine) == 1

def _to_transit(client, user_factory):
    c = user_factory(role="customer"); k = user_factory(role="courier", transportation_mode="car")
    oid = quoted_create(client, "/orders/create_compat", json=P, headers=c["headers"]).json()["id"]
    assert client.post(f"/orders/{oid}/claim", headers=k["headers"]).status_code == 200
    return c, k, oid

def test_permissions_and_proof_gate(client, user_factory):
    c, k, oid = _to_transit(client, user_factory)
    other_c = user_factory(role="customer"); other_k = user_factory(role="courier", transportation_mode="car")
    res = {}
    res["other_customer_detail"] = client.get(f"/orders/{oid}", headers=other_c["headers"]).status_code
    res["other_courier_detail"] = client.get(f"/orders/{oid}", headers=other_k["headers"]).status_code
    res["no_token_detail"] = client.get(f"/orders/{oid}").status_code
    res["other_courier_status"] = client.patch(f"/orders/{oid}/status", json={"status":"picked_up"}, headers=other_k["headers"]).status_code
    res["customer_status"] = client.patch(f"/orders/{oid}/status", json={"status":"picked_up"}, headers=c["headers"]).status_code
    res["skip_to_delivered"] = client.patch(f"/orders/{oid}/status", json={"status":"delivered"}, headers=k["headers"]).status_code
    res["pickup"] = client.patch(f"/orders/{oid}/status", json={"status":"picked_up"}, headers=k["headers"]).status_code
    res["pickup_repeat"] = client.patch(f"/orders/{oid}/status", json={"status":"picked_up"}, headers=k["headers"]).status_code
    res["transit"] = client.patch(f"/orders/{oid}/status", json={"status":"in_transit"}, headers=k["headers"]).status_code
    res["deliver_without_proof"] = client.patch(f"/orders/{oid}/status", json={"status":"delivered"}, headers=k["headers"]).status_code
    f = {"file":("p.jpg", b"x", "image/jpeg")}
    res["customer_proof"] = client.post(f"/orders/{oid}/proof", files=f, headers=c["headers"]).status_code
    res["other_courier_proof"] = client.post(f"/orders/{oid}/proof", files=f, headers=other_k["headers"]).status_code
    res["bad_type_proof"] = client.post(f"/orders/{oid}/proof", files={"file":("p.txt", b"x", "text/plain")}, headers=k["headers"]).status_code
    res["proof"] = client.post(f"/orders/{oid}/proof", files=f, headers=k["headers"]).status_code
    res["proof_repeat"] = client.post(f"/orders/{oid}/proof", files=f, headers=k["headers"]).status_code
    res["deliver"] = client.patch(f"/orders/{oid}/status", json={"status":"delivered"}, headers=k["headers"]).status_code
    res["deliver_repeat"] = client.patch(f"/orders/{oid}/status", json={"status":"delivered"}, headers=k["headers"]).status_code
    res["courier_history_has_delivered"] = oid in [o["id"] for o in client.get("/orders/assigned", headers=k["headers"]).json()]
    log("permission_matrix", res)
