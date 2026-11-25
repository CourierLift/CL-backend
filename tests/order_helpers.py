"""Test helper for the quote-bound, idempotent order creation contract.

COMPATIBILITY: after patch qa/quote-idempotency, POST /orders and
POST /orders/create_compat require a server quote_id and an Idempotency-Key
header. Pre-existing tests that only need "an order" call this helper instead
of posting directly; their behavioral assertions are unchanged.
"""

import uuid

QUOTE_PATHS = {"/orders/create_compat": "/quote/estimate", "/orders": "/quote"}


def quoted_create(client, path, *, json, headers, idempotency_key=None):
    quote = client.post(QUOTE_PATHS[path], json=json, headers=headers)
    assert quote.status_code == 200, quote.text
    assert quote.json().get("quote_id"), quote.text
    return client.post(
        path,
        json={**json, "quote_id": quote.json()["quote_id"]},
        headers={**headers, "Idempotency-Key": idempotency_key or f"test-{uuid.uuid4()}"},
    )
