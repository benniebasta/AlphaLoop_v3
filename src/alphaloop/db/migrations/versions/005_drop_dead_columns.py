"""Drop confirmed-dead columns from trade_logs.

Revision ID: 005_drop_dead_columns
Revises: 004_fk_constraints
Create Date: 2026-04-13

Dead column criteria: column exists in model but has zero write-path
references in any business-logic file (confirmed via full codebase grep).

Dropped columns:
  - closed_manually   — v1/v2 relic; no writer; no reader
  - partial_pnl_usd   — partial-close mechanic never implemented
  - h1_trend          — only appears as a local variable in signals/engine.py
                        and validation/prompts.py; never persisted to DB
  - m15_structure     — only appears as a local variable in validation/prompts.py

NOT dropped (still active):
  - qwen_confidence   — serialized by webui/routes/trades.py:41
  - claude_risk_score — serialized by webui/routes/trades.py:42
"""

from alembic import op

revision = "005_drop_dead_columns"
down_revision = "004_fk_constraints"
branch_labels = None
depends_on = None

_DEAD_COLUMNS = [
    "closed_manually",
    "partial_pnl_usd",
    "h1_trend",
    "m15_structure",
]


def upgrade() -> None:
    with op.batch_alter_table("trade_logs", recreate="auto") as batch_op:
        for col in _DEAD_COLUMNS:
            batch_op.drop_column(col)


def downgrade() -> None:
    import sqlalchemy as sa

    with op.batch_alter_table("trade_logs", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("m15_structure", sa.String(16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("h1_trend", sa.String(16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("partial_pnl_usd", sa.Float(), nullable=True, server_default="0.0")
        )
        batch_op.add_column(
            sa.Column("closed_manually", sa.Boolean(), nullable=False, server_default="0")
        )
