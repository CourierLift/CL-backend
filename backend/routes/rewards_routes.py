"""Canonical rewards balance and ledger-event routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps_jwt import get_current_user
from ..models import Order, RewardEvent, RewardEventType, User, UserRole
from ..schemas import RewardEventOut, RewardIn


router = APIRouter(prefix="/rewards", tags=["rewards"])


@router.get("/balance", response_model=int)
def balance(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> int:
    total = (
        db.query(func.coalesce(func.sum(RewardEvent.points), 0))
        .filter(RewardEvent.user_id == user.id)
        .scalar()
    )
    return int(total or 0)


@router.post("/event", response_model=RewardEventOut)
def add_event(
    payload: RewardIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RewardEventOut:
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=403,
            detail="Reward events may only be created by a trusted administrator",
        )

    if payload.order_id is not None:
        order = db.get(Order, payload.order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
    event = RewardEvent(
        user_id=user.id,
        order_id=payload.order_id,
        type=RewardEventType(payload.type),
        points=payload.points,
        reason=payload.reason,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return RewardEventOut(
        id=event.id,
        points=event.points,
        type=event.type.value,
        reason=event.reason,
    )
