/**
 * Trades tab — trade list and details.
 */
import { apiGet } from '../api.js';

function outcomeBadge(outcome) {
  const map = {
    WIN: 'badge-green', LOSS: 'badge-red', BE: 'badge-amber',
    OPEN: 'badge-blue',
  };
  return `<span class="badge ${map[outcome] || 'badge-muted'}">${outcome || '—'}</span>`;
}

function pnlCell(val) {
  if (val == null) return '—';
  const cls = val > 0 ? 'pnl-positive' : val < 0 ? 'pnl-negative' : 'pnl-zero';
  return `<span class="${cls}">${val >= 0 ? '+' : ''}${val.toFixed(2)}</span>`;
}

function renderRow(t) {
  return `<tr>
    <td>${t.id}</td>
    <td>${t.symbol || '—'}</td>
    <td>${t.direction || '—'}</td>
    <td>${t.setup_type || '—'}</td>
    <td>${t.entry_price?.toFixed(2) ?? '—'}</td>
    <td>${t.lot_size?.toFixed(2) ?? '—'}</td>
    <td>${outcomeBadge(t.outcome)}</td>
    <td>${pnlCell(t.pnl_usd)}</td>
    <td>${t.opened_at ? new Date(t.opened_at).toLocaleString() : '—'}</td>
  </tr>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-title">📋 Trades</div>
    <div style="margin-bottom:12px;display:flex;gap:8px">
      <button class="btn" data-filter="all">All</button>
      <button class="btn" data-filter="open">Open</button>
      <button class="btn" data-filter="closed">Closed</button>
    </div>
    <div class="card" style="overflow-x:auto">
      <table class="data-table" id="trades-table">
        <thead><tr>
          <th>ID</th><th>Symbol</th><th>Dir</th><th>Setup</th>
          <th>Entry</th><th>Lots</th><th>Outcome</th><th>P&L</th><th>Opened</th>
        </tr></thead>
        <tbody id="trades-body"><tr><td colspan="9">Loading...</td></tr></tbody>
      </table>
    </div>
  `;

  async function loadTrades(status = 'all') {
    const tbody = document.getElementById('trades-body');
    try {
      const data = await apiGet(`/api/trades?status=${status}&limit=200`);
      if (data.trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No trades found</td></tr>';
      } else {
        tbody.innerHTML = data.trades.map(renderRow).join('');
      }
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="9" style="color:var(--red)">${err.message}</td></tr>`;
    }
  }

  // Filter buttons
  container.querySelectorAll('[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('btn-primary'));
      btn.classList.add('btn-primary');
      loadTrades(btn.dataset.filter);
    });
  });

  await loadTrades();
}
