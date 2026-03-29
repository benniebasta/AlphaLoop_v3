/**
 * AI Model Hub — centralized AI configuration page.
 *
 * Section A: Provider status cards (key status + test buttons)
 * Section B: Model catalog table (21 built-in models)
 * Section C: Default role assignments (signal, validator, research, autolearn)
 */
import { apiGet, apiPost, apiPut } from '../api.js';

const PROVIDERS = [
  { id: 'gemini',    key: 'GEMINI_API_KEY',    label: 'Google Gemini',    icon: '✦', color: '#3b82f6', model: 'gemini-2.5-flash' },
  { id: 'anthropic', key: 'ANTHROPIC_API_KEY',  label: 'Anthropic Claude', icon: '◆', color: '#8b5cf6', model: 'claude-sonnet-4-6' },
  { id: 'openai',    key: 'OPENAI_API_KEY',     label: 'OpenAI',           icon: '⬡', color: '#22c55e', model: 'gpt-4o-mini' },
  { id: 'deepseek',  key: 'DEEPSEEK_API_KEY',   label: 'DeepSeek',         icon: '◉', color: '#14b8a6', model: 'deepseek-chat' },
  { id: 'xai',       key: 'XAI_API_KEY',        label: 'xAI / Grok',      icon: '✕', color: '#f59e0b', model: 'grok-3-mini' },
  { id: 'qwen',      key: 'QWEN_API_KEY',       label: 'Qwen',            icon: '◈', color: '#ef4444', model: 'Qwen/Qwen2.5-7B-Instruct-Turbo' },
  { id: 'ollama',    key: null,                  label: 'Ollama (Local)',   icon: '🖥', color: '#64748b', model: 'qwen2.5:7b' },
];

const ROLE_KEYS = [
  { role: 'default_signal_model',    label: 'Signal Model',    desc: 'Generates trade signals. Used when strategy doesn\'t override.' },
  { role: 'default_validator_model', label: 'Validator Model',  desc: 'Validates signals before execution.' },
  { role: 'default_research_model',  label: 'Research Model',   desc: 'Analyzes trade performance and suggests improvements.' },
  { role: 'default_autolearn_model', label: 'Autolearn Model',  desc: 'Optimizes parameters via auto-learning loop.' },
];

export async function render(container) {
  container.innerHTML = `<div class="page-title">AI Model Hub</div><div class="hub-loading">Loading...</div>`;

  // Load data in parallel
  let settings = {}, models = [], keysStatus = {};
  try {
    const [settingsData, modelData, hubData] = await Promise.all([
      apiGet('/api/settings'),
      apiGet('/api/test/models').catch(() => ({ models: [] })),
      apiGet('/api/ai-hub').catch(() => ({ api_keys_configured: {} })),
    ]);
    settings = settingsData.settings || {};
    models = modelData.models || [];
    keysStatus = hubData.api_keys_configured || {};
  } catch (err) {
    container.innerHTML = `<div class="page-title">AI Model Hub</div><div class="page-error">${err.message}</div>`;
    return;
  }

  container.innerHTML = `
    <div class="page-title">🧠 AI Model Hub</div>

    <!-- Section A: Provider Status -->
    <div class="section-label">Provider Connections</div>
    <div class="provider-grid">
      ${PROVIDERS.map(p => {
        const hasKey = p.key ? !!settings[p.key] : true;
        return `
          <div class="provider-card ${hasKey ? '' : 'unconfigured'}">
            <div class="provider-icon" style="background:${p.color}22;color:${p.color}">${p.icon}</div>
            <div class="provider-info">
              <div class="provider-name">${p.label}</div>
              <span class="badge ${hasKey ? 'badge-green' : 'badge-muted'}" style="font-size:0.65rem">${hasKey ? '✓ Key set' : '✗ No key'}</span>
            </div>
            <button class="btn btn-sm hub-test-btn" data-provider="${p.id}" data-model="${p.model}" style="font-size:0.7rem;padding:2px 8px">Test</button>
            <span class="hub-test-result" data-result="${p.id}" style="font-size:0.7rem"></span>
          </div>`;
      }).join('')}
    </div>

    <!-- Section B: Model Catalog -->
    <div class="section-label" style="margin-top:20px">Model Catalog <span style="color:var(--muted);font-size:0.75rem">(${models.length} models)</span></div>
    <div class="card" style="padding:0;overflow:auto;max-height:300px">
      <table class="data-table" style="margin:0;font-size:0.78rem">
        <thead><tr>
          <th>Provider</th><th>Model ID</th><th>Display Name</th>
        </tr></thead>
        <tbody>
          ${models.map(m => `
            <tr>
              <td><span style="color:${(PROVIDERS.find(p => p.id === m.provider) || {}).color || 'var(--muted)'}">${m.provider}</span></td>
              <td style="font-family:monospace;font-size:0.72rem">${m.id}</td>
              <td>${m.display_name}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>

    <!-- Section C: Default Role Assignments -->
    <div class="section-label" style="margin-top:20px">Default Role Assignments</div>
    <div class="card">
      <div style="color:var(--muted);font-size:0.75rem;margin-bottom:0.75rem">
        Global defaults — each strategy card can override these individually.
      </div>
      ${ROLE_KEYS.map(r => {
        const val = settings[r.role] || '';
        const opts = models.map(m =>
          `<option value="${m.id}" ${val === m.id ? 'selected' : ''}>${m.display_name} (${m.id})</option>`
        ).join('');
        const hasVal = val && models.some(m => m.id === val);
        const customOpt = val && !hasVal ? `<option value="${val}" selected>${val}</option>` : '';
        return `
          <div class="field-row" style="margin-bottom:0.75rem">
            <div class="field-label">${r.label}</div>
            <div class="field-control">
              <select class="field-input" data-role="${r.role}">
                <option value="">— Not set (use hub default) —</option>
                ${customOpt}${opts}
              </select>
              <div class="field-desc">${r.desc}</div>
            </div>
          </div>`;
      }).join('')}
      <div style="display:flex;align-items:center;gap:0.75rem;margin-top:0.5rem">
        <button class="btn btn-primary" id="hub-save-roles">Save Defaults</button>
        <span id="hub-save-hint" style="font-size:0.8rem"></span>
      </div>
    </div>
  `;

  // Wire provider test buttons
  container.querySelectorAll('.hub-test-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const provider = btn.dataset.provider;
      const model = btn.dataset.model;
      const resultEl = container.querySelector(`[data-result="${provider}"]`);
      btn.disabled = true; btn.textContent = '...';
      resultEl.textContent = '';
      try {
        const endpoint = provider === 'ollama' ? '/api/test/ollama' : '/api/test/ai-key';
        const body = provider === 'ollama' ? {} : { provider, model };
        const res = await apiPost(endpoint, body);
        resultEl.textContent = res.success ? '✓' : '✗';
        resultEl.style.color = res.success ? 'var(--green)' : 'var(--red)';
        resultEl.title = res.message || '';
      } catch (e) {
        resultEl.textContent = '✗';
        resultEl.style.color = 'var(--red)';
        resultEl.title = e.message;
      }
      btn.disabled = false; btn.textContent = 'Test';
    });
  });

  // Wire save defaults
  document.getElementById('hub-save-roles').addEventListener('click', async () => {
    const updates = {};
    container.querySelectorAll('[data-role]').forEach(el => {
      updates[el.dataset.role] = el.value;
    });
    try {
      await apiPut('/api/settings', { settings: updates });
      const hint = document.getElementById('hub-save-hint');
      hint.textContent = '✓ Saved'; hint.style.color = 'var(--green)';
      setTimeout(() => { hint.textContent = ''; }, 2500);
      window.showToast('Default roles saved');
    } catch (err) {
      window.showToast(err.message, 'error');
    }
  });
}
