"""
POST /api/test-flow/run   — launch pytest for a specific signal mode
GET  /api/test-flow/status — poll run state + captured log lines
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/test-flow", tags=["test_flow"])

_VALID_MODES = {"algo_only", "algo_ai", "ai_signal"}
_LINES: deque[str] = deque(maxlen=500)

_state: dict = {
    "running": False,
    "mode": None,
    "passed": 0,
    "failed": 0,
    "errors": 0,
    "start_time": None,
    "end_time": None,
    "exit_code": None,
}

# Project root: routes/ → webui/ → alphaloop/ → src/ → project_root
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


@router.post("/run")
async def run_test_flow(mode: str = Query(...)) -> dict:
    """Start a pytest run for the given signal mode."""
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"mode must be one of {sorted(_VALID_MODES)}",
        )
    if _state["running"]:
        raise HTTPException(status_code=409, detail="A test run is already in progress")
    asyncio.create_task(_run_pytest(mode))
    return {"started": True, "mode": mode}


@router.get("/status")
async def get_status() -> dict:
    """Return current test-run state and captured log lines."""
    elapsed = None
    if _state["start_time"]:
        end = _state["end_time"] or time.time()
        elapsed = round(end - _state["start_time"], 1)
    return {
        "running": _state["running"],
        "mode": _state["mode"],
        "passed": _state["passed"],
        "failed": _state["failed"],
        "errors": _state["errors"],
        "exit_code": _state["exit_code"],
        "elapsed": elapsed,
        "lines": list(_LINES),
    }


async def _run_pytest(mode: str) -> None:
    _state.update(
        running=True,
        mode=mode,
        passed=0,
        failed=0,
        errors=0,
        start_time=time.time(),
        end_time=None,
        exit_code=None,
    )
    _LINES.clear()
    _LINES.append(f"» Starting test flow  mode={mode}")
    _LINES.append(f"» Command: pytest tests/integration/test_signal_modes.py -k {mode} -v")
    _LINES.append("")

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pytest",
            "tests/integration/test_signal_modes.py",
            "-k", mode,
            "-v", "--tb=short", "--no-header", "-p", "no:warnings",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_PROJECT_ROOT),
        )

        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            _LINES.append(line)
            upper = line.upper()
            if " PASSED" in upper:
                _state["passed"] += 1
            elif " FAILED" in upper:
                _state["failed"] += 1
            elif (" ERROR" in upper and "::" in line):
                _state["errors"] += 1

        exit_code = await proc.wait()
        _state["exit_code"] = exit_code
        _LINES.append("")
        status_word = "PASSED" if exit_code == 0 else "FAILED"
        _LINES.append(
            f"» Done  exit={exit_code}  {status_word}  "
            f"passed={_state['passed']}  failed={_state['failed']}  errors={_state['errors']}"
        )

    except Exception as exc:
        _LINES.append(f"[runner error] {exc}")
        _state["exit_code"] = -1

    finally:
        _state["running"] = False
        _state["end_time"] = time.time()
