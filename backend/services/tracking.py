"""Tracking service boundary with a local-only in-memory implementation."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from fastapi import WebSocket
from pydantic import BaseModel, Field


class OrderEventType(str, Enum):
    created = "order.created"
    claimed = "order.claimed"
    status_changed = "order.status_changed"
    completed = "order.completed"


class OrderEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: OrderEventType
    order_id: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any]


class TrackingPublisher(Protocol):
    async def publish(self, event: OrderEvent) -> None: ...


def make_order_event(
    event_type: OrderEventType,
    order_id: int,
    **data: Any,
) -> OrderEvent:
    return OrderEvent(type=event_type, order_id=order_id, data=data)


class InMemoryTrackingService:
    """Process-local WebSocket rooms for development and tests only.

    Connections and events are lost when the process restarts, and multiple
    worker processes do not share rooms. A production deployment must replace
    this implementation behind the same service boundary.
    """

    def __init__(self) -> None:
        self._rooms: dict[int, set[WebSocket]] = {}

    async def connect(self, order_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(order_id, set()).add(websocket)

    def disconnect(self, order_id: int, websocket: WebSocket) -> None:
        room = self._rooms.get(order_id)
        if room is None:
            return
        room.discard(websocket)
        if not room:
            self._rooms.pop(order_id, None)

    async def send_payload(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        await websocket.send_json(payload)

    async def broadcast_payload(
        self,
        order_id: int,
        payload: dict[str, Any],
    ) -> None:
        for websocket in list(self._rooms.get(order_id, set())):
            try:
                await websocket.send_json(payload)
            except Exception:
                self.disconnect(order_id, websocket)

    async def publish(self, event: OrderEvent) -> None:
        await self.broadcast_payload(
            event.order_id,
            event.model_dump(mode="json"),
        )

    def reset(self) -> None:
        self._rooms.clear()


tracking_service = InMemoryTrackingService()
