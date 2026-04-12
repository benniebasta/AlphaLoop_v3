/**
 * 🔍 Observability — Pipeline funnel, decision panel, stage heatmap, mode compare.
 *
 * Gate-1 read-only view of every blocked trade. All data comes from
 * /api/pipeline/* (funnel, decisions/latest, stages/heatmap, modes/compare)
 * and /api/controls/guards-status. No UI action on this page changes system
 * behaviour — it only surfaces what the backend already decided.
 */
import { apiGet } from '../api.js';

const STAGE_ORDER = [
  'market_gate', 'regime', 'signal', 'construction', 'setup_policy',
  'invalidation', 'quality', 'conviction', 'ai_validator', 'risk_gate',
  'execution_guard', 'freshness', 'sizing', 'shadow_mode',
];

function stagePos(name) {
  const i = STAGE_ORDER.indexOf(name);
  return i === -1 ? STAGE_ORDER.length : i;
}

function cssVar(name) { return getComputedStyle(document.body).getPropertyValue(name).trim(); }

function escape(value) {
  if (value == null) return '';
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function badge(label, color) {
  return `<span class="obs-badge" style="background:${color}22;color:${color};border:1px solid ${color}55">${escape(label)}</span>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Funnel
   ═══════════════════════════════════════════════════════════════════════════ */

function renderFunnel(data) {
  if (!data || !data.stages || !data.stages.length) {
    return `<div class="card"><p>No pipeline data yet. Run a bot or <code>python -m scripts.replay_pipeline --source backfill</code>.</p></div>`;
  }
  const maxTotal = Math.max(...data.stages.map(s => s.total || 0), 1);
  const rowsHtml = data.stages.map(s => {
    const total = s.total || 0;
    const passed = s.passed || 0;
    const blocked = s.blocked || 0;
    const held = s.held || 0;
    const other = s.other || 0;
    const passPct = total ? ((passed / total) * 100).toFixed(1) : '0.0';
    const blockPct = total ? ((blocked / total) * 100).toFixed(1) : '0.0';
    const heldPct = total ? ((held / total) * 100).toFixed(1) : '0.0';
    const width = Math.max(3, (total / maxTotal) * 100);
    const reasons = (s.top_reasons || [])
      .map(r => `<span class="obs-reason-chip">${escape(r.reason)} (${r.count})</span>`)
      .join(' ');
    return `
      <div class="obs-funnel-row">
        <div class="obs-funnel-stage">${escape(s.stage)}</div>
        <div class="obs-funnel-bar" style="width:${width}%">
          <span class="obs-seg obs-seg-passed" style="flex:${passed}" title="passed: ${passed}"></span>
          <span class="obs-seg obs-seg-blocked" style="flex:${blocked}" title="blocked: ${blocked}"></span>
          <span class="obs-seg obs-seg-held" style="flex:${held}" title="held: ${held}"></span>
          <span class="obs-seg obs-seg-other" style="flex:${other}" title="other: ${other}"></span>
        </div>
        <div class="obs-funnel-nums">
          <span title="total">${total}</span>
          <span class="obs-funnel-pct" style="color:var(--green)">${passPct}%</span>
          <span class="obs-funnel-pct" style="color:var(--red)">${blockPct}%</span>
          <span class="obs-funnel-pct" style="color:var(--amber)">${heldPct}%</span>
        </div>
        ${reasons ? `<div class="obs-funnel-reasons">${reasons}</div>` : ''}
      </div>`;
  }).join('');

  return `
    <div class="card">
      <div class="obs-funnel-header">
        <h3>Pipeline funnel</h3>
        <div class="obs-funnel-meta">
          <span>Total cycles: <b>${data.total_cycles}</b></span>
          <span>Executed: <b style="color:var(--green)">${data.executed_cycles}</b></span>
          <span>Window: ${new Date(data.window_start).toLocaleString()} → now</span>
        </div>
      </div>
      <div class="obs-funnel-legend">
        <span><span class="obs-seg obs-seg-passed"></span> passed</span>
        <span><span class="obs-seg obs-seg-blocked"></span> blocked</span>
        <span><span class="obs-seg obs-seg-held"></span> held</span>
        <span><span class="obs-seg obs-seg-other"></span> other</span>
      </div>
      <div class="obs-funnel-body">${rowsHtml}</div>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Decision panel
   ═══════════════════════════════════════════════════════════════════════════ */

function outcomeColor(outcome) {
  switch (outcome) {
    case 'trade_opened': return 'var(--green)';
    case 'rejected':
    case 'order_failed': return 'var(--red)';
    case 'held':
    case 'no_signal':
    case 'no_construction': return 'var(--amber)';
    case 'delayed': return 'var(--blue)';
    default: return 'var(--muted)';
  }
}

function renderDecisionCard(entry) {
  const d = entry.decision || {};
  const penalties = (d.penalties || [])
    .map(p => `<li><b>${escape(p.source)}</b> — ${p.points} pts <span class="muted">${escape(p.reason || '')}</span></li>`)
    .join('');
  const journey = d.journey && d.journey.stages
    ? d.journey.stages.map((s, i) => `
        <div class="obs-journey-step">
          <span class="obs-journey-idx">${i + 1}</span>
          <span class="obs-journey-stage">${escape(s.stage)}</span>
          <span class="obs-journey-status obs-status-${escape(s.status)}">${escape(s.status)}</span>
          ${s.detail ? `<span class="obs-journey-detail">${escape(s.detail)}</span>` : ''}
          ${s.blocked_by ? `<span class="obs-journey-blocked">blocked_by=${escape(s.blocked_by)}</span>` : ''}
        </div>`).join('')
    : '<div class="muted">No journey recorded</div>';

  const color = outcomeColor(d.outcome);
  return `
    <div class="card obs-decision-card">
      <div class="obs-decision-head">
        <div>
          <h4>
            ${escape(d.symbol || '?')} ·
            ${badge(d.mode || '?', 'var(--blue)')}
            ${d.direction ? badge(d.direction, d.direction === 'BUY' ? 'var(--green)' : 'var(--red)') : ''}
            ${d.setup_type ? badge(d.setup_type, 'var(--muted)') : ''}
          </h4>
          <div class="muted">${escape(d.occurred_at || '')}</div>
        </div>
        <div class="obs-decision-outcome" style="color:${color}">
          <b>${escape(d.outcome || 'n/a')}</b>
          ${d.reject_stage ? `<div class="muted">stage: ${escape(d.reject_stage)}</div>` : ''}
          ${d.reject_reason ? `<div class="muted">${escape(d.reject_reason)}</div>` : ''}
        </div>
      </div>
      <div class="obs-decision-metrics">
        <div><span class="muted">raw conf</span><b>${d.confidence_raw != null ? d.confidence_raw.toFixed(3) : '—'}</b></div>
        <div><span class="muted">adj conf</span><b>${d.confidence_adjusted != null ? d.confidence_adjusted.toFixed(3) : '—'}</b></div>
        <div><span class="muted">conviction</span><b>${d.conviction_score != null ? d.conviction_score.toFixed(1) : '—'} (${escape(d.conviction_decision || '—')})</b></div>
        <div><span class="muted">size mult</span><b>${d.size_multiplier != null ? d.size_multiplier.toFixed(3) : '—'}</b></div>
        <div><span class="muted">AI verdict</span><b>${escape(d.ai_verdict || '—')}</b></div>
        <div><span class="muted">latency</span><b>${d.latency_ms != null ? d.latency_ms.toFixed(0) + 'ms' : '—'}</b></div>
      </div>
      ${penalties ? `<div class="obs-decision-penalties"><h5>Penalties</h5><ul>${penalties}</ul></div>` : ''}
      <details class="obs-decision-journey">
        <summary>Journey (${(d.journey && d.journey.stages ? d.journey.stages.length : 0)} stages)</summary>
        <div class="obs-journey-body">${journey}</div>
      </details>
    </div>`;
}

function renderDecisions(data) {
  if (!data || !data.decisions || !data.decisions.length) {
    return '<div class="card"><p>No recent decisions.</p></div>';
  }
  return data.decisions.map(renderDecisionCard).join('');
}

/* ═══════════════════════════════════════════════════════════════════════════
   Heatmap
   ═══════════════════════════════════════════════════════════════════════════ */

function renderHeatmap(data) {
  if (!data || !data.stages || !data.stages.length || !data.symbols.length) {
    return '<div class="card"><p>No heatmap data.</p></div>';
  }
  const cellMap = new Map();
  for (const c of data.cells) cellMap.set(`${c.stage}|${c.symbol}`, c);
  const header = `<th>stage \\ symbol</th>${data.symbols.map(s => `<th>${escape(s)}</th>`).join('')}`;
  const rows = data.stages.map(stage => {
    const cells = data.symbols.map(sym => {
      const c = cellMap.get(`${stage}|${sym}`);
      if (!c) return '<td class="obs-heat-empty">—</td>';
      const r = c.rejection_rate;
      const hue = 120 - r * 120; // green→red
      const bg = `hsl(${hue}, 70%, 20%)`;
      return `<td style="background:${bg}" title="total=${c.total}, blocked=${c.blocked}, held=${c.held}">${(r * 100).toFixed(0)}%</td>`;
    }).join('');
    return `<tr><td><b>${escape(stage)}</b></td>${cells}</tr>`;
  }).join('');
  return `
    <div class="card">
      <h3>Stage × symbol rejection heatmap</h3>
      <table class="obs-heatmap"><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Mode compare
   ═══════════════════════════════════════════════════════════════════════════ */

function renderModeCompare(data) {
  if (!data || !data.modes || !data.modes.length) {
    return '<div class="card"><p>No mode comparison data.</p></div>';
  }
  const rows = data.modes.map(m => {
    const out = m.outcomes || {};
    const total = m.total || 0;
    const exec = m.executed || 0;
    const pct = total ? ((exec / total) * 100).toFixed(1) : '0.0';
    return `
      <tr>
        <td><b>${escape(m.mode)}</b></td>
        <td>${total}</td>
        <td style="color:var(--green)">${exec} (${pct}%)</td>
        <td style="color:var(--red)">${out.rejected || 0}</td>
        <td style="color:var(--amber)">${(out.held || 0) + (out.no_signal || 0) + (out.no_construction || 0)}</td>
        <td style="color:var(--blue)">${out.delayed || 0}</td>
      </tr>`;
  }).join('');
  return `
    <div class="card">
      <h3>Mode comparison</h3>
      <table class="obs-compare">
        <thead><tr><th>mode</th><th>cycles</th><th>executed</th><th>rejected</th><th>held/no_signal</th><th>delayed</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Guards status
   ═══════════════════════════════════════════════════════════════════════════ */

function renderGuardsStatus(data) {
  if (!data) return '';
  const dd = data.drawdown_pause || {};
  const cb = data.circuit_breaker || {};
  const ddDot = dd.global_paused ? 'var(--red)' : (dd.available ? 'var(--green)' : 'var(--muted)');
  const cbDot = cb.is_open ? 'var(--red)' : (cb.available ? 'var(--green)' : 'var(--muted)');
  const perSym = Object.entries(dd.per_symbol || {})
    .map(([sym, v]) => `<li>${escape(sym)}: ${v.paused ? `<b style="color:var(--red)">paused until ${escape(v.until)}</b>` : 'active'}</li>`)
    .join('');
  const events = (cb.recent_events || [])
    .map(e => `<li><span class="muted">${escape(e.created_at || '')}</span> <b>${escape(e.event_type || '')}</b> ${escape(e.severity || '')} — ${escape(e.message || '')}</li>`)
    .join('');
  return `
    <div class="card">
      <h3>Guards status</h3>
      <div class="obs-guards">
        <div>
          <h4><span class="pulse-dot" style="background:${ddDot}"></span> Drawdown pause</h4>
          <div class="muted">global: <b>${dd.global_paused ? 'PAUSED until ' + escape(dd.global_pause_until || '?') : 'active'}</b></div>
          ${perSym ? `<ul>${perSym}</ul>` : '<div class="muted">no per-symbol pauses</div>'}
          ${dd.note ? `<div class="muted"><i>${escape(dd.note)}</i></div>` : ''}
        </div>
        <div>
          <h4><span class="pulse-dot" style="background:${cbDot}"></span> Circuit breaker</h4>
          <div class="muted">${cb.is_open ? '<b style="color:var(--red)">OPEN</b>' : 'closed'}</div>
          ${events ? `<ul>${events}</ul>` : '<div class="muted">no recent events</div>'}
          ${cb.note ? `<div class="muted"><i>${escape(cb.note)}</i></div>` : ''}
        </div>
      </div>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Main render
   ═══════════════════════════════════════════════════════════════════════════ */

let _state = { symbol: '', hours: 24, source: 'live' };

async function refresh(container) {
  const qs = new URLSearchParams();
  if (_state.symbol) qs.set('symbol', _state.symbol);
  qs.set('hours', String(_state.hours));
  qs.set('source', _state.source);

  try {
    const [funnel, decisions, heatmap, modes, guards] = await Promise.all([
      apiGet(`/api/pipeline/funnel?${qs.toString()}`).catch(e => ({ error: e.message })),
      apiGet(`/api/pipeline/decisions/latest?limit=15${_state.symbol ? '&symbol=' + encodeURIComponent(_state.symbol) : ''}`)
        .catch(e => ({ error: e.message })),
      apiGet(`/api/pipeline/stages/heatmap?${qs.toString()}`).catch(e => ({ error: e.message })),
      apiGet(`/api/pipeline/modes/compare?${qs.toString()}`).catch(e => ({ error: e.message })),
      apiGet('/api/controls/guards-status').catch(e => ({ error: e.message })),
    ]);

    const errBlock = [funnel, decisions, heatmap, modes, guards]
      .filter(x => x && x.error)
      .map(x => `<div style="color:var(--red)">${escape(x.error)}</div>`)
      .join('');

    const content = container.querySelector('#obs-content');
    content.innerHTML = `
      ${errBlock ? `<div class="card">${errBlock}</div>` : ''}
      ${renderGuardsStatus(guards)}
      ${renderFunnel(funnel)}
      ${renderModeCompare(modes)}
      ${renderHeatmap(heatmap)}
      <h3 class="obs-section-title">Latest trade decisions</h3>
      ${renderDecisions(decisions)}
    `;
  } catch (err) {
    container.querySelector('#obs-content').innerHTML = `<div class="card" style="color:var(--red)">${escape(err.message)}</div>`;
  }
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-title">🔍 Observability</div>
      <div class="obs-controls">
        <input id="obs-symbol" placeholder="symbol (e.g. XAUUSD)" value="${escape(_state.symbol)}" />
        <select id="obs-source">
          <option value="live"${_state.source === 'live' ? ' selected' : ''}>live</option>
          <option value="backtest_replay"${_state.source === 'backtest_replay' ? ' selected' : ''}>backtest replay</option>
        </select>
        <select id="obs-hours">
          <option value="1">1h</option>
          <option value="6">6h</option>
          <option value="24" selected>24h</option>
          <option value="72">72h</option>
          <option value="168">7d</option>
        </select>
        <button id="obs-refresh" class="btn btn-primary">Refresh</button>
      </div>
    </div>
    <div id="obs-content"><div class="card">Loading…</div></div>
  `;

  const symInput = container.querySelector('#obs-symbol');
  const srcSelect = container.querySelector('#obs-source');
  const hrSelect = container.querySelector('#obs-hours');
  hrSelect.value = String(_state.hours);

  function commit() {
    _state.symbol = symInput.value.trim();
    _state.source = srcSelect.value;
    _state.hours = parseInt(hrSelect.value, 10) || 24;
    refresh(container);
  }
  container.querySelector('#obs-refresh').addEventListener('click', commit);
  symInput.addEventListener('keydown', e => { if (e.key === 'Enter') commit(); });
  srcSelect.addEventListener('change', commit);
  hrSelect.addEventListener('change', commit);

  await refresh(container);
}
