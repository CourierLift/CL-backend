"""Small process-local rate limiter for the single-worker SQLite MVP."""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._expires: dict[tuple[str, str], float] = {}
        self._next_sweep = 0.0

    def check(
        self,
        *,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        now = monotonic()
        cutoff = now - window_seconds
        key = (scope, identity)

        with self._lock:
            if now >= self._next_sweep:
                for expired in [item for item, deadline in self._expires.items() if deadline <= now]:
                    self._attempts.pop(expired, None)
                    self._expires.pop(expired, None)
                self._next_sweep = now + 60
            attempts = self._attempts[key]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()

            if len(attempts) >= limit:
                retry_after = max(1, int(attempts[0] + window_seconds - now) + 1)
                raise HTTPException(
                    status_code=429,
                    detail="Too many authentication attempts",
                    headers={"Retry-After": str(retry_after)},
                )
            attempts.append(now)
            self._expires[key] = now + window_seconds

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()
            self._expires.clear()
            self._next_sweep = 0.0


def request_identity(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if forwarded_for:
        return forwarded_for
    if request.client is not None:
        return request.client.host
    return "unknown"


auth_rate_limiter = InMemoryRateLimiter()
