"""Canonical quote and marketplace order routes."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .api_errors import StructuredErrorRoute, api_error
from .database import get_db
from .deps_jwt import get_current_user
from .models import Order, OrderStatus, Quote, User, UserRole
from .quote_engine import PRICING_ENGINE_VERSION, QuoteResult, estimate_quote
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
from .services.route_distance import RouteDistanceError, google_route_distance
from .services.tracking import OrderEventType, make_order_event, tracking_service
from .settings import settings


router = APIRouter(tags=["orders"])
# Order creation routes use the structured {"detail": {"code", ...}} error contract.
create_router = APIRouter(tags=["orders"], route_class=StructuredErrorRoute)
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
CREATOR_ROLES = {UserRole.customer, UserRole.merchant}
PRODUCTION_ENVS = {"prod", "production"}

ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.assigned, OrderStatus.canceled},
    OrderStatus.assigned: {OrderStatus.picked_up, OrderStatus.canceled},
    OrderStatus.picked_up: {OrderStatus.in_transit},
    OrderStatus.in_transit: {OrderStatus.delivered},
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


def pilot_conditions() -> dict[str, object]:
    """Server-controlled pricing conditions; client values are ignored."""
    return {
        "weather": settings.CL_PILOT_WEATHER.strip().lower(),
        "traffic": settings.CL_PILOT_TRAFFIC.strip().lower(),
        "surge": float(settings.CL_PILOT_SURGE),
    }


def _coordinate_quote(payload: QuoteRequest, conditions: dict[str, object]) -> QuoteResult:
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
        weather=conditions["weather"],
        traffic=conditions["traffic"],
        surge=conditions["surge"],
    )


async def _address_quote(
    payload: AddressQuoteRequest, conditions: dict[str, object]
) -> QuoteResult:
    if settings.CL_APP_ENV.strip().lower() in PRODUCTION_ENVS:
        try:
            route = await google_route_distance(
                origin=payload.origin,
                destination=payload.destination,
                transportation_mode=payload.vehicle,
                api_key=settings.CL_GOOGLE_MAPS_API_KEY or "",
            )
        except RouteDistanceError as exc:
            raise HTTPException(
                status_code=503,
                detail="Production route distance is temporarily unavailable",
            ) from exc
        distance_kwargs = {
            "authoritative_distance_miles": route.miles,
            "authoritative_distance_source": route.source,
        }
    else:
        distance_kwargs = {
            "development_fallback_miles": settings.CL_DEVELOPMENT_FALLBACK_MILES,
        }

    return estimate_quote(
        **distance_kwargs,
        transportation_mode=payload.vehicle,
        item_type=payload.item_type,
        quantity=payload.quantity,
        weight_lb=payload.weight_kg * 2.2046226218,
        length_in=payload.length_in,
        width_in=payload.width_in,
        height_in=payload.height_in,
        weather=conditions["weather"],
        traffic=conditions["traffic"],
        surge=conditions["surge"],
    )


def _pricing_snapshot(
    result: QuoteResult,
    *,
    item_type: str,
    quantity: int,
    weight_lb: float,
    length_in: float,
    width_in: float,
    height_in: float,
    weather: str,
    traffic: str,
    surge: float,
) -> dict[str, object]:
    """Freeze every material pricing input/result used for this order."""
    return {
        "engine_version": PRICING_ENGINE_VERSION,
        "customer_total": result.price_total,
        "breakdown": dict(result.breakdown),
        "mileage": result.miles,
        "eta_min": result.eta_min,
        "tier": result.tier,
        "vehicle_type": result.transportation_mode,
        "inputs": {
            "vehicle": result.transportation_mode,
            "item_type": item_type.strip().lower(),
            "quantity": int(quantity),
            "weight_lb": float(weight_lb),
            "length_in": float(length_in),
            "width_in": float(width_in),
            "height_in": float(height_in),
            "weather": weather.strip().lower(),
            "traffic": traffic.strip().lower(),
            "surge": float(surge),
            "distance_miles": result.miles,
            "distance_estimated": result.estimated,
            "distance_source": result.distance_source,
        },
    }


# ---------------------------------------------------------------------------
# Quote binding and idempotency helpers
# ---------------------------------------------------------------------------

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{8,128}$")


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _address_details(payload: AddressQuoteRequest) -> dict[str, object]:
    return {
        "kind": "address",
        "origin": payload.origin.strip(),
        "destination": payload.destination.strip(),
        "vehicle": payload.vehicle,
        "item_type": payload.item_type.strip().lower(),
        "quantity": int(payload.quantity),
        "weight_kg": float(payload.weight_kg),
        "length_in": float(payload.length_in),
        "width_in": float(payload.width_in),
        "height_in": float(payload.height_in),
        "delivery_requirements": list(payload.delivery_requirements),
    }


def _coordinate_details(payload: QuoteRequest) -> dict[str, object]:
    return {
        "kind": "coordinate",
        "pickup_lat": float(payload.pickup_lat),
        "pickup_lng": float(payload.pickup_lng),
        "dropoff_lat": float(payload.dropoff_lat),
        "dropoff_lng": float(payload.dropoff_lng),
        "vehicle": payload.vehicle,
        "item_type": payload.item_type.strip().lower(),
        "quantity": int(payload.quantity),
        "weight_lb": float(payload.weight_lb),
        "length_in": float(payload.length_in),
        "width_in": float(payload.width_in),
        "height_in": float(payload.height_in),
        "delivery_requirements": list(payload.delivery_requirements),
    }


def _as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; values are always written in UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _optional_creator(
    token: str | None = Depends(optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    if not token:
        return None
    user = get_current_user(token=token, db=db)
    return user if user.role in CREATOR_ROLES else None


def _store_quote(
    db: Session,
    *,
    user: User,
    kind: str,
    details: dict[str, object],
    result: QuoteResult,
    conditions: dict[str, object],
    weight_lb: float,
    payload: QuoteRequest | AddressQuoteRequest,
) -> Quote:
    now = datetime.now(timezone.utc)
    quote = Quote(
        id=f"q_{secrets.token_hex(16)}",
        user_id=user.id,
        kind=kind,
        details_hash=_hash(details),
        pricing_conditions=dict(conditions),
        pricing_snapshot=_pricing_snapshot(
            result,
            item_type=payload.item_type,
            quantity=payload.quantity,
            weight_lb=weight_lb,
            length_in=payload.length_in,
            width_in=payload.width_in,
            height_in=payload.height_in,
            weather=str(conditions["weather"]),
            traffic=str(conditions["traffic"]),
            surge=float(conditions["surge"]),
        ),
        price_total=result.price_total,
        eta_min=result.eta_min,
        distance_miles=result.miles,
        distance_estimated=result.estimated,
        distance_source=result.distance_source,
        tier=result.tier,
        pricing_engine_version=PRICING_ENGINE_VERSION,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.CL_QUOTE_TTL_SECONDS),
    )
    db.add(quote)
    db.commit()
    return quote


def _quote_binding_fields(quote: Quote | None) -> dict[str, object]:
    if quote is None:
        return {}
    expires_at = _as_utc(quote.expires_at)
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return {
        "quote_id": quote.id,
        "expires_at": expires_at,
        "expires_in_seconds": max(0, remaining),
        "pricing_conditions": quote.pricing_conditions,
    }


def _quote_response(result: QuoteResult, quote: Quote | None = None) -> QuoteResponse:
    return QuoteResponse(
        price=result.price_total,
        eta=result.eta_min,
        miles=result.miles,
        tier=result.tier,
        estimated=result.estimated,
        distance_source=result.distance_source,
        **_quote_binding_fields(quote),
    )


def _estimate_response(result: QuoteResult, quote: Quote | None = None) -> QuoteEstimateResponse:
    return QuoteEstimateResponse(
        price_total=result.price_total,
        eta_min=result.eta_min,
        miles=result.miles,
        tier=result.tier,
        estimated=result.estimated,
        distance_source=result.distance_source,
        **_quote_binding_fields(quote),
    )


def _tracking_data(order: Order) -> dict[str, object]:
    return {
        "status": order.status.value,
        "creator_id": order.user_id,
        "assigned_courier_id": order.assigned_courier_id,
    }


def _redacted_available_order(order: Order) -> OrderOut:
    return OrderOut.model_validate(order).model_copy(
        update={
            "origin": None,
            "destination": None,
            "pickup_lat": None,
            "pickup_lng": None,
            "dropoff_lat": None,
            "dropoff_lng": None,
        }
    )


@router.post("/quote", response_model=QuoteResponse, response_model_exclude_none=True)
def quote_price(
    payload: QuoteRequest,
    db: Session = Depends(get_db),
    creator: User | None = Depends(_optional_creator),
) -> QuoteResponse:
    conditions = pilot_conditions()
    result = _coordinate_quote(payload, conditions)
    quote = None
    if creator is not None:
        quote = _store_quote(
            db,
            user=creator,
            kind="coordinate",
            details=_coordinate_details(payload),
            result=result,
            conditions=conditions,
            weight_lb=payload.weight_lb,
            payload=payload,
        )
    return _quote_response(result, quote)


@router.post(
    "/quote/estimate",
    response_model=QuoteEstimateResponse,
    response_model_exclude_none=True,
)
async def quote_estimate(
    payload: AddressQuoteRequest,
    db: Session = Depends(get_db),
    creator: User | None = Depends(_optional_creator),
) -> QuoteEstimateResponse:
    conditions = pilot_conditions()
    result = await _address_quote(payload, conditions)
    quote = None
    if creator is not None:
        quote = _store_quote(
            db,
            user=creator,
            kind="address",
            details=_address_details(payload),
            result=result,
            conditions=conditions,
            weight_lb=payload.weight_kg * 2.2046226218,
            payload=payload,
        )
    return _estimate_response(result, quote)


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if key is None or not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise api_error(
            400,
            "idempotency_key_required",
            "An Idempotency-Key header of 8-128 visible ASCII characters is required.",
        )
    return key


def _order_for_key(db: Session, user_id: int, key: str) -> Order | None:
    return (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.idempotency_key == key)
        .one_or_none()
    )


def _replay_or_conflict(order: Order, request_hash: str, response: Response) -> Order:
    if order.request_hash != request_hash:
        raise api_error(
            409,
            "idempotency_key_reused",
            "This Idempotency-Key was already used for a different request.",
        )
    response.status_code = status.HTTP_200_OK
    return order


def _quote_already_used(order_id: int) -> HTTPException:
    return api_error(
        409,
        "quote_already_used",
        "An order was already created from this quote.",
        order_id=order_id,
    )


async def _create_from_quote(
    *,
    kind: str,
    payload: OrderCreate | OrderCreateCompat,
    details: dict[str, object],
    location: dict[str, object],
    request: Request,
    response: Response,
    db: Session,
    current_user: User,
) -> Order:
    if current_user.role not in CREATOR_ROLES:
        raise api_error(403, "forbidden", "Only customers and merchants may create deliveries")
    key = _idempotency_key(request)
    request_hash = _hash({"quote_id": payload.quote_id, "details": details})

    # 1. Idempotent replay is resolved before any quote validation so a retry
    #    after quote expiry still returns the original order.
    existing = _order_for_key(db, current_user.id, key)
    if existing is not None:
        return _replay_or_conflict(existing, request_hash, response)

    # 2. Quote validation.
    if not payload.quote_id:
        raise api_error(422, "quote_required", "A quote_id from a current quote is required.",
                        fields=[{"loc": ["body", "quote_id"], "message": "Field required",
                                 "type": "missing"}])
    quote = db.get(Quote, payload.quote_id)
    if quote is None or quote.user_id != current_user.id:
        raise api_error(404, "quote_not_found", "Quote not found.")
    if quote.kind != kind or quote.details_hash != _hash(details):
        raise api_error(
            409,
            "quote_details_mismatch",
            "Delivery details changed since this quote. Request a new quote.",
        )
    used_by = db.query(Order.id).filter(Order.quote_id == quote.id).scalar()
    if used_by is not None:
        raise _quote_already_used(used_by)
    if _as_utc(quote.expires_at) <= datetime.now(timezone.utc):
        raise api_error(409, "quote_expired", "This quote has expired. Request a new quote.")

    conditions = quote.pricing_conditions
    order = Order(
        user_id=current_user.id,
        **location,
        vehicle=str(details["vehicle"]),
        item_type=str(details["item_type"]),
        delivery_requirements=list(details["delivery_requirements"]),
        quantity=int(details["quantity"]),
        weight_lb=float(quote.pricing_snapshot["inputs"]["weight_lb"]),
        length_in=float(details["length_in"]),
        width_in=float(details["width_in"]),
        height_in=float(details["height_in"]),
        price=quote.price_total,
        eta_min=quote.eta_min,
        distance_miles=quote.distance_miles,
        distance_estimated=quote.distance_estimated,
        distance_source=quote.distance_source,
        weather=str(conditions["weather"]),
        traffic=str(conditions["traffic"]),
        surge_multiplier=float(conditions["surge"]),
        pricing_engine_version=quote.pricing_engine_version,
        pricing_snapshot=dict(quote.pricing_snapshot),
        quote_id=quote.id,
        idempotency_key=key,
        request_hash=request_hash,
        status=OrderStatus.pending,
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request won the unique constraint race. Re-resolve
        # against committed state instead of creating a second order.
        db.rollback()
        existing = _order_for_key(db, current_user.id, key)
        if existing is not None:
            return _replay_or_conflict(existing, request_hash, response)
        used_by = db.query(Order.id).filter(Order.quote_id == quote.id).scalar()
        if used_by is not None:
            raise _quote_already_used(used_by)
        raise
    db.refresh(order)
    await tracking_service.publish(
        make_order_event(OrderEventType.created, order.id, **_tracking_data(order))
    )
    return order


@create_router.post(
    "/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: OrderCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    return await _create_from_quote(
        kind="coordinate",
        payload=payload,
        details=_coordinate_details(payload),
        location={
            "pickup_lat": payload.pickup_lat,
            "pickup_lng": payload.pickup_lng,
            "dropoff_lat": payload.dropoff_lat,
            "dropoff_lng": payload.dropoff_lng,
        },
        request=request,
        response=response,
        db=db,
        current_user=current_user,
    )


@create_router.post(
    "/orders/create_compat",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_order_compat(
    payload: OrderCreateCompat,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    return await _create_from_quote(
        kind="address",
        payload=payload,
        details=_address_details(payload),
        location={"origin": payload.origin, "destination": payload.destination},
        request=request,
        response=response,
        db=db,
        current_user=current_user,
    )


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
) -> list[OrderOut]:
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
        _redacted_available_order(order)
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
    if next_status == OrderStatus.delivered and order.proof is None:
        raise HTTPException(status_code=409, detail="Delivery proof is required before completion")

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
