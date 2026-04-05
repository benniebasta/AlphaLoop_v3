/**
 * Strategies page — browse, promote, and manage strategy versions.
 *
 * Cards grouped by symbol tabs. Lifecycle: candidate → dry_run → demo → live
 */
import { apiGet, apiPost, apiPut, apiDelete } from '../api.js';

const STATUS_COLORS = {
  candidate: 'var(--amber)',
  dry_run: 'var(--blue)',
  demo: 'var(--purple)',
  live: 'var(--green)',
  retired: 'var(--muted)',
};

const TOOL_LABELS = {
  session_filter: 'Session', news_filter: 'News', volatility_filter: 'Volatility',
  dxy_filter: 'DXY', sentiment_filter: 'Sentiment', risk_filter: 'Risk',
  bos_guard: 'BOS', fvg_guard: 'FVG', vwap_guard: 'VWAP', correlation_guard: 'Corr',
  ema200_filter: 'EMA200',
  macd_filter: 'MACD', bollinger_filter: 'BB', adx_filter: 'ADX',
  volume_filter: 'Volume', swing_structure: 'Swing',
  ema_crossover: 'EMA Cross', rsi_feature: 'RSI Feature', trendilo: 'Trendilo',
  fast_fingers: 'Fast Fingers', choppiness_index: 'Choppiness', alma_filter: 'ALMA',
};

const STATUS_LABELS = {
  candidate: 'Candidate',
  dry_run: 'Dry Run',
  demo: 'Demo',
  live: 'Live',
  retired: 'Retired',
};

const STATUS_ORDER = ['candidate', 'dry_run', 'demo', 'live'];
const LEGACY_SIGNAL_MODES = [
  ['algo_only', 'Algo Only'],
  ['algo_ai', 'Algo + AI'],
];
const AI_SIGNAL_MODES = [
  ['ai_signal', 'AI Signal'],
];
const ALL_SIGNAL_MODES = [...LEGACY_SIGNAL_MODES, ...AI_SIGNAL_MODES];

const DETAIL_TAB_ORDER = ['tune', 'tools', 'validation', 'signal'];

const SIGNAL_SOURCES = [
  { value: 'ema_crossover',      label: 'EMA Crossover' },
  { value: 'macd_crossover',     label: 'MACD Crossover' },
  { value: 'rsi_reversal',       label: 'RSI Reversal' },
  { value: 'bollinger_breakout', label: 'Bollinger Breakout' },
  { value: 'adx_trend',          label: 'ADX Trend' },
  { value: 'bos_confirm',        label: 'BOS Confirm' },
];

const SIGNAL_LOGIC_OPTIONS = ['AND', 'OR', 'MAJORITY'];

const DEFAULT_SCORING_WEIGHTS = {
  trend: 0.30, momentum: 0.25, structure: 0.20, volume: 0.10, volatility: 0.15,
};

const DEFAULT_CONFIDENCE_THRESHOLDS = {
  strong_entry: 75.0, min_entry: 60.0,
};
const DETAIL_TOOL_GROUPS = [
  {
    key: 'pre_signal',
    label: '① Pre-Signal',
    color: 'var(--blue)',
    tools: ['session_filter', 'news_filter', 'volatility_filter'],
  },
  {
    key: 'hard_rules',
    label: '② Hard Rules',
    color: 'var(--green)',
    tools: ['ema200_filter', 'bos_guard', 'fvg_guard', 'tick_jump_guard', 'liq_vacuum_guard', 'vwap_guard'],
  },
  {
    key: 'post_signal',
    label: '③ Post-Signal',
    color: '#9b7fe8',
    tools: ['dxy_filter', 'sentiment_filter', 'risk_filter'],
  },
  {
    key: 'guards',
    label: '④ Guards',
    color: '#e09030',
    tools: ['correlation_guard'],
  },
  {
    key: 'extras',
    label: 'Extras',
    color: '#e09030',
    tools: ['macd_filter', 'bollinger_filter', 'adx_filter', 'volume_filter', 'swing_structure',
            'ema_crossover', 'rsi_feature', 'trendilo', 'fast_fingers', 'choppiness_index', 'alma_filter'],
  },
];

const DETAIL_TUNE_FIELDS = [
  { key: 'ema_fast', label: 'EMA Fast', min: 5, max: 200, step: 1 },
  { key: 'ema_slow', label: 'EMA Slow', min: 10, max: 300, step: 1 },
  { key: 'sl_atr_mult', label: 'SL ATR Mult', min: 0.5, max: 6, step: 0.05 },
  { key: 'tp1_rr', label: 'TP1 RR', min: 0.5, max: 10, step: 0.1 },
  { key: 'tp2_rr', label: 'TP2 RR', min: 1, max: 20, step: 0.1 },
  { key: 'tp1_close_pct', label: 'TP1 Close %', min: 0.05, max: 1, step: 0.05 },
  { key: 'rsi_period', label: 'RSI Period', min: 2, max: 100, step: 1 },
  { key: 'rsi_ob', label: 'RSI OB', min: 50, max: 100, step: 1 },
  { key: 'rsi_os', label: 'RSI OS', min: 0, max: 50, step: 1 },
  { key: 'risk_pct', label: 'Risk %', min: 0.001, max: 0.05, step: 0.001 },
  { key: 'macd_fast', label: 'MACD Fast', min: 2, max: 50, step: 1 },
  { key: 'macd_slow', label: 'MACD Slow', min: 10, max: 100, step: 1 },
  { key: 'macd_signal', label: 'MACD Signal', min: 2, max: 30, step: 1 },
  { key: 'bb_period', label: 'BB Period', min: 5, max: 100, step: 1 },
  { key: 'bb_std_dev', label: 'BB Std Dev', min: 0.5, max: 4, step: 0.1 },
  { key: 'adx_period', label: 'ADX Period', min: 5, max: 50, step: 1 },
  { key: 'adx_min_threshold', label: 'ADX Min Threshold', min: 10, max: 50, step: 1 },
  { key: 'volume_ma_period', label: 'Volume MA Period', min: 5, max: 100, step: 1 },
];

const DETAIL_VALIDATION_FIELDS = [
  { key: 'min_confidence', label: 'Min Confidence', min: 0, max: 1, step: 0.01 },
  { key: 'min_rr', label: 'Min R:R', min: 0.5, max: 10, step: 0.05 },
  { key: 'min_session_score', label: 'Min Session Score', min: 0, max: 1, step: 0.01 },
  { key: 'max_spread_points', label: 'Max Spread (pts)', min: 1, max: 200, step: 1 },
  { key: 'avoid_pre_news_minutes', label: 'Pre-News Minutes', min: 0, max: 240, step: 5 },
  { key: 'avoid_post_news_minutes', label: 'Post-News Minutes', min: 0, max: 120, step: 5 },
  { key: 'rsi_ob', label: 'RSI OB', min: 50, max: 100, step: 1 },
  { key: 'rsi_os', label: 'RSI OS', min: 0, max: 50, step: 1 },
  { key: 'tick_jump_atr_max', label: 'Tick Jump ATR', min: 0.1, max: 5, step: 0.1 },
  { key: 'liq_vacuum_spike_mult', label: 'Liq Vacuum Spike', min: 1, max: 10, step: 0.1 },
  { key: 'liq_vacuum_body_pct', label: 'Liq Vacuum Body %', min: 5, max: 90, step: 1 },
];

const TOOL_PRESETS = {
  ai_signal_default: {
    signal_mode: 'ai_signal',
    session_filter: true,
    news_filter: true,
    volatility_filter: true,
    ema200_filter: true,
    bos_guard: true,
    fvg_guard: true,
    tick_jump_guard: true,
    liq_vacuum_guard: true,
    vwap_guard: true,
    dxy_filter: true,
    sentiment_filter: true,
    risk_filter: true,
    correlation_guard: true,
    macd_filter: false,
    bollinger_filter: false,
    adx_filter: false,
    volume_filter: false,
    swing_structure: false,
  },
  v1_exact: {
    signal_mode: 'algo_ai',
    session_filter: true,
    news_filter: true,
    volatility_filter: true,
    ema200_filter: true,
    bos_guard: true,
    fvg_guard: true,
    tick_jump_guard: true,
    liq_vacuum_guard: true,
    vwap_guard: true,
    dxy_filter: true,
    sentiment_filter: true,
    risk_filter: true,
    correlation_guard: true,
    macd_filter: false,
    bollinger_filter: false,
    adx_filter: false,
    volume_filter: false,
    swing_structure: false,
  },
  balanced: {
    signal_mode: 'algo_ai',
    session_filter: true,
    news_filter: true,
    volatility_filter: true,
    ema200_filter: true,
    bos_guard: true,
    fvg_guard: false,
    tick_jump_guard: true,
    liq_vacuum_guard: true,
    vwap_guard: true,
    dxy_filter: false,
    sentiment_filter: false,
    risk_filter: true,
    correlation_guard: true,
  },
  aggressive: {
    signal_mode: 'algo_ai',
    session_filter: true,
    news_filter: false,
    volatility_filter: true,
    ema200_filter: true,
    bos_guard: true,
    fvg_guard: false,
    tick_jump_guard: false,
    liq_vacuum_guard: false,
    vwap_guard: false,
    dxy_filter: false,
    sentiment_filter: false,
    risk_filter: false,
    correlation_guard: false,
  },
};

const VALIDATION_PRESETS = {
  scalping: {
    mode: 'scalping',
    min_confidence: 0.65,
    min_rr: 1.2,
    min_session_score: 0.70,
    max_spread_points: 35,
    avoid_pre_news_minutes: 30,
    avoid_post_news_minutes: 15,
    check_rsi: true,
    rsi_ob: 75,
    rsi_os: 25,
    check_ema200_trend: true,
    check_bos: true,
    check_fvg: false,
    check_tick_jump: true,
    tick_jump_atr_max: 0.6,
    check_liq_vacuum: true,
    liq_vacuum_spike_mult: 2.0,
    liq_vacuum_body_pct: 25,
    check_regime: true,
    claude_enabled: true,
  },
  swing: {
    mode: 'swing',
    min_confidence: 0.70,
    min_rr: 1.5,
    min_session_score: 0.75,
    max_spread_points: 50,
    avoid_pre_news_minutes: 45,
    avoid_post_news_minutes: 30,
    check_rsi: true,
    rsi_ob: 70,
    rsi_os: 30,
    check_ema200_trend: true,
    check_bos: true,
    check_fvg: true,
    check_tick_jump: true,
    tick_jump_atr_max: 0.8,
    check_liq_vacuum: true,
    liq_vacuum_spike_mult: 2.5,
    liq_vacuum_body_pct: 30,
    check_regime: true,
    claude_enabled: true,
  },
};

let _activeSymbol = ''; // '' = all
let _modelCatalog = []; // loaded from /api/test/models
let _assetCatalog = []; // loaded from /api/assets
let _mt5SymbolCatalog = []; // loaded from /api/test/mt5/symbols
let _mt5SymbolSource = 'fallback';
let _strategySymbolCatalog = []; // merged assets + MT5 symbols for the AI Signal picker
let _detailState = null;
let _detailOriginal = null;
let _detailTab = 'tune';

function getSignalModesForStrategy(strategy) {
  const source = String(strategy?.source || '').trim().toLowerCase();
  if (source === 'ai_signal_discovery' || source === 'ui_ai_signal_card') {
    return AI_SIGNAL_MODES;
  }
  return LEGACY_SIGNAL_MODES;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normalizeSymbolRow(row, fallbackSource = 'fallback') {
  const symbol = String(row?.symbol || '').trim().toUpperCase();
  if (!symbol) return null;

  return {
    symbol,
    display_name: row.display_name || row.name || symbol,
    asset_class: row.asset_class || row.group || '',
    group: row.group || row.asset_class || '',
    mt5_symbol: row.mt5_symbol || row.asset_symbol || '',
    path: row.path || '',
    visible: row.visible,
    selected: row.selected,
    source: row.source || fallbackSource,
    order: Number.isFinite(row.order) ? row.order : 0,
  };
}

function buildStrategySymbolCatalog() {
  const merged = new Map();

  const addRow = (row, order, fallbackSource) => {
    const normalized = normalizeSymbolRow({ ...row, order }, fallbackSource);
    if (!normalized) return;

    const existing = merged.get(normalized.symbol);
    if (!existing) {
      merged.set(normalized.symbol, normalized);
      return;
    }

    merged.set(normalized.symbol, {
      ...existing,
      ...normalized,
      display_name: existing.display_name || normalized.display_name,
      asset_class: existing.asset_class || normalized.asset_class,
      mt5_symbol: existing.mt5_symbol || normalized.mt5_symbol,
      path: existing.path || normalized.path,
      visible: existing.visible ?? normalized.visible,
      selected: existing.selected ?? normalized.selected,
      source: existing.source === 'assets' ? 'assets' : normalized.source,
      order: Math.min(existing.order, normalized.order),
    });
  };

  _assetCatalog.forEach((row, index) => addRow(row, index, 'assets'));
  _mt5SymbolCatalog.forEach((row, index) => addRow(row, 1000 + index, _mt5SymbolSource));

  const rows = [...merged.values()].sort((a, b) => a.order - b.order || a.symbol.localeCompare(b.symbol));
  return rows;
}

const _FALLBACK_ASSETS = [
  { symbol: 'XAUUSD', display_name: 'Gold', asset_class: 'spot_metal' },
  { symbol: 'XAGUSD', display_name: 'Silver', asset_class: 'spot_metal' },
  { symbol: 'BTCUSD', display_name: 'Bitcoin', asset_class: 'crypto' },
  { symbol: 'ETHUSD', display_name: 'Ethereum', asset_class: 'crypto' },
  { symbol: 'EURUSD', display_name: 'Euro/Dollar', asset_class: 'forex_major' },
  { symbol: 'GBPUSD', display_name: 'Cable', asset_class: 'forex_major' },
  { symbol: 'USDJPY', display_name: 'Dollar/Yen', asset_class: 'forex_major' },
  { symbol: 'AUDUSD', display_name: 'Aussie/Dollar', asset_class: 'forex_major' },
  { symbol: 'US30',   display_name: 'Dow Jones', asset_class: 'index' },
  { symbol: 'NAS100', display_name: 'Nasdaq 100', asset_class: 'index' },
];

function buildSymbolOptions(selectedSymbol = '') {
  const requested = String(selectedSymbol || '').trim().toUpperCase();
  const rows = _strategySymbolCatalog.length > 0
    ? _strategySymbolCatalog
    : _FALLBACK_ASSETS.map((a, i) => ({
        symbol: a.symbol,
        display_name: a.display_name,
        asset_symbol: '',
        group: a.asset_class,
        source: 'fallback',
        order: i,
      }));
  const seen = new Set();
  let selectedApplied = false;
  const options = [];

  const addOption = (symbol, label, meta, selected = false) => {
    const value = String(symbol || '').trim().toUpperCase();
    if (!value || seen.has(value)) return;
    seen.add(value);
    const parts = [value];
    if (label) parts.push(label);
    if (meta) parts.push(meta);
    options.push(
      `<option value="${escapeHtml(value)}"${selected ? ' selected' : ''}>${escapeHtml(parts.join(' - '))}</option>`,
    );
  };

  rows.forEach((item, index) => {
    const value = String(item.symbol || '').trim().toUpperCase();
    const label = item.display_name || item.name || item.asset_symbol || value;
    const meta = item.asset_symbol && item.asset_symbol !== value
      ? item.asset_symbol
      : (item.group || item.asset_class || item.source || '');
    const selected = value === requested || (!requested && index === 0);
    if (selected) selectedApplied = true;
    addOption(value, label, meta, selected);
  });

  if (requested && !seen.has(requested)) {
    addOption(requested, 'Current selection', 'custom', true);
    selectedApplied = true;
  }

  if (!selectedApplied && options.length > 0) {
    options[0] = options[0].replace('<option ', '<option selected ');
  }

  if (!options.length) {
    _FALLBACK_ASSETS.forEach((a, i) => addOption(a.symbol, a.display_name, a.asset_class, i === 0));
  }

  return options.join('');
}

export async function render(el) {
  const signalModes = ALL_SIGNAL_MODES;
  const allowedSignalModes = signalModes.map(([mode]) => mode);
  const showCreatePanel = true;
  const createCardSource = 'ui_ai_signal_card';
  const pageTitle = 'Strategies';

  const [modelResult, assetResult, mt5Result] = await Promise.allSettled([
    apiGet('/api/test/models'),
    apiGet('/api/assets'),
    apiGet('/api/test/mt5/symbols'),
  ]);

  _modelCatalog = modelResult.status === 'fulfilled' ? (modelResult.value.models || []) : [];
  _assetCatalog = assetResult.status === 'fulfilled' ? (assetResult.value.assets || []) : [];
  _mt5SymbolCatalog = mt5Result.status === 'fulfilled' ? (mt5Result.value.symbols || []) : [];
  _mt5SymbolSource = mt5Result.status === 'fulfilled' ? (mt5Result.value.source || 'fallback') : 'fallback';
  _strategySymbolCatalog = buildStrategySymbolCatalog();
  const defaultAiSymbol = _activeSymbol || _strategySymbolCatalog[0]?.symbol || _mt5SymbolCatalog[0]?.symbol || 'XAUUSD';
  const symbolCatalogNote = _assetCatalog.length && _mt5SymbolCatalog.length
    ? `Showing ${_assetCatalog.length} configured assets + ${_mt5SymbolCatalog.length} MT5 symbols`
    : _assetCatalog.length
      ? `Showing ${_assetCatalog.length} configured assets`
      : (_mt5SymbolSource === 'mt5'
        ? `Loaded ${_mt5SymbolCatalog.length} MT5 symbols`
        : 'MT5 unavailable, showing fallback symbols');

  el.innerHTML = `
    <div class="page-title">🎯 ${pageTitle}</div>

    ${showCreatePanel ? `
    <div class="panel-card" style="margin-bottom:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <div>
          <div class="section-label" style="margin:0">🤖 SignalForge™</div>
          <div style="font-size:0.75rem;color:var(--muted);margin-top:2px">Build and deploy an AI-driven signal card</div>
        </div>
        <button class="btn btn-sm" id="ai-signal-toggle">✦ New Signal Card</button>
      </div>
      <div id="ai-signal-panel" style="display:none;margin-top:12px">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px">
          <div class="form-group">
            <label class="field-label">Symbol</label>
            <select id="ai-signal-symbol" class="field-input">
              ${buildSymbolOptions(defaultAiSymbol)}
            </select>
            <div style="font-size:0.72rem;color:var(--muted);margin-top:4px">
              ${symbolCatalogNote}
            </div>
          </div>
          <div class="form-group">
            <label class="field-label">Name</label>
            <input id="ai-signal-name" class="field-input" placeholder="Leave blank to auto-generate">
          </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-top:10px">
          <div class="form-group">
            <label class="field-label">Signal Instruction</label>
            <textarea id="ai-signal-instruction" class="field-input" rows="5" placeholder="Leave blank for a starter signal prompt template"></textarea>
          </div>
          <div class="form-group">
            <label class="field-label">Validator Instruction</label>
            <textarea id="ai-validator-instruction" class="field-input" rows="5" placeholder="Leave blank for a starter validator prompt template"></textarea>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px">
          <button class="btn btn-primary" id="ai-signal-create">🚀 Deploy Signal Card</button>
          <span style="font-size:0.75rem;color:var(--muted)">Blank fields will auto-generate a strategy name and starter prompts.</span>
        </div>
      </div>
    </div>` : ''}

    <!-- Symbol tabs -->
    <div class="strat-symbol-tabs" id="strat-symbol-tabs">
      <button class="strat-tab active" data-symbol="">All</button>
    </div>

    <!-- Status filter pills + lifecycle counts -->
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px">
      <div class="strat-status-pills">
        <button class="strat-status-pill active" data-status="">All</button>
        <button class="strat-status-pill" data-status="candidate">🟡 Candidate <span id="count-candidate" style="opacity:0.7">0</span></button>
        <button class="strat-status-pill" data-status="dry_run">🔵 Dry Run <span id="count-dry_run" style="opacity:0.7">0</span></button>
        <button class="strat-status-pill" data-status="demo">🟣 Demo <span id="count-demo" style="opacity:0.7">0</span></button>
        <button class="strat-status-pill" data-status="live">🟢 Live <span id="count-live" style="opacity:0.7">0</span></button>
      </div>
      <button id="strat-refresh" class="btn btn-sm">↻ Refresh</button>
    </div>
    <input type="hidden" id="strat-filter-status" value="">

    <div class="strat-grid" id="strat-cards"></div>
  `;

  const filterStat = el.querySelector('#strat-filter-status');
  const refreshBtn = el.querySelector('#strat-refresh');
  const tabsContainer = el.querySelector('#strat-symbol-tabs');

  // Create detail panel on document.body so position:fixed always works
  let detailBackdrop = document.getElementById('strategy-detail-backdrop');
  let detailPanel = document.getElementById('strategy-detail-panel');
  if (!detailBackdrop) {
    detailBackdrop = document.createElement('div');
    detailBackdrop.id = 'strategy-detail-backdrop';
    detailBackdrop.className = 'sd-backdrop';
    document.body.appendChild(detailBackdrop);
  }
  if (!detailPanel) {
    detailPanel = document.createElement('aside');
    detailPanel.id = 'strategy-detail-panel';
    detailPanel.className = 'sd-panel';
    detailPanel.innerHTML = '<div id="strategy-detail-shell"></div>';
    document.body.appendChild(detailPanel);
  }

  const aiSignalToggle = el.querySelector('#ai-signal-toggle');
  const aiSignalPanel = el.querySelector('#ai-signal-panel');
  if (showCreatePanel && aiSignalToggle && aiSignalPanel) {
    aiSignalToggle.addEventListener('click', () => {
      const visible = aiSignalPanel.style.display !== 'none';
      aiSignalPanel.style.display = visible ? 'none' : 'block';
      aiSignalToggle.textContent = visible ? '✦ New Signal Card' : '✦ Hide';
    });
  }

  if (detailBackdrop) {
    detailBackdrop.addEventListener('click', closeStrategyDetail);
  }

  async function load() {
    const params = new URLSearchParams();
    if (_activeSymbol) params.set('symbol', _activeSymbol);
    if (filterStat.value) params.set('status', filterStat.value);
    params.set('signal_mode', allowedSignalModes.join(','));

    const data = await apiGet(`/api/strategies?${params}`);
    const strategies = (data.strategies || []).filter(s =>
      allowedSignalModes.includes((s.signal_mode || 'algo_ai').trim().toLowerCase()),
    );

    // Update counts
    const counts = { candidate: 0, dry_run: 0, demo: 0, live: 0 };
    strategies.forEach(s => {
      if (counts[s.status] !== undefined) counts[s.status]++;
    });
    Object.entries(counts).forEach(([k, v]) => {
      const el = document.getElementById(`count-${k}`);
      if (el) el.textContent = v;
    });

    // Build symbol tabs from data
    const symbols = [...new Set(strategies.map(s => s.symbol))].sort();
    buildSymbolTabs(symbols);

    const container = document.getElementById('strat-cards');
    if (!container) return;

    if (strategies.length === 0) {
      container.innerHTML = `
          <div class="card" style="text-align:center;padding:2rem;grid-column:1/-1">
          <div style="font-size:1.5rem;margin-bottom:0.5rem">&#127919;</div>
          <div style="color:var(--muted)">No strategy versions found</div>
          <div style="color:var(--muted);font-size:0.8rem;margin-top:0.25rem">
            Launch a SignalForge™ card to begin AI signal discovery
          </div>
        </div>
      `;
      return;
    }

    container.innerHTML = strategies.map(s => {
      const sum = s.summary || {};
      const color = STATUS_COLORS[s.status] || 'var(--muted)';
      const label = STATUS_LABELS[s.status] || s.status;
      const nextStatus = STATUS_ORDER[STATUS_ORDER.indexOf(s.status) + 1];
      const canPromote = nextStatus && s.status !== 'live';
      const canActivate = ['dry_run', 'demo', 'live'].includes(s.status);
      const isDiscovery = ['ai_signal_discovery', 'ui_ai_signal_card'].includes((s.source || '').trim().toLowerCase());
      const isAiSignal = (s.signal_mode || '').trim().toLowerCase() === 'ai_signal';
      const wr = (sum.win_rate||0);
      const sharpe = (sum.sharpe||0);
      const pnl = (sum.total_pnl||0);
      const dd = (sum.max_dd_pct||0);

      const tf = sum.timeframe || '';
      const btDays = sum.days || '';
      const capital = sum.initial_capital || '';

      const cardSignalModes = getSignalModesForStrategy(s);
      const currentMode = cardSignalModes.some(([mode]) => mode === (s.signal_mode || '').trim().toLowerCase())
        ? (s.signal_mode || cardSignalModes[0][0])
        : cardSignalModes[0][0];

      return `
        <div class="strat-card-box" style="border-top:3px solid ${color}">
          <div class="strat-card-header">
            <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              <strong style="font-size:0.8rem">${s.name || s.symbol + ' v' + s.version}</strong>
            </div>
            <span class="badge" style="background:${color};color:#000;font-size:0.6rem;padding:2px 6px;flex-shrink:0">${label}</span>
          </div>
          ${tf || btDays || capital || s.seed_hash ? `
          <div style="font-size:0.68rem;color:var(--muted);padding:0.15rem 0.5rem 0;display:flex;gap:0.6rem;flex-wrap:wrap">
            ${tf ? `<span>📊 ${tf}</span>` : ''}
            ${btDays ? `<span>📅 ${btDays}d</span>` : ''}
            ${capital ? `<span>💰 $${Number(capital).toLocaleString()}</span>` : ''}
            ${s.seed_hash ? `<span title="Seed backtest: ${s.seed_hash}">🔗 seed: ${s.seed_hash.slice(0,8)}</span>` : ''}
          </div>` : ''}
          <div class="strat-card-metrics">
            <div class="strat-metric">
              <div class="strat-metric-val">${sum.total_trades || 0}</div>
              <div class="strat-metric-lbl">Trades</div>
            </div>
            <div class="strat-metric">
              <div class="strat-metric-val" style="color:${wr>=0.5?'var(--green)':'var(--red)'}">${(wr*100).toFixed(1)}%</div>
              <div class="strat-metric-lbl">Win Rate</div>
            </div>
            <div class="strat-metric">
              <div class="strat-metric-val" style="color:${sharpe>=0.5?'var(--green)':'var(--red)'}">${sharpe.toFixed(2)}</div>
              <div class="strat-metric-lbl">Sharpe</div>
            </div>
            <div class="strat-metric">
              <div class="strat-metric-val" style="color:${pnl>=0?'var(--green)':'var(--red)'}">$${pnl.toFixed(0)}</div>
              <div class="strat-metric-lbl">P&L</div>
            </div>
            <div class="strat-metric">
              <div class="strat-metric-val" style="color:var(--red)">${dd.toFixed(1)}%</div>
              <div class="strat-metric-lbl">Max DD</div>
            </div>
          </div>
          ${s.params ? `
          <div style="font-size:0.7rem;color:var(--muted);padding:0 0.5rem;margin-bottom:0.3rem">
            EMA ${s.params.ema_fast||21}/${s.params.ema_slow||55} | SL ${(s.params.sl_atr_mult||2).toFixed(1)} ATR | TP1 ${(s.params.tp1_rr||2).toFixed(1)} RR
          </div>` : ''}
          <div class="strat-tools-row">${s.tools
            ? Object.entries(s.tools).filter(([_,on]) => on).map(([name]) =>
                `<span class="strat-tool-badge">${TOOL_LABELS[name] || name}</span>`
              ).join('') || '<span style="font-size:0.68rem;color:var(--muted)">No filters</span>'
            : '<span style="font-size:0.68rem;color:var(--muted)">No filters</span>'
          }</div>
          ${cardSignalModes.length > 1 ? `
          <div class="signal-mode-toggle strat-signal-mode" data-sym="${s.symbol}" data-ver="${s.version}" style="margin:0.3rem 0.5rem 0.1rem;border-radius:6px;overflow:hidden">
            ${cardSignalModes.map(([mode, label]) => `
              <button class="signal-mode-btn ${currentMode===mode?'active':''}" data-mode="${mode}" style="font-size:0.68rem;padding:5px 0">${label}</button>
            `).join('')}
          </div>` : `
          <div style="margin:0.3rem 0.5rem 0.1rem">
            <span class="badge" style="background:${color};color:#000;font-size:0.65rem">${cardSignalModes[0]?.[1] || 'AI Signal'}</span>
          </div>`}
          ${currentMode === 'algo_ai' ? `
          <div style="font-size:0.65rem;color:var(--amber);padding:0.15rem 0.5rem;line-height:1.3">
            ⚠ Backtest metrics reflect algo-only — AI validator not modelled.
          </div>` : ''}
          <div class="strat-models-toggle" data-toggle-models="${s.symbol}_${s.version}" style="font-size:0.7rem;color:var(--blue);padding:0.2rem 0.5rem;cursor:pointer;${currentMode==='algo_only'?'display:none':''}">
            ▶ AI Models
          </div>
          <div class="strat-models-panel" id="models-${s.symbol}_${s.version}" style="display:none;padding:0.3rem 0.5rem;border-top:1px solid var(--border)">
            ${_modelCatalog.length === 0 ? `
              <div style="font-size:0.72rem;color:var(--muted);padding:0.3rem 0">
                No API keys configured — add keys in <strong>Settings</strong> to enable model selection.
              </div>` :
            [
              ['signal','Signal'],['validator','Validator'],['research','Research'],
              ['param_suggest','Optimizer'],['regime','Regime'],['fallback','Fallback']
            ].map(([role, label]) => {
              const current = s.ai_models?.[role] || '';
              const opts = _modelCatalog.map(m =>
                `<option value="${m.id}" ${current === m.id ? 'selected' : ''}>${m.display_name}</option>`
              ).join('');
              const hasVal = current && _modelCatalog.some(m => m.id === current);
              const customOpt = current && !hasVal ? `<option value="${current}" selected>${current} (key not set)</option>` : '';
              return `
                <div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.3rem">
                  <span style="font-size:0.68rem;color:var(--muted);width:65px;flex-shrink:0">${label}</span>
                  <select class="field-input strat-model-sel" data-sym="${s.symbol}" data-ver="${s.version}" data-role="${role}" style="font-size:0.72rem;padding:2px 4px;flex:1">
                    <option value="">Use Default</option>
                    ${customOpt}${opts}
                  </select>
                </div>`;
            }).join('')}
            <button class="btn btn-sm strat-save-models" data-symbol="${s.symbol}" data-version="${s.version}" style="font-size:0.68rem;padding:2px 8px;margin-top:0.2rem">Save Models</button>
            <span class="strat-model-hint" data-hint="${s.symbol}_${s.version}" style="font-size:0.7rem;margin-left:0.5rem"></span>
          </div>
          ${((s.status === 'candidate' || s.status === 'dry_run')) ? `
          <div class="strat-overlay-toggle" data-sym="${s.symbol}" data-ver="${s.version}" style="font-size:0.7rem;color:var(--muted);padding:0.2rem 0.5rem;cursor:pointer;border-top:1px solid var(--border)">
            ▶ 🧪 Dry Run Overlay
          </div>
          <div class="strat-overlay-panel" id="overlay-${s.symbol}-${s.version}" style="display:none;padding:0.3rem 0.5rem;border-top:1px solid var(--border);flex-wrap:wrap;gap:0.3rem">
            ${Object.keys(TOOL_LABELS).filter(t => !s.tools || !s.tools[t]).map(t =>
              `<label style="font-size:0.68rem;display:flex;align-items:center;gap:0.2rem;cursor:pointer">
                <input type="checkbox" class="overlay-cb" data-tool="${t}"> ${TOOL_LABELS[t]}
              </label>`
            ).join('')}
            <div style="width:100%;margin-top:0.2rem">
              <button class="btn btn-sm save-overlay-btn" data-sym="${s.symbol}" data-ver="${s.version}" style="font-size:0.68rem;padding:2px 8px">Save Overlay</button>
              <span class="overlay-save-hint" style="font-size:0.7rem;margin-left:0.4rem"></span>
            </div>
          </div>` : ''}
          <div class="strat-card-actions">
            <button class="btn btn-sm strat-detail" data-symbol="${s.symbol}" data-version="${s.version}" style="background:var(--surface);border:1px solid var(--border)">Detail</button>
            ${canPromote ? `<button class="btn btn-sm strat-promote" data-symbol="${s.symbol}" data-version="${s.version}" style="background:var(--blue)">Promote</button>` : ''}
            ${canActivate ? `<button class="btn btn-sm strat-activate" data-symbol="${s.symbol}" data-version="${s.version}" style="background:var(--green)">Activate</button>` : ''}
            ${s.status !== 'live' && s.status !== 'demo' ? `<button class="btn btn-sm strat-delete" data-symbol="${s.symbol}" data-version="${s.version}" data-status="${s.status}" style="background:var(--red)" title="Delete">&#10005;</button>` : ''}
          </div>
          ${canPromote && s.status === 'candidate' && !isDiscovery ? `
          <div style="font-size:0.62rem;color:var(--muted);padding:0.15rem 0.5rem 0.3rem;line-height:1.4">
            Requires: ${sum.total_trades || 0}/40 trades, Sharpe ${(sharpe || 0).toFixed(2)}/1.0, WR ${((wr||0)*100).toFixed(0)}%/42%
          </div>` : ''}
          ${canPromote && isDiscovery ? `
          <div style="font-size:0.62rem;color:var(--green);padding:0.15rem 0.5rem 0.3rem">
            AI Signal — candidate gate bypassed
          </div>` : ''}
          <div class="strat-promote-result" id="promote-result-${s.symbol}_${s.version}" style="display:none;font-size:0.68rem;padding:0.3rem 0.5rem;border-top:1px solid var(--border)"></div>
        </div>
      `;
    }).join('');

    wireButtons(container);
  }

  function buildSymbolTabs(symbols) {
    // Keep "All" + one tab per symbol
    const existing = tabsContainer.querySelectorAll('.strat-tab[data-symbol]');
    const existingSymbols = new Set([...existing].map(t => t.dataset.symbol));
    const needed = new Set(['', ...symbols]);

    // Add missing tabs
    for (const sym of symbols) {
      if (!existingSymbols.has(sym)) {
        const btn = document.createElement('button');
        btn.className = 'strat-tab';
        btn.dataset.symbol = sym;
        btn.textContent = sym;
        btn.classList.toggle('active', _activeSymbol === sym);
        btn.addEventListener('click', () => {
          _activeSymbol = sym;
          tabsContainer.querySelectorAll('.strat-tab').forEach(t => t.classList.remove('active'));
          btn.classList.add('active');
          load();
        });
        tabsContainer.appendChild(btn);
      }
    }

    tabsContainer.querySelectorAll('.strat-tab[data-symbol]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.symbol === _activeSymbol);
    });
    const allTab = tabsContainer.querySelector('.strat-tab[data-symbol=""]');
    if (allTab) allTab.classList.toggle('active', !_activeSymbol);
  }

  // Wire "All" tab
  tabsContainer.querySelector('.strat-tab').addEventListener('click', () => {
    _activeSymbol = '';
    tabsContainer.querySelectorAll('.strat-tab').forEach(t => t.classList.remove('active'));
    tabsContainer.querySelector('.strat-tab').classList.add('active');
    load();
  });

  function wireButtons(container) {
    // Overlay toggle + save
    container.querySelectorAll('.strat-overlay-toggle').forEach(toggle => {
      toggle.addEventListener('click', async () => {
        const sym = toggle.dataset.sym, ver = toggle.dataset.ver;
        const panel = document.getElementById(`overlay-${sym}-${ver}`);
        if (!panel) return;
        const hidden = panel.style.display === 'none';
        panel.style.display = hidden ? 'flex' : 'none';
        toggle.textContent = (hidden ? '▼' : '▶') + ' 🧪 Dry Run Overlay';
        // Load saved overlay on first open
        if (hidden && !panel.dataset.loaded) {
          try {
            const data = await apiGet(`/api/strategies/${sym}/v${ver}/overlay`);
            (data.extra_tools || []).forEach(t => {
              const cb = panel.querySelector(`input[data-tool="${t}"]`);
              if (cb) cb.checked = true;
            });
            panel.dataset.loaded = '1';
          } catch (_) {}
        }
      });
    });
    container.querySelectorAll('.save-overlay-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.sym, ver = btn.dataset.ver;
        const panel = document.getElementById(`overlay-${sym}-${ver}`);
        const checked = [...panel.querySelectorAll('.overlay-cb:checked')].map(cb => cb.dataset.tool);
        try {
          await apiPut(`/api/strategies/${sym}/v${ver}/overlay`, { extra_tools: checked });
          const hint = btn.nextElementSibling;
          hint.textContent = '✓ Saved'; hint.style.color = 'var(--green)';
          setTimeout(() => { hint.textContent = ''; }, 2000);
        } catch (e) {
          window.showToast(e.message, 'error');
        }
      });
    });

    container.querySelectorAll('.strat-detail').forEach(btn => {
      btn.addEventListener('click', () => {
        openStrategyDetail(btn.dataset.symbol, btn.dataset.version);
      });
    });

    container.querySelectorAll('.strat-promote').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.symbol;
        const ver = btn.dataset.version;
        const resultEl = document.getElementById(`promote-result-${sym}_${ver}`);
        btn.disabled = true;
        btn.textContent = 'Checking...';

        try {
          // Step 1: Evaluate eligibility
          const evalResult = await apiPost(`/api/strategies/${sym}/v${ver}/evaluate`, { cycles_completed: 0 });

          if (!evalResult.eligible) {
            btn.textContent = 'Not Eligible';
            btn.style.background = 'var(--amber)';
            if (resultEl) {
              resultEl.style.display = 'block';
              resultEl.style.color = 'var(--amber)';
              resultEl.innerHTML = (evalResult.reasons || []).map(r =>
                `<div style="padding:2px 0">&#9888; ${r}</div>`
              ).join('');
            }
            setTimeout(() => { btn.textContent = 'Promote'; btn.disabled = false; btn.style.background = 'var(--blue)'; }, 4000);
            return;
          }

          // Step 2: Eligible — proceed with promotion
          btn.textContent = 'Promoting...';
          const result = await apiPost(`/api/strategies/${sym}/v${ver}/promote`, { cycles_completed: 0 });
          if (result.promoted) {
            showToast(`Promoted ${sym} v${ver} to ${result.new_status}`);
            if (resultEl) { resultEl.style.display = 'none'; }
          } else {
            if (resultEl) {
              resultEl.style.display = 'block';
              resultEl.style.color = 'var(--red)';
              resultEl.innerHTML = (result.reasons || []).map(r =>
                `<div style="padding:2px 0">&#10007; ${r}</div>`
              ).join('');
            }
          }
        } catch (err) {
          showToast(`Promote failed: ${err.message}`, 'error');
        }
        load();
      });
    });

    container.querySelectorAll('.strat-activate').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.symbol;
        const ver = btn.dataset.version;
        if (!confirm(`Activate ${sym} v${ver} for live trading?`)) return;
        btn.disabled = true;
        try {
          await apiPost(`/api/strategies/${sym}/v${ver}/activate`, {});
          showToast(`Activated ${sym} v${ver}`);
        } catch (err) {
          showToast(`Activation failed: ${err.message}`, 'error');
        }
        load();
      });
    });

    // Wire signal mode pills
    container.querySelectorAll('.strat-signal-mode').forEach(group => {
      const sym = group.dataset.sym, ver = group.dataset.ver;
      group.querySelectorAll('.signal-mode-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const mode = btn.dataset.mode;
          group.querySelectorAll('.signal-mode-btn').forEach(b => b.classList.toggle('active', b === btn));
          // Show/hide AI Models toggle row
          const modelsToggle = container.querySelector(`[data-toggle-models="${sym}_${ver}"]`);
          if (modelsToggle) modelsToggle.style.display = mode === 'algo_only' ? 'none' : '';
          // Save immediately
          try {
            await apiPut(`/api/strategies/${sym}/v${ver}/models`, { signal_mode: mode });
          } catch (e) {
            showToast(`Failed to save signal mode: ${e.message}`, 'error');
          }
        });
      });
    });

    // Wire AI Models toggle
    container.querySelectorAll('.strat-models-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.toggleModels;
        const panel = document.getElementById(`models-${key}`);
        if (!panel) return;
        const visible = panel.style.display !== 'none';
        panel.style.display = visible ? 'none' : 'block';
        btn.textContent = btn.textContent.replace(/[▶▼]/, visible ? '▶' : '▼');
      });
    });

    // Wire Save Models buttons
    container.querySelectorAll('.strat-save-models').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.symbol;
        const ver = btn.dataset.version;
        const models = {};
        container.querySelectorAll(`.strat-model-sel[data-sym="${sym}"][data-ver="${ver}"]`).forEach(sel => {
          models[sel.dataset.role] = sel.value;
        });
        btn.disabled = true;
        try {
          await apiPut(`/api/strategies/${sym}/v${ver}/models`, models);
          const hint = container.querySelector(`[data-hint="${sym}_${ver}"]`);
          if (hint) { hint.textContent = '✓ Saved'; hint.style.color = 'var(--green)'; setTimeout(() => hint.textContent = '', 2000); }
          showToast(`Models saved for ${sym} v${ver}`);
        } catch (err) {
          showToast(`Save failed: ${err.message}`, 'error');
        }
        btn.disabled = false;
      });
    });

    container.querySelectorAll('.strat-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.symbol;
        const ver = btn.dataset.version;
        const status = btn.dataset.status || 'candidate';
        const statusLabel = STATUS_LABELS[status] || status;
        const msg = status === 'dry_run'
          ? `${sym} v${ver} is in ${statusLabel} mode. Deleting will stop any active dry-run. Continue?`
          : `Delete ${sym} v${ver} (${statusLabel})?`;
        if (!confirm(msg)) return;
        btn.disabled = true;
        try {
          await apiDelete(`/api/strategies/${sym}/v${ver}`);
          showToast(`Deleted ${sym} v${ver}`);
        } catch (err) {
          showToast(`Delete failed: ${err.message}`, 'error');
        }
        load();
      });
    });
  }

  function currentStrategy() {
    return _detailState || _detailOriginal || null;
  }

  function detailId(prefix, key) {
    return `${prefix}-${key}`;
  }

  function readNum(id, fallback = 0) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const value = parseFloat(el.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function readBool(id, fallback = false) {
    const el = document.getElementById(id);
    return el ? !!el.checked : fallback;
  }

  function setValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox' || el.type === 'radio') {
      el.checked = !!value;
    } else {
      el.value = value ?? '';
    }
  }

  function openStrategyDetail(sym, ver) {
    apiGet(`/api/strategies/${sym}/v${ver}`)
      .then(data => {
        _detailState = data;
        _detailOriginal = JSON.parse(JSON.stringify(data));
        _detailTab = 'tune';
        renderDetailShell();
      })
      .catch(err => showToast(`Failed to load detail: ${err.message}`, 'error'));
  }

  function closeStrategyDetail() {
    _detailState = null;
    _detailOriginal = null;
    const backdrop = document.getElementById('strategy-detail-backdrop');
    const panel = document.getElementById('strategy-detail-panel');
    const shell = document.getElementById('strategy-detail-shell');
    if (backdrop) backdrop.classList.remove('open');
    if (panel) panel.classList.remove('open');
    if (shell) shell.innerHTML = '';
  }

  function applyToolPreset(presetName) {
    const preset = TOOL_PRESETS[presetName];
    if (!preset) return;
    const current = currentStrategy();
    if (current) {
      current.tools = current.tools || {};
    }
    Object.entries(preset).forEach(([key, value]) => {
      if (key === 'signal_mode') {
        const sigModeBtns = document.querySelectorAll('.detail-signal-mode');
        const hasModeButton = [...sigModeBtns].some(b => b.dataset.mode === value);
        if (hasModeButton) {
          sigModeBtns.forEach(b => { b.classList.toggle('active', b.dataset.mode === value); });
          if (current) current.signal_mode = value;
        }
        return;
      }
      const el = document.getElementById(detailId('tool', key));
      if (el) el.checked = !!value;
      if (current && current.tools) current.tools[key] = !!value;
    });
    renderDetailShell();
  }

  function applyValidationPreset(presetName) {
    const preset = VALIDATION_PRESETS[presetName];
    if (!preset) return;
    const current = currentStrategy();
    if (current) {
      current.validation = current.validation || {};
    }
    Object.entries(preset).forEach(([key, value]) => {
      if (key === 'mode') {
        const radios = document.querySelectorAll('input[name="validation-mode"]');
        radios.forEach(r => { r.checked = r.value === value; });
        if (current && current.validation) current.validation.mode = value;
        return;
      }
      if (typeof value === 'boolean') {
        const el = document.getElementById(detailId('validation-bool', key));
        if (el) el.checked = value;
        if (current && current.validation) current.validation[key] = value;
      } else {
        const num = document.getElementById(detailId('validation-num', key));
        const rng = document.getElementById(detailId('validation-range', key));
        if (num) num.value = value;
        if (rng) rng.value = value;
        if (current && current.validation) current.validation[key] = value;
      }
    });
    renderDetailShell();
  }

  const TAB_ICONS = {
    tune: '\u2699',       // gear
    tools: '\u{1F527}',    // wrench
    validation: '\u{1F6E1}', // shield
    signal: '\u26A1',     // bolt
  };

  const VALIDATION_BOOL_ICONS = {
    check_news: '\u{1F4F0}',
    check_rsi: '\u{1F4C8}',
    check_ema200_trend: '\u{1F4C9}',
    check_bos: '\u{1F9F1}',
    check_fvg: '\u2B1B',
    check_tick_jump: '\u26A0',
    check_liq_vacuum: '\u{1F300}',
    check_regime: '\u{1F3AF}',
    check_setup_type: '\u{1F50D}',
    claude_enabled: '\u{1F916}',
  };

  const VALIDATION_BOOL_LABELS = {
    check_news: 'News',
    check_rsi: 'RSI',
    check_ema200_trend: 'EMA200 Trend',
    check_bos: 'BOS',
    check_fvg: 'FVG',
    check_tick_jump: 'Tick Jump',
    check_liq_vacuum: 'Liq Vacuum',
    check_regime: 'Regime',
    check_setup_type: 'Setup Type',
    claude_enabled: 'AI Validator',
  };

  const TOOL_ICONS = {
    session_filter: '\u{1F553}', news_filter: '\u{1F4F0}', volatility_filter: '\u{1F30A}',
    dxy_filter: '\u{1F4B5}', sentiment_filter: '\u{1F4AC}', risk_filter: '\u{1F6E1}',
    bos_guard: '\u{1F9F1}', fvg_guard: '\u2B1B', vwap_guard: '\u{1F4CA}', correlation_guard: '\u{1F517}',
    ema200_filter: '\u{1F4C9}', macd_filter: '\u{1F4C8}', bollinger_filter: '\u{1F4CF}',
    adx_filter: '\u{1F4AA}', volume_filter: '\u{1F50A}', swing_structure: '\u{1F3D4}',
    ema_crossover: '\u2194', rsi_feature: '\u{1F4CA}', trendilo: '\u{1F30C}',
    fast_fingers: '\u{1F446}', choppiness_index: '\u{1F32A}', alma_filter: '\u{1F52E}',
    tick_jump_guard: '\u26A0', liq_vacuum_guard: '\u{1F300}',
  };

  function renderDetailShell() {
    const shell = document.getElementById('strategy-detail-shell');
    const backdrop = document.getElementById('strategy-detail-backdrop');
    const panel = document.getElementById('strategy-detail-panel');
    const strat = currentStrategy();
    if (!shell || !backdrop || !panel) return;

    if (!strat) {
      backdrop.classList.remove('open');
      panel.classList.remove('open');
      shell.innerHTML = '';
      return;
    }

    backdrop.classList.add('open');
    panel.classList.add('open');

    const params = strat.params || {};
    const validation = strat.validation || {};
    const tools = strat.tools || {};
    const cardSignalModes = getSignalModesForStrategy(strat);
    const currentMode = cardSignalModes.some(([mode]) => mode === (strat.signal_mode || '').trim().toLowerCase())
      ? strat.signal_mode
      : cardSignalModes[0][0];
    const validationMode = validation.mode || 'swing';

    /* ── Tab bar ── */
    const tabs = DETAIL_TAB_ORDER.map(tab => `
      <button class="sd-tab detail-tab ${_detailTab === tab ? 'active' : ''}" data-detail-tab="${tab}">
        <span class="sd-tab-icon">${TAB_ICONS[tab] || ''}</span>
        ${tab.charAt(0).toUpperCase() + tab.slice(1)}
      </button>
    `).join('');

    /* ── Tune tab ── */
    const tune = `
      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u2699</span> Parameter Tuning
        </div>
        ${DETAIL_TUNE_FIELDS.map(field => {
          const value = params[field.key] ?? field.min;
          return `
            <div class="sd-field-row">
              <div class="sd-field-top">
                <div>
                  <div class="sd-field-label">${field.label}</div>
                  <div class="sd-field-key">${field.key}</div>
                </div>
                <input id="${detailId('tune-num', field.key)}" type="number" min="${field.min}" max="${field.max}" step="${field.step}" value="${value}" class="sd-field-num">
              </div>
              <input id="${detailId('tune-range', field.key)}" type="range" min="${field.min}" max="${field.max}" step="${field.step}" value="${value}" class="sd-field-range">
            </div>
          `;
        }).join('')}
      </div>
    `;

    /* ── Tools tab ── */
    const toolsHtml = DETAIL_TOOL_GROUPS.map(group => `
      <div class="sd-section">
        <div class="sd-tool-group">
          <div class="sd-tool-group-header" style="color:${group.color}">
            <span>${group.label}</span>
            ${group.key === 'pre_signal' ? '<button class="btn btn-sm detail-preset-v1 sd-preset-btn" style="font-size:10px;padding:3px 8px">V1 Exact</button>' : ''}
          </div>
          ${group.tools.map(name => {
            const on = tools[name] !== false;
            return `
              <label class="sd-tool-item">
                <div style="display:flex;align-items:center;gap:8px">
                  <span style="font-size:14px;width:20px;text-align:center">${TOOL_ICONS[name] || '\u{1F50C}'}</span>
                  <div>
                    <div class="sd-tool-name">${escapeHtml(TOOL_LABELS[name] || name)}</div>
                    <div class="sd-tool-key">${name}</div>
                  </div>
                </div>
                <input id="${detailId('tool', name)}" type="checkbox" ${on ? 'checked' : ''}>
              </label>
            `;
          }).join('')}
        </div>
      </div>
    `).join('');

    /* ── Validation tab ── */
    const validationHtml = `
      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u{1F3AF}</span> Mode Preset
        </div>
        <div class="sd-mode-bar">
          <button class="sd-mode-btn detail-validation-preset ${validationMode === 'scalping' ? 'active' : ''}" data-preset="scalping">\u26A1 Scalping</button>
          <button class="sd-mode-btn detail-validation-preset ${validationMode === 'swing' ? 'active' : ''}" data-preset="swing">\u{1F4C8} Swing</button>
        </div>
      </div>

      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u{1F916}</span> Validator Model
        </div>
        ${(() => {
          const curVal = strat.ai_models?.validator || '';
          const hasVal = curVal && _modelCatalog.some(m => m.id === curVal);
          const customOpt = curVal && !hasVal ? `<option value="${curVal}" selected>${curVal} (key not set)</option>` : '';
          return `<select id="${detailId('validation-select', 'validator_model')}" class="sd-select">
            <option value="">Use default</option>
            ${customOpt}${_modelCatalog.map(m => `<option value="${m.id}" ${curVal === m.id ? 'selected' : ''}>${m.display_name}</option>`).join('')}
          </select>`;
        })()}
      </div>

      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u{1F4CF}</span> Thresholds
        </div>
        ${DETAIL_VALIDATION_FIELDS.map(field => {
          const value = validation[field.key] ?? field.min;
          return `
            <div class="sd-field-row">
              <div class="sd-field-top">
                <div>
                  <div class="sd-field-label">${field.label}</div>
                  <div class="sd-field-key">${field.key}</div>
                </div>
                <input id="${detailId('validation-num', field.key)}" type="number" min="${field.min}" max="${field.max}" step="${field.step}" value="${value}" class="sd-field-num">
              </div>
              <input id="${detailId('validation-range', field.key)}" type="range" min="${field.min}" max="${field.max}" step="${field.step}" value="${value}" class="sd-field-range">
            </div>
          `;
        }).join('')}
      </div>

      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u2705</span> Guard Checks
        </div>
        <div class="sd-bool-grid">
          ${['check_news','check_rsi','check_ema200_trend','check_bos','check_fvg','check_tick_jump','check_liq_vacuum','check_regime','check_setup_type','claude_enabled'].map(key => `
            <label class="sd-bool-item">
              <span>${VALIDATION_BOOL_ICONS[key] || '\u2714'} ${VALIDATION_BOOL_LABELS[key] || key}</span>
              <input id="${detailId('validation-bool', key)}" type="checkbox" ${validation[key] !== false ? 'checked' : ''}>
            </label>
          `).join('')}
        </div>
      </div>
    `;

    /* ── Signal tab ── */
    const isAlgoMode = currentMode === 'algo_only' || currentMode === 'algo_ai';
    const signalRules = (params.signal_rules || [{ source: 'ema_crossover' }]);
    const activeSourceSet = new Set(signalRules.map(r => r.source));
    const signalLogic = params.signal_logic || 'AND';
    const scoringW = strat.scoring_weights && Object.keys(strat.scoring_weights).length ? strat.scoring_weights : DEFAULT_SCORING_WEIGHTS;
    const confThresh = strat.confidence_thresholds && Object.keys(strat.confidence_thresholds).length ? strat.confidence_thresholds : DEFAULT_CONFIDENCE_THRESHOLDS;

    const signalHtml = `
      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u26A1</span> Signal Mode
        </div>
        <div class="sd-mode-bar">
          ${cardSignalModes.length > 1 ? cardSignalModes.map(([mode, label]) => `
            <button class="sd-mode-btn detail-signal-mode ${currentMode === mode ? 'active' : ''}" data-mode="${mode}">${label}</button>
          `).join('') : `
            <div style="padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r-xl);font-size:var(--fs-md);color:var(--muted)">
              \u{1F512} AI Signal mode locked for this strategy
            </div>`}
        </div>
      </div>

      ${isAlgoMode ? `
      <div class="sd-section">
        <div class="sd-section-title" style="color:var(--blue)">
          <span class="sd-sec-icon">\u{1F4E1}</span> Signal Sources
        </div>
        <div class="sd-tool-group">
          ${SIGNAL_SOURCES.map(src => `
            <label class="sd-tool-item">
              <div>
                <div class="sd-tool-name">${src.label}</div>
                <div class="sd-tool-key">${src.value}</div>
              </div>
              <input id="signal-src-${src.value}" type="checkbox" ${activeSourceSet.has(src.value) ? 'checked' : ''} style="accent-color:var(--blue)">
            </label>
          `).join('')}
        </div>
      </div>
      <div class="sd-section">
        <div class="sd-section-title" style="color:var(--blue)">
          <span class="sd-sec-icon">\u{1F517}</span> Combine Logic
        </div>
        <div class="sd-mode-bar">
          ${SIGNAL_LOGIC_OPTIONS.map(opt => `
            <button class="sd-mode-btn signal-logic-btn ${signalLogic === opt ? 'active' : ''}" data-logic="${opt}">${opt}</button>
          `).join('')}
        </div>
      </div>
      ` : ''}

      ${currentMode === 'algo_ai' ? `
      <div class="sd-section">
        <div class="sd-section-title" style="color:var(--green)">
          <span class="sd-sec-icon">\u2696</span> Scoring Weights
        </div>
        <div class="sd-tool-group" style="padding:8px 14px">
          ${Object.entries(DEFAULT_SCORING_WEIGHTS).map(([group, def]) => {
            const val = scoringW[group] ?? def;
            return `
              <div class="sd-field-row">
                <div class="sd-field-top">
                  <div class="sd-field-label" style="text-transform:capitalize">${group}</div>
                  <input id="scoring-num-${group}" type="number" min="0" max="1" step="0.05" value="${val.toFixed(2)}" class="sd-field-num" style="width:72px">
                </div>
                <input id="scoring-range-${group}" type="range" min="0" max="1" step="0.05" value="${val}" class="sd-field-range" style="accent-color:var(--green)">
              </div>`;
          }).join('')}
        </div>
      </div>
      <div class="sd-section">
        <div class="sd-section-title" style="color:var(--green)">
          <span class="sd-sec-icon">\u{1F3AF}</span> Confidence Thresholds
        </div>
        <div class="sd-tool-group" style="padding:8px 14px">
          ${[
            { key: 'strong_entry', label: 'Strong Entry (full size)', min: 0, max: 100, step: 1, def: 75 },
            { key: 'min_entry',    label: 'Min Entry (reduced size)', min: 0, max: 100, step: 1, def: 60 },
          ].map(f => {
            const val = confThresh[f.key] ?? f.def;
            return `
              <div class="sd-field-row">
                <div class="sd-field-top">
                  <div>
                    <div class="sd-field-label">${f.label}</div>
                    <div class="sd-field-key">${f.key}</div>
                  </div>
                  <input id="conf-thresh-num-${f.key}" type="number" min="${f.min}" max="${f.max}" step="${f.step}" value="${val}" class="sd-field-num" style="width:72px">
                </div>
                <input id="conf-thresh-range-${f.key}" type="range" min="${f.min}" max="${f.max}" step="${f.step}" value="${val}" class="sd-field-range" style="accent-color:var(--green)">
              </div>`;
          }).join('')}
        </div>
      </div>
      ` : ''}

      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u{1F4DD}</span> Signal Instruction
        </div>
        <textarea id="detail-signal-instruction" rows="6" class="sd-textarea">${escapeHtml(strat.signal_instruction || '')}</textarea>
      </div>
      <div class="sd-section">
        <div class="sd-section-title">
          <span class="sd-sec-icon">\u{1F4DD}</span> Validator Instruction
        </div>
        <textarea id="detail-validator-instruction" rows="6" class="sd-textarea">${escapeHtml(strat.validator_instruction || '')}</textarea>
      </div>
    `;

    const content = {
      tune,
      tools: toolsHtml,
      validation: validationHtml,
      signal: signalHtml,
    }[_detailTab] || tune;

    shell.innerHTML = `
      <div class="sd-header">
        <div class="sd-header-info">
          <input id="detail-name" value="${escapeHtml(strat.name || `${strat.symbol} v${strat.version}`)}" class="sd-header-name">
          <div class="sd-header-meta">
            <span class="badge" style="background:${STATUS_COLORS[strat.status] || 'var(--muted)'};color:#000">${STATUS_LABELS[strat.status] || strat.status}</span>
            <span>${escapeHtml(strat.symbol)} v${escapeHtml(strat.version)}</span>
            <span>\u2022 ${escapeHtml(strat.signal_mode || 'algo_ai')}</span>
          </div>
        </div>
        <button class="sd-close" id="detail-close" title="Close">\u2715</button>
      </div>
      <div class="sd-tabs">${tabs}</div>
      <div class="sd-presets">
        ${strat.signal_mode === 'ai_signal' ? `
          <button class="sd-preset-btn accent detail-preset-global" data-preset="ai_signal_default">\u{1F916} AI Signal Defaults</button>
        ` : `
          <button class="sd-preset-btn accent detail-preset-global" data-preset="v1_exact">\u{1F4CC} V1 Exact</button>
          <button class="sd-preset-btn accent-green detail-preset-global" data-preset="balanced">\u2696 Balanced</button>
          <button class="sd-preset-btn accent-amber detail-preset-global" data-preset="aggressive">\u{1F525} Aggressive</button>
        `}
        <div class="spacer"></div>
      </div>
      <div class="sd-body">${content}</div>
      <div class="sd-footer">
        <button class="btn btn-sm" id="detail-reset" style="background:var(--bg3);border:1px solid var(--border)">\u21BA Reset</button>
        <div class="spacer"></div>
        <button class="btn btn-sm btn-primary" id="detail-save">\u{1F4BE} Save</button>
      </div>
    `;

    document.getElementById('detail-close')?.addEventListener('click', closeStrategyDetail);
    document.getElementById('detail-name')?.addEventListener('input', e => {
      const current = currentStrategy();
      if (current) current.name = e.target.value;
    });
    ['detail-signal-instruction', 'detail-validator-instruction'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', e => {
        const current = currentStrategy();
        if (!current) return;
        current[id === 'detail-signal-instruction' ? 'signal_instruction' : 'validator_instruction'] = e.target.value;
      });
    });
    document.getElementById('detail-reset')?.addEventListener('click', () => {
      if (_detailOriginal) {
        _detailState = JSON.parse(JSON.stringify(_detailOriginal));
        renderDetailShell();
      }
    });
    document.getElementById('detail-save')?.addEventListener('click', saveStrategyDetail);
    document.querySelectorAll('.detail-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        _detailTab = btn.dataset.detailTab || 'tune';
        renderDetailShell();
      });
    });
    document.querySelectorAll('.detail-preset-global').forEach(btn => {
      btn.addEventListener('click', () => applyToolPreset(btn.dataset.preset || 'balanced'));
    });
    document.querySelectorAll('.detail-preset-v1').forEach(btn => {
      btn.addEventListener('click', () => applyToolPreset('v1_exact'));
    });
    document.querySelectorAll('.detail-validation-preset').forEach(btn => {
      btn.addEventListener('click', () => applyValidationPreset(btn.dataset.preset || 'swing'));
    });
    document.querySelectorAll('.detail-signal-mode').forEach(btn => {
      btn.addEventListener('click', () => {
        const current = currentStrategy();
        if (current) current.signal_mode = btn.dataset.mode || allowedSignalModes[0];
        renderDetailShell();
      });
    });
    DETAIL_TUNE_FIELDS.forEach(field => {
      const range = document.getElementById(detailId('tune-range', field.key));
      const num = document.getElementById(detailId('tune-num', field.key));
      const sync = value => {
        if (range) range.value = value;
        if (num) num.value = value;
        const current = currentStrategy();
        if (current) {
          current.params = current.params || {};
          current.params[field.key] = parseFloat(value);
        }
      };
      range?.addEventListener('input', () => sync(range.value));
      num?.addEventListener('input', () => sync(num.value));
    });
    DETAIL_VALIDATION_FIELDS.forEach(field => {
      const range = document.getElementById(detailId('validation-range', field.key));
      const num = document.getElementById(detailId('validation-num', field.key));
      const sync = value => {
        if (range) range.value = value;
        if (num) num.value = value;
        const current = currentStrategy();
        if (current) {
          current.validation = current.validation || {};
          current.validation[field.key] = parseFloat(value);
        }
      };
      range?.addEventListener('input', () => sync(range.value));
      num?.addEventListener('input', () => sync(num.value));
    });

    document.getElementById(detailId('validation-select', 'validator_model'))?.addEventListener('change', e => {
      const current = currentStrategy();
      if (!current) return;
      current.ai_models = current.ai_models || {};
      current.ai_models.validator = e.target.value;
    });

    document.querySelectorAll('input[id^="tool-"]').forEach(chk => {
      chk.addEventListener('change', () => {
        const current = currentStrategy();
        if (!current) return;
        current.tools = current.tools || {};
        current.tools[chk.id.replace(/^tool-/, '')] = chk.checked;
      });
    });

    document.querySelectorAll('input[id^="validation-bool-"]').forEach(chk => {
      chk.addEventListener('change', () => {
        const current = currentStrategy();
        if (!current) return;
        current.validation = current.validation || {};
        current.validation[chk.id.replace(/^validation-bool-/, '')] = chk.checked;
      });
    });

    document.querySelectorAll('input[name="validation-mode"]').forEach(radio => {
      radio.addEventListener('change', () => {
        const current = currentStrategy();
        if (!current) return;
        current.validation = current.validation || {};
        current.validation.mode = radio.value;
      });
    });

    // Signal source checkboxes
    SIGNAL_SOURCES.forEach(src => {
      const chk = document.getElementById(`signal-src-${src.value}`);
      chk?.addEventListener('change', () => {
        const current = currentStrategy();
        if (!current) return;
        current.params = current.params || {};
        const rules = (current.params.signal_rules || [{ source: 'ema_crossover' }]).slice();
        if (chk.checked) {
          if (!rules.some(r => r.source === src.value)) rules.push({ source: src.value });
        } else {
          const idx = rules.findIndex(r => r.source === src.value);
          if (idx >= 0) rules.splice(idx, 1);
        }
        current.params.signal_rules = rules.length ? rules : [{ source: 'ema_crossover' }];
      });
    });

    // Signal logic buttons
    document.querySelectorAll('.signal-logic-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const current = currentStrategy();
        if (!current) return;
        current.params = current.params || {};
        current.params.signal_logic = btn.dataset.logic;
        document.querySelectorAll('.signal-logic-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.logic === btn.dataset.logic));
      });
    });

    // Scoring weight sliders
    Object.keys(DEFAULT_SCORING_WEIGHTS).forEach(group => {
      const range = document.getElementById(`scoring-range-${group}`);
      const num = document.getElementById(`scoring-num-${group}`);
      const sync = value => {
        if (range) range.value = value;
        if (num) num.value = parseFloat(value).toFixed(2);
        const current = currentStrategy();
        if (current) {
          current.scoring_weights = current.scoring_weights || {};
          current.scoring_weights[group] = parseFloat(value);
        }
      };
      range?.addEventListener('input', () => sync(range.value));
      num?.addEventListener('input', () => sync(num.value));
    });

    // Confidence threshold sliders
    ['strong_entry', 'min_entry'].forEach(key => {
      const range = document.getElementById(`conf-thresh-range-${key}`);
      const num = document.getElementById(`conf-thresh-num-${key}`);
      const sync = value => {
        if (range) range.value = value;
        if (num) num.value = value;
        const current = currentStrategy();
        if (current) {
          current.confidence_thresholds = current.confidence_thresholds || {};
          current.confidence_thresholds[key] = parseFloat(value);
        }
      };
      range?.addEventListener('input', () => sync(range.value));
      num?.addEventListener('input', () => sync(num.value));
    });
  }

  function readTuneSection() {
    const strat = currentStrategy();
    const params = {};
    DETAIL_TUNE_FIELDS.forEach(field => {
      params[field.key] = readNum(detailId('tune-num', field.key), field.min);
    });
    // Always preserve signal_rules and signal_logic (edited on Signal tab)
    params.signal_rules = strat?.params?.signal_rules || [{ source: 'ema_crossover' }];
    params.signal_logic = strat?.params?.signal_logic || 'AND';
    return params;
  }

  function readToolsSection() {
    const tools = {};
    DETAIL_TOOL_GROUPS.forEach(group => {
      group.tools.forEach(name => {
        tools[name] = readBool(detailId('tool', name), true);
      });
    });
    return tools;
  }

  function readValidationSection() {
    const validation = {};
    validation.mode = document.querySelector('input[name="validation-mode"]:checked')?.value || 'swing';
    DETAIL_VALIDATION_FIELDS.forEach(field => {
      validation[field.key] = readNum(detailId('validation-num', field.key), field.min);
    });
    ['check_news','check_rsi','check_ema200_trend','check_bos','check_fvg','check_tick_jump','check_liq_vacuum','check_regime','check_setup_type','claude_enabled'].forEach(key => {
      validation[key] = readBool(detailId('validation-bool', key), true);
    });
    return validation;
  }

  async function saveStrategyDetail() {
    const strat = currentStrategy();
    if (!strat) return;
    const strategySignalModes = getSignalModesForStrategy(strat);
    const payload = {
      name: (document.getElementById('detail-name')?.value || strat.name || '').trim(),
      signal_mode: strategySignalModes.some(([mode]) => mode === (strat.signal_mode || '').trim().toLowerCase())
        ? strat.signal_mode
        : strategySignalModes[0][0],
      params: readTuneSection(),
      tools: readToolsSection(),
      validation: readValidationSection(),
      ai_models: strat.ai_models || {},
      signal_instruction: document.getElementById('detail-signal-instruction')?.value || strat.signal_instruction || '',
      validator_instruction: document.getElementById('detail-validator-instruction')?.value || strat.validator_instruction || '',
      scoring_weights: strat.scoring_weights || {},
      confidence_thresholds: strat.confidence_thresholds || {},
    };
    try {
      const res = await apiPut(`/api/strategies/${strat.symbol}/v${strat.version}`, payload);
      if (res?.strategy) {
        _detailState = res.strategy;
        _detailOriginal = JSON.parse(JSON.stringify(res.strategy));
      }
      showToast('Strategy saved');
      await load();
      renderDetailShell();
    } catch (err) {
      showToast(`Save failed: ${err.message}`, 'error');
    }
  }

  if (_detailState) {
    renderDetailShell();
  }

  const createBtn = showCreatePanel ? document.getElementById('ai-signal-create') : null;
  if (createBtn) {
    createBtn.addEventListener('click', async () => {
      const symbolEl = document.getElementById('ai-signal-symbol');
      const nameEl = document.getElementById('ai-signal-name');
      const sigEl = document.getElementById('ai-signal-instruction');
      const valEl = document.getElementById('ai-validator-instruction');
      const previousScrollY = window.scrollY;
      const payload = {
        symbol: (symbolEl?.value || _activeSymbol || 'XAUUSD').trim(),
        name: (nameEl?.value || '').trim(),
        signal_instruction: (sigEl?.value || '').trim(),
        validator_instruction: (valEl?.value || '').trim(),
      };
      createBtn.disabled = true;
      createBtn.textContent = 'Deploying...';
      try {
        const res = await apiPost('/api/strategies/ai-signal', {
          ...payload,
          source: createCardSource,
        });
        const strat = res.strategy || {};
        showToast(`Created ${strat.name || payload.symbol} (${strat.signal_mode || 'ai_signal'})`);
        if (payload.symbol) _activeSymbol = payload.symbol.toUpperCase();
        if (nameEl) nameEl.value = '';
        if (sigEl) sigEl.value = '';
        if (valEl) valEl.value = '';
        await load();
        window.requestAnimationFrame(() => window.scrollTo(0, previousScrollY));
      } catch (err) {
        showToast(`Create failed: ${err.message}`, 'error');
      }
      createBtn.disabled = false;
      createBtn.textContent = '🚀 Deploy Signal Card';
    });
  }

  // Wire status pill buttons
  el.querySelectorAll('.strat-status-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      el.querySelectorAll('.strat-status-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      filterStat.value = pill.dataset.status;
      load();
    });
  });

  filterStat.addEventListener('change', load);
  refreshBtn.addEventListener('click', load);

  await load();
}

function showToast(msg, type = 'info') {
  if (window.showToast) window.showToast(msg, type);
}
