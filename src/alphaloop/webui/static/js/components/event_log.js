/**
 * Event Log — redesigned timeline feed with stats, search, expand, and icons.
 */
import { apiGet, apiDelete } from '../api.js';

/* ── Event metadata ─────────────────────────────────────────────────────────── */

const TYPE_META = {
  TradeOpened:        { color: 'var(--green)',   icon: 'fi-rr-arrow-trend-up',    label: 'Trade Opened',      group: 'trading'   },
  TradeClosed:        { color: 'var(--blue)',    icon: 'fi-rr-square-check',      label: 'Trade Closed',      group: 'trading'   },
  SignalGenerated:    { color: 'var(--amber)',   icon: 'fi-rr-signal-alt-2',      label: 'Signal',            group: 'signals'   },
  SignalValidated:    { color: 'var(--green)',   icon: 'fi-rr-shield-check',      label: 'Validated',         group: 'signals'   },
  SignalRejected:     { color: 'var(--red)',     icon: 'fi-rr-ban',               label: 'Rejected',          group: 'signals'   },
  RiskLimitHit:       { color: 'var(--red)',     icon: 'fi-rr-stop-circle',       label: 'Risk Limit',        group: 'risk'      },
  PipelineBlocked:    { color: 'var(--red)',     icon: 'fi-rr-lock',              label: 'Blocked',           group: 'risk'      },
  ConfigChanged:      { color: 'var(--muted)',   icon: 'fi-rr-settings',          label: 'Config',            group: 'system'    },
  StrategyPromoted:   { color: 'var(--primary)', icon: 'fi-rr-rocket-lunch',      label: 'Promoted',          group: 'strategy'  },
  SeedLabProgress:    { color: 'var(--amber)',   icon: 'fi-rr-flask',             label: 'SeedLab',           group: 'strategy'  },
  CanaryStarted:      { color: 'var(--amber)',   icon: 'fi-rr-feather',           label: 'Canary Started',    group: 'strategy'  },
  CanaryEnded:        { color: 'var(--blue)',    icon: 'fi-rr-feather',           label: 'Canary Ended',      group: 'strategy'  },
  MetaLoopCompleted:  { color: 'var(--green)',   icon: 'fi-rr-rotate-right',      label: 'Meta Loop',         group: 'system'    },
  StrategyRolledBack: { color: 'var(--red)',     icon: 'fi-rr-undo',              label: 'Rolled Back',       group: 'strategy'  },
  ResearchCompleted:  { color: 'var(--blue)',    icon: 'fi-rr-chart-line-up',     label: 'Research Done',     group: 'discovery' },
  PipelineStep:       { color: 'var(--muted)',   icon: 'fi-rr-filter',            label: 'Pipeline Step',     group: 'system'    },
};

const GROUPS = [
  { id: 'ALL',       label: 'All Events',  icon: 'fi-rr-apps'             },
  { id: 'waterfall', label: 'Waterfall',   icon: 'fi-rr-chart-waterfall'  },
  { id: 'trading',   label: 'Trading',     icon: 'fi-rr-arrow-trend-up'   },
  { id: 'signals',   label: 'Signals',     icon: 'fi-rr-signal-alt-2'     },
  { id: 'risk',      label: 'Risk',        icon: 'fi-rr-shield-check'     },
  { id: 'strategy',  label: 'Strategy',    icon: 'fi-rr-bullseye'         },
  { id: 'discovery', label: 'Discovery',   icon: 'fi-rr-flask'            },
  { id: 'system',    label: 'System',      icon: 'fi-rr-settings'         },
];

function getMeta(type) {
  return TYPE_META[type] || { color: 'var(--muted)', icon: 'fi-rr-info', label: type, group: 'system' };
}

/* ── Helpers ──────────────────────────────────────────────────────────────── */

function fmtTime(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return 'Today';
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function extractSymbol(data) {
  return data?.symbol || null;
}

function buildSummaryLine(type, data) {
  if (!data) return '';
  switch (type) {
    case 'TradeOpened':
      return `${data.direction || ''} @ ${data.entry_price ?? ''}${data.lot_size ? ' · ' + data.lot_size + ' lots' : ''}`;
    case 'TradeClosed':
      return `${data.outcome || ''} · PnL $${(data.pnl_usd ?? 0).toFixed(2)}`;
    case 'SignalGenerated':
      return `${data.direction || ''} · confidence ${Math.round((data.confidence || 0) * 100)}%`;
    case 'SignalValidated':
      return data.approved === false ? `Rejected · risk_score=${data.risk_score}` : `Approved`;
    case 'SignalRejected': {
      const note = data.validator_reasoning ? ` — ${data.validator_reasoning}` : '';
      return `${data.rejected_by || ''}: ${data.reason || ''}${note}`;
    }
    case 'RiskLimitHit':
      return `${data.limit_type || ''} — ${data.details || ''}`;
    case 'PipelineBlocked':
      return `Blocked by ${data.blocked_by || ''}`;
    case 'ConfigChanged':
      return (data.keys || []).join(', ');
    case 'StrategyPromoted':
      return `${data.from_status || ''} → ${data.to_status || ''}`;
    case 'SeedLabProgress':
      return `${data.phase || ''} ${data.current ?? ''}/${data.total ?? ''} ${data.message || ''}`;
    case 'CanaryStarted':
      return `${data.allocation_pct ?? ''}% allocation`;
    case 'CanaryEnded':
      return `Recommendation: ${data.recommendation || ''}`;
    case 'MetaLoopCompleted':
      return `Generation ${data.generation ?? ''}`;
    case 'StrategyRolledBack':
      return `${data.reason || ''}`;
    case 'ResearchCompleted':
      return `Report ready`;
    case 'PipelineStep': {
      const stageName = data.stage || '';
      const stageStatus = data.status || '';
      if (stageName === 'filters') {
        const results = data.results || [];
        const combined = results.reduce((acc, r) => acc * (r.size_modifier ?? 1), 1);
        const blocker = results.find(r => !r.passed);
        const statusTxt = stageStatus === 'blocked'
          ? `X BLOCKED${blocker ? ` by ${blocker.tool_name}` : ''}`
          : stageStatus === 'passed' ? `✓ passed` : stageStatus;
        return `${stageName} · ${statusTxt} · size: ${combined.toFixed(3)}`;
      }
      return `${stageName} · ${stageStatus}${data.detail ? ' · ' + data.detail : ''}`;
    }
    default: {
      const skip = new Set(['symbol', 'timestamp', 'type']);
      const parts = [];
      for (const [k, v] of Object.entries(data)) {
        if (skip.has(k) || v === '' || v === null || v === undefined || typeof v === 'object') continue;
        parts.push(`${k}: ${v}`);
        if (parts.length >= 4) break;
      }
      return parts.join(' · ');
    }
  }
}

function typeMatchesGroup(type, group) {
  if (group === 'ALL') return true;
  return getMeta(type).group === group;
}

function typeMatchesSearch(type, data, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  if (type.toLowerCase().includes(q)) return true;
  const sym = extractSymbol(data);
  if (sym && sym.toLowerCase().includes(q)) return true;
  const summary = buildSummaryLine(type, data);
  if (summary.toLowerCase().includes(q)) return true;
  return false;
}

/* ── Stats computation ────────────────────────────────────────────────────── */

function computeStats(events) {
  const today = new Date().toDateString();
  let todayCount = 0;
  let criticalCount = 0;
  const symbols = new Set();
  const typeCounts = {};

  for (const e of events) {
    if (e.timestamp && new Date(e.timestamp).toDateString() === today) todayCount++;
    if (e.type === 'RiskLimitHit' || e.type === 'SignalRejected' || e.type === 'PipelineBlocked' || e.type === 'StrategyRolledBack') criticalCount++;
    const sym = extractSymbol(e.data);
    if (sym && sym !== '--') symbols.add(sym);
    typeCounts[e.type] = (typeCounts[e.type] || 0) + 1;
  }

  // Top type
  let topType = '--';
  let topCount = 0;
  for (const [t, c] of Object.entries(typeCounts)) {
    if (c > topCount) { topCount = c; topType = t; }
  }

  return { total: events.length, todayCount, criticalCount, symbols: symbols.size, topType, topCount };
}

/* ── Render pieces ────────────────────────────────────────────────────────── */

function renderStats(stats) {
  const cards = [
    { accent: 'var(--primary)', icon: 'fi-rr-list',          label: 'Total Events', value: stats.total.toLocaleString() },
    { accent: 'var(--blue)',    icon: 'fi-rr-calendar-day',  label: 'Today',        value: stats.todayCount },
    { accent: 'var(--red)',     icon: 'fi-rr-stop-circle',   label: 'Critical',     value: stats.criticalCount },
    { accent: 'var(--green)',   icon: 'fi-rr-coins',         label: 'Symbols',      value: stats.symbols },
  ];
  return `
    <div class="el-stats-row">
      ${cards.map(c => `
        <div class="el-stat-card" style="--stat-accent:${c.accent}">
          <div class="el-stat-icon"><i class="fi ${c.icon}"></i> ${c.label}</div>
          <div class="el-stat-value">${c.value}</div>
        </div>`).join('')}
    </div>`;
}

function renderControls(activeGroup, search) {
  const pills = GROUPS.map(g => `
    <button class="el-group-pill${activeGroup === g.id ? ' active' : ''}" data-group="${g.id}">
      <i class="fi ${g.icon}"></i> ${g.label}
    </button>`).join('');

  return `
    <div class="el-controls">
      <div class="el-search-wrap">
        <i class="fi fi-rr-search el-search-icon"></i>
        <input class="el-search-input" id="el-search" type="text" placeholder="Search events, symbols…" value="${search || ''}">
        ${search ? '<button class="el-search-clear" id="el-search-clear">✕</button>' : ''}
      </div>
      <div class="el-group-pills">${pills}</div>
    </div>`;
}

function modClass(modifier) {
  if (modifier === 0) return 'el-mod-red';
  if (modifier >= 0.8) return 'el-mod-green';
  if (modifier >= 0.4) return 'el-mod-yellow';
  return 'el-mod-red';
}

function renderFilterDetail(data) {
  const results = data.results || [];
  if (!results.length) return `<pre class="el-json">${JSON.stringify(data, null, 2)}</pre>`;

  // Build multiplicative chain
  const chain = results.map(r => (r.size_modifier ?? 1).toFixed(2)).join(' × ');
  const combined = results.reduce((acc, r) => acc * (r.size_modifier ?? 1), 1);
  const combinedClamped = Math.max(0, Math.min(1, combined));
  const blocked_by = data.blocked_by || (results.find(r => !r.passed)?.tool_name) || null;
  const isBlocked = data.status === 'blocked';
  const verdict = isBlocked
    ? `<span class="el-mod-red">BLOCKED${blocked_by ? ` by ${blocked_by}` : ''}</span>`
    : `<span class="el-mod-green">PASSED</span>`;

  const rows = results.map(r => {
    const passed = r.passed !== false;
    const mod = r.size_modifier ?? 1;
    const badge = passed
      ? `<span class="el-filter-badge el-filter-badge--pass">✓ pass</span>`
      : `<span class="el-filter-badge el-filter-badge--block">✗ ${r.severity === 'block' ? 'block' : 'warn'}</span>`;
    return `
      <div class="el-filter-tool-row">
        <span class="el-filter-tool-name">${r.tool_name || '?'}</span>
        ${badge}
        <span class="el-filter-modifier ${modClass(mod)}">${mod.toFixed(2)}×</span>
        <span class="el-filter-reason">${r.reason || ''}</span>
      </div>`;
  }).join('');

  return `
    <div class="el-filter-breakdown">
      <div class="el-filter-header">
        <span>Tool</span><span>Status</span><span>Modifier</span><span>Reason</span>
      </div>
      ${rows}
      <div class="el-filter-combined">
        <span class="el-filter-chain">${chain} = <span class="${modClass(combinedClamped)}">${combinedClamped.toFixed(3)}</span></span>
        <span class="el-filter-verdict">${verdict}</span>
      </div>
    </div>`;
}

function renderEventRow(e, idx) {
  const meta = getMeta(e.type);
  const sym = extractSymbol(e.data);
  const summary = buildSummaryLine(e.type, e.data);
  const timeStr = fmtTime(e.timestamp);
  const dateStr = fmtDate(e.timestamp);

  return `
    <div class="el-row" data-idx="${idx}" role="button" tabindex="0" aria-expanded="false">
      <div class="el-row-accent" style="background:${meta.color}"></div>
      <div class="el-row-icon" style="color:${meta.color}"><i class="fi ${meta.icon}"></i></div>
      <div class="el-row-main">
        <div class="el-row-top">
          <span class="el-type-badge" style="color:${meta.color};background:${meta.color}1a;border-color:${meta.color}33">
            ${meta.label}
          </span>
          ${sym ? `<span class="el-symbol-chip">${sym}</span>` : ''}
          <span class="el-row-summary">${summary}</span>
        </div>
        <div class="el-row-time">
          <i class="fi fi-rr-clock"></i> ${timeStr}${dateStr ? ' · ' + dateStr : ''}
        </div>
      </div>
      <div class="el-row-expand-icon"><i class="fi fi-rr-angle-down"></i></div>
      <div class="el-row-detail" id="el-detail-${idx}">
        ${(e.type === 'PipelineStep' && e.data?.stage === 'filters' && (e.data?.results?.length > 0))
          ? renderFilterDetail(e.data)
          : `<pre class="el-json">${JSON.stringify(e.data || {}, null, 2)}</pre>`}
      </div>
    </div>`;
}

function renderFeed(events, activeGroup, search) {
  const filtered = events.filter(e =>
    typeMatchesGroup(e.type, activeGroup) &&
    typeMatchesSearch(e.type, e.data, search)
  );

  if (!filtered.length) {
    return `
      <div class="el-empty">
        <div class="el-empty-icon"><i class="fi fi-rr-time-past"></i></div>
        <div class="el-empty-title">No events${activeGroup !== 'ALL' ? ' in this category' : ''}</div>
        <div class="el-empty-sub">${search ? `No results for "${search}"` : 'Events will appear here in real time'}</div>
      </div>`;
  }

  return `
    <div class="el-feed">
      ${filtered.map((e, i) => renderEventRow(e, i)).join('')}
    </div>`;
}

/* ── Waterfall view ───────────────────────────────────────────────────────── */

const OUTCOME_META = {
  trade_opened: { color: 'var(--green)',  icon: 'fi-rr-check-circle', label: 'TRADE' },
  held:         { color: 'var(--amber)',  icon: 'fi-rr-pause-circle', label: 'HOLD' },
  rejected:     { color: 'var(--red)',    icon: 'fi-rr-ban',          label: 'REJECT' },
  delayed:      { color: 'var(--blue)',   icon: 'fi-rr-clock',        label: 'DELAY' },
  no_signal:       { color: 'var(--muted)',  icon: 'fi-rr-minus-circle', label: 'NO SIGNAL' },
  no_construction: { color: 'var(--muted)',  icon: 'fi-rr-minus-circle', label: 'NO STRUCTURE' },
  order_failed:    { color: 'var(--red)',    icon: 'fi-rr-triangle-warning', label: 'FAILED' },
};

function getOutcomeMeta(outcome) {
  return OUTCOME_META[outcome] || { color: 'var(--muted)', icon: 'fi-rr-info', label: outcome };
}

function renderPenaltyBar(value, max, label, color) {
  const pct = Math.min(100, Math.max(0, (Math.abs(value) / max) * 100));
  if (value === 0) return '';
  return `
    <div class="wf-penalty-row">
      <span class="wf-penalty-label">${label}</span>
      <div class="wf-penalty-bar-track">
        <div class="wf-penalty-bar-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <span class="wf-penalty-value">-${value.toFixed(1)}</span>
    </div>`;
}

function renderGroupBar(name, score) {
  const clr = score >= 70 ? 'var(--green)' : score >= 50 ? 'var(--amber)' : 'var(--red)';
  return `
    <div class="wf-group-row">
      <span class="wf-group-label">${name}</span>
      <div class="wf-group-bar-track">
        <div class="wf-group-bar-fill" style="width:${score}%;background:${clr}"></div>
      </div>
      <span class="wf-group-value">${score.toFixed(0)}</span>
    </div>`;
}

function renderWaterfallCard(w, idx) {
  const om = getOutcomeMeta(w.outcome);
  const timeStr = fmtTime(w.timestamp);
  const sig = w.signal || {};
  const conv = w.conviction || {};
  const qual = w.quality || {};
  const regime = w.regime || {};
  const inv = w.invalidation || {};

  const hasConviction = conv.score !== undefined;

  // Group score bars
  const groupBars = Object.entries(qual.group_scores || {})
    .sort((a, b) => b[1] - a[1])
    .map(([name, score]) => renderGroupBar(name, score))
    .join('');

  // Penalty bars
  const penaltyBars = [
    conv.invalidation_penalty > 0 ? renderPenaltyBar(conv.invalidation_penalty, 50, 'Invalidation', 'var(--red)') : '',
    conv.conflict_penalty > 0 ? renderPenaltyBar(conv.conflict_penalty, 50, 'Conflict', 'var(--amber)') : '',
    conv.portfolio_penalty > 0 ? renderPenaltyBar(conv.portfolio_penalty, 50, 'Portfolio', 'var(--blue)') : '',
  ].join('');

  // Sizing scalars
  const sz = w.sizing || {};
  const scalars = [
    sz.conviction_scalar ? `conviction: ${sz.conviction_scalar.toFixed(2)}` : '',
    sz.regime_scalar && sz.regime_scalar !== 1 ? `regime: ${sz.regime_scalar.toFixed(2)}` : '',
    sz.freshness_scalar && sz.freshness_scalar !== 1 ? `freshness: ${sz.freshness_scalar.toFixed(2)}` : '',
    sz.risk_gate_scalar && sz.risk_gate_scalar !== 1 ? `risk: ${sz.risk_gate_scalar.toFixed(2)}` : '',
    sz.equity_curve_scalar && sz.equity_curve_scalar !== 1 ? `equity: ${sz.equity_curve_scalar.toFixed(2)}` : '',
  ].filter(Boolean).join(' · ');

  return `
    <div class="wf-card" data-idx="${idx}" role="button" tabindex="0" aria-expanded="false">
      <div class="wf-card-header">
        <span class="wf-outcome-badge" style="color:${om.color};background:${om.color}1a;border-color:${om.color}33">
          <i class="fi ${om.icon}"></i> ${om.label}
        </span>
        ${sig.direction ? `<span class="wf-direction ${sig.direction === 'BUY' ? 'wf-buy' : 'wf-sell'}">${sig.direction}</span>` : ''}
        ${sig.setup_type ? `<span class="wf-setup">${sig.setup_type}</span>` : ''}
        <span class="wf-regime-chip">${regime.regime || '—'}</span>
        ${hasConviction ? `<span class="wf-score" style="color:${conv.score >= 60 ? 'var(--green)' : conv.score >= 40 ? 'var(--amber)' : 'var(--red)'}">${conv.score?.toFixed(1)}</span>` : ''}
        <span class="wf-time"><i class="fi fi-rr-clock"></i> ${timeStr}</span>
        <span class="wf-elapsed">${w.elapsed_ms?.toFixed(0)}ms</span>
      </div>

      <div class="wf-card-detail" style="display:none">
        ${w.rejection_reason ? `<div class="wf-reason"><i class="fi fi-rr-info"></i> ${w.rejection_reason}</div>` : ''}

        ${inv.severity && inv.severity !== 'PASS' ? `
          <div class="wf-section">
            <div class="wf-section-title">Invalidation (${inv.severity})</div>
            ${(inv.failures || []).map(f =>
              `<div class="wf-inv-failure"><span class="wf-inv-severity ${f.severity === 'HARD_INVALIDATE' ? 'wf-hard' : 'wf-soft'}">${f.severity === 'HARD_INVALIDATE' ? 'HARD' : 'SOFT'}</span> ${f.check}: ${f.reason}</div>`
            ).join('')}
          </div>
        ` : ''}

        ${groupBars ? `
          <div class="wf-section">
            <div class="wf-section-title">Feature Groups (overall: ${qual.overall_score?.toFixed(1) ?? '—'})</div>
            ${groupBars}
          </div>
        ` : ''}

        ${penaltyBars ? `
          <div class="wf-section">
            <div class="wf-section-title">Penalties (${conv.total_penalty?.toFixed(1) ?? 0} / ${conv.penalty_budget_cap ?? 50}${conv.penalties_prorated ? ' PRORATED' : ''})</div>
            ${penaltyBars}
          </div>
        ` : ''}

        ${hasConviction ? `
          <div class="wf-section">
            <div class="wf-section-title">Conviction</div>
            <div class="wf-conviction-row">
              <span>Score: <strong>${conv.score?.toFixed(1)}</strong></span>
              <span>Min Entry: ${conv.regime_min_entry?.toFixed(0)}</span>
              <span>Ceiling: ${conv.regime_ceiling?.toFixed(0)}</span>
              <span>Decision: <strong style="color:${conv.decision === 'TRADE' ? 'var(--green)' : 'var(--amber)'}">${conv.decision}</strong></span>
              ${conv.quality_floor_triggered ? '<span class="wf-floor-badge">FLOOR</span>' : ''}
            </div>
          </div>
        ` : ''}

        ${scalars ? `
          <div class="wf-section">
            <div class="wf-section-title">Sizing Scalars</div>
            <div class="wf-scalars">${scalars}</div>
          </div>
        ` : ''}

        ${sig.entry_zone ? `
          <div class="wf-section">
            <div class="wf-section-title">Signal</div>
            <div class="wf-signal-row">
              Entry: [${sig.entry_zone[0]?.toFixed(2)}, ${sig.entry_zone[1]?.toFixed(2)}]
              · SL: ${sig.stop_loss?.toFixed(2)}
              · TP: ${(sig.take_profit || []).map(t => t.toFixed(2)).join(', ')}
              · R:R: ${sig.rr_ratio?.toFixed(2)}
              · Conf: ${(sig.raw_confidence * 100)?.toFixed(0)}%
            </div>
          </div>
        ` : ''}
      </div>
    </div>`;
}

function renderWaterfallFeed(waterfalls) {
  if (!waterfalls.length) {
    return `
      <div class="el-empty">
        <div class="el-empty-icon"><i class="fi fi-rr-chart-waterfall"></i></div>
        <div class="el-empty-title">No pipeline waterfalls yet</div>
        <div class="el-empty-sub">Conviction waterfalls will appear here once the trading loop runs</div>
      </div>`;
  }
  return `<div class="wf-feed">${waterfalls.map((w, i) => renderWaterfallCard(w, i)).join('')}</div>`;
}

function attachWaterfallListeners() {
  document.querySelectorAll('.wf-card').forEach(card => {
    const handler = () => {
      const expanded = card.getAttribute('aria-expanded') === 'true';
      card.setAttribute('aria-expanded', String(!expanded));
      const detail = card.querySelector('.wf-card-detail');
      if (detail) detail.style.display = expanded ? 'none' : 'block';
    };
    card.addEventListener('click', handler);
    card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') handler(); });
  });
}

/* ── Main render ──────────────────────────────────────────────────────────── */

export async function render(container) {
  let activeGroup = 'ALL';
  let searchQuery = '';
  let allEvents = [];
  let isLive = true;

  // Skeleton shell
  container.innerHTML = `
    <div class="page-header">
      <div>
        <div class="page-title">📋 Event Log</div>
        <div class="page-subtitle">Real-time event bus activity stream</div>
      </div>
      <div class="el-header-actions">
        <button class="btn btn-sm btn-outline" id="el-clear-btn" title="Clear all events"><i class="fi fi-rr-trash"></i> Clear</button>
        <span class="el-live-badge" id="el-live-badge"><span class="pulse-dot" style="position:static;display:inline-block;margin-right:4px"></span>Live</span>
        <span class="el-last-update" id="el-last-update">—</span>
      </div>
    </div>

    <div id="el-stats"></div>
    <div id="el-controls"></div>
    <div id="el-feed-wrap"><div class="el-loading"><span class="spinner"></span> Loading events…</div></div>`;

  /* ── Data load ── */
  async function load() {
    try {
      const statsEl = document.getElementById('el-stats');
      const controlsEl = document.getElementById('el-controls');
      const feedWrap = document.getElementById('el-feed-wrap');
      const lastUpdate = document.getElementById('el-last-update');

      // Waterfall mode: fetch from dedicated endpoint
      if (activeGroup === 'waterfall') {
        const wfData = await apiGet('/api/events/waterfall?limit=50');
        const waterfalls = wfData.waterfalls || [];
        if (statsEl) statsEl.innerHTML = renderWaterfallStats(waterfalls);
        if (controlsEl) {
          controlsEl.innerHTML = renderControls(activeGroup, '');
          attachControlListeners();
        }
        if (feedWrap) feedWrap.innerHTML = renderWaterfallFeed(waterfalls);
        if (lastUpdate) lastUpdate.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        attachWaterfallListeners();
        return;
      }

      // Normal event mode
      const params = new URLSearchParams({ limit: '200' });
      const data = await apiGet(`/api/events?${params}`);
      allEvents = data.events || [];

      const stats = computeStats(allEvents);
      if (statsEl) statsEl.innerHTML = renderStats(stats);
      if (controlsEl) {
        const prevSearch = document.getElementById('el-search')?.value ?? searchQuery;
        searchQuery = prevSearch;
        controlsEl.innerHTML = renderControls(activeGroup, searchQuery);
        attachControlListeners();
      }
      if (feedWrap) feedWrap.innerHTML = renderFeed(allEvents, activeGroup, searchQuery);
      if (lastUpdate) lastUpdate.textContent = `Updated ${new Date().toLocaleTimeString()}`;

      attachRowListeners();
    } catch (err) {
      const feedWrap = document.getElementById('el-feed-wrap');
      if (feedWrap) feedWrap.innerHTML = `<div class="el-error"><i class="fi fi-rr-triangle-warning"></i> ${err.message}</div>`;
    }
  }

  function renderWaterfallStats(waterfalls) {
    const outcomes = {};
    for (const w of waterfalls) outcomes[w.outcome] = (outcomes[w.outcome] || 0) + 1;
    const cards = [
      { accent: 'var(--primary)', icon: 'fi-rr-list',         label: 'Total Cycles', value: waterfalls.length },
      { accent: 'var(--green)',   icon: 'fi-rr-check-circle', label: 'Trades',       value: outcomes.trade_opened || 0 },
      { accent: 'var(--amber)',   icon: 'fi-rr-pause-circle', label: 'Held',         value: outcomes.held || 0 },
      { accent: 'var(--red)',     icon: 'fi-rr-ban',          label: 'Rejected',     value: outcomes.rejected || 0 },
    ];
    return `
      <div class="el-stats-row">
        ${cards.map(c => `
          <div class="el-stat-card" style="--stat-accent:${c.accent}">
            <div class="el-stat-icon"><i class="fi ${c.icon}"></i> ${c.label}</div>
            <div class="el-stat-value">${c.value}</div>
          </div>`).join('')}
      </div>`;
  }

  /* ── Re-render feed only (no API call) ── */
  function refilter() {
    const feedWrap = document.getElementById('el-feed-wrap');
    if (feedWrap) feedWrap.innerHTML = renderFeed(allEvents, activeGroup, searchQuery);
    attachRowListeners();
    // Update group pills active state
    document.querySelectorAll('.el-group-pill').forEach(p => {
      p.classList.toggle('active', p.dataset.group === activeGroup);
    });
  }

  /* ── Row expand/collapse ── */
  function attachRowListeners() {
    document.querySelectorAll('.el-row').forEach(row => {
      const handler = () => {
        const expanded = row.getAttribute('aria-expanded') === 'true';
        row.setAttribute('aria-expanded', String(!expanded));
        const detail = row.querySelector('.el-row-detail');
        if (detail) detail.style.display = expanded ? 'none' : 'block';
        const chevron = row.querySelector('.el-row-expand-icon');
        if (chevron) chevron.classList.toggle('rotated', !expanded);
      };
      row.addEventListener('click', handler);
      row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') handler(); });
    });
  }

  /* ── Control listeners ── */
  function attachControlListeners() {
    // Group pills
    document.querySelectorAll('.el-group-pill').forEach(pill => {
      pill.addEventListener('click', () => {
        activeGroup = pill.dataset.group;
        refilter();
      });
    });

    // Search
    const searchInput = document.getElementById('el-search');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        searchQuery = searchInput.value;
        // Update clear button visibility
        const controls = document.getElementById('el-controls');
        if (controls) {
          const clearBtn = controls.querySelector('.el-search-clear');
          if (searchQuery && !clearBtn) {
            const btn = document.createElement('button');
            btn.className = 'el-search-clear';
            btn.id = 'el-search-clear';
            btn.textContent = '✕';
            searchInput.parentNode.appendChild(btn);
            btn.addEventListener('click', () => { searchQuery = ''; searchInput.value = ''; btn.remove(); refilter(); });
          } else if (!searchQuery && clearBtn) {
            clearBtn.remove();
          }
        }
        refilter();
      });
    }

    // Clear button (if rendered)
    const clearBtn = document.getElementById('el-search-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        searchQuery = '';
        const si = document.getElementById('el-search');
        if (si) si.value = '';
        clearBtn.remove();
        refilter();
      });
    }
  }

  // Clear button
  document.getElementById('el-clear-btn')?.addEventListener('click', async () => {
    await apiDelete('/api/events');
    allEvents = [];
    await load();
  });

  await load();

  const pollTimer = setInterval(load, 5000);
  window.addEventListener('route-change', () => clearInterval(pollTimer), { once: true });

  // WS events trigger immediate reload
  const wsHandler = () => load();
  window.addEventListener('ws-event', wsHandler);
  window.addEventListener('route-change', () => window.removeEventListener('ws-event', wsHandler), { once: true });
}
