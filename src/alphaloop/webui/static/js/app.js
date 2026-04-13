/**
 * AlphaLoop™ — SPA Router, Theme Manager & WebSocket Manager
 */
import { getAuthToken, setAuthToken } from './api.js';

// Component registry — lazy-loaded on first navigate
const components = {};

const ROUTES = [
  'dashboard', 'live', 'trades', 'agents', 'strategies',
  'seedlab', 'research', 'risk_dashboard', 'event_log', 'health',
  'ai_hub', 'settings', 'asset_params',
];

// Backward-compatible aliases (old route → new route)
const ALIASES = {
  bots: 'agents',
  backtests: 'seedlab',
  ai_signal_discovery: 'strategies',
  'ai-signal-discovery': 'strategies',
};

// Cache-bust version — increment when deploying new JS
const _V = '8.6';

/**
 * Load a component module on demand.
 */
async function loadComponent(name) {
  if (components[name]) return components[name];
  const mod = await import(`./components/${name}.js?v=${_V}`);
  components[name] = mod;
  return mod;
}

/**
 * Navigate to a page.
 */
async function navigateTo(page) {
  // Resolve aliases
  if (ALIASES[page]) page = ALIASES[page];
  if (!ROUTES.includes(page)) page = 'dashboard';
  window.location.hash = page;

  // Notify current page to clean up timers/polls before replacing DOM
  window.dispatchEvent(new CustomEvent('route-change', { detail: { page } }));

  // Update sidebar
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });

  const container = document.getElementById('page-content');
  container.innerHTML = '<div class="page-title">Loading...</div>';

  try {
    const mod = await loadComponent(page);
    container.innerHTML = '';
    await mod.render(container);
  } catch (err) {
    container.innerHTML = `
      <div class="page-title">Error</div>
      <div class="card"><p style="color:var(--red)">${err.message}</p></div>
    `;
    console.error('Page load error:', err);
  }
}

/**
 * Set up hash-based routing.
 */
function initRouter() {
  window.addEventListener('hashchange', () => {
    let page = window.location.hash.slice(1) || 'dashboard';
    if (ALIASES[page]) page = ALIASES[page];
    navigateTo(page);
  });

  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      navigateTo(el.dataset.page);
    });
  });

  let initial = window.location.hash.slice(1) || 'dashboard';
  if (ALIASES[initial]) initial = ALIASES[initial];
  navigateTo(initial);
}

/* ═══════════════════════════════════════════════════════════════════════════
   THEME MANAGER — Dark / Light Mode
   ═══════════════════════════════════════════════════════════════════════════ */

function initTheme() {
  const saved = localStorage.getItem('alphaloop-theme') || 'dark';
  applyTheme(saved);

  const toggleBtn = document.getElementById('theme-toggle');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      localStorage.setItem('alphaloop-theme', next);
    });
  }
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const icon = document.getElementById('theme-icon');
  const label = document.getElementById('theme-label');
  if (icon) icon.textContent = theme === 'dark' ? '🌙' : '☀️';
  if (label) label.textContent = theme === 'dark' ? 'Dark Mode' : 'Light Mode';
}

/* ═══════════════════════════════════════════════════════════════════════════
   SIDEBAR TOGGLE — Mobile hamburger menu
   ═══════════════════════════════════════════════════════════════════════════ */

function initSidebar() {
  const btn     = document.getElementById('hamburger-btn');
  const overlay = document.getElementById('sidebar-overlay');
  if (!btn || !overlay) return;

  const open   = () => { document.body.classList.add('sidebar-open');    btn.setAttribute('aria-expanded', 'true'); };
  const close  = () => { document.body.classList.remove('sidebar-open'); btn.setAttribute('aria-expanded', 'false'); };
  const toggle = () => document.body.classList.contains('sidebar-open') ? close() : open();

  btn.addEventListener('click', toggle);
  overlay.addEventListener('click', close);

  document.querySelectorAll('.nav-item').forEach(el =>
    el.addEventListener('click', () => {
      if (window.getComputedStyle(btn).display !== 'none') close();
    })
  );

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
      close();
      btn.focus();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 768) close();
  });
}

/* ═══════════════════════════════════════════════════════════════════════════
   WEBSOCKET — Auto-reconnect with exponential backoff
   ═══════════════════════════════════════════════════════════════════════════ */

let ws = null;
let wsRetryDelay = 1000;
let wsPingInterval = null;

function connectWebSocket() {
  if (wsPingInterval) {
    clearInterval(wsPingInterval);
    wsPingInterval = null;
  }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = getAuthToken();
  const url = `${proto}//${location.host}/ws`;
  // Pass auth token as WebSocket subprotocol (not in URL — invisible to server logs)
  ws = token ? new WebSocket(url, [token]) : new WebSocket(url);

  const statusEl = document.getElementById('ws-status');
  const updateEl = document.getElementById('last-update');

  ws.onopen = () => {
    wsRetryDelay = 1000;
    statusEl.textContent = 'WS: connected';
    statusEl.className = 'ws-connected';
  };

  ws.onclose = () => {
    statusEl.textContent = 'WS: disconnected';
    statusEl.className = 'ws-disconnected';
    setTimeout(connectWebSocket, wsRetryDelay);
    wsRetryDelay = Math.min(wsRetryDelay * 2, 30000);
  };

  ws.onerror = () => {
    ws.close();
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updateEl.textContent = `Last event: ${data.type || '?'} @ ${new Date().toLocaleTimeString()}`;
      // Dispatch custom event for components to listen to
      window.dispatchEvent(new CustomEvent('ws-event', { detail: data }));
    } catch (e) {
      // ignore parse errors
    }
  };

  // Ping every 30s to keep connection alive
  wsPingInterval = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send('ping');
    }
  }, 30000);
}

/* ═══════════════════════════════════════════════════════════════════════════
   WEBSOCKET EVENT HANDLERS — Central dispatcher for all 13 event types
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Handle all WebSocket events centrally. Shows toasts for important events
 * and dispatches targeted refresh signals to active pages.
 */
function initEventHandlers() {
  window.addEventListener('ws-event', (e) => {
    const data = e.detail;
    if (!data || !data.type) return;

    switch (data.type) {
      case 'SignalGenerated':
        showToast(`📡 Signal: ${data.direction} ${data.symbol} (${Math.round((data.confidence || 0) * 100)}%)`, 'info');
        break;

      case 'SignalValidated':
        if (data.approved === false) {
          showToast(`⚠ Signal rejected for ${data.symbol}: risk_score=${data.risk_score}`, 'warning');
        }
        break;

      case 'SignalRejected':
        showToast(`🚫 ${data.symbol} rejected by ${data.rejected_by}: ${data.reason}`, 'warning');
        break;

      case 'TradeOpened':
        showToast(`🟢 Trade opened: ${data.direction} ${data.symbol} @ ${data.entry_price}`, 'success');
        break;

      case 'TradeClosed': {
        const pnl = data.pnl_usd || 0;
        const icon = pnl >= 0 ? '💰' : '📉';
        const type = pnl >= 0 ? 'success' : 'error';
        showToast(`${icon} Trade closed: ${data.symbol} ${data.outcome} $${pnl.toFixed(2)}`, type);
        break;
      }

      case 'PipelineBlocked':
        showToast(`🔧 Pipeline blocked ${data.symbol}: ${data.blocked_by}`, 'warning');
        break;

      case 'RiskLimitHit':
        showToast(`🛑 Risk limit: ${data.limit_type} — ${data.details}`, 'error');
        break;

      case 'ResearchCompleted':
        showToast(`📊 Research report ready for ${data.symbol}`, 'info');
        break;

      case 'ConfigChanged':
        showToast(`⚙ Config updated: ${(data.keys || []).join(', ')}`, 'info');
        break;

      case 'StrategyPromoted':
        showToast(`🚀 ${data.symbol} promoted: ${data.from_status} → ${data.to_status}`, 'success');
        break;

      case 'SeedLabProgress':
        // Don't toast every progress tick — too noisy. Only toast phase changes.
        if (data.current === 1 || data.current === data.total) {
          showToast(`🧬 SeedLab ${data.phase}: ${data.message || ''}`, 'info');
        }
        break;

      case 'CanaryStarted':
        showToast(`🐤 Canary started: ${data.symbol} @ ${data.allocation_pct}%`, 'info');
        break;

      case 'CanaryEnded':
        showToast(`🐤 Canary ended: ${data.symbol} — ${data.recommendation}`, data.recommendation === 'promote' ? 'success' : 'warning');
        break;
    }
  });
}

/* ═══════════════════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
   ═══════════════════════════════════════════════════════════════════════════ */

window.showToast = function(message, type = 'success') {
  let toast = document.querySelector('.toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = `toast ${type} show`;
  setTimeout(() => { toast.classList.remove('show'); }, 3000);
};

/* ═══════════════════════════════════════════════════════════════════════════
   BOOT
   ═══════════════════════════════════════════════════════════════════════════ */

initTheme();
initSidebar();

// ── Auth bootstrap — prompt for token if server requires it ─────────────
(async () => {
  try {
    const res = await fetch('/api/auth/status');
    const { required } = await res.json();
    if (required && !getAuthToken()) {
      const token = prompt('AUTH_TOKEN required. Paste your token:');
      if (token) setAuthToken(token.trim());
    }
  } catch { /* server unreachable — proceed without auth */ }
})();

initRouter();
connectWebSocket();
initEventHandlers();
