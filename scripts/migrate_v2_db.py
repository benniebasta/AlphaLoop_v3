"""
Migrate v2 SQLite database to v3 schema.

Usage:
    python scripts/migrate_v2_db.py --v2-db ../tradingai/alphaloop/alphaloop.db --v3-db ./alphaloop_v3.db
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def migrate(v2_path: str, v3_path: str) -> None:
    """Migrate data from v2 SQLite to v3 SQLite."""
    v2 = sqlite3.connect(v2_path)
    v2.row_factory = sqlite3.Row

    v3 = sqlite3.connect(v3_path)
    v3.execute("PRAGMA journal_mode=WAL")

    # Migrate app_settings
    try:
        rows = v2.execute("SELECT key, value FROM app_settings").fetchall()
        for row in rows:
            v3.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (row["key"], row["value"], datetime.now(timezone.utc).isoformat()),
            )
        print(f"Migrated {len(rows)} app_settings")
    except Exception as e:
        print(f"app_settings migration skipped: {e}")

    # Migrate trade_logs
    try:
        rows = v2.execute("SELECT * FROM trade_logs").fetchall()
        columns = [desc[0] for desc in v2.execute("SELECT * FROM trade_logs LIMIT 1").description]
        # Map v2 columns to v3
        count = 0
        for row in rows:
            data = dict(zip(columns, row))
            v3.execute(
                """INSERT OR IGNORE INTO trade_logs
                (symbol, direction, outcome, instance_id, entry_price, lot_size,
                 stop_loss, take_profit_1, pnl_usd, confidence, setup_type,
                 signal_reasoning, risk_score, order_ticket, created_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data.get("symbol", ""),
                    data.get("direction", ""),
                    data.get("outcome", ""),
                    data.get("instance_id", ""),
                    data.get("entry_price"),
                    data.get("lot_size"),
                    data.get("stop_loss"),
                    data.get("take_profit_1"),
                    data.get("pnl_usd"),
                    data.get("confidence"),
                    data.get("setup_type"),
                    data.get("signal_reasoning"),
                    data.get("risk_score"),
                    data.get("order_ticket"),
                    data.get("created_at"),
                    data.get("closed_at"),
                ),
            )
            count += 1
        print(f"Migrated {count} trade_logs")
    except Exception as e:
        print(f"trade_logs migration skipped: {e}")

    # Migrate research_reports
    try:
        rows = v2.execute("SELECT * FROM research_reports").fetchall()
        columns = [desc[0] for desc in v2.execute("SELECT * FROM research_reports LIMIT 1").description]
        count = 0
        for row in rows:
            data = dict(zip(columns, row))
            # Map v2 columns to v3 schema (v3 has symbol, strategy_version, analysis_summary, etc.)
            report_json = data.get("report_json", "{}")
            v3.execute(
                """INSERT OR IGNORE INTO research_reports
                (symbol, strategy_version, analysis_summary, created_at)
                VALUES (?, ?, ?, ?)""",
                (
                    data.get("symbol", ""),
                    data.get("strategy_version", "v1"),
                    report_json,
                    data.get("created_at"),
                ),
            )
            count += 1
        print(f"Migrated {count} research_reports")
    except Exception as e:
        print(f"research_reports migration skipped: {e}")

    # Migrate backtest_runs
    try:
        rows = v2.execute("SELECT * FROM backtest_runs").fetchall()
        columns = [desc[0] for desc in v2.execute("SELECT * FROM backtest_runs LIMIT 1").description]
        count = 0
        for row in rows:
            data = dict(zip(columns, row))
            v3.execute(
                """INSERT OR IGNORE INTO backtest_runs
                (run_id, symbol, state, params_json, results_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    data.get("run_id", ""),
                    data.get("symbol", ""),
                    data.get("state", "completed"),
                    data.get("params_json", "{}"),
                    data.get("results_json", "{}"),
                    data.get("created_at"),
                ),
            )
            count += 1
        print(f"Migrated {count} backtest_runs")
    except Exception as e:
        print(f"backtest_runs migration skipped: {e}")

    v3.commit()
    v2.close()
    v3.close()
    print("Migration complete!")


def main():
    parser = argparse.ArgumentParser(description="Migrate v2 DB to v3")
    parser.add_argument("--v2-db", required=True, help="Path to v2 SQLite database")
    parser.add_argument("--v3-db", required=True, help="Path to v3 SQLite database")
    args = parser.parse_args()

    if not Path(args.v2_db).exists():
        print(f"Error: v2 database not found at {args.v2_db}")
        sys.exit(1)

    migrate(args.v2_db, args.v3_db)


if __name__ == "__main__":
    main()
