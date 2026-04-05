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
        "--port", type=int, default=8090, help="WebUI port (default: 8090)"
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
    parser.add_argument(
        "--risk-budget",
        type=float,
        default=1.0,
        help="Risk budget multiplier 0.0-1.0 (default: 1.0 = full budget)",
    )
    # ── Phase 0: Remediation containment flags ───────────────────────────────
    parser.add_argument(
        "--allow-v4-live",
        action="store_true",
        default=False,
        help="Allow v4 execution path in live mode (default: False — forces dry-run)",
    )
    parser.add_argument(
        "--force-start",
        action="store_true",
        default=False,
        help="Override critical reconciliation block (requires --force-reason)",
    )
    parser.add_argument(
        "--force-reason",
        type=str,
        default="",
        help="Required reason string when using --force-start",
    )
    parser.add_argument(
        "--operator",
        type=str,
        default="",
        help="Operator identity for audit attribution (defaults to $USER)",
    )
    parser.add_argument(
        "--webui-port",
        type=int,
        default=8090,
        help="WebUI port for event bridge (default: 8090)",
    )
    parser.add_argument(
        "--expected-account",
        type=int,
        default=0,
        help="Expected MT5 account login number for identity verification",
    )
    parser.add_argument(
        "--expected-server",
        type=str,
        default="",
        help="Expected MT5 server name for identity verification",
    )
    # ─────────────────────────────────────────────────────────────────────────
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    dry_run = not args.live
    instance_id = args.instance_id or f"{args.symbol}_{uuid.uuid4().hex[:8]}"

    _recon_report = None  # Phase 3E: initialized here so manifest can reference it

    # ── Phase 0A: v4 live containment ────────────────────────────────────────
    if args.live and not args.allow_v4_live:
        logger.critical(
            "[DEGRADED] Operator requested --live but v4 execution path is under "
            "remediation. Forcing dry-run. Use --allow-v4-live to override."
        )
        dry_run = True
    # ─────────────────────────────────────────────────────────────────────────

    from alphaloop.core.config import AppConfig
    from alphaloop.app import create_app

    config = AppConfig()
    container = await create_app(
        config,
        symbol=args.symbol,
        instance_id=instance_id,
        dry_run=dry_run,
    )

    async def _record_incident(
        incident_type: str,
        details: str,
        *,
        severity: str = "critical",
        payload: dict | None = None,
    ) -> None:
        if getattr(container, "supervision_service", None) is None:
            return
        await container.supervision_service.record_incident(
            incident_type=incident_type,
            details=details,
            severity=severity,
            symbol=args.symbol,
            instance_id=instance_id,
            source="main",
            payload=payload or {},
        )

    async def _record_event(
        category: str,
        event_type: str,
        message: str,
        *,
        severity: str = "info",
        payload: dict | None = None,
    ) -> None:
        if getattr(container, "supervision_service", None) is None:
            return
        await container.supervision_service.record_event(
            category=category,
            event_type=event_type,
            severity=severity,
            symbol=args.symbol,
            instance_id=instance_id,
            message=message,
            payload=payload or {},
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
    from alphaloop.risk.sizer import PositionSizer
    from alphaloop.risk.monitor import RiskMonitor
    from alphaloop.execution.mt5_executor import MT5Executor

    executor = MT5Executor(
        symbol=args.symbol,
        dry_run=dry_run,
        magic=config.broker.magic,
    )
    # Always connect to MT5 for price data — dry_run only skips trade execution
    await executor.connect()

    # ── Phase 0D: Verify broker identity on live startup ─────────────────────
    if not dry_run and (args.expected_account or args.expected_server):
        id_ok, id_err = await executor.verify_identity(
            expected_account=args.expected_account,
            expected_server=args.expected_server,
        )
        if not id_ok:
            await _record_incident(
                "broker_identity_mismatch",
                id_err or "Broker identity verification failed",
                payload={
                    "expected_account": args.expected_account or None,
                    "expected_server": args.expected_server or None,
                },
            )
            logger.critical(
                "Broker identity verification FAILED: %s. "
                "Live startup BLOCKED.", id_err,
            )
            raise SystemExit(1)
    elif not dry_run:
        # No expected account/server configured — verify trade permission at minimum
        id_ok, id_err = await executor.verify_identity()
        if not id_ok:
            await _record_incident(
                "broker_identity_mismatch",
                id_err or "Broker identity verification failed",
            )
            logger.critical(
                "Broker identity verification FAILED: %s. "
                "Live startup BLOCKED.", id_err,
            )
            raise SystemExit(1)
    # ─────────────────────────────────────────────────────────────────────────

    if not dry_run:
        # ── Phase 0B: Reconcile broker positions — fail closed on critical ───
        from alphaloop.execution.reconciler import PositionReconciler
        from alphaloop.db.repositories.trade_repo import TradeRepository

        _recon_report = None
        try:
            async with container.db_session_factory() as _recon_session:
                trade_repo = TradeRepository(_recon_session)
                reconciler = PositionReconciler(executor=executor, trade_repo=trade_repo)
                _recon_report = await reconciler.reconcile(instance_id=instance_id)
        except Exception as recon_exc:
            await _record_incident(
                "reconciliation_block",
                f"Startup reconciliation failed: {recon_exc}",
                payload={"stage": "startup", "reason": "exception"},
            )
            logger.critical(
                "Startup reconciliation FAILED with exception: %s", recon_exc,
            )
            if args.force_start:
                import os as _os_fs
                _operator = args.operator or _os_fs.environ.get("USER", _os_fs.environ.get("USERNAME", "unknown"))
                if not args.force_reason:
                    logger.critical("[BLOCKED] --force-start requires --force-reason. Exiting.")
                    raise SystemExit(1)
                logger.critical(
                    "[FORCE START ACTIVE] Operator: %s, Reason: %s — "
                    "proceeding despite reconciliation failure",
                    _operator, args.force_reason,
                )
                # Phase 3A: Persist force-start to operator audit log
                try:
                    from alphaloop.db.models.operator_audit import OperatorAuditLog
                    async with container.db_session_factory() as _fs_sess:
                        _fs_sess.add(OperatorAuditLog(
                            operator=_operator,
                            action="force_start",
                            target=instance_id,
                            old_value="reconciliation_exception",
                            new_value=args.force_reason,
                        ))
                        await _fs_sess.commit()
                except Exception as _audit_err:
                    logger.warning("Audit log write failed (non-fatal): %s", _audit_err)
            else:
                await _record_event(
                    "reconciliation",
                    "startup_reconciliation_failed",
                    "Live startup blocked because reconciliation was unavailable",
                    severity="critical",
                    payload={"stage": "startup", "force_start": False},
                )
                logger.critical(
                    "Live startup BLOCKED — reconciliation unavailable. "
                    "Use --force-start --force-reason <reason> to override."
                )
                raise SystemExit(1)

        if _recon_report is not None:
            _recon_payload = {
                "stage": "startup",
                "reconciled": _recon_report.reconciled,
                "has_critical": _recon_report.has_critical,
                "issue_count": _recon_report.issue_count,
                "broker_positions": _recon_report.broker_positions,
                "db_open_trades": _recon_report.db_open_trades,
                "issues": [
                    {
                        "ticket": issue.ticket,
                        "symbol": issue.symbol,
                        "issue_type": issue.issue_type,
                        "description": issue.description,
                        "severity": issue.severity,
                        "auto_resolved": issue.auto_resolved,
                    }
                    for issue in _recon_report.issues
                ],
            }
            await _record_event(
                "reconciliation",
                "startup_reconciliation_completed",
                "Startup reconciliation completed",
                severity="warning" if _recon_report.issues else "info",
                payload=_recon_payload,
            )
            if _recon_report.has_critical:
                await _record_incident(
                    "reconciliation_block",
                    "Startup reconciliation found critical issues",
                    payload=_recon_payload,
                )
                logger.critical(
                    "Startup reconciliation found CRITICAL issues: %s",
                    [i.description for i in _recon_report.issues if i.severity == "critical"],
                )
                if args.force_start:
                    import os as _os_fs2
                    _operator = args.operator or _os_fs2.environ.get("USER", _os_fs2.environ.get("USERNAME", "unknown"))
                    if not args.force_reason:
                        logger.critical("[BLOCKED] --force-start requires --force-reason. Exiting.")
                        raise SystemExit(1)
                    logger.critical(
                        "[FORCE START ACTIVE] Operator: %s, Reason: %s — "
                        "proceeding despite critical reconciliation issues",
                        _operator, args.force_reason,
                    )
                    # Phase 3A: Persist force-start to operator audit log
                    try:
                        from alphaloop.db.models.operator_audit import OperatorAuditLog
                        _issues_summary = "; ".join(
                            i.description for i in _recon_report.issues if i.severity == "critical"
                        )[:500]
                        async with container.db_session_factory() as _fs_sess2:
                            _fs_sess2.add(OperatorAuditLog(
                                operator=_operator,
                                action="force_start",
                                target=instance_id,
                                old_value=f"critical_reconciliation: {_issues_summary}",
                                new_value=args.force_reason,
                            ))
                            await _fs_sess2.commit()
                    except Exception as _audit_err2:
                        logger.warning("Force-start audit log write failed (non-fatal): %s", _audit_err2)
                else:
                    logger.critical(
                        "Live startup BLOCKED — critical reconciliation issues. "
                        "Use --force-start --force-reason <reason> to override."
                    )
                    raise SystemExit(1)
            elif _recon_report.issues:
                logger.warning(
                    "Startup reconciliation found %d issue(s): %s",
                    len(_recon_report.issues),
                    [i.description for i in _recon_report.issues],
                )
            else:
                logger.info("Startup reconciliation: no discrepancies found")
        # ─────────────────────────────────────────────────────────────────────

    # ── Phase 3D: Lease-based single-writer execution lock ─────────────────
    _lock_heartbeat_task = None
    _scope_key = "n/a"
    if not dry_run:
        import os as _os_lock
        from datetime import datetime as _dt_lock, timezone as _tz_lock
        from sqlalchemy import select as _sa_select, delete as _sa_del
        from alphaloop.db.models.execution_lock import ExecutionLock

        _owner_uuid = uuid.uuid4().hex
        _scope_key = f"{config.broker.login}|{args.symbol}|default"
        _lease_timeout = 120  # seconds

        async with container.db_session_factory() as _lock_session:
            existing = (await _lock_session.execute(
                _sa_select(ExecutionLock).where(ExecutionLock.scope_key == _scope_key)
            )).scalar_one_or_none()

            if existing:
                age = (_dt_lock.now(_tz_lock.utc) - existing.heartbeat_at).total_seconds()
                if age < existing.lease_timeout_sec:
                    logger.critical(
                        "[single-writer] Execution lock held by UUID=%s (PID=%d, "
                        "heartbeat %.0fs ago). Another process owns scope '%s'. Exiting.",
                        existing.owner_uuid, existing.pid, age, _scope_key,
                    )
                    raise SystemExit(1)
                else:
                    logger.warning(
                        "[single-writer] Stale lock detected (UUID=%s, heartbeat %.0fs ago). "
                        "Acquiring.",
                        existing.owner_uuid, age,
                    )
                    await _lock_session.execute(
                        _sa_del(ExecutionLock).where(ExecutionLock.scope_key == _scope_key)
                    )

            _lock_session.add(ExecutionLock(
                scope_key=_scope_key,
                owner_uuid=_owner_uuid,
                pid=_os_lock.getpid(),
                lease_timeout_sec=_lease_timeout,
            ))
            await _lock_session.commit()
        logger.info("[single-writer] Acquired lock for scope '%s' (UUID=%s)", _scope_key, _owner_uuid[:12])

        async def _refresh_lock_heartbeat():
            """Background task to refresh heartbeat every lease_timeout/3."""
            while True:
                await asyncio.sleep(_lease_timeout / 3)
                try:
                    async with container.db_session_factory() as _hb_session:
                        lock = (await _hb_session.execute(
                            _sa_select(ExecutionLock).where(
                                ExecutionLock.scope_key == _scope_key,
                                ExecutionLock.owner_uuid == _owner_uuid,
                            )
                        )).scalar_one_or_none()
                        if lock:
                            lock.heartbeat_at = _dt_lock.now(_tz_lock.utc)
                            await _hb_session.commit()
                except Exception as _hb_err:
                    logger.debug("Lock heartbeat refresh failed (non-fatal, lock will expire): %s", _hb_err)

        _lock_heartbeat_task = asyncio.create_task(_refresh_lock_heartbeat())
    # ─────────────────────────────────────────────────────────────────────────

    balance = await executor.get_account_balance()
    sizer = PositionSizer(balance, symbol=args.symbol)
    risk_monitor = RiskMonitor(balance, budget_multiplier=args.risk_budget)
    # ── Phase 3B: Seed risk monitor from DB with trade_repo ────────────────
    async with container.db_session_factory() as _seed_session:
        from alphaloop.db.repositories.trade_repo import TradeRepository
        _seed_repo = TradeRepository(_seed_session)
        await risk_monitor.seed_from_db(trade_repo=_seed_repo, instance_id=instance_id)
    # ─────────────────────────────────────────────────────────────────────────

    # ── Phase 4E: Load DB settings into runtime components ─────────────────
    try:
        from alphaloop.config.settings_service import SettingsService as _SS4E
        _ss_4e = _SS4E(container.db_session_factory)
        _db_risk_pct = await _ss_4e.get_float("RISK_PCT", None)
        if _db_risk_pct is None:
            _db_risk_pct = await _ss_4e.get_float("RISK_PER_TRADE_PCT", None)
        _db_max_daily = await _ss_4e.get_float("MAX_DAILY_LOSS_PCT", None)
        _db_max_session = await _ss_4e.get_float("MAX_SESSION_LOSS_PCT", None)
        _db_max_heat = await _ss_4e.get_float("MAX_PORTFOLIO_HEAT_PCT", None)
        _db_max_concurrent = await _ss_4e.get_int("MAX_CONCURRENT_TRADES", None)
        _db_consecutive_loss_limit = await _ss_4e.get_int(
            "CONSECUTIVE_LOSS_LIMIT", None
        )
        if _db_risk_pct is not None:
            sizer.risk_per_trade_pct = _db_risk_pct
        if _db_max_daily is not None:
            risk_monitor.max_daily_loss_pct = _db_max_daily
        if _db_max_session is not None:
            risk_monitor.max_session_loss_pct = _db_max_session
        if _db_max_heat is not None:
            risk_monitor.max_portfolio_heat_pct = _db_max_heat
        if _db_max_concurrent is not None:
            risk_monitor.max_concurrent_trades = _db_max_concurrent
        if _db_consecutive_loss_limit is not None:
            risk_monitor.consecutive_loss_limit = _db_consecutive_loss_limit
        logger.info(
            "[settings] Runtime risk params from DB: risk_pct=%s daily_loss=%s "
            "session_loss=%s heat=%s concurrent=%s consecutive=%s",
            _db_risk_pct,
            _db_max_daily,
            _db_max_session,
            _db_max_heat,
            _db_max_concurrent,
            _db_consecutive_loss_limit,
        )
    except Exception as settings_err:
        logger.warning("[settings] Failed to load DB settings into runtime: %s", settings_err)
    # ─────────────────────────────────────────────────────────────────────────

    # ── Redis HA state sync (optional — only if REDIS_URL env var is set) ──────
    import os as _os
    _redis_sync = None
    _redis_url = _os.environ.get("REDIS_URL", "")
    if _redis_url:
        from alphaloop.risk.redis_state import RedisStateSync
        _redis_sync = RedisStateSync(_redis_url, instance_id=instance_id)
        if await _redis_sync.connect():
            # Attempt to restore in-memory state from Redis cache
            # (non-destructive — DB seed is authoritative)
            await _redis_sync.pull_risk_state(risk_monitor)
        else:
            _redis_sync = None  # disable if connection failed
    # ──────────────────────────────────────────────────────────────────────────

    from alphaloop.tools.registry import get_registry
    from alphaloop.config.settings_service import SettingsService
    from alphaloop.ai.caller import AICaller
    from alphaloop.notifications.telegram import TelegramNotifier
    from alphaloop.utils.crypto import decrypt_value

    tool_registry = get_registry()
    settings_service = SettingsService(container.db_session_factory)

    async def _load_ai_api_keys() -> dict[str, str]:
        key_names = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "xai": "XAI_API_KEY",
            "qwen": "QWEN_API_KEY",
        }
        env_keys = {
            "gemini": config.api.gemini_api_key.get_secret_value(),
            "openai": config.api.openai_api_key.get_secret_value(),
            "anthropic": config.api.claude_api_key.get_secret_value(),
            "deepseek": config.api.deepseek_api_key.get_secret_value(),
            "xai": config.api.xai_api_key.get_secret_value(),
            "qwen": config.api.qwen_api_key.get_secret_value(),
        }

        resolved: dict[str, str] = {}
        for provider, setting_key in key_names.items():
            db_key = ""
            raw = await settings_service.get(setting_key)
            if raw:
                try:
                    db_key = decrypt_value(raw)
                except Exception:
                    db_key = raw
            key = (db_key or env_keys.get(provider, "")).strip()
            if key:
                resolved[provider] = key
        return resolved

    # P0.2: AI caller — routes to correct provider based on model_id
    ai_caller = AICaller(api_keys=await _load_ai_api_keys())

    # P0.3: Telegram notifier — reads creds from settings dynamically
    notifier = TelegramNotifier(settings_service=settings_service)

    # S-03: Restore RegimeClassifier EWM smoothed state from DB (survives restarts)
    try:
        import json as _regime_json
        _regime_key = f"regime_state_{args.symbol}"
        _regime_raw = await settings_service.get(_regime_key)
        if _regime_raw and hasattr(trading_loop, "_regime_classifier"):
            _regime_state = _regime_json.loads(_regime_raw)
            trading_loop._regime_classifier.load_state(_regime_state)
            logger.info("[startup] Regime EWM state restored for %s", args.symbol)
    except Exception as _regime_err:
        logger.warning("[startup] Could not restore regime state (non-critical): %s", _regime_err)

    # Wire regime state save callback — persists EWM scores after each classify()
    try:
        import json as _regime_save_json
        _regime_save_key = f"regime_state_{args.symbol}"
        async def _save_regime_state(state: dict) -> None:
            try:
                await settings_service.set(_regime_save_key, _regime_save_json.dumps(state))
            except Exception as _rs_err:
                logger.debug("[regime] State save failed (non-critical): %s", _rs_err)
        if hasattr(trading_loop, "_regime_classifier"):
            trading_loop._regime_classifier._on_state_changed = _save_regime_state
    except Exception as _regime_wire_err:
        logger.warning("[startup] Could not wire regime state callback: %s", _regime_wire_err)

    # ── Phase 3E: Runtime capability manifest ──────────────────────────────
    _mode = "dry-run"
    if args.live and not dry_run and args.force_start:
        _mode = "live (FORCE START)"
    elif args.live and dry_run:
        _mode = "live (DEGRADED)"
    elif args.live and not dry_run:
        _mode = "live"

    _recon_status = "skipped (dry-run)"
    if not dry_run:
        _recon_status = "passed" if (_recon_report and not _recon_report.has_critical) else "failed"

    import json as _manifest_json
    _manifest = {
        "version": __version__,
        "mode": _mode,
        "symbol": args.symbol,
        "instance_id": instance_id,
        "reconciliation": _recon_status,
        "broker_identity": "verified" if not dry_run else "skipped",
        "schema": "dev-mode (create_all)" if "sqlite" in config.db.url else "verified",
        "risk_state": f"seeded (open={risk_monitor._open_trades}, daily_pnl={risk_monitor._daily_pnl:.2f})" if hasattr(risk_monitor, "_open_trades") else "unseeded",
        "single_writer": f"acquired (scope={_scope_key})" if not dry_run else "skipped (dry-run)",
    }
    logger.info(
        "[startup-manifest] AlphaLoop v%s\n%s",
        __version__,
        "\n".join(f"  {k:20s} {v}" for k, v in _manifest.items()),
    )
    logger.info("[startup-manifest-json] %s", _manifest_json.dumps(_manifest))

    # Phase 3E: Publish manifest to event bus
    try:
        from alphaloop.core.events import CycleStarted  # reuse as lightweight event
        from dataclasses import dataclass as _dc_manifest
        from alphaloop.core.events import Event as _EventBase

        @_dc_manifest
        class StartupManifest(_EventBase):
            manifest: dict = None

        await container.event_bus.publish(StartupManifest(manifest=_manifest))
    except Exception as _manifest_err:
        logger.debug("Startup manifest publish failed (non-critical): %s", _manifest_err)
    # ─────────────────────────────────────────────────────────────────────────

    trading_loop = TradingLoop(
        symbol=args.symbol,
        instance_id=instance_id,
        poll_interval=args.poll_interval,
        dry_run=dry_run,
        event_bus=container.event_bus,
        signal_engine=MultiAssetSignalEngine(args.symbol, event_bus=container.event_bus),
        sizer=sizer,
        executor=executor,
        risk_monitor=risk_monitor,
        settings_service=settings_service,
        tool_registry=tool_registry,
        ai_caller=ai_caller,               # P0.2
        notifier=notifier,                  # P0.3
        session_factory=container.db_session_factory,  # P0.1
        supervision_service=container.supervision_service,
        redis_sync=_redis_sync,            # v3.1 HA state cache (optional)
    )

    # Wire MetaLoop if enabled
    metaloop_enabled = await settings_service.get_bool("METALOOP_ENABLED", default=False)
    if metaloop_enabled:
        from alphaloop.trading.meta_loop import MetaLoop
        from alphaloop.core.events import TradeClosed

        meta_loop = MetaLoop(
            symbol=args.symbol,
            instance_id=instance_id,
            session_factory=container.db_session_factory,
            event_bus=container.event_bus,
            settings_service=settings_service,
            ai_callback=ai_caller,
            check_interval=await settings_service.get_int("METALOOP_CHECK_INTERVAL", 20),
            rollback_window=await settings_service.get_int("METALOOP_ROLLBACK_WINDOW", 30),
            auto_activate=await settings_service.get_bool("METALOOP_AUTO_ACTIVATE", default=False),
            degradation_threshold=await settings_service.get_float("METALOOP_DEGRADATION_THRESHOLD", 0.7),
        )
        container.event_bus.subscribe(TradeClosed, meta_loop.on_trade_closed)
        logger.info("MetaLoop enabled for %s", args.symbol)

    # ── Compliance Reporter — wire breach log to RiskLimitHit events ──────────
    from alphaloop.compliance.reporting import ComplianceReporter
    from alphaloop.core.events import RiskLimitHit as _RLH_compliance
    _compliance_reporter = ComplianceReporter(settings_service=settings_service)
    container.event_bus.subscribe(_RLH_compliance, _compliance_reporter.record_breach)
    container.compliance_reporter = _compliance_reporter  # expose to WebUI routes
    logger.info("ComplianceReporter wired to RiskLimitHit events")
    # ──────────────────────────────────────────────────────────────────────────

    # ── P1.3 + P1.4: AlertEngine + NotificationDispatcher ─────────────────────
    from alphaloop.monitoring.alert_rules import AlertEngine, create_default_rules
    from alphaloop.notifications.dispatcher import NotificationDispatcher
    from alphaloop.core.events import (
        TradeClosed as _TC, RiskLimitHit as _RLH, PipelineBlocked as _PB,
    )

    alert_engine = AlertEngine()
    for rule in create_default_rules():
        alert_engine.register_rule(rule)
    container.alert_engine = alert_engine  # expose to WebUI

    # P1.4: Wrap notifier with dispatcher for batching/dedup on alerts
    alert_dispatcher = NotificationDispatcher(notifier, flush_interval_sec=60.0)

    def _alert_notify_callback(alert) -> None:
        """Forward fired alerts to Telegram via dispatcher (fire-and-forget)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(alert_dispatcher.enqueue(alert.message))
        except RuntimeError:
            pass  # No running loop — skip notification

    alert_engine.on_alert(_alert_notify_callback)

    async def _alert_on_trade_closed(event) -> None:
        alert_engine.evaluate({
            "daily_pnl": risk_monitor._daily_pnl if risk_monitor else 0,
            "daily_loss_threshold": -(balance * 0.03),
            "consecutive_losses": risk_monitor._consecutive_losses if risk_monitor else 0,
        })

    async def _alert_on_risk_limit(event) -> None:
        alert_engine.evaluate({
            "circuit_breaker_open": event.limit_type == "circuit_breaker",
            "portfolio_heat_pct": 6.0 if event.limit_type == "portfolio_heat" else 0,
        })

    container.event_bus.subscribe(_TC, _alert_on_trade_closed)
    container.event_bus.subscribe(_RLH, _alert_on_risk_limit)
    logger.info("AlertEngine wired with %d rules + NotificationDispatcher", len(alert_engine.rules_summary))
    # ────────────────────────────────────────────────────────────────────────────

    # H-10: Alert if NEWS_API_KEY is unconfigured — news_filter will block all trades silently.
    try:
        from alphaloop.core.events import AlertTriggered as _AlertTriggered
        _news_key = await settings_service.get("NEWS_API_KEY") or ""
        _finnhub = await settings_service.get("FINNHUB_API_KEY") or ""
        _fmp = await settings_service.get("FMP_API_KEY") or ""
        if not (_news_key.strip() or _finnhub.strip() or _fmp.strip()):
            logger.warning(
                "[H-10] No news API key configured (NEWS_API_KEY / FINNHUB_API_KEY / FMP_API_KEY). "
                "news_filter will block all trades if enabled."
            )
            await container.event_bus.publish(_AlertTriggered(
                severity="HIGH",
                rule_name="news_api_key_unconfigured",
                message=(
                    "No news API key configured — news_filter will block all trades. "
                    "Set NEWS_API_KEY, FINNHUB_API_KEY, or FMP_API_KEY in Settings → Tools."
                ),
                symbol=args.symbol,
            ))
    except Exception as _h10_err:
        logger.debug("[H-10] News API key check failed (non-critical): %s", _h10_err)
    # ────────────────────────────────────────────────────────────────────────────

    # Register this instance in the DB so the WebUI shows the agent card
    import os
    from alphaloop.db.models.instance import RunningInstance
    from sqlalchemy import delete as sa_delete

    async with container.db_session_factory() as db:
        # Clean up stale entry for this specific instance (not all entries for the symbol,
        # since multiple agents can run on the same symbol with different strategy cards)
        await db.execute(
            sa_delete(RunningInstance).where(RunningInstance.instance_id == instance_id)
        )
        db.add(RunningInstance(
            symbol=args.symbol,
            instance_id=instance_id,
            pid=os.getpid(),
            strategy_version=None,  # Updated once strategy is loaded
        ))
        await db.commit()
    logger.info("Registered agent %s (PID %d) for %s", instance_id, os.getpid(), args.symbol)

    # ── Bridge: forward all events to the WebUI ring buffer via HTTP ──────────
    import dataclasses
    import urllib.request

    _webui_ingest = f"http://localhost:{args.webui_port}/api/events/ingest"
    _bridge_token = os.environ.get("AUTH_TOKEN", "")

    import json as _json

    def _do_post(payload: bytes) -> None:
        """Synchronous HTTP POST — runs in thread pool to avoid blocking the event loop."""
        headers = {"Content-Type": "application/json"}
        if _bridge_token:
            headers["Authorization"] = f"Bearer {_bridge_token}"
        req = urllib.request.Request(
            _webui_ingest, data=payload,
            headers=headers, method="POST",
        )
        urllib.request.urlopen(req, timeout=1)

    async def _bridge_event(event) -> None:
        try:
            data = {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                    for k, v in dataclasses.asdict(event).items()}
            data["instance_id"] = instance_id
            payload = _json.dumps({
                "type":      type(event).__name__,
                "timestamp": event.timestamp.isoformat(),
                "data":      data,
            }).encode()
            await asyncio.to_thread(_do_post, payload)
        except Exception as exc:
            logger.warning("Event bridge POST failed: %s", exc)

    from alphaloop.core.events import (
        CycleStarted, CycleCompleted, PipelineStep, PipelineBlocked,
        SignalGenerated, SignalValidated, SignalRejected, TradeOpened,
        TradeClosed, RiskLimitHit, StrategyPromoted, StrategyRolledBack,
        MetaLoopCompleted, TradeRepositioned,
    )
    for _evt_cls in [CycleStarted, CycleCompleted, PipelineStep, PipelineBlocked,
                     SignalGenerated, SignalValidated, SignalRejected, TradeOpened,
                     TradeClosed, RiskLimitHit, StrategyPromoted, StrategyRolledBack,
                     MetaLoopCompleted, TradeRepositioned]:
        container.event_bus.subscribe(_evt_cls, _bridge_event)
    logger.info("Event bridge → %s (%d event types)", _webui_ingest, 14)
    # ─────────────────────────────────────────────────────────────────────────

    # H-03: Background reconciliation task — runs every 15 minutes in live mode
    _bg_recon_task = None
    if not dry_run:
        async def _bg_reconcile():
            """Periodic reconciliation to detect orphaned positions."""
            _RECON_INTERVAL_SEC = 900  # 15 minutes
            while True:
                await asyncio.sleep(_RECON_INTERVAL_SEC)
                try:
                    from alphaloop.execution.reconciler import PositionReconciler
                    from alphaloop.db.repositories.trade_repo import TradeRepository
                    async with container.db_session_factory() as _bg_session:
                        _bg_repo = TradeRepository(_bg_session)
                        _bg_reconciler = PositionReconciler(
                            executor=executor, trade_repo=_bg_repo
                        )
                        _bg_report = await _bg_reconciler.reconcile(
                            instance_id=instance_id
                        )
                    if _bg_report.has_critical:
                        logger.critical(
                            "[recon-bg] Critical discrepancy found: %d issues",
                            _bg_report.issue_count,
                        )
                        await _record_incident(
                            "bg_reconciliation_critical",
                            f"Background reconciliation found {_bg_report.issue_count} critical issues",
                            severity="critical",
                        )
                    elif _bg_report.issue_count:
                        logger.warning(
                            "[recon-bg] %d discrepancies found (non-critical)",
                            _bg_report.issue_count,
                        )
                    else:
                        logger.debug("[recon-bg] Clean — broker/DB positions in sync")
                except asyncio.CancelledError:
                    raise
                except Exception as _bg_recon_err:
                    logger.warning("[recon-bg] Reconciliation failed: %s", _bg_recon_err)

        _bg_recon_task = asyncio.create_task(_bg_reconcile())
        logger.info("[recon-bg] Background reconciliation task started (every 15min)")

    try:
        await trading_loop.run()
    finally:
        if _bg_recon_task is not None:
            _bg_recon_task.cancel()
            try:
                await _bg_recon_task
            except asyncio.CancelledError:
                pass
        logger.info("Graceful shutdown initiated for %s", instance_id)

        # Step 1: Save guard state to DB (authoritative kill-switch persistence)
        try:
            from alphaloop.risk.guard_persistence import save_guard_state
            await save_guard_state(
                settings_service=settings_service,
                hash_filter=getattr(trading_loop, "_signal_hash", None),
                conf_variance=getattr(trading_loop, "_conf_variance", None),
                spread_regime=getattr(trading_loop, "_spread_regime", None),
                equity_scaler=getattr(trading_loop, "_equity_scaler", None),
                dd_pause=getattr(trading_loop, "_dd_pause", None),
            )
            logger.info("Guard state saved to DB")
        except Exception as gs_err:
            logger.warning("Failed to save guard state: %s", gs_err)

        # Step 2: Reconcile open positions on shutdown (non-dry-run)
        if not dry_run:
            try:
                from alphaloop.execution.reconciler import PositionReconciler
                from alphaloop.db.repositories.trade_repo import TradeRepository
                async with container.db_session_factory() as _recon_session:
                    trade_repo = TradeRepository(_recon_session)
                    reconciler = PositionReconciler(executor=executor, trade_repo=trade_repo)
                    report = await reconciler.reconcile(instance_id=instance_id)
                _shutdown_payload = {
                    "stage": "shutdown",
                    "reconciled": report.reconciled,
                    "has_critical": report.has_critical,
                    "issue_count": report.issue_count,
                    "broker_positions": report.broker_positions,
                    "db_open_trades": report.db_open_trades,
                    "issues": [
                        {
                            "ticket": issue.ticket,
                            "symbol": issue.symbol,
                            "issue_type": issue.issue_type,
                            "description": issue.description,
                            "severity": issue.severity,
                            "auto_resolved": issue.auto_resolved,
                        }
                        for issue in report.issues
                    ],
                }
                await _record_event(
                    "reconciliation",
                    "shutdown_reconciliation_completed",
                    "Shutdown reconciliation completed",
                    severity="warning" if report.issues else "info",
                    payload=_shutdown_payload,
                )
                if report.issues:
                    await _record_incident(
                        "reconciliation_block",
                        "Shutdown reconciliation found unresolved issues",
                        severity="warning" if not report.has_critical else "critical",
                        payload=_shutdown_payload,
                    )
                    logger.warning(
                        "Shutdown reconciliation: %d issue(s) — manual attention required",
                        len(report.issues),
                    )
                else:
                    logger.info("Shutdown reconciliation: no discrepancies")
            except Exception as recon_err:
                await _record_incident(
                    "reconciliation_block",
                    f"Shutdown reconciliation failed: {recon_err}",
                    severity="warning",
                    payload={"stage": "shutdown", "reason": "exception"},
                )
                logger.warning("Shutdown reconciliation failed: %s", recon_err)

        # Step 3: Record shutdown metric
        try:
            from alphaloop.monitoring.metrics import metrics_tracker as _mt
            _mt.record_sync("graceful_shutdown", 1)
        except Exception as _metric_err:
            logger.debug("Failed to record shutdown metric (non-critical): %s", _metric_err)

        # Step 4: Close Redis connection if open
        if _redis_sync:
            try:
                await _redis_sync.close()
            except Exception as _redis_err:
                logger.debug("Redis close failed during shutdown (non-critical): %s", _redis_err)

        # Step 5: Unregister this instance from the DB
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

        # Step 6: Release execution lock and cancel heartbeat
        if _lock_heartbeat_task:
            _lock_heartbeat_task.cancel()
        if not dry_run:
            try:
                from alphaloop.db.models.execution_lock import ExecutionLock as _EL
                async with container.db_session_factory() as _lock_sess:
                    await _lock_sess.execute(
                        sa_delete(_EL).where(_EL.scope_key == _scope_key)
                    )
                    await _lock_sess.commit()
                logger.info("[single-writer] Released lock for scope '%s'", _scope_key)
            except Exception as lock_err:
                logger.warning("Failed to release execution lock: %s", lock_err)

        await executor.disconnect()
        from alphaloop.core.lifecycle import shutdown
        await shutdown(container)
        logger.info("GRACEFUL_SHUTDOWN_COMPLETE | instance=%s", instance_id)


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
