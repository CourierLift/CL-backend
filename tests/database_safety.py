"""Guard destructive test fixtures before any application engine is imported."""

import os

from sqlalchemy.engine import make_url


def validate_disposable_database_url(value: str) -> None:
    url = make_url(value)
    if url.get_backend_name() == "sqlite":
        if url.database in (None, "", ":memory:") and not url.query:
            return
        raise ValueError("pytest only permits in-memory SQLite; never point fixtures at a preserved file")
    if (
        url.get_backend_name() == "postgresql"
        and url.host in {"localhost", "127.0.0.1", "::1"}
        and (url.database or "").startswith(("pilot_", "test_", "courier_lifts_test"))
        and not url.query
        and not any(key.startswith("PG") for key in os.environ)
    ):
        return
    raise ValueError("pytest requires a disposable loopback PostgreSQL test database or in-memory SQLite")
