from datetime import datetime, timedelta
from passlib.context import CryptContext
import jwt, os

PWD = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
ALGO = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

def hash_password(plain: str) -> str: return PWD.hash(plain)
def verify_password(plain: str, hashed: str) -> bool: return PWD.verify(plain, hashed)

def create_access_token(user_id: int, role: str) -> str:
    exp = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MIN)
    payload = {"user_id": user_id, "role": role, "exp": int(exp.timestamp())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)

def decode_access_token(token: str):
    data = jwt.decode(token, SECRET_KEY, algorithms=[ALGO])
    return data["user_id"], data.get("role", "customer")
