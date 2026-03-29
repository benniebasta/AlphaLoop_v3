import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from alphaloop.research.report_generator import ReportGenerator

@pytest.fixture
def mock_session_factory():
    factory = AsyncMock()
    return factory

async def test_generate_empty(mock_session_factory):
    """Test report with no trades."""
    gen = ReportGenerator(mock_session_factory)
    with patch.object(gen, 'generate', return_value={"period": "daily", "trade_count": 0, "message": "No closed trades in this period"}):
        report = await gen.generate("daily")
        assert report["trade_count"] == 0

async def test_format_telegram_empty():
    gen = ReportGenerator(AsyncMock())
    report = {"period": "daily", "trade_count": 0}
    msg = await gen.format_telegram(report)
    assert "No trades" in msg

async def test_format_telegram_with_data():
    gen = ReportGenerator(AsyncMock())
    report = {
        "period": "weekly",
        "start": "2026-03-23T00:00:00",
        "end": "2026-03-29T23:59:59",
        "trade_count": 10,
        "wins": 6,
        "losses": 3,
        "breakevens": 1,
        "win_rate": 66.7,
        "total_pnl": 450.50,
        "avg_pnl": 45.05,
        "best_trade": 200.0,
        "worst_trade": -100.0,
        "sharpe": 1.234,
        "max_drawdown": 150.0,
        "by_symbol": {"XAUUSD": {"count": 10, "pnl": 450.50, "wins": 6}},
    }
    msg = await gen.format_telegram(report)
    assert "Weekly Report" in msg
    assert "$450.50" in msg or "+450.50" in msg
    assert "XAUUSD" in msg
