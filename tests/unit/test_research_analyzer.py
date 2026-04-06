from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alphaloop.core.types import TradeOutcome
from alphaloop.db.models.research import ResearchReport
from alphaloop.db.models.trade import TradeLog
from alphaloop.research.analyzer import ResearchAnalyzer, compute_metrics


def test_compute_metrics_emits_drawdown_aliases():
    trades = [
        {"outcome": TradeOutcome.WIN, "pnl_usd": 100.0, "pnl_r": 1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.LOSS, "pnl_usd": -35.0, "pnl_r": -1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.WIN, "pnl_usd": 40.0, "pnl_r": 1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.LOSS, "pnl_usd": -20.0, "pnl_r": -1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.WIN, "pnl_usd": 10.0, "pnl_r": 1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.LOSS, "pnl_usd": -5.0, "pnl_r": -1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.WIN, "pnl_usd": 15.0, "pnl_r": 1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.WIN, "pnl_usd": 5.0, "pnl_r": 1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.LOSS, "pnl_usd": -8.0, "pnl_r": -1.0, "setup_type": "trend"},
        {"outcome": TradeOutcome.WIN, "pnl_usd": 12.0, "pnl_r": 1.0, "setup_type": "trend"},
    ]

    metrics = compute_metrics(trades)

    assert metrics["max_drawdown_usd"] == -35.0
    assert metrics["max_drawdown_pct"] == -35.0
    assert metrics["max_dd_pct"] == -35.0


@pytest.mark.asyncio
async def test_research_analyzer_persists_drawdown_from_matching_metric_key(db_engine):
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = SimpleNamespace(publish=AsyncMock())
    analyzer = ResearchAnalyzer(
        session_factory=session_factory,
        event_bus=event_bus,
        ai_callback=None,
    )

    now = datetime.now(timezone.utc)
    pnl_values = [100.0, -35.0, 40.0, -20.0, 10.0, -5.0, 15.0, 5.0, -8.0, 12.0]

    async with session_factory() as session:
        for idx, pnl in enumerate(pnl_values):
            session.add(TradeLog(
                signal_id=f"sig-{idx}",
                client_order_id=f"cid-{idx}",
                symbol="XAUUSD",
                direction="BUY",
                setup_type="trend",
                entry_price=2300.0,
                entry_zone_low=2299.0,
                entry_zone_high=2301.0,
                stop_loss=2290.0,
                take_profit_1=2320.0,
                lot_size=0.2,
                risk_pct=1.0,
                risk_amount_usd=100.0,
                outcome=TradeOutcome.WIN if pnl >= 0 else TradeOutcome.LOSS,
                opened_at=now - timedelta(days=1, minutes=idx),
                closed_at=now - timedelta(hours=1, minutes=idx),
                close_price=2310.0,
                pnl_usd=pnl,
                pnl_r=1.0 if pnl >= 0 else -1.0,
                order_ticket=1000 + idx,
                instance_id="research-test",
                strategy_version="v1",
                is_dry_run=True,
            ))
        await session.commit()

    result = await analyzer.run(symbol="XAUUSD", strategy_version="v1", lookback_days=30)

    assert result is not None
    assert result["metrics"]["max_drawdown_pct"] == -35.0

    async with session_factory() as session:
        report = (
            await session.execute(
                select(ResearchReport).where(ResearchReport.symbol == "XAUUSD")
            )
        ).scalar_one()

    assert report.max_drawdown_pct == -35.0
    assert report.raw_metrics["max_drawdown_pct"] == -35.0
    assert report.raw_metrics["max_drawdown_usd"] == -35.0
    event_bus.publish.assert_awaited_once()
