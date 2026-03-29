"""
CLI entry point for AlphaLoop v3.
~100 lines — all orchestration is in trading/loop.py and app.py.
"""

import argparse
import asyncio
import logging
import signal
import uuid

from alphaloop import __version__

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"AlphaLoop v3 Trading Bot ({__version__})",
    )
    parser.add_argument(
        "--symbol", default="XAUUSD", help="Trading symbol (default: XAUUSD)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Run in dry-run mode (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live mode (overrides --dry-run)",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Start only the web dashboard",
    )
    parser.add_argument(
        "--port", type=int, default=8888, help="WebUI port (default: 8888)"
    )
    parser.add_argument(
        "--instance-id", default="", help="Instance ID (auto-generated if empty)"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=300.0,
        help="Signal polling interval in seconds (default: 300)",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    dry_run = not args.live
    instance_id = args.instance_id or f"{args.symbol}_{uuid.uuid4().hex[:8]}"

    from alphaloop.core.config import AppConfig
    from alphaloop.app import create_app

    config = AppConfig()
    container = await create_app(
        config,
        symbol=args.symbol,
        instance_id=instance_id,
        dry_run=dry_run,
    )

    if args.web_only:
        from alphaloop.webui.app import create_webui_app
        import uvicorn

        app = create_webui_app(container)
        uvicorn_config = uvicorn.Config(app, host="0.0.0.0", port=args.port)
        server = uvicorn.Server(uvicorn_config)
        await server.serve()
        return

    # Set up signal handlers for graceful shutdown
    trading_loop = None

    def handle_signal(sig, frame):
        logger.info("Received signal %s — shutting down", sig)
        if trading_loop:
            trading_loop.stop()

    signal.signal(signal.SIGINT, handle_signal)
    try:
        signal.signal(signal.SIGTERM, handle_signal)
    except OSError:
        pass  # SIGTERM not available on Windows

    # Create and run trading loop
    from alphaloop.trading.loop import TradingLoop
    from alphaloop.signals.engine import MultiAssetSignalEngine
    from alphaloop.validation.validator import UniversalValidator
    from alphaloop.risk.sizer import PositionSizer
    from alphaloop.risk.monitor import RiskMonitor
    from alphaloop.execution.mt5_executor import MT5Executor

    executor = MT5Executor(
        symbol=args.symbol,
        dry_run=dry_run,
        magic=config.broker.magic,
    )
    if not dry_run:
        await executor.connect()

    balance = await executor.get_account_balance()
    risk_monitor = RiskMonitor(balance)
    await risk_monitor.seed_from_db()

    from alphaloop.tools.registry import get_registry
    from alphaloop.config.settings_service import SettingsService

    tool_registry = get_registry()
    settings_service = SettingsService(container.db_session_factory)

    trading_loop = TradingLoop(
        symbol=args.symbol,
        instance_id=instance_id,
        poll_interval=args.poll_interval,
        dry_run=dry_run,
        event_bus=container.event_bus,
        signal_engine=MultiAssetSignalEngine(args.symbol),
        validator=UniversalValidator(args.symbol, dry_run=dry_run),
        sizer=PositionSizer(balance, symbol=args.symbol),
        executor=executor,
        risk_monitor=risk_monitor,
        settings_service=settings_service,
        tool_registry=tool_registry,
    )

    # Wire MetaLoop if enabled
    metaloop_enabled = await settings_service.get_bool("METALOOP_ENABLED", default=False)
    if metaloop_enabled:
        from alphaloop.trading.meta_loop import MetaLoop
        from alphaloop.core.events import TradeClosed

        meta_loop = MetaLoop(
            symbol=args.symbol,
            session_factory=container.db_session_factory,
            event_bus=container.event_bus,
            settings_service=settings_service,
            check_interval=await settings_service.get_int("METALOOP_CHECK_INTERVAL", 20),
            rollback_window=await settings_service.get_int("METALOOP_ROLLBACK_WINDOW", 30),
            auto_activate=await settings_service.get_bool("METALOOP_AUTO_ACTIVATE", default=False),
            degradation_threshold=await settings_service.get_float("METALOOP_DEGRADATION_THRESHOLD", 0.7),
        )
        container.event_bus.subscribe(TradeClosed, meta_loop.on_trade_closed)
        logger.info("MetaLoop enabled for %s", args.symbol)

    # Register this instance in the DB so the WebUI shows the agent card
    import os
    from alphaloop.db.models.instance import RunningInstance
    from sqlalchemy import delete as sa_delete

    async with container.db_session_factory() as db:
        # Clean up any stale entries for this symbol first
        await db.execute(
            sa_delete(RunningInstance).where(RunningInstance.symbol == args.symbol)
        )
        db.add(RunningInstance(
            symbol=args.symbol,
            instance_id=instance_id,
            pid=os.getpid(),
            strategy_version=None,  # Updated once strategy is loaded
        ))
        await db.commit()
    logger.info("Registered agent %s (PID %d) for %s", instance_id, os.getpid(), args.symbol)

    try:
        await trading_loop.run()
    finally:
        # Unregister this instance from the DB
        try:
            async with container.db_session_factory() as db:
                await db.execute(
                    sa_delete(RunningInstance).where(
                        RunningInstance.instance_id == instance_id
                    )
                )
                await db.commit()
            logger.info("Unregistered agent %s", instance_id)
        except Exception as unreg_err:
            logger.warning("Failed to unregister agent: %s", unreg_err)

        await executor.disconnect()
        from alphaloop.core.lifecycle import shutdown
        await shutdown(container)


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
