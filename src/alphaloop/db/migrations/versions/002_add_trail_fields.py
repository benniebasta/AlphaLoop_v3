"""Add trailing stop loss state columns to trade_logs.

Revision ID: 002_trail_fields
Revises: 001_initial
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa

revision = "002_trail_fields"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trade_logs",
        sa.Column("trail_high_water", sa.Float(), nullable=True),
    )
    op.add_column(
        "trade_logs",
        sa.Column("trail_sl_applied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_logs", "trail_sl_applied_at")
    op.drop_column("trade_logs", "trail_high_water")
