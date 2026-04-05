/**
 * 🧪 SeedLab™ — Algo Strategy Discovery Engine
 */
import { apiGet, apiPost, apiPatch, apiDelete } from '../api.js';
import { playSeedLabDone } from '../sounds.js';

const STATE_META = {
  pending:   { color: 'var(--amber)',  icon: '⏳', badge: 'badge-amber' },
  running:   { color: 'var(--blue)',   icon: '⚡', badge: 'badge-blue' },
  completed: { color: 'var(--green)',  icon: '✓',  badge: 'badge-green' },
  failed:    { color: 'var(--red)',    icon: '✕',  badge: 'badge-red' },
  paused:    { color: 'var(--amber)',  icon: '⏸',  badge: 'badge-amber' },
  killed:    { color: 'var(--red)',    icon: '⛔', badge: 'badge-red' },
};

// Track log offsets and poll timers per run
const _logOffsets = {};
const _pollTimers = {};

function progressBar(gen, max, color) {
  const pct = max > 0 ? Math.round((gen / max) * 100) : 0;
  return `
    <div class="bt-progress">
      <div class="bt-progress-bar">
        <div class="bt-progress-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <span class="bt-progress-label">${gen}/${max} (${pct}%)</span>
    </div>`;
}

function runCard(r) {
  const meta = STATE_META[r.state] || STATE_META.pending;
  const modeLabel = r.signal_mode === 'algo_only' ? 'Algo Only' : 'Algo + AI';
  // is_running = actual asyncio task alive; state = DB state (can be stale after restart)
  const active = r.is_running;
  const staleRunning = r.state === 'running' && !r.is_running; // task lost on restart
  const paused = r.state === 'paused';
  const canStop = active;
  const canResume = paused || staleRunning; // allow resume of stale "running"
  const canDelete = !active; // always allow delete if task not alive

  return `
    <div class="bt-live-card ${active ? 'bt-active' : ''}" data-run-id="${r.run_id}">
      <div class="bt-live-header">
        <div class="bt-live-left">
          <span class="bt-state-icon" style="color:${meta.color}">${meta.icon}</span>
          <div>
            <div class="bt-name">${r.name || r.run_id}</div>
            <div class="bt-meta">${r.symbol} · ${modeLabel} · ${r.timeframe || '1h'} · ${r.days}d · $${r.balance?.toLocaleString() ?? '10,000'}</div>
          </div>
        </div>
        <div class="bt-live-actions">
          ${canStop ? `<button class="btn btn-sm btn-stop" data-action="stop" data-id="${r.run_id}" title="Stop">⏹ Stop</button>` : ''}
          ${canResume ? `<button class="btn btn-sm btn-resume" data-action="resume" data-id="${r.run_id}" title="Resume">▶ Resume</button>` : ''}
          ${canDelete ? `<button class="btn btn-sm btn-danger" data-action="delete" data-id="${r.run_id}" title="Delete">✕</button>` : ''}
          <span class="badge ${meta.badge}">${r.state}</span>
        </div>
      </div>
      <div class="bt-live-stats">
        <div class="bt-stat">
          <div class="bt-stat-val">${r.best_sharpe != null ? r.best_sharpe.toFixed(2) : '—'}</div>
          <div class="bt-stat-lbl">Sharpe</div>
        </div>
        <div class="bt-stat">
          <div class="bt-stat-val">${r.best_wr != null ? (r.best_wr * 100).toFixed(1) + '%' : '—'}</div>
          <div class="bt-stat-lbl">Win Rate</div>
        </div>
        <div class="bt-stat">
          <div class="bt-stat-val">${r.best_pnl != null ? '$' + r.best_pnl.toFixed(0) : '—'}</div>
          <div class="bt-stat-lbl">Best PnL</div>
        </div>
        <div class="bt-stat">
          <div class="bt-stat-val">${r.generation ?? 0}/${r.max_generations ?? '—'}</div>
          <div class="bt-stat-lbl">Gens</div>
        </div>
      </div>
      ${progressBar(r.generation ?? 0, r.max_generations ?? 0, meta.color)}
      ${r.message ? `<div class="bt-message">${r.message}</div>` : ''}
      ${r.error_message ? `<div class="bt-error">${r.error_message}</div>` : ''}
      <div class="bt-log-section">
        <div class="bt-log-header">
          <span class="bt-log-title">Live Output</span>
          <button class="btn btn-sm bt-log-toggle" data-toggle-log="${r.run_id}">
            ${active ? '▼ Show' : '▶ Show'}
          </button>
        </div>
        <div class="bt-log-body" id="log-${r.run_id}" style="display:none">
          <pre class="bt-log-pre" id="logpre-${r.run_id}">Loading...</pre>
        </div>
      </div>
    </div>`;
}

async function fetchLogs(runId, logEl) {
  const offset = _logOffsets[runId] || 0;
  try {
    const data = await apiGet(`/api/backtests/${runId}/logs?offset=${offset}`);
    if (data.lines && data.lines.length > 0) {
      if (offset === 0) logEl.innerHTML = '';
      logEl.innerHTML += data.lines.map(colorize).join('\n') + '\n';
      _logOffsets[runId] = data.total;
      logEl.scrollTop = logEl.scrollHeight;
    } else if (offset === 0) {
      logEl.innerHTML = '<span class="log-info">Waiting for output...</span>';
    }
  } catch (e) {
    if (offset === 0) logEl.innerHTML = '<span class="log-err">Error loading logs.</span>';
  }
}

function startLogPoll(runId) {
  if (_pollTimers[runId]) return;
  const logEl = document.getElementById(`logpre-${runId}`);
  if (!logEl) return;
  _logOffsets[runId] = 0;
  fetchLogs(runId, logEl);
  _pollTimers[runId] = setInterval(() => fetchLogs(runId, logEl), 2000);
}

function stopLogPoll(runId) {
  if (_pollTimers[runId]) {
    clearInterval(_pollTimers[runId]);
    delete _pollTimers[runId];
  }
}

function stopAllPolls() {
  for (const id of Object.keys(_pollTimers)) stopLogPoll(id);
}

function colorize(line) {
  const e = line
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  if (e.includes('[GEN]'))  return `<span class="log-gen">${e}</span>`;
  if (e.includes('[CKPT]')) return `<span class="log-ckpt">${e}</span>`;
  if (e.includes('[WARN]')) return `<span class="log-warn">${e}</span>`;
  if (e.includes('[ERR]'))  return `<span class="log-err">${e}</span>`;
  if (e.includes('[STAT]')) return `<span class="log-stat">${e}</span>`;
  if (e.includes('[DATA]')) return `<span class="log-data">${e}</span>`;
  return `<span class="log-info">${e}</span>`;
}

export async function render(container) {
  if (!document.getElementById('bt-log-colors')) {
    const s = document.createElement('style');
    s.id = 'bt-log-colors';
    s.textContent = `
      .log-gen  { color: var(--blue); }
      .log-stat { color: var(--green); }
      .log-ckpt { color: var(--amber); font-weight: 600; }
      .log-warn { color: var(--amber); }
      .log-err  { color: var(--red); }
      .log-data { color: #64b5f6; }
      .log-info { color: var(--muted); }
      .bt-log-pre { white-space: pre-wrap; }
    `;
    document.head.appendChild(s);
  }
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🧪 SeedLab™</div>
      <div class="page-subtitle">Backtest, evolve, and optimise algorithmic strategies</div>
    </div>

    <div class="section-label">New Discovery Run</div>
    <div class="panel-card">
      <div class="bt-form">
        <div class="form-group">
          <label>Symbol</label>
          <div class="symbol-picker" id="symbol-picker">
            <input type="text" id="bt-symbol-search" class="field-input" placeholder="Search symbols..." autocomplete="off" style="display:none">
            <input type="hidden" id="bt-symbol" value="XAUUSD">
            <div class="symbol-selected" id="symbol-selected">XAUUSD — Gold Futures</div>
            <div class="symbol-dropdown" id="symbol-dropdown" style="display:none"></div>
          </div>
        </div>
        <div class="form-group">
          <label>Timeframe</label>
          <select id="bt-timeframe" class="field-input">
            <option value="1m">M1 (1 min)</option>
            <option value="5m">M5 (5 min)</option>
            <option value="15m" selected>M15 (15 min)</option>
            <option value="30m">M30 (30 min)</option>
            <option value="1h">H1 (1 hour)</option>
            <option value="1d">D1 (daily)</option>
            <option value="1wk">W1 (weekly)</option>
            <option value="1mo">MN (monthly)</option>
          </select>
        </div>
        <div class="form-group">
          <label>History (days)</label>
          <input type="number" id="bt-days" value="365" min="7" max="730">
        </div>
        <div class="form-group">
          <label>Starting Balance ($)</label>
          <input type="number" id="bt-balance" value="10000" min="1000">
        </div>
        <div class="form-group">
          <label>Generations</label>
          <input type="number" id="bt-gens" value="3" min="1" max="100">
        </div>
        <div class="form-group">
          <label>Strategy Mode</label>
          <select id="bt-signal-mode" class="field-input">
            <option value="algo_ai" selected>Algo + AI</option>
            <option value="algo_only">Algo Only</option>
          </select>
        </div>
        <div class="form-group" style="align-self:flex-end">
          <button class="btn-gradient" id="bt-start" style="width:100%">🚀 Launch Discovery</button>
        </div>
      </div>

      <!-- Signal Rules Builder (full-width below the form row) -->
      <div class="bt-signal-rules-section" style="margin-top:12px">
        <div class="bt-tools-header" id="bt-rules-toggle" style="cursor:pointer;display:flex;align-items:center;gap:6px;padding:6px 0">
          <span>Signal Rules</span>
          <span class="bt-rules-arrow" style="font-size:0.8em;color:var(--muted)">▶</span>
        </div>
        <div id="bt-rules-body" style="display:none;padding:8px 0 4px">
          <div id="bt-signal-rules-list" style="display:flex;flex-direction:column;gap:6px"></div>
          <div style="display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap">
            <button id="bt-add-rule" class="btn btn-sm">+ Add Rule</button>
            <label style="display:flex;align-items:center;gap:6px;font-size:0.82rem">
              Combine:
              <select id="bt-signal-logic" class="field-input" style="padding:3px 6px;font-size:0.82rem">
                <option value="AND" selected>AND — all must fire</option>
                <option value="OR">OR — any fires</option>
                <option value="MAJORITY">MAJORITY — &gt;50%</option>
              </select>
            </label>
            <label style="display:flex;align-items:center;gap:5px;font-size:0.82rem">
              <input type="checkbox" id="bt-signal-auto">
              Let Optuna auto-pick sources
            </label>
          </div>
        </div>
      </div>

      <div id="bt-data-hint" style="font-size:0.72rem;color:var(--amber);padding:4px 0 0;min-height:1em"></div>
      <div class="bt-tools-section">
        <div class="bt-tools-header" id="bt-tools-toggle">
          <span>Signal Tools (backtest-compatible)</span>
          <span class="bt-tools-arrow">▶</span>
        </div>
        <div class="bt-tools-body" id="bt-tools-body" style="display:none">
          <div class="bt-tools-grid">
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-session" checked> <span>Session Filter</span> <em>Block outside London/NY hours</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-volatility" checked> <span>Volatility Filter</span> <em>Block extreme/dead ATR%</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-ema200" checked> <span>EMA200 Trend Filter</span> <em>Block against EMA200 direction</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-bos"> <span>BOS Structure Guard</span> <em>Require Break of Structure</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-fvg"> <span>FVG Structure Guard</span> <em>Require Fair Value Gap entry</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-tick-jump"> <span>Tick Jump Guard</span> <em>Reject 2-bar spikes</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-liq-vacuum"> <span>Liquidity Vacuum Guard</span> <em>Reject thin-body candles</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-vwap"> <span>VWAP Guard</span> <em>Block overextended entries</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-macd"> <span>MACD Filter</span> <em>Block if histogram disagrees</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-bollinger"> <span>Bollinger Filter</span> <em>Block outside band zone</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-adx"> <span>ADX Filter</span> <em>Block if ADX &lt; 20 (no trend)</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-volume"> <span>Volume Filter</span> <em>Block if volume below avg</em></label>
            <label class="bt-tool-item"><input type="checkbox" id="bt-tool-swing"> <span>Swing Structure</span> <em>Require HH/HL or LH/LL</em></label>
          </div>
        </div>
      </div>
    </div>

    <div class="section-label" style="margin-top:24px">
      Runs
      <button class="btn btn-sm" id="bt-refresh" style="margin-left:8px">↻ Refresh</button>
    </div>
    <div id="bt-runs"><div class="dash-loading">Loading…</div></div>`;

  async function load() {
    const el = document.getElementById('bt-runs');
    if (!el) return; // page navigated away
    try {
      const data = await apiGet('/api/backtests');
      if (!data.backtests || data.backtests.length === 0) {
        el.innerHTML = `<div class="empty-state"><div class="icon">📊</div><div>No backtests yet.</div></div>`;
        return;
      }
      el.innerHTML = data.backtests.map(runCard).join('');
      // Seed known states for change detection
      for (const bt of data.backtests) _knownStates[bt.run_id] = bt.state;
      wireActions(el, data.backtests);
    } catch (err) {
      el.innerHTML = `<div class="page-error">${err.message}</div>`;
    }
  }

  function wireActions(el, backtests) {
    // Stop/Resume/Delete buttons
    el.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const action = btn.dataset.action;
        const id = btn.dataset.id;
        try {
          if (action === 'stop') {
            await apiPatch(`/api/backtests/${id}/stop`);
            window.showToast('Stop requested');
          } else if (action === 'resume') {
            await apiPatch(`/api/backtests/${id}/resume`);
            window.showToast('Resumed');
          } else if (action === 'delete') {
            if (!confirm(`Delete backtest ${id}?`)) return;
            stopLogPoll(id);
            await apiDelete(`/api/backtests/${id}`);
            window.showToast('Deleted');
          }
          setTimeout(load, 500);
        } catch (err) {
          window.showToast(err.message, 'error');
        }
      });
    });

    // Log toggle buttons
    el.querySelectorAll('[data-toggle-log]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.toggleLog;
        const body = document.getElementById(`log-${id}`);
        if (!body) return;
        const visible = body.style.display !== 'none';
        body.style.display = visible ? 'none' : 'block';
        btn.textContent = visible ? '▶ Show' : '▼ Hide';
        if (!visible) {
          startLogPoll(id);
        } else {
          stopLogPoll(id);
        }
      });
    });

    // Auto-expand logs for running backtests
    for (const bt of backtests) {
      if (bt.state === 'running' || bt.is_running) {
        const body = document.getElementById(`log-${bt.run_id}`);
        const btn = el.querySelector(`[data-toggle-log="${bt.run_id}"]`);
        if (body) {
          body.style.display = 'block';
          if (btn) btn.textContent = '▼ Hide';
          startLogPoll(bt.run_id);
        }
      }
    }
  }

  // Tools section toggle
  document.getElementById('bt-tools-toggle').addEventListener('click', () => {
    const body = document.getElementById('bt-tools-body');
    const arrow = document.querySelector('.bt-tools-arrow');
    const visible = body.style.display !== 'none';
    body.style.display = visible ? 'none' : 'block';
    arrow.textContent = visible ? '▶' : '▼';
  });

  // ── Signal Rules Builder ────────────────────────────────────────────────────

  const SIGNAL_SOURCES = [
    { value: 'ema_crossover',     label: 'EMA Crossover' },
    { value: 'macd_crossover',    label: 'MACD Crossover' },
    { value: 'rsi_reversal',      label: 'RSI Reversal' },
    { value: 'bollinger_breakout',label: 'Bollinger Breakout' },
    { value: 'adx_trend',         label: 'ADX Trend' },
    { value: 'bos_confirm',       label: 'BOS Confirm' },
  ];

  const SOURCE_PARAMS = {
    ema_crossover:     [{ id: 'ema_fast',  label: 'Fast',   def: 21, min: 3  }, { id: 'ema_slow', label: 'Slow', def: 55, min: 5 }],
    macd_crossover:    [{ id: 'macd_fast', label: 'Fast',   def: 12, min: 2  }, { id: 'macd_slow', label: 'Slow', def: 26, min: 5 }, { id: 'macd_signal', label: 'Signal', def: 9, min: 2 }],
    rsi_reversal:      [{ id: 'rsi_ob',    label: 'OB',     def: 70, min: 55, max: 90 }, { id: 'rsi_os', label: 'OS', def: 30, min: 10, max: 45 }],
    bollinger_breakout:[{ id: 'bb_period', label: 'Period', def: 20, min: 5  }, { id: 'bb_std_dev', label: 'Std Dev', def: 2.0, min: 0.5, step: 0.1, float: true }],
    adx_trend:         [{ id: 'adx_period',label: 'Period', def: 14, min: 5  }, { id: 'adx_min_threshold', label: 'Min ADX', def: 20, min: 5, max: 60 }],
    bos_confirm:       [],
  };

  function buildRuleRow(src) {
    const params = SOURCE_PARAMS[src] || [];
    const paramHtml = params.map(p => `
      <label class="rule-param" style="font-size:0.8rem;display:flex;align-items:center;gap:4px">
        ${p.label}
        <input type="${p.float ? 'number' : 'number'}" class="field-input rule-param-input"
          data-param="${p.id}"
          value="${p.def}"
          min="${p.min ?? ''}"
          ${p.max ? `max="${p.max}"` : ''}
          ${p.step ? `step="${p.step}"` : ''}
          style="width:60px;padding:3px 5px;font-size:0.8rem">
      </label>`).join('');
    const sourceOptions = SIGNAL_SOURCES.map(s =>
      `<option value="${s.value}"${s.value === src ? ' selected' : ''}>${s.label}</option>`
    ).join('');
    return `
      <div class="rule-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--card-bg);border-radius:6px;padding:6px 8px">
        <select class="rule-source field-input" style="padding:3px 6px;font-size:0.82rem;width:auto">${sourceOptions}</select>
        <span class="rule-params" style="display:flex;gap:8px;flex-wrap:wrap">${paramHtml}</span>
        <button class="rule-remove btn btn-sm" style="margin-left:auto;padding:2px 8px;font-size:0.8rem">✕</button>
      </div>`;
  }

  function renderRuleRows() {
    const list = document.getElementById('bt-signal-rules-list');
    if (!list) return;
    if (list.children.length === 0) {
      list.innerHTML = buildRuleRow('ema_crossover');
      wireRuleRow(list.children[0]);
    }
  }

  function wireRuleRow(row) {
    row.querySelector('.rule-remove').addEventListener('click', () => {
      row.remove();
      if (document.getElementById('bt-signal-rules-list').children.length === 0) {
        renderRuleRows(); // always keep at least one
      }
    });
    row.querySelector('.rule-source').addEventListener('change', (e) => {
      const newSrc = e.target.value;
      const params = SOURCE_PARAMS[newSrc] || [];
      const paramHtml = params.map(p => `
        <label class="rule-param" style="font-size:0.8rem;display:flex;align-items:center;gap:4px">
          ${p.label}
          <input type="number" class="field-input rule-param-input"
            data-param="${p.id}"
            value="${p.def}"
            min="${p.min ?? ''}"
            ${p.max ? `max="${p.max}"` : ''}
            ${p.step ? `step="${p.step}"` : ''}
            style="width:60px;padding:3px 5px;font-size:0.8rem">
        </label>`).join('');
      row.querySelector('.rule-params').innerHTML = paramHtml;
    });
  }

  function collectRules() {
    const rows = document.querySelectorAll('#bt-signal-rules-list .rule-row');
    return Array.from(rows).map(row => {
      const src = row.querySelector('.rule-source').value;
      const rule = { source: src };
      row.querySelectorAll('.rule-param-input').forEach(inp => {
        rule[inp.dataset.param] = parseFloat(inp.value) || parseInt(inp.value) || 0;
      });
      return rule;
    });
  }

  // Signal Rules toggle
  document.getElementById('bt-rules-toggle').addEventListener('click', () => {
    const body = document.getElementById('bt-rules-body');
    const arrow = document.querySelector('.bt-rules-arrow');
    const visible = body.style.display !== 'none';
    body.style.display = visible ? 'none' : 'block';
    arrow.textContent = visible ? '▶' : '▼';
    if (!visible) renderRuleRows(); // lazy init
  });

  document.getElementById('bt-add-rule').addEventListener('click', () => {
    const list = document.getElementById('bt-signal-rules-list');
    const div = document.createElement('div');
    div.innerHTML = buildRuleRow('ema_crossover');
    const row = div.children[0];
    list.appendChild(row);
    wireRuleRow(row);
  });

  // ── Asset tool preset cache (shared via window to survive Settings saves) ──
  async function loadAssetPresets() {
    if (window.__assetPresetsCache) return window.__assetPresetsCache;
    try {
      const data = await apiGet('/api/assets');
      const map = {};
      for (const a of (data.assets || [])) map[a.symbol] = a.tools;
      window.__assetPresetsCache = map;
    } catch (err) {
      console.warn('Failed to load asset presets:', err);
      window.__assetPresetsCache = {};
    }
    return window.__assetPresetsCache;
  }

  // Map from tools API key to bt-tool-* checkbox id suffix
  const _toolIdMap = {
    session:    'session',
    volatility: 'volatility',
    ema200:     'ema200',
    bos:        'bos',
    fvg:        'fvg',
    tick_jump:  'tick-jump',
    liq_vacuum: 'liq-vacuum',
    vwap:       'vwap',
    macd:       'macd',
    bollinger:  'bollinger',
    adx:        'adx',
    volume:     'volume',
    swing:      'swing',
  };

  async function applyAssetPreset(symbol) {
    const presets = await loadAssetPresets();
    const tools = presets[symbol];
    if (!tools) return; // unknown symbol — leave checkboxes as-is
    for (const [key, idSuffix] of Object.entries(_toolIdMap)) {
      const cb = document.getElementById(`bt-tool-${idSuffix}`);
      if (cb && key in tools) cb.checked = tools[key];
    }
  }

  // ── Symbol picker: load catalog + search/filter ──
  let _symbolCatalog = [];
  let _symbolGroups = [];

  async function loadSymbolCatalog() {
    try {
      const data = await apiGet('/api/backtests/symbols');
      _symbolCatalog = data.symbols || [];
      _symbolGroups = data.groups || [];
      renderSymbolDropdown(_symbolCatalog);
    } catch (err) {
      console.warn('Failed to load symbol catalog:', err);
      // Fallback: show a basic input
      document.getElementById('symbol-selected').style.display = 'none';
      const search = document.getElementById('bt-symbol-search');
      search.value = 'XAUUSD';
      search.style.display = 'block';
    }
  }

  function renderSymbolDropdown(symbols) {
    const dropdown = document.getElementById('symbol-dropdown');
    if (!dropdown) return;

    // Group by asset class
    const grouped = {};
    for (const s of symbols) {
      if (!grouped[s.group]) grouped[s.group] = [];
      grouped[s.group].push(s);
    }

    let html = '';
    for (const group of _symbolGroups) {
      const items = grouped[group];
      if (!items || items.length === 0) continue;
      html += `<div class="symbol-group-label">${group}</div>`;
      for (const s of items) {
        html += `<div class="symbol-option" data-symbol="${s.symbol}" data-yf="${s.yf_ticker}" data-name="${s.name}">
          <span class="symbol-ticker">${s.symbol}</span>
          <span class="symbol-name">${s.name}</span>
          <span class="symbol-yf">${s.yf_ticker}</span>
        </div>`;
      }
    }
    dropdown.innerHTML = html;

    // Wire click events
    dropdown.querySelectorAll('.symbol-option').forEach(el => {
      el.addEventListener('click', () => {
        selectSymbol(el.dataset.symbol, el.dataset.name);
        dropdown.style.display = 'none';
      });
    });
  }

  function selectSymbol(symbol, name) {
    document.getElementById('bt-symbol').value = symbol;
    document.getElementById('symbol-selected').textContent = `${symbol} — ${name}`;
    document.getElementById('symbol-selected').style.display = 'block';
    document.getElementById('bt-symbol-search').value = '';
    document.getElementById('bt-symbol-search').style.display = 'none';
    // Apply the asset's saved filter preset (async, non-blocking)
    applyAssetPreset(symbol);
  }

  // Search input events
  const searchInput = document.getElementById('bt-symbol-search');
  const dropdown = document.getElementById('symbol-dropdown');
  const selectedDisplay = document.getElementById('symbol-selected');

  searchInput.addEventListener('focus', () => {
    dropdown.style.display = 'block';
    selectedDisplay.style.display = 'none';
  });

  searchInput.addEventListener('input', () => {
    const q = searchInput.value.toLowerCase().trim();
    if (!q) {
      renderSymbolDropdown(_symbolCatalog);
      return;
    }
    const filtered = _symbolCatalog.filter(s =>
      s.symbol.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      s.yf_ticker.toLowerCase().includes(q) ||
      s.group.toLowerCase().includes(q)
    );
    renderSymbolDropdown(filtered);
  });

  // Click on selected display → open search
  selectedDisplay.addEventListener('click', () => {
    selectedDisplay.style.display = 'none';
    searchInput.style.display = 'block';
    searchInput.focus();
  });

  // Close dropdown on outside click
  document.addEventListener('click', (e) => {
    const picker = document.getElementById('symbol-picker');
    if (picker && !picker.contains(e.target)) {
      dropdown.style.display = 'none';
      if (document.getElementById('bt-symbol').value) {
        selectedDisplay.style.display = 'block';
        searchInput.style.display = 'none';
      }
    }
  });

  // Allow typing a custom symbol (for advanced users)
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const val = searchInput.value.trim().toUpperCase();
      if (val) {
        selectSymbol(val, 'Custom symbol');
        dropdown.style.display = 'none';
      }
    }
  });

  // Load catalog on mount
  loadSymbolCatalog();

  // MT5 max history days per timeframe (100k bar hard cap)
  const _mt5MaxDaysMap = { '1m': 69, '5m': 347, '15m': 1041, '30m': 2083, '1h': 4166, '4h': 16666, '1d': 9999, '1wk': 9999, '1mo': 9999 };
  const _yfMaxDaysMap  = { '1m': 7,  '5m': 60,  '15m': 60,   '30m': 60,   '1h': 730,  '4h': 730,  '1d': 9999, '1wk': 9999, '1mo': 9999 };

  function applyDaysCap(autoFill = false) {
    const tf = document.getElementById('bt-timeframe').value;
    const daysInput = document.getElementById('bt-days');
    const hintEl = document.getElementById('bt-data-hint');
    const mt5Max = _mt5MaxDaysMap[tf] || 365;
    const yfMax  = _yfMaxDaysMap[tf]  || 730;
    daysInput.max = mt5Max;
    // Auto-fill the recommended MT5 max when timeframe changes
    if (autoFill) {
      daysInput.value = Math.min(mt5Max, 365);
    }
    const cur = parseInt(daysInput.value);
    if (cur > yfMax && yfMax < mt5Max) {
      hintEl.textContent = `Without MT5: yfinance limits ${tf} to ${yfMax} days`;
    } else {
      hintEl.textContent = '';
    }
  }
  document.getElementById('bt-timeframe').addEventListener('change', () => applyDaysCap(true));
  document.getElementById('bt-days').addEventListener('input', () => applyDaysCap(false));
  applyDaysCap(false);

  // Start button
  document.getElementById('bt-start').addEventListener('click', async () => {
    const btn = document.getElementById('bt-start');
    btn.disabled = true; btn.textContent = 'Launching…';
    try {
      await apiPost('/api/backtests', {
        symbol:              document.getElementById('bt-symbol').value,
        timeframe:           document.getElementById('bt-timeframe').value,
        days:                parseInt(document.getElementById('bt-days').value),
        balance:             parseFloat(document.getElementById('bt-balance').value),
        max_generations:     parseInt(document.getElementById('bt-gens').value),
        signal_mode:         document.getElementById('bt-signal-mode').value,
        signal_rules:        collectRules(),
        signal_logic:        document.getElementById('bt-signal-logic').value,
        signal_auto:         document.getElementById('bt-signal-auto').checked,
        use_session_filter:  document.getElementById('bt-tool-session').checked,
        use_volatility_filter: document.getElementById('bt-tool-volatility').checked,
        use_ema200_filter:   document.getElementById('bt-tool-ema200').checked,
        use_bos_guard:       document.getElementById('bt-tool-bos').checked,
        use_fvg_guard:       document.getElementById('bt-tool-fvg').checked,
        use_tick_jump_guard: document.getElementById('bt-tool-tick-jump').checked,
        use_liq_vacuum_guard: document.getElementById('bt-tool-liq-vacuum').checked,
        use_vwap_guard:      document.getElementById('bt-tool-vwap').checked,
        use_macd_filter:     document.getElementById('bt-tool-macd').checked,
        use_bollinger_filter: document.getElementById('bt-tool-bollinger').checked,
        use_adx_filter:      document.getElementById('bt-tool-adx').checked,
        use_volume_filter:   document.getElementById('bt-tool-volume').checked,
        use_swing_structure: document.getElementById('bt-tool-swing').checked,
      });
      window.showToast('Discovery launched 🚀');
      setTimeout(load, 500);
    } catch (err) {
      window.showToast(err.message, 'error');
    } finally {
      btn.disabled = false; btn.textContent = '🚀 Launch Discovery';
    }
  });

  document.getElementById('bt-refresh').addEventListener('click', load);

  // Track known states to detect transitions
  const _knownStates = {};

  // Auto-refresh running backtests
  const autoRefresh = setInterval(async () => {
    try {
      const data = await apiGet('/api/backtests');
      if (!data.backtests) return;

      let needsFullReload = false;

      for (const bt of data.backtests) {
        const prev = _knownStates[bt.run_id];
        _knownStates[bt.run_id] = bt.state;

        // State changed → full reload to update buttons, icons, stats
        if (prev && prev !== bt.state) {
          needsFullReload = true;
          if (bt.state === 'completed' || bt.state === 'failed' || bt.state === 'paused') {
            stopLogPoll(bt.run_id);
          }
          if (bt.state === 'completed') playSeedLabDone();
          continue;
        }

        // Partial update for in-progress backtests
        const card = document.querySelector(`[data-run-id="${bt.run_id}"]`);
        if (!card) continue;

        // Update message
        const msgEl = card.querySelector('.bt-message');
        if (msgEl && bt.message) msgEl.textContent = bt.message;

        // Update stats (Sharpe, WR, PnL, Gens)
        const statVals = card.querySelectorAll('.bt-stat-val');
        if (statVals.length >= 4) {
          statVals[0].textContent = bt.best_sharpe != null ? bt.best_sharpe.toFixed(2) : '—';
          statVals[1].textContent = bt.best_wr != null ? (bt.best_wr * 100).toFixed(1) + '%' : '—';
          statVals[2].textContent = bt.best_pnl != null ? '$' + bt.best_pnl.toFixed(0) : '—';
          statVals[3].textContent = `${bt.generation ?? 0}/${bt.max_generations ?? '—'}`;
        }

        // Update progress bar
        const fillEl = card.querySelector('.bt-progress-fill');
        const labelEl = card.querySelector('.bt-progress-label');
        if (fillEl && bt.max_generations > 0) {
          const pct = Math.round((bt.generation / bt.max_generations) * 100);
          fillEl.style.width = `${pct}%`;
          fillEl.style.background = (STATE_META[bt.state] || STATE_META.pending).color;
          if (labelEl) labelEl.textContent = `${bt.generation}/${bt.max_generations} (${pct}%)`;
        }
      }

      if (needsFullReload) load();
    } catch (_) {}
  }, 3000);

  window.addEventListener('route-change', () => { clearInterval(autoRefresh); stopAllPolls(); }, { once: true });

  await load();
}
