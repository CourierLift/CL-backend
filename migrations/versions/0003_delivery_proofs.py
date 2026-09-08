"""Add durable delivery proof records.

Revision ID: 0003_delivery_proofs
Revises: 0002_add_in_transit_status
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_delivery_proofs"
down_revision: Union[str, None] = "0002_add_in_transit_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "delivery_proofs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(op.f("ix_delivery_proofs_id"), "delivery_proofs", ["id"], unique=False)
    op.create_index(op.f("ix_delivery_proofs_order_id"), "delivery_proofs", ["order_id"], unique=True)
    op.create_index(
        op.f("ix_delivery_proofs_uploaded_by_user_id"),
        "delivery_proofs",
        ["uploaded_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_delivery_proofs_uploaded_by_user_id"), table_name="delivery_proofs")
    op.drop_index(op.f("ix_delivery_proofs_order_id"), table_name="delivery_proofs")
    op.drop_index(op.f("ix_delivery_proofs_id"), table_name="delivery_proofs")
    op.drop_table("delivery_proofs")
