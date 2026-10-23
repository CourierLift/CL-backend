"""JSON authentication routes backed by the canonical JWT helpers."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth_jwt import create_access_token, verify_password
from ..database import get_db
from ..models import User
from ..rate_limit import auth_rate_limiter, request_identity
from ..schemas import LoginRequest, RegisterIn, TokenOut, UserOut
from ..services.account_provisioning import DuplicateAccountError, provision_account
from ..settings import settings


router = APIRouter(prefix="/auth", tags=["auth"])
REGISTRATION_CLOSED_MESSAGE = (
    "Registration is invite-only. An operator must provision your pilot account. "
    "Existing accounts can still sign in."
)


@router.post("/register", response_model=UserOut)
def register(
    payload: RegisterIn,
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    auth_rate_limiter.check(
        scope="auth.register",
        identity=request_identity(request),
        limit=settings.CL_AUTH_REGISTER_RATE_LIMIT,
        window_seconds=settings.CL_AUTH_RATE_WINDOW_SECONDS,
    )
    # Validation and rate limiting stay intact. The gate is checked before any
    # account lookup, hashing, or write. No request input controls this setting.
    if settings.CL_REGISTRATION_MODE != "public":
        raise HTTPException(status_code=403, detail=REGISTRATION_CLOSED_MESSAGE)
    try:
        return provision_account(db, payload)
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=400, detail="Email already registered") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/login", response_model=TokenOut)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenOut:
    auth_rate_limiter.check(
        scope="auth.login",
        identity=request_identity(request),
        limit=settings.CL_AUTH_LOGIN_RATE_LIMIT,
        window_seconds=settings.CL_AUTH_RATE_WINDOW_SECONDS,
    )
    user = db.query(User).filter(User.email == str(payload.email).lower()).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenOut(
        access_token=create_access_token(user.id, user.role.value),
        expires_in=settings.CL_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
