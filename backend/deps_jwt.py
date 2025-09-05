from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .auth_jwt import decode_access_token
from .database import get_db
from . import models

# Dependency for retrieving the current authenticated user from a JWT.

# OAuth2 password flow. The tokenUrl matches the login endpoint used in
# auth_routes_jwt.py. When documenting in Swagger, this will show an
# Authorize button for /auth/login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    """Decode the JWT and return the corresponding User or raise 401."""
    try:
        user_id, _ = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user