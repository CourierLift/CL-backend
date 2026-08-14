"""Canonical quote and marketplace order routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from .database import get_db
from .deps_jwt import get_current_user
from .models import Order, OrderStatus, User, UserRole
from .quote_engine import QuoteResult, estimate_quote
from .schemas import (
    AddressQuoteRequest,
    OrderCreate,
    OrderCreateCompat,
    OrderOut,
    QuoteEstimateResponse,
    QuoteRequest,
    QuoteResponse,
    StatusUpdate,
)
from .services.eligibility import evaluate_courier_eligibility
from .services.tracking import OrderEventType, make_order_event, tracking_service
from .settings import settings


router = APIRouter(tags=["orders"])
CREATOR_ROLES = {UserRole.customer, UserRole.merchant}

ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.assigned, OrderStatus.canceled},
    OrderStatus.assigned: {OrderStatus.picked_up, OrderStatus.canceled},
    OrderStatus.picked_up: {OrderStatus.delivered},
    OrderStatus.delivered: set(),
    OrderStatus.canceled: set(),
}


def can_transition(current: OrderStatus, next_status: OrderStatus) -> bool:
    return next_status in ALLOWED_TRANSITIONS.get(current, set())


def _require_order_creator(user: User) -> None:
    if user.role not in CREATOR_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only customers and merchants may create deliveries",
        )


def _require_courier(user: User) -> None:
    if user.role != UserRole.courier:
        raise HTTPException(status_code=403, detail="Courier role required")


def _coordinate_quote(payload: QuoteRequest) -> QuoteResult:
    return estimate_quote(
        pickup=(payload.pickup_lat, payload.pickup_lng),
        dropoff=(payload.dropoff_lat, payload.dropoff_lng),
        transportation_mode=payload.vehicle,
        item_type=payload.item_type,
        quantity=payload.quantity,
        weight_lb=payload.weight_lb,
        length_in=payload.length_in,
        width_in=payload.width_in,
        height_in=payload.height_in,
        weather=payload.weather,
        traffic=payload.traffic,
        surge=payload.surge,
    )


def _address_quote(payload: AddressQuoteRequest) -> QuoteResult:
    return estimate_quote(
        development_fallback_miles=settings.CL_DEVELOPMENT_FALLBACK_MILES,
        transportation_mode=payload.vehicle,
        item_type=payload.item_type,
        quantity=payload.quantity,
        weight_lb=payload.weight_kg * 2.2046226218,
        length_in=payload.length_in,
        width_in=payload.width_in,
        height_in=payload.height_in,
        weather=payload.weather,
        traffic=payload.traffic,
        surge=payload.surge,
    )


def _quote_response(result: QuoteResult) -> QuoteResponse:
    return QuoteResponse(
        price=result.price_total,
        eta=result.eta_min,
        miles=result.miles,
        tier=result.tier,
        estimated=result.estimated,
        distance_source=result.distance_source,
    )


def _estimate_response(result: QuoteResult) -> QuoteEstimateResponse:
    return QuoteEstimateResponse(
        price_total=result.price_total,
        eta_min=result.eta_min,
        miles=result.miles,
        tier=result.tier,
        estimated=result.estimated,
        distance_source=result.distance_source,
    )


def _tracking_data(order: Order) -> dict[str, object]:
    return {
        "status": order.status.value,
        "creator_id": order.user_id,
        "assigned_courier_id": order.assigned_courier_id,
    }


@router.post("/quote", response_model=QuoteResponse)
def quote_price(payload: QuoteRequest) -> QuoteResponse:
    return _quote_response(_coordinate_quote(payload))


@router.post("/quote/estimate", response_model=QuoteEstimateResponse)
def quote_estimate(payload: AddressQuoteRequest) -> QuoteEstimateResponse:
    return _estimate_response(_address_quote(payload))


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    _require_order_creator(current_user)
    quote = _coordinate_quote(payload)
    order = Order(
        user_id=current_user.id,
        pickup_lat=payload.pickup_lat,
        pickup_lng=payload.pickup_lng,
        dropoff_lat=payload.dropoff_lat,
        dropoff_lng=payload.dropoff_lng,
        vehicle=quote.transportation_mode,
        item_type=payload.item_type.strip().lower(),
        delivery_requirements=payload.delivery_requirements,
        quantity=payload.quantity,
        weight_lb=payload.weight_lb,
        length_in=payload.length_in,
        width_in=payload.width_in,
        height_in=payload.height_in,
        price=quote.price_total,
        eta_min=quote.eta_min,
        distance_miles=quote.miles,
        distance_estimated=quote.estimated,
        distance_source=quote.distance_source,
        weather=payload.weather,
        traffic=payload.traffic,
        surge_multiplier=payload.surge,
        status=OrderStatus.pending,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    await tracking_service.publish(
        make_order_event(OrderEventType.created, order.id, **_tracking_data(order))
    )
    return order


@router.post(
    "/orders/create_compat",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_order_compat(
    payload: OrderCreateCompat,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    _require_order_creator(current_user)
    quote = _address_quote(payload)
    order = Order(
        user_id=current_user.id,
        origin=payload.origin,
        destination=payload.destination,
        vehicle=quote.transportation_mode,
        item_type=payload.item_type.strip().lower(),
        delivery_requirements=payload.delivery_requirements,
        quantity=payload.quantity,
        weight_lb=payload.weight_kg * 2.2046226218,
        length_in=payload.length_in,
        width_in=payload.width_in,
        height_in=payload.height_in,
        price=quote.price_total,
        eta_min=quote.eta_min,
        distance_miles=quote.miles,
        distance_estimated=quote.estimated,
        distance_source=quote.distance_source,
        weather=payload.weather,
        traffic=payload.traffic,
        surge_multiplier=payload.surge,
        status=OrderStatus.pending,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    await tracking_service.publish(
        make_order_event(OrderEventType.created, order.id, **_tracking_data(order))
    )
    return order


@router.get("/orders/mine", response_model=list[OrderOut])
def list_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Order]:
    return (
        db.query(Order)
        .filter(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .all()
    )


@router.get("/orders/available", response_model=list[OrderOut])
def list_available_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Order]:
    _require_courier(current_user)
    candidates = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.pending,
            Order.assigned_courier_id.is_(None),
        )
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    return [
        order
        for order in candidates
        if evaluate_courier_eligibility(current_user, order).eligible
    ]


@router.post("/orders/{order_id}/claim", response_model=OrderOut)
async def claim_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    _require_courier(current_user)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.pending or order.assigned_courier_id is not None:
        raise HTTPException(status_code=409, detail="Order is no longer available")

    eligibility = evaluate_courier_eligibility(current_user, order)
    if not eligibility.eligible:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Courier is not eligible for this delivery",
                "reasons": list(eligibility.reasons),
            },
        )

    assigned_at = datetime.now(timezone.utc)
    claim = (
        update(Order)
        .where(
            Order.id == order_id,
            Order.status == OrderStatus.pending,
            Order.assigned_courier_id.is_(None),
        )
        .values(
            assigned_courier_id=current_user.id,
            assigned_at=assigned_at,
            status=OrderStatus.assigned,
        )
        .execution_options(synchronize_session=False)
    )
    result = db.execute(claim)
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Order was claimed by another courier")

    db.commit()
    db.expire_all()
    claimed_order = db.get(Order, order_id)
    if claimed_order is None:
        raise HTTPException(status_code=404, detail="Order not found after claim")
    await tracking_service.publish(
        make_order_event(
            OrderEventType.claimed,
            claimed_order.id,
            **_tracking_data(claimed_order),
        )
    )
    return claimed_order


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
async def update_order_status(
    order_id: int,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = OrderStatus(order.status)
    next_status = OrderStatus(payload.status)
    if next_status == OrderStatus.assigned:
        raise HTTPException(status_code=409, detail="Use the claim endpoint to assign an order")

    if current_user.role in CREATOR_ROLES:
        if order.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not your order")
        if next_status != OrderStatus.canceled:
            raise HTTPException(
                status_code=403,
                detail="Customers and merchants may only cancel their own order",
            )
    elif current_user.role == UserRole.courier:
        if order.assigned_courier_id != current_user.id:
            raise HTTPException(status_code=403, detail="Order is assigned to another courier")
    elif current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Insufficient role")

    if not can_transition(current_status, next_status):
        raise HTTPException(
            status_code=409,
            detail=f"Illegal transition {current_status.value} -> {next_status.value}",
        )

    order.status = next_status
    if next_status == OrderStatus.delivered:
        order.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)

    await tracking_service.publish(
        make_order_event(
            OrderEventType.status_changed,
            order.id,
            previous_status=current_status.value,
            actor_id=current_user.id,
            **_tracking_data(order),
        )
    )
    if next_status == OrderStatus.delivered:
        await tracking_service.publish(
            make_order_event(
                OrderEventType.completed,
                order.id,
                actor_id=current_user.id,
                **_tracking_data(order),
            )
        )
    return order

