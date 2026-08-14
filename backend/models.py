"""Canonical database models for users, courier profiles, orders, and rewards."""

from datetime import datetime, timezone
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    customer = "customer"
    courier = "courier"
    merchant = "merchant"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.customer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Keep the existing creator relationship name for compatibility.
    orders = relationship(
        "Order",
        back_populates="user",
        foreign_keys="Order.user_id",
    )
    assigned_orders = relationship(
        "Order",
        back_populates="assigned_courier",
        foreign_keys="Order.assigned_courier_id",
    )
    courier_profile = relationship(
        "CourierProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class CourierProfile(Base):
    __tablename__ = "courier_profiles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    transportation_mode = Column(String, nullable=False, default="car")
    max_weight_lb = Column(Float, nullable=False)
    max_length_in = Column(Float, nullable=False)
    max_width_in = Column(Float, nullable=False)
    max_height_in = Column(Float, nullable=False)
    max_volume_cu_ft = Column(Float, nullable=False)
    capabilities = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)

    user = relationship("User", back_populates="courier_profile")


class OrderStatus(str, enum.Enum):
    pending = "pending"
    assigned = "assigned"
    picked_up = "picked_up"
    delivered = "delivered"
    canceled = "canceled"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
        CheckConstraint("weight_lb >= 0", name="ck_orders_weight_nonnegative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    assigned_courier_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    origin = Column(String, nullable=True)
    destination = Column(String, nullable=True)
    pickup_lat = Column(Float, nullable=True)
    pickup_lng = Column(Float, nullable=True)
    dropoff_lat = Column(Float, nullable=True)
    dropoff_lng = Column(Float, nullable=True)

    vehicle = Column(String, nullable=False)
    item_type = Column(String, nullable=False)
    delivery_requirements = Column(JSON, nullable=False, default=list)
    quantity = Column(Integer, default=1, nullable=False)
    weight_lb = Column(Float, default=0, nullable=False)
    length_in = Column(Float, default=12, nullable=False)
    width_in = Column(Float, default=8, nullable=False)
    height_in = Column(Float, default=6, nullable=False)

    price = Column(Float, default=0, nullable=False)
    eta_min = Column(Integer, default=0, nullable=False)
    distance_miles = Column(Float, default=0, nullable=False)
    distance_estimated = Column(Boolean, default=False, nullable=False)
    distance_source = Column(String, default="coordinate_haversine", nullable=False)
    weather = Column(String, default="clear", nullable=False)
    traffic = Column(String, default="medium", nullable=False)
    surge_multiplier = Column(Float, default=1.0, nullable=False)

    status = Column(
        Enum(OrderStatus),
        default=OrderStatus.pending,
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship(
        "User",
        back_populates="orders",
        foreign_keys=[user_id],
    )
    assigned_courier = relationship(
        "User",
        back_populates="assigned_orders",
        foreign_keys=[assigned_courier_id],
    )


class RewardEventType(str, enum.Enum):
    earn = "earn"
    redeem = "redeem"
    adjust = "adjust"


class RewardEvent(Base):
    __tablename__ = "reward_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    type = Column(
        Enum(RewardEventType),
        nullable=False,
        default=RewardEventType.earn,
    )
    points = Column(Integer, nullable=False, default=0)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

