from concurrent.futures import ThreadPoolExecutor
from tests.test_qa_round2 import P, log
def test_concurrent_same_key_create(client, user_factory):
    c = user_factory(role="customer")
    h = {**c["headers"], "Idempotency-Key": "same-attempt"}
    with ThreadPoolExecutor(5) as ex:
        codes = list(ex.map(lambda _: client.post("/orders/create_compat", json=P, headers=h).status_code, range(5)))
    from backend.database import SessionLocal
    from backend.models import Order
    with SessionLocal() as s:
        n = s.query(Order).filter(Order.user_id == c["user"]["id"]).count()
    log("concurrent_create", {"codes": codes, "db_order_rows": n})
