"""Add authoritative in-transit delivery state.

Revision ID: 0002_add_in_transit_status
Revises: 0001_initial_schema
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_add_in_transit_status"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL stores SQLAlchemy Enum values in a native enum type. SQLite
    # stores this model enum as text and requires no schema change for the new value.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'in_transit'")


def downgrade() -> None:
    # Removing a PostgreSQL enum value safely requires rewriting dependent data/type.
    # The additive value is intentionally retained on downgrade; application code at
    # the previous revision simply does not emit it.
    pass
