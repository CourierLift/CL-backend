from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt
import os


# This module provides password hashing and JWT token creation/decoding
# for the Courier Lifts API. It replaces the older auth.py implementation.

# CryptContext with bcrypt scheme for password hashing
PWD = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Secret and algorithm settings are configurable via environment variables
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
ALGO = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return PWD.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a hashed password."""
    return PWD.verify(plain, hashed)

def create_access_token(user_id: int, role: str) -> str:
    """Create a JWT access token for the given user id and role."""
    exp = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MIN)
    payload = {"user_id": user_id, "role": role, "exp": int(exp.timestamp())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)

def decode_access_token(token: str):
    """Decode a JWT and return the user id and role encoded within it."""
    data = jwt.decode(token, SECRET_KEY, algorithms=[ALGO])
    return data["user_id"], data.get("role", "customer")