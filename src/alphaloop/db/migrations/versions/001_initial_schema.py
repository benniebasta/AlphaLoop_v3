"""Initial schema — all tables for AlphaLoop v3.

Revision ID: 001_initial
Revises: None
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # App Settings (key-value store)
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # Trade logs
    op.create_table(
        "trade_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("setup_type", sa.String(32), nullable=True),
        sa.Column("timeframe", sa.String(8), nullable=True),
        sa.Column("entry_price", sa.Float, nullable=True),
        sa.Column("entry_zone_low", sa.Float, nullable=True),
        sa.Column("entry_zone_high", sa.Float, nullable=True),
        sa.Column("lot_size", sa.Float, nullable=True),
        sa.Column("risk_pct", sa.Float, nullable=True),
        sa.Column("risk_amount_usd", sa.Float, nullable=True),
        sa.Column("stop_loss", sa.Float, nullable=True),
        sa.Column("take_profit_1", sa.Float, nullable=True),
        sa.Column("take_profit_2", sa.Float, nullable=True),
        sa.Column("outcome", sa.String(8), nullable=True),
        sa.Column("close_price", sa.Float, nullable=True),
        sa.Column("pnl_usd", sa.Float, nullable=True),
        sa.Column("pnl_r", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("risk_score", sa.Float, nullable=True),
        sa.Column("rr_ratio", sa.Float, nullable=True),
        sa.Column("signal_reasoning", sa.Text, nullable=True),
        sa.Column("signal_json", sa.JSON, nullable=True),
        sa.Column("validation_json", sa.JSON, nullable=True),
        sa.Column("market_context_snapshot", sa.JSON, nullable=True),
        sa.Column("instance_id", sa.String(64), nullable=True),
        sa.Column("strategy_version", sa.String(32), nullable=True),
        sa.Column("session_name", sa.String(32), nullable=True),
        sa.Column("h1_rsi", sa.Float, nullable=True),
        sa.Column("h1_atr", sa.Float, nullable=True),
        sa.Column("h1_trend", sa.String(16), nullable=True),
        sa.Column("m15_structure", sa.String(32), nullable=True),
        sa.Column("macro_bias", sa.String(16), nullable=True),
        sa.Column("macro_modifier", sa.Float, nullable=True),
        sa.Column("order_ticket", sa.Integer, nullable=True),
        sa.Column("dry_run", sa.Boolean, nullable=True),
        sa.Column("opened_at", sa.DateTime, nullable=True),
        sa.Column("closed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_trade_opened_at", "trade_logs", ["opened_at"])
    op.create_index("ix_trade_outcome", "trade_logs", ["outcome"])
    op.create_index("ix_trade_setup_session", "trade_logs", ["setup_type", "session_name"])
    op.create_index("ix_trade_instance_outcome", "trade_logs", ["instance_id", "outcome"])
    op.create_index("ix_trade_symbol_strat_outcome", "trade_logs", ["symbol", "strategy_version", "outcome"])

    # Trade audit log
    op.create_table(
        "trade_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_id", sa.Integer, nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("changed_at", sa.DateTime, nullable=True),
        sa.Column("changed_by", sa.String(32), nullable=True),
    )

    # Backtest runs
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), unique=True, nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("plan", sa.Text, nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("pid", sa.Integer, nullable=True),
        sa.Column("heartbeat_at", sa.DateTime, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("days", sa.Integer, server_default="365"),
        sa.Column("timeframe", sa.String(8), server_default="1h"),
        sa.Column("balance", sa.Float, server_default="10000"),
        sa.Column("max_generations", sa.Integer, server_default="10"),
        sa.Column("tools_json", sa.JSON, nullable=True),
        sa.Column("generation", sa.Integer, server_default="0"),
        sa.Column("phase", sa.String(32), nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("bars_loaded", sa.Integer, nullable=True),
        sa.Column("best_version", sa.Integer, nullable=True),
        sa.Column("best_sharpe", sa.Float, nullable=True),
        sa.Column("best_wr", sa.Float, nullable=True),
        sa.Column("best_pnl", sa.Float, nullable=True),
        sa.Column("best_dd", sa.Float, nullable=True),
        sa.Column("best_trades", sa.Integer, nullable=True),
        sa.Column("generations_json", sa.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("error_traceback", sa.Text, nullable=True),
        sa.Column("checkpoint_path", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_bt_state", "backtest_runs", ["state"])
    op.create_index("ix_bt_symbol_state", "backtest_runs", ["symbol", "state"])

    # Research reports
    op.create_table(
        "research_reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("strategy_version", sa.String(32), nullable=True),
        sa.Column("report_date", sa.DateTime, nullable=True),
        sa.Column("period_start", sa.DateTime, nullable=True),
        sa.Column("period_end", sa.DateTime, nullable=True),
        sa.Column("total_trades", sa.Integer, nullable=True),
        sa.Column("win_rate", sa.Float, nullable=True),
        sa.Column("avg_rr", sa.Float, nullable=True),
        sa.Column("total_pnl_usd", sa.Float, nullable=True),
        sa.Column("sharpe_ratio", sa.Float, nullable=True),
        sa.Column("max_drawdown_pct", sa.Float, nullable=True),
        sa.Column("setup_stats", sa.JSON, nullable=True),
        sa.Column("session_stats", sa.JSON, nullable=True),
        sa.Column("analysis_summary", sa.Text, nullable=True),
        sa.Column("improvement_suggestions", sa.JSON, nullable=True),
        sa.Column("hourly_stats", sa.JSON, nullable=True),
        sa.Column("ai_confidence", sa.Float, nullable=True),
        sa.Column("raw_metrics", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    # Parameter snapshots
    op.create_table(
        "parameter_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("snapped_at", sa.DateTime, nullable=True),
        sa.Column("trigger", sa.String(32), nullable=True),
        sa.Column("parameters", sa.JSON, nullable=True),
        sa.Column("sharpe_at_snapshot", sa.Float, nullable=True),
        sa.Column("win_rate_at_snapshot", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )

    # Evolution events
    op.create_table(
        "evolution_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime, nullable=True),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("strategy_version", sa.String(32), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=True),
        sa.Column("metrics_before", sa.JSON, nullable=True),
        sa.Column("metrics_after", sa.JSON, nullable=True),
        sa.Column("params_before", sa.JSON, nullable=True),
        sa.Column("params_after", sa.JSON, nullable=True),
        sa.Column("details", sa.Text, nullable=True),
    )

    # Running instances
    op.create_table(
        "running_instances",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("instance_id", sa.String(64), unique=True, nullable=False),
        sa.Column("pid", sa.Integer, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("strategy_version", sa.String(32), nullable=True),
    )

    # Pipeline decisions
    op.create_table(
        "pipeline_decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime, nullable=True),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("direction", sa.String(8), nullable=True),
        sa.Column("allowed", sa.Boolean, nullable=True),
        sa.Column("blocked_by", sa.String(32), nullable=True),
        sa.Column("block_reason", sa.Text, nullable=True),
        sa.Column("size_modifier", sa.Float, nullable=True),
        sa.Column("bias", sa.String(16), nullable=True),
        sa.Column("tool_results", sa.JSON, nullable=True),
        sa.Column("instance_id", sa.String(64), nullable=True),
    )

    # Rejection log
    op.create_table(
        "rejection_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime, nullable=True),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("direction", sa.String(8), nullable=True),
        sa.Column("setup_type", sa.String(32), nullable=True),
        sa.Column("session_name", sa.String(32), nullable=True),
        sa.Column("rejected_by", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("instance_id", sa.String(64), nullable=True),
    )

    # Strategy versions (new in audit)
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("seed_hash", sa.String(16), nullable=True),
        sa.Column("params_json", sa.JSON, nullable=True),
        sa.Column("tools_json", sa.JSON, nullable=True),
        sa.Column("validation_json", sa.JSON, nullable=True),
        sa.Column("ai_models_json", sa.JSON, nullable=True),
        sa.Column("total_trades", sa.Integer, nullable=True),
        sa.Column("win_rate", sa.Float, nullable=True),
        sa.Column("sharpe", sa.Float, nullable=True),
        sa.Column("max_drawdown_pct", sa.Float, nullable=True),
        sa.Column("total_pnl", sa.Float, nullable=True),
        sa.Column("mc_p_value", sa.Float, nullable=True),
        sa.Column("mc_significant", sa.Boolean, nullable=True),
        sa.Column("mc_ruin_probability", sa.Float, nullable=True),
        sa.Column("canary_id", sa.String(64), nullable=True),
        sa.Column("canary_start", sa.DateTime, nullable=True),
        sa.Column("canary_end", sa.DateTime, nullable=True),
        sa.Column("canary_result", sa.String(16), nullable=True),
        sa.Column("promoted_at", sa.DateTime, nullable=True),
        sa.Column("activated_at", sa.DateTime, nullable=True),
        sa.Column("retired_at", sa.DateTime, nullable=True),
        sa.Column("file_path", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_strat_symbol_version", "strategy_versions", ["symbol", "version"], unique=True)
    op.create_index("ix_strat_status", "strategy_versions", ["status"])
    op.create_index("ix_strat_symbol_status", "strategy_versions", ["symbol", "status"])


def downgrade() -> None:
    op.drop_table("strategy_versions")
    op.drop_table("rejection_log")
    op.drop_table("pipeline_decisions")
    op.drop_table("running_instances")
    op.drop_table("evolution_events")
    op.drop_table("parameter_snapshots")
    op.drop_table("research_reports")
    op.drop_table("backtest_runs")
    op.drop_table("trade_audit_log")
    op.drop_table("trade_logs")
    op.drop_table("app_settings")
