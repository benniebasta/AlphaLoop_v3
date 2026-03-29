"""
GET/POST /api/bots — Alpha Agent (running instance) management.

Includes WebUI-driven agent launch via subprocess.
"""

from __future__ import annotations

import logging
import os
import signal as os_signal
import subprocess
import sys
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.instance import RunningInstance
from alphaloop.webui.deps import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bots", tags=["bots"])


class BotCreate(BaseModel):
    symbol: str
    instance_id: str
    pid: int
    strategy_version: str | None = None


def _bot_to_dict(b: RunningInstance) -> dict:
    return {
        "id": b.id,
        "symbol": b.symbol,
        "instance_id": b.instance_id,
        "pid": b.pid,
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "strategy_version": b.strategy_version,
    }


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@router.get("")
async def list_bots(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all running bot instances, auto-purging stale entries."""
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
    return {"bots": [_bot_to_dict(b) for b in live]}


@router.post("")
async def register_bot(
    body: BotCreate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Register a new running bot instance."""
    # Check for collision
    existing = await session.execute(
        select(RunningInstance).where(RunningInstance.symbol == body.symbol)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Bot already running for {body.symbol}",
        )
    bot = RunningInstance(
        symbol=body.symbol,
        instance_id=body.instance_id,
        pid=body.pid,
        strategy_version=body.strategy_version,
    )
    session.add(bot)
    await session.flush()
    return {"status": "ok", "bot": _bot_to_dict(bot)}


@router.delete("/{instance_id}")
async def unregister_bot(
    instance_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a bot instance record."""
    result = await session.execute(
        select(RunningInstance).where(RunningInstance.instance_id == instance_id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    await session.delete(bot)
    return {"status": "ok", "removed": instance_id}


# ── WebUI Agent Launch / Stop ────────────────────────────────────────────────

class AgentStartRequest(BaseModel):
    symbol: str = "XAUUSD"
    dry_run: bool = True
    strategy_version: str | None = None


@router.post("/start")
async def start_agent(
    body: AgentStartRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Launch a trading agent as a subprocess from the WebUI.

    Spawns: python -m alphaloop.main --symbol {symbol} [--dry-run|--live]
    The subprocess registers itself in RunningInstance on startup.
    """
    # Check if an agent is already running for this symbol (skip stale records)
    existing = await session.execute(
        select(RunningInstance).where(RunningInstance.symbol == body.symbol)
    )
    existing_bot = existing.scalar_one_or_none()
    if existing_bot:
        if _pid_alive(existing_bot.pid):
            raise HTTPException(
                status_code=409,
                detail=f"Agent already running for {body.symbol}",
            )
        # Stale record — purge and allow relaunch
        await session.delete(existing_bot)
        await session.flush()

    instance_id = f"{body.symbol}_{uuid.uuid4().hex[:8]}"

    cmd = [
        sys.executable, "-m", "alphaloop.main",
        "--symbol", body.symbol,
        "--instance-id", instance_id,
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
        )
        logger.info(
            "Launched agent %s for %s (PID %d, dry_run=%s)",
            instance_id, body.symbol, proc.pid, body.dry_run,
        )
        return {
            "status": "ok",
            "instance_id": instance_id,
            "pid": proc.pid,
            "symbol": body.symbol,
            "dry_run": body.dry_run,
        }
    except Exception as e:
        logger.error("Failed to launch agent: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to launch agent: {e}")


@router.post("/{instance_id}/stop")
async def stop_agent(
    instance_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Stop a running trading agent by sending SIGTERM to its process.
    The agent's shutdown handler will unregister itself from the DB.
    """
    result = await session.execute(
        select(RunningInstance).where(RunningInstance.instance_id == instance_id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    pid = bot.pid
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("taskkill /F /T /PID %d for agent %s", pid, instance_id)
        else:
            os.kill(pid, os_signal.SIGTERM)
            logger.info("Sent SIGTERM to agent %s (PID %d)", instance_id, pid)
    except ProcessLookupError:
        logger.warning("Agent PID %d not found — cleaning up stale record", pid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop agent: {e}")

    # Always remove the DB record — on Windows SIGTERM is TerminateProcess (hard kill)
    # so the agent's shutdown handler never runs to self-unregister.
    await session.delete(bot)
    await session.flush()

    return {"status": "ok", "instance_id": instance_id, "signal_sent": "SIGTERM"}
