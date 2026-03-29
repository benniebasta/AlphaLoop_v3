/**
 * Event Log — live-updating event bus activity table.
 */
import { apiGet } from '../api.js';

const TYPE_COLORS = {
  TradeOpened:       'var(--green)',
  TradeClosed:       'var(--blue)',
  SignalGenerated:   'var(--amber)',
  SignalValidated:   'var(--green)',
  SignalRejected:    'var(--red)',
  RiskLimitHit:      'var(--red)',
  PipelineBlocked:   'var(--red)',
  ConfigChanged:     'var(--muted)',
  StrategyPromoted:  'var(--blue)',
  SeedLabProgress:   'var(--amber)',
  CanaryStarted:     'var(--amber)',
  CanaryEnded:       'var(--blue)',
  MetaLoopCompleted: 'var(--green)',
  StrategyRolledBack:'var(--red)',
  ResearchCompleted: 'var(--blue)',
};

function badgeColor(type) {
  return TYPE_COLORS[type] || 'var(--muted)';
}

function fmtTime(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleTimeString();
}

function extractSymbol(data) {
  return data?.symbol || '--';
}

function extractDetails(data) {
  if (!data) return '--';
  const skip = new Set(['symbol', 'timestamp']);
  const parts = [];
  for (const [k, v] of Object.entries(data)) {
    if (skip.has(k) || v === '' || v === null || v === undefined) continue;
    if (typeof v === 'object') continue;
    parts.push(`${k}: ${v}`);
  }
  return parts.join(', ') || '--';
}

function renderTable(events) {
  if (!events.length) {
    return '<div class="page-empty">No events recorded yet.</div>';
  }
  const rows = events.map(e => `
    <tr>
      <td class="el-time">${fmtTime(e.timestamp)}</td>
      <td><span class="el-badge" style="background:${badgeColor(e.type)}22;color:${badgeColor(e.type)};border:1px solid ${badgeColor(e.type)}44">${e.type}</span></td>
      <td class="el-symbol">${extractSymbol(e.data)}</td>
      <td class="el-details">${extractDetails(e.data)}</td>
    </tr>`).join('');

  return `
    <div class="table-scroll-wrap">
      <table class="el-table">
        <thead>
          <tr>
            <th>Time</th><th>Type</th><th>Symbol</th><th>Details</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderFilters(types, active) {
  const btns = ['ALL', ...types].map(t => {
    const cls = t === active ? 'el-filter-btn active' : 'el-filter-btn';
    return `<button class="${cls}" data-filter="${t}">${t}</button>`;
  }).join('');
  return `<div class="el-filters">${btns}</div>`;
}

export async function render(container) {
  let activeFilter = 'ALL';
  const knownTypes = Object.keys(TYPE_COLORS);

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">Event Log</div>
      <div class="page-subtitle">Real-time event bus activity</div>
    </div>
    <div id="el-filters"></div>
    <div id="el-content"><div class="dash-loading">Loading...</div></div>
    <div id="el-footer" class="dash-footer">
      <span class="dash-updated">Last updated: ${new Date().toLocaleTimeString()}</span>
      <span class="dash-live"><span class="pulse-dot" style="position:static;display:inline-block"></span> Live (5s)</span>
    </div>`;

  document.getElementById('el-filters').innerHTML = renderFilters(knownTypes, activeFilter);

  container.addEventListener('click', (e) => {
    const btn = e.target.closest('.el-filter-btn');
    if (!btn) return;
    activeFilter = btn.dataset.filter;
    document.getElementById('el-filters').innerHTML = renderFilters(knownTypes, activeFilter);
    load();
  });

  async function load() {
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (activeFilter !== 'ALL') params.set('type', activeFilter);
      const data = await apiGet(`/api/events?${params}`);
      const el = document.getElementById('el-content');
      if (el) el.innerHTML = renderTable(data.events || []);
      const footer = document.getElementById('el-footer');
      if (footer) {
        footer.querySelector('.dash-updated').textContent =
          `Last updated: ${new Date().toLocaleTimeString()} | ${data.total ?? 0} total events`;
      }
    } catch (err) {
      const el = document.getElementById('el-content');
      if (el) el.innerHTML = `<div class="page-error">Error: ${err.message}</div>`;
    }
  }

  await load();

  const pollTimer = setInterval(load, 5000);
  window.addEventListener('route-change', () => clearInterval(pollTimer), { once: true });

  window.addEventListener('ws-event', async (e) => {
    load();
  });
}
