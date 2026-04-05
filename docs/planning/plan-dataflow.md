# AlphaLoop v3 — Data Flow

## Purpose
How data moves through the system — from market data ingestion to trade execution.

---

## 1. Market Data Flow

```
MT5 Terminal / yfinance
        │
        v
  OHLCVFetcher (data/fetcher.py)
  ├── MT5: asyncio.to_thread(mt5.copy_rates_from_pos) — for WebUI/backtests
  ├── Trading loop: sync mt5.copy_rates_from_pos directly (MT5 API is thread-unsafe)
  ├── yfinance: fallback when MT5 unavailable
  ├── TTL cache per timeframe (M1:60s, M5/M15:290s, H1:300s)
  ├── Symbol auto-resolution: XAUUSD → XAUUSDm (Exness suffix fallback: m, M, .raw)
  └── MT5 always connects even in dry-run mode (needed for price data)
        │
        v
  Indicators (data/indicators.py) — pure functions
  ├── RSI, EMA(21/55/200), ATR
  ├── VWAP, MACD, Bollinger, ADX
  ├── BOS (break of structure), FVG (fair value gap)
  └── volume_ma, swing_highs_lows
        │
        v
  MarketContext (data/market_context.py)
  ├── current_price, spread, atr, rsi, ema_21/55/200
  ├── trend_direction, session_info, news_events
  └── Built async from fetcher + indicators + news + dxy + sentiment
```

---

## 2. Signal Generation Flow

```
MarketContext + StrategyParams
        │
        ├── Mode AI ──────────┐
        │   SignalEngine       │
        │   (signals/engine.py)│
        │   ├── Build prompt   │
        │   ├── AICaller.call_role("signal")
        │   └── Parse JSON → TradeSignal
        │                      │
        ├── Mode A/B ─────────┤
        │   AlgorithmicEngine  │
        │   (signals/algorithmic.py)
        │   ├── EMA crossover  │
        │   ├── RSI filter     │
        │   └── Deterministic → TradeSignal
        │                      │
        v                      v
  TradeSignal (signals/schema.py)
  ├── trend, setup_type, direction
  ├── entry_zone, sl, tp1, tp2
  ├── confidence (0.0-1.0)
  └── reasoning (text)
```

---

## 3. Validation Flow

```
TradeSignal
        │
        v
  HardRuleChecker (validation/rules.py) — 13 checks
  ├── 1. confidence ≥ min_confidence
  ├── 2. sl_tp_dir — SL/TP on correct sides of entry
  ├── 3. sl_distance — within min/max ATR range
  ├── 4. rr_ratio ≥ min_rr_ratio
  ├── 5. session — tradeable session score
  ├── 6. spread — within spread limit
  ├── 7. rsi_extreme — not overbought/oversold
  ├── 8. ema200_trend — trend alignment
  ├── 9. news_blackout — no high-impact news
  ├── 10. tick_jump — 2-bar ATR spike
  ├── 11. liq_vacuum — thin-body spike candles
  ├── 12. setup_type — blocked setup types
  └── 13. regime_block — dead market regime
        │
        ├── FAIL → SignalRejected event → done
        │
        v
  UniversalValidator (validation/validator.py)
  ├── Optional AI validation (call_role("validator"))
  └── Applies validation_overrides from active strategy
        │
        v
  ValidatedSignal
  ├── All TradeSignal fields +
  ├── risk_score, validation_status
  └── rejection_feedback (if rejected)
```

---

## 4. Risk & Execution Flow

```
ValidatedSignal
        │
        v
  Risk Guards (risk/guards.py) — 7 stateful guards
  ├── 1. SignalHashFilter — duplicate signal dedup (window=3)
  ├── 2. ConfidenceVarianceFilter — unstable AI confidence (stdev>0.15)
  ├── 3. SpreadRegimeFilter — spread spike detection (1.8x normal)
  ├── 4. EquityCurveScaler — halve risk below equity MA (window=20)
  ├── 5. DrawdownPauseGuard — pause on accelerating losses
  ├── 6. NearDedupGuard — skip if open trade within N ATR
  └── 7. PortfolioCapGuard — block when total open risk exceeds cap
        │
        ├── BLOCKED → PipelineBlocked event → done
        │
        v
  RiskMonitor (risk/monitor.py)
  ├── Kill switch check
  ├── Daily loss limit check
  ├── Consecutive loss check
  └── Portfolio heat check
        │
        v
  PositionSizer (risk/sizer.py)
  ├── ATR-based position sizing
  ├── Confidence multiplier (0.85+→1.25x, <0.55→0.5x)
  ├── Margin cap enforcement (max 20% margin used)
  └── Macro modifier + vol regime adjustment
        │
        v
  MT5Executor (execution/mt5_executor.py)
  ├── asyncio.to_thread(mt5.order_send)
  ├── Dry-run mode: log only, no order
  └── → OrderResult (Pydantic)
        │
        v
  TradeOpened event → DB (TradeLog) → Telegram notification
```

---

## 5. Post-Trade Flow

```
  TradeClosed event (from MT5 position monitor / repositioner)
        │
        ├── MetaLoop (trading/meta_loop.py)
        │   ├── Count closed trades
        │   ├── Every check_interval trades: run health check
        │   ├── If degrading: trigger research/auto-improve
        │   └── If improved: create new strategy version
        │
        ├── MicroLearner (trading/micro_learner.py)
        │   ├── Confidence recalibration
        │   ├── SL distance nudge (±1% per trade, ±5% cap)
        │   └── Store adjustments in DB
        │
        ├── HealthMonitor (trading/health_monitor.py)
        │   ├── Update rolling metrics (window=30)
        │   └── Compute: w1*sharpe + w2*winrate - w3*drawdown - w4*stagnation
        │
        └── WebSocket → browser (live dashboard update)
```

---

## 6. Strategy Lifecycle Flow

```
SeedLab                      Backtest/Optuna              Strategy Version
[generate seeds]             [optimize params]            [create version JSON]
  │                            │                            │
  ├── template seeds (10)      ├── Gen 1: baseline          ├── strategy_versions/
  ├── combinatorial            ├── Gen 2+: Optuna TPE       │   {SYMBOL}_v{N}.json
  │                            │   30 trials/gen             │
  ├── Regime detection         ├── 80% train / 20% val      ├── Params + metrics
  ├── Multi-regime backtest    ├── Overfit: gap > 0.30       ├── Tool config
  ├── Stability analysis       ├── Early stop: 2 gens       ├── AI model config
  ├── Strategy card build      │   no improvement           │
  └── Registry save            └── Checkpoint save/load     └── DB registration
                                                              │
                                                              v
  Deployment Pipeline (backtester/deployment_pipeline.py)
  ├── retired     — auto-assigned at creation if result fails candidate→dry_run gate
  ├── candidate   — newly created (passed quality gate)
  ├── dry_run     — gate: 40+ trades, Sharpe ≥ 1.0, WR ≥ 42%, DD ≤ 25%
  ├── demo        — gate: 50+ trades, Sharpe ≥ 0.5, WR ≥ 42%, DD ≤ 20%, 3 cycles
  └── live        — gate: 100+ trades, Sharpe ≥ 0.7, WR ≥ 45%, DD ≤ 15%, 5 cycles
```

---

## 7. Event Flow (WebSocket + HTTP Bridge)

```
EventBus.publish(event)
        │
        ├─── [web server process] websocket.py subscriber
        │    ├── Serialize event to JSON
        │    ├── Broadcast to all connected WS clients
        │    └── Record to in-memory ring buffer (event_log.py)
        │
        └─── [subprocess agents] main.py _bridge_event subscriber
             ├── Serialize event + tag with instance_id
             ├── POST to http://localhost:8090/api/events/ingest (sync, <50ms)
             └── Fire-and-forget (failures silently ignored)
        │
        v
  Browser SPA
  ├── Dashboard: refresh stats on TradeOpened/TradeClosed
  ├── Backtests: update progress on SeedLabProgress
  ├── Strategies: flash on StrategyPromoted
  ├── Alpha Agents: Raw Signal Log modal (GET /api/events?instance_id=...)
  └── Toast notifications for RiskLimitHit

EventBus.publish() traverses __mro__ so subscribing to Event base class
catches all subclasses (PipelineBlocked, SignalGenerated, etc.)
```
