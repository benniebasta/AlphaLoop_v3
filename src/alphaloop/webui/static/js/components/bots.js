/**
 * 🤖 Alpha Agents — Live Execution Fleet
 * Renamed from "Bots" — now with WebUI deploy button.
 */
import { apiGet, apiPost, apiDelete } from '../api.js';

const STATUS_COLORS = {
  candidate: 'var(--amber)',
  dry_run: 'var(--blue)',
  demo: 'var(--purple)',
  live: 'var(--green)',
  retired: 'var(--muted)',
};

function uptime(startedAt) {
  if (!startedAt) return '—';
  const ms = Date.now() - new Date(startedAt).getTime();
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

function strategyMiniCard(strat) {
  if (!strat) return '';
  const sum = strat.summary || {};
  const color = STATUS_COLORS[strat.status] || 'var(--muted)';
  const wr = (sum.win_rate || 0);
  const sharpe = (sum.sharpe || 0);
  const pnl = (sum.total_pnl || 0);
  const dd = (sum.max_dd_pct || 0);
  return `
    <div style="border-top:1px solid var(--border);margin-top:10px;padding-top:10px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:11px;color:var(--muted)">${strat.name || strat.symbol + ' v' + strat.version}</span>
        <span class="badge" style="background:${color};color:#000;font-size:10px;padding:2px 6px">${strat.status || '—'}</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;text-align:center">
        <div>
          <div style="font-size:12px;font-weight:600;color:${wr>=0.5?'var(--green)':'var(--red)'}">${(wr*100).toFixed(1)}%</div>
          <div style="font-size:10px;color:var(--muted)">WR</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:${sharpe>=0.5?'var(--green)':'var(--red)'}">${sharpe.toFixed(2)}</div>
          <div style="font-size:10px;color:var(--muted)">Sharpe</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:${pnl>=0?'var(--green)':'var(--red)'}">$${pnl.toFixed(0)}</div>
          <div style="font-size:10px;color:var(--muted)">P&L</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:var(--red)">${dd.toFixed(1)}%</div>
          <div style="font-size:10px;color:var(--muted)">Max DD</div>
        </div>
      </div>
    </div>`;
}

function agentCard(b, stratMap) {
  const verNum = b.strategy_version ? parseInt(String(b.strategy_version).replace(/\D/g, ''), 10) : null;
  const stratKey = verNum != null ? `${b.symbol}_${verNum}` : null;
  const strat = stratKey ? stratMap[stratKey] : null;
  return `
    <div class="bot-card bot-active">
      <div class="bot-card-header">
        <div class="bot-live-dot"></div>
        <div class="bot-symbol">${b.symbol}</div>
        <span class="badge badge-green">🟢 Active</span>
      </div>
      <div class="bot-stats">
        <div class="bot-stat">
          <div class="bot-stat-val">${b.strategy_version || 'v1'}</div>
          <div class="bot-stat-lbl">Version</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-val">${uptime(b.started_at)}</div>
          <div class="bot-stat-lbl">Uptime</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-val mono-sm">${b.pid}</div>
          <div class="bot-stat-lbl">PID</div>
        </div>
      </div>
      ${strategyMiniCard(strat)}
      <div class="bot-id mono-sm" style="font-size:10px;margin-top:8px">${b.instance_id}</div>
      <div class="bot-started">Started ${b.started_at ? new Date(b.started_at).toLocaleString() : '—'}</div>
      <div class="bot-actions">
        <button class="btn btn-danger btn-sm" data-stop="${b.instance_id}" title="Stop agent">⏹ Stop</button>
        <button class="btn btn-danger btn-sm" data-remove="${b.instance_id}" title="Remove record">✕ Remove</button>
      </div>
    </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🤖 Alpha Agents</div>
      <div class="page-subtitle">Live Execution Fleet — deploy and manage trading agents</div>
    </div>
    <div class="deploy-agent-bar">
      <button class="btn-gradient" id="deploy-agent-btn">🚀 Deploy Agent</button>
      <button class="btn btn-sm" id="agents-refresh" style="margin-left:auto">↻ Refresh</button>
    </div>

    <!-- Deploy Modal -->
    <div id="deploy-modal" style="display:none">
      <div class="card" style="margin-bottom:16px;border-color:var(--primary-border)">
        <div style="font-size:14px;font-weight:600;margin-bottom:12px">🚀 Deploy New Agent</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;align-items:end">
          <div class="form-group">
            <label>Symbol</label>
            <select id="deploy-symbol" class="field-input">
              <option value="XAUUSD">XAUUSD — Gold</option>
              <option value="BTCUSD">BTCUSD — Bitcoin</option>
              <option value="EURUSD">EURUSD — Euro/USD</option>
              <option value="GBPUSD">GBPUSD — GBP/USD</option>
              <option value="NAS100">NAS100 — Nasdaq</option>
              <option value="US30">US30 — Dow Jones</option>
            </select>
          </div>
          <div class="form-group">
            <label>Mode</label>
            <select id="deploy-mode" class="field-input">
              <option value="dry">🧪 Dry Run (Safe)</option>
              <option value="live">⚡ Live Trading</option>
            </select>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn-gradient" id="deploy-confirm" style="flex:1">🚀 Launch</button>
            <button class="btn btn-sm" id="deploy-cancel">Cancel</button>
          </div>
        </div>
      </div>
    </div>

    <div id="agents-content"><div class="dash-loading">Loading…</div></div>`;

  // Deploy modal toggle
  const deployBtn = document.getElementById('deploy-agent-btn');
  const deployModal = document.getElementById('deploy-modal');
  deployBtn.addEventListener('click', () => {
    deployModal.style.display = deployModal.style.display === 'none' ? 'block' : 'none';
  });
  document.getElementById('deploy-cancel').addEventListener('click', () => {
    deployModal.style.display = 'none';
  });

  // Deploy confirm
  document.getElementById('deploy-confirm').addEventListener('click', async () => {
    const symbol = document.getElementById('deploy-symbol').value;
    const dryRun = document.getElementById('deploy-mode').value === 'dry';
    try {
      const res = await apiPost('/api/bots/start', { symbol, dry_run: dryRun });
      window.showToast(`Agent deployed: ${symbol} (${dryRun ? 'Dry Run' : 'Live'})`);
      deployModal.style.display = 'none';
      setTimeout(load, 2000); // Wait for process to register
    } catch (err) {
      window.showToast(err.message, 'error');
    }
  });

  async function load() {
    const el = document.getElementById('agents-content');
    if (!el) return;
    try {
      const [data, stratData] = await Promise.all([
        apiGet('/api/bots'),
        apiGet('/api/strategies').catch(() => ({ strategies: [] })),
      ]);
      const stratMap = {};
      for (const s of (stratData.strategies || [])) {
        stratMap[`${s.symbol}_${s.version}`] = s;
      }
      if (!data.bots || data.bots.length === 0) {
        el.innerHTML = `
          <div class="empty-state">
            <div class="icon">🤖</div>
            <div class="empty-title">No agents deployed</div>
            <div class="empty-desc">Click "Deploy Agent" above to launch your first trading agent,<br>
              or start from CLI: <code style="background:var(--bg3);padding:2px 8px;border-radius:4px">python -m alphaloop.main --symbol XAUUSD</code>
            </div>
          </div>`;
        return;
      }
      el.innerHTML = `<div class="bot-grid">${data.bots.map(b => agentCard(b, stratMap)).join('')}</div>`;

      // Stop buttons
      el.querySelectorAll('[data-stop]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm(`Stop agent ${btn.dataset.stop}?`)) return;
          try {
            await apiPost(`/api/bots/${btn.dataset.stop}/stop`, {});
            window.showToast('Agent stop signal sent');
            setTimeout(load, 2000);
          } catch (err) {
            window.showToast(err.message, 'error');
          }
        });
      });

      // Remove buttons
      el.querySelectorAll('[data-remove]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm(`Remove agent record ${btn.dataset.remove}?`)) return;
          try {
            await apiDelete(`/api/bots/${btn.dataset.remove}`);
            window.showToast('Agent record removed');
            load();
          } catch (err) {
            window.showToast(err.message, 'error');
          }
        });
      });
    } catch (err) {
      el.innerHTML = `<div class="page-error">⚠️ ${err.message}</div>`;
    }
  }

  document.getElementById('agents-refresh').addEventListener('click', load);
  await load();

  const timer = setInterval(load, 30000);
  window.addEventListener('route-change', () => clearInterval(timer), { once: true });
}
