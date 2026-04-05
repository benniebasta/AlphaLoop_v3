/**
 * AlphaLoop™ Sound Effects — Web Audio API synthesizer.
 * No audio files required. AudioContext is created lazily on first use
 * (satisfies browser autoplay policy — requires a prior user gesture).
 *
 * Preferences are persisted in localStorage:
 *   sounds_enabled         — global mute toggle  (default: true)
 *   sounds_volume          — master volume 0–1   (default: 0.6)
 *   sounds_trade_open      — per-event toggle    (default: true)
 *   sounds_trade_close     — per-event toggle    (default: true)
 *   sounds_seedlab         — per-event toggle    (default: true)
 *   sounds_evolution       — per-event toggle    (default: true)
 */

let _ctx = null;

function getCtx() {
  if (!_ctx) {
    _ctx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (_ctx.state === 'suspended') _ctx.resume();
  return _ctx;
}

// ── Preference helpers ────────────────────────────────────────────────────────

export function isGloballyEnabled() {
  return localStorage.getItem('sounds_enabled') !== 'false';
}

export function getVolume() {
  return parseFloat(localStorage.getItem('sounds_volume') ?? '0.6');
}

export function isEventEnabled(key) {
  return localStorage.getItem(`sounds_${key}`) !== 'false';
}

export function setSoundsEnabled(val) {
  localStorage.setItem('sounds_enabled', val ? 'true' : 'false');
}

export function setVolume(val) {
  localStorage.setItem('sounds_volume', String(Math.max(0, Math.min(1, val))));
}

export function setEventEnabled(key, val) {
  localStorage.setItem(`sounds_${key}`, val ? 'true' : 'false');
}

// ── Core tone engine ──────────────────────────────────────────────────────────

/**
 * Play a single synthesized tone.
 * @param {number} freq     - Frequency in Hz
 * @param {string} type     - OscillatorNode type
 * @param {number} delay    - Seconds from now to start
 * @param {number} duration - Tone length in seconds
 * @param {number} gain     - Peak volume before master volume (0–1)
 */
function tone(freq, type, delay, duration, gain = 0.25) {
  const ac  = getCtx();
  const osc = ac.createOscillator();
  const env = ac.createGain();

  osc.connect(env);
  env.connect(ac.destination);

  osc.type = type;
  osc.frequency.setValueAtTime(freq, ac.currentTime + delay);

  const masterGain = gain * getVolume();
  env.gain.setValueAtTime(0, ac.currentTime + delay);
  env.gain.linearRampToValueAtTime(masterGain, ac.currentTime + delay + 0.01);
  env.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + delay + duration);

  osc.start(ac.currentTime + delay);
  osc.stop(ac.currentTime + delay + duration + 0.05);
}

// ── Public sound effects ──────────────────────────────────────────────────────

/** Trade opened — ascending two-note ping (E5 → G#5). */
export function playTradeOpened() {
  if (!isGloballyEnabled() || !isEventEnabled('trade_open')) return;
  tone(659.25, 'sine', 0.00, 0.30, 0.28);
  tone(830.61, 'sine', 0.12, 0.40, 0.22);
}

/** Trade closed in profit — bright ascending trio (E5 → G5 → C6). */
export function playTradeClosedProfit() {
  if (!isGloballyEnabled() || !isEventEnabled('trade_close_profit')) return;
  tone(659.25,  'sine', 0.00, 0.20, 0.26);   // E5
  tone(783.99,  'sine', 0.14, 0.20, 0.26);   // G5
  tone(1046.50, 'sine', 0.28, 0.50, 0.30);   // C6
}

/** Trade closed at a loss — soft descending trio (G4 → E4 → C4). */
export function playTradeClosedLoss() {
  if (!isGloballyEnabled() || !isEventEnabled('trade_close_loss')) return;
  tone(392.00, 'sine', 0.00, 0.25, 0.18);    // G4
  tone(329.63, 'sine', 0.16, 0.25, 0.15);    // E4
  tone(261.63, 'sine', 0.32, 0.55, 0.12);    // C4
}

/** SeedLab run completed — four-note success fanfare (C5→E5→G5→C6). */
export function playSeedLabDone() {
  if (!isGloballyEnabled() || !isEventEnabled('seedlab')) return;
  tone(523.25, 'sine', 0.00, 0.22, 0.28);
  tone(659.25, 'sine', 0.18, 0.22, 0.28);
  tone(783.99, 'sine', 0.36, 0.22, 0.28);
  tone(1046.50, 'sine', 0.54, 0.55, 0.32);
}

/** Strategy evolution — five-note ascending arpeggio (C5→E5→G5→B5→C6). */
export function playEvolution() {
  if (!isGloballyEnabled() || !isEventEnabled('evolution')) return;
  [523.25, 659.25, 783.99, 987.77, 1046.50].forEach((freq, i) => {
    tone(freq, 'sine', i * 0.09, 0.28, 0.24);
  });
}
