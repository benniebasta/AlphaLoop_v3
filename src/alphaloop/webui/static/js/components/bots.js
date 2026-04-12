/**
 * Alpha Agents — Live Execution Fleet
 * Features: strategy card picker, loop status panel, card identity, same-symbol badge, evolution badge
 */
import { apiGet, apiPost, apiDelete } from '../api.js';
import { playTradeOpened, playTradeClosedProfit, playTradeClosedLoss, playEvolution } from '../sounds.js';

const STATUS_COLORS = {
  candidate: 'var(--amber)',
  dry_run: 'var(--blue)',
  demo: 'var(--purple)',
  live: 'var(--green)',
  retired: 'var(--muted)',
};

const MODE_COLORS = {
  algo_only:    'var(--amber)',
  algo_ai:      'var(--blue)',
  ai_signal:    'var(--purple)',
};

const TOOL_LABELS = {
  session_filter: 'Session', news_filter: 'News', volatility_filter: 'Volatility',
  dxy_filter: 'DXY', sentiment_filter: 'Sentiment', risk_filter: 'Risk',
  bos_guard: 'BOS', fvg_guard: 'FVG', vwap_guard: 'VWAP', correlation_guard: 'Corr',
  ema200_filter: 'EMA200', macd_filter: 'MACD', bollinger_filter: 'BB',
  adx_filter: 'ADX', volume_filter: 'Volume', swing_structure: 'Swing',
};

// Per-instance loop state, populated by WebSocket events
const loopStates = {};
const manualTradeDir = {};   // instance_id → 'BUY' | 'SELL' | null


function timeAgo(ts) {
  if (!ts) return '—';
  const ms = Date.now() - new Date(ts).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s ago`;
  return `${Math.floor(m / 60)}h ${m % 60}m ago`;
}

function stratBaseName(name) {
  return (name || 'Strategy').replace(/_v\d+$/i, '');
}

function stratVersionBadge(version) {
  const ver = (version != null && version !== '') ? version : '?';
  return `<span style="font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;background:var(--bg3);color:var(--text);border:1px solid var(--border)">V${ver}</span>`;
}

function formatTick(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

function signalModeLabel(mode) {
  if (mode === 'algo_only') return 'ALGO ONLY';
  if (mode === 'algo_ai') return 'ALGO + AI';
  if (mode === 'ai_signal') return 'AI SIGNAL';
  return (mode || 'UNKNOWN').replace(/_/g, ' ').toUpperCase();
}

function signalModePill(mode) {
  const label = signalModeLabel(mode);
  const color = MODE_COLORS[mode] || 'var(--blue)';
  return `<span class="badge" style="background:${color};color:#000;font-size:10px;padding:2px 6px;margin-left:4px">${label}</span>`;
}

/* ─── Card identity block (bound strategy) ─────────────────────────────── */

function cardIdentityBlock(strat) {
  if (!strat) return '<div style="font-size:11px;color:var(--muted);margin-top:8px">No strategy bound</div>';
  const m = strat.metrics || {};
  const wr = m.win_rate || 0;
  const sharpe = m.sharpe || 0;
  const dd = m.max_dd_pct || 0;
  const pnl = m.total_pnl || 0;
  const statusColor = STATUS_COLORS[strat.status] || 'var(--muted)';
  const mode = strat.signal_mode || 'algo_ai';

  const baseTools = strat.tools
    ? Object.entries(strat.tools).filter(([, on]) => on).map(([name]) =>
        `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg3);color:var(--text);border:1px solid var(--border);white-space:nowrap">${TOOL_LABELS[name] || name}</span>`
      )
    : [];
  const overlayTools = (strat.overlay || []).map(name =>
    `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:transparent;color:var(--amber);border:1px dashed var(--amber);white-space:nowrap" title="Overlay tool">+${TOOL_LABELS[name] || name}</span>`
  );
  const allTools = [...baseTools, ...overlayTools];
  const toolsRow = allTools.length > 0
    ? `<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:6px">${allTools.join('')}</div>`
    : '';

  const aiLabel = (mode === 'algo_ai' || mode === 'ai_signal')
    ? `<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:var(--blue);color:#000;font-weight:600">AI</span>`
    : '';

  return `
    <div style="border-top:1px solid var(--border);margin-top:10px;padding-top:10px">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap">
        ${signalModePill(mode)}
        ${aiLabel}
        <span class="badge" style="background:${statusColor};color:#000;font-size:10px;padding:2px 6px">${strat.status || '—'}</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;text-align:center">
        <div>
          <div style="font-size:12px;font-weight:600;color:${wr >= 0.5 ? 'var(--green)' : 'var(--red)'}">${(wr * 100).toFixed(1)}%</div>
          <div style="font-size:10px;color:var(--muted)">WR</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:${sharpe >= 0.5 ? 'var(--green)' : 'var(--red)'}">${sharpe.toFixed(2)}</div>
          <div style="font-size:10px;color:var(--muted)">Sharpe</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:${pnl >= 0 ? 'var(--green)' : 'var(--red)'}">$${pnl.toFixed(0)}</div>
          <div style="font-size:10px;color:var(--muted)">P&L</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:var(--red)">${dd.toFixed(1)}%</div>
          <div style="font-size:10px;color:var(--muted)">Max DD</div>
        </div>
      </div>
      ${toolsRow}
    </div>`;
}

/* ─── Loop status panel ─────────────────────────────────────────────────── */

function loopStatusPanel(instanceId) {
  const state = loopStates[instanceId];
  if (!state || !state.lastEvent) {
    return `
      <div class="loop-panel" id="loop-${instanceId}" style="display:none">
        <div style="font-size:11px;color:var(--muted);padding:8px 0">Waiting for first cycle...</div>
      </div>`;
  }

  const s = state;
  const pipeRows = (s.pipeline || []).map(p => {
    const icon = p.passed ? '<span style="color:var(--green)">&#10003;</span>' : '<span style="color:var(--red)">&#10007;</span>';
    const reason = p.reason ? ` — <span style="color:var(--muted)">${p.reason}</span>` : '';
    return `<div style="display:flex;gap:6px;font-size:11px;padding:1px 0">${icon} <span>${p.name}</span>${reason}</div>`;
  }).join('');

  const sigDir = s.signal?.direction || '—';
  const sigConf = s.signal?.confidence ? `${Math.round(s.signal.confidence * 100)}%` : '—';
  const sigMode = s.signal?.signal_mode ? signalModeLabel(s.signal.signal_mode) : '—';
  const valStatus = s.validation?.approved === true
    ? '<span style="color:var(--green)">&#10003; Approved</span>'
    : s.validation?.approved === false
    ? `<span style="color:var(--red)">&#10007; Rejected</span> — ${s.validation.reason || ''}`
    : '<span style="color:var(--muted)">—</span>';

  const riskRows = (s.risk || []).map(r => {
    const color = r.blocked ? 'color:var(--red)' : '';
    return `<div style="font-size:11px;padding:1px 0;${color}">${r.label}: ${r.value}</div>`;
  }).join('');

  return `
    <div class="loop-panel" id="loop-${instanceId}" style="display:none">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:11px;color:var(--muted)">Last event: ${timeAgo(s.lastEvent)}</span>
      </div>
      ${pipeRows ? `<div style="margin-bottom:6px"><div style="font-size:10px;font-weight:600;color:var(--muted);margin-bottom:3px">PIPELINE</div>${pipeRows}</div>` : ''}
      <div style="margin-bottom:6px">
        <div style="font-size:10px;font-weight:600;color:var(--muted);margin-bottom:3px">SIGNAL</div>
        <div style="font-size:11px">Direction: <strong>${sigDir}</strong> &nbsp; Conf: ${sigConf} &nbsp; Mode: ${sigMode}</div>
        <div style="font-size:11px">Validation: ${valStatus}</div>
      </div>
      ${riskRows ? `<div><div style="font-size:10px;font-weight:600;color:var(--muted);margin-bottom:3px">RISK</div>${riskRows}</div>` : ''}
      ${s.lastTrade ? `<div style="margin-top:6px;padding:4px 8px;background:var(--bg3);border-radius:4px;font-size:11px;color:var(--green)">Order Placed: ${s.lastTrade}</div>` : ''}
    </div>`;
}

/* ─── Agent card ────────────────────────────────────────────────────────── */

function agentCard(b, symbolCounts) {
  const strat = b.strategy || null;
  const multiCount = symbolCounts[b.symbol] || 1;
  const multiBadge = multiCount > 1
    ? `<span class="badge" style="background:var(--amber);color:#000;font-size:10px;padding:2px 6px;margin-left:6px">${multiCount}x ${b.symbol}</span>`
    : '';

  return `
    <div class="bot-card bot-active" data-instance="${b.instance_id}" data-symbol="${b.symbol}">
      <div class="bot-card-header">
        <div class="bot-live-dot"></div>
        <div class="bot-symbol" style="display:flex;align-items:center;gap:6px">
          ${strat ? stratBaseName(strat.name) : b.symbol}
          ${strat ? stratVersionBadge(strat.version) : ''}
        </div>
        <span class="badge badge-green">Active</span>
        ${multiBadge}
      </div>
      <div class="bot-stats">
        <div class="bot-stat">
          <div class="bot-stat-val"><span class="uptime-counter" data-start="${b.started_at ? new Date(b.started_at).getTime() : Date.now()}">${b.started_at ? formatTick(Math.floor((Date.now() - new Date(b.started_at).getTime()) / 1000)) : '0:00'}</span></div>
          <div class="bot-stat-lbl">Uptime</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-val mono-sm">${b.pid}</div>
          <div class="bot-stat-lbl">PID</div>
        </div>
      </div>
      ${cardIdentityBlock(strat)}
      <div id="loop-wrap-${b.instance_id}" style="margin-top:8px;display:${loopStates[b.instance_id]?.lastEvent ? 'block' : 'none'}">
        <button class="btn btn-sm loop-toggle" data-loop="${b.instance_id}" style="width:100%;text-align:left;font-size:11px;padding:4px 8px">
          &#9660; Loop Status
        </button>
        ${loopStatusPanel(b.instance_id)}
      </div>
      <div class="bot-id mono-sm" style="font-size:10px;margin-top:8px">${b.instance_id}</div>
      <div id="evo-badge-${b.instance_id}" style="display:none;margin-top:4px"></div>
      <div class="bot-actions">
        <button class="btn btn-sm raw-log-btn" data-symbol="${b.symbol}" data-instance="${b.instance_id}" style="flex:1">📋 Raw Log</button>
        <button class="btn btn-sm manual-trade-toggle" data-instance="${b.instance_id}" title="Open/close manual trade">⚡ Trade</button>
        <button class="btn btn-danger btn-sm" data-stop="${b.instance_id}" title="Stop agent">Stop</button>
        <button class="btn btn-danger btn-sm" data-remove="${b.instance_id}" title="Remove record">Remove</button>
      </div>
      <div id="manual-trade-panel-${b.instance_id}" style="display:none;margin-top:8px;border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--bg2)">
        <div style="font-size:11px;font-weight:700;margin-bottom:8px;color:var(--gold)">Manual Trade <span style="font-weight:400;color:var(--muted)">(dry run)</span></div>
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <button class="btn btn-sm manual-dir-btn" data-dir="BUY" data-instance="${b.instance_id}" style="flex:1;border:2px solid var(--green);color:var(--green);background:transparent;font-weight:700">BUY</button>
          <button class="btn btn-sm manual-dir-btn" data-dir="SELL" data-instance="${b.instance_id}" style="flex:1;border:2px solid var(--red);color:var(--red);background:transparent;font-weight:700">SELL</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px">
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:2px">Lots</div>
            <input type="number" class="manual-lots" data-instance="${b.instance_id}" value="0.01" step="0.01" min="0.01" style="width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid var(--border);border-radius:4px;background:var(--bg3);color:var(--text);font-size:12px">
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:2px">Price (opt)</div>
            <input type="number" class="manual-price" data-instance="${b.instance_id}" placeholder="auto" step="0.00001" style="width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid var(--border);border-radius:4px;background:var(--bg3);color:var(--text);font-size:12px">
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:2px">SL (opt)</div>
            <input type="number" class="manual-sl" data-instance="${b.instance_id}" placeholder="0.0" step="0.00001" style="width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid var(--border);border-radius:4px;background:var(--bg3);color:var(--text);font-size:12px">
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:2px">TP (opt)</div>
            <input type="number" class="manual-tp" data-instance="${b.instance_id}" placeholder="0.0" step="0.00001" style="width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid var(--border);border-radius:4px;background:var(--bg3);color:var(--text);font-size:12px">
          </div>
        </div>
        <button class="btn btn-sm manual-open-btn" data-instance="${b.instance_id}" disabled style="width:100%;font-weight:600;opacity:0.5">Select BUY or SELL</button>
        <div id="manual-trades-${b.instance_id}" style="margin-top:10px"></div>
      </div>
    </div>`;
}

/* ─── Strategy card picker (for deploy modal) ──────────────────────────── */

function strategyPickerHTML(strategies, activeVersion) {
  if (!strategies || strategies.length === 0) {
    return `<div style="padding:12px;color:var(--muted);font-size:12px;text-align:center;border:1px dashed var(--border);border-radius:6px">
      No strategies found for this symbol. Train one in SeedLab first.
    </div>`;
  }
  return strategies.map(s => {
    const sum = s.summary || {};
    const wr = sum.win_rate || 0;
    const sharpe = sum.sharpe || 0;
    const dd = sum.max_dd_pct || 0;
    const mode = s.signal_mode || 'algo_ai';
    const isActive = s.version === activeVersion;
    const isCandidate = s.status === 'candidate';
    const statusColor = STATUS_COLORS[s.status] || 'var(--muted)';
    return `
      <label class="strategy-pick-card" style="display:flex;gap:10px;align-items:center;padding:8px 12px;border:1px solid var(--border);border-radius:6px;${isCandidate ? 'opacity:0.55;cursor:not-allowed' : 'cursor:pointer'};margin-bottom:4px;overflow:hidden${isActive ? ';border-color:var(--gold)' : ''}">
        <input type="radio" name="pick-strategy" value="${s.version}" data-name="${s.name}" ${isActive ? 'checked' : ''} ${isCandidate ? 'disabled' : ''} style="accent-color:var(--gold);width:auto;flex-shrink:0">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap">
            ${isActive ? '<span style="color:var(--gold);font-size:11px">&#9733; Active</span>' : ''}
            <span style="font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px">${stratBaseName(s.name)}</span>
            ${stratVersionBadge(s.version)}
            ${signalModePill(mode)}
            <span class="badge" style="background:${statusColor};color:#000;font-size:10px;padding:2px 6px">${s.status}</span>
            ${isCandidate ? '<span style="font-size:10px;color:var(--amber)">— promote to deploy</span>' : ''}
          </div>
          <div style="display:flex;gap:12px;margin-top:4px;font-size:11px;color:var(--muted)">
            <span>WR: <strong style="color:${wr >= 0.5 ? 'var(--green)' : 'var(--red)'}">${(wr * 100).toFixed(1)}%</strong></span>
            <span>Sharpe: <strong>${sharpe.toFixed(2)}</strong></span>
            <span>DD: <strong style="color:var(--red)">${dd.toFixed(1)}%</strong></span>
          </div>
        </div>
      </label>`;
  }).join('');
}

/* ─── WebSocket event handler for loop status + evolution badges ─────── */

function handleWSEvent(data, botsData) {
  if (!data || !data.type || !data.symbol) return;

  // Find matching instance(s) by symbol
  const matchingBots = (botsData || []).filter(b => b.symbol === data.symbol);
  if (matchingBots.length === 0) return;

  for (const bot of matchingBots) {
    const id = bot.instance_id;
    if (!loopStates[id]) {
      loopStates[id] = { pipeline: [], signal: null, validation: null, risk: [], lastTrade: null, lastEvent: null };
    }
    const st = loopStates[id];
    const firstEvent = !st.lastEvent;
    st.lastEvent = data.timestamp || new Date().toISOString();

    // Reveal the Loop Status section on first event
    if (firstEvent) {
      const wrap = document.getElementById(`loop-wrap-${id}`);
      if (wrap) wrap.style.display = 'block';
    }

    switch (data.type) {
      case 'PipelineBlocked':
        st.pipeline = st.pipeline.filter(p => p.name !== data.blocked_by);
        st.pipeline.push({ name: data.blocked_by, passed: false, reason: data.reason });
        st.signal = null;
        st.validation = null;
        st.lastTrade = null;
        break;

      case 'SignalGenerated':
        st.pipeline = st.pipeline.map(p => ({ ...p, passed: true, reason: '' }));
        st.signal = {
          direction: data.signal?.direction || data.direction || '—',
          confidence: data.signal?.confidence || data.confidence || 0,
          signal_mode: data.signal_mode || '—',
        };
        st.validation = null;
        st.lastTrade = null;
        break;

      case 'SignalValidated':
        st.validation = { approved: data.approved !== false, reason: '' };
        break;

      case 'SignalRejected':
        st.validation = { approved: false, reason: `${data.rejected_by}: ${data.reason}` };
        break;

      case 'TradeOpened':
        st.lastTrade = `${data.direction} ${data.symbol} @ ${data.entry_price} (${data.lot_size} lots)`;
        playTradeOpened();
        break;

      case 'TradeClosed':
        st.lastTrade = null;
        if ((data.pnl_usd ?? 0) >= 0) playTradeClosedProfit();
        else playTradeClosedLoss();
        break;

      case 'RiskLimitHit':
        st.risk = [{ label: data.limit_type, value: data.details, blocked: true }];
        break;

      case 'StrategyPromoted':
      case 'StrategyRolledBack': {
        const evoBadge = document.getElementById(`evo-badge-${id}`);
        if (evoBadge) {
          const isPromo = data.type === 'StrategyPromoted';
          const color = isPromo ? 'var(--green)' : 'var(--amber)';
          const label = isPromo
            ? `Evolved: ${data.from_status} -> ${data.to_status}`
            : `Rolled back -> v${data.to_version}`;
          evoBadge.innerHTML = `<span class="badge" style="background:${color};color:#000;font-size:10px;padding:2px 6px">${label}</span>`;
          evoBadge.style.display = 'block';
          setTimeout(() => { evoBadge.style.display = 'none'; }, 60000);
        }
        if (data.type === 'StrategyPromoted') playEvolution();
        break;
      }
    }

    // Update the loop panel DOM if visible
    const panel = document.getElementById(`loop-${id}`);
    if (panel && panel.style.display !== 'none') {
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = loopStatusPanel(id);
      const newPanel = tempDiv.querySelector('.loop-panel');
      if (newPanel) {
        newPanel.style.display = 'block';
        panel.replaceWith(newPanel);
      }
    }
  }
}

/* ─── Main render ───────────────────────────────────────────────────────── */

export async function render(container) {
  let currentBots = [];

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">Alpha Agents</div>
      <div class="page-subtitle">Live Execution Fleet — deploy and manage trading agents</div>
    </div>
    <div class="deploy-agent-bar">
      <button class="btn-gradient" id="deploy-agent-btn">Deploy Agent</button>
      <button class="btn btn-sm" id="agents-refresh" style="margin-left:auto">Refresh</button>
    </div>

    <!-- Deploy Modal -->
    <div id="deploy-modal" style="display:none">
      <div class="card" style="margin-bottom:16px;border-color:var(--primary-border)">
        <div style="font-size:14px;font-weight:600;margin-bottom:12px">Deploy New Agent</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
          <div class="form-group">
            <label>Symbol</label>
            <select id="deploy-symbol" class="field-input">
              <option value="XAUUSD">XAUUSD — Gold</option>
              <option value="BTCUSD">BTCUSD — Bitcoin</option>
              <option value="EURUSD">EURUSD — Euro/USD</option>
              <option value="GBPUSD">GBPUSD — GBP/USD</option>
              <option value="NAS100">NAS100 — Nasdaq</option>
              <option value="US30">US30 — Dow Jones</option>
            </select>
          </div>
          <div class="form-group">
            <label>Mode</label>
            <select id="deploy-mode" class="field-input">
              <option value="dry">Dry Run (Safe)</option>
              <option value="live">Live Trading</option>
            </select>
          </div>
        </div>

        <!-- Risk Budget Slider -->
        <div class="form-group" style="margin-bottom:12px">
          <label>Risk Budget: <strong id="risk-budget-val">100%</strong></label>
          <div style="display:flex;gap:6px;align-items:center">
            <input type="range" id="deploy-risk-budget" min="25" max="100" step="25" value="100"
              style="flex:1;accent-color:var(--gold)">
            <span id="risk-budget-hint" style="font-size:10px;color:var(--muted)"></span>
          </div>
        </div>

        <!-- Cycle Interval -->
        <div class="form-group" style="margin-bottom:12px">
          <label style="font-size:11px;color:var(--muted)">Cycle Interval</label>
          <select id="deploy-poll-interval" class="field-input" style="font-size:12px">
            <option value="60">60 s — active monitoring</option>
            <option value="120">120 s — balanced</option>
            <option value="300">300 s — conservative</option>
          </select>
        </div>

        <!-- Strategy Picker -->
        <div class="form-group" style="margin-bottom:12px">
          <label>Strategy Card</label>
          <div id="strategy-picker" style="max-height:240px;overflow-y:auto;overflow-x:hidden;width:100%">
            <div style="font-size:11px;color:var(--muted)">Select a symbol first...</div>
          </div>
        </div>

        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="btn btn-sm" id="deploy-cancel">Cancel</button>
          <button class="btn-gradient" id="deploy-confirm">Launch</button>
        </div>
      </div>
    </div>

    <div id="agents-content"><div class="dash-loading">Loading...</div></div>

    <!-- Raw Signal Log Modal -->
    <div id="raw-log-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;padding:24px;box-sizing:border-box">
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;max-width:820px;margin:0 auto;height:100%;display:flex;flex-direction:column">
        <!-- Header -->
        <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border);flex-shrink:0">
          <div>
            <div style="font-weight:700;font-size:14px">📋 Raw Signal Log</div>
            <div id="raw-log-subtitle" style="font-size:11px;color:var(--muted);margin-top:2px"></div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <span id="raw-log-live-dot" style="font-size:10px;color:var(--muted)">⏸ paused</span>
            <button id="raw-log-refresh" class="btn btn-sm" style="font-size:11px">↻ Refresh</button>
            <button id="raw-log-close" class="btn btn-sm" style="font-size:11px">✕ Close</button>
          </div>
        </div>
        <!-- Pipeline Status Grid -->
        <div style="flex-shrink:0;padding:12px 18px;border-bottom:1px solid var(--border)">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px">Pipeline Status</div>
          <div id="raw-log-pipeline" style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px"></div>
        </div>
        <!-- Raw Event Stream -->
        <div style="flex-shrink:0;padding:8px 18px 4px;border-bottom:1px solid var(--border)">
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Event Stream</div>
        </div>
        <div id="raw-log-body" style="overflow-y:auto;flex:1;padding:8px 18px;font-family:monospace;font-size:11px;line-height:1.6"></div>
      </div>
    </div>`;

  // ─── Raw Signal Log modal ───────────────────────────────────────────
  const rawLogModal    = document.getElementById('raw-log-modal');
  const rawLogBody     = document.getElementById('raw-log-body');
  const rawLogPipeline = document.getElementById('raw-log-pipeline');
  const rawLogSub      = document.getElementById('raw-log-subtitle');
  const rawLogLiveDot  = document.getElementById('raw-log-live-dot');
  let   _rawLogSymbol = '', _rawLogInstance = '';
  let   _rawLogTimer  = null;

  const RAW_LOG_EVENT_STAGES = [
    { type: 'CycleStarted',      icon: '🔄', label: 'Cycle' },
    { type: 'SignalGenerated',   icon: '📡', label: 'Signal Gen' },
    { type: 'SignalValidated',   icon: '✅', label: 'Validated' },
    { type: 'SignalRejected',    icon: '❌', label: 'Rejected' },
    { type: 'TradeOpened',       icon: '📈', label: 'Trade Open' },
    { type: 'TradeClosed',       icon: '📉', label: 'Trade Close' },
    { type: 'PipelineBlocked',   icon: '⛔', label: 'Blocked' },
    { type: 'RiskLimitHit',      icon: '⚠️', label: 'Risk Limit' },
    { type: 'TradeRepositioned', icon: '🔧', label: 'Repositioned' },
    { type: 'CycleCompleted',    icon: '✔', label: 'Cycle Done' },
    { type: 'StrategyPromoted',  icon: '🏆', label: 'Promoted' },
  ];

  const RAW_LOG_PIPELINE_LAYOUTS = {
    classic: [
      { kind: 'event', key: 'CycleStarted' },
      { kind: 'step', key: 'risk_check' },
      { kind: 'step', key: 'filters' },
      { kind: 'step', key: 'signal_gen' },
      { kind: 'step', key: 'validation' },
      { kind: 'step', key: 'guards' },
      { kind: 'step', key: 'sizing' },
      { kind: 'step', key: 'execution' },
      { kind: 'event', key: 'PipelineBlocked' },
      { kind: 'event', key: 'CycleCompleted' },
      { kind: 'event', key: 'TradeRepositioned' },
      { kind: 'event', key: 'StrategyPromoted' },
    ],
    ai_signal: [
      { kind: 'event', key: 'CycleStarted' },
      { kind: 'step', key: 'risk_check' },
      { kind: 'step', key: 'filters' },
      { kind: 'step', key: 'ai_signal_gen' },
      { kind: 'step', key: 'ai_validator' },
      { kind: 'step', key: 'ai_research' },
      { kind: 'step', key: 'ai_optimizer' },
      { kind: 'step', key: 'ai_regime' },
      { kind: 'step', key: 'ai_fallback' },
      { kind: 'step', key: 'validation' },
      { kind: 'step', key: 'execution' },
      { kind: 'event', key: 'CycleCompleted' },
    ],
    algo_ai: [
      { kind: 'event', key: 'CycleStarted' },
      { kind: 'step', key: 'risk_check' },
      { kind: 'step', key: 'market_gate' },
      { kind: 'step', key: 'regime' },
      { kind: 'step', key: 'hypothesis' },
      { kind: 'step', key: 'construction' },
      { kind: 'step', key: 'signal_gen' },
      { kind: 'step', key: 'conviction' },
      { kind: 'step', key: 'risk_gate' },
      { kind: 'step', key: 'execution_guard' },
      { kind: 'step', key: 'execution' },
      { kind: 'event', key: 'PipelineBlocked' },
      { kind: 'event', key: 'CycleCompleted' },
    ],
  };

  const EVENT_COLORS = {
    CycleStarted:      'var(--muted)',
    CycleCompleted:    'var(--muted)',
    TradeRepositioned: 'var(--amber)',
    PipelineBlocked:   'var(--amber)',
    SignalGenerated:  'var(--blue)',
    SignalValidated:  'var(--green)',
    SignalRejected:   'var(--red)',
    TradeOpened:      'var(--green)',
    TradeClosed:      'var(--muted)',
    RiskLimitHit:     'var(--red)',
    StrategyPromoted: 'var(--purple)',
  };

  function _timeAgoShort(isoTs) {
    const diffMs = Date.now() - new Date(isoTs).getTime();
    const s = Math.floor(diffMs / 1000);
    if (s < 60)   return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60)   return `${m}m ago`;
    return `${Math.floor(m / 60)}h ago`;
  }

  function _serializeValue(value) {
    if (typeof value === 'number') return String(value);
    if (typeof value === 'string') return value;
    return JSON.stringify(value);
  }

  function _extractRuleCodes(text) {
    if (!text) return [];
    return [...String(text).matchAll(/\[([^\]]+)\]/g)].map(m => m[1]);
  }

  function _summarizeRuleFailures(text) {
    const codes = _extractRuleCodes(text);
    if (!codes.length) return text || '';
    const uniq = [...new Set(codes)];
    return `${uniq.length} rule fail${uniq.length === 1 ? '' : 's'}: ${uniq.join(', ')}`;
  }

  function _compactSignalSummary(direction, confidence, setup, mode = '') {
    const parts = [];
    if (direction) parts.push(direction);
    if (confidence != null) parts.push(`${Math.round(confidence * 100)}%`);
    if (setup) parts.push(setup);
    if (mode) parts.push(mode);
    return parts.join(' • ');
  }

  function _eventDetail(ev) {
    if (!ev?.data) return '';
    if (ev.type === 'SignalGenerated') {
      const direction = ev.data.direction || ev.data.signal?.direction || '';
      const confidence = ev.data.confidence ?? ev.data.signal?.confidence;
      const setup = ev.data.setup || ev.data.signal?.setup || '';
      const parts = [];
      if (direction) parts.push(`direction:${direction}`);
      if (confidence != null) parts.push(`confidence:${Math.round(confidence * 100)}%`);
      if (setup) parts.push(`setup:${setup}`);
      if (parts.length) return parts.join(' ');
    }
    const skip = new Set(['symbol', 'instance_id', 'timestamp']);
    return Object.entries(ev.data)
      .filter(([k]) => !skip.has(k))
      .slice(0, 2)
      .map(([k, v]) => `${k}:${_serializeValue(v)}`)
      .join(' ');
  }

  function _compactEventText(e) {
    const data = e?.data || {};
    if (e.type === 'SignalGenerated') {
      const direction = data.direction || data.signal?.direction || '';
      const confidence = data.confidence ?? data.signal?.confidence;
      const setup = data.setup || data.signal?.setup || '';
      return _compactSignalSummary(direction, confidence, setup);
    }

    if (e.type === 'SignalRejected') {
      const by = data.rejected_by || 'validator';
      const reason = data.reason || '';
      const summary = _summarizeRuleFailures(reason);
      const aiNote = data.validator_reasoning ? ` — ${data.validator_reasoning}` : '';
      return `${by}${summary ? ` • ${summary}` : ''}${aiNote}`;
    }

    if (e.type === 'CycleCompleted') {
      const outcome = data.outcome || 'done';
      const detail = data.detail || '';
      const summary = outcome === 'rejected' ? _summarizeRuleFailures(detail) : detail;
      return `${outcome}${summary ? ` • ${summary}` : ''}`;
    }

    if (e.type === 'CycleStarted') {
      return data.cycle != null ? `cycle:${data.cycle}` : '';
    }

    if (e.type === 'SignalValidated') {
      return data.approved === false ? 'rejected' : 'approved';
    }

    if (e.type === 'TradeOpened') {
      const direction = data.direction || '';
      const entry = data.entry_price != null ? `@ ${data.entry_price}` : '';
      const lots = data.lot_size != null ? `${data.lot_size} lots` : '';
      return [direction, entry, lots].filter(Boolean).join(' ');
    }

    if (e.type === 'PipelineBlocked') {
      const blocker = data.blocked_by || 'pipeline';
      const reason = data.reason || '';
      return `${blocker}${reason ? ` • ${reason}` : ''}`;
    }

    return _eventDetail(e);
  }

  function _extractToolValue(r) {
    const d = r.data || {};
    const parts = [];
    if (d.score != null)              parts.push(`score=${(+d.score).toFixed(2)}`);
    if (d.atr_pct != null)            parts.push(`atr=${(+d.atr_pct).toFixed(3)}%`);
    if (d.atr != null && d.atr_pct == null) parts.push(`atr=${(+d.atr).toFixed(2)}`);
    if (d.regime != null)             parts.push(`${d.regime}`);
    if (d.session != null)            parts.push(`${d.session}`);
    if (d.adx != null)                parts.push(`adx=${(+d.adx).toFixed(1)}`);
    if (d.bb_pct_b != null)           parts.push(`%b=${(+d.bb_pct_b).toFixed(2)}`);
    if (d.macd_histogram != null)     parts.push(`hist=${(+d.macd_histogram).toFixed(4)}`);
    if (d.volume_ratio != null)       parts.push(`vol×${(+d.volume_ratio).toFixed(2)}`);
    if (d.tick_jump_atr != null)      parts.push(`jump=${(+d.tick_jump_atr).toFixed(2)}ATR`);
    if (d.vwap != null)               parts.push(`vwap=${(+d.vwap).toFixed(2)}`);
    if (d.extension_atr != null)      parts.push(`ext=${d.extension_atr}ATR`);
    if (d.effective_exposure != null) parts.push(`corr=${((+d.effective_exposure)*100).toFixed(0)}%`);
    if (d.swing_structure != null)    parts.push(`${d.swing_structure}`);
    if (parts.length > 0) return parts.join('  ');
    const reason = r.reason || '';
    return reason.length > 52 ? reason.slice(0, 49) + '…' : reason;
  }

  function _compactStepDetail(stage, status, detail) {
    if (!detail) return '';

    if (stage === 'validation' && status === 'rejected') {
      return _summarizeRuleFailures(detail);
    }

    if (stage === 'signal_gen' || stage === 'ai_signal_gen') {
      const dir = detail.match(/\b(BUY|SELL)\b/i)?.[1]?.toUpperCase() || '';
      const conf = detail.match(/conf:(\d+(?:\.\d+)?)/i)?.[1];
      const setup = detail.match(/setup:([a-z_]+)/i)?.[1] || '';
      const confidence = conf != null ? Number(conf) : null;
      const summary = _compactSignalSummary(dir, confidence, setup);
      return summary || detail;
    }

    return detail;
  }

  function _detectRawLogMode(latestSteps) {
    if (latestSteps.ai_signal_gen || latestSteps.ai_validator || latestSteps.ai_research) {
      return 'ai_signal';
    }
    if (latestSteps.features || latestSteps.scoring || latestSteps.ai_review) {
      return 'algo_ai';
    }
    return 'classic';
  }

  function _getPipelineCardMeta(card) {
    if (card.kind === 'step') {
      return _STEP_META[card.key] || { icon: '•', label: card.key };
    }
    return _EVENT_META[card.key] || { icon: '•', label: card.key };
  }

  function _renderPipelineGrid(events) {
    const latestEvents = {};
    const latestSteps = {};
    for (const e of events) {
      if (!latestEvents[e.type]) latestEvents[e.type] = e;
      if (e.type === 'PipelineStep') {
        const stage = e.data?.stage;
        if (stage && !latestSteps[stage]) latestSteps[stage] = e;
      }
    }

    const mode = _detectRawLogMode(latestSteps);
    const cards = RAW_LOG_PIPELINE_LAYOUTS[mode] || RAW_LOG_PIPELINE_LAYOUTS.classic;

    rawLogPipeline.innerHTML = cards.map(card => {
      const meta = _getPipelineCardMeta(card);
      const ev = card.kind === 'step' ? latestSteps[card.key] : latestEvents[card.key];
      const hasEv = !!ev;
      const ts = hasEv ? _timeAgoShort(ev.timestamp) : null;
      const status = ev?.data?.status || '';
      const color = card.kind === 'step'
        ? (_STATUS_COLORS[status] || 'var(--text)')
        : (EVENT_COLORS[card.key] || 'var(--text)');
      const detail = card.kind === 'step' ? (ev?.data?.detail || '') : _eventDetail(ev);
      // Filters card: show "N tools | size:X.XX bias" instead of the full pipe list
      let gridDetail = detail;
      // Validation rejected: surface AI reasoning from SignalRejected event
      if (card.kind === 'step' && card.key === 'validation' && status === 'rejected') {
        const rejEv = latestEvents['SignalRejected'];
        const aiReasoning = rejEv?.data?.validator_reasoning || '';
        gridDetail = aiReasoning || detail || '';
      }
      if (card.kind === 'step' && card.key === 'filters' && ev?.data?.results?.length) {
        const n = ev.data.results.length;
        const size = ev.data.detail?.match(/size:([\d.]+)/)?.[1] || '1.00';
        const bias = ev.data.detail?.match(/bias:(\w+)/)?.[1] || '';
        gridDetail = `${n} tools | size:${size}${bias ? ' ' + bias : ''}`;
      }
      const stateLine = card.kind === 'step'
        ? (_STATUS_BADGE[status] || status || 'waiting...')
        : (detail || '—');

      return `<div style="
          background:var(--bg3);
          border:1px solid ${hasEv ? color : 'var(--border)'};
          border-radius:6px;
          padding:7px 9px;
          min-width:0;
        ">
        <div style="display:flex;align-items:center;gap:5px;margin-bottom:3px">
          <span style="font-size:13px">${meta.icon}</span>
          <span style="font-size:10px;font-weight:700;color:${hasEv ? color : 'var(--muted)'};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${meta.label}</span>
          ${hasEv ? `<span style="margin-left:auto;font-size:9px;color:var(--muted);white-space:nowrap">${ts}</span>` : ''}
        </div>
        <div style="font-size:9px;color:var(--muted);font-family:monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
          ${hasEv ? stateLine : '<span style="opacity:.45">waiting...</span>'}
        </div>
        <div style="font-size:9px;color:var(--muted);font-family:monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px">
          ${hasEv ? (gridDetail || '—') : '<span style="opacity:.45">waiting...</span>'}
        </div>
      </div>`;
    }).join('');
  }

  const _EVENT_META = Object.fromEntries(RAW_LOG_EVENT_STAGES.map(s => [s.type, s]));

  // Stage icons + labels for PipelineStep events
  const _STEP_META = {
    // Shared
    risk_check:      { icon: '🛡', label: 'Risk Check' },
    signal_gen:      { icon: '📡', label: 'Signal Gen' },
    validation:      { icon: '✅', label: 'Validation' },
    guards:          { icon: '🧱', label: 'Guards' },
    sizing:          { icon: '📐', label: 'Sizing' },
    execution:       { icon: '⚡', label: 'Execution' },
    // v4 institutional pipeline stages
    market_gate:     { icon: '🚪', label: 'Market Gate' },
    regime:          { icon: '📊', label: 'Regime' },
    hypothesis:      { icon: '💡', label: 'Hypothesis' },
    construction:    { icon: '🏗', label: 'Construction' },
    invalidation:    { icon: '🚫', label: 'Invalidation' },
    conviction:      { icon: '🎯', label: 'Conviction' },
    risk_gate:       { icon: '🚦', label: 'Risk Gate' },
    execution_guard: { icon: '🔒', label: 'Exec Guard' },
    // AI agent stages (ai_signal mode)
    filters:         { icon: '🔍', label: 'Filters' },
    ai_signal_gen:   { icon: '🤖', label: 'Signal Scoring' },
    ai_validator:    { icon: '🧠', label: 'Validator' },
    ai_research:     { icon: '🔬', label: 'Research' },
    ai_optimizer:    { icon: '⚙️', label: 'Optimizer' },
    ai_regime:       { icon: '📊', label: 'Regime' },
    ai_fallback:     { icon: '🔄', label: 'Fallback' },
  };

  // Status → color mapping for PipelineStep
  const _STATUS_COLORS = {
    passed:     'var(--green)',
    generated:  'var(--blue)',
    approved:   'var(--green)',
    filled:     'var(--green)',
    blocked:    'var(--red)',
    rejected:   'var(--red)',
    failed:     'var(--red)',
    no_signal:       'var(--muted)',
    no_construction: 'var(--muted)',
    skipped:         'var(--muted)',
    calling:    'var(--amber)',
    configured: 'var(--blue)',
    standby:    'var(--muted)',
    no_key:     'var(--amber)',
    hard_rules: 'var(--muted)',
    collected:  'var(--blue)',
    groups:     'var(--blue)',
    BUY:        'var(--green)',
    SELL:       'var(--red)',
    HOLD:       'var(--muted)',
    veto:       'var(--red)',
    adjust:     'var(--amber)',
    approve:    'var(--green)',
  };

  // Status → badge label
  const _STATUS_BADGE = {
    passed:     '✓ PASS',
    generated:  '✓ SIGNAL',
    approved:   '✓ APPROVED',
    filled:     '✓ FILLED',
    blocked:    '✗ BLOCKED',
    rejected:   '✗ REJECTED',
    failed:     '✗ FAILED',
    no_signal:       '— NO SIGNAL',
    no_construction: '— NO STRUCTURE',
    skipped:         '— SKIP',
    calling:    '⟳ CALLING',
    configured: '● READY',
    standby:    '○ STANDBY',
    no_key:     '⚠ NO KEY',
    hard_rules: '— HARD RULES',
    collected:  '✓ FEATURES',
    groups:     '✓ GROUPS',
    BUY:        '✓ BUY',
    SELL:       '✓ SELL',
    HOLD:       '— HOLD',
    veto:       '✕ VETO',
    adjust:     '↺ ADJUST',
    approve:    '✓ APPROVE',
  };

  function _renderEventStream(events) {
    if (!events.length) {
      rawLogBody.innerHTML = `<span style="color:var(--muted);font-size:11px">No events yet — stream will update automatically.</span>`;
      return;
    }
    rawLogBody.innerHTML = events.map(e => {
      const ts = new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

      // Special rendering for PipelineStep events
      if (e.type === 'PipelineStep') {
        const d      = e.data || {};
        const stage  = d.stage || '';
        const status = d.status || '';
        const si     = _STEP_META[stage] || { icon: '•', label: stage };
        const color  = _STATUS_COLORS[status] || 'var(--text)';
        const badge  = _STATUS_BADGE[status] || status;

        // Filters stage: expanded per-tool breakdown when results are available
        if (stage === 'filters' && Array.isArray(d.results) && d.results.length > 0) {
          const combinedSize = d.results.reduce((acc, r) => acc * (r.size_modifier ?? 1), 1);
          const toolRows = d.results.map(r => {
            const passed    = r.passed !== false;
            const tc        = passed ? 'var(--green)' : 'var(--red)';
            const icon      = passed ? '✓' : '✕';
            const valText   = _extractToolValue(r);
            const weight    = r.size_modifier != null ? `×${(+r.size_modifier).toFixed(2)}` : '';
            return `<div style="display:flex;gap:6px;padding:1px 0;align-items:baseline">
              <span style="font-size:9px;color:${tc};font-weight:700;min-width:10px">${icon}</span>
              <span style="font-size:9px;color:var(--text);min-width:130px;font-family:monospace">${r.tool_name || '?'}</span>
              <span style="font-size:9px;color:var(--muted);font-family:monospace;flex:1">${valText}</span>
              ${weight ? `<span style="font-size:9px;color:var(--primary);font-family:monospace;min-width:32px;text-align:right">${weight}</span>` : ''}
            </div>`;
          }).join('');
          return `<div style="padding:5px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
              <span style="color:var(--muted);font-size:11px;min-width:72px">${ts}</span>
              <span style="font-size:13px">${si.icon}</span>
              <span style="font-size:11px;font-weight:700;color:var(--text);min-width:75px">${si.label}</span>
              <span style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;background:${color}22;color:${color}">${badge}</span>
              <span style="font-size:10px;color:var(--muted);font-family:monospace">${d.results.length} tools | size:${combinedSize.toFixed(2)}</span>
            </div>
            <div style="padding-left:90px">${toolRows}</div>
          </div>`;
        }

        // Default single-line rendering for all other stages
        const detail = _compactStepDetail(stage, status, d.detail || '');
        return `<div style="padding:5px 0;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:8px">
          <span style="color:var(--muted);font-size:11px;min-width:72px">${ts}</span>
          <span style="font-size:13px">${si.icon}</span>
          <span style="font-size:11px;font-weight:700;color:var(--text);min-width:75px">${si.label}</span>
          <span style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;background:${color}22;color:${color}">${badge}</span>
          <span style="font-size:10px;color:var(--muted);font-family:monospace;white-space:pre-wrap;word-break:break-all">${detail}</span>
        </div>`;
      }

      // Default rendering for other events
      const color  = EVENT_COLORS[e.type] || 'var(--text)';
      const meta   = _EVENT_META[e.type];
      const label  = meta ? `${meta.icon} ${meta.label}` : e.type.replace(/([A-Z])/g, ' $1').trim();
      const summary = _compactEventText(e);
      return `<div style="padding:4px 0;border-bottom:1px solid var(--border)">
        <span style="color:var(--muted);font-size:11px">${ts}</span>
        <span style="color:${color};font-weight:700;margin:0 8px">${label}</span>
        <span style="color:var(--text);font-size:11px">${summary}</span>
      </div>`;
    }).join('');
  }

  async function loadRawLog() {
    try {
      const data   = await apiGet(`/api/events?limit=200&instance_id=${encodeURIComponent(_rawLogInstance)}`);
      const events = data.events || [];
      _renderPipelineGrid(events);
      _renderEventStream(events);
    } catch (err) {
      rawLogPipeline.innerHTML = '';
      rawLogBody.innerHTML = `<span style="color:var(--red)">Error: ${err.message}</span>`;
    }
  }

  function _startRawLogPolling() {
    _stopRawLogPolling();
    rawLogLiveDot.textContent = '🟢 live';
    rawLogLiveDot.style.color = 'var(--green)';
    _rawLogTimer = setInterval(loadRawLog, 3000);
  }

  function _stopRawLogPolling() {
    if (_rawLogTimer) { clearInterval(_rawLogTimer); _rawLogTimer = null; }
    rawLogLiveDot.textContent = '⏸ paused';
    rawLogLiveDot.style.color = 'var(--muted)';
  }

  document.getElementById('raw-log-close').addEventListener('click', () => {
    _stopRawLogPolling();
    rawLogModal.style.display = 'none';
  });
  document.getElementById('raw-log-refresh').addEventListener('click', loadRawLog);
  rawLogModal.addEventListener('click', e => {
    if (e.target === rawLogModal) {
      _stopRawLogPolling();
      rawLogModal.style.display = 'none';
    }
  });

  // ─── Deploy modal logic ─────────────────────────────────────────────
  const deployBtn = document.getElementById('deploy-agent-btn');
  const deployModal = document.getElementById('deploy-modal');
  const symbolSelect = document.getElementById('deploy-symbol');
  const riskSlider = document.getElementById('deploy-risk-budget');
  const riskVal = document.getElementById('risk-budget-val');
  const riskHint = document.getElementById('risk-budget-hint');

  deployBtn.addEventListener('click', () => {
    deployModal.style.display = deployModal.style.display === 'none' ? 'block' : 'none';
    if (deployModal.style.display === 'block') loadStrategiesForSymbol(symbolSelect.value);
  });
  document.getElementById('deploy-cancel').addEventListener('click', () => {
    deployModal.style.display = 'none';
  });

  riskSlider.addEventListener('input', () => {
    riskVal.textContent = riskSlider.value + '%';
  });

  // Auto-suggest 50% risk budget when another agent already runs on this symbol
  function updateRiskHint(symbol) {
    const running = currentBots.filter(b => b.symbol === symbol);
    if (running.length > 0) {
      riskHint.textContent = `${running.length} agent(s) already on ${symbol} — consider 50%`;
      if (riskSlider.value === '100') {
        riskSlider.value = '50';
        riskVal.textContent = '50%';
      }
    } else {
      riskHint.textContent = '';
      riskSlider.value = '100';
      riskVal.textContent = '100%';
    }
  }

  // Load strategies when symbol changes
  async function loadStrategiesForSymbol(symbol) {
    updateRiskHint(symbol);
    const picker = document.getElementById('strategy-picker');
    picker.innerHTML = '<div style="font-size:11px;color:var(--muted)">Loading strategies...</div>';
    try {
      const data = await apiGet(`/api/strategies?symbol=${symbol}`);
      const strategies = (data.strategies || []).filter(s => s.status !== 'retired');
      // Find active version for this symbol
      let activeVer = null;
      try {
        const settings = await apiGet('/api/settings');
        const raw = settings.settings?.[`active_strategy_${symbol}`];
        if (raw) {
          const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
          activeVer = parsed.version;
        }
      } catch (e) { /* ignore */ }
      picker.innerHTML = strategyPickerHTML(strategies, activeVer);
    } catch (err) {
      picker.innerHTML = `<div style="color:var(--red);font-size:11px">${err.message}</div>`;
    }
  }

  symbolSelect.addEventListener('change', () => loadStrategiesForSymbol(symbolSelect.value));

  // Deploy confirm
  document.getElementById('deploy-confirm').addEventListener('click', async () => {
    const symbol = symbolSelect.value;
    const dryRun = document.getElementById('deploy-mode').value === 'dry';
    const riskBudget = parseInt(riskSlider.value, 10) / 100;
    const pollInterval = parseFloat(document.getElementById('deploy-poll-interval').value) || 60;
    const pickedRadio = document.querySelector('input[name="pick-strategy"]:checked');
    const stratVer = pickedRadio ? parseInt(pickedRadio.value, 10) : null;
    const stratName = pickedRadio ? (pickedRadio.dataset.name || '') : '';

    try {
      const payload = { symbol, dry_run: dryRun, risk_budget_pct: riskBudget, poll_interval_sec: pollInterval };
      if (stratVer !== null) payload.strategy_version = stratVer;
      if (stratName) payload.strategy_name = stratName;
      await apiPost('/api/bots/start', payload);
      const modeLabel = dryRun ? 'Dry Run' : 'Live';
      const budgetLabel = riskBudget < 1 ? ` @ ${Math.round(riskBudget * 100)}% budget` : '';
      window.showToast(`Agent deployed: ${symbol} (${modeLabel})${budgetLabel}`);
      deployModal.style.display = 'none';
      setTimeout(load, 2000);
    } catch (err) {
      window.showToast(err.message, 'error');
    }
  });

  // ─── Load and render agents ─────────────────────────────────────────
  async function load() {
    const el = document.getElementById('agents-content');
    if (!el) return;
    try {
      const data = await apiGet('/api/bots');
      currentBots = data.bots || [];

      if (currentBots.length === 0) {
        el.innerHTML = `
          <div class="empty-state">
            <div class="icon" style="font-size:32px">&#129302;</div>
            <div class="empty-title">No agents deployed</div>
            <div class="empty-desc">Click "Deploy Agent" above to launch your first trading agent.</div>
          </div>`;
        return;
      }

      // Count agents per symbol for same-symbol badge
      const symbolCounts = {};
      for (const b of currentBots) {
        symbolCounts[b.symbol] = (symbolCounts[b.symbol] || 0) + 1;
      }

      el.innerHTML = `<div class="bot-grid">${currentBots.map(b => agentCard(b, symbolCounts)).join('')}</div>`;

      // Uptime counters — tick every second from page-load (start = 0:00)
      if (window._uptimeInterval) clearInterval(window._uptimeInterval);
      window._uptimeInterval = setInterval(() => {
        document.querySelectorAll('.uptime-counter').forEach(span => {
          const elapsed = Math.floor((Date.now() - Number(span.dataset.start)) / 1000);
          span.textContent = formatTick(elapsed);
        });
      }, 1000);

      // Loop status toggle buttons
      el.querySelectorAll('.loop-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.dataset.loop;
          const panel = document.getElementById(`loop-${id}`);
          if (!panel) return;
          const isOpen = panel.style.display !== 'none';
          panel.style.display = isOpen ? 'none' : 'block';
          btn.innerHTML = isOpen ? '&#9660; Loop Status' : '&#9650; Loop Status';
        });
      });

      // Raw log buttons
      el.querySelectorAll('.raw-log-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          _rawLogSymbol   = btn.dataset.symbol;
          _rawLogInstance = btn.dataset.instance;
          rawLogSub.textContent = `${_rawLogSymbol} · ${_rawLogInstance}`;
          rawLogModal.style.display = 'block';
          loadRawLog();
          _startRawLogPolling();
        });
      });

      // Stop buttons
      el.querySelectorAll('[data-stop]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm(`Stop agent ${btn.dataset.stop}?`)) return;
          try {
            await apiPost(`/api/bots/${btn.dataset.stop}/stop`, {});
            window.showToast('Agent stop signal sent');
            setTimeout(load, 2000);
          } catch (err) {
            window.showToast(err.message, 'error');
          }
        });
      });

      // Remove buttons
      el.querySelectorAll('[data-remove]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm(`Remove agent record ${btn.dataset.remove}?`)) return;
          try {
            await apiDelete(`/api/bots/${btn.dataset.remove}`);
            window.showToast('Agent record removed');
            load();
          } catch (err) {
            window.showToast(err.message, 'error');
          }
        });
      });

      // Manual trade panel toggle
      el.querySelectorAll('.manual-trade-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.dataset.instance;
          const panel = document.getElementById(`manual-trade-panel-${id}`);
          if (!panel) return;
          const open = panel.style.display !== 'none';
          panel.style.display = open ? 'none' : 'block';
          if (!open) _loadManualTrades(id);
        });
      });

      // Direction BUY/SELL selection
      el.querySelectorAll('.manual-dir-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.dataset.instance;
          const dir = btn.dataset.dir;
          manualTradeDir[id] = dir;
          // Update button styles
          el.querySelectorAll(`.manual-dir-btn[data-instance="${id}"]`).forEach(b => {
            const active = b.dataset.dir === dir;
            if (b.dataset.dir === 'BUY') {
              b.style.background = active ? 'var(--green)' : 'transparent';
              b.style.color = active ? '#000' : 'var(--green)';
            } else {
              b.style.background = active ? 'var(--red)' : 'transparent';
              b.style.color = active ? '#fff' : 'var(--red)';
            }
          });
          // Enable open button
          const openBtn = el.querySelector(`.manual-open-btn[data-instance="${id}"]`);
          if (openBtn) {
            openBtn.disabled = false;
            openBtn.style.opacity = '1';
            openBtn.textContent = `Open ${dir}`;
            openBtn.style.background = dir === 'BUY' ? 'var(--green)' : 'var(--red)';
            openBtn.style.color = dir === 'BUY' ? '#000' : '#fff';
          }
        });
      });

      // Open trade button
      el.querySelectorAll('.manual-open-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const id = btn.dataset.instance;
          const dir = manualTradeDir[id];
          if (!dir) return;
          const lots = parseFloat(el.querySelector(`.manual-lots[data-instance="${id}"]`)?.value || '0.01');
          const price = parseFloat(el.querySelector(`.manual-price[data-instance="${id}"]`)?.value || '0') || null;
          const sl = parseFloat(el.querySelector(`.manual-sl[data-instance="${id}"]`)?.value || '0') || null;
          const tp = parseFloat(el.querySelector(`.manual-tp[data-instance="${id}"]`)?.value || '0') || null;
          btn.disabled = true;
          btn.textContent = 'Opening...';
          try {
            await apiPost(`/api/bots/${id}/trades/open`, { direction: dir, lots, sl, tp, entry_price: price });
            window.showToast(`${dir} trade opened`);
            await _loadManualTrades(id);
          } catch (err) {
            window.showToast(err.message, 'error');
          } finally {
            btn.disabled = false;
            btn.textContent = `Open ${dir}`;
          }
        });
      });

      async function _loadManualTrades(id) {
        const container = document.getElementById(`manual-trades-${id}`);
        if (!container) return;
        try {
          const data = await apiGet(`/api/bots/${id}/trades`);
          const trades = data.trades || [];
          if (trades.length === 0) {
            container.innerHTML = `<div style="font-size:11px;color:var(--muted);text-align:center;padding:6px">No open trades</div>`;
            return;
          }
          container.innerHTML = `<div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:6px">OPEN TRADES</div>` +
            trades.map(t => `
              <div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--border);font-size:11px">
                <span style="font-weight:700;color:${t.direction === 'BUY' ? 'var(--green)' : 'var(--red)'}">${t.direction}</span>
                <span>${t.lot_size} lots</span>
                <span style="color:var(--muted)">@ ${t.entry_price ?? '—'}</span>
                <span style="color:var(--muted);margin-left:auto">#${t.order_ticket ?? t.id}</span>
                <button class="btn btn-danger btn-sm manual-close-btn"
                  data-instance="${id}" data-trade="${t.id}"
                  style="padding:2px 8px;font-size:10px">Close</button>
              </div>`).join('');
          // Bind close buttons
          container.querySelectorAll('.manual-close-btn').forEach(cb => {
            cb.addEventListener('click', async () => {
              const tid = parseInt(cb.dataset.trade);
              cb.disabled = true;
              cb.textContent = '...';
              try {
                const res = await apiPost(`/api/bots/${id}/trades/close`, { trade_id: tid });
                const pnl = res.pnl_usd ?? 0;
                const sign = pnl > 0 ? '+' : '';
                const outcome = res.outcome || 'BE';
                const color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--amber)';
                window.showToast(`Closed @ ${res.close_price?.toFixed(2) ?? '—'} · P&L: ${sign}$${pnl.toFixed(2)} (${outcome})`, pnl >= 0 ? 'success' : 'error');
                await _loadManualTrades(id);
              } catch (err) {
                window.showToast(err.message, 'error');
                cb.disabled = false;
                cb.textContent = 'Close';
              }
            });
          });
        } catch (err) {
          container.innerHTML = `<div style="font-size:11px;color:var(--red)">${err.message}</div>`;
        }
      }
    } catch (err) {
      el.innerHTML = `<div class="page-error">${err.message}</div>`;
    }
  }

  // ─── WebSocket listener for live loop updates + evolution badges ─────
  function onWSEvent(e) {
    handleWSEvent(e.detail, currentBots);
  }
  window.addEventListener('ws-event', onWSEvent);

  document.getElementById('agents-refresh').addEventListener('click', load);
  await load();

  const timer = setInterval(load, 30000);
  window.addEventListener('route-change', () => {
    clearInterval(timer);
    window.removeEventListener('ws-event', onWSEvent);
  }, { once: true });
}
