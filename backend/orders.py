"""Order management routes and helpers for the Courier Lifts API.

This module centralizes all quote and order functionality.  It computes quotes
for delivery based on pseudo-geocoded addresses and various multipliers,
creates new orders, lists a user's orders, and updates the status of an order.

All routes in this module require a valid JWT for authentication and depend
on the `deps_jwt.get_current_user` dependency to resolve the current user.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from typing import List, Optional
from math import sqrt

from .database import get_db
from .models import Order, OrderStatus, User, UserRole
from .schemas import QuoteRequest, QuoteResponse, OrderCreate, OrderOut, StatusUpdate
from .deps_jwt import get_current_user

router = APIRouter(prefix="", tags=["orders"])


def fake_geocode(addr: str) -> Optional[tuple[float, float]]:
    if not addr or not addr.strip():
        return None
    total = sum(ord(c) for c in addr)
    return (30.0 + (total % 5000) / 100.0, -100.0 - (total % 5000) / 100.0)


def haversine_like(pu: tuple[float, float], do: tuple[float, float]) -> float:
    (a, b), (c, d) = pu, do
    return max(0.5, sqrt((a - c) ** 2 + (b - d) ** 2) * 69.0)


def vehicle_multiplier(vehicle: str) -> float:
    return {
        "bike": 1.0,
        "car": 1.2,
        "van": 1.5,
        "truck": 2.0,
    }.get(vehicle, 1.2)


def item_tier(item_type: str) -> str:
    t = (item_type or "").lower()
    if any(k in t for k in ["fragile", "glass", "art"]):
        return "fragile"
    if any(k in t for k in ["food", "meal", "grocery"]):
        return "perishable"
    if any(k in t for k in ["electronics", "laptop", "tv"]):
        return "electronics"
    return "standard"


def compute_quote(data: QuoteRequest) -> QuoteResponse:
    pu = fake_geocode(getattr(data, "pickup_addr", "")) if hasattr(data, "pickup_addr") else None
    do = fake_geocode(getattr(data, "dropoff_addr", "")) if hasattr(data, "dropoff_addr") else None
    if pu is None and all(hasattr(data, k) for k in ("pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng")):
        pu = (float(data.pickup_lat), float(data.pickup_lng))
        do = (float(data.dropoff_lat), float(data.dropoff_lng))
    if pu is None or do is None:
        raise HTTPException(status_code=400, detail="Could not geocode addresses")

    miles = haversine_like(pu, do)
    base = 3.5
    per_mile = 1.75 * vehicle_multiplier(data.vehicle)
    qty_factor = max(1.0, (data.quantity or 1) * 0.9)
    weight_factor = 1.0 + min(0.8, (data.weight_lb or 0.0) / 100.0)
    size_factor = 1.0 + min(
        0.6,
        ((data.length_in or 12) * (data.width_in or 8) * (data.height_in or 6)) / 1728.0 * 0.2,
    )

    price = round((base + miles * per_mile) * qty_factor * weight_factor * size_factor, 2)
    eta = max(10, int(miles * 3))
    return QuoteResponse(price=price, eta=eta, miles=round(miles, 2), tier=item_tier(getattr(data, "item_type", "")))


@router.post("/quote", response_model=QuoteResponse)
def quote_price(payload: QuoteRequest) -> QuoteResponse:
    return compute_quote(payload)


@router.post("/quote/estimate")
def quote_estimate(payload: dict = Body(...)):
    origin = str(payload.get("origin", "")).strip()
    destination = str(payload.get("destination", "")).strip()
    weight_kg = float(payload.get("weight_kg") or 0)
    vehicle = (payload.get("vehicle") or "car").lower()
    item_type = (payload.get("item_type") or "standard").lower()

    class _Q: pass
    q = _Q()
    q.pickup_addr = origin
    q.dropoff_addr = destination
    q.vehicle = vehicle
    q.item_type = item_type
    q.quantity = 1
    q.weight_lb = weight_kg * 2.20462
    q.length_in = 12; q.width_in = 8; q.height_in = 6

    res = compute_quote(q)

    return {
        "price_total": res.price,
        "eta_min": res.eta,
        "miles": res.miles,
        "tier": res.tier,
    }


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrderOut:
    pu = fake_geocode(getattr(payload, "pickup_addr", "")) if hasattr(payload, "pickup_addr") else None
    do = fake_geocode(getattr(payload, "dropoff_addr", "")) if hasattr(payload, "dropoff_addr") else None
    if pu is None and all(hasattr(payload, k) for k in ("pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng")):
        pu = (payload.pickup_lat, payload.pickup_lng)
        do = (payload.dropoff_lat, payload.dropoff_lng)
    if pu is None or do is None:
        raise HTTPException(status_code=400, detail="Invalid pickup/dropoff")

    quote = compute_quote(payload)
    order = Order(
        user_id=current_user.id,
        pickup_lat=pu[0], pickup_lng=pu[1],
        dropoff_lat=do[0], dropoff_lng=do[1],
        vehicle=payload.vehicle,
        item_type=getattr(payload, "item_type", "standard"),
        quantity=payload.quantity or 1,
        weight_lb=payload.weight_lb or 0.0,
        length_in=payload.length_in or 12,
        width_in=payload.width_in or 8,
        height_in=payload.height_in or 6,
        price=quote.price,
        eta_min=quote.eta,
        status=OrderStatus.pending,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/create_compat", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order_compat(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrderOut:
    origin = str(payload.get("origin", "")).strip()
    destination = str(payload.get("destination", "")).strip()
    if not origin or not destination:
        raise HTTPException(status_code=422, detail="origin and destination are required")

    vehicle = (payload.get("vehicle") or "car").lower()
    item_type = (payload.get("item_type") or "standard").lower()
    quantity = int(payload.get("quantity") or 1)
    weight_lb = float(payload.get("weight_kg") or 0.0) * 2.20462
    length_in = float(payload.get("length_in") or 12)
    width_in  = float(payload.get("width_in")  or 8)
    height_in = float(payload.get("height_in") or 6)

    pu = fake_geocode(origin)
    do = fake_geocode(destination)
    if pu is None or do is None:
        raise HTTPException(status_code=400, detail="Invalid origin/destination")

    class _Q: pass
    q = _Q()
    q.pickup_addr = origin
    q.dropoff_addr = destination
    q.vehicle = vehicle
    q.item_type = item_type
    q.quantity = quantity
    q.weight_lb = weight_lb
    q.length_in = length_in; q.width_in = width_in; q.height_in = height_in

    quote = compute_quote(q)

    order = Order(
        user_id=current_user.id,
        pickup_lat=pu[0], pickup_lng=pu[1],
        dropoff_lat=do[0], dropoff_lng=do[1],
        vehicle=vehicle,
        item_type=item_type,
        quantity=quantity,
        weight_lb=weight_lb,
        length_in=length_in,
        width_in=width_in,
        height_in=height_in,
        price=quote.price,
        eta_min=quote.eta,
        status=OrderStatus.pending,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("/orders/mine", response_model=List[OrderOut])
def list_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[OrderOut]:
    return (
        db.query(Order)
        .filter(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
        .all()
    )


ALLOWED_CHAIN: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.assigned, OrderStatus.canceled},
    OrderStatus.assigned: {OrderStatus.picked_up, OrderStatus.canceled},
    OrderStatus.picked_up: {OrderStatus.delivered},
    OrderStatus.delivered: set(),
    OrderStatus.canceled: set(),
}


def can_transition(current: OrderStatus, nxt: OrderStatus) -> bool:
    return nxt in ALLOWED_CHAIN.get(current, set())


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
def update_order_status(
    order_id: int,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrderOut:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        new_status = OrderStatus(payload.status)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid status")

    if current_user.role == UserRole.customer:
        if order.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not your order")
        if new_status != OrderStatus.canceled:
            raise HTTPException(status_code=403, detail="Customers may only cancel")
        if order.status not in {OrderStatus.pending, OrderStatus.assigned}:
            raise HTTPException(status_code=409, detail=f"Cannot cancel from {order.status}")
    elif current_user.role in {UserRole.courier, UserRole.admin}:
        if new_status != OrderStatus.canceled and not can_transition(order.status, new_status):
            raise HTTPException(status_code=409, detail=f"Illegal transition {order.status} -> {new_status}")
    else:
        raise HTTPException(status_code=403, detail="Insufficient role")

    order.status = new_status
    db.commit()
    db.refresh(order)
    return order

