/**
 * Settings — fully redesigned with categories, friendly labels, toggles & status badges.
 */
import { apiGet, apiPost, apiPut, setAuthToken } from '../api.js';
import {
  isGloballyEnabled, getVolume, isEventEnabled,
  setSoundsEnabled, setVolume, setEventEnabled,
  playTradeOpened, playTradeClosedProfit, playTradeClosedLoss, playSeedLabDone, playEvolution,
} from '../sounds.js';

/* ── Schema ─────────────────────────────────────────────────────────────────
   Each tab → sections → fields.
   type: 'password' | 'text' | 'number' | 'toggle' | 'select' | 'readonly'
   options: [...] for select
   desc: short hint shown beneath the field
──────────────────────────────────────────────────────────────────────────── */
const SCHEMA = [
  {
    id: 'api_keys', label: 'API Keys', icon: '🔑',
    sections: [
      {
        title: 'Anthropic Claude', color: '#8b5cf6',
        testAction: { endpoint: '/api/test/ai-key', label: '🔑 Test Key', body: { provider: 'anthropic', model: 'claude-sonnet-4-6' } },
        fields: [
          { key: 'ANTHROPIC_API_KEY', label: 'API Key', type: 'password', desc: 'Used for Claude validator & research loop.' },
        ],
      },
      {
        title: 'Google Gemini', color: '#3b82f6',
        testAction: { endpoint: '/api/test/ai-key', label: '🔑 Test Key', body: { provider: 'gemini', model: 'gemini-2.5-flash' } },
        fields: [
          { key: 'GEMINI_API_KEY', label: 'API Key', type: 'password', desc: 'Primary signal generation provider.' },
        ],
      },
      {
        title: 'OpenAI', color: '#22c55e',
        testAction: { endpoint: '/api/test/ai-key', label: '🔑 Test Key', body: { provider: 'openai', model: 'gpt-4o-mini' } },
        fields: [
          { key: 'OPENAI_API_KEY', label: 'API Key', type: 'password', desc: 'Optional fallback provider.' },
        ],
      },
      {
        title: 'DeepSeek', color: '#14b8a6',
        testAction: { endpoint: '/api/test/ai-key', label: '🔑 Test Key', body: { provider: 'deepseek', model: 'deepseek-chat' } },
        fields: [
          { key: 'DEEPSEEK_API_KEY', label: 'API Key', type: 'password', desc: 'Alternative AI provider.' },
        ],
      },
      {
        title: 'xAI / Grok', color: '#f59e0b',
        testAction: { endpoint: '/api/test/ai-key', label: '🔑 Test Key', body: { provider: 'xai', model: 'grok-3-mini' } },
        fields: [
          { key: 'XAI_API_KEY', label: 'API Key', type: 'password', desc: 'xAI Grok provider.' },
        ],
      },
      {
        title: 'Qwen / Together.ai', color: '#ef4444',
        testAction: { endpoint: '/api/test/ai-key', label: '🔑 Test Key', body: { provider: 'qwen', model: 'Qwen/Qwen2.5-7B-Instruct-Turbo' } },
        fields: [
          { key: 'QWEN_API_KEY', label: 'API Key', type: 'password', desc: 'Together.ai API key for Qwen models.' },
        ],
      },
      {
        title: 'Local LLM (Ollama)', color: '#22c55e',
        testAction: { endpoint: '/api/test/ollama', label: '🔌 Test Connection', body: {}, liveFields: { base_url: 'QWEN_LOCAL_BASE' } },
        fields: [
          { key: 'QWEN_LOCAL_BASE', label: 'Ollama Base URL', type: 'text', desc: 'Ollama API endpoint. Default: http://localhost:11434/v1' },
        ],
      },
      {
        title: 'News & Data', color: '#64748b',
        testAction: { endpoint: '/api/test/news', label: '📰 Test News API', body: {} },
        fields: [
          { key: 'NEWS_PROVIDER', label: 'Calendar Provider', type: 'select',
            options: [
              { value: 'forexfactory', label: 'ForexFactory (free, no key)' },
              { value: 'finnhub',      label: 'Finnhub (API key required)' },
              { value: 'fmp',          label: 'FMP Financial (API key required)' },
            ],
            desc: 'Economic calendar source. Finnhub/FMP fall back to ForexFactory if unavailable.',
          },
          { key: 'FINNHUB_API_KEY', label: 'Finnhub API Key', type: 'password', desc: 'Required when provider is Finnhub. Get free key at finnhub.io.' },
          { key: 'FMP_API_KEY',     label: 'FMP API Key',     type: 'password', desc: 'Required when provider is FMP Financial. Get key at financialmodelingprep.com.' },
        ],
      },
    ],
  },

  {
    id: 'web_ui', label: 'Web UI', icon: '🔒',
    sections: [
      {
        title: 'Authentication',
        fields: [
          { key: 'AUTH_TOKEN', label: 'WebUI Bearer Token', type: 'password', desc: 'Bearer token required for sensitive endpoints (agent deploy, settings, strategies). Leave blank to disable auth.' },
        ],
      },
    ],
  },

  {
    id: 'broker', label: 'Broker / MT5', icon: '📡',
    sections: [
      {
        title: 'MetaTrader 5 Connection',
        testAction: { endpoint: '/api/test/mt5', label: '🔌 Test MT5 Connection' },
        fields: [
          { key: 'MT5_SERVER',    label: 'Server',        type: 'text',     desc: 'MT5 broker server (e.g. Exness-MT5Trial7).' },
          { key: 'MT5_LOGIN',     label: 'Login',         type: 'number',   desc: 'MT5 account number.' },
          { key: 'MT5_PASSWORD',  label: 'Password',      type: 'password', desc: 'MT5 account password.' },
          { key: 'MT5_SYMBOL',    label: 'Symbol',        type: 'text',     desc: 'Default trading instrument (e.g. XAUUSDm, BTCUSDm).' },
          { key: 'MT5_TERMINAL_PATH', label: 'Terminal Path', type: 'text', desc: 'Full path to terminal64.exe. Leave blank to auto-detect.' },
        ],
      },
    ],
  },

  {
    id: 'risk', label: 'Risk', icon: '⚖️',
    sections: [
      {
        title: 'Loss Limits',
        fields: [
          { key: 'MAX_DAILY_LOSS_PCT',     label: 'Max Daily Loss (%)',     type: 'number', desc: 'Halt trading once this % of balance is lost today.' },
          { key: 'MAX_CONCURRENT_TRADES',  label: 'Max Concurrent Trades',  type: 'number', desc: 'Maximum open positions at once.' },
          { key: 'CONSECUTIVE_LOSS_LIMIT', label: 'Consecutive Loss Limit', type: 'number', desc: 'Circuit break after N consecutive losses.' },
          { key: 'MAX_SESSION_LOSS_PCT',   label: 'Max Session Loss (%)',   type: 'number', desc: 'Per-session loss cap (Asia/London/NY).' },
          { key: 'MAX_PORTFOLIO_HEAT_PCT', label: 'Max Portfolio Heat (%)', type: 'number', desc: 'Max total open risk across all positions.' },
        ],
      },
      {
        title: 'Thresholds',
        fields: [
          { key: 'RISK_SCORE_THRESHOLD',      label: 'Risk Score Threshold', type: 'number', desc: 'Abort trade if Claude risk_score exceeds this.' },
          { key: 'MACRO_ABORT_THRESHOLD',     label: 'Macro Abort Threshold', type: 'number', desc: 'Abort if pipeline modifier ≤ this (extreme conflict).' },
        ],
      },
    ],
  },

  {
    id: 'guardrails', label: 'Guardrails', icon: '🛡️',
    sections: [
      {
        title: 'Circuit Breaker',
        fields: [
          { key: 'CIRCUIT_PAUSE_SEC',   label: 'Pause Duration (s)',  type: 'number', desc: 'Seconds to pause after API failure spike.' },
          { key: 'CIRCUIT_KILL_COUNT',  label: 'Kill Count',          type: 'number', desc: 'Consecutive failures before kill switch.' },
          { key: 'PIPELINE_SIZE_FLOOR', label: 'Pipeline Size Floor', type: 'number', desc: 'Block trade if pipeline modifier is below this.' },
        ],
      },
      {
        title: 'Position Sizing Guardrails',
        fields: [
          { key: 'RISK_PCT',               label: 'Risk per Trade (%)',     type: 'number', desc: 'Account % risked per trade. Hard cap: 0.1–5%.' },
          { key: 'LEVERAGE',               label: 'Leverage',               type: 'number', desc: 'Broker leverage (e.g. 100 for 1:100).' },
          { key: 'CONTRACT_SIZE',          label: 'Contract Size',          type: 'number', desc: 'Units per lot (100 for gold, 100000 for FX).' },
          { key: 'COMMISSION_PER_LOT',     label: 'Commission / Lot (USD)', type: 'number', desc: 'Round-trip commission in USD (typical ECN: $7).' },
          { key: 'SL_SLIPPAGE_BUFFER',     label: 'SL Slippage Buffer',     type: 'number', desc: 'Inflate SL distance to absorb spread at stop.' },
          { key: 'MARGIN_CAP_PCT',         label: 'Margin Cap (%)',         type: 'number', desc: 'Max margin used as % of balance. Hard cap: 5–50%.' },
        ],
      },
      {
        title: 'Evolution Guardrails',
        fields: [
          { key: 'METALOOP_ENABLED',                label: 'MetaLoop Enabled',          type: 'toggle', desc: 'Enable automatic strategy evolution loop.' },
          { key: 'METALOOP_CHECK_INTERVAL',          label: 'Check Interval (trades)',   type: 'number', desc: 'Run research after every N closed trades. Default: 20' },
          { key: 'METALOOP_ROLLBACK_WINDOW',         label: 'Rollback Window (trades)',  type: 'number', desc: 'Monitor new version for N trades before confirming. Default: 30' },
          { key: 'METALOOP_AUTO_ACTIVATE',           label: 'Auto-Activate',             type: 'toggle', desc: 'Automatically activate optimized strategy versions.' },
          { key: 'METALOOP_DEGRADATION_THRESHOLD',   label: 'Degradation Threshold',     type: 'number', desc: 'Sharpe ratio threshold to trigger retraining (0-1). Default: 0.7' },
        ],
      },
    ],
  },

  {
    id: 'promotion', label: 'Promotion', icon: '🚦',
    sections: [
      {
        title: 'Candidate → Dry Run Gate',
        fields: [
          { key: 'PROMOTION_CANDIDATE_GATE_ALGO_ONLY', label: 'Algo Only Uses Gate', type: 'toggle', desc: 'Keep the standard promotion gate on for Algo Only cards. Recommended: on.' },
          { key: 'PROMOTION_CANDIDATE_GATE_ALGO_AI', label: 'Algo + AI Uses Gate', type: 'toggle', desc: 'Keep the standard promotion gate on for mixed Algo + AI cards. Recommended: on.' },
          { key: 'PROMOTION_CANDIDATE_GATE_AI_SIGNAL', label: 'AI Signal Discovery Uses Gate', type: 'toggle', desc: 'Turn this on only if discovery cards should also wait for the same gate before entering Dry Run.' },
        ],
      },
    ],
  },

  {
    id: 'signal', label: 'Legacy Signal & Validation', icon: '🕰️',
    sections: [
      {
        title: 'Legacy Validation Defaults',
        fields: [
          { key: 'MIN_CONFIDENCE',      label: 'Min Confidence',   type: 'number', desc: 'Minimum AI confidence score to accept a signal (0–1).' },
          { key: 'CLAUDE_MIN_RR',       label: 'Min R:R Ratio',    type: 'number', desc: 'Minimum reward-to-risk ratio.' },
          { key: 'MAX_VOLATILITY_ATR_PCT', label: 'Max Volatility (ATR %)', type: 'number', desc: 'Reject signals when ATR% exceeds this.' },
          { key: 'MIN_VOLATILITY_ATR_PCT', label: 'Min Volatility (ATR %)', type: 'number', desc: 'Reject signals when market is too quiet.' },
          { key: 'TRADE_COOLDOWN_MINUTES', label: 'Cooldown (min)',type: 'number', desc: 'Minutes to wait between trades.' },
          { key: 'MAX_SLIPPAGE_ATR',    label: 'Max Slippage (ATR)', type: 'number', desc: 'Reject fills with slippage above this ATR multiple.' },
          { key: 'MAX_SIGNAL_AGE_SECONDS', label: 'Max Signal Age (s)', type: 'number', desc: 'Discard signals older than this.' },
        ],
      },
      {
        title: 'Legacy Validation Guards',
        fields: [
          { key: 'CLAUDE_CHECK_H1_TREND', label: 'H1 Trend Check', type: 'toggle', desc: 'Validate signal against H1 trend direction.' },
          { key: 'CLAUDE_CHECK_RSI',      label: 'RSI Check',      type: 'toggle', desc: 'Block trades when RSI is overbought/oversold.' },
          { key: 'CLAUDE_RSI_OB',         label: 'RSI Overbought', type: 'number', desc: 'RSI level considered overbought.' },
          { key: 'CLAUDE_RSI_OS',         label: 'RSI Oversold',   type: 'number', desc: 'RSI level considered oversold.' },
          { key: 'CLAUDE_CHECK_NEWS',     label: 'News Check',     type: 'toggle', desc: 'Avoid trading around high-impact news.' },
          { key: 'CLAUDE_CHECK_SETUP',    label: 'Setup Check',    type: 'toggle', desc: 'Validate trade setup type against allowed list.' },
          { key: 'CLAUDE_AVOID_SETUPS',   label: 'Avoid Setups',   type: 'text',   desc: 'Comma-separated setup types to reject.' },
        ],
      },
    ],
  },

  {
    id: 'session', label: 'Session', icon: '🕐',
    sections: [
      {
        title: 'Trading Hours (UTC)',
        fields: [
          { key: 'SESSION_LONDON_OPEN',  label: 'London Open',  type: 'text', desc: 'Format: HH:MM' },
          { key: 'SESSION_LONDON_CLOSE', label: 'London Close', type: 'text' },
          { key: 'SESSION_NY_OPEN',      label: 'New York Open',  type: 'text' },
          { key: 'SESSION_NY_CLOSE',     label: 'New York Close', type: 'text' },
        ],
      },
      {
        title: 'News Avoidance',
        fields: [
          { key: 'NEWS_PRE_MINUTES',  label: 'Avoid Before News (min)', type: 'number', desc: 'Minutes before high-impact news to stop trading.' },
          { key: 'NEWS_POST_MINUTES', label: 'Avoid After News (min)',  type: 'number', desc: 'Minutes after news to resume trading.' },
          { key: 'MIN_SPREAD_POINTS', label: 'Min Spread (points)',     type: 'number', desc: 'Block trades when spread is too wide.' },
        ],
      },
    ],
  },

  {
    id: 'telegram', label: 'Telegram', icon: '✈️',
    sections: [
      {
        title: 'Notification Bot',
        testAction: { endpoint: '/api/test/telegram', label: '📨 Send Test Message' },
        fields: [
          { key: 'TELEGRAM_TOKEN',   label: 'Bot Token',  type: 'password', desc: 'Bot token from @BotFather.' },
          { key: 'TELEGRAM_CHAT_ID', label: 'Chat ID',    type: 'text',     desc: 'Your Telegram user or group chat ID.' },
          { key: 'TELEGRAM_ENABLED', label: 'Enabled',    type: 'toggle',   desc: 'Send trade alerts via Telegram.' },
        ],
      },
    ],
  },

  {
    id: 'tools', label: 'Tools', icon: '🛠️',
    sections: [
      {
        title: '1. Pipeline Filters (toggleable)',
        fields: [
          { key: 'tool_enabled_session_filter',     label: 'Session Filter',     type: 'toggle', desc: 'Block trades outside active sessions (London/NY). Order: 1.' },
          { key: 'MIN_SESSION_SCORE',               label: 'Min Session Score',  type: 'number', desc: 'Min session quality score (0.0–1.0). Default: 0.55' },
          { key: 'tool_enabled_news_filter',        label: 'News Filter',        type: 'toggle', desc: 'Block trades around high-impact news events. Order: 2.' },
          { key: 'NEWS_PRE_MINUTES',                label: 'Pre-News Window (min)', type: 'number', desc: 'Minutes before news to block. Default: 30' },
          { key: 'NEWS_POST_MINUTES',               label: 'Post-News Window (min)', type: 'number', desc: 'Minutes after news to block. Default: 15' },
          { key: 'tool_enabled_volatility_filter',  label: 'Volatility Filter',  type: 'toggle', desc: 'Block trades during extreme or dead volatility. Order: 3.' },
          { key: 'MAX_VOLATILITY_ATR_PCT',          label: 'Max ATR %',          type: 'number', desc: 'Block if H1 ATR% exceeds this. Default: 2.5' },
          { key: 'MIN_VOLATILITY_ATR_PCT',          label: 'Min ATR %',          type: 'number', desc: 'Block if H1 ATR% below this (dead market). Default: 0.05' },
          { key: 'tool_enabled_dxy_filter',         label: 'DXY Filter',         type: 'toggle', desc: 'Use USD index correlation for gold/forex signals. Order: 4.' },
          { key: 'tool_enabled_sentiment_filter',   label: 'Sentiment Filter',   type: 'toggle', desc: 'Polymarket macro sentiment — reduces size on conflict. Order: 5.' },
          { key: 'tool_enabled_risk_filter',        label: 'Risk Filter',        type: 'toggle', desc: 'Enforce daily loss, drawdown, kill switch limits. Order: 6.' },
        ],
      },
      {
        title: '2. Validation Rule Guards (toggleable per-strategy)',
        fields: [
          { key: 'USE_EMA200_FILTER',    label: 'EMA200 Trend Filter',    type: 'toggle', desc: 'Block trades against the EMA200 trend direction.' },
          { key: 'USE_BOS_GUARD',        label: 'BOS Structure Guard',    type: 'toggle', desc: 'Require Break of Structure confirmation on M15.' },
          { key: 'BOS_MIN_BREAK_ATR',    label: 'BOS Min Break (ATR)',    type: 'number', desc: 'Min break distance in ATR units. Default: 0.2' },
          { key: 'BOS_SWING_LOOKBACK',   label: 'BOS Swing Lookback',    type: 'number', desc: 'Bars to scan for swing points. Default: 20' },
          { key: 'CHECK_FVG',            label: 'FVG Structure Guard',    type: 'toggle', desc: 'Require entry inside a Fair Value Gap zone.' },
          { key: 'FVG_MIN_SIZE_ATR',     label: 'FVG Min Size (ATR)',     type: 'number', desc: 'Minimum gap size in ATR units. Default: 0.15' },
          { key: 'FVG_LOOKBACK',         label: 'FVG Lookback',          type: 'number', desc: 'Candles to scan for gaps. Default: 20' },
          { key: 'CHECK_TICK_JUMP',      label: 'Tick Jump Guard',       type: 'toggle', desc: 'Reject entries on sudden 2-bar price spikes.' },
          { key: 'TICK_JUMP_ATR_MAX',    label: 'Tick Jump Max (ATR)',   type: 'number', desc: 'Max 2-bar move in ATR units. Default: 0.8' },
          { key: 'CHECK_LIQ_VACUUM',     label: 'Liquidity Vacuum Guard', type: 'toggle', desc: 'Reject thin-body spike candles (no conviction).' },
          { key: 'LIQ_VACUUM_SPIKE_MULT', label: 'Spike Multiplier',     type: 'number', desc: 'ATR spike threshold. Default: 2.5' },
          { key: 'LIQ_VACUUM_BODY_PCT',  label: 'Min Body %',            type: 'number', desc: 'Candle body min % of range. Default: 30' },
          { key: 'USE_VWAP_GUARD',       label: 'VWAP Guard',            type: 'toggle', desc: 'Block entries overextended from VWAP.' },
          { key: 'VWAP_EXTENSION_MAX_ATR', label: 'VWAP Max Extension (ATR)', type: 'number', desc: 'Max distance from VWAP in ATR units. Default: 1.5' },
          { key: 'USE_MACD_FILTER',     label: 'MACD Filter',            type: 'toggle', desc: 'Block if MACD histogram disagrees with direction.' },
          { key: 'MACD_FAST',           label: 'MACD Fast Period',       type: 'number', desc: 'Fast EMA period. Default: 12' },
          { key: 'MACD_SLOW',           label: 'MACD Slow Period',       type: 'number', desc: 'Slow EMA period. Default: 26' },
          { key: 'MACD_SIGNAL',         label: 'MACD Signal Period',     type: 'number', desc: 'Signal line period. Default: 9' },
          { key: 'USE_BOLLINGER_FILTER', label: 'Bollinger Filter',      type: 'toggle', desc: 'Block if entry outside Bollinger band zone.' },
          { key: 'BB_PERIOD',           label: 'Bollinger Period',       type: 'number', desc: 'Moving average period. Default: 20' },
          { key: 'BB_STD_DEV',          label: 'Bollinger Std Dev',      type: 'number', desc: 'Standard deviation multiplier. Default: 2.0' },
          { key: 'USE_ADX_FILTER',      label: 'ADX Filter',             type: 'toggle', desc: 'Block if ADX below threshold (no trend).' },
          { key: 'ADX_PERIOD',          label: 'ADX Period',             type: 'number', desc: 'ADX indicator period. Default: 14' },
          { key: 'ADX_MIN_THRESHOLD',   label: 'ADX Min Threshold',      type: 'number', desc: 'Block below this value. Default: 20' },
          { key: 'USE_VOLUME_FILTER',   label: 'Volume Filter',          type: 'toggle', desc: 'Block if volume below average.' },
          { key: 'VOLUME_MA_PERIOD',    label: 'Volume MA Period',       type: 'number', desc: 'Volume moving average bars. Default: 20' },
          { key: 'USE_SWING_STRUCTURE', label: 'Swing Structure',        type: 'toggle', desc: 'Require HH/HL for BUY, LH/LL for SELL.' },
        ],
      },
      {
        title: '3. Stateful Guards (always-on system protection)',
        fields: [
          { key: 'GUARD_SIGNAL_HASH_WINDOW',       label: 'Signal Hash Dedup — Window',      type: 'number', desc: 'Reject duplicate setups within N cycles. Default: 3' },
          { key: 'GUARD_CONF_VARIANCE_WINDOW',      label: 'Confidence Variance — Window',    type: 'number', desc: 'Rolling window of confidence scores. Default: 3' },
          { key: 'GUARD_CONF_VARIANCE_MAX_STDEV',   label: 'Confidence Variance — Max Stdev', type: 'number', desc: 'Max allowed stdev before rejection. Default: 0.15' },
          { key: 'GUARD_SPREAD_REGIME_WINDOW',      label: 'Spread Regime — Window',          type: 'number', desc: 'Samples for rolling spread median. Default: 50' },
          { key: 'GUARD_SPREAD_REGIME_THRESHOLD',   label: 'Spread Regime — Threshold',       type: 'number', desc: 'Reject if spread > N× median. Default: 1.8' },
          { key: 'GUARD_EQUITY_CURVE_WINDOW',       label: 'Equity Curve Scaler — Window',    type: 'number', desc: 'Trades to look back for equity MA. Default: 20' },
          { key: 'GUARD_EQUITY_CURVE_SCALE',        label: 'Equity Curve Scaler — Scale',     type: 'number', desc: 'Risk multiplier when equity below MA. Default: 0.5' },
          { key: 'GUARD_DD_PAUSE_MINUTES',          label: 'Drawdown Pause — Duration (min)', type: 'number', desc: 'Pause entries after accelerating losses. Default: 30' },
          { key: 'GUARD_DD_PAUSE_LOOKBACK',         label: 'Drawdown Pause — Lookback',       type: 'number', desc: 'Consecutive losses to trigger pause. Default: 3' },
          { key: 'GUARD_PORTFOLIO_CAP_ENABLED',     label: 'Portfolio Risk Cap',              type: 'toggle', desc: 'Block new entries when total open risk exceeds limit. Always recommended.' },
          { key: 'USE_CORRELATION_GUARD',           label: 'Correlation Guard',               type: 'toggle', desc: 'Block/reduce correlated positions.' },
          { key: 'CORRELATION_THRESHOLD_BLOCK',     label: 'Correlation — Block Threshold',   type: 'number', desc: 'Block if correlation ≥ this. Default: 0.90' },
          { key: 'CORRELATION_THRESHOLD_REDUCE',    label: 'Correlation — Reduce Threshold',  type: 'number', desc: 'Reduce size if correlation ≥ this. Default: 0.75' },
          { key: 'GUARD_NEAR_DEDUP_ATR',            label: 'Near-Position Dedup (ATR)',       type: 'number', desc: 'Skip signal if open trade within N ATR. Default: 1.0' },
        ],
      },
      {
        title: '4. Position Management (live trades)',
        fields: [
          { key: 'REPOSITIONER_ENABLED',              label: 'Trade Repositioner',          type: 'toggle', desc: 'Dynamically manage open trades (SL trail, partial close).' },
          { key: 'REPOSITIONER_OPPOSITE_SIGNAL',      label: 'Close on Opposite Signal',    type: 'toggle', desc: 'Full close if new signal conflicts with open trade.' },
          { key: 'REPOSITIONER_NEWS_RISK',            label: 'News Risk — Tighten SL',      type: 'toggle', desc: 'Move SL to breakeven or partial close before news.' },
          { key: 'REPOSITIONER_NEWS_WINDOW_MIN',      label: 'News Window (min)',            type: 'number', desc: 'Minutes before news to trigger. Default: 15' },
          { key: 'REPOSITIONER_VOLUME_SPIKE',         label: 'Volume Spike — Trail SL',     type: 'toggle', desc: 'Move SL to breakeven on volume spike if in profit.' },
          { key: 'REPOSITIONER_VOLUME_SPIKE_MULT',    label: 'Volume Spike Multiplier',     type: 'number', desc: 'M15 volume must be ≥ N× 20-bar avg. Default: 2.5' },
          { key: 'REPOSITIONER_VOLATILITY_SPIKE',     label: 'Volatility Spike — Trail SL', type: 'toggle', desc: 'Move SL to breakeven on ATR spike if in profit.' },
          { key: 'REPOSITIONER_VOLATILITY_SPIKE_MULT', label: 'ATR Spike Multiplier',       type: 'number', desc: 'H1 ATR must be ≥ N× baseline. Default: 1.8' },
        ],
      },
      {
        title: 'Mode-Specific Overrides',
        fields: [
          { key: 'tool_enabled_risk_filter_dry_run',  label: 'Risk Filter (Dry Run)', type: 'toggle' },
          { key: 'tool_enabled_risk_filter_backtest', label: 'Risk Filter (Backtest)', type: 'toggle' },
          { key: 'tool_enabled_risk_filter_live',     label: 'Risk Filter (Live)',    type: 'toggle' },
        ],
      },
    ],
  },

  {
    id: 'sounds', label: 'Sounds', icon: '🔊',
    localOnly: true,
    sections: [],
  },

  {
    id: 'assets', label: 'Assets', icon: '🪙',
    localOnly: true,
    sections: [],
  },

  {
    id: 'system', label: 'System', icon: '⚙️',
    sections: [
      {
        title: 'Runtime',
        fields: [
          { key: 'DRY_RUN',      label: 'Dry Run Mode',  type: 'toggle', desc: 'Simulate trades without executing on the broker.' },
          { key: 'LOG_LEVEL',    label: 'Log Level',     type: 'select', options: ['DEBUG','INFO','WARNING','ERROR'], desc: 'Logging verbosity.' },
          { key: 'ENVIRONMENT',  label: 'Environment',   type: 'select', options: ['dev','staging','prod'], desc: 'Deployment environment.' },
        ],
      },
      {
        title: 'Health Monitor',
        fields: [
          { key: 'HEALTH_W_SHARPE',           label: 'Weight: Sharpe',        type: 'number', desc: 'Sharpe component weight. Default: 0.35' },
          { key: 'HEALTH_W_WINRATE',          label: 'Weight: Win Rate',      type: 'number', desc: 'Win rate component weight. Default: 0.25' },
          { key: 'HEALTH_W_DRAWDOWN',         label: 'Weight: Drawdown',      type: 'number', desc: 'Drawdown penalty weight. Default: 0.25' },
          { key: 'HEALTH_W_STAGNATION',       label: 'Weight: Stagnation',    type: 'number', desc: 'Stagnation penalty weight. Default: 0.15' },
          { key: 'HEALTH_HEALTHY_THRESHOLD',   label: 'Healthy Threshold',    type: 'number', desc: 'Score above this = healthy. Default: 0.6' },
          { key: 'HEALTH_CRITICAL_THRESHOLD',  label: 'Critical Threshold',   type: 'number', desc: 'Score below this = critical rollback. Default: 0.3' },
        ],
      },
      {
        title: 'Confidence Sizing & Micro-Learning',
        fields: [
          { key: 'CONFIDENCE_SIZE_ENABLED',   label: 'Confidence Sizing',     type: 'toggle', desc: 'Scale position size by signal confidence (0.85+→1.25×, 0.55-→0.5×).' },
          { key: 'MICRO_LEARN_ENABLED',       label: 'Micro-Learning',        type: 'toggle', desc: 'Enable per-trade parameter nudges (±1% per trade, ±5% total drift).' },
          { key: 'MICRO_LEARN_MAX_PER_TRADE', label: 'Max Nudge Per Trade',   type: 'number', desc: 'Max param change per trade (fraction). Default: 0.01' },
          { key: 'MICRO_LEARN_MAX_DRIFT',     label: 'Max Total Drift',       type: 'number', desc: 'Max cumulative drift from baseline (fraction). Default: 0.05' },
        ],
      },
      {
        title: 'Database',
        fields: [
          { key: 'DATABASE_URL', label: 'Database URL', type: 'text', desc: 'SQLite: sqlite:///alphaloop.db — PostgreSQL: postgresql+asyncpg://user:pass@host/db' },
        ],
      },
    ],
  },
];

const SENSITIVE_SUFFIXES = ['_API_KEY', '_TOKEN', '_PASSWORD', '_SECRET'];
function isSensitive(key) {
  return SENSITIVE_SUFFIXES.some(s => key.toUpperCase().endsWith(s));
}

function isTrue(val) {
  return String(val).toLowerCase() === 'true' || val === '1' || val === 'yes';
}

/* ── Render ─────────────────────────────────────────────────────────────── */
export async function render(container) {
  container.innerHTML = `
    <div class="page-title">⚙️ Settings</div>
    <div class="settings-wrap">
      <div class="settings-sidebar" id="settings-sidebar"></div>
      <div class="settings-body">
        <div id="settings-panel">
          <div class="settings-loading">Loading settings…</div>
        </div>
        <div class="settings-footer">
          <button class="btn btn-primary" id="save-settings">
            <span>💾</span> Save Changes
          </button>
          <span class="settings-save-hint" id="save-hint"></span>
        </div>
      </div>
    </div>
  `;

  let allSettings = {};
  let activeTab = SCHEMA[0].id;
  let _modelCatalog = []; // loaded from /api/test/models
  let _usageData = {}; // per-provider session usage from /api/settings/usage

  /* Load settings + model catalog + session usage */
  try {
    const [settingsData, modelData, usageResp] = await Promise.all([
      apiGet('/api/settings'),
      apiGet('/api/test/models').catch(() => ({ models: [] })),
      apiGet('/api/settings/usage').catch(() => ({ usage: {} })),
    ]);
    allSettings = settingsData.settings || {};
    _modelCatalog = modelData.models || [];
    _usageData = usageResp.usage || {};
  } catch (err) {
    document.getElementById('settings-panel').innerHTML =
      `<div class="settings-error">⚠️ ${err.message}</div>`;
    return;
  }

  /* ── Sounds panel (localStorage-only) ───────────────────────────────── */
  function renderSoundsPanel() {
    const el = document.getElementById('settings-panel');

    const SOUND_EVENTS = [
      {
        key:     'trade_open',
        icon:    '📈',
        label:   'Trade Opened',
        desc:    'Plays when an agent opens a new position.',
        preview: playTradeOpened,
        notes:   'E5 → G#5 ascending ping',
      },
      {
        key:     'trade_close_profit',
        icon:    '🟢',
        label:   'Trade Closed — Profit',
        desc:    'Plays when a trade closes with a positive P&L.',
        preview: playTradeClosedProfit,
        notes:   'E5 → G5 → C6 ascending trio',
      },
      {
        key:     'trade_close_loss',
        icon:    '🔴',
        label:   'Trade Closed — Loss',
        desc:    'Plays when a trade closes with a negative P&L.',
        preview: playTradeClosedLoss,
        notes:   'G4 → E4 → C4 descending trio',
      },
      {
        key:     'seedlab',
        icon:    '🧪',
        label:   'SeedLab Complete',
        desc:    'Plays when a SeedLab backtest run finishes successfully.',
        preview: playSeedLabDone,
        notes:   'C5→E5→G5→C6 fanfare',
      },
      {
        key:     'evolution',
        icon:    '🏆',
        label:   'Strategy Evolution',
        desc:    'Plays when an agent\'s strategy is promoted to a new version.',
        preview: playEvolution,
        notes:   'C5→E5→G5→B5→C6 arpeggio',
      },
    ];

    const globalOn  = isGloballyEnabled();
    const vol       = getVolume();
    const volPct    = Math.round(vol * 100);

    el.innerHTML = `
      <div class="settings-section">
        <div class="settings-section-title">Master Controls</div>
        <div class="settings-fields">

          <div class="field-row" style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border)">
            <div>
              <div style="font-size:13px;font-weight:600;color:var(--text)">Sound Effects</div>
              <div class="field-desc">Enable or mute all sound effects globally.</div>
            </div>
            <label class="toggle-switch">
              <input type="checkbox" id="snd-global" ${globalOn ? 'checked' : ''}>
              <span class="toggle-track"><span class="toggle-thumb"></span></span>
              <span class="toggle-label">${globalOn ? 'Enabled' : 'Muted'}</span>
            </label>
          </div>

          <div class="field-row" id="snd-volume-row" style="padding:12px 0;border-bottom:1px solid var(--border);${!globalOn ? 'opacity:.4;pointer-events:none' : ''}">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
              <div style="font-size:13px;font-weight:600;color:var(--text)">Volume</div>
              <span id="snd-vol-label" style="font-size:12px;font-weight:700;color:var(--primary);min-width:36px;text-align:right">${volPct}%</span>
            </div>
            <input type="range" id="snd-volume" min="0" max="100" value="${volPct}"
              style="width:100%;accent-color:var(--primary);cursor:pointer">
            <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:4px">
              <span>Silent</span><span>50%</span><span>Full</span>
            </div>
          </div>

        </div>
      </div>

      <div class="settings-section" id="snd-events-section" style="${!globalOn ? 'opacity:.4;pointer-events:none' : ''}">
        <div class="settings-section-title">Event Sounds</div>
        <div class="settings-fields">
          ${SOUND_EVENTS.map(ev => {
            const on = isEventEnabled(ev.key);
            return `
            <div class="field-row" style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border)">
              <span style="font-size:22px;width:28px;text-align:center;flex-shrink:0">${ev.icon}</span>
              <div style="flex:1;min-width:0">
                <div style="font-size:13px;font-weight:600;color:var(--text)">${ev.label}</div>
                <div class="field-desc" style="margin-bottom:2px">${ev.desc}</div>
                <div style="font-size:10px;color:var(--muted);font-family:monospace">${ev.notes}</div>
              </div>
              <button class="btn btn-sm snd-preview-btn" data-key="${ev.key}"
                style="flex-shrink:0;font-size:11px;padding:4px 10px" title="Preview sound">
                ▶ Preview
              </button>
              <label class="toggle-switch" style="flex-shrink:0">
                <input type="checkbox" class="snd-event-toggle" data-key="${ev.key}" ${on ? 'checked' : ''}>
                <span class="toggle-track"><span class="toggle-thumb"></span></span>
                <span class="toggle-label">${on ? 'On' : 'Off'}</span>
              </label>
            </div>`;
          }).join('')}
        </div>
      </div>
    `;

    // ── Wire global toggle ──────────────────────────────────────────────
    const globalCb    = el.querySelector('#snd-global');
    const volumeRow   = el.querySelector('#snd-volume-row');
    const eventsSection = el.querySelector('#snd-events-section');

    globalCb.addEventListener('change', () => {
      setSoundsEnabled(globalCb.checked);
      globalCb.nextElementSibling.nextElementSibling.textContent = globalCb.checked ? 'Enabled' : 'Muted';
      const dim = !globalCb.checked ? 'opacity:.4;pointer-events:none' : '';
      volumeRow.style.cssText   = `padding:12px 0;border-bottom:1px solid var(--border);${dim}`;
      eventsSection.style.cssText = dim;
    });

    // ── Wire volume slider ──────────────────────────────────────────────
    const volSlider = el.querySelector('#snd-volume');
    const volLabel  = el.querySelector('#snd-vol-label');
    volSlider.addEventListener('input', () => {
      const pct = parseInt(volSlider.value);
      volLabel.textContent = `${pct}%`;
      setVolume(pct / 100);
    });

    // ── Wire event toggles ──────────────────────────────────────────────
    el.querySelectorAll('.snd-event-toggle').forEach(cb => {
      cb.addEventListener('change', () => {
        setEventEnabled(cb.dataset.key, cb.checked);
        cb.nextElementSibling.nextElementSibling.textContent = cb.checked ? 'On' : 'Off';
      });
    });

    // ── Wire preview buttons ────────────────────────────────────────────
    const previewMap = {
      trade_open:         playTradeOpened,
      trade_close_profit: playTradeClosedProfit,
      trade_close_loss:   playTradeClosedLoss,
      seedlab:            playSeedLabDone,
      evolution:          playEvolution,
    };
    el.querySelectorAll('.snd-preview-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        // Force-play regardless of toggle state so user can audition
        const origEnabled = localStorage.getItem(`sounds_${btn.dataset.key}`);
        const origGlobal  = localStorage.getItem('sounds_enabled');
        localStorage.setItem(`sounds_${btn.dataset.key}`, 'true');
        localStorage.setItem('sounds_enabled', 'true');
        previewMap[btn.dataset.key]?.();
        // Restore
        if (origEnabled !== null) localStorage.setItem(`sounds_${btn.dataset.key}`, origEnabled);
        else localStorage.removeItem(`sounds_${btn.dataset.key}`);
        if (origGlobal !== null) localStorage.setItem('sounds_enabled', origGlobal);
        else localStorage.removeItem('sounds_enabled');
      });
    });
  }

  /* ── Assets panel ───────────────────────────────────────────────────── */
  const _TOOL_GROUPS = [
    {
      label: 'Core',
      keys: ['session', 'volatility', 'ema200'],
    },
    {
      label: 'Structure Guards',
      keys: ['bos', 'fvg', 'tick_jump', 'liq_vacuum', 'vwap'],
    },
    {
      label: 'Technical',
      keys: ['macd', 'bollinger', 'adx', 'volume', 'swing'],
    },
  ];

  const _TOOL_SHORT = {
    session:    'Session',
    volatility: 'Volatility',
    ema200:     'EMA200',
    bos:        'BOS',
    fvg:        'FVG',
    tick_jump:  'Tick Jump',
    liq_vacuum: 'Liq Vacuum',
    vwap:       'VWAP',
    macd:       'MACD',
    bollinger:  'Bollinger',
    adx:        'ADX',
    volume:     'Volume',
    swing:      'Swing',
  };

  const _TOOL_DESC = {
    session:    'Block trades outside active sessions (London/NY)',
    volatility: 'Block extreme or dead ATR% conditions',
    ema200:     'Block entries against the EMA200 direction',
    bos:        'Require a Break of Structure before entry',
    fvg:        'Require a Fair Value Gap for entry',
    tick_jump:  'Reject 2-bar price spikes',
    liq_vacuum: 'Reject thin-body candles (no follow-through)',
    vwap:       'Block overextended entries far from VWAP',
    macd:       'Block when MACD histogram disagrees',
    bollinger:  'Block outside the Bollinger band zone',
    adx:        'Block when ADX < 20 (no trend)',
    volume:     'Block when volume is below rolling average',
    swing:      'Require HH/HL or LH/LL pattern',
  };

  const _CLASS_COLORS = {
    spot_metal:  '#f59e0b',
    crypto:      '#8b5cf6',
    forex_major: '#3b82f6',
    forex_minor: '#06b6d4',
    index:       '#22c55e',
    stock:       '#64748b',
  };

  const _CLASS_ICONS = {
    spot_metal:  '⚙',
    crypto:      '₿',
    forex_major: '💱',
    forex_minor: '💱',
    index:       '📈',
    stock:       '🏢',
  };

  let _assetsCache = null;

  function _countActive(tools) {
    return Object.values(tools).filter(Boolean).length;
  }

  function _buildCard(asset) {
    const color = _CLASS_COLORS[asset.asset_class] || '#64748b';
    const icon  = _CLASS_ICONS[asset.asset_class]  || '◆';
    const initials = asset.symbol.slice(0, 3);
    const active = _countActive(asset.tools);
    const total  = Object.keys(asset.tools).length;

    const groupsHtml = _TOOL_GROUPS.map(g => `
      <div>
        <div class="asset-tool-group-label">${g.label}</div>
        <div class="asset-tool-chips">
          ${g.keys.map(k => {
            const checked = asset.tools[k] ? 'checked' : '';
            return `<label class="tool-chip-label" title="${_TOOL_DESC[k] || k}">
              <input type="checkbox" data-tool="${k}" ${checked}>
              ${_TOOL_SHORT[k] || k}
            </label>`;
          }).join('')}
        </div>
      </div>`).join('');

    return `
      <div class="asset-preset-card" data-symbol="${asset.symbol}">
        <div class="asset-preset-header">
          <div class="asset-preset-monogram" style="background:${color}20;color:${color}">
            ${initials}
          </div>
          <div class="asset-preset-info">
            <span class="asset-preset-ticker">${asset.symbol}</span>
            <div class="asset-preset-meta">
              <span class="asset-preset-name">${asset.display_name}</span>
              <span class="asset-preset-class" style="background:${color}18;color:${color};border-color:${color}35">
                ${icon} ${asset.asset_class.replace('_', ' ')}
              </span>
            </div>
          </div>
          <span class="asset-active-count" data-count="${asset.symbol}">${active}/${total} active</span>
          <span class="asset-save-feedback" data-feedback="${asset.symbol}">✓ Saved</span>
          <button class="asset-save-btn" data-symbol="${asset.symbol}">Save</button>
        </div>
        <div class="asset-preset-body">${groupsHtml}</div>
      </div>`;
  }

  async function renderAssetsPanel() {
    const el = document.getElementById('settings-panel');
    const footer = document.querySelector('.settings-footer');
    if (footer) footer.style.display = 'none';

    el.innerHTML = '<div class="settings-loading">Loading asset configs…</div>';

    try {
      const data = await apiGet('/api/assets');
      _assetsCache = data.assets || [];
    } catch (err) {
      el.innerHTML = `<div class="settings-error">⚠️ ${err.message}</div>`;
      return;
    }

    el.innerHTML = `
      <p class="assets-intro">
        Configure which filters are active by default for each asset. Presets auto-apply in SeedLab when you switch symbols.
        Click a chip to toggle it, then <strong>Save</strong> to persist.
      </p>
      <div class="assets-grid">
        ${_assetsCache.map(_buildCard).join('')}
      </div>`;

    // Live active-count update on chip toggle
    el.querySelectorAll('.asset-preset-card').forEach(card => {
      const symbol = card.dataset.symbol;
      const countEl = el.querySelector(`.asset-active-count[data-count="${symbol}"]`);

      card.querySelectorAll('input[type="checkbox"][data-tool]').forEach(cb => {
        cb.addEventListener('change', () => {
          const total = card.querySelectorAll('input[data-tool]').length;
          const active = [...card.querySelectorAll('input[data-tool]')].filter(c => c.checked).length;
          if (countEl) countEl.textContent = `${active}/${total} active`;
        });
      });

      // Save button
      const btn = card.querySelector('.asset-save-btn');
      const feedbackEl = card.querySelector(`.asset-save-feedback[data-feedback="${symbol}"]`);
      btn.addEventListener('click', async () => {
        const tools = {};
        card.querySelectorAll('input[data-tool]').forEach(cb => {
          tools[cb.dataset.tool] = cb.checked;
        });
        btn.disabled = true;
        btn.textContent = 'Saving…';
        try {
          await apiPut(`/api/assets/${symbol}/tools`, { tools });
          feedbackEl.classList.add('shown');
          setTimeout(() => { feedbackEl.classList.remove('shown'); }, 2200);
          window.__assetPresetsCache = null;
          window.showToast(`${symbol} preset saved`);
        } catch (err) {
          window.showToast(err.message, 'error');
        }
        btn.disabled = false;
        btn.textContent = 'Save';
      });
    });
  }

  /* ── Sidebar nav ─────────────────────────────────────────────────────── */
  function renderSidebar() {
    const el = document.getElementById('settings-sidebar');
    el.innerHTML = SCHEMA.map(tab => {
      const hasData = tab.localOnly
        ? isGloballyEnabled()
        : tab.sections.some(s => s.fields.some(f => f.key in allSettings && allSettings[f.key]));
      return `
        <div class="settings-nav-item ${activeTab === tab.id ? 'active' : ''}" data-tab="${tab.id}">
          <span class="nav-icon">${tab.icon}</span>
          <span class="nav-label">${tab.label}</span>
          ${hasData ? '<span class="nav-dot"></span>' : ''}
        </div>`;
    }).join('');
    el.querySelectorAll('.settings-nav-item').forEach(item => {
      item.addEventListener('click', () => {
        activeTab = item.dataset.tab;
        renderSidebar();
        renderPanel();
      });
    });
  }

  /* ── Main panel ──────────────────────────────────────────────────────── */
  function renderPanel() {
    const tab = SCHEMA.find(t => t.id === activeTab);
    if (!tab) return;

    // Local-only tabs (e.g. Sounds) don't use the server API or the Save button
    const footer = document.querySelector('.settings-footer');
    if (footer) footer.style.display = tab.localOnly ? 'none' : '';

    if (tab.localOnly) {
      if (tab.id === 'sounds') renderSoundsPanel();
      if (tab.id === 'assets') renderAssetsPanel();
      return;
    }

    const el = document.getElementById('settings-panel');
    el.innerHTML = tab.sections.map(section => {
      const provider = section.testAction?.body?.provider;
      const usage = provider ? (_usageData[provider] || { calls: 0, errors: 0 }) : null;
      const usageBadge = usage
        ? `<span class="usage-badge${usage.errors > 0 ? ' usage-warn' : ''}">Session: ${usage.calls} call${usage.calls !== 1 ? 's' : ''}${usage.errors > 0 ? ` · ${usage.errors} err` : ''}</span>`
        : '';
      return `
      <div class="settings-section">
        <div class="settings-section-title">${section.title}${usageBadge}</div>
        <div class="settings-fields">
          ${section.fields.map(f => renderField(f)).join('')}
        </div>
        ${section.testAction ? `
          <div class="settings-test-row">
            <button class="btn btn-sm settings-test-btn" data-endpoint="${section.testAction.endpoint}" data-body='${JSON.stringify(section.testAction.body || {})}' data-live-fields='${JSON.stringify(section.testAction.liveFields || {})}' data-label="${section.testAction.label}">${section.testAction.label}</button>
            <span class="settings-test-result"></span>
          </div>` : ''}
      </div>`;
    }).join('');

    // Wire test buttons
    el.querySelectorAll('.settings-test-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const endpoint = btn.dataset.endpoint;
        const bodyStr = btn.dataset.body || '{}';
        const body = JSON.parse(bodyStr);
        // Merge live form field values into body (so test uses current input, not stale DB value)
        const liveFields = JSON.parse(btn.dataset.liveFields || '{}');
        const section = btn.closest('.settings-section');
        Object.entries(liveFields).forEach(([bodyKey, dataKey]) => {
          const input = section?.querySelector(`[data-key="${dataKey}"]`);
          if (input?.value) body[bodyKey] = input.value;
        });
        // Use unique result selector (endpoint + body hash)
        const resultEl = btn.nextElementSibling;
        const originalLabel = btn.dataset.label;
        btn.disabled = true;
        btn.textContent = 'Testing...';
        if (resultEl) resultEl.textContent = '';
        try {
          const res = await apiPost(endpoint, body);
          if (resultEl) {
            if (res.success) {
              resultEl.textContent = `✓ ${res.message || 'OK'}`;
              resultEl.style.color = 'var(--green)';
            } else {
              resultEl.textContent = `✗ ${res.message || res.error || 'Failed'}`;
              resultEl.style.color = 'var(--red)';
            }
          }
        } catch (err) {
          if (resultEl) {
            resultEl.textContent = `✗ ${err.message}`;
            resultEl.style.color = 'var(--red)';
          }
        }
        btn.disabled = false;
        btn.textContent = originalLabel;
      });
    });

    // Wire toggle switches
    el.querySelectorAll('.toggle-switch input').forEach(cb => {
      cb.addEventListener('change', () => {
        allSettings[cb.dataset.key] = cb.checked ? 'true' : 'false';
      });
    });

    // Wire text/number/select inputs to update allSettings immediately
    el.querySelectorAll('input[data-key]:not([type="checkbox"]), select[data-key]').forEach(inp => {
      inp.addEventListener('input', () => { allSettings[inp.dataset.key] = inp.value; });
      inp.addEventListener('change', () => { allSettings[inp.dataset.key] = inp.value; });
    });

    // Wire show/hide eye buttons
    el.querySelectorAll('.eye-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const inp = btn.previousElementSibling;
        const hidden = inp.type === 'password';
        inp.type = hidden ? 'text' : 'password';
        btn.textContent = hidden ? '🙈' : '👁️';
      });
    });
  }

  /* ── Field renderer ──────────────────────────────────────────────────── */
  function renderField(f) {
    const val = allSettings[f.key] ?? '';
    const sensitive = isSensitive(f.key);
    const configured = sensitive && val;
    const desc = f.desc ? `<div class="field-desc">${f.desc}</div>` : '';

    let inputHtml;
    if (f.type === 'model_select') {
      // Dropdown populated from model hub catalog
      const models = f.filter
        ? _modelCatalog.filter(m => m.provider === f.filter)
        : _modelCatalog;
      const opts = models.map(m =>
        `<option value="${m.id}" ${val === m.id ? 'selected' : ''}>${m.display_name} (${m.id})</option>`
      ).join('');
      // Include current value even if not in catalog (custom model)
      const hasVal = val && models.some(m => m.id === val);
      const customOpt = val && !hasVal ? `<option value="${val}" selected>${val}</option>` : '';
      inputHtml = `<select class="field-input" data-key="${f.key}">
        <option value="">— Select model —</option>
        ${customOpt}${opts}
      </select>`;
    } else if (f.type === 'toggle') {
      const checked = isTrue(val) ? 'checked' : '';
      inputHtml = `
        <label class="toggle-switch">
          <input type="checkbox" data-key="${f.key}" ${checked}>
          <span class="toggle-track"><span class="toggle-thumb"></span></span>
          <span class="toggle-label">${isTrue(val) ? 'Enabled' : 'Disabled'}</span>
        </label>`;
      // Update label on change
    } else if (f.type === 'select') {
      const opts = (f.options || []).map(o => {
        const v = typeof o === 'object' ? o.value : o;
        const l = typeof o === 'object' ? o.label : o;
        return `<option value="${v}" ${val === v ? 'selected' : ''}>${l}</option>`;
      }).join('');
      inputHtml = `<select class="field-input" data-key="${f.key}">${opts}</select>`;
    } else {
      const isPass = f.type === 'password';
      const statusBadge = sensitive
        ? `<span class="api-status ${configured ? 'configured' : 'not-set'}">${configured ? '✓ Set' : '✗ Not set'}</span>`
        : '';
      inputHtml = `
        <div class="field-input-wrap">
          ${statusBadge}
          <div class="input-row">
            <input type="${isPass ? 'password' : f.type === 'number' ? 'number' : 'text'}"
              class="field-input" data-key="${f.key}"
              value="${val.replace(/"/g, '&quot;')}"
              placeholder="${isPass && !val ? 'Not configured' : ''}">
            ${isPass ? '<button class="eye-btn" type="button" title="Show/hide">👁️</button>' : ''}
          </div>
        </div>`;
    }

    return `
      <div class="field-row">
        <div class="field-label">${f.label}</div>
        <div class="field-control">
          ${inputHtml}
          ${desc}
        </div>
      </div>`;
  }

  /* ── Save ────────────────────────────────────────────────────────────── */
  document.getElementById('save-settings').addEventListener('click', async () => {
    // Strip masked values (***...xxxx) — these are server-side redactions and must
    // not be written back, otherwise they overwrite the real secrets in the DB.
    const toSend = Object.fromEntries(
      Object.entries(allSettings).filter(([, v]) => !String(v).startsWith('***'))
    );
    try {
      // Sync AUTH_TOKEN to localStorage BEFORE the PUT so the Bearer header is sent.
      // Only sync if the user actually typed a new token (not a masked placeholder).
      const tokenVal = allSettings.AUTH_TOKEN;
      if (tokenVal && !String(tokenVal).startsWith('***')) setAuthToken(tokenVal);
      await apiPut('/api/settings', { settings: toSend });
      const hint = document.getElementById('save-hint');
      hint.textContent = '✓ Saved';
      hint.style.color = 'var(--green)';
      setTimeout(() => { hint.textContent = ''; }, 2500);
      renderSidebar(); // refresh dot indicators
      renderPanel();   // refresh status badges
      window.showToast('Settings saved');
    } catch (err) {
      window.showToast(err.message, 'error');
    }
  });

  // Wire toggle label updates
  document.getElementById('settings-panel').addEventListener('change', e => {
    if (e.target.type === 'checkbox' && e.target.dataset.key) {
      const label = e.target.closest('.toggle-switch')?.querySelector('.toggle-label');
      if (label) label.textContent = e.target.checked ? 'Enabled' : 'Disabled';
    }
  });

  renderSidebar();
  renderPanel();
}
