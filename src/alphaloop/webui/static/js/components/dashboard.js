/**
 * Dashboard — trading overview with rich stat cards.
 */
import { apiGet } from '../api.js';

function fmt(val, prefix = '') {
  if (val == null) return '—';
  const n = parseFloat(val);
  return `${prefix}${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
}

function pnlColor(val) {
  const n = parseFloat(val);
  if (n > 0) return 'var(--green)';
  if (n < 0) return 'var(--red)';
  return 'var(--muted)';
}

function statCard({ icon, label, value, sub, color, pulse }) {
  return `
    <div class="dcard">
      <div class="dcard-header">
        <span class="dcard-icon" style="background:${color}22;color:${color}">${icon}</span>
        ${pulse ? '<span class="pulse-dot"></span>' : ''}
      </div>
      <div class="dcard-value" style="color:${color}">${value}</div>
      <div class="dcard-label">${label}</div>
      ${sub ? `<div class="dcard-sub">${sub}</div>` : ''}
    </div>`;
}

function renderDash(data) {
  const daily = parseFloat(data.daily_pnl ?? 0);
  const weekly = parseFloat(data.weekly_pnl ?? 0);
  const total = parseFloat(data.total_pnl ?? 0);

  return `
    <div class="dash-grid">
      ${statCard({ icon: '📈', label: 'Open Trades', value: data.open_trades ?? 0,
        sub: 'active positions', color: 'var(--blue)', pulse: (data.open_trades > 0) })}
      ${statCard({ icon: '💰', label: 'Daily P&L', value: fmt(daily, '$'),
        sub: `${data.daily_trades ?? 0} trades today`, color: pnlColor(daily) })}
      ${statCard({ icon: '🎯', label: 'Daily Win Rate', value: `${data.daily_win_rate ?? 0}%`,
        sub: 'today', color: parseFloat(data.daily_win_rate) >= 50 ? 'var(--green)' : 'var(--red)' })}
      ${statCard({ icon: '📅', label: 'Weekly P&L', value: fmt(weekly, '$'),
        sub: 'this week', color: pnlColor(weekly) })}
      ${statCard({ icon: '🏦', label: 'Total P&L', value: fmt(total, '$'),
        sub: `${data.total_trades ?? 0} total trades`, color: pnlColor(total) })}
      ${statCard({ icon: '🏆', label: 'Overall Win Rate', value: `${data.overall_win_rate ?? 0}%`,
        sub: 'all time', color: parseFloat(data.overall_win_rate) >= 50 ? 'var(--green)' : 'var(--amber)' })}
    </div>
    <div class="dash-footer">
      <span class="dash-updated">Last updated: ${new Date().toLocaleTimeString()}</span>
      <span class="dash-live"><span class="pulse-dot" style="position:static;display:inline-block"></span> Live</span>
    </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">📊 Dashboard</div>
      <div class="page-subtitle">Real-time trading performance overview</div>
    </div>
    <div id="dash-content"><div class="dash-loading">Loading…</div></div>`;

  async function load() {
    try {
      const data = await apiGet('/api/dashboard');
      const el = document.getElementById('dash-content');
      if (el) el.innerHTML = renderDash(data);
    } catch (err) {
      const el = document.getElementById('dash-content');
      if (el) el.innerHTML = `<div class="page-error">⚠️ ${err.message}</div>`;
    }
  }

  await load();

  // Polling fallback — refresh every 30s in case WebSocket is down
  const pollTimer = setInterval(load, 30000);
  window.addEventListener('route-change', () => clearInterval(pollTimer), { once: true });

  window.addEventListener('ws-event', async (e) => {
    if (['TradeOpened', 'TradeClosed'].includes(e.detail?.type)) load();
  });
}
