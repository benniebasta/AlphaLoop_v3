/**
 * Research tab — reports and evolution events.
 */
import { apiGet } from '../api.js';

export async function render(container) {
  container.innerHTML = `
    <div class="page-title">📈 Research</div>
    <div class="card">
      <div class="card-title">Recent Reports</div>
      <table class="data-table">
        <thead><tr>
          <th>Date</th><th>Symbol</th><th>Trades</th><th>Win Rate</th>
          <th>Avg RR</th><th>P&L</th><th>Sharpe</th><th>Max DD</th>
        </tr></thead>
        <tbody id="reports-body"><tr><td colspan="8">Loading...</td></tr></tbody>
      </table>
    </div>
    <div class="card">
      <div class="card-title">Evolution Events</div>
      <table class="data-table">
        <thead><tr>
          <th>Time</th><th>Symbol</th><th>Type</th><th>Version</th><th>Details</th>
        </tr></thead>
        <tbody id="evo-body"><tr><td colspan="5">Loading...</td></tr></tbody>
      </table>
    </div>
  `;

  // Load reports
  try {
    const data = await apiGet('/api/research');
    const tbody = document.getElementById('reports-body');
    if (data.reports.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No reports</td></tr>';
    } else {
      tbody.innerHTML = data.reports.map(r => `<tr>
        <td>${r.report_date ? new Date(r.report_date).toLocaleDateString() : '—'}</td>
        <td>${r.symbol || '—'}</td>
        <td>${r.total_trades ?? '—'}</td>
        <td>${r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '—'}</td>
        <td>${r.avg_rr?.toFixed(2) ?? '—'}</td>
        <td class="${(r.total_pnl_usd || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}">
          ${r.total_pnl_usd != null ? '$' + r.total_pnl_usd.toFixed(2) : '—'}
        </td>
        <td>${r.sharpe_ratio?.toFixed(2) ?? '—'}</td>
        <td>${r.max_drawdown_pct != null ? (r.max_drawdown_pct * 100).toFixed(1) + '%' : '—'}</td>
      </tr>`).join('');
    }
  } catch (err) {
    document.getElementById('reports-body').innerHTML =
      `<tr><td colspan="8" style="color:var(--red)">${err.message}</td></tr>`;
  }

  // Load evolution events
  try {
    const data = await apiGet('/api/research/evolution');
    const tbody = document.getElementById('evo-body');
    if (data.events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No events</td></tr>';
    } else {
      tbody.innerHTML = data.events.map(e => `<tr>
        <td>${e.occurred_at ? new Date(e.occurred_at).toLocaleString() : '—'}</td>
        <td>${e.symbol || '—'}</td>
        <td><span class="badge badge-blue">${e.event_type || '—'}</span></td>
        <td>${e.strategy_version || '—'}</td>
        <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${e.details || '—'}</td>
      </tr>`).join('');
    }
  } catch (err) {
    document.getElementById('evo-body').innerHTML =
      `<tr><td colspan="5" style="color:var(--red)">${err.message}</td></tr>`;
  }
}
