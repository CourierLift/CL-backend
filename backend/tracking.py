"""Authenticated WebSocket tracking for users related to an order."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .auth_jwt import decode_access_token
from .database import get_db
from .models import Order, User, UserRole
from .services.tracking import tracking_service
from .settings import settings


router = APIRouter(tags=["tracking"])
POLICY_VIOLATION = 1008


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


def _offered_subprotocols(websocket: WebSocket) -> list[str]:
    return [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]


def _bearer_token(websocket: WebSocket) -> str | None:
    protocols = _offered_subprotocols(websocket)
    if len(protocols) == 2 and protocols[0].lower() == "bearer":
        return protocols[1]
    return None


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    allowed = {settings.CL_FRONTEND_ORIGIN, "http://localhost:5173"}
    if settings.CL_APP_ENV == "test":
        allowed.add("http://testserver")
    return origin in allowed


def _may_track_order(user: User, order: Order) -> bool:
    return (
        user.role == UserRole.admin
        or order.user_id == user.id
        or order.assigned_courier_id == user.id
    )


async def _reject(websocket: WebSocket, reason: str) -> None:
    await websocket.close(code=POLICY_VIOLATION, reason=reason)


@router.websocket("/ws/track")
async def ws_track(
    websocket: WebSocket,
    order_id: int = Query(..., gt=0, description="Order room to join"),
    db: Session = Depends(get_db),
) -> None:
    if not _origin_allowed(websocket):
        await _reject(websocket, "Origin is not allowed")
        return

    token = _bearer_token(websocket)
    if token is None:
        await _reject(websocket, "Bearer WebSocket subprotocol required")
        return

    try:
        user_id, _token_role = decode_access_token(token)
    except ValueError:
        await _reject(websocket, "Invalid or expired token")
        return

    user = db.get(User, user_id)
    order = db.get(Order, order_id)
    if user is None or order is None or not _may_track_order(user, order):
        await _reject(websocket, "Not authorized for this order")
        return

    await tracking_service.connect(order_id, websocket, subprotocol="bearer")
    await tracking_service.send_payload(
        websocket,
        _connection_event("connection.ready", order_id, user.role.value),
    )

    try:
        while True:
            await websocket.receive_text()
            await websocket.close(
                code=POLICY_VIOLATION,
                reason="Client tracking messages are not supported",
            )
            break
    except WebSocketDisconnect:
        pass
    finally:
        tracking_service.disconnect(order_id, websocket)
