/* Boots the GPU viewer and publishes `window.aetherViewer` for app.js.
 *
 * app.js is a plain script and deliberately knows nothing about three.js; it
 * only sees this small facade.  When WebGL2 is missing, `available` is false
 * and app.js keeps its original image-based viewport.
 */

import { createGLViewer } from './viewer.js';

function note(container, message) {
  const box = document.createElement('div');
  box.className = 'gl-note';
  box.textContent = message;
  container.appendChild(box);
}

function boot() {
  const canvas = document.getElementById('gl-viewport');
  const container = document.getElementById('viewport');
  if (!canvas || !container) return;

  const api = {
    available: false,
    reason: null,
    onTime: null,
    onStats: null,
    onStatus: null
  };

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
    onStatus: function (status) { if (api.onStatus) api.onStatus(status); }
  });

  if (!gl.available) {
    canvas.hidden = true;
    api.reason = gl.reason;
    note(container, 'GPU viewer unavailable (' + gl.reason + ') - showing rendered frames instead.');
  } else {
    api.available = true;
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
