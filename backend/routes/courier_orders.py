"""Courier-specific order history routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps_jwt import get_current_user
from ..models import Order, User, UserRole
from ..schemas import OrderOut


router = APIRouter(tags=["orders"])


@router.get("/orders/assigned", response_model=list[OrderOut])
def list_assigned_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Order]:
    """Return every order assigned to the authenticated courier.

    This is the server-authoritative recovery path for claimed work. It allows a
    courier to refresh, sign in on another browser, or switch devices without
    losing access to active or completed deliveries.
    """
    if current_user.role != UserRole.courier:
        raise HTTPException(status_code=403, detail="Courier role required")

    return (
        db.query(Order)
        .filter(Order.assigned_courier_id == current_user.id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .all()
    )
