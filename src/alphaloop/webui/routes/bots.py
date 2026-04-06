"""
GET/POST /api/bots — Alpha Agent (running instance) management.

Includes WebUI-driven agent launch via subprocess.
"""

from __future__ import annotations

import json
import logging
import os
import signal as os_signal
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.core.constants import STRATEGY_VERSIONS_DIR
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.trading.strategy_loader import build_active_strategy_payload
from alphaloop.db.models.instance import RunningInstance
from alphaloop.webui.deps import get_db_session, get_container

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bots", tags=["bots"])


class BotCreate(BaseModel):
    symbol: str
    instance_id: str
    pid: int
    strategy_version: str | None = None


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for operator bot-control actions when AUTH_TOKEN is set."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _record_operator_audit(
    session: AsyncSession,
    *,
    action: str,
    target: str,
    old_value: str | None,
    new_value: str | None,
    source_ip: str,
) -> None:
    session.add(OperatorAuditLog(
        operator="webui",
        action=action,
        target=target,
        old_value=old_value,
        new_value=new_value,
        source_ip=source_ip,
    ))


def _bot_to_dict(b: RunningInstance, bound_strategy: dict | None = None) -> dict:
    bound_version = None
    if bound_strategy:
        try:
            version = int(bound_strategy.get("version", 0) or 0)
            if version > 0:
                bound_version = f"v{version}"
        except (TypeError, ValueError):
            bound_version = None
    d = {
        "id": b.id,
        "symbol": b.symbol,
        "instance_id": b.instance_id,
        "pid": b.pid,
        "started_at": b.started_at.isoformat() + "Z" if b.started_at else None,
        "strategy_version": b.strategy_version or bound_version,
    }
    if bound_strategy:
        payload = build_active_strategy_payload(bound_strategy)
        summary = payload.get("summary", {})
        sharpe = summary.get("sharpe")
        max_dd_pct = summary.get("max_dd_pct")
        total_pnl = summary.get("total_pnl")
        d["strategy"] = {
            "name": payload.get("name", ""),
            "version": payload.get("version", 0),
            "signal_mode": payload.get("signal_mode", ""),
            "status": payload.get("status", ""),
            "tools": payload.get("tools", {}),
            "overlay": bound_strategy.get("overlay", []),
            "metrics": {
                "win_rate": summary.get("win_rate", 0),
                "sharpe": sharpe if sharpe is not None else 0,
                "max_dd_pct": max_dd_pct if max_dd_pct is not None else 0,
                "total_pnl": total_pnl if total_pnl is not None else 0,
            },
        }
    return d


def _bound_active_strategy_payload(symbol: str, strategy_data: dict) -> dict:
    payload = build_active_strategy_payload(strategy_data)
    payload["symbol"] = symbol
    return payload


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_INFORMATION = 0x0400
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@router.get("")
async def list_bots(
    session: AsyncSession = Depends(get_db_session),
    container=Depends(get_container),
) -> dict:
    """Return all running bot instances with bound strategy data, auto-purging stale entries."""
    result = await session.execute(select(RunningInstance))
    bots = list(result.scalars())
    live, stale = [], []
    for b in bots:
        if _pid_alive(b.pid):
            live.append(b)
        else:
            stale.append(b)
    for b in stale:
        logger.info("Auto-purging stale agent record %s (PID %d)", b.instance_id, b.pid)
        await session.delete(b)
    if stale:
        await session.flush()

    # Enrich each live bot with its bound strategy card data
    enriched = []
    settings_svc = None
    try:
        from alphaloop.config.settings_service import SettingsService
        settings_svc = SettingsService(container.db_session_factory)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("[bots] SettingsService init failed: %s", e)

    for b in live:
        bound = None
        if settings_svc:
            try:
                raw = await settings_svc.get(f"active_strategy_{b.instance_id}")
                if not raw:
                    raw = await settings_svc.get(f"active_strategy_{b.symbol}")
                if raw:
                    bound = json.loads(raw)
                    # Fetch dry-run overlay tools for this strategy
                    ver = bound.get("version", 0)
                    overlay_raw = await settings_svc.get(f"dry_run_overlay_{b.symbol}_v{ver}")
                    if overlay_raw:
                        bound["overlay"] = json.loads(overlay_raw).get("extra_tools", [])
            except Exception:
                pass
        enriched.append(_bot_to_dict(b, bound))
    return {"bots": enriched}


@router.post("")
async def register_bot(
    body: BotCreate,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Register a new running bot instance."""
    _require_operator_auth(authorization)
    # Check for collision by instance_id (not symbol — multiple agents per symbol allowed)
    existing = await session.execute(
        select(RunningInstance).where(RunningInstance.instance_id == body.instance_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Instance {body.instance_id} already registered",
        )
    bot = RunningInstance(
        symbol=body.symbol,
        instance_id=body.instance_id,
        pid=body.pid,
        strategy_version=body.strategy_version,
    )
    session.add(bot)
    await session.flush()
    _record_operator_audit(
        session,
        action="bot_register",
        target=body.instance_id,
        old_value=None,
        new_value=f"{body.symbol}:{body.pid}:{body.strategy_version or ''}",
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()
    return {"status": "ok", "bot": _bot_to_dict(bot)}


@router.delete("/{instance_id}")
async def unregister_bot(
    instance_id: str,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a bot instance record."""
    _require_operator_auth(authorization)
    result = await session.execute(
        select(RunningInstance).where(RunningInstance.instance_id == instance_id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    _record_operator_audit(
        session,
        action="bot_unregister",
        target=instance_id,
        old_value=f"{bot.symbol}:{bot.pid}:{bot.strategy_version or ''}",
        new_value="deleted",
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.delete(bot)
    await session.commit()
    return {"status": "ok", "removed": instance_id}


# ── WebUI Agent Launch / Stop ────────────────────────────────────────────────

class AgentStartRequest(BaseModel):
    symbol: str = "XAUUSD"
    dry_run: bool = True
    strategy_version: int | None = None
    risk_budget_pct: float = 1.0
    poll_interval_sec: float = 60.0


@router.post("/start")
async def start_agent(
    body: AgentStartRequest,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
    container=Depends(get_container),
) -> dict:
    """
    Launch a trading agent as a subprocess from the WebUI.

    Spawns: python -m alphaloop.main --symbol {symbol} [--dry-run|--live]
    Binds the selected strategy card to active_strategy_{instance_id} before launch.
    Multiple agents can run on the same symbol with different strategy cards.
    """
    _require_operator_auth(authorization)

    # Purge stale records for this symbol (dead PIDs only)
    existing = await session.execute(
        select(RunningInstance).where(RunningInstance.symbol == body.symbol)
    )
    for bot in existing.scalars():
        if not _pid_alive(bot.pid):
            logger.info("Purging stale agent record %s (PID %d)", bot.instance_id, bot.pid)
            await session.delete(bot)
    await session.flush()

    instance_id = f"{body.symbol}_{uuid.uuid4().hex[:8]}"

    # Bind selected strategy card to per-instance settings key
    if body.strategy_version is not None:
        try:
            from alphaloop.config.settings_service import SettingsService
            settings_svc = SettingsService(container.db_session_factory)

            # Look up the strategy JSON file
            strategy_data = None
            for f in STRATEGY_VERSIONS_DIR.glob(f"{body.symbol}_v*.json"):
                try:
                    data = json.loads(f.read_text())
                    if data.get("version") == body.strategy_version:
                        strategy_data = data
                        break
                except (json.JSONDecodeError, OSError):
                    continue

            if strategy_data:
                active_payload = _bound_active_strategy_payload(body.symbol, strategy_data)
                await settings_svc.set(
                    f"active_strategy_{instance_id}",
                    json.dumps(active_payload),
                )
                logger.info(
                    "Bound strategy %s v%d to instance %s",
                    body.symbol, body.strategy_version, instance_id,
                )
            else:
                logger.warning(
                    "Strategy %s v%d not found on disk — agent will use fallback",
                    body.symbol, body.strategy_version,
                )
        except Exception as e:
            logger.error("Failed to bind strategy card: %s", e)

    risk_budget = max(0.01, min(1.0, body.risk_budget_pct))

    # Use pythonw.exe on Windows — it's the windowless variant and never
    # creates a console window, unlike python.exe which is a console app.
    if sys.platform == "win32":
        _pythonw = Path(sys.executable).with_name("pythonw.exe")
        _exec = str(_pythonw) if _pythonw.exists() else sys.executable
    else:
        _exec = sys.executable

    # Detect the port this WebUI is actually running on so the agent bridge
    # POSTs events to the correct endpoint.
    from alphaloop.core.constants import WEBUI_DEFAULT_PORT
    _webui_port = request.url.port or WEBUI_DEFAULT_PORT

    cmd = [
        _exec, "-m", "alphaloop.main",
        "--symbol", body.symbol,
        "--instance-id", instance_id,
        "--risk-budget", str(risk_budget),
        "--webui-port", str(_webui_port),
        "--poll-interval", str(max(10.0, body.poll_interval_sec)),
    ]
    if body.dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--live")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent process
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        logger.info(
            "Launched agent %s for %s (PID %d, dry_run=%s, risk_budget=%.0f%%)",
            instance_id, body.symbol, proc.pid, body.dry_run, risk_budget * 100,
        )
        # Pre-register so the card survives a hard refresh during agent startup.
        # main.py will delete+re-insert this record once the agent is fully up.
        pre_reg = RunningInstance(
            symbol=body.symbol,
            instance_id=instance_id,
            pid=proc.pid,
            strategy_version=(
                f"v{int(active_payload.get('version', 0) or 0)}"
                if body.strategy_version is not None and strategy_data
                else None
            ),
        )
        session.add(pre_reg)
        _record_operator_audit(
            session,
            action="bot_start",
            target=instance_id,
            old_value=None,
            new_value=f"{body.symbol}:{proc.pid}:{'dry_run' if body.dry_run else 'live'}",
            source_ip=request.client.host if request.client else "unknown",
        )
        await session.flush()
        return {
            "status": "ok",
            "instance_id": instance_id,
            "pid": proc.pid,
            "symbol": body.symbol,
            "dry_run": body.dry_run,
            "risk_budget_pct": risk_budget,
        }
    except Exception as e:
        logger.error("Failed to launch agent: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to launch agent: {e}")


@router.post("/{instance_id}/stop")
async def stop_agent(
    instance_id: str,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Stop a running trading agent by sending SIGTERM to its process.
    The agent's shutdown handler will unregister itself from the DB.
    """
    _require_operator_auth(authorization)

    result = await session.execute(
        select(RunningInstance).where(RunningInstance.instance_id == instance_id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    pid = bot.pid
    _method = "unknown"
    try:
        if sys.platform == "win32":
            # Phase 7C: Try graceful shutdown first via sentinel file,
            # then wait up to 10s, only force-kill on timeout
            import tempfile
            _sentinel = os.path.join(
                tempfile.gettempdir(), f"alphaloop_stop_{pid}.sentinel"
            )
            # Write sentinel file — the bot checks for this in its loop
            with open(_sentinel, "w") as f:
                f.write(instance_id)
            logger.info("Wrote stop sentinel for agent %s (PID %d)", instance_id, pid)

            # Poll for process exit (up to 10 seconds)
            _exited = False
            for _ in range(20):
                import time
                time.sleep(0.5)
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if str(pid) not in r.stdout:
                    _exited = True
                    break

            if _exited:
                _method = "graceful (sentinel)"
                logger.info("Agent %s exited gracefully via sentinel", instance_id)
            else:
                # Force kill as last resort
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                _method = "forced (taskkill /F)"
                logger.warning(
                    "Agent %s did not exit gracefully — forced kill (PID %d)",
                    instance_id, pid,
                )

            # Clean up sentinel
            try:
                os.remove(_sentinel)
            except OSError:
                pass
        else:
            os.kill(pid, os_signal.SIGTERM)
            _method = "SIGTERM"
            logger.info("Sent SIGTERM to agent %s (PID %d)", instance_id, pid)
    except ProcessLookupError:
        _method = "already_dead"
        logger.warning("Agent PID %d not found — cleaning up stale record", pid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop agent: {e}")

    # Remove DB record (agent shutdown handler may not have run on force kill)
    await session.delete(bot)
    _record_operator_audit(
        session,
        action="bot_stop",
        target=instance_id,
        old_value=f"{bot.symbol}:{pid}:running",
        new_value=_method,
        source_ip=request.client.host if request.client else "unknown",
    )
    await session.flush()

    return {"status": "ok", "instance_id": instance_id, "stop_method": _method}
