/**
 * Health Status — system component health overview with auto-refresh.
 */
import { apiGet } from '../api.js';

const STATUS_COLORS = {
  healthy:   'var(--green)',
  degraded:  'var(--amber)',
  unhealthy: 'var(--red)',
  unknown:   'var(--muted)',
};

const STATUS_ICONS = {
  healthy:   '\u2705',
  degraded:  '\u26A0\uFE0F',
  unhealthy: '\u274C',
  unknown:   '\u2754',
};

function statusColor(status) {
  return STATUS_COLORS[status] || STATUS_COLORS.unknown;
}

function statusIcon(status) {
  return STATUS_ICONS[status] || STATUS_ICONS.unknown;
}

function relativeTime(epoch) {
  if (!epoch) return 'never';
  const diff = Math.floor(Date.now() / 1000 - epoch);
  if (diff < 0) return 'just now';
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function statusBadge(status, large) {
  const color = statusColor(status);
  const size = large ? 'font-size:1.1rem;padding:6px 18px' : 'font-size:0.8rem;padding:3px 10px';
  return `<span style="display:inline-block;background:${color}22;color:${color};
    border:1px solid ${color}44;border-radius:999px;${size};font-weight:600;
    text-transform:uppercase;letter-spacing:0.5px">${status}</span>`;
}

function componentCard(name, comp) {
  const color = statusColor(comp.status);
  return `
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:12px;
      padding:20px;display:flex;flex-direction:column;gap:10px;
      transition:border-color 0.2s;cursor:default"
      onmouseenter="this.style.borderColor='${color}44'"
      onmouseleave="this.style.borderColor='var(--border)'">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <span style="font-weight:600;font-size:0.95rem;color:var(--text)">${name}</span>
        ${statusBadge(comp.status, false)}
      </div>
      ${comp.details ? `<div style="color:var(--muted);font-size:0.82rem;line-height:1.4">${comp.details}</div>` : ''}
      <div style="color:var(--muted);font-size:0.75rem;margin-top:auto">
        Last check: ${relativeTime(comp.last_check)}
      </div>
    </div>`;
}

function watchdogSection(wd) {
  if (!wd || typeof wd !== 'object') return '';
  const entries = Object.entries(wd);
  if (entries.length === 0) return '';

  const rows = entries.map(([k, v]) => {
    const display = typeof v === 'object' ? JSON.stringify(v) : String(v);
    return `<tr>
      <td style="padding:6px 14px 6px 0;color:var(--muted);white-space:nowrap">${k}</td>
      <td style="padding:6px 0;color:var(--text);word-break:break-all">${display}</td>
    </tr>`;
  }).join('');

  return `
    <div style="margin-top:28px">
      <div style="font-size:1rem;font-weight:600;color:var(--text);margin-bottom:14px">
        Watchdog
      </div>
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:12px;
        padding:16px 20px;overflow-x:auto">
        <table style="font-size:0.85rem;border-collapse:collapse;width:100%">
          ${rows}
        </table>
      </div>
    </div>`;
}

function renderHealth(data) {
  const status = data.status || 'unknown';
  const color = statusColor(status);
  const components = data.components || {};
  const names = Object.keys(components);

  const cards = names.length > 0
    ? names.map(n => componentCard(n, components[n])).join('')
    : '<div style="color:var(--muted);padding:20px">No components reported.</div>';

  return `
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:8px">
      <span style="display:inline-flex;align-items:center;gap:10px;font-size:1.5rem;font-weight:700">
        <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
          background:${color};box-shadow:0 0 8px ${color}88"></span>
        System ${status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
      ${statusBadge(status, true)}
      ${data.version ? `<span style="color:var(--muted);font-size:0.85rem;margin-left:auto">v${data.version}</span>` : ''}
    </div>

    <div style="margin-top:24px">
      <div style="font-size:1rem;font-weight:600;color:var(--text);margin-bottom:14px">
        Components <span style="color:var(--muted);font-weight:400;font-size:0.85rem">(${names.length})</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px">
        ${cards}
      </div>
    </div>

    ${watchdogSection(data.watchdog)}

    <div style="margin-top:20px;color:var(--muted);font-size:0.75rem;text-align:right">
      Last refreshed: ${new Date().toLocaleTimeString()}
    </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">Health Status</div>
      <div class="page-subtitle">System component health overview (auto-refreshes every 15s)</div>
    </div>
    <div id="health-content"><div class="dash-loading">Loading...</div></div>`;

  async function load() {
    try {
      const data = await apiGet('/health/detailed');
      const el = document.getElementById('health-content');
      if (el) el.innerHTML = renderHealth(data);
    } catch (err) {
      const el = document.getElementById('health-content');
      if (el) el.innerHTML = `<div class="page-error">${err.message}</div>`;
    }
  }

  await load();

  const timer = setInterval(load, 15000);

  const cleanup = () => { clearInterval(timer); };
  window.addEventListener('route-change', cleanup, { once: true });
}
