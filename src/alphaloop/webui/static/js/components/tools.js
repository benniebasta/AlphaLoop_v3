/**
 * Tools — filter pipeline decisions and rejection stats.
 */
import { apiGet } from '../api.js';

const FILTER_META = {
  risk_filter:        { label: 'Risk Filter',       icon: '⚖️',  color: 'var(--red)' },
  volatility_filter:  { label: 'Volatility Filter', icon: '📊',  color: 'var(--amber)' },
  sentiment_filter:   { label: 'Sentiment Filter',  icon: '🌐',  color: 'var(--purple)' },
  session_filter:     { label: 'Session Filter',    icon: '🕐',  color: 'var(--blue)' },
  news_filter:        { label: 'News Filter',       icon: '📰',  color: 'var(--teal)' },
  dxy_filter:         { label: 'DXY Filter',        icon: '💵',  color: 'var(--green)' },
  bos_guard:          { label: 'BOS Guard',         icon: '🔀',  color: 'var(--red)' },
  fvg_guard:          { label: 'FVG Guard',         icon: '🕳️',  color: 'var(--amber)' },
  vwap_guard:         { label: 'VWAP Guard',        icon: '📏',  color: 'var(--blue)' },
  correlation_guard:  { label: 'Correlation Guard',  icon: '🔗',  color: 'var(--purple)' },
  tick_jump_guard:    { label: 'Tick Jump Guard',    icon: '⚡',  color: 'var(--amber)' },
  liq_vacuum_guard:   { label: 'Liq Vacuum Guard',   icon: '🌀',  color: 'var(--teal)' },
  ema200_filter:      { label: 'EMA 200 Filter',     icon: '📈',  color: 'var(--green)' },
  macd_filter:        { label: 'MACD Filter',        icon: '📉',  color: 'var(--red)' },
  bollinger_filter:   { label: 'Bollinger Filter',   icon: '🎯',  color: 'var(--purple)' },
};

function dirBadge(dir) {
  if (!dir) return '—';
  const up = dir.toUpperCase() === 'BUY' || dir.toUpperCase() === 'LONG';
  return `<span class="dir-badge ${up ? 'dir-buy' : 'dir-sell'}">${dir}</span>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🛠️ Tools &amp; Pipeline</div>
      <div class="page-subtitle">Filter pipeline decisions and signal rejection analysis</div>
    </div>
    <div class="tools-loading">Loading…</div>`;

  let data;
  try {
    data = await apiGet('/api/tools');
  } catch (err) {
    container.querySelector('.tools-loading').innerHTML = `<div class="page-error">⚠️ ${err.message}</div>`;
    return;
  }

  const decisions = data.decisions || [];
  const counts = data.rejection_counts || {};
  const total = decisions.length;
  const allowed = decisions.filter(d => d.allowed).length;
  const blocked = total - allowed;
  const pct = total > 0 ? Math.round((allowed / total) * 100) : 0;

  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🛠️ Tools &amp; Pipeline</div>
      <div class="page-subtitle">Filter pipeline decisions and signal rejection analysis</div>
    </div>

    <!-- Summary bar -->
    <div class="pipeline-summary">
      <div class="ps-stat">
        <div class="ps-val" style="color:var(--green)">${allowed}</div>
        <div class="ps-lbl">Allowed</div>
      </div>
      <div class="ps-divider"></div>
      <div class="ps-stat">
        <div class="ps-val" style="color:var(--red)">${blocked}</div>
        <div class="ps-lbl">Blocked</div>
      </div>
      <div class="ps-divider"></div>
      <div class="ps-stat">
        <div class="ps-val">${total}</div>
        <div class="ps-lbl">Total</div>
      </div>
      <div class="ps-bar-wrap">
        <div class="ps-bar-label">Pass rate</div>
        <div class="ps-bar">
          <div class="ps-bar-fill" style="width:${pct}%;background:${pct >= 50 ? 'var(--green)' : 'var(--red)'}"></div>
        </div>
        <div class="ps-bar-pct">${pct}%</div>
      </div>
    </div>

    <!-- Rejection filter cards -->
    <div class="panel-card">
    <div class="section-label">Rejection Counts by Filter</div>
    <div class="filter-cards">
      ${Object.keys(FILTER_META).map(key => {
        const meta = FILTER_META[key];
        const count = counts[key] ?? 0;
        const maxCount = Math.max(...Object.values(counts), 1);
        const barPct = Math.round((count / maxCount) * 100);
        return `
          <div class="filter-card">
            <div class="fc-icon" style="color:${meta.color}">${meta.icon}</div>
            <div class="fc-body">
              <div class="fc-label">${meta.label}</div>
              <div class="fc-bar">
                <div class="fc-bar-fill" style="width:${barPct}%;background:${meta.color}"></div>
              </div>
            </div>
            <div class="fc-count" style="color:${count > 0 ? meta.color : 'var(--muted)'}">${count}</div>
          </div>`;
      }).join('')}
    </div>
    </div>

    <!-- Decisions table -->
    <div class="section-label" style="margin-top:24px">Recent Pipeline Decisions</div>
    <div class="panel-card" style="overflow-x:auto;padding:0">
      <table class="data-table">
        <thead><tr>
          <th>Time</th><th>Symbol</th><th>Direction</th><th>Decision</th>
          <th>Blocked By</th><th>Reason</th><th>Size Mod</th>
        </tr></thead>
        <tbody>
          ${decisions.length === 0
            ? '<tr><td colspan="7"><div class="empty-state"><div class="icon">🔍</div>No decisions recorded</div></td></tr>'
            : decisions.map(d => `<tr>
                <td class="mono-sm">${d.occurred_at ? new Date(d.occurred_at).toLocaleString() : '—'}</td>
                <td><span class="symbol-tag">${d.symbol || '—'}</span></td>
                <td>${dirBadge(d.direction)}</td>
                <td>${d.allowed
                  ? '<span class="badge badge-green">✓ Allowed</span>'
                  : '<span class="badge badge-red">✗ Blocked</span>'}</td>
                <td>${d.blocked_by ? `<span class="badge badge-muted">${d.blocked_by}</span>` : '—'}</td>
                <td class="reason-cell">${d.block_reason || '—'}</td>
                <td>${d.size_modifier != null ? `<span class="mono-sm">${d.size_modifier.toFixed(2)}</span>` : '—'}</td>
              </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
