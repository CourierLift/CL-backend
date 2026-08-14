"""WebSocket tracking router using the replaceable tracking service boundary."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .services.tracking import tracking_service


router = APIRouter(tags=["tracking"])


def _connection_event(
    event_type: str,
    order_id: int,
    role: str,
    **data: str,
) -> dict[str, object]:
    return {
        "event_id": str(uuid4()),
        "type": event_type,
        "order_id": order_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"role": role, **data},
    }


@router.websocket("/ws/track")
async def ws_track(
    websocket: WebSocket,
    order_id: int = Query(..., gt=0, description="Order room to join"),
    role: str = Query("client", description="client|courier|merchant|admin"),
) -> None:
    await tracking_service.connect(order_id, websocket)
    await tracking_service.send_payload(
        websocket,
        _connection_event("connection.ready", order_id, role),
    )

    try:
        while True:
            message = await websocket.receive_text()
            await tracking_service.broadcast_payload(
                order_id,
                _connection_event(
                    "tracking.message",
                    order_id,
                    role,
                    message=message,
                ),
            )
    except WebSocketDisconnect:
        tracking_service.disconnect(order_id, websocket)
        await tracking_service.broadcast_payload(
            order_id,
            _connection_event("connection.left", order_id, role),
        )
