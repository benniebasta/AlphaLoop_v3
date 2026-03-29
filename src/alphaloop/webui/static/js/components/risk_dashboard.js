/**
 * Risk Dashboard — live risk metrics with gauges and indicators.
 */
import { apiGet } from '../api.js';

function pnlColor(val) {
  const n = parseFloat(val);
  if (n > 0) return 'var(--green)';
  if (n < 0) return 'var(--red)';
  return 'var(--muted)';
}

function fmt(val, prefix = '') {
  if (val == null) return '--';
  const n = parseFloat(val);
  return `${prefix}${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
}

function riskCard({ label, value, sub, color, icon }) {
  return `
    <div class="dcard">
      <div class="dcard-header">
        <span class="dcard-icon" style="background:${color}22;color:${color}">${icon}</span>
      </div>
      <div class="dcard-value" style="color:${color}">${value}</div>
      <div class="dcard-label">${label}</div>
      ${sub ? `<div class="dcard-sub">${sub}</div>` : ''}
    </div>`;
}

function winRateBar(pct) {
  const color = pct >= 50 ? 'var(--green)' : 'var(--red)';
  return `
    <div class="risk-bar-wrap">
      <div class="risk-bar-track">
        <div class="risk-bar-fill" style="width:${Math.min(pct, 100)}%;background:${color}"></div>
      </div>
      <span class="risk-bar-label" style="color:${color}">${pct}%</span>
    </div>`;
}

function heatMeter(openPositions, maxSlots) {
  const pct = Math.min((openPositions / Math.max(maxSlots, 1)) * 100, 100);
  let color = 'var(--green)';
  if (pct >= 80) color = 'var(--red)';
  else if (pct >= 50) color = 'var(--amber)';
  return `
    <div class="risk-heat">
      <div class="risk-heat-label">Portfolio Heat</div>
      <div class="risk-bar-track">
        <div class="risk-bar-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <div class="risk-heat-sub" style="color:${color}">${openPositions} / ${maxSlots} slots (${pct.toFixed(0)}%)</div>
    </div>`;
}

function renderRisk(data) {
  const pnl = parseFloat(data.daily_pnl ?? 0);
  const consec = data.consecutive_losses ?? 0;
  const consecColor = consec >= 3 ? 'var(--red)' : consec >= 1 ? 'var(--amber)' : 'var(--green)';
  const maxSlots = 5; // configurable max concurrent positions

  return `
    <div class="dash-grid">
      ${riskCard({
        icon: '\u{1F4B0}', label: 'Daily P&L', value: fmt(pnl, '$'),
        sub: `${data.daily_trades ?? 0} trades today`, color: pnlColor(pnl)
      })}
      ${riskCard({
        icon: '\u{1F4CA}', label: 'Open Positions', value: data.open_positions ?? 0,
        sub: 'active now', color: 'var(--blue)'
      })}
      ${riskCard({
        icon: '\u{1F525}', label: 'Consecutive Losses', value: consec,
        sub: consec >= 3 ? 'DANGER - consider pausing' : consec >= 1 ? 'monitor closely' : 'clean streak',
        color: consecColor
      })}
      ${riskCard({
        icon: '\u{2705}', label: 'Daily Wins', value: data.daily_wins ?? 0,
        sub: '', color: 'var(--green)'
      })}
      ${riskCard({
        icon: '\u{274C}', label: 'Daily Losses', value: data.daily_losses ?? 0,
        sub: '', color: 'var(--red)'
      })}
    </div>

    <div class="risk-section">
      <div class="risk-section-title">Daily Win Rate</div>
      ${winRateBar(data.daily_win_rate ?? 0)}
    </div>

    <div class="risk-section">
      ${heatMeter(data.open_positions ?? 0, maxSlots)}
    </div>
  `;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">Risk Dashboard</div>
      <div class="page-subtitle">Real-time risk monitoring and position health</div>
    </div>
    <div id="risk-content"><div class="dash-loading">Loading...</div></div>
    <div class="dash-footer">
      <span class="dash-updated" id="risk-updated">Last updated: ${new Date().toLocaleTimeString()}</span>
      <span class="dash-live"><span class="pulse-dot" style="position:static;display:inline-block"></span> Live (10s)</span>
    </div>`;

  async function load() {
    try {
      const data = await apiGet('/api/risk');
      const el = document.getElementById('risk-content');
      if (el) el.innerHTML = renderRisk(data);
      const ts = document.getElementById('risk-updated');
      if (ts) ts.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
    } catch (err) {
      const el = document.getElementById('risk-content');
      if (el) el.innerHTML = `<div class="page-error">Error: ${err.message}</div>`;
    }
  }

  await load();

  const pollTimer = setInterval(load, 10000);
  window.addEventListener('route-change', () => clearInterval(pollTimer), { once: true });

  window.addEventListener('ws-event', async (e) => {
    if (['TradeOpened', 'TradeClosed', 'RiskLimitHit'].includes(e.detail?.type)) load();
  });
}
