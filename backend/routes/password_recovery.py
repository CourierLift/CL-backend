"""Password setup and recovery for an existing, non-admin pilot account."""
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..api_errors import StructuredErrorRoute
from ..auth_jwt import hash_password, session_version
from ..database import get_db
from ..models import User, UserRole
from ..rate_limit import auth_rate_limiter, request_identity
from ..services import password_recovery as recovery
from ..services.tracking import tracking_service

router = APIRouter(prefix="/auth/password", tags=["auth"], route_class=StructuredErrorRoute)
ACK = {"message": "If this email has a pilot account, a code will be sent."}


class RequestCode(BaseModel):
    email: EmailStr


class SetPassword(RequestCode):
    code: SecretStr = Field(min_length=1, max_length=64)
    new_password: SecretStr = Field(min_length=12, max_length=72)


@router.post("/request", status_code=202)
def request_code(payload: RequestCode, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not recovery.mail_configured():
        raise HTTPException(503, "Password email is unavailable. Contact the pilot operator.")
    auth_rate_limiter.check(scope="password.request.ip", identity=request_identity(request), limit=12, window_seconds=900)
    auth_rate_limiter.check(scope="password.request.total", identity="single-worker-pilot", limit=60, window_seconds=900)
    email = str(payload.email).lower()
    # Apply suppression equally to all addresses, including unknown ones.
    try:
        auth_rate_limiter.check(scope="password.mailbox", identity=recovery.recipient_key(email), limit=3, window_seconds=900)
    except HTTPException as exc:
        if exc.status_code != 429:
            raise
        return ACK
    user = db.query(User).filter(User.email == email, User.role != UserRole.admin).first()
    if user:
        tasks.add_task(recovery.send_code, email, recovery.make_code(user, int(time.time())))
    return ACK


@router.post("/confirm")
def set_password(payload: SetPassword, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    auth_rate_limiter.check(scope="password.confirm.ip", identity=request_identity(request), limit=15, window_seconds=900)
    email = str(payload.email).lower()
    auth_rate_limiter.check(scope="password.confirm.email", identity=recovery.recipient_key(email), limit=15, window_seconds=900)
    invalid = HTTPException(400, "That code is invalid or expired. Request a new code.")
    user = db.query(User).filter(User.email == email, User.role != UserRole.admin).first()
    if not user or not recovery.valid_code(user, payload.code.get_secret_value()):
        raise invalid
    try:
        new_hash = hash_password(payload.new_password.get_secret_value())
    except ValueError:
        raise HTTPException(422, "Use a password of at most 72 UTF-8 bytes.") from None
    # Competing confirmations cannot spend the same code twice.
    uid, old_version = user.id, session_version(user.password_hash)
    result = db.execute(update(User).where(User.id == uid, User.email == email, User.role != UserRole.admin,
                                           User.password_hash == user.password_hash)
                        .values(password_hash=new_hash).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise invalid
    db.commit()
    tasks.add_task(tracking_service.revoke_user, uid, old_version)
    return {"message": "Password saved. Sign in with your new password."}
