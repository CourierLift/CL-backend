"""Server-issued quotes and idempotent order creation.

Revision ID: 0005_quotes_idempotency
Revises: 0004_pricing_snapshot
Create Date: 2026-10-12

Existing orders receive NULL quote_id/idempotency_key/request_hash. NULLs are
distinct under both unique constraints on PostgreSQL and SQLite, so legacy rows
never conflict. Downgrade drops the columns and the quotes table; quote binding
and idempotency records created after upgrade are lost on downgrade.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_quotes_idempotency"
down_revision: Union[str, None] = "0004_pricing_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quotes",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("details_hash", sa.String(length=64), nullable=False),
        sa.Column("pricing_conditions", sa.JSON(), nullable=False),
        sa.Column("pricing_snapshot", sa.JSON(), nullable=False),
        sa.Column("price_total", sa.Float(), nullable=False),
        sa.Column("eta_min", sa.Integer(), nullable=False),
        sa.Column("distance_miles", sa.Float(), nullable=False),
        sa.Column("distance_estimated", sa.Boolean(), nullable=False),
        sa.Column("distance_source", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("pricing_engine_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quotes_user_id", "quotes", ["user_id"])
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("quote_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("request_hash", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key("fk_orders_quote_id", "quotes", ["quote_id"], ["id"])
        batch_op.create_unique_constraint(
            "uq_orders_user_idempotency_key", ["user_id", "idempotency_key"]
        )
        batch_op.create_unique_constraint("uq_orders_quote_id", ["quote_id"])


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("uq_orders_quote_id", type_="unique")
        batch_op.drop_constraint("uq_orders_user_idempotency_key", type_="unique")
        batch_op.drop_constraint("fk_orders_quote_id", type_="foreignkey")
        batch_op.drop_column("request_hash")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("quote_id")
    op.drop_index("ix_quotes_user_id", table_name="quotes")
    op.drop_table("quotes")
