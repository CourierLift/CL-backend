"""Pydantic request and response contracts used by the API routers."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .quote_engine import normalize_transport_mode


PublicRoleName = Literal["customer", "courier", "merchant"]


def _normalized_requirements(values: list[str]) -> list[str]:
    return sorted(
        {
            value.strip().lower().replace(" ", "_")
            for value in values
            if value.strip()
        }
    )


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)
    role: PublicRoleName = "customer"
    transportation_mode: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    max_weight_lb: float | None = Field(default=None, gt=0)
    max_length_in: float | None = Field(default=None, gt=0)
    max_width_in: float | None = Field(default=None, gt=0)
    max_height_in: float | None = Field(default=None, gt=0)
    max_volume_cu_ft: float | None = Field(default=None, gt=0)

    @field_validator("transportation_mode")
    @classmethod
    def validate_transportation_mode(cls, value: str | None) -> str | None:
        return normalize_transport_mode(value) if value else None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        return _normalized_requirements(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class QuoteRequest(BaseModel):
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)
    dropoff_lat: float = Field(ge=-90, le=90)
    dropoff_lng: float = Field(ge=-180, le=180)
    vehicle: str = "car"
    item_type: str = "standard"
    quantity: int = Field(default=1, ge=1, le=1000)
    weight_lb: float = Field(default=0, ge=0)
    length_in: float = Field(default=12, gt=0)
    width_in: float = Field(default=8, gt=0)
    height_in: float = Field(default=6, gt=0)
    weather: str = "clear"
    traffic: str = "medium"
    surge: float = Field(default=1.0, ge=1.0, le=3.0)
    delivery_requirements: list[str] = Field(default_factory=list)

    @field_validator("vehicle")
    @classmethod
    def validate_vehicle(cls, value: str) -> str:
        return normalize_transport_mode(value)

    @field_validator("delivery_requirements")
    @classmethod
    def validate_requirements(cls, value: list[str]) -> list[str]:
        return _normalized_requirements(value)


class AddressQuoteRequest(BaseModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    vehicle: str = "car"
    item_type: str = "standard"
    quantity: int = Field(default=1, ge=1, le=1000)
    weight_kg: float = Field(default=0, ge=0)
    length_in: float = Field(default=12, gt=0)
    width_in: float = Field(default=8, gt=0)
    height_in: float = Field(default=6, gt=0)
    weather: str = "clear"
    traffic: str = "medium"
    surge: float = Field(default=1.0, ge=1.0, le=3.0)
    delivery_requirements: list[str] = Field(default_factory=list)

    @field_validator("origin", "destination")
    @classmethod
    def strip_address(cls, value: str) -> str:
        return value.strip()

    @field_validator("vehicle")
    @classmethod
    def validate_vehicle(cls, value: str) -> str:
        return normalize_transport_mode(value)

    @field_validator("delivery_requirements")
    @classmethod
    def validate_requirements(cls, value: list[str]) -> list[str]:
        return _normalized_requirements(value)


class QuoteResponse(BaseModel):
    price: float
    eta: int
    miles: float
    tier: str
    estimated: bool
    distance_source: str


class QuoteEstimateResponse(BaseModel):
    price_total: float
    eta_min: int
    miles: float
    tier: str
    estimated: bool
    distance_source: str


class OrderCreate(QuoteRequest):
    pass


class OrderCreateCompat(AddressQuoteRequest):
    pass


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    assigned_courier_id: int | None
    origin: str | None
    destination: str | None
    pickup_lat: float | None
    pickup_lng: float | None
    dropoff_lat: float | None
    dropoff_lng: float | None
    vehicle: str
    item_type: str
    delivery_requirements: list[str]
    quantity: int
    weight_lb: float
    length_in: float
    width_in: float
    height_in: float
    price: float
    eta_min: int
    distance_miles: float
    distance_estimated: bool
    distance_source: str
    status: str
    created_at: datetime
    assigned_at: datetime | None
    completed_at: datetime | None


class StatusUpdate(BaseModel):
    status: Literal["pending", "assigned", "picked_up", "delivered", "canceled"]


class RewardIn(BaseModel):
    order_id: int | None = None
    type: Literal["earn", "redeem", "adjust"] = "earn"
    points: int
    reason: str | None = None


class RewardEventOut(BaseModel):
    id: int
    points: int
    type: str
    reason: str | None
