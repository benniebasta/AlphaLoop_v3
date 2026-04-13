"""Add trade_id FK column to order_records for trade-order reconciliation.

Revision ID: 004_fk_constraints
Revises: 003_pipeline_stage_decisions
Create Date: 2026-04-13

SQLite note: FK column is defined but FK enforcement requires
`PRAGMA foreign_keys = ON` at connection time (production PostgreSQL
enforces FKs natively).
"""

from alembic import op
import sqlalchemy as sa

revision = "004_fk_constraints"
down_revision = "003_pipeline_stage_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add trade_id to order_records so each order can reference its parent trade.
    # Nullable: orders may be created before the trade_logs row is committed
    # (e.g. IOC orders that fill then get logged).
    with op.batch_alter_table("order_records", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("trade_id", sa.Integer(), sa.ForeignKey("trade_logs.id"), nullable=True)
        )
        batch_op.create_index("ix_order_trade_id", ["trade_id"])


def downgrade() -> None:
    with op.batch_alter_table("order_records", recreate="auto") as batch_op:
        batch_op.drop_index("ix_order_trade_id")
        batch_op.drop_column("trade_id")
