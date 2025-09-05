from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..auth_jwt import hash_password, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    role: str | None = "customer"  # no full_name since your model doesn't have it

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 60 * 60 * 24

@router.post("/register")
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Your models define UserRole (not RoleEnum); fall back to that if RoleEnum missing
    RoleEnum = getattr(models, "RoleEnum", None) or getattr(models, "UserRole")
    role_value = payload.role or "customer"
    try:
        role = RoleEnum(role_value)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid role: {role_value}")

    user = models.User(
        email=payload.email,
        password_hash=hash_password(payload.password),  # your column name
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "role": user.role.value}

@router.post("/login", response_model=TokenOut)
def login(payload: RegisterIn, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user_id=user.id, role=user.role.value)
    return TokenOut(access_token=token)
