/**
 * Test Flow — Run signal mode integration tests and stream live output.
 *
 * Three modes: algo_only | algo_ai | ai_signal
 * Backend: POST /api/test-flow/run?mode=X  +  GET /api/test-flow/status
 */
import { apiGet, apiPost } from '../api.js';

const MODES = [
  { id: 'algo_only', label: 'Algo Only',  icon: '⚙️',  desc: 'Deterministic signal — no AI' },
  { id: 'algo_ai',   label: 'Algo + AI',  icon: '🤖',  desc: 'Algo hypothesis + AI refinement' },
  { id: 'ai_signal', label: 'AI Signal',  icon: '🧠',  desc: 'Fully AI-generated signal' },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function statusColor(state) {
  if (state.running)                       return 'var(--amber)';
  if (state.exit_code === null)            return 'var(--muted)';
  if (state.exit_code === 0)               return 'var(--green)';
  return 'var(--red)';
}

function statusLabel(state) {
  if (state.running)        return 'RUNNING';
  if (state.exit_code === null) return 'IDLE';
  if (state.exit_code === 0)    return 'PASSED';
  return 'FAILED';
}

function badge(text, color) {
  return `<span style="display:inline-block;background:${color}22;color:${color};
    border:1px solid ${color}44;border-radius:999px;padding:2px 10px;
    font-size:0.75rem;font-weight:700;letter-spacing:0.5px;text-transform:uppercase"
  >${text}</span>`;
}

function elapsedStr(sec) {
  if (sec === null) return '';
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

// Colorize a log line based on content
function colorLine(line) {
  if (!line.trim()) return '<br>';
  if (/PASSED/.test(line))  return `<span style="color:var(--green)">${esc(line)}</span>`;
  if (/FAILED|ERROR/.test(line)) return `<span style="color:var(--red)">${esc(line)}</span>`;
  if (/WARNINGS|warning/.test(line)) return `<span style="color:var(--amber)">${esc(line)}</span>`;
  if (/^»/.test(line))      return `<span style="color:var(--primary)">${esc(line)}</span>`;
  if (/^=+/.test(line))     return `<span style="color:var(--muted)">${esc(line)}</span>`;
  if (/^PASSED|^passed/.test(line)) return `<span style="color:var(--green)">${esc(line)}</span>`;
  return `<span style="color:var(--code-fg)">${esc(line)}</span>`;
}

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Render ───────────────────────────────────────────────────────────────────

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🧪 Test Flow</div>
      <div class="page-subtitle">Run signal-mode integration tests and verify DB writes end-to-end</div>
    </div>

    <!-- Mode selector -->
    <div id="tf-modes" style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px"></div>

    <!-- Status bar -->
    <div id="tf-status-bar" style="
      background:var(--bg2);border:1px solid var(--border);border-radius:12px;
      padding:16px 20px;display:flex;align-items:center;gap:20px;flex-wrap:wrap;
      margin-bottom:20px">
      <span id="tf-status-dot" style="display:inline-block;width:10px;height:10px;
        border-radius:50%;background:var(--muted)"></span>
      <span id="tf-status-label" style="font-weight:700;font-size:0.9rem;
        color:var(--muted);text-transform:uppercase;letter-spacing:0.5px">IDLE</span>
      <span id="tf-mode-label" style="color:var(--muted);font-size:0.85rem"></span>
      <span id="tf-counts" style="color:var(--muted);font-size:0.85rem;margin-left:auto"></span>
      <span id="tf-elapsed" style="color:var(--muted);font-size:0.82rem"></span>
    </div>

    <!-- Log terminal -->
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden">
      <div style="
        display:flex;align-items:center;justify-content:space-between;
        padding:10px 16px;border-bottom:1px solid var(--border);
        background:var(--bg3)">
        <span style="font-size:0.8rem;font-weight:600;color:var(--muted);
          letter-spacing:0.5px;text-transform:uppercase">Output Log</span>
        <button id="tf-clear-btn" style="
          background:none;border:1px solid var(--border);border-radius:6px;
          color:var(--muted);font-size:0.75rem;padding:3px 10px;cursor:pointer">
          Clear
        </button>
      </div>
      <div id="tf-log" style="
        font-family:'Courier New',Courier,monospace;font-size:0.8rem;
        line-height:1.6;padding:16px 18px;max-height:480px;overflow-y:auto;
        background:var(--code-bg);color:var(--code-fg);white-space:pre-wrap;
        word-break:break-word">
        <span style="color:var(--muted)">Select a mode above and click Run to start.</span>
      </div>
    </div>`;

  // ── Render mode buttons ───────────────────────────────────────────────────
  const modesEl = document.getElementById('tf-modes');
  MODES.forEach(m => {
    const btn = document.createElement('div');
    btn.id = `tf-btn-${m.id}`;
    btn.style.cssText = `
      background:var(--bg2);border:1px solid var(--border);border-radius:12px;
      padding:18px 22px;cursor:pointer;flex:1;min-width:180px;max-width:260px;
      transition:border-color 0.2s,background 0.2s;user-select:none`;
    btn.innerHTML = `
      <div style="font-size:1.4rem;margin-bottom:6px">${m.icon}</div>
      <div style="font-weight:700;font-size:0.95rem;color:var(--text);margin-bottom:4px">${m.label}</div>
      <div style="font-size:0.8rem;color:var(--muted);margin-bottom:14px">${m.desc}</div>
      <button data-mode="${m.id}" style="
        background:var(--primary);color:#000;border:none;border-radius:8px;
        padding:7px 18px;font-size:0.82rem;font-weight:700;cursor:pointer;
        width:100%;transition:background 0.15s">
        ▶ Run
      </button>`;
    btn.addEventListener('mouseenter', () => {
      btn.style.borderColor = 'var(--primary-border)';
      btn.style.background = 'var(--primary-dim)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.borderColor = 'var(--border)';
      btn.style.background = 'var(--bg2)';
    });
    btn.querySelector('button').addEventListener('click', (e) => {
      e.stopPropagation();
      startRun(m.id);
    });
    modesEl.appendChild(btn);
  });

  document.getElementById('tf-clear-btn').addEventListener('click', () => {
    const log = document.getElementById('tf-log');
    if (log) log.innerHTML = '<span style="color:var(--muted)">Log cleared.</span>';
  });

  // ── Poll logic ────────────────────────────────────────────────────────────
  let pollTimer = null;
  let lastLineCount = 0;
  let isRunning = false;

  async function poll() {
    try {
      const s = await apiGet('/api/test-flow/status');
      updateStatusBar(s);
      updateLog(s.lines || []);
      isRunning = s.running;
    } catch (_) { /* ignore transient errors */ }
  }

  function startPolling(fast) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, fast ? 1500 : 5000);
  }

  async function startRun(mode) {
    try {
      setButtonsDisabled(true);
      await apiPost(`/api/test-flow/run?mode=${mode}`);
      lastLineCount = 0;
      const log = document.getElementById('tf-log');
      if (log) log.innerHTML = '';
      startPolling(true);   // fast poll while running
      await poll();
    } catch (err) {
      showToast(`Test Flow error: ${err.message}`, 'error');
      setButtonsDisabled(false);
    }
  }

  function updateStatusBar(s) {
    const color = statusColor(s);
    const label = statusLabel(s);

    const dot = document.getElementById('tf-status-dot');
    const lbl = document.getElementById('tf-status-label');
    const ml  = document.getElementById('tf-mode-label');
    const cnt = document.getElementById('tf-counts');
    const ela = document.getElementById('tf-elapsed');

    if (dot) { dot.style.background = color; dot.style.boxShadow = s.running ? `0 0 6px ${color}` : 'none'; }
    if (lbl) { lbl.textContent = label; lbl.style.color = color; }
    if (ml && s.mode) ml.textContent = `mode: ${s.mode}`;
    if (cnt && s.mode) {
      cnt.innerHTML = `${badge(s.passed + ' passed', 'var(--green)')} &nbsp; ${badge(s.failed + ' failed', 'var(--red)')}`;
    }
    if (ela) ela.textContent = elapsedStr(s.elapsed);

    // If run just finished, slow-poll and re-enable buttons
    if (!s.running && isRunning) {
      startPolling(false);
      setButtonsDisabled(false);
    }
  }

  function updateLog(lines) {
    if (lines.length === lastLineCount) return;
    lastLineCount = lines.length;
    const log = document.getElementById('tf-log');
    if (!log) return;
    log.innerHTML = lines.map(colorLine).join('\n');
    log.scrollTop = log.scrollHeight;
  }

  function setButtonsDisabled(disabled) {
    document.querySelectorAll('[data-mode]').forEach(btn => {
      btn.disabled = disabled;
      btn.style.opacity = disabled ? '0.45' : '1';
      btn.style.cursor = disabled ? 'not-allowed' : 'pointer';
    });
  }

  // Start slow background poll to pick up any in-progress run on page load
  await poll();
  startPolling(isRunning ? 1500 : 5000);

  // Cleanup on route-change
  window.addEventListener('route-change', () => {
    if (pollTimer) clearInterval(pollTimer);
  }, { once: true });
}
