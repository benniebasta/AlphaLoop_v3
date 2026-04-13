"""Strengthen trade_logs.outcome: server default + CHECK constraint.

Revision ID: 006_strengthen_outcome
Revises: 005_drop_dead_columns
Create Date: 2026-04-13

Changes:
  1. Add server_default='open' so new trade rows always have an outcome.
  2. Add CHECK constraint restricting outcome to known values.
     Allows NULL for legacy rows; new rows always get 'open' via default.

Valid outcome values:
  open       — trade placed, not yet resolved
  win        — closed at profit
  loss       — closed at loss
  breakeven  — closed at or near entry
  cancelled  — cancelled before fill

NOT enforcing NOT NULL here: existing rows may have outcome=NULL from
before this migration. A follow-up data migration can backfill 'open'
for all NULL rows with closed_at IS NULL and then add NOT NULL.
"""

from alembic import op
import sqlalchemy as sa

revision = "006_strengthen_outcome"
down_revision = "005_drop_dead_columns"
branch_labels = None
depends_on = None

_VALID_OUTCOMES = ("open", "win", "loss", "breakeven", "cancelled")


def upgrade() -> None:
    # SQLite note: CHECK constraints added via batch_alter work on new rows;
    # existing rows are not re-validated. PostgreSQL validates on constraint add.
    with op.batch_alter_table("trade_logs", recreate="auto") as batch_op:
        batch_op.alter_column(
            "outcome",
            existing_type=sa.String(16),
            server_default="open",
            existing_nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_trade_outcome_valid",
            "outcome IS NULL OR outcome IN ('open','win','loss','breakeven','cancelled')",
        )


def downgrade() -> None:
    with op.batch_alter_table("trade_logs", recreate="auto") as batch_op:
        batch_op.drop_constraint("ck_trade_outcome_valid", type_="check")
        batch_op.alter_column(
            "outcome",
            existing_type=sa.String(16),
            server_default=None,
            existing_nullable=True,
        )
