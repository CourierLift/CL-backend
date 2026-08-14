"""JSON authentication routes backed by the canonical JWT helpers."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_jwt import create_access_token, hash_password, verify_password
from ..database import get_db
from ..models import CourierProfile, User, UserRole
from ..schemas import LoginRequest, RegisterIn, TokenOut, UserOut
from ..services.eligibility import courier_profile_defaults
from ..settings import settings


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> User:
    email = str(payload.email).lower()
    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user = User(
        email=email,
        password_hash=password_hash,
        role=UserRole(payload.role),
    )
    db.add(user)

    if user.role == UserRole.courier:
        mode = payload.transportation_mode or "car"
        defaults = courier_profile_defaults(mode)
        profile = CourierProfile(
            user=user,
            transportation_mode=str(defaults["transportation_mode"]),
            max_weight_lb=payload.max_weight_lb or float(defaults["max_weight_lb"]),
            max_length_in=payload.max_length_in or float(defaults["max_length_in"]),
            max_width_in=payload.max_width_in or float(defaults["max_width_in"]),
            max_height_in=payload.max_height_in or float(defaults["max_height_in"]),
            max_volume_cu_ft=(
                payload.max_volume_cu_ft or float(defaults["max_volume_cu_ft"])
            ),
            capabilities=payload.capabilities,
        )
        db.add(profile)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered") from exc
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenOut:
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
