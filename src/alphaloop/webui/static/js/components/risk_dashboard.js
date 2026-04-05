/**
 * Risk Dashboard — live risk metrics with gauges, VaR/CVaR, and stress scenarios.
 *
 * v3.1 additions:
 *  - VaR/CVaR gauges (Section B)
 *  - Stress Test scenarios panel (Section C)
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
  else if (pct >= 50) color = 'var(--amber, #f59e0b)';
  return `
    <div class="risk-heat">
      <div class="risk-heat-label">Portfolio Heat</div>
      <div class="risk-bar-track">
        <div class="risk-bar-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <div class="risk-heat-sub" style="color:${color}">${openPositions} / ${maxSlots} slots (${pct.toFixed(0)}%)</div>
    </div>`;
}

function varGauge(label, value, balance) {
  if (value == null) {
    return `
      <div class="var-gauge">
        <div class="var-gauge-label">${label}</div>
        <div class="var-gauge-value" style="color:var(--muted)">--</div>
        <div class="var-gauge-sub" style="color:var(--muted)">Not enough trade history</div>
      </div>`;
  }
  const loss = Math.abs(value);
  const pct = balance > 0 ? (loss / balance * 100).toFixed(2) : '--';
  let color = 'var(--green)';
  if (balance > 0) {
    if (loss / balance >= 0.03) color = 'var(--red)';
    else if (loss / balance >= 0.015) color = 'var(--amber, #f59e0b)';
  }
  return `
    <div class="var-gauge">
      <div class="var-gauge-label">${label}</div>
      <div class="var-gauge-value" style="color:${color}">-$${loss.toFixed(2)}</div>
      <div class="var-gauge-sub" style="color:${color}">${pct}% of balance</div>
    </div>`;
}

function stressRow(s) {
  const loss = parseFloat(s.simulated_loss_usd);
  const pct = parseFloat(s.simulated_loss_pct);
  const isMarginCall = s.margin_call_risk;
  const lossColor = loss >= 0 ? 'var(--green)' : 'var(--red)';
  const badge = isMarginCall
    ? `<span class="badge badge-red" style="font-size:0.65rem">⚠ Margin Call Risk</span>`
    : `<span class="badge badge-green" style="font-size:0.65rem">OK</span>`;
  return `
    <tr>
      <td><strong>${s.scenario_name}</strong><div style="font-size:0.7rem;color:var(--muted)">${s.description}</div></td>
      <td style="color:${lossColor}">$${loss.toFixed(2)}</td>
      <td style="color:${lossColor}">${pct.toFixed(2)}%</td>
      <td>$${parseFloat(s.final_equity).toFixed(2)}</td>
      <td>${badge}</td>
    </tr>`;
}

function renderRisk(data, stress) {
  const pnl = parseFloat(data.daily_pnl ?? 0);
  const consec = data.consecutive_losses ?? 0;
  const consecColor = consec >= 3 ? 'var(--red)' : consec >= 1 ? 'var(--amber, #f59e0b)' : 'var(--green)';
  const maxSlots = 5;

  // VaR breach alert banner
  const varBreach = data.var_breach_today && data.var_95 != null;
  const varAlert = varBreach
    ? `<div class="card" style="background:rgba(239,68,68,0.1);border:1px solid var(--red);padding:10px 16px;margin-bottom:16px;display:flex;align-items:center;gap:8px">
        <span style="font-size:1.2rem">⚠️</span>
        <span style="color:var(--red);font-weight:600">VaR Breach — Daily P&amp;L has exceeded the 95% VaR threshold ($${Math.abs(data.var_95).toFixed(2)}). Risk advisory active.</span>
       </div>`
    : '';

  // Stress scenarios table
  const stressHtml = stress && stress.scenarios && stress.scenarios.length > 0
    ? `<table class="data-table" style="margin:0;font-size:0.78rem">
        <thead><tr>
          <th>Scenario</th><th>Simulated Loss</th><th>Loss %</th><th>Final Equity</th><th>Status</th>
        </tr></thead>
        <tbody>${stress.scenarios.map(stressRow).join('')}</tbody>
       </table>
       <div style="font-size:0.72rem;color:var(--muted);margin-top:6px;padding:0 4px">
         Estimated balance: $${parseFloat(stress.estimated_balance ?? 0).toFixed(2)} |
         Open lots: ${parseFloat(stress.open_lot_exposure ?? 0).toFixed(4)}
       </div>`
    : `<div style="padding:12px 16px;color:var(--muted);font-size:0.8rem">Stress scenarios unavailable.</div>`;

  return `
    ${varAlert}

    <!-- Section A: Core Risk Cards -->
    <div class="dash-grid">
      ${riskCard({
        icon: '💰', label: 'Daily P&L', value: fmt(pnl, '$'),
        sub: `${data.daily_trades ?? 0} trades today`, color: pnlColor(pnl)
      })}
      ${riskCard({
        icon: '📊', label: 'Open Positions', value: data.open_positions ?? 0,
        sub: 'active now', color: 'var(--blue)'
      })}
      ${riskCard({
        icon: '🔥', label: 'Consecutive Losses', value: consec,
        sub: consec >= 3 ? 'DANGER — consider pausing' : consec >= 1 ? 'monitor closely' : 'clean streak',
        color: consecColor
      })}
      ${riskCard({
        icon: '✅', label: 'Daily Wins', value: data.daily_wins ?? 0,
        sub: '', color: 'var(--green)'
      })}
      ${riskCard({
        icon: '❌', label: 'Daily Losses', value: data.daily_losses ?? 0,
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

    <!-- Section B: VaR / CVaR Gauges -->
    <div class="section-label" style="margin-top:20px">
      Probabilistic Risk (VaR / CVaR)
      <span style="color:var(--muted);font-size:0.72rem;margin-left:8px">
        ${data.var_observations > 0 ? `based on ${data.var_observations} trades` : 'needs ≥5 closed trades'}
      </span>
    </div>
    <div class="card">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px">
        ${varGauge('VaR 95%', data.var_95, 10000)}
        ${varGauge('CVaR 95% (ES)', data.cvar_95, 10000)}
        ${varGauge('VaR 99%', data.var_99, 10000)}
      </div>
      <div style="margin-top:8px;font-size:0.72rem;color:var(--muted)">
        VaR = maximum expected loss at the given confidence level. CVaR = average loss beyond VaR (worst-case tail). Advisory only — does not block trading.
      </div>
    </div>

    <!-- Section C: Stress Test Scenarios -->
    <div class="section-label" style="margin-top:20px">Stress Test Scenarios</div>
    <div class="card" style="padding:0;overflow:auto">
      ${stressHtml}
    </div>
  `;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🛡️ Risk Dashboard</div>
      <div class="page-subtitle">Real-time risk monitoring, VaR gauges, and stress scenarios</div>
    </div>
    <div id="risk-content"><div class="dash-loading">Loading...</div></div>
    <div class="dash-footer">
      <span class="dash-updated" id="risk-updated">Last updated: ${new Date().toLocaleTimeString()}</span>
      <span class="dash-live"><span class="pulse-dot" style="position:static;display:inline-block"></span> Live (10s)</span>
    </div>`;

  async function load() {
    try {
      const [data, stress] = await Promise.all([
        apiGet('/api/risk'),
        apiGet('/api/risk/stress').catch(() => ({ scenarios: [] })),
      ]);
      const el = document.getElementById('risk-content');
      if (el) el.innerHTML = renderRisk(data, stress);
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
