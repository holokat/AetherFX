/* AetherFX Studio frontend.  Vanilla ES2018, no build step, no CDN.
 *
 * Contract with the server (aetherfx/studio/server.py):
 *   - every mutation is followed by GET /api/effect, which is the single
 *     source of truth for the graph, timeline, statistics and diagnostics;
 *   - every fetch goes through api()/apiRaw() so errors become toasts instead
 *     of silent dead UI;
 *   - the DOM is built with el() rather than innerHTML, so engine strings
 *     (node ids, diagnostics, agent prose) can never be interpreted as markup.
 */
'use strict';

/* ====================================================================== *
 * small helpers
 * ====================================================================== */

function el(tag, attrs) {
  var node = document.createElement(tag);
  if (attrs) {
    for (var key in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, key)) continue;
      var value = attrs[key];
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = String(value);
      else if (key === 'title') node.title = String(value);
      else if (key === 'value') node.value = value;
      else if (key === 'checked') node.checked = !!value;
      else if (key === 'disabled') node.disabled = !!value;
      else if (key === 'data') { for (var d in value) node.dataset[d] = value[d]; }
      else if (key.slice(0, 2) === 'on' && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, String(value));
    }
  }
  for (var i = 2; i < arguments.length; i++) {
    var kid = arguments[i];
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) { for (var j = 0; j < kid.length; j++) if (kid[j]) node.appendChild(kid[j]); continue; }
    node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function $(id) { return document.getElementById(id); }

function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); return node; }

function num(value, digits) {
  if (value === null || value === undefined || isNaN(value)) return '-';
  var n = Number(value);
  return digits === undefined ? String(n) : n.toFixed(digits);
}

function shorten(text, limit) {
  text = String(text === null || text === undefined ? '' : text);
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

function ago(seconds) {
  if (!seconds) return '';
  var delta = Date.now() / 1000 - seconds;
  if (delta < 60) return 'just now';
  if (delta < 3600) return Math.round(delta / 60) + 'm ago';
  if (delta < 86400) return Math.round(delta / 3600) + 'h ago';
  return Math.round(delta / 86400) + 'd ago';
}

/* ====================================================================== *
 * api
 * ====================================================================== */

function ApiError(message, status, detail) {
  this.name = 'ApiError';
  this.message = message || 'request failed';
  this.status = status || 0;
  this.detail = detail || null;
}
ApiError.prototype = Object.create(Error.prototype);

function apiRaw(path, options) {
  var opts = {};
  options = options || {};
  for (var key in options) opts[key] = options[key];
  opts.headers = opts.headers || {};
  if (opts.body !== undefined && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body);
    opts.headers['Content-Type'] = 'application/json';
    opts.method = opts.method || 'POST';
  }
  return fetch(path, opts).then(function (res) {
    if (res.ok) return res;
    var type = res.headers.get('content-type') || '';
    if (type.indexOf('json') >= 0) {
      return res.json().then(function (data) {
        var err = (data && data.error) || {};
        throw new ApiError(err.message || res.statusText, res.status, err);
      }, function () {
        throw new ApiError(res.status + ' ' + res.statusText, res.status, null);
      });
    }
    throw new ApiError(res.status + ' ' + res.statusText, res.status, null);
  }, function (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError('cannot reach the studio server (' + (err && err.message ? err.message : 'network error') + ')', 0, null);
  });
}

function api(path, options) {
  return apiRaw(path, options).then(function (res) {
    if (res.status === 204) return null;
    return res.json();
  });
}

/* Run an async action, turning any failure into a toast. */
function guard(promise, context) {
  return promise.catch(function (err) {
    if (err && err.name === 'AbortError') return null;
    var message = err && err.message ? err.message : String(err);
    if (err && err.detail && err.detail.node) message += '  [' + err.detail.node + (err.detail.param ? '.' + err.detail.param : '') + ']';
    toast((context ? context + ': ' : '') + message, 'error');
    return null;
  });
}

var toastTimers = [];
function toast(message, kind) {
  var box = $('toasts');
  if (!box) return;
  var node = el('div', { class: 'toast ' + (kind || ''), text: String(message) });
  node.addEventListener('click', function () { if (node.parentNode) node.parentNode.removeChild(node); });
  box.appendChild(node);
  var timer = setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, kind === 'error' ? 9000 : 4500);
  toastTimers.push(timer);
  while (box.children.length > 6) box.removeChild(box.firstChild);
}

/* ====================================================================== *
 * state
 * ====================================================================== */

var S = {
  status: null,
  data: null,            /* the last GET /api/effect payload */
  selected: null,        /* selected node id */
  node: null,            /* the last GET /api/node/<id> payload */
  lists: { examples: [], saved: [], open: [] },

  fps: 24,
  size: 384,
  loop: true,
  index: 0,
  playing: false,
  raf: null,
  lastTick: 0,

  preview: null,         /* {frames:[objectURL], fps, count, start, duration} */
  previewStale: true,
  previewBusy: false,

  frameTimer: null,
  frameToken: 0,
  frameAbort: null,
  frameUrl: null,

  job: null,             /* {id, since, timer, rendered} */
  statusTimer: null
};

var NODE_GLYPH = {
  emitter: '✳', particle_system: '∷', force: '↯', field: '▦',
  volume: '▣', mesh: '◆', curve: '∿', trail: '≈', beam: '╱',
  light: '☀', decal: '▢', material: '◐', event: '⚡', noise: '≋',
  collider: '⊟', camera: '◎', post_effect: '✦', texture: '▩'
};

/* Parameter groups pulled out of the flat list (docs/VOCABULARY.md). */
var TRANSFORM_PARAMS = ['position', 'rotation', 'scale'];
var WINDOW_PARAMS = ['start_time', 'duration', 'phase'];
var JSON_TYPES = { curve: 1, gradient: 1, float_list: 1, vec3_list: 1, json: 1 };
var VEC_SIZES = { vec2: 2, vec3: 3, vec4: 4 };
var COMPONENT_LABELS = ['x', 'y', 'z', 'w'];
