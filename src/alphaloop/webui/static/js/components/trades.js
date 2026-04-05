/**
 * Trades tab — trade list with expandable P&L attribution detail.
 *
 * v3.1 additions:
 *  - Expandable row → shows P&L attribution panel (entry skill, exit skill, slippage, commission)
 *  - TCA panel at top with execution quality score progress bar
 */
import { apiGet, apiPost } from '../api.js';

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

function skillCell(val, isR = false) {
  if (val == null) return '<span style="color:var(--muted)">—</span>';
  const n = parseFloat(val);
  const color = n > 0 ? 'var(--green)' : n < 0 ? 'var(--red)' : 'var(--muted)';
  if (isR) return `<span style="color:${color}">${n.toFixed(2)}×</span>`;
  return `<span style="color:${color}">${n >= 0 ? '+' : ''}$${Math.abs(n).toFixed(2)}</span>`;
}

function costCell(val) {
  if (val == null) return '<span style="color:var(--muted)">—</span>';
  const n = parseFloat(val);
  // costs are always negative; show as -$X.XX in red
  return `<span style="color:var(--red)">-$${Math.abs(n).toFixed(2)}</span>`;
}

function attributionPanel(t) {
  const hasAny = t.pnl_entry_skill != null || t.pnl_exit_skill != null
    || t.pnl_slippage_usd != null || t.pnl_commission_usd != null;
  if (!hasAny) {
    return `<div style="font-size:0.78rem;color:var(--muted);padding:8px 0">
      No attribution data — requires entry zone, TP1, and slippage data.
    </div>`;
  }
  return `
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:8px 0">
      <div class="attr-block">
        <div class="attr-label">Entry Skill</div>
        <div class="attr-value">${skillCell(t.pnl_entry_skill)}</div>
        <div class="attr-desc" style="font-size:0.65rem;color:var(--muted)">vs zone midpoint</div>
      </div>
      <div class="attr-block">
        <div class="attr-label">Exit Skill</div>
        <div class="attr-value">${skillCell(t.pnl_exit_skill, true)}</div>
        <div class="attr-desc" style="font-size:0.65rem;color:var(--muted)">${t.pnl_exit_skill != null ? (t.pnl_exit_skill > 1 ? '✓ exceeded TP1' : t.pnl_exit_skill < 0 ? '✗ closed at loss' : 'partial TP1') : ''}</div>
      </div>
      <div class="attr-block">
        <div class="attr-label">Slippage Cost</div>
        <div class="attr-value">${costCell(t.pnl_slippage_usd)}</div>
        <div class="attr-desc" style="font-size:0.65rem;color:var(--muted)">execution slippage</div>
      </div>
      <div class="attr-block">
        <div class="attr-label">Spread Cost</div>
        <div class="attr-value">${costCell(t.pnl_commission_usd)}</div>
        <div class="attr-desc" style="font-size:0.65rem;color:var(--muted)">bid-ask spread</div>
      </div>
    </div>`;
}

function renderRow(t) {
  const rowId = `trade-detail-${t.id}`;
  return `
    <tr class="trade-row" data-id="${t.id}" style="cursor:pointer" title="Click to expand attribution">
      <td>${t.id}</td>
      <td>${t.symbol || '—'}</td>
      <td>${t.direction || '—'}</td>
      <td>${t.setup_type || '—'}</td>
      <td>${t.entry_price?.toFixed(2) ?? '—'}</td>
      <td>${t.lot_size?.toFixed(2) ?? '—'}</td>
      <td>${outcomeBadge(t.outcome)}</td>
      <td>${pnlCell(t.pnl_usd)}</td>
      <td>${t.opened_at ? new Date(t.opened_at).toLocaleString() : '—'}</td>
      <td style="font-size:0.7rem;color:var(--muted)">▶</td>
    </tr>
    <tr id="${rowId}" style="display:none">
      <td colspan="10" style="background:var(--surface-raised,rgba(255,255,255,0.03));padding:8px 16px">
        <div style="font-size:0.78rem;color:var(--muted);margin-bottom:4px">
          ⚡ P&L Attribution breakdown for trade #${t.id}
        </div>
        ${attributionPanel(t)}
        ${t.claude_reasoning ? `<div style="margin-top:6px;font-size:0.72rem;color:var(--muted);font-style:italic">"${t.claude_reasoning}"</div>` : ''}
      </td>
    </tr>`;
}

function tcaPanel(tca) {
  if (!tca || tca.trade_count === 0) return '';
  const score = parseFloat(tca.execution_quality_score ?? 0);
  const scoreColor = score >= 80 ? 'var(--green)' : score >= 60 ? 'var(--amber, #f59e0b)' : 'var(--red)';
  const label = tca.score_label || '';
  return `
    <div class="card" style="margin-bottom:16px">
      <div class="section-label" style="margin:0 0 8px 0">Execution Quality (TCA)</div>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div style="min-width:200px;flex:1">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="font-size:0.78rem">Quality Score</span>
            <span style="font-size:0.78rem;color:${scoreColor};font-weight:600">${score.toFixed(0)}/100 — ${label}</span>
          </div>
          <div class="risk-bar-track">
            <div class="risk-bar-fill" style="width:${score}%;background:${scoreColor}"></div>
          </div>
        </div>
        <div style="font-size:0.75rem;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap">
          <span>Avg slippage: <strong>${(tca.avg_slippage_points ?? 0).toFixed(3)} pts</strong></span>
          <span>Max slippage: <strong>${(tca.max_slippage_points ?? 0).toFixed(3)} pts</strong></span>
          <span>Avg spread cost: <strong>$${(tca.avg_spread_cost_usd ?? 0).toFixed(2)}</strong></span>
          <span>Total spread cost: <strong>$${(tca.total_spread_cost_usd ?? 0).toFixed(2)}</strong></span>
          <span>Analyzed: <strong>${tca.trade_count} trades</strong></span>
        </div>
      </div>
    </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-title">📋 Trades</div>
    <div id="tca-panel"></div>
    <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
      <button class="btn" data-filter="all">All</button>
      <button class="btn" data-filter="open">Open</button>
      <button class="btn" data-filter="closed">Closed</button>
      <button class="btn btn-sm" id="backfill-btn" style="margin-left:auto;font-size:0.72rem">⚡ Backfill Attribution</button>
      <span id="backfill-hint" style="font-size:0.72rem;color:var(--muted)"></span>
    </div>
    <div class="card" style="overflow-x:auto">
      <table class="data-table" id="trades-table">
        <thead><tr>
          <th>ID</th><th>Symbol</th><th>Dir</th><th>Setup</th>
          <th>Entry</th><th>Lots</th><th>Outcome</th><th>P&L</th><th>Opened</th><th></th>
        </tr></thead>
        <tbody id="trades-body"><tr><td colspan="10">Loading...</td></tr></tbody>
      </table>
    </div>
  `;

  // Load TCA panel
  apiGet('/api/execution/tca').then(tca => {
    const el = document.getElementById('tca-panel');
    if (el) el.innerHTML = tcaPanel(tca);
  }).catch(() => {});

  async function loadTrades(status = 'all') {
    const tbody = document.getElementById('trades-body');
    try {
      const data = await apiGet(`/api/trades?status=${status}&limit=200`);
      if (data.trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No trades found</td></tr>';
      } else {
        tbody.innerHTML = data.trades.map(renderRow).join('');

        // Wire expand/collapse on trade rows
        document.querySelectorAll('.trade-row').forEach(row => {
          row.addEventListener('click', () => {
            const id = row.dataset.id;
            const detail = document.getElementById(`trade-detail-${id}`);
            if (detail) {
              const isOpen = detail.style.display !== 'none';
              detail.style.display = isOpen ? 'none' : 'table-row';
              const arrow = row.querySelector('td:last-child');
              if (arrow) arrow.textContent = isOpen ? '▶' : '▼';
            }
          });
        });
      }
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="10" style="color:var(--red)">${err.message}</td></tr>`;
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

  // Backfill attribution button
  const backfillBtn = document.getElementById('backfill-btn');
  if (backfillBtn) {
    backfillBtn.addEventListener('click', async () => {
      backfillBtn.disabled = true;
      backfillBtn.textContent = '⏳ Running...';
      const hint = document.getElementById('backfill-hint');
      try {
        const res = await apiPost('/api/execution/attribution/backfill', { limit: 500 });
        if (hint) hint.textContent = `✓ Updated ${res.updated} trades`;
        await loadTrades(); // refresh table
      } catch (e) {
        if (hint) hint.textContent = `✗ ${e.message}`;
      }
      backfillBtn.disabled = false;
      backfillBtn.textContent = '⚡ Backfill Attribution';
    });
  }

  await loadTrades();
}
