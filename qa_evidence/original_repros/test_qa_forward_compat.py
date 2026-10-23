from tests.test_qa_round2 import P, log
def test_old_backend_accepts_new_frontend_request(client, user_factory):
    c = user_factory(role="customer")
    r = client.post("/orders/create_compat", json={**P, "quote_id": "q_abc"}, headers={**c["headers"], "Idempotency-Key": "k-12345678"})
    log("old_backend_new_client", {"status": r.status_code})
    assert r.status_code == 201
