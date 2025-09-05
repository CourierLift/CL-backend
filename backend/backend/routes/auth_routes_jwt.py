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
    full_name: str | None = None
    role: str | None = "customer"

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 60 * 60 * 24

@router.post("/register")
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    role = getattr(models.RoleEnum, (payload.role or "customer"))
    user = models.User(email=payload.email, hashed_password=hash_password(payload.password),
                       full_name=payload.full_name, role=role)
    db.add(user); db.commit(); db.refresh(user)
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role.value}

@router.post("/login", response_model=TokenOut)
def login(payload: RegisterIn, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user_id=user.id, role=user.role.value)
    return TokenOut(access_token=token)
