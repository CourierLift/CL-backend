"""Persist immutable pricing snapshots on orders.

Revision ID: 0004_pricing_snapshot
Revises: 0003_delivery_proofs
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_pricing_snapshot"
down_revision: Union[str, None] = "0003_delivery_proofs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing pre-snapshot rows are marked explicitly rather than recalculated
    # with today's pricing rules. New rows always receive a complete snapshot
    # from the application at creation time.
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pricing_engine_version",
                sa.String(),
                nullable=False,
                server_default="legacy_pre_snapshot",
            )
        )
        batch_op.add_column(
            sa.Column(
                "pricing_snapshot",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("pricing_snapshot")
        batch_op.drop_column("pricing_engine_version")
