"""
pipeline/risk_gate.py — Stage 7: Risk capacity gating.

Completely independent of alpha evaluation.  Answers: "Can we afford
this trade?" not "Is this a good trade?"

Checks:
  - Daily loss limit
  - Session loss limit
  - Kill switch (defence-in-depth, also in MarketGate)
  - Portfolio heat cap (correlation-adjusted)
  - Drawdown pause (magnitude-scaled)
  - Correlation exposure
  - Equity curve scaling
  - Trade frequency
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.pipeline.types import RiskGateResult

logger = logging.getLogger(__name__)


class RiskGateRunner:
    """
    Wraps existing risk guards into a single Stage 7 check.

    Returns RiskGateResult with allowed=True/False and size modifiers.
    """

    def __init__(
        self,
        *,
        equity_curve_scaler=None,
        drawdown_pause_guard=None,
        portfolio_cap_guard=None,
        correlation_guard=None,
        risk_filter_tool=None,
    ):
        self._ec_scaler = equity_curve_scaler
        self._dd_pause = drawdown_pause_guard
        self._portfolio_cap = portfolio_cap_guard
        self._corr_guard = correlation_guard  # CorrelationGuard plugin instance
        self._risk_filter = risk_filter_tool  # RiskFilter plugin instance

    async def check(
        self,
        signal,
        context,
        *,
        symbol: str = "",
    ) -> RiskGateResult:
        """Run all risk capacity checks."""

        size_modifier = 1.0
        ec_scalar = 1.0

        # --- Risk filter plugin (delegated RiskMonitor check via plugin) ---
        if self._risk_filter:
            try:
                if hasattr(context, "trade_direction"):
                    context.trade_direction = getattr(signal, "direction", "")
                rf_result = await self._risk_filter.timed_run(context)
                if not rf_result.passed and rf_result.severity == "block":
                    return RiskGateResult(
                        allowed=False,
                        block_reason=f"Risk filter: {rf_result.reason}",
                        size_modifier=0.0,
                    )
            except Exception as exc:
                logger.warning("[RiskGate] Risk filter plugin error: %s", exc)

        # --- Risk monitor hard checks ---
        rm = getattr(context, "risk_monitor", None)
        if rm:
            can_trade, reason = await self._check_risk_monitor(rm)
            if not can_trade:
                return RiskGateResult(
                    allowed=False,
                    block_reason=reason,
                    size_modifier=0.0,
                )

        # --- Drawdown pause ---
        # Phase 7H: DrawdownPauseGuard has is_paused() but not remaining_pause()
        if self._dd_pause:
            if self._dd_pause.is_paused(symbol):
                return RiskGateResult(
                    allowed=False,
                    block_reason=f"Drawdown pause active for {symbol}",
                    size_modifier=0.0,
                )

        # --- Portfolio cap ---
        if self._portfolio_cap:
            direction = getattr(signal, "direction", "")
            entry_mid = 0.0
            ez = getattr(signal, "entry_zone", (0, 0))
            if ez:
                entry_mid = (ez[0] + ez[1]) / 2
            sl = getattr(signal, "stop_loss", 0)
            risk_pts = abs(entry_mid - sl) if entry_mid and sl else 0

            allowed, reason = self._portfolio_cap.check(
                symbol, direction, risk_pts, context
            )
            if not allowed:
                return RiskGateResult(
                    allowed=False,
                    block_reason=f"Portfolio cap: {reason}",
                    size_modifier=0.0,
                )

        # --- Correlation guard (uses the CorrelationGuard plugin) ---
        if self._corr_guard:
            try:
                # Set trade_direction on context for the plugin
                if hasattr(context, "trade_direction"):
                    context.trade_direction = getattr(signal, "direction", "")
                corr_result = await self._corr_guard.timed_run(context)
                if not corr_result.passed and corr_result.severity == "block":
                    return RiskGateResult(
                        allowed=False,
                        block_reason=f"Correlation: {corr_result.reason}",
                        size_modifier=0.0,
                    )
                if corr_result.size_modifier < 1.0:
                    size_modifier *= corr_result.size_modifier
                    logger.info(
                        "[RiskGate] Correlation reduce: %s → %.0f%% size",
                        corr_result.reason,
                        corr_result.size_modifier * 100,
                    )
            except Exception as exc:
                logger.warning("[RiskGate] Correlation guard error: %s", exc)

        # --- Equity curve scaler ---
        if self._ec_scaler:
            # Phase 7H: method is risk_scale(), not scale()
            ec_scalar = self._ec_scaler.risk_scale()
            if ec_scalar < 1.0:
                logger.info(
                    "[RiskGate] Equity curve scalar: %.2f", ec_scalar
                )

        risk_util = 0.0
        if rm:
            balance = getattr(rm, "account_balance", 0)
            open_risk = getattr(rm, "_open_risk_usd", 0)
            if balance > 0:
                risk_util = round(open_risk / balance, 4)

        return RiskGateResult(
            allowed=True,
            size_modifier=round(size_modifier, 3),
            equity_curve_scalar=round(ec_scalar, 3),
            risk_utilization=risk_util,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _check_risk_monitor(rm) -> tuple[bool, str]:
        """
        Delegate to RiskMonitor.can_open_trade().

        Returns (allowed, reason).
        """
        try:
            if hasattr(rm, "can_open_trade"):
                result = await rm.can_open_trade()
                if isinstance(result, tuple):
                    return result
                if isinstance(result, bool):
                    return result, "" if result else "Risk monitor denied"
                # dict-style response
                if isinstance(result, dict):
                    return (
                        result.get("allowed", True),
                        result.get("reason", ""),
                    )
            return True, ""
        except Exception as exc:
            logger.error("[RiskGate] Risk monitor error: %s", exc)
            # Fail-closed for risk checks
            return False, f"Risk monitor error: {exc}"
