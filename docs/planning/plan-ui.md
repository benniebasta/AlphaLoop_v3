# AlphaLoop v3 — UI Architecture

## Stack
- Vanilla JS SPA (no framework), ES modules
- Hash-based routing (`#dashboard`, `#trades`, etc.)
- FastAPI static file serving
- WebSocket real-time event stream
- Dark terminal CSS theme (CSS variables)

## File Structure
```
src/alphaloop/webui/
├── app.py                  (148 lines)  FastAPI factory, health + /health/detailed endpoints, watchdog startup, static mount, 11 router includes
├── auth.py                 (69 lines)   BearerAuthMiddleware — validates Authorization header, safe methods pass
├── deps.py                 (48 lines)   DI helpers: get_container(), get_config(), get_db_session(), _get_session_factory() for background tasks
├── routes/
│   ├── dashboard.py        (69 lines)   GET /api/dashboard → open_trades, daily/weekly/total pnl, win rates
│   ├── trades.py           (87 lines)   GET /api/trades(?status&symbol&limit), GET /{id}, GET /stats/summary
│   ├── bots.py             (84 lines)   GET /api/bots, POST (register), DELETE /{instance_id}
│   ├── backtests.py        (210 lines)  GET /symbols (116-asset yfinance catalog), GET, GET/{id}, POST (create+start), PATCH stop/resume, DELETE, GET/{id}/logs
│   ├── tools.py            (81 lines)   GET /api/tools(?limit) → decisions + rejection_counts, GET /rejections
│   ├── ai_hub.py           (70 lines)   GET /api/ai-hub → models + providers + api_keys_configured, PUT
│   ├── research.py         (70 lines)   GET /api/research(?symbol&limit), GET /evolution(?event_type)
│   ├── settings.py         (38 lines)   GET /api/settings → {settings: {}}, PUT
│   ├── seedlab.py          (64 lines)   GET /api/seedlab(?limit), POST (queue discovery run)
│   ├── strategies.py       (340 lines)  GET /api/strategies, evaluate, promote, activate, delete, canary start/end
│   ├── test_connections.py (150 lines)  POST /api/test/{mt5,telegram,ai,ai-key}, GET /api/test/models
│   └── websocket.py        (93 lines)   WS /ws — broadcasts events, ping/pong, token auth
└── static/
    ├── index.html           (62 lines)  SPA shell: sidebar nav (9 items incl. Strategies), #page-content, status bar, cache-bust versioned JS
    ├── css/app.css          (~1100 lines) Dark theme, cards, tables, badges, **symbol picker dropdown**, strategy lifecycle pipeline, all page styles
    └── js/
        ├── app.js           (148 lines) Router (9 routes), cache-bust version (`_V`), WebSocket auto-reconnect, showToast()
        ├── api.js           (85 lines)  setAuthToken, getAuthToken, apiFetch, apiGet/Post/Put/Patch/Delete
        ├── sounds.js        — Web Audio API synthesizer (no audio files). Five named sounds: playTradeOpened (E5→G#5), playTradeClosedProfit (E5→G5→C6), playTradeClosedLoss (G4→E4→C4), playSeedLabDone (C5→E5→G5→C6 fanfare), playEvolution (C5→E5→G5→B5→C6 arpeggio). AudioContext created lazily on first user gesture. All preferences stored in localStorage. Exports: isGloballyEnabled(), getVolume(), isEventEnabled(key), setSoundsEnabled(), setVolume(), setEventEnabled(key, val).
        └── components/
            ├── live.js       — **Live Trading Monitor** (`#live`). Symbol pills (XAUUSD/BTCUSD/EURUSD/GBPUSD/NAS100/US30) + custom input. Price header (symbol, $price, 24H change, day range, session badge). **Candlestick chart** (Lightweight Charts v4.2, EMA-9/21 overlay, RSI sub-pane, 1m–1w timeframe switcher). **Signal Intelligence card**: BUY/SELL/SCANNING badge; when bot sends `SignalGenerated` event — shows direction + confidence gauge (SVG arc 0–100%) locked for 5 min; when no bot signal — badge shows SCANNING and gauge shows **BUY/SELL/NEUTRAL EMA bias** (derived from EMA9/EMA21 gap direction, color-coded green/red/amber, label "EMA BIAS"). Market regime (▲ Trending Up / ▼ Trending Down / ↔ Ranging), last-signal time, recent EMA crossover pills (last 5 of trailing 50 bars). **Agent Status** row — updated live from `CycleCompleted` events. **Live Thoughts panel** — real-time pipeline narration from `PipelineStep`/`CycleStarted`/`CycleCompleted` WebSocket events; stage icons (🔄🛡🔍📡✅🏰📐⚡) + color-coded status badges; max 20 entries newest-first; "Waiting for bot events..." placeholder. 24H session timeline (Asia/London/Overlap/NY/Off with UTC marker). Info cards: Next News Event, Session Clock, Volatility Regime (CALM/NORMAL/ELEVATED/EXTREME + ATR%). Polls `GET /api/live` every 5s + WebSocket real-time push. Signal data computed server-side from yfinance OHLC via EMA-9/21/50 + RSI-14; `ema_state` always returned for bias display.
            ├── dashboard.js  — 6 stat cards (icons, colors, pulse dot, live indicator)
            ├── trades.js     — filter buttons (All/Open/Closed), data table, outcome badges, pnl coloring
            ├── bots.js       — **Deploy modal**: strategy card picker (fetches per-symbol excluding retired only; candidates shown greyed-out/disabled with "— promote to deploy" label; dry_run/demo/live selectable; empty state = "No strategies found…"), risk budget slider (25-100%, auto-suggests 50% for same-symbol), mode dropdown. **Agent cards**: card identity block (name, version, signal_mode pill, status badge, WR/Sharpe/DD/P&L metrics), same-symbol badge (`2x XAUUSD`), collapsible **Loop Status panel** (pipeline steps, signal, validation, risk — live via WebSocket), evolution flash badge (StrategyPromoted/RolledBack). **Raw Signal Log modal** (redesigned): two-section layout — Pipeline Status Grid (9 stage cards always rendered, dimmed until event arrives) + Event Stream (chronological list); auto-polls `GET /api/events` every 3s while open (`🟢 live` indicator); uses `apiGet()` for auth. **Sound triggers**: TradeOpened → playTradeOpened(), TradeClosed → profit/loss sound by pnl_usd sign, StrategyPromoted → playEvolution(). Auto-refresh 30s + WS live updates.
            ├── backtests.js  — new backtest form (**searchable symbol dropdown** with 116 yfinance assets across 13 groups, days, balance, gens, timeframe, 13 tool toggles), run cards (state icon, sharpe, WR, progress bar, live log streaming). **Sound trigger**: state transition to `'completed'` → `playSeedLabDone()`.
            ├── tools.js      — pipeline summary bar (allowed/blocked/total, pass rate %), filter cards (6 filters w/ icons & bars), decisions table
            ├── ai_hub.js     — provider grid (6 cards: icon, name, active badge, key status), model roles (text inputs + toggles)
            ├── research.js   — two tables: reports (date, symbol, WR, sharpe, pnl) + evolution events (type badge, details)
            ├── strategies.js — strategy version lifecycle page: filter by symbol (dynamic from catalog API) + status, lifecycle pipeline visualization (Candidate → Dry Run → Demo → Live with counts), version cards with metrics (Sharpe, WR, DD, P&L), **backtest context row** (📊 timeframe, 📅 days, 💰 initial capital — shown when fields present), **tool badges** (read-only pills), **dry-run overlay panel** (per-card tool checkboxes), Promote/Activate buttons
            └── settings.js   — 10-tab sidebar (API Keys, Web UI, Broker/MT5, Risk, Signal, Session, Telegram, Tools, System, **🔊 Sounds** — AI Models moved to AI Hub), toggle switches, show/hide passwords, status badges, field descriptions. System tab includes MetaLoop/AutoLearn, Health Monitor, Confidence Sizing & Micro-Learning sections. **Sounds tab** (`localOnly: true` — skips server API, no Save button): Master Controls (global toggle + volume slider) + Event Sounds (5 rows with icon, description, sound notation, ▶ Preview, toggle). All preferences stored in localStorage instantly.
```

## API Endpoints (All Existing)

| Method | Path | Response |
|--------|------|----------|
| GET | `/health` | `{status, version}` |
| GET | `/health/detailed` | `{status, version, components: {name: {status, details, last_check}}, watchdog: {...}}` |
| GET | `/api/dashboard` | `{open_trades, daily_pnl, daily_trades, daily_win_rate, weekly_pnl, total_pnl, total_trades, overall_win_rate}` |
| GET | `/api/trades` | `{trades: [{id, symbol, direction, setup_type, entry_price, lot_size, outcome, pnl_usd, opened_at, ...}]}` |
| GET | `/api/trades/{id}` | Single trade dict or 404 |
| GET | `/api/trades/stats/summary` | `{counts: {WIN: n, LOSS: n, ...}}` |
| GET | `/api/bots` | `{bots: [{id, symbol, instance_id, pid, started_at, strategy_version, strategy: {name, version, signal_mode, status, metrics}}]}` |
| POST | `/api/bots` | Body: `{symbol, instance_id, pid, strategy_version}` — manual register |
| POST | `/api/bots/start` | Body: `{symbol, dry_run, strategy_version, risk_budget_pct}` — deploy subprocess with strategy binding |
| POST | `/api/bots/{instance_id}/stop` | `{status: ok, instance_id, signal_sent}` — stop agent + delete record |
| DELETE | `/api/bots/{instance_id}` | `{status: ok, removed: id}` |
| GET | `/api/backtests/symbols` | `{symbols: [{symbol, name, yf_ticker, group}], groups: [...]}` — **116 yfinance assets, 13 groups** |
| GET | `/api/backtests` | `{backtests: [{run_id, symbol, name, state, generation, max_generations, best_sharpe, best_wr, ...}]}` |
| GET | `/api/backtests/{run_id}` | Single backtest or 404 |
| POST | `/api/backtests` | Body: `{symbol, name, days, balance, max_generations, timeframe, use_*_filter/guard}` — creates AND starts engine |
| PATCH | `/api/backtests/{run_id}/stop` | Request running backtest to stop |
| PATCH | `/api/backtests/{run_id}/resume` | Resume a paused backtest |
| DELETE | `/api/backtests/{run_id}` | Delete backtest (stops if running) |
| GET | `/api/backtests/{run_id}/logs?offset=N` | `{run_id, offset, lines: [...], total}` — live log stream |
| GET | `/api/tools` | `{decisions: [...], rejection_counts: {filter_name: count}}` |
| GET | `/api/tools/rejections` | `{rejections: [{symbol, direction, setup_type, rejected_by, reason}]}` |
| GET | `/api/ai-hub` | `{models: {role: value}, providers: [...], api_keys_configured: {provider: bool}}` |
| PUT | `/api/ai-hub` | Body: `{settings: {role: value}}` |
| GET | `/api/research` | `{reports: [{symbol, strategy_version, win_rate, sharpe_ratio, total_pnl_usd, ...}]}` |
| GET | `/api/research/evolution` | `{events: [{event_type, symbol, details, params_before, params_after}]}` |
| GET | `/api/settings` | `{settings: {key: value, ...}}` |
| PUT | `/api/settings` | Body: `{settings: {key: value}}` |
| GET | `/api/seedlab` | `{runs: [{run_id, name, symbol, status, is_running, created_at}]}` |
| POST | `/api/seedlab` | Body: `{name, symbol, days, balance}` — creates AND starts background task |
| GET | `/api/seedlab/{run_id}/logs?offset=N` | `{run_id, offset, lines, total, is_running}` — live log stream |
| PATCH | `/api/seedlab/{run_id}/stop` | Request running SeedLab run to stop |
| DELETE | `/api/seedlab/{run_id}` | Delete SeedLab run (stops if running) |
| GET | `/api/strategies` | `{strategies: [{symbol, version, status, params, summary}], total}` |
| GET | `/api/strategies/{symbol}/v{ver}` | Full strategy version JSON |
| POST | `/api/strategies/{symbol}/v{ver}/evaluate` | `{eligible, target_status, reasons}` |
| POST | `/api/strategies/{symbol}/v{ver}/promote` | `{promoted, new_status, reasons}` |
| POST | `/api/strategies/{symbol}/v{ver}/activate` | `{status, activated, strategy_status}` |
| GET | `/api/test/models` | `{models: [{id, provider, display_name}]}` — 21 built-in AI models for dropdown population |
| POST | `/api/test/mt5` | `{success, message}` — tests MT5 connection with stored credentials, returns balance + leverage |
| POST | `/api/test/telegram` | `{success, message}` — sends test message to configured chat ID |
| POST | `/api/test/ai` | `{success, message}` — pings configured signal model with a test prompt |
| POST | `/api/test/ai-key` | `{success, message}` — body: `{provider, model}` — tests a specific provider API key |
| POST | `/api/test/ollama` | `{success, message}` — checks local Ollama endpoint, returns available model count + names |
| POST | `/api/strategies/{symbol}/v{ver}/canary/start` | `{canary_id, symbol, allocation_pct, status, start_time, end_time}` |
| POST | `/api/strategies/{symbol}/v{ver}/canary/end` | `{canary_id, recommendation, metrics, reasons}` |
| PUT | `/api/strategies/{symbol}/v{version}/models` | `{status, ai_models}` — body: `{signal, validator, research, autolearn}` — updates per-strategy model assignments |
| DELETE | `/api/strategies/{symbol}/v{version}` | `{status, deleted}` — removes strategy version JSON file |
| GET | `/api/live` | `{symbol, timeframe, price, change_pct, day_high, day_low, ohlc:[{time,o,h,l,c}], session, signal:{direction,confidence,rsi,ema9,ema21,source,timestamp}\|null, ema_state:{ema9,ema21,ema50,rsi,gap_pct,regime}, market_regime, recent_signals:[{direction,time,price}], volatility:{regime,atr_value,atr_pct}, bot_running, recent_trades, timestamp}` — `signal` is null when no crossover detected; `ema_state` always returned for bias display |
| GET | `/api/live/symbols` | `{symbols: [{symbol, bot_running, price, change_pct}]}` |
| GET | `/api/live/sessions` | `{sessions, current_time_utc, current_hour}` |
| WS | `/ws` | Event stream: `{type, timestamp, ...fields}` |

## WebSocket Events (Defined in core/events.py — 18 event types)
- `CycleStarted` — symbol, instance_id, cycle
- `CycleCompleted` — symbol, instance_id, cycle, outcome, detail
- `PipelineStep` — symbol, instance_id, cycle, stage, status, detail *(risk_check/filters/signal_gen/validation/guards/sizing/execution)*
- `SignalGenerated` — symbol, direction, confidence, setup_type
- `SignalValidated` — symbol, direction, status, risk_score
- `SignalRejected` — symbol, direction, reason, rejected_by
- `TradeOpened` — symbol, direction, entry_price, lot_size, trade_id
- `TradeClosed` — symbol, outcome, pnl_usd, trade_id
- `TradeRepositioned` — symbol, instance_id, trade_id, trigger, action, reason
- `PipelineBlocked` — symbol, blocked_by, reason
- `RiskLimitHit` — limit_type, details
- `ResearchCompleted` — symbol, report_id
- `ConfigChanged` — keys changed list, source
- `StrategyPromoted` — symbol, version, from_status, to_status
- `SeedLabProgress` — run_id, phase, current, total, message
- `CanaryStarted` — symbol, canary_id, allocation_pct, duration_hours
- `CanaryEnded` — symbol, canary_id, recommendation
- `MetaLoopCompleted` — symbol, action_taken, new_version

## Pages — Current Implementation

### Dashboard (`#dashboard`)
- 6 icon stat cards in responsive grid (📈 Open Trades, 💰 Daily P&L, 🎯 Daily WR, 📅 Weekly P&L, 🏦 Total P&L, 🏆 Overall WR)
- Color-coded values (green=positive, red=negative, muted=zero)
- Live pulse indicator, last updated timestamp
- Auto-refreshes on TradeOpened/TradeClosed WebSocket events

### Trades (`#trades`)
- Filter buttons: All, Open, Closed (highlighted active)
- Data table: ID, Symbol, Direction, Setup, Entry, Lots, Outcome, P&L, Opened
- Outcome badges: WIN=green, LOSS=red, BE=amber, OPEN=blue
- Loads up to 200 trades

### Alpha Agents (`#agents`)
- Card grid per running instance
- **Header:** Strategy name (stripped `_v1` suffix) + version badge (V1 pill) + Active badge. Falls back to symbol if no strategy bound.
- **Stats:** Uptime counter (starts 0:00 on page load, ticks every second via setInterval) + PID
- **Identity block:** Signal mode pill (ALGO ONLY / ALGO+AI) + status badge + WR/Sharpe/P&L/DD metrics
- **Raw Signal Log** button: opens per-instance modal with Pipeline Status Grid (9 stage cards, always rendered, dimmed until event arrives) + Event Stream (chronological list). Auto-polls `GET /api/events?instance_id=...` every 3s while open (`🟢 live` indicator). Uses `apiGet()` with auth. Click outside or ✕ to close, polling stops.
- **Loop Status** section: hidden until first WS event, then collapsible with pipeline/signal/validation/risk data
- **Deploy modal:** Symbol select + Mode (Dry/Live) + Risk Budget slider + Strategy Card picker (radio buttons with signal mode pills and metrics)
- Auto-refresh every 30s, stop/remove confirmation
- Empty state: robot emoji + deploy prompt

### Backtests (`#backtests`)
- **New Backtest form:**
  - **Symbol picker** (searchable dropdown, replaces old text input):
    - Loads 116 yfinance assets from `GET /api/backtests/symbols` on page mount
    - Grouped by 13 asset classes: Metals, Crypto, Forex Majors, Forex Crosses, Indices, Index Futures, Energy, Agriculture, US Mega-Cap Stocks, US Tech Stocks, Popular ETFs, Volatility, Bonds
    - Default: XAUUSD — Gold Futures (shown as selected chip)
    - Click selected chip → opens search input + dropdown
    - Type to filter by symbol, name, yfinance ticker, or category name
    - Click option → selects symbol, hides search input, shows selected chip
    - Press Enter → accepts custom symbol (for assets not in catalog)
    - Click outside → closes dropdown, restores selected chip
    - Sticky group headers in dropdown for easy scanning
    - Solid dark background (`--bg`) on dropdown, no transparency
  - **Timeframe** (dropdown: M1/M5/**M15 (default)**/M30/H1/D1/W1/MN), History days (default **365**, max 730), Starting balance ($10,000), Generations (default 3), Start button
  - **Data source**: MT5 primary (no day limits), yfinance fallback (auto-capped). Frontend allows full 730d range; shows amber warning hint when yfinance fallback would cap (e.g. "yfinance fallback: max 60d for 15m")
  - **Signal Tools** collapsible section (13 backtest-compatible tools as checkboxes):
    - Default ON: Session Filter, Volatility Filter, EMA200 Trend Filter
    - Default OFF: BOS Structure Guard, FVG Structure Guard, Tick Jump Guard, Liquidity Vacuum Guard, VWAP Guard, MACD Filter, Bollinger Filter, ADX Filter, Volume Filter, Swing Structure
    - Each shows name + short description
    - NOT backtest-compatible (excluded): News Filter, DXY Filter, Sentiment Filter, Risk Filter, Correlation Guard, Portfolio Cap, all stateful guards, Repositioner
- Live card per run showing: state icon, name/run_id, metadata line (`symbol · timeframe · days · balance`), Sharpe, Win Rate, Best PnL, Generations, progress bar with %, status message
- **Live Output** panel: expandable black terminal log (green text, `Fira Code` mono), auto-polls every 2s, auto-scrolls
- **Actions**: Stop (red, for running), Resume (green, for paused), Delete (red ✕, for completed/failed/pending)
- Auto-expands log for running backtests, auto-refreshes every 3s with **real-time stat updates**:
  - Sharpe, Win Rate, Best PnL, Gens counter all update live during backtest execution
  - Progress bar width + label update in sync with generation progress
  - Message text updates (phase, baseline results, optimization progress)
  - State transition detection triggers full card re-render (running→completed updates icon, badge, buttons)
- State colors: pending=amber, running=blue (glowing border), completed=green, failed=red, paused=amber
- **Auto-generated creative names**: Each backtest gets a unique name like `cosmic-falcon-XAUUSD_v1`. Always `_v1` — versioning (v2, v3...) only happens at the strategy card level through auto-learn and mutation.
- Backend: POST creates DB record AND spawns `asyncio.Task` via `backtester/runner.py`
- **Engine**: `BacktestEngine.run()` with tunable signal function + tool filters, yfinance OHLCV data, bar-by-bar SL/TP simulation
- **Optimization loop** (ported from v1):
  - Gen 1: Baseline with default params (EMA 21/55, SL=2.0 ATR, TP1=2.0 RR, TP2=4.0 RR, RSI 30-70)
  - Gen 2+: Optuna TPE (30 trials) mutates 7 params on 80% train split → validates on 20% holdout
  - Overfitting detection: rejects if train-val Sharpe gap > 0.30
  - Early stop: 2 consecutive gens without improvement
  - Log shows: trial results, accepted/rejected params, overfit warnings
- Signal function applies enabled tools: volatility_filter (ATR% range), ema200_filter (trend direction), bos_guard (swing break), fvg_guard (gap entry), tick_jump_guard (spike reject), liq_vacuum_guard (thin body), vwap_guard (extension check)
- Logs buffered in-memory (`runner._logs[run_id]`), max 500 lines, streamed via `GET /api/backtests/{id}/logs?offset=N`

### Tools & Pipeline (`#tools`)
- Pipeline summary bar: Allowed (green) / Blocked (red) / Total counts, pass rate progress bar with percentage
- 6 filter rejection cards: Risk (⚖️), Volatility (📊), Sentiment (🌐), Session (🕐), News (📰), DXY (💵) — each with mini bar graph + count
- Pipeline decisions data table: Time, Symbol, Direction badge, Decision badge, Blocked By, Reason, Size Modifier

### AI Hub (`#ai_hub`) — Centralized AI Model Configuration
- **Section A: Provider Connections** — 7 provider cards (Gemini, Claude, OpenAI, DeepSeek, xAI, Qwen, Ollama) showing API key status badge + "Test" button each
- **Section B: Model Catalog** — scrollable table of all 21 built-in models: provider (color-coded), model ID (monospace), display name
- **Section C: Default Role Assignments** — 4 dropdowns (all 21 models): Signal Model, Validator Model, Research Model, Autolearn Model. Global defaults — each strategy card can override individually. "Save Defaults" button.

### Research (`#research`)
- Reports table: Date, Symbol, Trades, Win Rate, Avg RR, P&L, Sharpe, Max DD
- Evolution events table: Time, Symbol, Type badge, Version, Details
- Basic implementation, no rich styling yet

### Strategies (`#strategies`)
- **Symbol tabs**: `All` + one tab per symbol found in data (e.g. `All | BTCUSD | XAUUSD`). Click to filter. Active tab has blue underline. Tabs auto-populate from strategy data.
- **Status filter + lifecycle dots**: Inline status dropdown + colored dot counters (amber/blue/purple/green) with arrow flow
- **Card grid** (`grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))`):
  - Each card: colored top border by status, header (creative name + badge), 5-metric grid (Trades, Win Rate, Sharpe, P&L, Max DD), param summary, **tool badges row** (read-only pill badges showing which tools were baked in — e.g. `[Session] [Volatility] [EMA200]`, locked with `pointer-events: none`), **signal mode toggle** (2 pills: `Algo Only` / `Algo + AI`, saves immediately via `PUT /api/strategies/{sym}/v{ver}/models { signal_mode }`, clicking Algo Only hides AI Models row), **expandable AI Models panel** (4 dropdowns: Signal, Validator, Research, Autolearn — each can be "Use Default" or specific model from catalog, saved via `PUT /api/strategies/{symbol}/v{version}/models`), action buttons
  - Cards lift on hover (`translateY(-2px)` + shadow)
  - Actions: **Promote** (blue), **Activate** (green, for dry_run+), **Delete ✕** (red, with confirmation)
  - `DELETE /api/strategies/{symbol}/v{version}` removes the JSON file
- Empty state with guidance: "Run a backtest to auto-create strategy versions"

### Settings (`#settings`)
- 10-tab sidebar with icons: 🔑 API Keys, 🔒 Web UI, 📡 Broker/MT5, ⚖️ Risk, 📊 Signal, 🕐 Session, ✈️ Telegram, 🛠️ Tools, ⚙️ System, 🔊 Sounds (AI Models tab removed — moved to AI Hub page)
- **Connection test buttons** on 3 tabs (below section fields, with inline result display):
  - **API Keys**: Each provider (Gemini, Claude, OpenAI, DeepSeek, xAI, Qwen) has "🔑 Test Key" → `POST /api/test/ai-key {provider, model}` → pings specific provider
  - **Broker/MT5**: "🔌 Test MT5 Connection" → `POST /api/test/mt5` → shows server, balance, leverage
  - **Telegram**: "📨 Send Test Message" → `POST /api/test/telegram` → sends message to chat ID
  - **AI Models — Signal Engine**: "🤖 Test AI Connection" → `POST /api/test/ai` → pings configured signal model
  - **AI Models — Claude**: "🤖 Test Claude" → `POST /api/test/ai-key {anthropic}` → pings Claude validator
  - **AI Models — Qwen Cloud**: "🤖 Test Qwen API" → `POST /api/test/ai-key {qwen}` → pings Qwen API
  - **AI Models — Ollama Local**: "🖥️ Test Ollama" → `POST /api/test/ollama` → checks local Ollama endpoint, lists models
- **AI model dropdowns**: All 7 model selection fields use `model_select` type → populated from `GET /api/test/models` (21 built-in models). Filtered by provider where applicable (e.g. Gemini Model only shows Gemini models, Claude Model only shows Anthropic). Custom/unknown values preserved as-is.
- Green dot indicators on tabs with configured values
- Field types: password (with 👁️ show/hide), text, number, toggle switch, select dropdown
- Status badges: "✓ Set" (green) / "✗ Not set" (red) for sensitive fields
- Field descriptions below each input with default values shown
- Signal tab: Core Thresholds, Validation Guards (H1 trend, RSI, news, setup), Entry Parameters (SL/TP ATR mults), Circuit Breaker — 25 params
- Tools tab defaults seeded into DB on startup via `SettingsService.seed_defaults(SETTING_DEFAULTS)` in `lifecycle.py` — 43 keys covering all 5 sections: Pipeline Filters (session/news/volatility/dxy/sentiment/risk toggles + params), Validation Rule Guards (EMA200, BOS, FVG, tick jump, liq vacuum, VWAP + their ATR params), Stateful Guards (hash dedup, conf variance, spread regime, equity curve, DD pause, portfolio cap, correlation, near-dedup), Position Management (repositioner + 4 trigger toggles + multipliers), Mode-Specific Overrides (risk filter per mode). `seed_defaults` fills absent keys AND empty-string entries so blank fields get populated without overwriting real user values.
- **Tools tab** (expanded — 5 sections, 50+ fields):
  - **1. Pipeline Filters (toggleable):** Session Filter + min score, News Filter + pre/post windows, Volatility Filter + max/min ATR%, DXY Filter, Sentiment Filter, Risk Filter
  - **2. Validation Rule Guards (toggleable per-strategy):** EMA200 Trend Filter, BOS Structure Guard + min break ATR + lookback, FVG Structure Guard + min size ATR + lookback, Tick Jump Guard + max ATR, Liquidity Vacuum Guard + spike mult + body %, VWAP Guard + max extension ATR, MACD Filter + fast/slow/signal periods, Bollinger Filter + period/std dev, ADX Filter + period/threshold, Volume Filter + MA period, Swing Structure toggle
  - **3. Stateful Guards (always-on system protection):** Signal Hash Dedup (window), Confidence Variance (window + max stdev), Spread Regime (window + threshold), Equity Curve Scaler (window + scale factor), Drawdown Pause (duration + lookback), Portfolio Risk Cap, Correlation Guard (block/reduce thresholds), Near-Position Dedup (ATR distance)
  - **4. Position Management (live trades):** Trade Repositioner master toggle, Close on Opposite Signal, News Risk SL Tighten + window, Volume Spike Trail SL + multiplier, Volatility Spike Trail SL + multiplier
  - **5. Mode-Specific Overrides:** Risk Filter per mode (Dry Run, Backtest, Live)
- **System tab** (expanded — 4 sections):
  - **Runtime:** Dry Run Mode, Log Level, Environment
  - **MetaLoop / AutoLearn:** Enabled toggle, Check Interval (trades), Rollback Window (trades), Auto-Activate toggle, Degradation Threshold
  - **Health Monitor:** Weight Sharpe/WinRate/Drawdown/Stagnation, Healthy Threshold, Critical Threshold
  - **Confidence Sizing & Micro-Learning:** Confidence Sizing toggle, Micro-Learning toggle, Max Nudge Per Trade, Max Total Drift
  - **Database:** Database URL
- **Sounds tab** (`localOnly: true` — no server API call, "Save Changes" footer hidden):
  - **Master Controls:** global Sound Effects toggle, Volume slider (0–100%)
  - **Event Sounds:** 5 rows — Trade Opened, Trade Closed—Profit, Trade Closed—Loss, SeedLab Complete, Strategy Evolution. Each row: icon, name, description, sound notation, ▶ Preview button (force-plays regardless of toggle state), On/Off toggle
  - All preferences persist instantly to `localStorage`; no save needed
- 110+ settings fields total

## CSS Theme Variables
```css
--bg:     #0b0e17    --bg2:    #111827    --bg3:    #1a2035    --bg4:    #243049
--border: rgba(255,255,255,0.08)    --text:   #e2e8f0    --muted:  #64748b
--green:  #22c55e    --red:    #ef4444    --amber:  #f59e0b    --blue:   #3b82f6
--purple: #8b5cf6    --teal:   #14b8a6
```

## UI Patterns & Lessons Learned

### Auto-Refresh with State Transitions
**Problem:** Partial DOM updates (only updating badge text, progress bar) cause stale UI when backend state changes (running → completed). Stats, icons, and action buttons don't update, making the UI appear frozen.

**Solution:** Track known states per entity. On each poll cycle, compare previous state to current. If state changed, trigger a full `load()` re-render instead of partial patching.

```js
const _knownStates = {};
setInterval(async () => {
  const data = await apiGet('/api/resource');
  let needsFullReload = false;
  for (const item of data.items) {
    const prev = _knownStates[item.id];
    _knownStates[item.id] = item.state;
    if (prev && prev !== item.state) { needsFullReload = true; }
  }
  if (needsFullReload) load(); // full re-render
  else { /* partial DOM patches: stats, message, progress bar */ }
}, 3000);
```

**Rule:** Any page with stateful entities (backtests, bots, trades) that transition through lifecycle states MUST use state-change detection + full reload, not just partial patching.

### Background Task Log Streaming
**Pattern:** For long-running backend tasks (backtests, SeedLab runs):
1. Backend buffers logs in-memory per `run_id` (`dict[str, list[str]]`, max 500 lines)
2. Frontend polls `GET /api/{resource}/{id}/logs?offset=N` every 2s
3. Appends new lines to a `<pre>` element, auto-scrolls to bottom
4. Starts polling when log panel is expanded, stops when collapsed or page changes
5. Cleanup: `stopAllPolls()` on `route-change` event, `clearInterval` on page leave

**Rule:** Always stop all poll timers when navigating away. Use `window.addEventListener('route-change', cleanup, { once: true })`.

### Action Buttons Must Match State
**Problem:** Showing "Stop" on a completed backtest, or "Delete" on a running one. Worse: after server restart, DB state stays "running" but the asyncio task is gone — card shows Stop but no Delete, and Stop returns "Not running".

**Solution:** Use `is_running` (actual task alive) NOT `state` (DB, can be stale) for button logic:
```js
const active = r.is_running;                              // actual task alive
const staleRunning = r.state === 'running' && !r.is_running; // task lost on restart
const canStop = active;
const canResume = paused || staleRunning;                  // allow resume of orphaned "running"
const canDelete = !active;                                 // always allow if task not alive
```

**Backend fix:** When PATCH `/stop` is called on a non-running task with DB state="running", auto-fix to "paused":
```python
if run.state == "running":
    run.state = "paused"
    run.message = "Stopped (task lost on server restart)"
```

**Rule:** Never trust DB `state` alone for liveness — always cross-check with `is_running` (in-memory task dict). DB state can be orphaned if the server restarts while a backtest is running.

### Backend Task Lifecycle
**Pattern for any async background task (backtests, SeedLab, etc.):**
1. `POST` creates DB record (state="pending") AND spawns `asyncio.Task`
2. Task updates DB state: pending → running → completed/failed/paused
3. Stop: set a flag in `_stop_flags[run_id]`, task checks flag each iteration
4. Delete: cancel task + remove DB record + clear logs
5. Resume: spawn new task from where it left off

**Required backend infrastructure:**
- `_tasks: dict[str, asyncio.Task]` — track running tasks
- `_stop_flags: dict[str, bool]` — graceful stop signals
- `_logs: dict[str, list[str]]` — log buffers
- `_get_session_factory()` helper in `deps.py` for background tasks outside request context

### CPU-Bound Tasks Must Not Block the Event Loop
**Problem:** Optuna optimization (30 sync trials, each running an async backtest) blocked the uvicorn event loop. While backtests ran, the entire server froze — no HTTP responses, WebSocket disconnected, Settings showed "Loading..." forever.

**Root cause:** `run_coroutine_threadsafe()` scheduled async backtests on the main loop from an Optuna thread. Each trial waited via `future.result()`, creating backpressure that starved all other coroutines (HTTP handlers, WebSocket pings).

**Solution:** Run the entire Optuna loop + backtests in a thread pool via `asyncio.to_thread()`. Each Optuna trial uses `asyncio.run()` to create a fresh event loop in the thread — no interaction with the main uvicorn loop.

```python
def run_on_train(params):
    # Fresh event loop per trial — does NOT touch the main loop
    r = asyncio.run(engine.run(...))
    return r.sharpe or -999.0

# Run entire optimizer in thread pool
opt_params, sharpe, stopped = await asyncio.to_thread(optimize, ...)
```

**Rule:** Any CPU-bound work (backtests, Optuna, heavy computation) MUST use `asyncio.to_thread()` with `asyncio.run()` inside. NEVER use `run_coroutine_threadsafe()` for blocking workloads — it starves the event loop.

**Applied to all engine.run() calls:** Baseline, validation, and full-data runs all use `_run_engine_in_thread()` which wraps `asyncio.to_thread(lambda: asyncio.run(engine.run(...)))`. The engine instance is created without `session_factory` (no DB in thread) — the runner handles all DB updates on the main loop. This keeps the server responsive even with 35K+ bar backtests (M15/365d via MT5).

### Navigation Timer Leak Fix
**Problem:** `bots.js` (30s auto-refresh) and `backtests.js` (3s auto-refresh + 2s log polls) registered `setInterval` timers that kept firing after navigating away. The DOM elements they targeted no longer existed, causing 50+ `TypeError: Cannot set properties of null` errors per second, eventually freezing the UI.

**Root cause:** `app.js:navigateTo()` never dispatched the `route-change` event that cleanup listeners depended on.

**Solution (two layers):**
1. `app.js` now dispatches `window.dispatchEvent(new CustomEvent('route-change'))` BEFORE replacing the page container
2. All `load()` functions start with `if (!el) return;` null guard as safety net

**Rule:** Every component with `setInterval` or polling MUST:
- Register cleanup on `route-change`: `window.addEventListener('route-change', () => { clearInterval(timer); stopAllPolls(); }, { once: true });`
- Add null guards on DOM element access in async callbacks

### deps.py — Background Task Access
**Problem:** Background `asyncio.Task`s need DB session factories but don't have a `Request` object.

**Solution:** Store app reference in `deps.py`:
```python
_app_ref = None  # Set in app.py after creating FastAPI app

def _get_session_factory():
    if _app_ref and hasattr(_app_ref.state, "container"):
        return _app_ref.state.container.db_session_factory
    return None
```

Wire in `app.py`: `deps._app_ref = app`

### API Client — PATCH Method
Added `apiPatch(url, data)` to `api.js` for state-changing operations (stop, resume) that aren't full replacements (PUT) or creations (POST).

## Auth Flow
1. `BearerAuthMiddleware` checks `Authorization: Bearer <token>` header
2. Token resolved from `AUTH_TOKEN` env var or `WEBUI_TOKEN` in settings
3. GET/OPTIONS/HEAD always pass (safe methods)
4. WebSocket auth via `?token=` query param
5. Dev mode: no token configured = all requests allowed
