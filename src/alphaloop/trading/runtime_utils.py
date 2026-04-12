from __future__ import annotations

import json
from typing import Any

from alphaloop.trading.strategy_loader import (
    build_runtime_strategy_context,
    build_strategy_reference,
)


def safe_json_payload(value: Any, *, max_depth: int = 4, _depth: int = 0) -> dict | None:
    """Best-effort conversion of runtime objects into JSON-safe dict payloads."""
    if value is None:
        return None
    if _depth > max_depth:
        return {"value": str(value)}
    if isinstance(value, dict):
        try:
            json.dumps(value, default=str)
            return value
        except Exception:
            return {"value": str(value)}
    try:
        from pydantic import BaseModel

        if isinstance(value, BaseModel):
            return safe_json_payload(value.model_dump(), max_depth=max_depth, _depth=_depth + 1)
    except ImportError:
        pass
    if hasattr(value, "__dict__") and not callable(value):
        raw = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        return safe_json_payload(raw, max_depth=max_depth, _depth=_depth + 1)
    return {"value": str(value)}


def session_name_from_context(context: Any) -> str:
    """Extract a stable session name from a dict or object-shaped market context."""
    if isinstance(context, dict):
        session = context.get("session", {})
        if isinstance(session, dict):
            return str(session.get("name", ""))
        return str(getattr(session, "name", "") or "")
    session = getattr(context, "session", None)
    if isinstance(session, dict):
        return str(session.get("name", ""))
    return str(getattr(session, "name", "") or "")


def current_account_balance(*, risk_monitor: Any = None, sizer: Any = None) -> float:
    """Return the best available positive account balance from live runtime state."""
    if risk_monitor is not None:
        balance = float(getattr(risk_monitor, "account_balance", 0.0) or 0.0)
        if balance > 0:
            return balance
    if sizer is not None:
        balance = float(getattr(sizer, "account_balance", 0.0) or 0.0)
        if balance > 0:
            return balance
    return 0.0


def current_runtime_strategy(*, runtime_strategy: Any = None, active_strategy: Any = None) -> dict[str, Any]:
    """Return the canonical current runtime snapshot, preferring a cached runtime payload."""
    if runtime_strategy:
        return dict(runtime_strategy)
    if active_strategy is None:
        return {}
    return build_runtime_strategy_context(active_strategy)


def current_strategy_reference(
    *,
    symbol: str,
    runtime_strategy: Any = None,
    active_strategy: Any = None,
) -> dict[str, str]:
    """Return canonical current strategy identity fields for a live runtime path."""
    runtime = current_runtime_strategy(
        runtime_strategy=runtime_strategy,
        active_strategy=active_strategy,
    )
    return build_strategy_reference(runtime, fallback_symbol=symbol)
