"""CONVERTED (qa/quote-idempotency): original (unchanged copy in
qa_evidence/original_repros/) only logged 5 rows for 5 same-key requests at
a320a65. It now asserts exactly one database row."""
from concurrent.futures import ThreadPoolExecutor
from tests.test_qa_round2 import P, log
def test_concurrent_same_key_create(client, user_factory):
    c = user_factory(role="customer")
    qid = client.post("/quote/estimate", json=P, headers=c["headers"]).json()["quote_id"]
    h = {**c["headers"], "Idempotency-Key": "same-attempt"}
    with ThreadPoolExecutor(5) as ex:
        codes = list(ex.map(lambda _: client.post("/orders/create_compat", json={**P, "quote_id": qid}, headers=h).status_code, range(5)))
    from backend.database import SessionLocal
    from backend.models import Order
    with SessionLocal() as s:
        n = s.query(Order).filter(Order.user_id == c["user"]["id"]).count()
    log("concurrent_create", {"codes": codes, "db_order_rows": n})
    assert n == 1
    assert sorted(codes) == [200, 200, 200, 200, 201]
