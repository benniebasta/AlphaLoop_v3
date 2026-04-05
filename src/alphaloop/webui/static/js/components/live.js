/**
 * ⚡ Live Trading Monitor — Ported from AlphaLoop v1
 * Real-time candlestick charts, signal intelligence, session timeline.
 */
import { apiGet } from '../api.js';

let refreshTimer = null;
let lwChart = null;
let candleSeries = null;
let rsiChart = null;
let rsiSeries = null;
let emaSeries = [];
let selectedSymbol = 'XAUUSD';
let selectedTf = '1m';
let showEMA = false;
let showRSI = false;

const SYMBOLS = ['XAUUSD', 'BTCUSD', 'EURUSD', 'GBPUSD', 'NAS100', 'US30'];

const SESSION_COLORS = {
  Asia: '#14b8a6',
  London: '#22c55e',
  Overlap: '#EF9F27',
  NY: '#eab308',
  Off: '#27272a',
};

/**
 * Render the Live Trading Monitor page.
 */
export async function render(container) {
  container.innerHTML = buildHTML();
  initSymbolPills();
  initTimeframeButtons();
  initChartToggles();
  initSymbolInput();

  // Listen for route changes to clean up
  const cleanup = () => {
    clearInterval(refreshTimer);
    destroyCharts();
    window.removeEventListener('route-change', cleanup);
    window.removeEventListener('ws-event', onWsEvent);
  };
  window.addEventListener('route-change', cleanup);
  window.addEventListener('ws-event', onWsEvent);

  // Initial data load
  await refreshLive();
  refreshTimer = setInterval(refreshLive, 5000);
}

function buildHTML() {
  const symbolPills = SYMBOLS.map(s =>
    `<button class="sym-pill ${s === selectedSymbol ? 'active' : ''}" data-sym="${s}">${s}</button>`
  ).join('');

  return `
    <div class="live-page">
      <!-- Row 1: Header -->
      <div class="live-header">
        <div class="page-title">⚡ Live Trading Monitor</div>
        <div class="live-header-right">
          <span id="live-clock" class="live-clock"></span>
          <span id="live-agent-status" class="live-agent-badge">● Bot offline</span>
        </div>
      </div>

      <!-- Row 2: Symbol Selector -->
      <div class="live-symbol-bar">
        <span class="live-viewing-label">VIEWING:</span>
        <div class="sym-pills">${symbolPills}</div>
        <div class="sym-input-wrap">
          <input type="text" id="sym-custom" class="field-input" placeholder="E.G. EURUS" style="width:120px;padding:5px 8px;font-size:12px;">
          <button class="btn btn-sm btn-primary" id="sym-go">Go →</button>
        </div>
      </div>

      <!-- Row 3: Status Banner -->
      <div id="live-status-banner" class="live-status-banner">
        No live agents running — start an agent with DRY RUN disabled in Settings → Trading
      </div>

      <!-- Row 4: Price Ticker Bar -->
      <div class="live-ticker-bar card">
        <div class="live-ticker-sym" id="live-sym">${selectedSymbol}</div>
        <div class="live-ticker-price" id="live-price">—</div>
        <div class="live-ticker-meta">
          <div class="live-ticker-item">
            <span class="live-ticker-label">24h change</span>
            <span id="live-change" class="live-ticker-value">—</span>
          </div>
          <div class="live-ticker-item">
            <span class="live-ticker-label">Day range</span>
            <span id="live-range" class="live-ticker-value">—</span>
          </div>
          <div class="live-ticker-item">
            <span class="live-ticker-label">Session</span>
            <span id="live-session" class="badge badge-orange">—</span>
          </div>
        </div>
      </div>

      <!-- Row 5: Chart + Signal Intelligence (2-column) -->
      <div class="live-grid2">
        <!-- Left: Candlestick Chart -->
        <div class="live-chart-card card">
          <div class="live-chart-header">
            <span id="live-chart-title">${selectedSymbol} · ${selectedTf.toUpperCase()} <span class="live-data-source-badge" title="Analytics data via yfinance. Bot trades use the MT5 live feed.">yfinance</span></span>
            <div class="live-chart-controls">
              <span id="live-bar-count" class="live-bar-count">— BARS</span>
              <button class="btn btn-sm ${showEMA ? 'btn-primary' : ''}" id="btn-ema">EMA</button>
              <button class="btn btn-sm ${showRSI ? 'btn-primary' : ''}" id="btn-rsi">RSI</button>
            </div>
          </div>
          <div class="live-tf-bar" id="tf-bar">
            ${['1m','5m','15m','30m','1h','4h','1d','1w'].map(tf =>
              `<button class="tf-btn ${tf === selectedTf ? 'active' : ''}" data-tf="${tf}">${tf.toUpperCase()}</button>`
            ).join('')}
          </div>
          <div id="live-chart-div" class="live-chart-div"></div>
          <div id="rsi-pane-wrap" class="rsi-pane-wrap" style="display:${showRSI ? 'block' : 'none'}">
            <div id="rsi-chart-div" class="rsi-chart-div"></div>
          </div>
        </div>

        <!-- Right: Signal Intelligence -->
        <div class="live-signal-card card">
          <div class="live-signal-header">SIGNAL INTELLIGENCE</div>

          <div class="live-signal-badge-wrap">
            <div class="live-signal-badge" id="sig-badge">
              <span class="sig-dot"></span>
              <span class="sig-text">SCANNING...</span>
            </div>
          </div>

          <div class="live-gauge-wrap">
            <svg viewBox="0 0 200 110" class="live-gauge-svg">
              <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="var(--bg4)" stroke-width="10" stroke-linecap="round"/>
              <path id="gauge-arc" d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="var(--primary)" stroke-width="10" stroke-linecap="round" stroke-dasharray="0 251"/>
              <circle id="gauge-dot" cx="20" cy="100" r="5" fill="var(--green)"/>
            </svg>
            <div class="live-gauge-val" id="gauge-val">—</div>
            <div class="live-gauge-label" id="gauge-label">AWAITING SIGNAL</div>
          </div>

          <div class="live-signal-meta">
            <div class="live-meta-row">
              <span class="live-meta-label">Last signal</span>
              <span id="live-sig-time" class="live-meta-value">— —</span>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">AI CONFIDENCE</span>
              <span id="live-ai-confidence" class="live-meta-value">—</span>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">MARKET REGIME</span>
              <span id="live-regime" class="live-meta-value">● —</span>
              <div id="live-ema-values" class="live-ema-values"></div>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">RSI (14)</span>
              <span id="live-rsi" class="live-meta-value">—</span>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">VOLATILITY</span>
              <span id="live-volatility" class="live-meta-value">—</span>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">RECENT SIGNALS</span>
              <span id="live-recent" class="live-meta-value">No signals recorded yet</span>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">AGENT STATUS</span>
              <span id="live-agent-detail" class="live-meta-value" style="color:var(--primary)">Bot offline — start a bot to begin trading</span>
            </div>
            <div class="live-meta-row">
              <span class="live-meta-label">LIVE THOUGHTS</span>
              <div id="live-thoughts" class="live-thoughts-log"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Row 6: 24H Session Timeline -->
      <div class="live-session-timeline card">
        <div class="live-timeline-title">24H SESSION TIMELINE — UTC (CURRENT TIME MARKED [·])</div>
        <div class="live-timeline-bar" id="session-timeline">
          <div class="timeline-segment" style="background:${SESSION_COLORS.Asia};flex:6" title="Asia 00:00-06:00">Asia</div>
          <div class="timeline-segment" style="background:${SESSION_COLORS.London};flex:6" title="London 06:00-12:00">London</div>
          <div class="timeline-segment" style="background:${SESSION_COLORS.Overlap};flex:3" title="Overlap 12:00-15:00">Overlap</div>
          <div class="timeline-segment" style="background:${SESSION_COLORS.NY};flex:5" title="NY 15:00-20:00">NY</div>
          <div class="timeline-segment" style="background:${SESSION_COLORS.Off};flex:4" title="Off 20:00-24:00">Off</div>
          <div class="timeline-marker" id="time-marker"></div>
        </div>
        <div class="live-timeline-labels">
          <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span>
        </div>
      </div>

      <!-- Row 7: Three Info Cards -->
      <div class="live-info-grid">
        <div class="live-info-card card">
          <div class="live-info-title">📰 NEXT NEWS EVENT</div>
          <div id="live-news" class="live-info-body">
            <span class="live-info-value">No data</span>
            <span class="live-info-sub">——</span>
          </div>
        </div>
        <div class="live-info-card card">
          <div class="live-info-title">🕐 SESSION CLOCK</div>
          <div id="live-session-clock" class="live-info-body">
            <span class="live-info-session-name" id="session-name">—</span>
            <span class="live-info-countdown" id="session-countdown">—</span>
            <span class="live-info-sub" id="session-closes">—</span>
          </div>
        </div>
        <div class="live-info-card card">
          <div class="live-info-title">
            📊 VOLATILITY REGIME
            <span id="vol-badge" class="badge badge-green" style="margin-left:auto;font-size:10px">—</span>
          </div>
          <div id="live-vol" class="live-info-body">
            <div class="live-vol-meter">
              <div class="vol-segment" data-level="calm">Calm</div>
              <div class="vol-segment" data-level="normal">Normal</div>
              <div class="vol-segment" data-level="elevated">Elevated</div>
              <div class="vol-segment" data-level="extreme">Extreme</div>
            </div>
            <span class="live-info-sub" id="vol-atr">ATR: —</span>
          </div>
        </div>
      </div>

      <!-- Row 8: Filter Pipeline -->
      <div id="live-pipeline" class="live-pipeline card" style="display:none">
        <div class="live-pipeline-title">🔧 FILTER PIPELINE</div>
        <div id="pipeline-filters" class="pipeline-filters"></div>
      </div>
    </div>

    <style>
      /* ── Live Page Styles ────────────────────────────────── */
      .live-page { display: flex; flex-direction: column; gap: 16px; }
      .live-header { display: flex; justify-content: space-between; align-items: center; }
      .live-header-right { display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--muted); }
      .live-clock { font-family: 'Fira Code', monospace; }
      .live-agent-badge { color: var(--muted); }
      .live-agent-badge.online { color: var(--green); }

      .live-symbol-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
      .live-viewing-label { font-size: 11px; font-weight: 600; color: var(--muted); letter-spacing: 0.05em; }
      .sym-pills { display: flex; gap: 6px; flex-wrap: wrap; }
      .sym-pill {
        padding: 5px 14px; border-radius: 20px; border: 1px solid var(--border);
        background: transparent; color: var(--muted); font-size: 12px; font-weight: 600;
        cursor: pointer; transition: all 0.15s;
      }
      .sym-pill:hover { border-color: var(--primary-border); color: var(--text); }
      .sym-pill.active { background: var(--primary); border-color: var(--primary); color: #fff; }
      .sym-input-wrap { display: flex; gap: 6px; margin-left: auto; }

      .live-status-banner {
        padding: 8px 16px; font-size: 12px; color: var(--muted);
        background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
      }
      .live-status-banner.active { color: var(--green); border-color: rgba(34,197,94,0.3); }

      /* Ticker Bar */
      .live-ticker-bar {
        display: flex; align-items: center; gap: 24px; padding: 16px 20px;
      }
      .live-ticker-sym { font-size: 14px; font-weight: 600; color: var(--muted); min-width: 80px; }
      .live-ticker-price { font-size: 32px; font-weight: 800; min-width: 160px; transition: color 0.3s; }
      .live-ticker-meta { display: flex; gap: 24px; flex: 1; }
      .live-ticker-item { display: flex; flex-direction: column; }
      .live-ticker-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
      .live-ticker-value { font-size: 14px; font-weight: 600; }

      @keyframes flashGreen { 0% { color: var(--green); text-shadow: 0 0 8px var(--green); } 100% { text-shadow: none; } }
      @keyframes flashRed { 0% { color: var(--red); text-shadow: 0 0 8px var(--red); } 100% { text-shadow: none; } }
      .price-up { animation: flashGreen 0.5s ease-out; color: var(--green); }
      .price-down { animation: flashRed 0.5s ease-out; color: var(--red); }

      /* Chart Grid */
      .live-grid2 { display: grid; grid-template-columns: 7fr 3fr; gap: 16px; }
      .live-chart-card { padding: 0; overflow: hidden; }
      .live-chart-header {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 16px; border-bottom: 1px solid var(--border);
        font-size: 12px; font-weight: 600; color: var(--muted);
      }
      .live-chart-controls { display: flex; align-items: center; gap: 8px; }
      .live-bar-count { font-size: 11px; color: var(--muted); margin-right: 8px; }

      .live-tf-bar { display: flex; gap: 4px; padding: 8px 16px; border-bottom: 1px solid var(--border); }
      .tf-btn {
        padding: 4px 10px; border-radius: 4px; border: 1px solid var(--border);
        background: transparent; color: var(--muted); font-size: 11px; font-weight: 600;
        cursor: pointer; transition: all 0.15s;
      }
      .tf-btn:hover { border-color: var(--primary-border); color: var(--text); }
      .tf-btn.active { background: var(--primary); border-color: var(--primary); color: #fff; }

      .live-chart-div { height: 380px; }
      .rsi-pane-wrap { border-top: 1px solid var(--border); }
      .rsi-chart-div { height: 80px; }

      /* Signal Intelligence */
      .live-signal-card { display: flex; flex-direction: column; }
      .live-signal-header { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 16px; }

      .live-signal-badge-wrap { text-align: center; margin-bottom: 16px; }
      .live-signal-badge {
        display: inline-flex; align-items: center; gap: 8px;
        padding: 8px 24px; border-radius: 20px;
        background: var(--bg3); border: 1px solid var(--border);
        font-size: 13px; font-weight: 600; color: var(--muted);
      }
      .live-signal-badge.buy { background: rgba(34,197,94,0.12); border-color: rgba(34,197,94,0.3); color: var(--green); }
      .live-signal-badge.sell { background: rgba(239,68,68,0.12); border-color: rgba(239,68,68,0.3); color: var(--red); }
      .sig-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 2s infinite; }

      .live-gauge-wrap { text-align: center; margin-bottom: 16px; }
      .live-gauge-svg { width: 160px; height: 88px; }
      .live-gauge-val { font-size: 20px; font-weight: 700; margin-top: -8px; }
      .live-gauge-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 4px; }

      .live-ema-values { margin-top: 3px; }
      .live-signal-meta { flex: 1; display: flex; flex-direction: column; gap: 0; }
      .live-meta-row { padding: 8px 0; border-bottom: 1px solid var(--border); }
      .live-meta-row:last-child { border-bottom: none; }
      .live-meta-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); display: block; margin-bottom: 4px; }
      .live-meta-value { font-size: 12px; color: var(--text); }

      .live-thoughts-log {
        max-height: 160px; overflow-y: auto; font-family: 'Fira Code', monospace;
        font-size: 10px; color: var(--code-fg); background: var(--code-bg);
        padding: 6px 8px; border-radius: 4px; margin-top: 4px;
      }
      .live-thoughts-log:empty::before {
        content: 'Waiting for bot events...';
        color: var(--muted); font-style: italic;
      }
      .thought-line {
        padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .thought-line:first-child { border-top: none; }
      .thought-line:last-child { border-bottom: none; }

      /* Session Timeline */
      .live-session-timeline { padding: 12px 16px; }
      .live-timeline-title { font-size: 10px; font-weight: 600; color: var(--muted); letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 8px; }
      .live-timeline-bar { display: flex; height: 28px; border-radius: 6px; overflow: hidden; position: relative; }
      .timeline-segment { display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; color: #fff; opacity: 0.9; }
      .timeline-marker { position: absolute; top: 0; bottom: 0; width: 2px; background: #fff; z-index: 1; transition: left 0.5s; }
      .live-timeline-labels { display: flex; justify-content: space-between; font-size: 10px; color: var(--muted); margin-top: 4px; }

      /* Info Cards */
      .live-info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
      .live-info-card { padding: 14px 16px; }
      .live-info-title { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }
      .live-info-body { display: flex; flex-direction: column; gap: 4px; }
      .live-info-value { font-size: 14px; font-weight: 600; }
      .live-info-session-name { font-size: 12px; color: var(--primary); font-weight: 600; }
      .live-info-countdown { font-size: 28px; font-weight: 800; }
      .live-info-sub { font-size: 11px; color: var(--muted); }

      .live-vol-meter { display: flex; gap: 2px; margin-bottom: 6px; }
      .vol-segment {
        flex: 1; padding: 4px 0; text-align: center; font-size: 10px; font-weight: 500;
        border-radius: 3px; background: var(--bg3); color: var(--muted); transition: all 0.2s;
      }
      .vol-segment.active[data-level="calm"] { background: var(--green); color: #fff; }
      .vol-segment.active[data-level="normal"] { background: var(--amber); color: #000; }
      .vol-segment.active[data-level="elevated"] { background: var(--primary); color: #fff; }
      .vol-segment.active[data-level="extreme"] { background: var(--red); color: #fff; }

      /* Pipeline */
      .live-pipeline-title { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 8px; }
      .pipeline-filters { display: flex; gap: 6px; flex-wrap: wrap; }
      .pipeline-chip {
        padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 500;
        border: 1px solid var(--border);
      }
      .pipeline-chip.passed { background: rgba(34,197,94,0.1); border-color: rgba(34,197,94,0.3); color: var(--green); }
      .pipeline-chip.blocked { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.3); color: var(--red); }

      @media (max-width: 768px) {
        .live-grid2 { grid-template-columns: 1fr; }
        .live-info-grid { grid-template-columns: 1fr; }
        .live-ticker-bar { flex-wrap: wrap; }
        .live-chart-div { height: 260px; }
      }
    </style>
  `;
}

/* ── Event Handlers ────────────────────────────────────── */

function initSymbolPills() {
  document.querySelectorAll('.sym-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      selectedSymbol = btn.dataset.sym;
      document.querySelectorAll('.sym-pill').forEach(b => b.classList.toggle('active', b.dataset.sym === selectedSymbol));
      const title = document.getElementById('live-chart-title');
      if (title) title.innerHTML = `${selectedSymbol} · ${selectedTf.toUpperCase()} <span class="live-data-source-badge" title="Analytics data via yfinance. Bot trades use the MT5 live feed.">yfinance</span>`;
      destroyCharts();
      refreshLive();
    });
  });
}

function initTimeframeButtons() {
  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      selectedTf = btn.dataset.tf;
      document.querySelectorAll('.tf-btn').forEach(b => b.classList.toggle('active', b.dataset.tf === selectedTf));
      const title = document.getElementById('live-chart-title');
      if (title) title.innerHTML = `${selectedSymbol} · ${selectedTf.toUpperCase()} <span class="live-data-source-badge" title="Analytics data via yfinance. Bot trades use the MT5 live feed.">yfinance</span>`;
      destroyCharts();
      refreshLive();
    });
  });
}

function initChartToggles() {
  document.getElementById('btn-ema')?.addEventListener('click', () => {
    showEMA = !showEMA;
    document.getElementById('btn-ema').classList.toggle('btn-primary', showEMA);
    refreshLive();
  });
  document.getElementById('btn-rsi')?.addEventListener('click', () => {
    showRSI = !showRSI;
    document.getElementById('btn-rsi').classList.toggle('btn-primary', showRSI);
    const wrap = document.getElementById('rsi-pane-wrap');
    if (wrap) wrap.style.display = showRSI ? 'block' : 'none';
    refreshLive();
  });
}

function initSymbolInput() {
  document.getElementById('sym-go')?.addEventListener('click', () => {
    const input = document.getElementById('sym-custom');
    if (input?.value.trim()) {
      selectedSymbol = input.value.trim().toUpperCase();
      const title = document.getElementById('live-chart-title');
      if (title) title.innerHTML = `${selectedSymbol} · ${selectedTf.toUpperCase()} <span class="live-data-source-badge" title="Analytics data via yfinance. Bot trades use the MT5 live feed.">yfinance</span>`;
      destroyCharts();
      refreshLive();
    }
  });
}

function onWsEvent(e) {
  const data = e.detail;
  if (!data || !data.type) return;

  // Real-time signal from the trading bot
  if (data.type === 'SignalGenerated' && data.symbol === selectedSymbol) {
    updateSignalPanel(data, true);
    const confidence = data.confidence ?? data.signal?.confidence;
    const direction = data.direction || data.signal?.direction || 'SIGNAL';
    const setup = data.setup || data.signal?.setup || '';
    const conf = confidence != null ? `${Math.round(confidence * 100)}%` : '';
    const agentEl = document.getElementById('live-agent-detail');
    if (agentEl) {
      agentEl.textContent = `Bot signal: ${direction} ${conf}`.trim();
      agentEl.style.color = direction === 'BUY' ? 'var(--green)' : direction === 'SELL' ? 'var(--red)' : 'var(--primary)';
    }
    appendThought(
      'signal_gen',
      'generated',
      `${direction}${conf ? ` confidence:${conf}` : ''}${setup ? ` setup:${setup}` : ''}`,
      data.timestamp,
    );
  }

  // Pipeline steps → Live Thoughts
  if (data.type === 'PipelineStep' && data.symbol === selectedSymbol) {
    appendThought(data.stage, data.status, data.detail, data.timestamp);
  }

  // Cycle markers → Live Thoughts
  if (data.type === 'CycleStarted' && data.symbol === selectedSymbol) {
    appendThought('cycle', 'started', `Cycle #${data.cycle}`, data.timestamp);
  }
  if (data.type === 'CycleCompleted' && data.symbol === selectedSymbol) {
    appendThought('cycle', data.outcome || 'done', data.detail || '', data.timestamp);
    const agentEl = document.getElementById('live-agent-detail');
    if (agentEl) {
      agentEl.textContent = `Cycle #${data.cycle} — ${data.outcome || 'done'}`;
      agentEl.style.color = 'var(--primary)';
    }
  }
}

/* ── Live Thoughts ────────────────────────────────────── */

const _THOUGHT_ICONS = {
  cycle: '\u{1F504}', risk_check: '\u{1F6E1}', filters: '\u{1F50D}', signal_gen: '\u{1F4E1}',
  validation: '\u{2705}', guards: '\u{1F3F0}', sizing: '\u{1F4D0}', execution: '\u{26A1}',
};
const _THOUGHT_STATUS_COLOR = {
  passed: 'var(--green)', generated: 'var(--green)', approved: 'var(--green)', filled: 'var(--green)',
  started: 'var(--primary)',
  blocked: 'var(--red)', rejected: 'var(--red)', failed: 'var(--red)',
  no_signal: 'var(--muted)', no_construction: 'var(--muted)', skipped: 'var(--muted)', done: 'var(--muted)',
};
const MAX_THOUGHTS = 20;

function appendThought(stage, status, detail, timestamp) {
  const el = document.getElementById('live-thoughts');
  if (!el) return;

  const time = timestamp
    ? new Date(typeof timestamp === 'number' ? timestamp * 1000 : timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const icon = _THOUGHT_ICONS[stage] || '\u{2022}';
  const color = _THOUGHT_STATUS_COLOR[status] || 'var(--muted)';
  const label = (stage || '').replace(/_/g, ' ');
  const badge = status || '';
  const text = detail ? ` \u2014 ${detail}` : '';

  const line = document.createElement('div');
  line.className = 'thought-line';
  line.innerHTML = `<span style="color:var(--muted)">${time}</span> ${icon} <span style="text-transform:capitalize">${label}</span> <span style="color:${color};font-weight:600">${badge}</span><span style="color:var(--text-secondary)">${text}</span>`;

  // Prepend newest at top
  el.insertBefore(line, el.firstChild);

  // Trim to max
  while (el.children.length > MAX_THOUGHTS) {
    el.removeChild(el.lastChild);
  }
}

/* ── Data Refresh ──────────────────────────────────────── */

let lastPrice = null;

async function refreshLive() {
  try {
    const data = await apiGet(`/api/live?symbol=${selectedSymbol}&timeframe=${selectedTf}`);
    if (!data) return;
    updatePrice(data);
    updateChart(data.ohlc || []);
    updateSignalPanel(data);
    updateSessionTimeline();
    updateInfoCards(data);
    updateClock();
  } catch (err) {
    // Live endpoint may not exist yet — show placeholder
    updateClock();
    updateSessionTimeline();
    if (!lwChart) initChart([]);
  }
}

function updatePrice(data) {
  const priceEl = document.getElementById('live-price');
  const changeEl = document.getElementById('live-change');
  const rangeEl = document.getElementById('live-range');
  const sessionEl = document.getElementById('live-session');
  const symEl = document.getElementById('live-sym');

  if (symEl) symEl.textContent = selectedSymbol;

  if (data.price != null && priceEl) {
    const price = parseFloat(data.price);
    priceEl.textContent = `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

    if (lastPrice !== null) {
      priceEl.classList.remove('price-up', 'price-down');
      void priceEl.offsetWidth; // force reflow
      priceEl.classList.add(price > lastPrice ? 'price-up' : price < lastPrice ? 'price-down' : '');
    }
    lastPrice = price;
  }

  if (data.change_pct != null && changeEl) {
    const pct = parseFloat(data.change_pct);
    changeEl.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    changeEl.style.color = pct >= 0 ? 'var(--green)' : 'var(--red)';
  }

  if (data.day_high != null && data.day_low != null && rangeEl) {
    rangeEl.textContent = `$${data.day_low.toLocaleString()} – $${data.day_high.toLocaleString()}`;
  }

  if (data.session && sessionEl) {
    sessionEl.textContent = `🟢 ${data.session.name}`;
  }
}

/* ── Chart ─────────────────────────────────────────────── */

function initChart(ohlc) {
  const chartDiv = document.getElementById('live-chart-div');
  if (!chartDiv || !window.LightweightCharts) return;

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

  lwChart = window.LightweightCharts.createChart(chartDiv, {
    width: chartDiv.clientWidth,
    height: chartDiv.clientHeight || 380,
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: isDark ? '#a1a1aa' : '#71717a',
    },
    grid: {
      vertLines: { color: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)' },
      horzLines: { color: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.04)' },
    },
    crosshair: { mode: 0 },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)' },
  });

  const _csOpts = {
    upColor: '#1D9E75', downColor: '#E24B4A',
    borderUpColor: '#1D9E75', borderDownColor: '#E24B4A',
    wickUpColor: '#1D9E75', wickDownColor: '#E24B4A',
  };
  const LC = window.LightweightCharts;
  // v4: addSeries(CandlestickSeries, opts) — fallback to v3 addCandlestickSeries
  try {
    candleSeries = LC.CandlestickSeries ? lwChart.addSeries(LC.CandlestickSeries, _csOpts) : lwChart.addCandlestickSeries(_csOpts);
  } catch {
    candleSeries = lwChart.addCandlestickSeries(_csOpts);
  }

  if (ohlc.length) {
    candleSeries.setData(ohlc);
    const countEl = document.getElementById('live-bar-count');
    if (countEl) countEl.textContent = `${ohlc.length} BARS · ${selectedTf.toUpperCase()}`;
  }

  // Responsive
  const ro = new ResizeObserver(() => {
    if (lwChart) lwChart.applyOptions({ width: chartDiv.clientWidth });
  });
  ro.observe(chartDiv);
}

function _dedup(ohlc) {
  // Remove duplicate timestamps — yfinance can return dupes which cause setData to throw
  const seen = new Set();
  return ohlc.filter(b => {
    if (seen.has(b.time)) return false;
    seen.add(b.time);
    return true;
  });
}

function updateChart(ohlc) {
  if (!ohlc.length) return;
  // Deduplicate before any series use — duplicate timestamps cause LightweightCharts to throw
  const bars = _dedup(ohlc);

  if (!lwChart) {
    initChart(bars);
  } else if (candleSeries) {
    try { candleSeries.setData(bars); } catch (e) { console.warn('[chart] candleSeries.setData failed:', e.message); }
    const countEl = document.getElementById('live-bar-count');
    if (countEl) countEl.textContent = `${bars.length} BARS · ${selectedTf.toUpperCase()}`;
  }

  // EMA overlay
  if (showEMA && lwChart && bars.length > 55) {
    emaSeries.forEach(s => { try { if (s.detach) s.detach(); else lwChart.removeSeries(s); } catch {} });
    emaSeries = [];

    const closes = bars.map(b => b.close);
    const ema21data = calcEMA(closes, 21)
      .map((v, i) => ({ time: bars[i].time, value: v }))
      .filter(d => d.value !== null);
    const ema55data = calcEMA(closes, 55)
      .map((v, i) => ({ time: bars[i].time, value: v }))
      .filter(d => d.value !== null);

    try {
      const s21 = lwChart.addLineSeries({ color: '#3b82f6', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      s21.setData(ema21data);
      emaSeries.push(s21);
    } catch (e) { console.warn('[chart] EMA-21 failed:', e.message); }

    try {
      const s55 = lwChart.addLineSeries({ color: '#f59e0b', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      s55.setData(ema55data);
      emaSeries.push(s55);
    } catch (e) { console.warn('[chart] EMA-55 failed:', e.message); }

  } else if (!showEMA && emaSeries.length && lwChart) {
    emaSeries.forEach(s => { try { if (s.detach) s.detach(); else lwChart.removeSeries(s); } catch {} });
    emaSeries = [];
  }

  // RSI sub-chart
  if (showRSI && bars.length > 14) {
    const _buildRsiChart = () => {
      const rsiDiv = document.getElementById('rsi-chart-div');
      if (!rsiDiv || !window.LightweightCharts) return;
      if (!rsiChart) {
        const LC = window.LightweightCharts;
        const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
        const w = rsiDiv.clientWidth || rsiDiv.offsetWidth || 400;
        rsiChart = LC.createChart(rsiDiv, {
          width: w,
          height: 80,
          layout: { background: { type: 'solid', color: 'transparent' }, textColor: isDark ? '#a1a1aa' : '#71717a' },
          grid: { vertLines: { color: 'rgba(255,255,255,0.02)' }, horzLines: { color: 'rgba(255,255,255,0.02)' } },
          crosshair: { mode: 0 },
          timeScale: { visible: false },
          rightPriceScale: { borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)' },
        });
        try {
          rsiSeries = rsiChart.addLineSeries({ color: '#8b5cf6', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
        } catch (e) { console.warn('[chart] RSI series failed:', e.message); rsiSeries = null; }
      }
      if (rsiSeries) {
        try {
          const rsiData = calcRSI(bars.map(b => b.close), 14)
            .map((v, i) => ({ time: bars[i].time, value: v }))
            .filter(d => d.value !== null);
          rsiSeries.setData(rsiData);
        } catch (e) { console.warn('[chart] RSI setData failed:', e.message); }
      }
    };
    // Use rAF to ensure rsi-pane-wrap has been laid out before reading clientWidth
    requestAnimationFrame(_buildRsiChart);
  }
}

/* ── Technical Indicator Helpers ───────────────────────── */

function calcEMA(closes, period) {
  const k = 2 / (period + 1);
  const result = new Array(closes.length).fill(null);
  if (closes.length < period) return result;
  let sum = 0;
  for (let i = 0; i < period; i++) sum += closes[i];
  result[period - 1] = sum / period;
  for (let i = period; i < closes.length; i++) {
    result[i] = closes[i] * k + result[i - 1] * (1 - k);
  }
  return result;
}

function calcRSI(closes, period) {
  const result = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return result;
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const delta = closes[i] - closes[i - 1];
    if (delta > 0) avgGain += delta; else avgLoss -= delta;
  }
  avgGain /= period;
  avgLoss /= period;
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(delta, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-delta, 0)) / period;
    result[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

function destroyCharts() {
  if (lwChart) { lwChart.remove(); lwChart = null; candleSeries = null; }
  if (rsiChart) { rsiChart.remove(); rsiChart = null; rsiSeries = null; }
  emaSeries = [];
}

/* ── Signal Panel ──────────────────────────────────────── */

let _botSignalActive = false;  // true when a bot SignalGenerated was received
let _botSignalExpiry = 0;      // auto-expire after 5 minutes

function updateSignalPanel(data, fromBot = false) {
  const signal = data?.signal ?? data;  // accept full data obj or bare signal
  const badge = document.getElementById('sig-badge');
  if (!badge) return;

  // If bot sent a signal, mark it active (expires after 5 min)
  if (fromBot && signal?.direction) {
    _botSignalActive = true;
    _botSignalExpiry = Date.now() + 300000;
  }
  // Don't let the 5s poll overwrite an active bot signal with "SCANNING..."
  if (!fromBot && _botSignalActive && Date.now() < _botSignalExpiry && (!signal || !signal.direction)) {
    return;
  }
  if (_botSignalActive && Date.now() >= _botSignalExpiry) {
    _botSignalActive = false;
  }

  if (!signal || !signal.direction) {
    const emaRegime = data?.ema_state?.regime || data?.market_regime;
    const biasBadge = emaRegime === 'trending_up' ? 'BUY' : emaRegime === 'trending_down' ? 'SELL' : 'NEUTRAL';
    const biasCls   = emaRegime === 'trending_up' ? 'buy' : emaRegime === 'trending_down' ? 'sell' : '';
    badge.className = `live-signal-badge${biasCls ? ' ' + biasCls : ''}`;
    badge.querySelector('.sig-text').textContent = biasBadge;

    // Derive bias from EMA alignment when no active crossover
    const ema = data?.ema_state;
    const arc = document.getElementById('gauge-arc');
    const val = document.getElementById('gauge-val');
    const lbl = document.getElementById('gauge-label');
    if (ema) {
      // Use market_regime (EMA stack alignment) — more reliable than raw gap_pct on short TFs
      const regime = ema.regime || data?.market_regime || 'ranging';
      let bias, biasColor, biasArc;
      if (regime === 'trending_up') {
        bias = 'BUY'; biasColor = 'var(--green)';
        const strength = Math.min(Math.max(Math.abs(ema.gap_pct || 0) / 5, 0.2), 0.9);
        biasArc = Math.round(strength * 251);
      } else if (regime === 'trending_down') {
        bias = 'SELL'; biasColor = 'var(--red)';
        const strength = Math.min(Math.max(Math.abs(ema.gap_pct || 0) / 5, 0.2), 0.9);
        biasArc = Math.round(strength * 251);
      } else {
        bias = 'NEUTRAL'; biasColor = 'var(--amber)'; biasArc = Math.round(0.15 * 251);
      }
      if (arc) { arc.setAttribute('stroke-dasharray', `${biasArc} 251`); arc.style.stroke = biasColor; }
      if (val) { val.textContent = bias; val.style.color = biasColor; }
      if (lbl) lbl.textContent = 'EMA BIAS';
    } else {
      if (arc) arc.setAttribute('stroke-dasharray', '0 251');
      if (val) { val.textContent = '—'; val.style.color = ''; }
      if (lbl) lbl.textContent = 'AWAITING SIGNAL';
    }
    const aiConfidenceEl = document.getElementById('live-ai-confidence');
    if (aiConfidenceEl) aiConfidenceEl.textContent = '—';
  } else {
    const dir = signal.direction.toLowerCase();
    badge.className = `live-signal-badge ${dir}`;
    badge.querySelector('.sig-text').textContent = dir.toUpperCase();

    if (signal.confidence != null) {
      const pct = Math.round(signal.confidence * 100);
      const arc = document.getElementById('gauge-arc');
      const val = document.getElementById('gauge-val');
      const label = document.getElementById('gauge-label');
      const aiConfidenceEl = document.getElementById('live-ai-confidence');
      if (arc) arc.setAttribute('stroke-dasharray', `${(pct / 100) * 251} 251`);
      if (val) val.textContent = `${pct}%`;
      if (label) label.textContent = dir.toUpperCase();
      if (aiConfidenceEl) aiConfidenceEl.textContent = `${pct}%`;
    }

    const sigTime = document.getElementById('live-sig-time');
    if (sigTime && signal.timestamp) {
      const ts = typeof signal.timestamp === 'number' ? signal.timestamp * 1000 : signal.timestamp;
      sigTime.textContent = new Date(ts).toLocaleTimeString();
    }
  }

  // Market regime
  const regimeEl = document.getElementById('live-regime');
  if (regimeEl) {
    const regime = data?.market_regime ?? 'ranging';
    const regimeMap = {
      trending_up:   { label: '▲ Trending Up',   color: 'var(--green)' },
      trending_down: { label: '▼ Trending Down',  color: 'var(--red)' },
      ranging:       { label: '↔ Ranging',        color: 'var(--amber)' },
    };
    const r = regimeMap[regime] || { label: regime, color: 'var(--muted)' };
    regimeEl.textContent = r.label;
    regimeEl.style.color = r.color;
  }

  // EMA values
  const emaValEl = document.getElementById('live-ema-values');
  if (emaValEl) {
    const ema = data?.ema_state;
    if (ema && ema.ema9 != null) {
      const trendCol = ema.ema9 > ema.ema21 ? 'var(--green)' : 'var(--red)';
      emaValEl.innerHTML = `<span style="color:${trendCol};font-family:'Fira Code',monospace;font-size:10px">` +
        `9: ${ema.ema9.toFixed(1)} &nbsp; 21: ${ema.ema21.toFixed(1)} &nbsp; 50: ${ema.ema50.toFixed(1)}` +
        `</span>`;
    } else {
      emaValEl.textContent = '';
    }
  }

  // RSI
  const rsiEl = document.getElementById('live-rsi');
  if (rsiEl) {
    const rsi = data?.ema_state?.rsi;
    if (rsi != null) {
      const rsiVal = rsi.toFixed(1);
      let rsiColor, rsiLabel;
      if (rsi < 35)      { rsiColor = 'var(--green)'; rsiLabel = `${rsiVal} — oversold`; }
      else if (rsi > 65) { rsiColor = 'var(--red)';   rsiLabel = `${rsiVal} — overbought`; }
      else               { rsiColor = 'var(--amber)';  rsiLabel = rsiVal; }
      rsiEl.textContent = rsiLabel;
      rsiEl.style.color = rsiColor;
    } else {
      rsiEl.textContent = '—';
      rsiEl.style.color = '';
    }
  }

  // Volatility
  const volEl = document.getElementById('live-volatility');
  if (volEl) {
    const volRegime = data?.volatility?.regime;
    const volMap = {
      calm:     { label: 'Calm',     color: 'var(--green)' },
      normal:   { label: 'Normal',   color: 'var(--text)' },
      elevated: { label: 'Elevated', color: 'var(--amber)' },
      extreme:  { label: 'Extreme',  color: 'var(--red)' },
    };
    const v = volMap[volRegime] || { label: volRegime || '—', color: 'var(--muted)' };
    volEl.textContent = v.label;
    volEl.style.color = v.color;
  }

  // Recent signals
  const recentEl = document.getElementById('live-recent');
  if (recentEl) {
    const sigs = data?.recent_signals ?? [];
    if (!sigs.length) {
      recentEl.textContent = 'No crossovers in last 50 bars';
    } else {
      recentEl.innerHTML = sigs.map(s => {
        const col = s.direction === 'BUY' ? 'var(--green)' : 'var(--red)';
        const t = new Date(s.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const price = s.price != null ? ` (${s.price.toFixed(2)})` : '';
        return `<span style="color:${col};margin-right:8px">${s.direction} @ ${t}${price}</span>`;
      }).join('');
    }
  }
}

/* ── Session Timeline ──────────────────────────────────── */

function updateSessionTimeline() {
  const marker = document.getElementById('time-marker');
  if (!marker) return;
  const now = new Date();
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  const pct = (h / 24) * 100;
  marker.style.left = `${pct}%`;
}

/* ── Info Cards ────────────────────────────────────────── */

function updateInfoCards(data) {
  if (data?.volatility) {
    const volBadge = document.getElementById('vol-badge');
    if (volBadge) volBadge.textContent = `${data.volatility.regime?.toUpperCase()} · ${data.volatility.atr_pct || '—'}%`;
    const atrEl = document.getElementById('vol-atr');
    if (atrEl) atrEl.textContent = `ATR: ${data.volatility.atr_value || '—'}`;

    document.querySelectorAll('.vol-segment').forEach(seg => {
      seg.classList.toggle('active', seg.dataset.level === data.volatility.regime?.toLowerCase());
    });
  }

  if (data?.session) {
    const nameEl = document.getElementById('session-name');
    if (nameEl) nameEl.textContent = data.session.name;

    // Populate session countdown using closes_at from API
    const closesRaw = data.session.closes_at; // e.g. "12:00 UTC"
    const countdownEl = document.getElementById('session-countdown');
    const closesEl = document.getElementById('session-closes');
    if (closesEl && closesRaw) closesEl.textContent = `Closes ${closesRaw}`;
    if (countdownEl && closesRaw) {
      try {
        const [hStr] = closesRaw.split(':');
        const closeHour = parseInt(hStr, 10);
        const now = new Date();
        const closeUtc = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), closeHour, 0, 0));
        if (closeUtc <= now) closeUtc.setUTCDate(closeUtc.getUTCDate() + 1);
        const diffMs = closeUtc - now;
        const hh = Math.floor(diffMs / 3600000);
        const mm = Math.floor((diffMs % 3600000) / 60000);
        countdownEl.textContent = `${hh}h ${mm}m remaining`;
      } catch { countdownEl.textContent = '—'; }
    }
  }

  const agentEl = document.getElementById('live-agent-status');
  const agentDetailEl = document.getElementById('live-agent-detail');
  const bannerEl = document.getElementById('live-status-banner');

  if (data?.bot_running) {
    if (agentEl) { agentEl.textContent = '● Agent Active'; agentEl.classList.add('online'); }
    if (agentDetailEl) { agentDetailEl.textContent = 'Agent running'; agentDetailEl.style.color = 'var(--green)'; }
    if (bannerEl) { bannerEl.textContent = `Agent active — trading ${selectedSymbol}`; bannerEl.classList.add('active'); }
  }
}

function updateClock() {
  const el = document.getElementById('live-clock');
  if (el) el.textContent = new Date().toLocaleTimeString();
}
