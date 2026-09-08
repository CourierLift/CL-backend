"""Initial Courier Lifts relational schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_role = sa.Enum("customer", "courier", "merchant", "admin", name="userrole")
    order_status = sa.Enum("pending", "assigned", "picked_up", "delivered", "canceled", name="orderstatus")
    reward_event_type = sa.Enum("earn", "redeem", "adjust", name="rewardeventtype")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)

    op.create_table(
        "courier_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("transportation_mode", sa.String(), nullable=False),
        sa.Column("max_weight_lb", sa.Float(), nullable=False),
        sa.Column("max_length_in", sa.Float(), nullable=False),
        sa.Column("max_width_in", sa.Float(), nullable=False),
        sa.Column("max_height_in", sa.Float(), nullable=False),
        sa.Column("max_volume_cu_ft", sa.Float(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_courier_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(), nullable=True),
        sa.Column("destination", sa.String(), nullable=True),
        sa.Column("pickup_lat", sa.Float(), nullable=True),
        sa.Column("pickup_lng", sa.Float(), nullable=True),
        sa.Column("dropoff_lat", sa.Float(), nullable=True),
        sa.Column("dropoff_lng", sa.Float(), nullable=True),
        sa.Column("vehicle", sa.String(), nullable=False),
        sa.Column("item_type", sa.String(), nullable=False),
        sa.Column("delivery_requirements", sa.JSON(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("weight_lb", sa.Float(), nullable=False),
        sa.Column("length_in", sa.Float(), nullable=False),
        sa.Column("width_in", sa.Float(), nullable=False),
        sa.Column("height_in", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("eta_min", sa.Integer(), nullable=False),
        sa.Column("distance_miles", sa.Float(), nullable=False),
        sa.Column("distance_estimated", sa.Boolean(), nullable=False),
        sa.Column("distance_source", sa.String(), nullable=False),
        sa.Column("weather", sa.String(), nullable=False),
        sa.Column("traffic", sa.String(), nullable=False),
        sa.Column("surge_multiplier", sa.Float(), nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
        sa.CheckConstraint("weight_lb >= 0", name="ck_orders_weight_nonnegative"),
        sa.ForeignKeyConstraint(["assigned_courier_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orders_assigned_courier_id"), "orders", ["assigned_courier_id"], unique=False)
    op.create_index(op.f("ix_orders_id"), "orders", ["id"], unique=False)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)
    op.create_index(op.f("ix_orders_user_id"), "orders", ["user_id"], unique=False)

    op.create_table(
        "reward_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("type", reward_event_type, nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reward_events_id"), "reward_events", ["id"], unique=False)
    op.create_index(op.f("ix_reward_events_user_id"), "reward_events", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reward_events_user_id"), table_name="reward_events")
    op.drop_index(op.f("ix_reward_events_id"), table_name="reward_events")
    op.drop_table("reward_events")
    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_assigned_courier_id"), table_name="orders")
    op.drop_table("orders")
    op.drop_table("courier_profiles")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    # PostgreSQL named ENUM types are independent schema objects. Alembic's
    # table drops do not remove them, so a full downgrade-to-base followed by
    # upgrade would otherwise fail with DuplicateObject on the next upgrade.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS rewardeventtype")
        op.execute("DROP TYPE IF EXISTS orderstatus")
        op.execute("DROP TYPE IF EXISTS userrole")
