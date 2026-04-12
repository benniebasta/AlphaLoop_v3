"""Add pipeline_stage_decisions table for Gate-1 observability funnel.

One row per (cycle, stage) — powers the /api/pipeline/funnel endpoint and
the observability UI.  Does not touch the existing cycle-level
``pipeline_decisions`` table.

Revision ID: 003_pipeline_stage_decisions
Revises: 002_trail_fields
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa

revision = "003_pipeline_stage_decisions"
down_revision = "002_trail_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_stage_decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime, nullable=False),
        sa.Column("cycle_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="live"),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("instance_id", sa.String(64), nullable=True),
        sa.Column("mode", sa.String(16), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("stage_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("blocked_by", sa.String(64), nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("reject_stage", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(8), nullable=True),
        sa.Column("setup_type", sa.String(32), nullable=True),
        sa.Column("conviction_score", sa.Float, nullable=True),
        sa.Column("size_multiplier", sa.Float, nullable=True),
        sa.Column("latency_ms", sa.Float, nullable=True),
    )
    op.create_index(
        "ix_pipeline_stage_decisions_occurred_at",
        "pipeline_stage_decisions",
        ["occurred_at"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_cycle_id",
        "pipeline_stage_decisions",
        ["cycle_id"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_source",
        "pipeline_stage_decisions",
        ["source"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_symbol",
        "pipeline_stage_decisions",
        ["symbol"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_instance",
        "pipeline_stage_decisions",
        ["instance_id"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_stage",
        "pipeline_stage_decisions",
        ["stage"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_status",
        "pipeline_stage_decisions",
        ["status"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_blocked_by",
        "pipeline_stage_decisions",
        ["blocked_by"],
    )
    op.create_index(
        "ix_pipeline_stage_decisions_outcome",
        "pipeline_stage_decisions",
        ["outcome"],
    )
    op.create_index(
        "ix_psd_symbol_at",
        "pipeline_stage_decisions",
        ["symbol", "occurred_at"],
    )
    op.create_index(
        "ix_psd_stage_status",
        "pipeline_stage_decisions",
        ["stage", "status"],
    )
    op.create_index(
        "ix_psd_cycle",
        "pipeline_stage_decisions",
        ["cycle_id"],
    )
    op.create_index(
        "ix_psd_source_at",
        "pipeline_stage_decisions",
        ["source", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_psd_source_at", table_name="pipeline_stage_decisions")
    op.drop_index("ix_psd_cycle", table_name="pipeline_stage_decisions")
    op.drop_index("ix_psd_stage_status", table_name="pipeline_stage_decisions")
    op.drop_index("ix_psd_symbol_at", table_name="pipeline_stage_decisions")
    op.drop_index(
        "ix_pipeline_stage_decisions_outcome", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_blocked_by", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_status", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_stage", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_instance", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_symbol", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_source", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_cycle_id", table_name="pipeline_stage_decisions"
    )
    op.drop_index(
        "ix_pipeline_stage_decisions_occurred_at",
        table_name="pipeline_stage_decisions",
    )
    op.drop_table("pipeline_stage_decisions")
