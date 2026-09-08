"""Canonical authorized order-detail retrieval."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps_jwt import get_current_user
from ..models import Order, User, UserRole
from ..schemas import OrderDetailOut


router = APIRouter(tags=["orders"])


@router.get("/orders/{order_id}", response_model=OrderDetailOut)
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    is_creator = order.user_id == current_user.id
    is_assigned_courier = order.assigned_courier_id == current_user.id
    is_admin = current_user.role == UserRole.admin
    if not (is_creator or is_assigned_courier or is_admin):
        raise HTTPException(status_code=403, detail="Not authorized to view this order")

    return order
