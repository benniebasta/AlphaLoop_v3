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
};

const STATUS_LABELS = {
  candidate: 'Candidate',
  dry_run: 'Dry Run',
  demo: 'Demo',
  live: 'Live',
  retired: 'Retired',
};

const STATUS_ORDER = ['candidate', 'dry_run', 'demo', 'live'];

let _activeSymbol = ''; // '' = all
let _modelCatalog = []; // loaded from /api/test/models

export async function render(el) {
  el.innerHTML = `
    <div class="page-title">🎯 Strategies</div>

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

  async function load() {
    const params = new URLSearchParams();
    if (_activeSymbol) params.set('symbol', _activeSymbol);
    if (filterStat.value) params.set('status', filterStat.value);

    const data = await apiGet(`/api/strategies?${params}`);
    const strategies = data.strategies || [];

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
            Run a backtest to auto-create strategy versions
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
      const wr = (sum.win_rate||0);
      const sharpe = (sum.sharpe||0);
      const pnl = (sum.total_pnl||0);
      const dd = (sum.max_dd_pct||0);

      return `
        <div class="strat-card-box" style="border-top:3px solid ${color}">
          <div class="strat-card-header">
            <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              <strong style="font-size:0.8rem">${s.name || s.symbol + ' v' + s.version}</strong>
            </div>
            <span class="badge" style="background:${color};color:#000;font-size:0.6rem;padding:2px 6px;flex-shrink:0">${label}</span>
          </div>
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
          <div class="strat-models-toggle" data-toggle-models="${s.symbol}_${s.version}" style="font-size:0.7rem;color:var(--blue);padding:0.2rem 0.5rem;cursor:pointer">
            ▶ AI Models: ${(s.ai_models?.signal || 'default').split('/').pop()}
          </div>
          <div class="strat-models-panel" id="models-${s.symbol}_${s.version}" style="display:none;padding:0.3rem 0.5rem;border-top:1px solid var(--border)">
            ${['signal','validator','research','autolearn'].map(role => {
              const current = s.ai_models?.[role] || '';
              const opts = _modelCatalog.map(m =>
                `<option value="${m.id}" ${current === m.id ? 'selected' : ''}>${m.display_name}</option>`
              ).join('');
              const hasVal = current && _modelCatalog.some(m => m.id === current);
              const customOpt = current && !hasVal ? `<option value="${current}" selected>${current}</option>` : '';
              return `
                <div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.3rem">
                  <span style="font-size:0.68rem;color:var(--muted);width:65px;text-transform:capitalize">${role}</span>
                  <select class="field-input strat-model-sel" data-sym="${s.symbol}" data-ver="${s.version}" data-role="${role}" style="font-size:0.72rem;padding:2px 4px;flex:1">
                    <option value="">Use Default</option>
                    ${customOpt}${opts}
                  </select>
                </div>`;
            }).join('')}
            <button class="btn btn-sm strat-save-models" data-symbol="${s.symbol}" data-version="${s.version}" style="font-size:0.68rem;padding:2px 8px;margin-top:0.2rem">Save Models</button>
            <span class="strat-model-hint" data-hint="${s.symbol}_${s.version}" style="font-size:0.7rem;margin-left:0.5rem"></span>
          </div>
          ${(s.status === 'candidate' || s.status === 'dry_run') ? `
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
            ${canPromote ? `<button class="btn btn-sm strat-promote" data-symbol="${s.symbol}" data-version="${s.version}" style="background:var(--blue)">Promote</button>` : ''}
            ${canActivate ? `<button class="btn btn-sm strat-activate" data-symbol="${s.symbol}" data-version="${s.version}" style="background:var(--green)">Activate</button>` : ''}
            <button class="btn btn-sm strat-delete" data-symbol="${s.symbol}" data-version="${s.version}" style="background:var(--red)" title="Delete">&#10005;</button>
          </div>
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
        btn.addEventListener('click', () => {
          _activeSymbol = sym;
          tabsContainer.querySelectorAll('.strat-tab').forEach(t => t.classList.remove('active'));
          btn.classList.add('active');
          load();
        });
        tabsContainer.appendChild(btn);
      }
    }
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

    container.querySelectorAll('.strat-promote').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sym = btn.dataset.symbol;
        const ver = btn.dataset.version;
        btn.disabled = true;
        btn.textContent = 'Promoting...';
        try {
          const result = await apiPost(`/api/strategies/${sym}/v${ver}/promote`, { cycles_completed: 0 });
          if (result.promoted) {
            showToast(`Promoted ${sym} v${ver} to ${result.new_status}`);
          } else {
            showToast(`Not eligible: ${(result.reasons||[]).join(', ')}`, 'warning');
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
        if (!confirm(`Delete ${sym} v${ver}?`)) return;
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

  // Load model catalog for dropdowns
  try {
    const md = await apiGet('/api/test/models');
    _modelCatalog = md.models || [];
  } catch (_) {}

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
