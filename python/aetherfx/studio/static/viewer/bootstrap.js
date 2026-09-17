/* Boots the GPU viewer and publishes `window.aetherViewer` for app.js.
 *
 * app.js is a plain script and deliberately knows nothing about three.js; it
 * only sees this small facade.  When WebGL2 is missing, `available` is false
 * and app.js keeps its original image-based viewport.
 *
 * The viewport badge (#gl-mode) and the note box are rendered by app.js, which
 * is a classic script and has therefore already run by the time this module
 * executes.  One renderer covers both what this module knows (which pipeline
 * the context could actually build, a lost context, a dead socket) and the one
 * thing it cannot report - this module never running at all, which app.js
 * detects with its own watchdog.
 */

import { createGLViewer } from './viewer.js';

/* A socket blip on a server restart reconnects well inside this; only a stream
 * that stays down is worth putting on the badge. */
const RECONNECT_GRACE_MS = 3000;

function setMode(tone, text, detail) {
  if (typeof window.aetherSetGlMode === 'function') window.aetherSetGlMode(tone, text, detail);
}

function note(message) {
  if (typeof window.aetherGlNote === 'function') window.aetherGlNote(message);
}

/* Badge-sized: the full text goes in the tooltip and the console. */
function shortReason(reason) {
  const text = String(reason || 'WebGL unavailable').replace(/\s+/g, ' ').trim();
  return text.length > 44 ? text.slice(0, 43) + '…' : text;
}

function boot() {
  const canvas = document.getElementById('gl-viewport');
  const container = document.getElementById('viewport');
  if (!canvas || !container) return;

  const api = {
    available: false,
    reason: null,
    mode: null,
    onTime: null,
    onStats: null,
    onStatus: null
  };

  /* Badge state.  `base` is the pipeline the context settled on; the transient
   * states (a lost context, a stream that has stayed down) win while they last
   * and fall back to `base` when they clear. */
  let base = null;
  let lost = false;
  let reconnecting = false;
  let reconnectTimer = null;

  function paint() {
    if (!base) return;
    if (lost) setMode('warn', 'context lost', 'the GPU dropped this page\'s WebGL context - rebuilding the pipeline');
    else if (reconnecting) setMode('warn', 'reconnecting…', 'the frame stream socket is down - retrying');
    else setMode(base.tone, base.label, base.detail);
  }

  function handleStatus(status) {
    if (!status) return;
    if (status.kind === 'context_lost') {
      lost = true;
      paint();
    } else if (status.kind === 'context_restored') {
      lost = false;
      if (status.mode) base = status.mode;
      paint();
    } else if (status.kind === 'disconnected') {
      if (!reconnectTimer && !reconnecting) {
        reconnectTimer = setTimeout(function () {
          reconnectTimer = null;
          reconnecting = true;
          console.warn('[aetherfx viewer] frame stream has been down for ' + (RECONNECT_GRACE_MS / 1000) + ' s - retrying');
          paint();
        }, RECONNECT_GRACE_MS);
      }
    } else if (status.kind === 'connected') {
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      if (reconnecting) { reconnecting = false; paint(); }
    }
  }

  const gl = createGLViewer({
    canvas: canvas,
    container: container,
    onTime: function (time, frame, duration) { if (api.onTime) api.onTime(time, frame, duration); },
    onStats: function (stats) {
      const line = document.getElementById('gl-stats');
      if (line) {
        line.textContent = Math.round(stats.fps) + ' fps · ' +
          stats.particles.toLocaleString() + ' particles · ' + stats.calls + ' draws';
      }
      if (api.onStats) api.onStats(stats);
    },
    onStatus: function (status) {
      handleStatus(status);
      if (api.onStatus) api.onStatus(status);
    }
  });

  if (!gl.available) {
    canvas.hidden = true;
    api.reason = gl.reason;
    console.warn('[aetherfx viewer] GPU viewer unavailable: ' + gl.reason + ' - showing CPU reference frames instead');
    setMode('bad', 'CPU fallback · ' + shortReason(gl.reason), 'GPU viewer unavailable: ' + gl.reason);
    note('GPU viewer unavailable (' + gl.reason + ') - showing rendered frames instead.');
  } else {
    api.available = true;
    base = gl.renderMode || { tone: 'ok', label: 'GPU', detail: 'GPU' };
    api.mode = base.detail;
    paint();
    if (base.tone === 'ok') console.info('[aetherfx viewer] ' + base.detail);
    else console.warn('[aetherfx viewer] ' + base.detail + ' - the best pipeline this WebGL2 context supports');
    ['play', 'pause', 'seek', 'step', 'reload', 'reloadAndFrame', 'setStage',
     'setResolution', 'resetView', 'snapshot', 'duration', 'camera', 'dispose'].forEach(function (name) {
      api[name] = gl[name];
    });
    api.stageDefaults = gl.stageDefaults;
    api.gl = gl.viewer;                 /* the three.js side, for console poking */
  }

  window.aetherViewer = api;
  window.dispatchEvent(new CustomEvent('aether-viewer-ready', { detail: api }));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
