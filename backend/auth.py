# --- backend/auth.py ---------------------------------------------------------
# Auth: register, login (JWT), and /auth/me (protected).
# [Glossary: JWT] A signed string proving identity for a limited time.

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from passlib.hash import bcrypt
import jwt  # PyJWT
import os

from .database import get_db
from .models import User, UserRole
from .schemas import UserCreate, UserOut, Token

router = APIRouter(prefix="/auth", tags=["auth"])

# --- Settings (read from .env in dev) ---------------------------------------
SECRET = os.getenv("CL_SECRET_KEY", "change-me")
ACCESS_MIN = int(os.getenv("CL_ACCESS_MIN", "120"))
ALGORITHM = "HS256"

# OAuth2 password flow; relative path so Swagger shows the Authorize button
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def create_token(user_id: int, role: str) -> str:
    """Create a signed JWT for the user. [Glossary: JWT]"""
    payload = {
        "sub": str(user_id),           # subject (user id)
        "role": role,                  # role claim
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_MIN),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


@router.post("/register", response_model=UserOut)
def register(data: UserCreate, db: Session = Depends(get_db)):
    """Create a new user with hashed password."""
    exists = db.query(User).filter(User.email == data.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=data.email,
        password_hash=bcrypt.hash(data.password),
        role=UserRole(data.role),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),  # Swagger posts x-www-form-urlencoded
    db: Session = Depends(get_db),
):
    """Verify credentials and return a JWT access token."""
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not bcrypt.verify(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return Token(access_token=create_token(user.id, user.role.value))


# ------------------------ Protected helpers & route --------------------------

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode JWT and return the current user or raise 401."""
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # SQLAlchemy 2.x way to get by PK
    user: Optional[User] = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)):
    """Return the logged-in user (requires Bearer token)."""
    return current_user
# ----------------------------------------------------------------------------- 

