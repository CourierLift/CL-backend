from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set
from datetime import datetime, timezone
import json

router = APIRouter()

class ConnectionManager:
    """
    Minimal in-memory connection manager grouped by order_id.
    Dev-only; production should use a shared store (e.g., Redis).
    """
    def __init__(self) -> None:
        # order_id -> set[WebSocket]
        self.rooms: Dict[str, Set[WebSocket]] = {}

    async def connect(self, order_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms.setdefault(order_id, set()).add(websocket)

    def disconnect(self, order_id: str, websocket: WebSocket) -> None:
        room = self.rooms.get(order_id)
        if not room:
            return
        room.discard(websocket)
        if not room:
            self.rooms.pop(order_id, None)

    async def send_personal(self, websocket: WebSocket, payload: dict) -> None:
        await websocket.send_text(json.dumps(payload))

    async def broadcast(self, order_id: str, payload: dict) -> None:
        for ws in list(self.rooms.get(order_id, [])):
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                # Drop dead sockets
                self.disconnect(order_id, ws)

manager = ConnectionManager()

@router.websocket("/ws/track")
async def ws_track(
    websocket: WebSocket,
    order_id: str = Query(..., description="Order to join/broadcast within"),
    role: str = Query("client", description="client|courier|merchant|admin"),
):
    """
    Echo-style tracker room per order_id.

    Connect with:
      ws://127.0.0.1:8000/ws/track?order_id=123&role=client

    Any text you send will be echoed back to you and broadcast to others
    in the same order room.
    """
    await manager.connect(order_id, websocket)

    # Greet the new connection
    await manager.send_personal(websocket, {
        "type": "hello",
        "order_id": order_id,
        "role": role,
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": f"joined room {order_id} as {role}",
    })

    try:
        while True:
            msg = await websocket.receive_text()
            payload = {
                "type": "echo",
                "order_id": order_id,
                "role": role,
                "message": msg,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            # Echo to sender
            await manager.send_personal(websocket, payload)
            # Broadcast to everyone else in the room
            await manager.broadcast(order_id, {**payload, "type": "broadcast"})
    except WebSocketDisconnect:
        manager.disconnect(order_id, websocket)
        await manager.broadcast(order_id, {
            "type": "left",
            "order_id": order_id,
            "role": role,
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": "peer disconnected",
        })
