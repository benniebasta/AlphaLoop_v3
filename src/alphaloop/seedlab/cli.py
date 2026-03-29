"""
seedlab/cli.py — Subprocess entry point for SeedLab runs.

Designed to be invoked as a separate process from the WebUI or CLI.
Parses arguments, loads data, runs the pipeline, and writes results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for a SeedLab run."""
    parser = argparse.ArgumentParser(
        description="AlphaLoop SeedLab — Strategy Discovery Pipeline",
    )
    parser.add_argument("--symbol", required=True, help="Trading symbol (e.g. XAUUSD)")
    parser.add_argument("--days", type=int, default=365, help="Lookback days")
    parser.add_argument("--balance", type=float, default=10_000.0, help="Starting balance")
    parser.add_argument("--risk-factor", type=float, default=0.85, help="Backtest risk factor")
    parser.add_argument("--combinatorial", action="store_true", help="Enable combinatorial seeds")
    parser.add_argument("--max-combo", type=int, default=30, help="Max combinatorial seeds")
    parser.add_argument("--run-id", default=None, help="Run ID (auto if not set)")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--registry-dir", default=None, help="Card registry directory")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    return parser.parse_args(argv)


async def run_cli(args: argparse.Namespace) -> int:
    """
    Execute a SeedLab run from CLI arguments.

    This is a stub entry point. The actual data loading and backtest
    function must be provided by the caller or loaded from configuration.
    """
    from alphaloop.seedlab.runner import SeedLabConfig, SeedLabRunner

    config = SeedLabConfig(
        symbol=args.symbol,
        days=args.days,
        balance=args.balance,
        backtest_risk_factor=args.risk_factor,
        use_template_seeds=True,
        use_combinatorial_seeds=args.combinatorial,
        max_combinatorial_seeds=args.max_combo,
    )

    logger.info(
        "SeedLab CLI starting: symbol=%s, days=%d, balance=%.0f",
        config.symbol, config.days, config.balance,
    )

    # NOTE: In production, data loading and backtest_fn would be wired here.
    # This stub demonstrates the interface.
    logger.error(
        "SeedLab CLI requires data loading and backtest function to be "
        "configured. This is a stub entry point."
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """Main entry point for seedlab CLI."""
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    return asyncio.run(run_cli(args))


if __name__ == "__main__":
    sys.exit(main())
