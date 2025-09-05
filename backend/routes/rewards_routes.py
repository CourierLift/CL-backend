from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..database import get_db
from ..deps_jwt import get_current_user
from .. import models

router = APIRouter(prefix="/rewards", tags=["rewards"])

class RewardIn(BaseModel):
    order_id: int | None = None
    type: str = "earn"   # earn | redeem | adjust
    points: int
    reason: str | None = None

@router.get("/balance", response_model=int)
def balance(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    evts = db.query(models.RewardEvent).filter(models.RewardEvent.user_id == user.id).all()
    return sum(e.points for e in evts)

@router.post("/event")
def add_event(payload: RewardIn, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    evt = models.RewardEvent(
        user_id=user.id,
        order_id=payload.order_id,
        type=getattr(models.RewardEventType, payload.type),
        points=payload.points,
        reason=payload.reason,
    )
    db.add(evt)
    db.commit()
    db.refresh(evt)
    return {"id": evt.id, "points": evt.points, "type": evt.type.value, "reason": evt.reason}