from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Literal

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    role: Literal["customer","courier","merchant","admin"] = "customer"

class UserOut(BaseModel):
    id: int
    email: EmailStr
    role: str
    created_at: datetime
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class QuoteRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    vehicle: str
    item_type: str
    quantity: int = 1
    weight_lb: float = 0
    length_in: float = 12
    width_in: float = 8
    height_in: float = 6
    weather: str = "clear"
    traffic: str = "med"

class QuoteResponse(BaseModel):
    price: float
    eta: int
    miles: float
    tier: str
class OrderCreate(QuoteRequest):
    pass

class OrderOut(BaseModel):
    id: int
    user_id: int
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    vehicle: str
    item_type: str
    quantity: int
    weight_lb: float
    length_in: float
    width_in: float
    height_in: float
    price: float
    eta_min: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
# --- status updates ---
from typing import Literal
from pydantic import BaseModel

class StatusUpdate(BaseModel):
    status: Literal["pending", "assigned", "picked_up", "delivered", "canceled"]

