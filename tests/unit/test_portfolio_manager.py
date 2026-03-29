from alphaloop.trading.portfolio_manager import PortfolioManager, OpenPosition

def test_register_and_count():
    pm = PortfolioManager(max_total_positions=3)
    pm.register_open(OpenPosition(symbol="XAUUSD", direction="BUY", entry_price=2000, lot_size=0.1, risk_usd=100))
    assert pm.total_positions == 1

def test_portfolio_heat():
    pm = PortfolioManager(account_balance=10000)
    pm.register_open(OpenPosition(symbol="XAUUSD", direction="BUY", entry_price=2000, lot_size=0.1, risk_usd=300))
    assert pm.portfolio_heat_pct == 3.0

def test_can_open_blocks_at_max():
    pm = PortfolioManager(max_total_positions=1, account_balance=10000)
    pm.register_open(OpenPosition(symbol="XAUUSD", direction="BUY", entry_price=2000, lot_size=0.1, risk_usd=100))
    ok, reason = pm.can_open_trade("BTCUSD", 100)
    assert not ok
    assert "Max positions" in reason

def test_can_open_blocks_at_heat():
    pm = PortfolioManager(max_total_positions=10, max_portfolio_heat_pct=5.0, account_balance=10000)
    pm.register_open(OpenPosition(symbol="XAUUSD", direction="BUY", entry_price=2000, lot_size=0.1, risk_usd=400))
    ok, reason = pm.can_open_trade("BTCUSD", 200)
    assert not ok
    assert "heat" in reason.lower()

def test_register_close():
    pm = PortfolioManager()
    pm.register_open(OpenPosition(symbol="XAUUSD", direction="BUY", entry_price=2000, lot_size=0.1, risk_usd=100))
    pm.register_close("XAUUSD", 2000)
    assert pm.total_positions == 0

def test_status():
    pm = PortfolioManager(account_balance=10000)
    pm.register_open(OpenPosition(symbol="XAUUSD", direction="BUY", entry_price=2000, lot_size=0.1, risk_usd=100))
    s = pm.status
    assert s["total_positions"] == 1
    assert "XAUUSD" in s["symbols"]
