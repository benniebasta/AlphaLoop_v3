/**
 * Asset Params — per-asset, per-timeframe construction & tool parameter editor.
 *
 * Table layout: rows = assets, columns = TFs (M1–D1).
 * Each cell shows sl_min/sl_max inline. Click → modal with full param editor.
 * Orange dot = user override (differs from baked-in default).
 * "Reset" button in modal deletes the DB override.
 * "Add Asset" creates a new custom asset entry.
 */
import { apiGet, apiPost, apiPut, apiDelete } from '../api.js';

const TFS = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
const CLASS_ORDER = ['crypto', 'spot_metal', 'forex_major', 'forex_minor', 'index', 'stock', 'unknown'];
const CLASS_LABELS = {
  crypto: 'Crypto', spot_metal: 'Metals', forex_major: 'Forex',
  forex_minor: 'Forex Minor', index: 'Indices', stock: 'Stocks', unknown: 'Other',
};

const CONSTRUCTION_KEYS = [
  { key: 'sl_min_points',      label: 'SL Min (pts)',          type: 'number', step: 1 },
  { key: 'sl_max_points',      label: 'SL Max (pts)',          type: 'number', step: 1 },
  { key: 'sl_atr_mult',        label: 'SL ATR Mult',          type: 'number', step: 0.05 },
  { key: 'sl_buffer_atr',      label: 'SL Buffer ATR',        type: 'number', step: 0.01 },
  { key: 'tp1_rr',             label: 'TP1 R:R',              type: 'number', step: 0.1 },
  { key: 'tp2_rr',             label: 'TP2 R:R',              type: 'number', step: 0.1 },
  { key: 'entry_zone_atr_mult',label: 'Entry Zone ATR Mult',  type: 'number', step: 0.05 },
];

const TOOL_KEYS = {
  volatility_filter: [
    { key: 'max_atr_pct', label: 'Max ATR %', step: 0.1 },
    { key: 'min_atr_pct', label: 'Min ATR %', step: 0.001 },
  ],
  adx_filter:        [{ key: 'min_adx',           label: 'Min ADX',              step: 1 }],
  fvg_guard:         [{ key: 'min_size_atr',       label: 'FVG Min Size ATR',     step: 0.01 }],
  liq_vacuum_guard:  [{ key: 'max_range_atr',      label: 'LiqVac Max Range ATR', step: 0.1 }],
  tick_jump_guard:   [{ key: 'max_tick_jump_atr',  label: 'Tick Jump Max ATR',    step: 0.1 }],
  vwap_guard:        [{ key: 'max_extension_atr',  label: 'VWAP Max Ext ATR',     step: 0.1 }],
  rsi_feature: [
    { key: 'rsi_overbought', label: 'RSI Overbought', step: 1 },
    { key: 'rsi_oversold',   label: 'RSI Oversold',   step: 1 },
  ],
  bollinger_filter: [
    { key: 'buy_max_pct_b',  label: 'Buy Max %B',  step: 0.01 },
    { key: 'sell_min_pct_b', label: 'Sell Min %B', step: 0.01 },
  ],
  trendilo:          [{ key: 'strength_threshold', label: 'Slope Strength Min',   step: 1 }],
  choppiness_index: [
    { key: 'choppy_threshold',   label: 'Choppy Threshold',   step: 0.1 },
    { key: 'trending_threshold', label: 'Trending Threshold', step: 0.1 },
  ],
  volume_filter:     [{ key: 'min_vol_ratio',       label: 'Min Vol Ratio',        step: 0.05 }],
};

let _allAssets = [];
let _activeFilter = 'all';

export async function render(container) {
  container.innerHTML = `
    <div class="page-header" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
      <div>
        <div class="page-title">📊 Asset Params</div>
        <div class="page-subtitle">Per-asset, per-timeframe construction &amp; filter calibration</div>
      </div>
      <button id="ap-add-btn" class="btn-primary" style="margin-left:auto;">+ Add Asset</button>
    </div>

    <div class="ap-filter-bar" style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">
      <button class="ap-filter-btn active" data-filter="all">All</button>
      <button class="ap-filter-btn" data-filter="crypto">Crypto</button>
      <button class="ap-filter-btn" data-filter="spot_metal">Metals</button>
      <button class="ap-filter-btn" data-filter="forex_major">Forex</button>
      <button class="ap-filter-btn" data-filter="index">Indices</button>
      <button class="ap-filter-btn" data-filter="stock">Stocks</button>
    </div>

    <div id="ap-table-wrap" style="overflow-x:auto;">
      <div class="ap-loading">Loading…</div>
    </div>

    <div id="ap-modal-overlay" style="display:none;"></div>
  `;

  _applyFilterBarStyles();

  document.getElementById('ap-add-btn').addEventListener('click', _showAddAssetModal);

  document.querySelectorAll('.ap-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ap-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _activeFilter = btn.dataset.filter;
      _renderTable();
    });
  });

  await _load();
  window.addEventListener('route-change', _cleanup, { once: true });
}

async function _load() {
  try {
    const data = await apiGet('/api/asset-params');
    _allAssets = data.assets || [];
    _renderTable();
  } catch (err) {
    document.getElementById('ap-table-wrap').innerHTML =
      `<div class="page-error">⚠️ ${err.message}</div>`;
  }
}

function _renderTable() {
  const wrap = document.getElementById('ap-table-wrap');
  if (!wrap) return;

  const filtered = _activeFilter === 'all'
    ? _allAssets
    : _allAssets.filter(a => a.asset_class === _activeFilter);

  if (!filtered.length) {
    wrap.innerHTML = '<div style="color:var(--text-muted);padding:24px;">No assets found.</div>';
    return;
  }

  // Group by asset class
  const byClass = {};
  for (const asset of filtered) {
    const cls = asset.asset_class || 'unknown';
    if (!byClass[cls]) byClass[cls] = [];
    byClass[cls].push(asset);
  }

  let html = `
    <table class="ap-table">
      <thead>
        <tr>
          <th class="ap-th-symbol">Symbol</th>
          ${TFS.map(tf => `<th class="ap-th-tf">${tf}</th>`).join('')}
        </tr>
      </thead>
      <tbody>
  `;

  for (const cls of CLASS_ORDER) {
    if (!byClass[cls]) continue;
    html += `<tr class="ap-class-row"><td colspan="${TFS.length + 1}">${CLASS_LABELS[cls] || cls}</td></tr>`;
    for (const asset of byClass[cls]) {
      html += `<tr class="ap-asset-row" data-symbol="${asset.symbol}">`;
      html += `<td class="ap-td-symbol">
        <span class="ap-symbol">${asset.symbol}</span>
        <span class="ap-name">${asset.display_name}</span>
        ${asset.is_custom ? '<span class="ap-badge-custom">custom</span>' : ''}
      </td>`;
      for (const tf of TFS) {
        const tfData = (asset.timeframes || {})[tf] || {};
        const overridden = tfData.is_overridden;
        const slMin = tfData.sl_min_points != null ? _fmt(tfData.sl_min_points) : '—';
        const slMax = tfData.sl_max_points != null ? _fmt(tfData.sl_max_points) : '—';
        html += `
          <td class="ap-td-tf${overridden ? ' ap-overridden' : ''}"
              data-symbol="${asset.symbol}" data-tf="${tf}" title="Click to edit ${asset.symbol} ${tf}">
            <span class="ap-cell-vals">${slMin} / ${slMax}</span>
            ${overridden ? '<span class="ap-dot" title="User override active">●</span>' : ''}
          </td>`;
      }
      html += '</tr>';
    }
  }

  html += '</tbody></table>';
  wrap.innerHTML = html;

  _applyTableStyles();

  // Cell click → edit modal
  wrap.querySelectorAll('.ap-td-tf').forEach(td => {
    td.addEventListener('click', () => {
      const symbol = td.dataset.symbol;
      const tf = td.dataset.tf;
      const asset = _allAssets.find(a => a.symbol === symbol);
      if (asset) _showEditModal(asset, tf);
    });
  });
}

function _fmt(v) {
  return typeof v === 'number' && v % 1 !== 0 ? v.toFixed(v < 1 ? 3 : 2) : String(v);
}

function _showEditModal(asset, tf) {
  const tfData = (asset.timeframes || {})[tf] || {};
  const toolsConfig = tfData.tools_config || {};
  const isOverridden = tfData.is_overridden;

  const overlay = document.getElementById('ap-modal-overlay');
  overlay.style.display = 'flex';

  let constructionFields = CONSTRUCTION_KEYS.map(f => {
    const val = tfData[f.key] != null ? tfData[f.key] : '';
    return `
      <div class="ap-field-row">
        <label class="ap-field-label">${f.label}</label>
        <input class="ap-field-input" type="number" step="${f.step}"
               name="${f.key}" value="${val}" placeholder="—">
      </div>`;
  }).join('');

  let toolFields = '';
  for (const [toolName, fields] of Object.entries(TOOL_KEYS)) {
    const tc = toolsConfig[toolName] || {};
    toolFields += `<div class="ap-tool-group"><div class="ap-tool-name">${toolName}</div>`;
    for (const f of fields) {
      const val = tc[f.key] != null ? tc[f.key] : '';
      toolFields += `
        <div class="ap-field-row">
          <label class="ap-field-label">${f.label}</label>
          <input class="ap-field-input ap-tool-input" type="number" step="${f.step}"
                 data-tool="${toolName}" name="${f.key}" value="${val}" placeholder="—">
        </div>`;
    }
    toolFields += '</div>';
  }

  overlay.innerHTML = `
    <div class="ap-modal">
      <div class="ap-modal-header">
        <span class="ap-modal-title">${asset.symbol} — ${tf}</span>
        <button class="ap-modal-close" id="ap-modal-close">✕</button>
      </div>
      <div class="ap-modal-body">
        <div class="ap-section-label">Construction Params</div>
        <form id="ap-edit-form">
          ${constructionFields}
          <div class="ap-section-label" style="margin-top:16px;">Tool Configs</div>
          ${toolFields}
        </form>
      </div>
      <div class="ap-modal-footer">
        ${isOverridden
          ? `<button class="ap-btn-reset" id="ap-reset-btn">↺ Reset to Default</button>`
          : `<span style="color:var(--text-muted);font-size:12px;">Using baked-in default</span>`}
        <div style="flex:1"></div>
        <button class="ap-btn-cancel" id="ap-cancel-btn">Cancel</button>
        <button class="ap-btn-save" id="ap-save-btn">Save</button>
      </div>
    </div>
  `;

  _applyModalStyles();

  document.getElementById('ap-modal-close').onclick = _closeModal;
  document.getElementById('ap-cancel-btn').onclick = _closeModal;

  const resetBtn = document.getElementById('ap-reset-btn');
  if (resetBtn) {
    resetBtn.onclick = async () => {
      try {
        await apiDelete(`/api/asset-params/${asset.symbol}/${tf}`);
        _closeModal();
        await _load();
      } catch (err) {
        alert(`Reset failed: ${err.message}`);
      }
    };
  }

  document.getElementById('ap-save-btn').onclick = async () => {
    const form = document.getElementById('ap-edit-form');
    const params = {};
    const toolsCfg = {};

    // Construction params
    form.querySelectorAll('input.ap-field-input:not(.ap-tool-input)').forEach(inp => {
      if (inp.value !== '') params[inp.name] = parseFloat(inp.value);
    });

    // Tool configs
    form.querySelectorAll('input.ap-tool-input').forEach(inp => {
      if (inp.value !== '') {
        const tool = inp.dataset.tool;
        if (!toolsCfg[tool]) toolsCfg[tool] = {};
        toolsCfg[tool][inp.name] = parseFloat(inp.value);
      }
    });

    if (Object.keys(toolsCfg).length) params.tools_config = toolsCfg;

    if (!Object.keys(params).length) {
      _closeModal();
      return;
    }

    try {
      await apiPut(`/api/asset-params/${asset.symbol}/${tf}`, { params });
      _closeModal();
      await _load();
    } catch (err) {
      alert(`Save failed: ${err.message}`);
    }
  };

  // Close on overlay click
  overlay.addEventListener('click', e => {
    if (e.target === overlay) _closeModal();
  }, { once: true });
}

function _showAddAssetModal() {
  const overlay = document.getElementById('ap-modal-overlay');
  overlay.style.display = 'flex';
  overlay.innerHTML = `
    <div class="ap-modal" style="max-width:420px;">
      <div class="ap-modal-header">
        <span class="ap-modal-title">Add New Asset</span>
        <button class="ap-modal-close" id="ap-modal-close">✕</button>
      </div>
      <div class="ap-modal-body">
        <form id="ap-add-form">
          <div class="ap-field-row"><label class="ap-field-label">Symbol *</label>
            <input class="ap-field-input" name="symbol" placeholder="e.g. TSLA" required></div>
          <div class="ap-field-row"><label class="ap-field-label">Display Name</label>
            <input class="ap-field-input" name="display_name" placeholder="Tesla"></div>
          <div class="ap-field-row"><label class="ap-field-label">Asset Class</label>
            <select class="ap-field-input" name="asset_class">
              <option value="stock">Stock</option>
              <option value="crypto">Crypto</option>
              <option value="spot_metal">Metal</option>
              <option value="forex_major">Forex</option>
              <option value="index">Index</option>
              <option value="unknown">Other</option>
            </select></div>
          <div class="ap-field-row"><label class="ap-field-label">Pip Size *</label>
            <input class="ap-field-input" type="number" name="pip_size" step="0.0001" value="0.01" required></div>
          <div class="ap-field-row"><label class="ap-field-label">SL ATR Mult</label>
            <input class="ap-field-input" type="number" name="sl_atr_mult" step="0.1" value="1.5"></div>
          <div class="ap-field-row"><label class="ap-field-label">SL Min (pts)</label>
            <input class="ap-field-input" type="number" name="sl_min_points" step="1" value="100"></div>
          <div class="ap-field-row"><label class="ap-field-label">SL Max (pts)</label>
            <input class="ap-field-input" type="number" name="sl_max_points" step="1" value="1000"></div>
          <div class="ap-field-row"><label class="ap-field-label">Max Spread (pts)</label>
            <input class="ap-field-input" type="number" name="max_spread_points" step="1" value="50"></div>
        </form>
      </div>
      <div class="ap-modal-footer">
        <button class="ap-btn-cancel" id="ap-cancel-btn">Cancel</button>
        <button class="ap-btn-save" id="ap-save-btn">Create</button>
      </div>
    </div>
  `;
  _applyModalStyles();

  document.getElementById('ap-modal-close').onclick = _closeModal;
  document.getElementById('ap-cancel-btn').onclick = _closeModal;

  document.getElementById('ap-save-btn').onclick = async () => {
    const form = document.getElementById('ap-add-form');
    const fd = new FormData(form);
    const body = {};
    fd.forEach((v, k) => { body[k] = v; });
    body.pip_size = parseFloat(body.pip_size);
    body.sl_atr_mult = parseFloat(body.sl_atr_mult || 1.5);
    body.sl_min_points = parseFloat(body.sl_min_points || 100);
    body.sl_max_points = parseFloat(body.sl_max_points || 1000);
    body.max_spread_points = parseFloat(body.max_spread_points || 50);
    if (!body.symbol) { alert('Symbol is required'); return; }
    try {
      await apiPost('/api/asset-params', body);
      _closeModal();
      await _load();
    } catch (err) {
      alert(`Create failed: ${err.message}`);
    }
  };

  overlay.addEventListener('click', e => {
    if (e.target === overlay) _closeModal();
  }, { once: true });
}

function _closeModal() {
  const overlay = document.getElementById('ap-modal-overlay');
  if (overlay) { overlay.style.display = 'none'; overlay.innerHTML = ''; }
}

function _cleanup() {
  _closeModal();
}

// ── Inline styles ─────────────────────────────────────────────────────────────

function _applyFilterBarStyles() {
  if (document.getElementById('ap-styles')) return;
  const style = document.createElement('style');
  style.id = 'ap-styles';
  style.textContent = `
    .ap-filter-btn {
      padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border);
      background: var(--bg2); color: var(--muted); cursor: pointer; font-size: 13px;
      transition: all 0.15s;
    }
    .ap-filter-btn:hover, .ap-filter-btn.active {
      background: var(--primary); color: #000; border-color: var(--primary);
    }
    .ap-table {
      border-collapse: collapse; width: 100%; min-width: 720px;
      background: var(--bg2); border-radius: 10px; overflow: hidden;
      box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }
    .ap-th-symbol { text-align: left; padding: 10px 14px; font-size: 12px;
      color: var(--muted); background: var(--bg3);
      font-weight: 600; min-width: 140px; }
    .ap-th-tf { text-align: center; padding: 10px 8px; font-size: 12px;
      color: var(--muted); background: var(--bg3);
      font-weight: 600; min-width: 80px; }
    .ap-class-row td {
      padding: 6px 14px; font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
      text-transform: uppercase; color: var(--primary); background: rgba(239,159,39,0.06);
      border-top: 1px solid var(--border);
    }
    .ap-asset-row:hover { background: rgba(239,159,39,0.05); }
    .ap-td-symbol { padding: 10px 14px; border-bottom: 1px solid var(--border); }
    .ap-symbol { font-weight: 700; font-size: 13px; margin-right: 6px; }
    .ap-name { font-size: 11px; color: var(--muted); }
    .ap-badge-custom { font-size: 10px; background: var(--primary); color: #000;
      padding: 1px 5px; border-radius: 3px; margin-left: 4px; }
    .ap-td-tf {
      padding: 10px 8px; text-align: center; border-bottom: 1px solid var(--border);
      cursor: pointer; font-size: 12px; position: relative; transition: background 0.12s;
    }
    .ap-td-tf:hover { background: rgba(239,159,39,0.12); }
    .ap-td-tf.ap-overridden { background: rgba(239,159,39,0.05); }
    .ap-cell-vals { display: block; font-family: monospace; font-size: 11px;
      color: var(--text-secondary); }
    .ap-dot { position: absolute; top: 4px; right: 5px; font-size: 8px; color: var(--primary); }
    /* Modal */
    #ap-modal-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.65);
      z-index: 1000; display: flex; align-items: center; justify-content: center;
    }
    .ap-modal {
      background: var(--bg2); border-radius: 12px; width: 560px; max-width: 95vw;
      max-height: 85vh; display: flex; flex-direction: column;
      box-shadow: 0 8px 32px rgba(0,0,0,0.5); border: 1px solid var(--border);
    }
    .ap-modal-header {
      display: flex; align-items: center; padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }
    .ap-modal-title { font-weight: 700; font-size: 15px; flex: 1; }
    .ap-modal-close { background: none; border: none; cursor: pointer; color: var(--muted);
      font-size: 18px; padding: 0 4px; }
    .ap-modal-body { padding: 16px 20px; overflow-y: auto; flex: 1; }
    .ap-modal-footer {
      display: flex; align-items: center; gap: 10px; padding: 14px 20px;
      border-top: 1px solid var(--border);
    }
    .ap-section-label { font-size: 11px; font-weight: 700; letter-spacing: 0.07em;
      text-transform: uppercase; color: var(--primary); margin-bottom: 10px; }
    .ap-field-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
    .ap-field-label { font-size: 12px; color: var(--muted); min-width: 160px; }
    .ap-field-input { flex: 1; background: var(--input-bg);
      border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px;
      color: var(--text); font-size: 13px; }
    .ap-tool-group { margin-bottom: 12px; padding: 10px; background: var(--bg3);
      border-radius: 8px; border: 1px solid var(--border); }
    .ap-tool-name { font-size: 11px; font-weight: 600; color: var(--muted);
      margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
    .ap-btn-save { background: var(--primary); color: #000; border: none; border-radius: 6px;
      padding: 8px 18px; cursor: pointer; font-weight: 600; font-size: 13px; }
    .ap-btn-cancel { background: none; border: 1px solid var(--border); border-radius: 6px;
      padding: 8px 14px; cursor: pointer; color: var(--muted); font-size: 13px; }
    .ap-btn-reset { background: none; border: 1px solid #f87171; border-radius: 6px;
      padding: 8px 14px; cursor: pointer; color: #f87171; font-size: 13px; }
    .ap-loading { padding: 40px; text-align: center; color: var(--muted); }
  `;
  document.head.appendChild(style);
}

function _applyTableStyles() { /* styles already applied in _applyFilterBarStyles */ }
function _applyModalStyles() { /* styles already applied in _applyFilterBarStyles */ }
