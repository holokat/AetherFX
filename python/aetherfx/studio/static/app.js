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
    if (typeof FormData !== 'undefined' && opts.body instanceof FormData) {
      opts.method = opts.method || 'POST';        /* the browser writes the multipart boundary */
    } else {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
      opts.method = opts.method || 'POST';
    }
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
  lists: { examples: [], saved: [], community: [], open: [] },
  view: 'studio',        /* which top-nav view is showing: 'studio' or 'community' */
  community: null,       /* the last GET /api/community payload */
  librarySearch: '',     /* live filters, kept per view in sessionStorage */
  communitySearch: '',

  fps: 24,
  speed: 1,              /* playback speed multiplier (1 = real time) */
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

  paramMessage: null,    /* inline result of the last parameter commit */
  controls: null,        /* the last GET /api/controls payload */
  controlPending: 0,     /* in-flight control commits; > 0 means a slider is being dragged */
  uiMutating: false,     /* suppress the status poll's external-change refresh */
  job: null,             /* {id, since, timer, refsShown} */
  statusTimer: null,

  attachments: [],       /* reference images: {key, id, name, thumb_url, url, uploading, localUrl} */
  exportTargets: null,   /* the last GET /api/export/targets payload */
  exportBusy: false,

  gl: null,              /* window.aetherViewer facade when WebGL2 is available */
  glTime: 0,             /* the time of the last frame the GPU viewer drew */
  seekGuardUntil: 0,     /* ignore viewer time updates right after a manual seek */
  referenceUrl: null     /* object URL of the CPU reference thumbnail */
};

/* Inline icons (visual only; the transport button swaps between them). */
var ICON_PLAY = '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true">' +
  '<path d="M5.4 3.5a.55.55 0 0 1 .85-.46l6.1 4.5a.55.55 0 0 1 0 .92l-6.1 4.5a.55.55 0 0 1-.85-.46z" fill="currentColor" stroke="none"/></svg>';
var ICON_PAUSE = '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true">' +
  '<rect x="4.6" y="3.4" width="2.5" height="9.2" rx="1.1" fill="currentColor" stroke="none"/>' +
  '<rect x="8.9" y="3.4" width="2.5" height="9.2" rx="1.1" fill="currentColor" stroke="none"/></svg>';

/* ====================================================================== *
 * GPU viewer bridge
 *
 * static/viewer/bootstrap.js publishes window.aetherViewer.  When WebGL2 is
 * available the viewport is a live three.js canvas fed by /ws/stream and the
 * CPU image path is used only for the side-by-side reference render; when it
 * is not, every call below short-circuits and the studio behaves as before.
 * ====================================================================== */

function glActive() { return !!(S.gl && S.gl.available); }

/* ---------------------------------------------------------------------- *
 * viewport mode badge (#gl-mode)
 *
 * The viewport has two very different paths behind it and used to say nothing
 * about which one was drawing, so a tab that quietly dropped to the CPU
 * reference frames - a 404 on a vendored three.js file, a lost GL context, a
 * dead stream socket - just looked like the renderer had regressed.  The badge
 * is the answer: one line, always visible, and a console.warn with the reason
 * whenever it is not the full GPU pipeline.
 *
 * viewer/bootstrap.js drives it for everything the viewer knows about; the
 * watchdog below covers the one case bootstrap.js cannot report, which is
 * bootstrap.js never running.
 * ---------------------------------------------------------------------- */

var GL_MODE_TONES = { ok: 1, warn: 1, bad: 1 };

function setGlMode(tone, text, detail) {
  var box = $('gl-mode');
  if (!box) return;
  box.hidden = false;
  box.className = 'gl-mode mono ' + (GL_MODE_TONES[tone] ? tone : 'warn');
  box.title = detail || text;
  var label = box.querySelector('.gl-mode-text');
  if (label) label.textContent = text;
}

/* The longer explanation under the viewport; one box, rewritten in place. */
function glNote(message) {
  var viewport = $('viewport');
  if (!viewport) return;
  var box = viewport.querySelector('.gl-note');
  if (!box) {
    box = el('div', { class: 'gl-note' });
    viewport.appendChild(box);
  }
  box.textContent = message;
}

window.aetherSetGlMode = setGlMode;
window.aetherGlNote = glNote;

/* If the viewer module never publishes window.aetherViewer nothing else in the
 * page notices: app.js simply keeps using the CPU image path, silently.  Watch
 * for the module's load error (a missing vendored file fires `error` at the
 * <script>, which reaches window in the capture phase) and, because a module
 * can also fail in ways that raise nothing at all, time it out. */
function watchViewerBoot() {
  var settled = false;

  function fail(why) {
    if (settled || window.aetherViewer) return;     /* bootstrap.js owns the badge once it runs */
    settled = true;
    console.warn('[aetherfx studio] GPU viewer did not start: ' + why + ' - showing CPU reference frames instead.');
    setGlMode('bad', 'CPU fallback · ' + why, 'GPU viewer did not start: ' + why);
    glNote('GPU viewer did not start (' + why + ') - showing rendered frames instead.');
  }

  window.addEventListener('error', function (event) {
    var target = event && event.target;
    if (!target || target === window || target.nodeName !== 'SCRIPT') return;
    var src = String(target.src || '');
    if (src.indexOf('/static/viewer/') < 0 && src.indexOf('/static/vendor/') < 0) return;
    fail('viewer module failed to load');
  }, true);

  var arm = function () { setTimeout(function () { fail('viewer module failed to load'); }, 5000); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arm);
  else arm();
}

watchViewerBoot();


/* Show the active effect: the live stream when the GPU viewer is up, the CPU
 * preview sequence otherwise. */
function startViewing() {
  if (!S.data) return;
  if (glActive()) { S.gl.reloadAndFrame(); play(); return; }
  renderPreview(true);
}

function attachViewer(api) {
  if (!api || !api.available || S.gl) return;
  S.gl = api;
  document.body.classList.add('gl-active');

  api.onTime = function (time) {
    S.glTime = time;
    if (Date.now() < S.seekGuardUntil) return;
    var max = timelineMax();
    S.index = Math.max(0, Math.min(Math.round(time * S.fps), max));
    $('frame-slider').value = String(S.index);
    paintSlider();
    updateReadout();
  };
  api.onStatus = function (status) {
    if (status.kind === 'error' && status.error && status.error.code !== 'no_effect') {
      toast('viewer: ' + (status.error.message || status.error.code), 'warn');
    } else if (status.kind === 'state' && S.playing && status.state && !status.state.playing) {
      /* playback ran off the end with loop off */
      S.playing = false;
      setPlayIcon(false);
    } else if (status.kind === 'connected' && S.playing) {
      /* the socket came back (server restart): pick playback up where it was */
      api.play(S.fps, S.loop, S.glTime || 0, S.speed);
    } else if (status.kind === 'context_restored') {
      /* The GPU path is live again.  The CPU image is only ever the fallback
         for a viewer that never came up, so make sure nothing left it showing. */
      document.body.classList.add('gl-active');
      $('viewport-img').hidden = true;
    }
  };

  api.setStage(stageParam());
  api.setResolution($('sel-size').value);

  /* 24 fps is a CPU-preview budget; the live stream runs at the simulation's own 60 Hz */
  if ($('sel-fps').querySelector('option[value="60"]')) {
    S.fps = 60;
    $('sel-fps').value = '60';
    syncTransportRange();
  }

  /* the old "Preview" button becomes the CPU reference comparison */
  var preview = $('btn-preview');
  preview.title = 'Render this frame with the CPU reference renderer and compare';
  var label = preview.querySelector('.lb');
  if (label) label.textContent = 'Reference';
  if (S.data) $('viewport-empty').hidden = true;
}

/* Camera for the CPU reference render: whatever OrbitControls is looking at. */
function glCameraParam() { return glActive() ? S.gl.camera() : cameraParam(); }
function glCameraQuery() { return '&camera=' + encodeURIComponent(JSON.stringify(glCameraParam())); }

function currentTime() { return glActive() ? (S.glTime || 0) : timeAt(S.index); }

/* Render the current time on the CPU and show it in the corner for comparison. */
function renderReference() {
  if (!S.data) { toast('load or create an effect first', 'warn'); return; }
  var vp = $('viewport');
  var width = 368;
  var height = Math.max(120, Math.round(width * (vp.clientHeight || 1) / (vp.clientWidth || 1)));
  var url = '/api/frame?time=' + encodeURIComponent(currentTime().toFixed(3)) +
    '&width=' + width + '&height=' + height + glCameraQuery() + stageQuery();
  $('btn-preview').disabled = true;
  setBusy(true, 'reference render');
  apiRaw(url).then(function (res) { return res.blob(); }).then(function (blob) {
    var objectUrl = URL.createObjectURL(blob);
    if (S.referenceUrl) URL.revokeObjectURL(S.referenceUrl);
    S.referenceUrl = objectUrl;
    $('gl-reference-img').src = objectUrl;
    $('gl-reference').hidden = false;
  }).catch(function (err) {
    toast('reference: ' + (err && err.message ? err.message : 'failed'), 'error');
  }).then(function () {
    $('btn-preview').disabled = false;
    setBusy(false);
  });
}

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

/* ====================================================================== *
 * status header
 * ====================================================================== */

function refreshStatus() {
  return api('/api/status').then(function (status) {
    S.status = status;
    var engine = status.engine || {};
    var dot = $('engine-dot');
    dot.className = 'dot ' + (engine.ok ? 'ok' : 'bad');
    $('engine-text').textContent = engine.ok ? 'engine ready' : 'engine offline';
    $('engine-status').title = engine.ok
      ? 'engine binary: ' + (engine.binary || 'unknown')
      : (engine.error || 'the engine is not answering') + '\nbinary: ' + (engine.binary || 'not found');

    var gen = status.generator || {};
    var genText = $('generator-status');
    genText.textContent = 'generator: ' + (gen.name || 'none') + (gen.available ? '' : ' (unavailable)');
    genText.title = gen.available ? 'ready' : (gen.reason || 'no generator backend configured');
    genText.classList.toggle('off', !gen.available);   /* styled as the status dot */

    var button = $('btn-generate');
    var busy = !!S.job;
    button.disabled = !gen.available || busy;
    button.title = gen.available ? 'Generate an effect from the prompt' : (gen.reason || 'no generator backend configured');
    setEffectName(status.active_effect);
    syncExportButton();
    followExternalChanges(status);
    return status;
  }, function (err) {
    $('engine-dot').className = 'dot bad';
    $('engine-text').textContent = 'studio offline';
    $('engine-status').title = err && err.message ? err.message : 'cannot reach the studio server';
    return null;
  });
}

/* The engine is shared with MCP clients (Claude Code) and generation workers.
 * When the active effect or the revision counter changes without a UI action,
 * reload the graph; when a different effect became active, render its preview. */
var externalSyncPending = false;
function followExternalChanges(status) {
  var activeId = status.active_effect ? status.active_effect.effect_id : null;
  var revision = typeof status.revision === 'number' ? status.revision : null;
  var first = S.revision === undefined;
  var idChanged = S.activeId !== undefined && activeId !== S.activeId;
  var revChanged = !first && revision !== null && revision !== S.revision;
  S.activeId = activeId;
  S.revision = revision;
  if (first || (!idChanged && !revChanged) || externalSyncPending || S.uiMutating) return;
  externalSyncPending = true;
  var work = idChanged ? Promise.resolve(resetPreview()).then(function () { return afterEffectChange(); })
                       : refreshEffect().then(function () { S.previewStale = true; updatePreviewHint(); });
  if (idChanged) work = work.then(function () { return refreshEffects(); });
  work.then(function () {
    externalSyncPending = false;
    if (idChanged && S.data && !S.job) {
      toast('effect changed externally: ' + (status.active_effect && status.active_effect.name), 'ok');
      if (glActive()) S.gl.reloadAndFrame(); else renderPreview(true);
    } else if (glActive()) {
      S.gl.reload();
    }
  }, function () { externalSyncPending = false; });
}

/* The loaded effect's name lives at the top of the right column, above the graph:
 * `#effect-name` holds exactly the name (tools/gl_capture.py and the tests read it),
 * the dot next to it carries the unsaved-changes state. */
function setEffectName(active) {
  var node = $('effect-name');
  var bar = $('effect-bar');
  if (!active) {
    node.textContent = 'no effect';
    bar.title = 'no effect is open';
    node.classList.remove('dirty');
    bar.classList.remove('dirty', 'loaded');
    return;
  }
  node.textContent = active.name || 'untitled';
  node.classList.toggle('dirty', !!active.dirty);   /* styling hook kept for the tests */
  bar.classList.toggle('dirty', !!active.dirty);
  bar.classList.add('loaded');
  bar.title = (active.effect_id || '') + (active.path ? '\n' + active.path : '') +
    (active.dirty ? '\nunsaved changes' : '');
}

/* A community effect keeps its credit next to the name: "by <github name>", linking
 * to the profile.  The author block has already been validated server side. */
function setEffectCredit(source) {
  var chip = $('effect-credit');
  var author = source && source.author;
  if (!author || !author.url) { chip.hidden = true; chip.textContent = ''; return; }
  chip.hidden = false;
  chip.textContent = 'by ' + (author.display || author.github);
  chip.href = author.url;
  chip.title = 'Contributed by ' + (author.display || author.github) + '\n' + author.url;
}

/* ====================================================================== *
 * effect lists (left column)
 * ====================================================================== */

/* ---------------------------------------------------------------------- *
 * live search: filters the already-loaded list, no server round trip
 * ---------------------------------------------------------------------- */

/* Lower case, accents folded, so "teleporté" matches "teleporte". */
function fold(text) {
  text = String(text === null || text === undefined ? '' : text).toLowerCase();
  return text.normalize ? text.normalize('NFD').replace(/[̀-ͯ]/g, '') : text;
}

function searchTerms(query) {
  return fold(query).split(/\s+/).filter(function (term) { return term.length > 0; });
}

/* Everything a query may match: name, file slug, tags, description, author. */
function searchHaystack(item) {
  var author = item.author || {};
  var slug = (item.path || '').split(/[\\/]/).pop().replace(/\.json$/, '');
  return fold([item.name, slug, (item.tags || []).join(' '), item.description,
    author.github, author.name].filter(Boolean).join(' '));
}

/* AND over the terms: every word has to appear somewhere. */
function matchesSearch(item, terms) {
  if (!terms.length) return true;
  var hay = item._hay || (item._hay = searchHaystack(item));
  for (var i = 0; i < terms.length; i++) if (hay.indexOf(terms[i]) === -1) return false;
  return true;
}

/* The name with matched substrings wrapped in <mark>, built as nodes (never innerHTML). */
function highlightName(name, terms) {
  var text = String(name || '');
  if (!terms.length) return [document.createTextNode(text)];
  var folded = fold(text);
  var hits = [];
  terms.forEach(function (term) {
    var at = folded.indexOf(term);
    while (at !== -1) { hits.push([at, at + term.length]); at = folded.indexOf(term, at + 1); }
  });
  if (!hits.length) return [document.createTextNode(text)];
  hits.sort(function (a, b) { return a[0] - b[0]; });
  var merged = [hits[0]];
  hits.slice(1).forEach(function (span) {
    var last = merged[merged.length - 1];
    if (span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
    else merged.push(span);
  });
  var out = [];
  var cursor = 0;
  merged.forEach(function (span) {
    if (span[0] > cursor) out.push(document.createTextNode(text.slice(cursor, span[0])));
    out.push(el('mark', { text: text.slice(span[0], span[1]) }));
    cursor = span[1];
  });
  if (cursor < text.length) out.push(document.createTextNode(text.slice(cursor)));
  return out;
}

/* One search field: input, clear button, "/" to focus, Esc to clear, remembered per view. */
function wireSearch(inputId, clearId, storageKey, onChange) {
  var input = $(inputId);
  var clear = $(clearId);
  if (!input) return function () { return ''; };
  var saved = '';
  try { saved = window.sessionStorage.getItem(storageKey) || ''; } catch (err) { saved = ''; }
  input.value = saved;

  function apply() {
    var query = input.value;
    clear.hidden = !query;
    try { window.sessionStorage.setItem(storageKey, query); } catch (err) { /* private mode */ }
    onChange(query);
  }
  input.addEventListener('input', apply);
  input.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') { ev.stopPropagation(); input.value = ''; apply(); input.blur(); }
  });
  clear.addEventListener('click', function () { input.value = ''; apply(); input.focus(); });
  clear.hidden = !input.value;
  return function () { return input.value; };
}

/* ---------------------------------------------------------------------- *
 * the Library: Core and Mine, each collapsible with an "n of m" count
 * ---------------------------------------------------------------------- */

var LIB_SECTIONS = [
  { id: 'core', list: 'list-core', count: 'count-core', empty: 'no built-in effects' },
  { id: 'mine', list: 'list-mine', count: 'count-mine', empty: 'nothing saved yet - press Save as' }
];

function renderLibrary() {
  var lists = S.lists || {};
  var source = (lists.working && lists.working.source) || null;
  var items = lists.library || [];
  var terms = searchTerms(S.librarySearch || '');
  var shown = 0;

  LIB_SECTIONS.forEach(function (section) {
    var all = items.filter(function (item) { return (item.section || 'core') === section.id; });
    var visible = all.filter(function (item) { return matchesSearch(item, terms); });
    shown += visible.length;
    $(section.count).textContent = visible.length === all.length
      ? String(all.length) : visible.length + ' of ' + all.length;
    renderFileList($(section.list), visible, terms.length ? 'no match' : section.empty, function (item) {
      return {
        label: item.name,
        labelNodes: highlightName(item.name, terms),
        meta: item.section === 'mine' ? ago(item.modified) : 'built-in',
        active: !!(source && source.path === item.path),
        onclick: function () { openLibraryItem(item); }
      };
    });
  });

  var empty = $('lib-empty');
  if (terms.length && !shown) {
    clear(empty);
    empty.hidden = false;
    empty.appendChild(document.createTextNode('No effects match "' + S.librarySearch + '" '));
    empty.appendChild(el('button', {
      class: 'linkish', type: 'button', text: 'clear',
      onclick: function () { $('lib-search').value = ''; $('lib-search').dispatchEvent(new Event('input')); }
    }));
  } else {
    empty.hidden = true;
  }
}

function refreshEffects() {
  return api('/api/effects').then(function (lists) {
    S.lists = lists;
    setEffectCredit((lists.working && lists.working.source) || null);
    renderLibrary();
    fillTemplateSelect((lists.library || []).filter(function (i) { return i.section === 'core'; }));
    return lists;
  });
}

/* ---------------------------------------------------------------------- *
 * the Community view: contributed effects, credit, open a working copy
 * ---------------------------------------------------------------------- */

var REPO_URL = 'https://github.com/holokat/AetherFX';

function showView(view) {
  var community = view === 'community';
  S.view = community ? 'community' : 'studio';
  $('layout').hidden = community;
  $('community-view').hidden = !community;
  document.body.classList.toggle('community-open', community);
  $('nav-studio').classList.toggle('is-active', !community);
  $('nav-community').classList.toggle('is-active', community);
  $('nav-studio').setAttribute('aria-current', community ? 'false' : 'page');
  $('nav-community').setAttribute('aria-current', community ? 'page' : 'false');
  if (community) {
    refreshCommunity();
    setTimeout(function () { $('com-search').focus(); }, 0);
  } else {
    onViewportResize();
  }
}

function refreshCommunity() {
  return api('/api/community').then(function (data) {
    S.community = data;
    renderCommunity();
    return data;
  }, function (err) {
    S.community = { local: [], remote: [], error: err && err.message };
    renderCommunity();
  });
}

function communityEntries() {
  var data = S.community || {};
  return (data.local || []).concat(data.remote || []);
}

function renderCommunity() {
  var list = $('community-list');
  var entries = communityEntries();
  var terms = searchTerms(S.communitySearch || '');
  var visible = entries.filter(function (item) { return matchesSearch(item, terms); });
  clear(list);
  visible.forEach(function (item) { list.appendChild(communityCard(item, terms)); });

  $('com-count').textContent = entries.length
    ? (visible.length === entries.length
        ? entries.length + ' effect' + (entries.length === 1 ? '' : 's')
        : visible.length + ' of ' + entries.length + ' effects')
    : '';

  var empty = $('community-empty');
  clear(empty);
  if (!entries.length) {
    empty.hidden = false;
    empty.appendChild(document.createTextNode('No community effects yet. '));
    empty.appendChild(el('a', { href: REPO_URL + '/blob/main/CONTRIBUTING.md', target: '_blank',
      rel: 'noopener noreferrer', text: 'Contribute the first one' }));
    empty.appendChild(document.createTextNode('.'));
  } else if (!visible.length) {
    empty.hidden = false;
    empty.appendChild(document.createTextNode('No effects match "' + S.communitySearch + '" '));
    empty.appendChild(el('button', {
      class: 'linkish', type: 'button', text: 'clear',
      onclick: function () { $('com-search').value = ''; $('com-search').dispatchEvent(new Event('input')); }
    }));
  } else {
    empty.hidden = true;
  }
}

function communityCard(item, terms) {
  var author = item.author || null;
  var title = el('h3', { class: 'card-title' });
  highlightName(item.name, terms).forEach(function (node) { title.appendChild(node); });

  var meta = el('div', { class: 'card-meta' },
    author ? el('a', {
      class: 'card-by', href: author.url, target: '_blank', rel: 'noopener noreferrer',
      title: 'github.com/' + author.github, text: 'by ' + (author.display || author.github)
    }) : el('span', { class: 'card-by dim', text: 'no credit' }),
    typeof item.duration === 'number' ? el('span', { class: 'card-dur', text: num(item.duration, 1) + ' s' }) : null,
    item.remote ? el('span', { class: 'card-remote', text: 'remote' }) : null);

  var tags = el('div', { class: 'card-tags' },
    (item.tags || []).slice(0, 6).map(function (tag) { return el('span', { class: 'tag', text: tag }); }));

  var open = el('button', {
    class: 'primary sm', type: 'button',
    text: item.remote ? 'Download' : 'Open',
    onclick: function () { item.remote ? downloadCommunity(item) : openCommunity(item); }
  });

  return el('article', { class: 'community-card' },
    title,
    el('p', { class: 'card-desc', text: item.description || 'No description.' }),
    (item.tags || []).length ? tags : null,
    el('div', { class: 'card-foot' }, meta, open));
}

function openCommunity(item) {
  showView('studio');
  loadEffect(item.path);
}

function downloadCommunity(item) {
  toast('downloading "' + item.name + '" and running the contribution check…', 'ok');
  api('/api/community/download', { body: { slug: item.slug } }).then(function (result) {
    toast('downloaded "' + item.name + '"', 'ok');
    return refreshCommunity().then(function () { return refreshEffects(); }).then(function () {
      showView('studio');
      return loadEffect(result.path);
    });
  }).catch(function (err) {
    toast(err && err.message ? err.message : 'download failed', 'error');
  });
}

function currentIsDirty() { return !!(S.status && S.status.active_effect && S.status.active_effect.dirty); }
function openLibraryItem(item) {
  var name = (S.status && S.status.active_effect && S.status.active_effect.name) || 'the current effect';
  if (currentIsDirty()) toast('unsaved changes to "' + name + '" were discarded', 'warn');
  loadEffect(item.path);
}

function renderFileList(list, items, emptyText, make) {
  clear(list);
  if (!items || !items.length) { list.appendChild(el('li', { class: 'empty', text: emptyText })); return; }
  items.forEach(function (item) {
    var spec = make(item);
    /* The label span keeps the plain effect name as its text even when the search
     * highlights part of it, so a "row starting with <name>" selector still works. */
    var label = el('span', { class: 'row-label' });
    (spec.labelNodes || [document.createTextNode(String(spec.label))]).forEach(function (node) {
      label.appendChild(node);
    });
    var row = el('li', { class: spec.active ? 'active' : '', title: item.path || '', onclick: spec.onclick },
      label,
      spec.meta ? el('span', { class: 'meta' + (spec.meta === 'modified' ? ' badge-dirty' : ''), text: spec.meta }) : null);
    list.appendChild(row);
  });
}

function fillTemplateSelect(examples) {
  var select = $('new-template');
  var current = select.value;
  clear(select);
  select.appendChild(el('option', { value: 'empty', text: 'empty' }));
  (examples || []).forEach(function (item) {
    select.appendChild(el('option', { value: item.path || item.name, text: item.name }));
  });
  select.value = current || 'empty';
  if (!select.value) select.value = 'empty';
}

function loadEffect(path) {
  S.paramMessage = null;
  S.activeId = undefined; // let the next status poll adopt the new id silently
  return guard(api('/api/effects/load', { body: { path: path } }).then(function (result) {
    toast('loaded ' + (result.name || path), 'ok');
    resetPreview();
    return afterEffectChange().then(function (r) { if (S.data) startViewing(); return r; });
  }), 'load');
}

function activateEffect(effectId) {
  S.paramMessage = null;
  S.activeId = undefined;
  return guard(api('/api/effects/activate', { body: { effect_id: effectId } }).then(function () {
    resetPreview();
    return afterEffectChange().then(function (r) { if (S.data) startViewing(); return r; });
  }), 'activate');
}

function createEffect() {
  S.paramMessage = null;
  var body = {
    name: $('new-name').value || 'Untitled',
    duration: parseFloat($('new-duration').value) || 2.0,
    template: $('new-template').value || 'empty'
  };
  return guard(api('/api/effects/new', { body: body }).then(function (result) {
    toast('created ' + (result.name || body.name), 'ok');
    $('new-effect-form').open = false;
    resetPreview();
    return afterEffectChange();
  }), 'create');
}

function saveEffect() {
  if (!S.data) { toast('nothing to save', 'warn'); return; }
  var current = (S.status && S.status.active_effect && S.status.active_effect.name) || 'effect';
  var src = (S.lists && S.lists.working && S.lists.working.source) || null;
  var suggestion = (src && src.builtin) ? current + ' copy' : current;
  var name = window.prompt('Save into your library as:', suggestion);
  if (name === null) return;
  name = name.trim();
  if (!name) return;
  api('/api/effects/save', { body: { name: name } }).then(function (r) {
    toast('saved "' + (r.name || name) + '"', 'ok');
    return afterEffectChange();
  }).catch(function (err) { toast(err && err.message ? err.message : 'save failed', 'error'); });
}

function runHistory(which) {
  S.paramMessage = null;
  return guard(api('/api/' + which, { body: {} }).then(function () {
    invalidatePreview();
    return afterEffectChange();
  }), which);
}

/* Refresh everything that depends on the active effect. */
function afterEffectChange() {
  return Promise.all([refreshStatus(), refreshEffects(), refreshEffect()]);
}

/* ====================================================================== *
 * the effect: graph, timeline, statistics, diagnostics
 * ====================================================================== */

function refreshEffect() {
  return apiRaw('/api/effect').then(function (res) { return res.json(); }).then(function (data) {
    S.data = data;
    if (!S.camera || S.cameraEffectId !== S.activeId) { resetCamera(); applyStage(data.stage_defaults); }
    if (glActive()) $('viewport-empty').hidden = true;
    renderPhases(data.timeline);
    renderGraph(data.graph);
    guard(refreshControls(), 'controls');
    renderStatistics(data.statistics);
    renderDiagnostics(data.graph && data.graph.diagnostics);
    syncTransportRange();
    if (S.selected) {
      var nodes = (data.graph && data.graph.nodes) || [];
      var stillThere = nodes.some(function (n) { return n.id === S.selected; });
      if (stillThere) { selectNode(S.selected, true); } else { S.selected = null; S.node = null; renderParams(); }
    }
    if (!glActive() && !S.preview && !S.playing) showCurrentFrame();
    return data;
  }, function (err) {
    if (err && err.status === 404) {
      S.data = null; S.selected = null; S.node = null; S.controls = null;
      renderPhases(null); renderGraph(null); renderStatistics(null); renderDiagnostics(null); renderParams();
      renderControls();
      clearViewport();
      return null;
    }
    throw err;
  });
}

function renderPhases(timeline) {
  var box = clear($('timeline-phases'));
  var phases = (timeline && timeline.phases) || [];
  if (!phases.length) return;
  phases.forEach(function (phase) {
    box.appendChild(el('div', { class: 'phase', title: 'timeline phase' },
      el('span', { text: phase.name }),
      el('span', { class: 'span', text: num(phase.start, 2) + ' – ' + num(phase.end, 2) + ' s' })));
  });
}

function renderGraph(graph) {
  var tree = clear($('graph-tree'));
  if (!graph || !graph.nodes) { tree.appendChild(el('p', { class: 'dim small', text: 'no effect loaded' })); return; }

  var errorNodes = {};
  var items = (graph.diagnostics && graph.diagnostics.items) || [];
  items.forEach(function (item) { if (item.node && item.severity === 'error') errorNodes[item.node] = true; });

  var layers = (graph.layers || []).slice();
  var byLayer = {};
  layers.forEach(function (layer) { byLayer[layer.id] = []; });
  var ungrouped = [];
  graph.nodes.forEach(function (node) {
    if (node.layer && byLayer[node.layer]) byLayer[node.layer].push(node);
    else ungrouped.push(node);
  });

  layers.forEach(function (layer) {
    tree.appendChild(layerGroup(layer.name || layer.id, layer.role || '', byLayer[layer.id], errorNodes, layer.id));
  });
  if (ungrouped.length) tree.appendChild(layerGroup('Ungrouped', '', ungrouped, errorNodes, '_ungrouped'));
}

var collapsedLayers = {};

function layerGroup(title, role, nodes, errorNodes, key) {
  var body = el('div', {});
  (nodes || []).forEach(function (node) { body.appendChild(nodeRow(node, errorNodes)); });
  if (!nodes || !nodes.length) body.appendChild(el('p', { class: 'dim small', text: 'empty' }));
  var details = el('details', {},
    el('summary', {},
      el('span', { text: title }),
      role ? el('span', { class: 'role', text: '  · ' + role }) : null,
      el('span', { class: 'count', text: String((nodes || []).length) })),
    body);
  details.open = !collapsedLayers[key];
  details.addEventListener('toggle', function () { collapsedLayers[key] = !details.open; });
  return details;
}

function nodeRow(node, errorNodes) {
  var classes = ['node-row'];
  if (node.id === S.selected) classes.push('selected');
  if (node.enabled === false) classes.push('disabled');
  if (errorNodes && errorNodes[node.id]) classes.push('has-error');

  var toggle = el('input', {
    type: 'checkbox', checked: node.enabled !== false, title: 'enabled',
    onclick: function (ev) { ev.stopPropagation(); }
  });
  toggle.addEventListener('change', function () {
    setNodeProperty(node.id, { enabled: toggle.checked });
  });

  return el('div', {
    class: classes.join(' '),
    title: node.type + ' · ' + node.id + (node.parent ? '\nparent: ' + node.parent : ''),
    data: { node: node.id, type: node.type || '' },
    onclick: function () { selectNode(node.id); }
  },
    el('span', { class: 'glyph', text: NODE_GLYPH[node.type] || '●' }),
    el('span', { class: 'node-id', text: node.id }),
    el('span', { class: 'node-type', text: node.type }),
    toggle);
}

function renderStatistics(statistics) {
  var line = $('statline');
  if (!statistics) { line.textContent = 'no statistics yet'; return; }
  var sim = statistics.simulation || {};
  var render = statistics.render || {};
  clear(line);
  function stat(label, value) {
    return [document.createTextNode(label + ' '), el('b', { text: String(value) }), document.createTextNode('   ')];
  }
  [
    stat('alive', num(sim.total_alive, 0)),
    stat('spawned', num(sim.total_spawned, 0)),
    stat('sim ms', num(sim.total_step_ms, 2)),
    stat('render ms', num(render.render_ms, 2)),
    stat('drawn', num(render.particles_drawn, 0)),
    stat('overdraw', num(render.overdraw, 2))
  ].forEach(function (parts) { parts.forEach(function (p) { line.appendChild(p); }); });
}

function renderDiagnostics(diagnostics) {
  var box = clear($('diagnostics'));
  var items = (diagnostics && diagnostics.items) || [];
  items.forEach(function (item) {
    var severity = item.severity || 'info';
    var where = item.node ? item.node + (item.param ? '.' + item.param : '') : '';
    var row = el('div', {
      class: 'diag ' + severity,
      title: item.node ? 'select ' + item.node : '',
      onclick: function () { if (item.node) selectNode(item.node); }
    },
      el('span', { class: 'code', text: item.code || severity }),
      where ? el('span', { class: 'where', text: '[' + where + ']' }) : null,
      el('span', { text: item.message || '' }));
    box.appendChild(row);
  });
}

/* ====================================================================== *
 * node selection + parameter editor
 * ====================================================================== */

function selectNode(nodeId, quiet) {
  if (!nodeId) return Promise.resolve(null);
  if (!quiet) S.paramMessage = null;
  S.selected = nodeId;
  markSelectedRow(nodeId, !quiet);
  return guard(api('/api/node/' + encodeURIComponent(nodeId)).then(function (node) {
    S.node = node;
    renderParams();
    return node;
  }), 'inspect node');
}

function markSelectedRow(nodeId, scroll) {
  var rows = document.querySelectorAll('.node-row');
  for (var i = 0; i < rows.length; i++) {
    var isIt = rows[i].dataset.node === nodeId;
    rows[i].classList.toggle('selected', isIt);
    if (isIt && scroll) {
      var group = rows[i].closest('details');
      if (group && !group.open) group.open = true;
      rows[i].scrollIntoView({ block: 'nearest' });
    }
  }
}

function setNodeProperty(nodeId, props) {
  var body = { node_id: nodeId };
  for (var key in props) body[key] = props[key];
  return guard(api('/api/node/property', { body: body }).then(function () {
    invalidatePreview();
    return refreshEffect().then(showCurrentFrame);
  }), 'set node property');
}

function deleteNode(nodeId) {
  if (!window.confirm('Delete node "' + nodeId + '"?  References to it are removed.')) return Promise.resolve(null);
  return guard(api('/api/node/delete', { body: { node_id: nodeId } }).then(function (result) {
    var dangling = (result && result.dangling) || [];
    toast('deleted ' + nodeId + (dangling.length ? ' (' + dangling.length + ' reference(s) cleared)' : ''), 'ok');
    S.selected = null; S.node = null;
    invalidatePreview();
    return refreshEffect().then(showCurrentFrame);
  }), 'delete node');
}

/* -- focus preservation across re-renders ------------------------------ */

function captureFocus() {
  var active = document.activeElement;
  var body = $('params-body');
  if (!active || !body || !body.contains(active) || !active.dataset || !active.dataset.param) return null;
  return { param: active.dataset.param, comp: active.dataset.comp || '' };
}

function restoreFocus(saved) {
  if (!saved || !/^[A-Za-z0-9_]+$/.test(saved.param)) return;
  var selector = '[data-param="' + saved.param + '"]' + (saved.comp ? '[data-comp="' + saved.comp + '"]' : '');
  var target = $('params-body').querySelector(selector);
  if (target && typeof target.focus === 'function') target.focus();
}

/* -- the panel --------------------------------------------------------- */

function renderParams() {
  var saved = captureFocus();
  var body = clear($('params-body'));
  var title = $('param-node-title');
  if (!S.node || !S.node.node) {
    title.textContent = '';
    body.appendChild(el('p', { class: 'dim small', text: 'Select a node in the graph.' }));
    return;
  }

  var node = S.node.node;
  var spec = S.node.spec || {};
  var specParams = spec.parameters || {};
  var effective = S.node.effective_parameters || {};
  var raw = node.parameters || {};
  title.textContent = node.type || '';

  var enabled = el('input', { type: 'checkbox', checked: node.enabled !== false, title: 'node enabled' });
  enabled.addEventListener('change', function () { setNodeProperty(node.id, { enabled: enabled.checked }); });

  body.appendChild(el('div', { class: 'node-head', data: { type: node.type || '' } },
    el('span', { class: 'glyph', text: NODE_GLYPH[node.type] || '●' }),
    el('span', { class: 'id', text: node.id }),
    el('span', { class: 'type', text: node.type }),
    S.node.tier ? el('span', { class: 'tier', text: String(S.node.tier) }) : null,
    el('label', { class: 'check' }, enabled, 'enabled'),
    el('span', { class: 'grow' }),
    el('button', { type: 'button', class: 'danger', title: 'Delete this node', onclick: function () { deleteNode(node.id); } }, 'Delete')));

  if (spec.description) body.appendChild(el('p', { class: 'dim small', text: spec.description }));

  var names = Object.keys(specParams);
  Object.keys(effective).forEach(function (name) { if (names.indexOf(name) < 0) names.push(name); });
  names.sort();

  var main = names.filter(function (n) { return TRANSFORM_PARAMS.indexOf(n) < 0 && WINDOW_PARAMS.indexOf(n) < 0; });
  var transform = TRANSFORM_PARAMS.filter(function (n) { return names.indexOf(n) >= 0; });
  var window_ = WINDOW_PARAMS.filter(function (n) { return names.indexOf(n) >= 0; });

  function rows(into, list) {
    list.forEach(function (name) {
      into.appendChild(paramRow(node, name, specParams[name] || {}, effective[name], raw[name]));
    });
  }

  var mainBox = el('div', {});
  rows(mainBox, main);
  body.appendChild(mainBox);

  if (transform.length) {
    var tBox = el('div', {});
    rows(tBox, transform);
    body.appendChild(el('details', { class: 'param-group' }, el('summary', { text: 'transform' }), tBox));
  }
  if (window_.length) {
    var wBox = el('div', {});
    rows(wBox, window_);
    body.appendChild(el('details', { class: 'param-group' }, el('summary', { text: 'time window' }), wBox));
  }

  body.appendChild(renderPorts());
  restoreFocus(saved);
}

function renderPorts() {
  var box = el('div', { class: 'ports' });
  var resolved = S.node.resolved_inputs || {};
  var portNames = Object.keys(resolved);
  box.appendChild(el('h3', { text: 'inputs' }));
  if (!portNames.length) box.appendChild(el('p', { class: 'dim small', text: 'none' }));
  portNames.sort().forEach(function (port) {
    var refs = resolved[port];
    if (!Array.isArray(refs)) refs = [refs];
    var chips = refs.map(function (ref) {
      var target = (ref && (ref.node || ref.ref)) || String(ref);
      var missing = ref && ref.exists === false;
      return el('span', {
        class: 'chip' + (missing ? ' missing' : ''),
        title: missing ? 'unresolved reference' : (ref && ref.type ? ref.type : ''),
        onclick: function () { if (!missing) selectNode(target); }
      }, target);
    });
    box.appendChild(el('div', { class: 'port' }, el('span', { class: 'name', text: port }), el('span', {}, chips)));
  });

  var consumers = S.node.consumers || [];
  box.appendChild(el('h3', { text: 'consumers' }));
  if (!consumers.length) box.appendChild(el('p', { class: 'dim small', text: 'none' }));
  else {
    var wrap = el('div', {});
    consumers.forEach(function (id) {
      wrap.appendChild(el('span', { class: 'chip', onclick: function () { selectNode(id); } }, String(id)));
    });
    box.appendChild(wrap);
  }
  return box;
}

function paramRow(node, name, spec, value, rawValue) {
  var units = spec.units ? ' ' + spec.units : '';
  var track = (rawValue && typeof rawValue === 'object' && !Array.isArray(rawValue) && Array.isArray(rawValue.track))
    ? rawValue.track : null;
  var isSet = Object.prototype.hasOwnProperty.call(node.parameters || {}, name);

  var label = el('span', {
    class: 'param-name' + (isSet ? ' changed' : ''),
    title: (spec.description || name) + '\ntype: ' + (spec.type || '?') +
      (spec.min !== null && spec.min !== undefined ? '\nmin: ' + spec.min : '') +
      (spec.max !== null && spec.max !== undefined ? '\nmax: ' + spec.max : '') +
      (spec.default !== undefined ? '\ndefault: ' + JSON.stringify(spec.default) : '')
  }, name, units ? el('em', { class: 'units', text: units }) : null,
    track ? el('span', { class: 'badge', title: 'keyframed; editing replaces the track with a constant', text: 'animated (' + track.length + ' keys)' }) : null);

  var msg = el('div', { class: 'param-msg' });
  if (S.paramMessage && S.paramMessage.name === name) {
    msg.textContent = S.paramMessage.text;
    msg.className = 'param-msg' + (S.paramMessage.ok ? ' ok' : '');
  }

  function commit(newValue) { commitParam(name, newValue); }
  var widget = buildWidget(name, spec, value === undefined ? spec.default : value, commit, msg);
  var row = el('div', { class: 'param-row' }, label, widget, msg);
  return row;
}

function commitParam(name, value) {
  if (!S.selected) return Promise.resolve(null);
  var nodeId = S.selected;
  return api('/api/param', { body: { node_id: nodeId, name: name, value: value } }).then(function (result) {
    var diagnostics = result.diagnostics || {};
    var items = diagnostics.items || [];
    var bad = items.filter(function (item) { return item.severity === 'error' || item.severity === 'warning'; });
    S.paramMessage = bad.length
      ? { name: name, text: bad.map(function (i) { return (i.code || '') + ' ' + i.message; }).join('; '), ok: false }
      : { name: name, text: 'ok', ok: true };
    invalidatePreview();
    return refreshEffect().then(showCurrentFrame);
  }, function (err) {
    S.paramMessage = { name: name, text: err && err.message ? err.message : 'failed', ok: false };
    toast('set ' + nodeId + '.' + name + ': ' + (err && err.message ? err.message : 'failed'), 'error');
    renderParams();
    return null;
  });
}

/* -- widgets ----------------------------------------------------------- */

function buildWidget(name, spec, value, commit, msg) {
  var box = el('span', { class: 'param-widget' });
  var type = spec.type || inferType(value);

  if (type === 'bool') { box.appendChild(boolWidget(name, value, commit)); return box; }
  if (type === 'enum' && spec.enum && spec.enum.length) { box.appendChild(enumWidget(name, spec, value, commit)); return box; }
  if (type === 'color') { colorWidget(name, value, commit).forEach(function (n) { box.appendChild(n); }); return box; }
  if (VEC_SIZES[type]) { vecWidget(name, VEC_SIZES[type], value, commit).forEach(function (n) { box.appendChild(n); }); return box; }
  if (JSON_TYPES[type]) { box.appendChild(jsonWidget(name, value, commit, msg)); return box; }
  if (type === 'float' || type === 'int') { numberWidget(name, spec, value, commit).forEach(function (n) { box.appendChild(n); }); return box; }
  box.appendChild(textWidget(name, value, commit));
  return box;
}

function inferType(value) {
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'number') return 'float';
  if (Array.isArray(value)) return value.length === 3 ? 'vec3' : 'json';
  if (value && typeof value === 'object') return 'json';
  return 'string';
}

function boolWidget(name, value, commit) {
  var input = el('input', { type: 'checkbox', checked: !!value, data: { param: name } });
  input.addEventListener('change', function () { commit(input.checked); });
  return input;
}

function enumWidget(name, spec, value, commit) {
  var select = el('select', { data: { param: name } });
  spec.enum.forEach(function (option) { select.appendChild(el('option', { value: option, text: option })); });
  select.value = value === undefined || value === null ? spec.enum[0] : String(value);
  select.addEventListener('change', function () { commit(select.value); });
  return select;
}

function textWidget(name, value, commit) {
  var input = el('input', { type: 'text', value: value === null || value === undefined ? '' : String(value), data: { param: name } });
  input.addEventListener('change', function () { commit(input.value); });
  return input;
}

function niceStep(spec) {
  if (spec.type === 'int') return 1;
  var min = spec.min, max = spec.max;
  if (min !== null && min !== undefined && max !== null && max !== undefined && isFinite(max - min)) {
    var raw = (max - min) / 200;
    var magnitude = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    return Math.max(0.001, magnitude);
  }
  return 0.01;
}

function numberWidget(name, spec, value, commit) {
  var isInt = spec.type === 'int';
  var step = niceStep(spec);
  var current = Number(value);
  if (isNaN(current)) current = 0;

  var input = el('input', { type: 'number', value: String(current), step: String(step), data: { param: name, comp: '0' } });
  if (spec.min !== null && spec.min !== undefined) input.min = String(spec.min);
  if (spec.max !== null && spec.max !== undefined) input.max = String(spec.max);

  var nodes = [input];
  var slider = null;
  var bounded = spec.min !== null && spec.min !== undefined && spec.max !== null && spec.max !== undefined;
  if (bounded && isFinite(spec.max - spec.min)) {
    slider = el('input', {
      type: 'range', min: String(spec.min), max: String(spec.max),
      step: String(isInt ? 1 : (spec.max - spec.min) / 200), value: String(current),
      title: spec.min + ' .. ' + spec.max
    });
    slider.addEventListener('input', function () { input.value = slider.value; });
    slider.addEventListener('change', function () { commit(read()); });
    nodes.push(slider);
  }

  function read() {
    var parsed = isInt ? parseInt(input.value, 10) : parseFloat(input.value);
    if (isNaN(parsed)) parsed = current;
    return parsed;
  }
  input.addEventListener('change', function () {
    if (slider) slider.value = String(read());
    commit(read());
  });
  return nodes;
}

function vecWidget(name, size, value, commit) {
  var values = Array.isArray(value) ? value.slice(0, size) : [];
  while (values.length < size) values.push(0);
  var inputs = [];
  var nodes = [];
  for (var i = 0; i < size; i++) {
    (function (index) {
      var input = el('input', {
        type: 'number', step: '0.01', value: String(Number(values[index]) || 0),
        data: { param: name, comp: String(index) }, title: COMPONENT_LABELS[index]
      });
      input.addEventListener('change', function () { commit(readVec(inputs)); });
      inputs.push(input);
      nodes.push(el('span', { class: 'comp' }, el('label', { text: COMPONENT_LABELS[index] || String(index) }), input));
    })(i);
  }
  return nodes;
}

function readVec(inputs) {
  return inputs.map(function (input) {
    var parsed = parseFloat(input.value);
    return isNaN(parsed) ? 0 : parsed;
  });
}

function linearToHex(component) {
  var clamped = Math.max(0, Math.min(1, Number(component) || 0));
  var byte = Math.round(Math.pow(clamped, 1 / 2.2) * 255);
  return ('0' + byte.toString(16)).slice(-2);
}

function hexToLinear(hex, offset) {
  var byte = parseInt(hex.substr(1 + offset * 2, 2), 16) / 255;
  return Math.round(Math.pow(byte, 2.2) * 10000) / 10000;
}

function colorWidget(name, value, commit) {
  var comps = Array.isArray(value) ? value.slice() : [1, 1, 1];
  while (comps.length < 3) comps.push(0);
  var size = comps.length >= 4 ? 4 : 3;
  comps = comps.slice(0, size);

  var inputs = [];
  var nodes = [];
  var swatch = el('input', {
    type: 'color', value: '#' + linearToHex(comps[0]) + linearToHex(comps[1]) + linearToHex(comps[2]),
    title: 'linear RGB; the swatch clamps to 0..1, the numbers may exceed it'
  });
  nodes.push(swatch);

  var labels = ['r', 'g', 'b', 'a'];
  for (var i = 0; i < size; i++) {
    (function (index) {
      var input = el('input', {
        type: 'number', step: '0.01', value: String(Number(comps[index]) || 0),
        data: { param: name, comp: String(index) }, title: labels[index]
      });
      input.addEventListener('change', function () {
        var next = readVec(inputs);
        swatch.value = '#' + linearToHex(next[0]) + linearToHex(next[1]) + linearToHex(next[2]);
        commit(next);
      });
      inputs.push(input);
      nodes.push(el('span', { class: 'comp' }, el('label', { text: labels[index] }), input));
    })(i);
  }

  swatch.addEventListener('change', function () {
    for (var c = 0; c < 3; c++) inputs[c].value = String(hexToLinear(swatch.value, c));
    commit(readVec(inputs));
  });
  return nodes;
}

function prettyJson(value) {
  if (Array.isArray(value) && value.length && value.every(function (item) { return Array.isArray(item); })) {
    return '[\n  ' + value.map(function (item) { return JSON.stringify(item); }).join(',\n  ') + '\n]';
  }
  return JSON.stringify(value === undefined ? null : value, null, 2);
}

function jsonWidget(name, value, commit, msg) {
  var area = el('textarea', { rows: 3, spellcheck: 'false', data: { param: name } });
  area.value = prettyJson(value);
  area.addEventListener('change', function () {
    var parsed;
    try {
      parsed = JSON.parse(area.value);
    } catch (err) {
      area.classList.add('invalid');
      if (msg) { msg.textContent = 'invalid JSON: ' + err.message; msg.className = 'param-msg'; }
      return;
    }
    area.classList.remove('invalid');
    commit(parsed);
  });
  area.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); area.blur(); }
  });
  return area;
}

/* ====================================================================== *
 * the Style panel: the effect's controls
 *
 * A control is a named numeric knob stored in the document and bound to node
 * parameters; the compiler folds it in, so moving one never rewrites what the
 * author typed (docs/CONTROLS.md).  Dragging is debounced to ~100 ms and every
 * commit re-opens the effect on the GPU stream through invalidatePreview(),
 * which keeps playback running.
 * ====================================================================== */

var CONTROL_DEBOUNCE_MS = 100;
var collapsedGroups = {};

function controlsOf() { return (S.controls && S.controls.controls) || []; }

function findControl(id) {
  var list = controlsOf();
  for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
  return null;
}

function refreshControls() {
  return apiRaw('/api/controls').then(function (res) { return res.json(); }).then(function (data) {
    S.controls = data;
    renderControls();
    return data;
  }, function (err) {
    if (err && err.status === 404) { S.controls = null; renderControls(); return null; }
    throw err;
  });
}

/* Groups in the order the engine lists them ("Global" first). */
function controlGroups() {
  var order = (S.controls && S.controls.groups) || [];
  var seen = {};
  var groups = [];
  order.forEach(function (name) { seen[name] = []; groups.push(name); });
  controlsOf().forEach(function (control) {
    var group = control.group || 'Global';
    if (!seen[group]) { seen[group] = []; groups.push(group); }
    seen[group].push(control);
  });
  return groups.map(function (name) { return { name: name, controls: seen[name] || [] }; })
    .filter(function (group) { return group.controls.length > 0; });
}

/* Which slider or number field has the keyboard, so nudging with the arrow
 * keys survives the repaint that follows every commit. */
function captureControlFocus() {
  var active = document.activeElement;
  if (!active || !active.dataset || !active.dataset.control) return null;
  var body = $('controls-body');
  if (!body || !body.contains(active)) return null;
  return { control: active.dataset.control, field: active.dataset.field || '' };
}

function restoreControlFocus(saved) {
  if (!saved || !/^[A-Za-z0-9_]+$/.test(saved.control)) return;
  var selector = '[data-control="' + saved.control + '"]' +
    (saved.field ? '[data-field="' + saved.field + '"]' : '');
  var target = $('controls-body').querySelector(selector);
  if (target && typeof target.focus === 'function') target.focus();
}

function renderControls() {
  if (S.controlPending) return;                  /* never repaint under the user's thumb */
  var focused = captureControlFocus();
  var body = clear($('controls-body'));
  var resetAll = $('btn-controls-reset');
  if (!S.data) {
    resetAll.hidden = true;
    body.appendChild(el('p', { class: 'dim small', text: 'no effect loaded' }));
    return;
  }
  var groups = controlGroups();
  if (!groups.length) {
    resetAll.hidden = true;
    body.appendChild(el('p', { class: 'dim small', text: 'This effect has no controls yet.' }));
    body.appendChild(el('button', {
      type: 'button', class: 'sm', title: 'Build a Global group and one per layer for this effect',
      onclick: generateControls
    }, 'Generate controls'));
    return;
  }
  resetAll.hidden = false;
  groups.forEach(function (group) { body.appendChild(controlGroupNode(group)); });
  restoreControlFocus(focused);
}

function controlGroupNode(group) {
  var box = el('div', { class: 'ctl-list' });
  group.controls.forEach(function (control) { box.appendChild(controlRow(control)); });

  var changed = group.controls.some(function (c) { return c.value !== c.default; });
  var details = el('details', { class: 'ctl-group' },
    el('summary', {},
      el('span', { class: 'name', text: group.name }),
      changed ? el('i', { class: 'dot-changed', title: 'moved off its defaults' }) : null,
      el('span', { class: 'count', text: String(group.controls.length) })),
    box);
  /* Global open, the layer groups folded away, so every part of the effect is
   * on screen at once and the one being tuned is a click away. */
  details.open = collapsedGroups[group.name] === undefined
    ? group.name === 'Global'
    : !collapsedGroups[group.name];
  details.addEventListener('toggle', function () { collapsedGroups[group.name] = !details.open; });
  return details;
}

function controlDigits(step) {
  if (!(step > 0) || step >= 1) return 0;
  return Math.min(4, Math.max(1, Math.ceil(-Math.log(step) / Math.LN10)));
}

function controlRow(control) {
  var step = control.step > 0 ? control.step : 0.01;
  var digits = controlDigits(step);
  var isDefault = control.value === control.default;

  var slider = el('input', {
    type: 'range', min: String(control.min), max: String(control.max), step: String(step),
    value: String(control.value), class: 'ctl-slider',
    data: { control: control.id, field: 'slider' },
    title: control.min + ' .. ' + control.max + '  (arrows step, shift x10)'
  });
  var number = el('input', {
    type: 'number', min: String(control.min), max: String(control.max), step: String(step),
    value: control.value.toFixed(digits), class: 'ctl-number mono',
    data: { control: control.id, field: 'number' }
  });
  var dot = el('i', { class: 'dot-changed' + (isDefault ? ' off' : ''), title: 'moved off its default' });

  function show(value) {
    slider.value = String(value);
    number.value = value.toFixed(digits);
    dot.classList.toggle('off', value === control.default);
  }
  function clamp(value) {
    if (isNaN(value)) return control.value;
    return Math.min(control.max, Math.max(control.min, value));
  }
  function push(value, immediate) {
    var next = clamp(value);
    show(next);
    control.value = next;
    if (immediate) commitControl(control.id, next); else scheduleControl(control.id, next);
  }
  function nudge(direction, big) {
    push(control.value + direction * step * (big ? 10 : 1), false);
  }
  function arrows(ev) {
    var up = ev.key === 'ArrowUp' || ev.key === 'ArrowRight';
    var down = ev.key === 'ArrowDown' || ev.key === 'ArrowLeft';
    if (!up && !down) return;
    ev.preventDefault();
    nudge(up ? 1 : -1, ev.shiftKey);
  }

  slider.addEventListener('input', function () { push(parseFloat(slider.value), false); });
  slider.addEventListener('change', function () { push(parseFloat(slider.value), true); });
  slider.addEventListener('keydown', arrows);
  number.addEventListener('change', function () { push(parseFloat(number.value), true); });
  number.addEventListener('keydown', arrows);

  var reset = el('button', {
    type: 'button', class: 'ghost icon-btn xs ctl-reset', title: 'Reset to ' + control.default,
    'aria-label': 'Reset ' + control.label,
    onclick: function () { push(control.default, true); }
  }, '↺');

  var label = el('span', {
    class: 'ctl-label',
    title: control.label + '\nid: ' + control.id + '\nrange: ' + control.min + ' .. ' + control.max +
      '\ndefault: ' + control.default + '\ndrives ' + (control.bindings || []).length + ' parameter(s)'
  }, control.label || control.id,
    control.unit ? el('em', { class: 'unit', text: control.unit }) : null);

  return el('div', { class: 'ctl-row' }, label, dot, number, reset, slider);
}

/* Latest-wins, one timer per control: dragging sends at most one call per
 * CONTROL_DEBOUNCE_MS and always ends on the value the user let go of. */
var controlTimers = {};
function scheduleControl(id, value) {
  if (controlTimers[id]) clearTimeout(controlTimers[id]);
  controlTimers[id] = setTimeout(function () {
    controlTimers[id] = null;
    commitControl(id, value);
  }, CONTROL_DEBOUNCE_MS);
}

function commitControl(id, value) {
  if (controlTimers[id]) { clearTimeout(controlTimers[id]); controlTimers[id] = null; }
  S.controlPending = (S.controlPending || 0) + 1;
  S.uiMutating = true;                       /* the status poll must not repaint mid-drag */
  return api('/api/controls/' + encodeURIComponent(id), { body: { value: value } }).then(function (result) {
    if (result && result.control) {
      var known = findControl(id);
      if (known) known.value = result.control.value;
    }
    invalidatePreview();                     /* GPU: re-open the stream; CPU: mark the preview stale */
    return null;
  }, function (err) {
    toast('control ' + id + ': ' + (err && err.message ? err.message : 'failed'), 'error');
    return null;
  }).then(function () {
    S.controlPending = Math.max(0, (S.controlPending || 1) - 1);
    if (!S.controlPending) {
      S.uiMutating = false;
      renderControls();                      /* repaint once the hand is off the slider */
      if (!S.playing) showCurrentFrame();
    }
    return null;
  });
}

function resetAllControls() {
  return guard(api('/api/controls/reset', { body: {} }).then(function (data) {
    S.controls = data;
    renderControls();
    invalidatePreview();
    if (!S.playing) showCurrentFrame();
    return data;
  }), 'reset controls');
}

function generateControls() {
  return guard(api('/api/controls/generate', { body: {} }).then(function (data) {
    S.controls = data;
    renderControls();
    toast('added ' + (data.added || 0) + ' control(s)', 'ok');
    return data;
  }), 'generate controls');
}

/* ====================================================================== *
 * viewport + transport
 * ====================================================================== */

function duration() {
  if (!S.data) return 0;
  var graph = S.data.graph || {};
  if (typeof graph.duration === 'number') return graph.duration;
  var effect = S.data.effect || {};
  return typeof effect.duration === 'number' ? effect.duration : 0;
}

function timelineMax() {
  if (S.preview && S.preview.count > 0) return S.preview.count - 1;
  return Math.max(0, Math.round(duration() * S.fps));
}

function timeAt(index) {
  if (S.preview && S.preview.count > 0) return (S.preview.start || 0) + index / S.preview.fps;
  return index / S.fps;
}

function usingPreview() { return !!(S.preview && S.preview.count && !S.previewStale); }

/* The scrubber paints its own progress: CSS reads --fill on the input. */
function paintSlider() {
  var slider = $('frame-slider');
  var max = parseFloat(slider.max) || 0;
  var value = parseFloat(slider.value) || 0;
  slider.style.setProperty('--fill', (max > 0 ? (value / max) * 100 : 0).toFixed(2) + '%');
}

function syncTransportRange() {
  var slider = $('frame-slider');
  var max = timelineMax();
  slider.max = String(Math.max(1, max));
  if (S.index > max) S.index = max;
  slider.value = String(S.index);
  slider.disabled = !S.data;
  paintSlider();
  updateReadout();
}

function updateReadout() {
  var total = timelineMax() + 1;
  $('time-readout').textContent =
    num(timeAt(S.index), 2) + ' s / ' + num(duration(), 2) + ' s  frame ' + (S.index + 1) + '/' + total;
}

function setIndex(index, fromPlayback) {
  var max = timelineMax();
  S.index = Math.max(0, Math.min(Math.round(index), max));
  $('frame-slider').value = String(S.index);
  paintSlider();
  updateReadout();
  if (glActive()) {
    if (!fromPlayback) { S.seekGuardUntil = Date.now() + 250; S.gl.seek(timeAt(S.index)); }
    return;
  }
  if (fromPlayback || usingPreview()) showPreviewFrame(S.index);
  else requestFrame(timeAt(S.index), false);
}

function step(delta) {
  pause();
  var max = timelineMax();
  var next = S.index + delta;
  if (next < 0) next = S.loop ? max : 0;
  if (next > max) next = S.loop ? 0 : max;
  setIndex(next);
}

/* Render resolution. 'fit' renders at the viewport's pixel size (aspect matched,
 * rounded to 8 px, long side capped) so the frame fills the canvas 1:1. */
function parseSize(value) { return value === 'fit' ? 'fit' : (parseInt(value, 10) || 384); }
function renderSize() {
  if (S.size !== 'fit') return { w: S.size, h: S.size };
  var vp = $('viewport');
  var w = Math.max(128, vp.clientWidth || 384), h = Math.max(128, vp.clientHeight || 384);
  var cap = 1024, scale = Math.min(1, cap / Math.max(w, h));
  w = Math.max(128, Math.floor(w * scale / 8) * 8); h = Math.max(128, Math.floor(h * scale / 8) * 8);
  return { w: w, h: h };
}
var resizeTimer = null;
function onViewportResize() {
  if (glActive()) return;                 /* the viewer watches its own container */
  if (S.size !== 'fit' || !S.data) return;
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    resizeTimer = null;
    var size = renderSize();
    if (S.preview && S.preview.w === size.w && S.preview.h === size.h) return;
    if (S.preview) { invalidatePreview(); schedulePreviewRefresh(); } else { showCurrentFrame(); }
  }, 600);
}

/* Auto re-render: after edits the preview is re-rendered (debounced) and keeps playing. */
var previewRefreshTimer = null;
function schedulePreviewRefresh() {
  if (glActive()) return;                 /* the stream is already live */
  if (previewRefreshTimer) clearTimeout(previewRefreshTimer);
  previewRefreshTimer = setTimeout(function () {
    previewRefreshTimer = null;
    if (!S.data) return;
    if (S.previewBusy) { S.previewDirty = true; return; }
    renderPreview(true);
  }, 800);
}

/* ====================================================================== *
 * stage: render settings for the preview (ground, background, bloom, exposure)
 * ====================================================================== */

var STAGE_DEFAULTS = { ground_albedo: 0.18, background: [0.02, 0.02, 0.025, 1], bloom_intensity: 0.35, bloom_radius: 0.04, exposure: 1.0, grid: true, light_scale: 3.2 };
function hexToLinear(hex) {
  var n = parseInt(hex.slice(1), 16); var c = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return c.map(function (v) { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }).concat([1]);
}
function linearToHex(rgb) {
  var out = '#';
  for (var i = 0; i < 3; i++) {
    var v = Math.max(0, Math.min(1, rgb[i] || 0));
    v = v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
    out += ('0' + Math.round(v * 255).toString(16)).slice(-2);
  }
  return out;
}
function stageFromControls() {
  return {
    ground_albedo: parseFloat($('stage-ground').value),
    background: hexToLinear($('stage-bg').value),
    bloom_intensity: parseFloat($('stage-bloom').value),
    bloom_radius: parseFloat($('stage-bloom-radius').value),
    exposure: parseFloat($('stage-exposure').value),
    grid: $('stage-grid').checked,
    light_scale: parseFloat($('stage-light-scale').value)
  };
}
function applyStage(stage) {
  var s = Object.assign({}, STAGE_DEFAULTS, stage || {});
  $('stage-ground').value = s.ground_albedo;
  $('stage-bg').value = linearToHex(s.background || STAGE_DEFAULTS.background);
  $('stage-bloom').value = s.bloom_intensity;
  $('stage-bloom-radius').value = s.bloom_radius;
  $('stage-exposure').value = s.exposure;
  $('stage-grid').checked = s.grid !== false;
  $('stage-light-scale').value = typeof s.light_scale === 'number' ? s.light_scale : STAGE_DEFAULTS.light_scale;
  S.stage = stageFromControls();
  if (glActive()) S.gl.setStage(S.stage);
}
function stageParam() { if (!S.stage) S.stage = stageFromControls(); return S.stage; }
function stageQuery() { return '&settings=' + encodeURIComponent(JSON.stringify(stageParam())); }
function stageChanged() {
  S.stage = stageFromControls();
  if (glActive()) { S.gl.setStage(S.stage); return; }
  if (usingPreview()) { S.previewStale = true; updatePreviewHint(); }
  requestFrame(timeAt(S.index), false);
  if (S.preview) schedulePreviewRefresh();
}
function wireStage() {
  ['stage-ground', 'stage-bg', 'stage-bloom', 'stage-bloom-radius', 'stage-exposure', 'stage-grid',
   'stage-light-scale'].forEach(function (id) {
    $(id).addEventListener('input', stageChanged);
    $(id).addEventListener('change', stageChanged);
  });
  $('btn-stage-save').addEventListener('click', function () {
    if (!S.data) return;
    var meta = (S.data.effect && S.data.effect.metadata) || {};
    meta = Object.assign({}, meta, { render_settings: stageFromControls() });
    api('/api/tool', { body: { name: 'set_effect_property', args: { metadata: meta } } })
      .then(function () { toast('stage saved into the effect', 'ok'); return afterEffectChange(); })
      .catch(function (err) { toast(err && err.message ? err.message : 'could not save stage', 'error'); });
  });
}

/* ====================================================================== *
 * camera: wheel zoom, drag orbit, shift/middle-drag pan (studio-only view)
 * ====================================================================== */

var DEFAULT_CAMERA = { position: [0, 1.5, 5], target: [0, 1, 0], up: [0, 1, 0], fov: 45 };
var STUDIO_ZOOM_OUT = 1.45;   /* the studio starts a little wider than the effect's own camera */

function trackValue(v) { return (v && typeof v === 'object' && !Array.isArray(v) && 'value' in v) ? v.value : v; }
function effectCamera() {
  var nodes = (S.data && S.data.effect && S.data.effect.nodes) || [];
  var cam = null;
  for (var i = 0; i < nodes.length; i++) if (nodes[i].type === 'camera') { cam = nodes[i]; break; }
  var p = cam && cam.parameters ? cam.parameters : {};
  var position = trackValue(p.position) || DEFAULT_CAMERA.position.slice();
  var target = trackValue(p.target) || DEFAULT_CAMERA.target.slice();
  return { position: position.slice(), target: target.slice(), up: (trackValue(p.up) || DEFAULT_CAMERA.up).slice(),
           fov: typeof trackValue(p.fov) === 'number' ? trackValue(p.fov) : DEFAULT_CAMERA.fov };
}
function vsub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function vadd(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function vscale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
function vlen(a) { return Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); }
function vnorm(a) { var l = vlen(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function vcross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }

function resetCamera(zoomOut) {
  var cam = effectCamera();
  if (zoomOut !== false) {
    var offset = vsub(cam.position, cam.target);
    cam.position = vadd(cam.target, vscale(offset, STUDIO_ZOOM_OUT));
  }
  S.camera = cam;
  S.cameraEffectId = S.activeId;
}
function cameraParam() {
  if (!S.camera) resetCamera();
  return { position: S.camera.position.map(function (v) { return +v.toFixed(4); }),
           target: S.camera.target.map(function (v) { return +v.toFixed(4); }),
           up: S.camera.up, fov: S.camera.fov };
}
function cameraQuery() { return '&camera=' + encodeURIComponent(JSON.stringify(cameraParam())); }

var cameraSettleTimer = null;
function cameraChanged() {
  /* live: re-render the current frame; settled: re-render the preview and keep playing */
  if (usingPreview()) { S.previewStale = true; updatePreviewHint(); }
  requestFrame(timeAt(S.index), false);
  if (cameraSettleTimer) clearTimeout(cameraSettleTimer);
  cameraSettleTimer = setTimeout(function () { cameraSettleTimer = null; if (S.preview) schedulePreviewRefresh(); }, 700);
}
function dolly(factor) {
  var cam = S.camera || (resetCamera(), S.camera);
  var offset = vsub(cam.position, cam.target);
  var dist = Math.max(0.25, Math.min(80, vlen(offset) * factor));
  cam.position = vadd(cam.target, vscale(vnorm(offset), dist));
  cameraChanged();
}
function orbit(dx, dy) {
  var cam = S.camera || (resetCamera(), S.camera);
  var offset = vsub(cam.position, cam.target);
  var dist = vlen(offset);
  var yaw = Math.atan2(offset[0], offset[2]);
  var pitch = Math.asin(Math.max(-1, Math.min(1, offset[1] / (dist || 1))));
  yaw -= dx * 0.006; pitch = Math.max(-1.45, Math.min(1.45, pitch + dy * 0.006));
  cam.position = vadd(cam.target, [dist * Math.cos(pitch) * Math.sin(yaw), dist * Math.sin(pitch), dist * Math.cos(pitch) * Math.cos(yaw)]);
  cameraChanged();
}
function pan(dx, dy) {
  var cam = S.camera || (resetCamera(), S.camera);
  var forward = vnorm(vsub(cam.target, cam.position));
  var right = vnorm(vcross(forward, cam.up));
  var upv = vnorm(vcross(right, forward));
  var dist = vlen(vsub(cam.position, cam.target));
  var k = dist * 0.0018;
  var delta = vadd(vscale(right, -dx * k), vscale(upv, dy * k));
  cam.position = vadd(cam.position, delta); cam.target = vadd(cam.target, delta);
  cameraChanged();
}
function wireCamera() {
  /* With the GPU viewer up, OrbitControls owns the pointer on the canvas. */
  if (glActive()) {
    $('btn-view-reset').addEventListener('click', function () { S.gl.resetView(); });
    return;
  }
  var vp = $('viewport');
  vp.addEventListener('wheel', function (ev) {
    if (!S.data) return;
    ev.preventDefault();
    dolly(Math.exp(ev.deltaY * 0.0012));
  }, { passive: false });
  var drag = null;
  vp.addEventListener('mousedown', function (ev) {
    if (!S.data || ev.button === 2) return;
    drag = { x: ev.clientX, y: ev.clientY, pan: ev.button === 1 || ev.shiftKey };
    vp.classList.add('dragging');
    ev.preventDefault();
  });
  window.addEventListener('mousemove', function (ev) {
    if (!drag) return;
    var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    drag.x = ev.clientX; drag.y = ev.clientY;
    if (drag.pan) pan(dx, dy); else orbit(dx, dy);
  });
  window.addEventListener('mouseup', function () { if (drag) { drag = null; vp.classList.remove('dragging'); } });
  $('btn-view-reset').addEventListener('click', function () { resetCamera(); cameraChanged(); });
}

/* ====================================================================== *
 * randomize
 * ====================================================================== */

function randomizeEffect() {
  if (!S.data) { toast('load or create an effect first', 'warn'); return; }
  var amount = parseFloat($('sel-random-amount').value) || 0.35;
  var body = { amount: amount };
  if (S.selected) body.node_id = S.selected;
  $('btn-random').disabled = true;
  api('/api/randomize', { body: body }).then(function (r) {
    toast('randomized ' + r.changed_params + ' value(s) in ' + r.changed_nodes + ' node(s)' + (r.seed ? ', seed ' + r.seed : ''), 'ok');
    return afterEffectChange().then(function () { invalidatePreview(); });
  }).catch(function (err) { toast(err && err.message ? err.message : 'randomize failed', 'error'); })
    .then(function () { $('btn-random').disabled = false; });
}

function showCurrentFrame() {
  if (!S.data) { clearViewport(); return; }
  if (glActive()) { S.seekGuardUntil = Date.now() + 250; S.gl.seek(timeAt(S.index)); return; }
  if (usingPreview()) showPreviewFrame(S.index);
  else requestFrame(timeAt(S.index), true);
}

function showPreviewFrame(index) {
  if (!S.preview || !S.preview.frames.length) return;
  var url = S.preview.frames[Math.max(0, Math.min(index, S.preview.frames.length - 1))];
  if (url) showImage(url);
}

function showImage(url) {
  var img = $('viewport-img');
  img.src = url;
  img.hidden = false;
  $('viewport-empty').hidden = true;
}

function clearViewport() {
  var img = $('viewport-img');
  $('gl-reference').hidden = true;
  img.hidden = true;
  img.removeAttribute('src');
  $('viewport-empty').hidden = false;
  setBusy(false);
}

function setBusy(on, text) {
  var box = $('viewport-busy');
  box.hidden = !on;
  if (on && text) $('viewport-busy-text').textContent = text;
}

/* Debounced, latest-wins single-frame render. */
function requestFrame(time, immediate) {
  if (!S.data || glActive()) return;      /* the GPU viewer draws the live stream */
  if (S.frameTimer) { clearTimeout(S.frameTimer); S.frameTimer = null; }
  var run = function () {
    S.frameTimer = null;
    var token = ++S.frameToken;
    if (S.frameAbort) { try { S.frameAbort.abort(); } catch (e) { /* ignore */ } }
    var controller = (typeof AbortController === 'function') ? new AbortController() : null;
    S.frameAbort = controller;
    var size = renderSize();
    var url = '/api/frame?time=' + encodeURIComponent(time.toFixed(3)) +
      '&width=' + size.w + '&height=' + size.h + cameraQuery() + stageQuery();
    setBusy(true, 'rendering frame');
    apiRaw(url, controller ? { signal: controller.signal } : {}).then(function (res) {
      var stats = res.headers.get('X-Aether-Render');
      return res.blob().then(function (blob) {
        if (token !== S.frameToken) return;
        var objectUrl = URL.createObjectURL(blob);
        var previous = S.frameUrl;
        S.frameUrl = objectUrl;
        showImage(objectUrl);
        if (previous) URL.revokeObjectURL(previous);
        if (stats) applyRenderStats(stats);
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      if (token === S.frameToken) toast('render: ' + (err && err.message ? err.message : 'failed'), 'error');
    }).then(function () {
      if (token === S.frameToken) setBusy(false);
    });
  };
  if (immediate) run(); else S.frameTimer = setTimeout(run, 120);
}

function applyRenderStats(raw) {
  try {
    var stats = JSON.parse(raw);
    if (S.data && S.data.statistics) {
      S.data.statistics.render = stats;
      renderStatistics(S.data.statistics);
    }
  } catch (err) { /* header is a nicety, never fatal */ }
}

/* -- preview ----------------------------------------------------------- */

function invalidatePreview() {
  /* Live path: re-open the effect on the stream so edits show within a frame. */
  if (glActive()) { S.gl.reload(); return; }
  if (S.preview) S.previewStale = true;
  updatePreviewHint();
  schedulePreviewRefresh();
}

function resetPreview() {
  pause();
  if (S.preview) S.preview.frames.forEach(function (url) { URL.revokeObjectURL(url); });
  S.preview = null;
  S.previewStale = true;
  S.index = 0;
  updatePreviewHint();
}

function updatePreviewHint() {
  var hint = $('preview-hint');
  if (S.previewBusy) { hint.textContent = 'rendering…'; return; }
  hint.textContent = (S.preview && S.previewStale) ? 'preview outdated – re-render' : '';
}

function renderPreview(autoplay) {
  if (!S.data) { toast('load or create an effect first', 'warn'); return Promise.resolve(null); }
  if (S.previewBusy) { S.previewDirty = true; return Promise.resolve(null); }
  pause();
  S.previewBusy = true;
  updatePreviewHint();
  setBusy(true, 'rendering preview');
  $('btn-preview').disabled = true;

  var size = renderSize();
  var body = { fps: S.fps, width: size.w, height: size.h, start: 0, end: duration(), camera: cameraParam(), settings: stageParam() };
  S.previewDirty = false;
  return api('/api/preview', { body: body }).then(function (result) {
    result.w = size.w; result.h = size.h;
    if (!result.frames || !result.frames.length) throw new ApiError('the engine returned no frames', 0, null);
    setBusy(true, 'loading ' + result.frames.length + ' frames');
    return Promise.all(result.frames.map(function (url) {
      return apiRaw(url).then(function (res) { return res.blob(); }).then(function (blob) { return URL.createObjectURL(blob); });
    })).then(function (objectUrls) {
      resetPreview();
      S.preview = {
        frames: objectUrls, fps: result.fps || S.fps, count: objectUrls.length,
        start: result.start || 0, duration: result.duration || 0
      , w: result.w, h: result.h };
      S.previewStale = false;
      if (result.statistics) { S.data.statistics = result.statistics; renderStatistics(result.statistics); }
      syncTransportRange();
      setIndex(0, true);
      if (autoplay !== false) play();
      toast(objectUrls.length + ' frames at ' + num(result.fps, 0) + ' fps', 'ok');
      return S.preview;
    });
  }).catch(function (err) {
    if (err && err.name === 'AbortError') return null;
    toast('preview: ' + (err && err.message ? err.message : 'failed'), 'error');
    return null;
  }).then(function (value) {
    S.previewBusy = false;
    $('btn-preview').disabled = false;
    setBusy(false);
    updatePreviewHint();
    return value;
  });
}

/* -- playback ---------------------------------------------------------- */

function setPlayIcon(playing) {
  $('btn-play').innerHTML = playing ? ICON_PAUSE : ICON_PLAY;
  $('btn-play').title = playing ? 'Pause (Space)' : 'Play (Space)';
  $('btn-play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
}

function play() {
  if (S.playing) return;
  if (glActive()) {
    if (!S.data) { toast('load or create an effect first', 'warn'); return; }
    S.playing = true;
    setPlayIcon(true);
    S.gl.play(S.fps, S.loop, timeAt(S.index), S.speed);
    return;
  }
  if (!S.preview || !S.preview.count) { renderPreview(true); return; }
  S.playing = true;
  S.lastTick = 0;
  setPlayIcon(true);
  S.raf = requestAnimationFrame(tick);
}

function pause() {
  if (!S.playing) return;
  S.playing = false;
  setPlayIcon(false);
  if (glActive()) { S.gl.pause(); return; }
  if (S.raf) cancelAnimationFrame(S.raf);
  S.raf = null;
}

function togglePlay() { if (S.playing) pause(); else play(); }

function tick(now) {
  if (!S.playing) return;
  var fps = (S.preview && S.preview.fps) || S.fps;
  var interval = 1000 / (Math.max(1, fps) * Math.max(0.05, S.speed || 1));
  if (!S.lastTick) S.lastTick = now;
  var steps = Math.floor((now - S.lastTick) / interval);
  if (steps >= 1) {
    S.lastTick += steps * interval;
    if (now - S.lastTick > 250) S.lastTick = now;       /* tab was hidden: do not fast-forward */
    var max = timelineMax();
    var next = S.index + steps;
    if (next > max) {
      if (!S.loop) { setIndex(max, true); pause(); return; }
      next = max > 0 ? next % (max + 1) : 0;
    }
    setIndex(next, true);
  }
  S.raf = requestAnimationFrame(tick);
}

/* ====================================================================== *
 * reference images
 *
 * Every pill is one upload: the file lives under <output_dir>/attachments on
 * the server and only its id travels with POST /api/generate.  Pills survive
 * the running job (the log shows the same thumbnails) and clear when it ends.
 * ====================================================================== */

var MAX_ATTACHMENTS = 4;
var ICON_CLOSE = '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true">' +
  '<path d="M4.9 4.9l6.2 6.2M11.1 4.9l-6.2 6.2"/></svg>';
var attachSeq = 0;

function attachmentIds() {
  var ids = [];
  S.attachments.forEach(function (item) { if (item.id) ids.push(item.id); });
  return ids;
}

function attachmentsUploading() {
  return S.attachments.some(function (item) { return item.uploading; });
}

function findAttachment(key) {
  for (var i = 0; i < S.attachments.length; i++) if (S.attachments[i].key === key) return S.attachments[i];
  return null;
}

function renderAttachments() {
  var list = $('attach-list');
  if (!list) return;
  clear(list);
  S.attachments.forEach(function (item) { list.appendChild(attachmentPill(item)); });
  var button = $('btn-attach');
  if (!button) return;
  var full = S.attachments.length >= MAX_ATTACHMENTS;
  button.classList.toggle('on', S.attachments.length > 0);
  button.disabled = full;
  button.title = full
    ? 'up to ' + MAX_ATTACHMENTS + ' reference images'
    : 'Attach a reference image — click, drop one on the box, or paste from the clipboard';
}

function attachmentPill(item) {
  var close = el('button', {
    type: 'button', class: 'drop', title: 'Remove ' + item.name, 'aria-label': 'Remove ' + item.name,
    disabled: !!item.uploading,
    onclick: function () { removeAttachment(item.key); }
  });
  close.innerHTML = ICON_CLOSE;
  return el('div', { class: 'attach-pill' + (item.uploading ? ' uploading' : ''), title: item.name },
    el('img', { class: 'thumb', src: item.thumb_url || item.localUrl || '', alt: '' }),
    item.uploading ? el('span', { class: 'spinner' }) : null,
    el('span', { class: 'name', text: item.name }),
    close);
}

/* Accept a FileList / array of File, dropping non-images and anything over the cap. */
function addAttachmentFiles(files) {
  var wanted = [];
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    if (!file) continue;
    if (file.type && file.type.indexOf('image/') !== 0) {
      toast((file.name || 'that file') + ' is not an image', 'warn');
      continue;
    }
    wanted.push(file);
  }
  if (!wanted.length) return;
  var room = MAX_ATTACHMENTS - S.attachments.length;
  if (room <= 0) { toast('up to ' + MAX_ATTACHMENTS + ' reference images', 'warn'); return; }
  if (wanted.length > room) {
    toast('only ' + room + ' more reference image' + (room === 1 ? '' : 's') + ' fit', 'warn');
    wanted = wanted.slice(0, room);
  }
  wanted.forEach(uploadAttachment);
}

function uploadAttachment(file) {
  var item = {
    key: 'att' + (++attachSeq),
    id: null,
    name: file.name || 'reference.png',
    uploading: true,
    localUrl: null,
    thumb_url: null,
    url: null
  };
  try { item.localUrl = URL.createObjectURL(file); } catch (err) { item.localUrl = null; }
  S.attachments.push(item);
  renderAttachments();

  var form = new FormData();
  form.append('file', file, item.name);
  api('/api/attachments', { method: 'POST', body: form }).then(function (result) {
    if (!findAttachment(item.key)) { dropLocalUrl(item); return; }   /* removed while uploading */
    item.id = result.id;
    item.name = result.name || item.name;
    item.url = result.url;
    item.thumb_url = result.thumb_url;
    item.width = result.width;
    item.height = result.height;
    item.uploading = false;
    renderAttachments();
    setTimeout(function () { dropLocalUrl(item); }, 0);
  }, function (err) {
    forgetAttachment(item.key);
    toast('attach: ' + (err && err.message ? err.message : 'upload failed'), 'error');
  });
}

function dropLocalUrl(item) {
  if (!item.localUrl) return;
  try { URL.revokeObjectURL(item.localUrl); } catch (err) { /* nothing to release */ }
  item.localUrl = null;
}

/* Take the pill out of the list; the server copy is untouched. */
function forgetAttachment(key) {
  S.attachments = S.attachments.filter(function (item) {
    if (item.key !== key) return true;
    dropLocalUrl(item);
    return false;
  });
  renderAttachments();
}

function removeAttachment(key) {
  var item = findAttachment(key);
  if (!item) return;
  var id = item.id;
  forgetAttachment(key);
  if (id) guard(api('/api/attachments/' + encodeURIComponent(id), { method: 'DELETE' }), 'attachment');
}

/* Clear the box without deleting the files: the job log still shows them. */
function clearAttachments() {
  S.attachments.forEach(dropLocalUrl);
  S.attachments = [];
  renderAttachments();
}

function wireAttachments() {
  var box = $('prompt-box');
  var input = $('attach-input');
  if (!box || !input) return;

  $('btn-attach').addEventListener('click', function () { input.value = ''; input.click(); });
  input.addEventListener('change', function () {
    addAttachmentFiles(input.files || []);
    input.value = '';
  });

  function carriesFiles(ev) {
    var types = (ev.dataTransfer && ev.dataTransfer.types) || [];
    for (var i = 0; i < types.length; i++) if (types[i] === 'Files') return true;
    return false;
  }
  var depth = 0;
  box.addEventListener('dragenter', function (ev) {
    if (!carriesFiles(ev)) return;
    ev.preventDefault();
    depth++;
    box.classList.add('dragging');
  });
  box.addEventListener('dragover', function (ev) {
    if (!carriesFiles(ev)) return;
    ev.preventDefault();
    try { ev.dataTransfer.dropEffect = 'copy'; } catch (err) { /* Safari */ }
    box.classList.add('dragging');
  });
  box.addEventListener('dragleave', function () {
    if (--depth <= 0) { depth = 0; box.classList.remove('dragging'); }
  });
  box.addEventListener('drop', function (ev) {
    if (!carriesFiles(ev)) return;
    ev.preventDefault();
    depth = 0;
    box.classList.remove('dragging');
    addAttachmentFiles((ev.dataTransfer && ev.dataTransfer.files) || []);
  });

  $('gen-prompt').addEventListener('paste', function (ev) {
    var data = ev.clipboardData;
    if (!data) return;
    var items = data.items || [];
    var files = [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind !== 'file') continue;
      var file = items[i].getAsFile();
      if (file && (!file.type || file.type.indexOf('image/') === 0)) files.push(file);
    }
    if (!files.length) return;
    ev.preventDefault();
    addAttachmentFiles(files);
  });
}

/* ====================================================================== *
 * export
 *
 * GET /api/export/targets describes the five targets and the destinations the
 * server remembered; POST /api/export runs one.  An empty destination means
 * "build a zip and hand it to the browser".
 * ====================================================================== */

function exportEnabled() { return !!(S.status && S.status.active_effect); }

function syncExportButton() {
  var summary = $('btn-export');
  var pop = $('export-pop');
  if (!summary || !pop) return;
  var off = !exportEnabled();
  summary.classList.toggle('is-disabled', off);
  summary.setAttribute('aria-disabled', off ? 'true' : 'false');
  summary.title = off ? 'Load or generate an effect first'
    : 'Export this effect into Unreal, Unity, Godot, a package or a flipbook';
  if (off && pop.open) pop.open = false;
}

function loadExportTargets() {
  return guard(api('/api/export/targets').then(function (payload) {
    S.exportTargets = payload;
    renderExportTargets(payload.targets || []);
    return payload;
  }), 'export targets');
}

function renderExportTargets(targets) {
  var box = clear($('export-targets'));
  if (!targets.length) { box.appendChild(el('p', { class: 'dim small', text: 'no export targets' })); return; }
  targets.forEach(function (target) { box.appendChild(exportRow(target)); });
}

function exportRow(target) {
  var dest = el('input', {
    type: 'text', class: 'export-dest', spellcheck: 'false',
    placeholder: target.hint || 'destination folder',
    value: target.destination || ''
  });
  var remember = el('input', { type: 'checkbox', checked: !!target.destination });
  var go = el('button', { type: 'button', class: 'sm primary export-go' });

  function syncLabel() {
    var path = dest.value.trim();
    go.textContent = path ? 'Export' : 'Download';
    go.title = path ? 'Copy the package into ' + path : 'Build the package and download it';
  }
  function fire() { runExport(target, dest.value, remember.checked, go); }

  dest.addEventListener('input', syncLabel);
  dest.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') { ev.preventDefault(); fire(); }
  });
  go.addEventListener('click', fire);
  syncLabel();

  return el('div', { class: 'export-row' },
    el('div', { class: 'export-label', text: target.label || target.id }),
    dest,
    el('div', { class: 'export-foot' },
      el('label', { class: 'check', title: 'Remember this destination for next time' },
        remember, el('span', { text: 'remember' })),
      el('span', { class: 'grow' }),
      go));
}

function runExport(target, destination, remember, button) {
  if (S.exportBusy) return;
  if (!exportEnabled()) { toast('load or generate an effect first', 'warn'); return; }
  var path = (destination || '').trim();
  var label = button.textContent;
  S.exportBusy = true;
  button.disabled = true;
  button.textContent = 'working…';

  function done() {
    S.exportBusy = false;
    button.disabled = false;
    button.textContent = label;
  }

  var body = { target: target.id, remember: !!remember };
  if (path) body.destination = path;
  api('/api/export', { body: body }).then(function (info) {
    done();
    var name = target.label || target.id;
    if (info.download) {
      downloadFile(info.download, info.path);
      toast(name + ': downloading ' + (fileName(info.path) || 'package'), 'ok');
    } else {
      var parts = [];
      if (info.path) parts.push(info.path);
      if (info.installed) parts.push('plugin installed');
      if (info.note) parts.push(shorten(info.note, 200));
      toast(name + ' → ' + parts.join(' · '), 'ok');
    }
    if (remember) loadExportTargets();
  }, function (err) {
    done();
    toast('export: ' + (err && err.message ? err.message : 'failed'), 'error');
  });
}

function fileName(path) {
  if (!path) return '';
  var parts = String(path).split(/[\\/]/);
  return parts[parts.length - 1] || '';
}

function downloadFile(url, path) {
  var link = el('a', { href: url, download: fileName(path) || '' });
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/* ====================================================================== *
 * generation jobs
 * ====================================================================== */

function startGeneration() {
  var prompt = $('gen-prompt').value.trim();
  if (!prompt) { toast('describe the effect first', 'warn'); $('gen-prompt').focus(); return; }
  if (attachmentsUploading()) { toast('a reference image is still uploading', 'warn'); return; }
  var checked = document.querySelector('input[name="gen-mode"]:checked');
  var mode = checked ? checked.value : 'new';
  var body = { prompt: prompt, mode: mode };
  var ids = attachmentIds();
  if (ids.length) body.attachments = ids;
  guard(api('/api/generate', { body: body }).then(function (result) {
    S.job = { id: result.job_id, since: 0, timer: null, refsShown: false };
    clear($('job-log'));
    $('job-log').classList.add('active');
    setGenerating(true, 'starting');
    pollJob();
  }), 'generate');
}

function setGenerating(on, stateText) {
  $('btn-generate').disabled = on || !(S.status && S.status.generator && S.status.generator.available);
  $('btn-cancel-job').hidden = !on;
  $('gen-spinner').hidden = !on;
  $('gen-state').textContent = stateText || '';
}

function pollJob() {
  if (!S.job) return;
  var jobId = S.job.id;
  api('/api/jobs/' + encodeURIComponent(jobId) + '?since=' + S.job.since).then(function (snapshot) {
    if (!S.job || S.job.id !== jobId) return;
    S.job.since = snapshot.next || 0;
    if (!S.job.refsShown) { S.job.refsShown = true; showJobReferences(snapshot.attachments); }
    appendJobEvents(snapshot.events || []);
    if (snapshot.status === 'running') {
      setGenerating(true, 'running · ' + snapshot.total + ' events');
      S.job.timer = setTimeout(pollJob, 700);
    } else {
      finishJob(snapshot);
    }
  }, function (err) {
    if (!S.job || S.job.id !== jobId) return;
    toast('job: ' + (err && err.message ? err.message : 'poll failed'), 'error');
    S.job.timer = setTimeout(pollJob, 2000);
  });
}

function finishJob(snapshot) {
  S.job = null;
  setGenerating(false, snapshot.status);
  clearAttachments();
  var summary = snapshot.summary || snapshot.status;
  toast('generation ' + snapshot.status + (summary ? ': ' + shorten(summary, 160) : ''),
    snapshot.status === 'done' ? 'ok' : 'warn');
  var after = snapshot.effect_id
    ? guard(api('/api/effects/activate', { body: { effect_id: snapshot.effect_id } }), 'activate')
    : Promise.resolve(null);
  after.then(function () {
    resetPreview();
    return afterEffectChange();
  }).then(function () {
    if (S.data && snapshot.status === 'done') { if (glActive()) S.gl.reloadAndFrame(); else renderPreview(true); }
  });
}

function cancelJob() {
  if (!S.job) return;
  guard(api('/api/jobs/' + encodeURIComponent(S.job.id) + '/cancel', { body: {} }), 'cancel');
}

/* The first row of a job log: the reference images it was started with. */
function showJobReferences(list) {
  if (!list || !list.length) return;
  var log = $('job-log');
  var row = el('div', { class: 'ev-refs', title: 'reference images sent with the prompt' });
  list.forEach(function (item) {
    var url = item.thumb_url || item.url;
    if (!url) return;
    var img = el('img', { src: url, alt: 'reference image', title: 'click to show in the viewport' });
    img.addEventListener('click', function () { pause(); showImage(item.url || url); });
    row.appendChild(img);
  });
  if (!row.firstChild) return;
  row.appendChild(el('span', { text: list.length + ' reference image' + (list.length === 1 ? '' : 's') }));
  log.insertBefore(row, log.firstChild);
}

function appendJobEvents(events) {
  var log = $('job-log');
  var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  events.forEach(function (event) {
    var node = jobEventNode(event);
    if (node) log.appendChild(node);
  });
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function jobEventNode(event) {
  var kind = event.kind || 'text';
  if (kind === 'status') return el('div', { class: 'ev-status', text: event.text || '' });
  if (kind === 'text') return el('div', { class: 'ev-text', text: event.text || '' });
  if (kind === 'error') return el('div', { class: 'ev-error', text: event.text || 'error' });
  if (kind === 'done') return el('div', { class: 'ev-done', text: event.summary || 'done' });
  if (kind === 'tool_call') return el('div', { class: 'ev-call', text: formatToolCall(event) });
  if (kind === 'tool_result') {
    return el('div', { class: 'ev-result ' + (event.ok === false ? 'bad' : 'ok') },
      (event.name || '') + (event.summary ? ' — ' + shorten(event.summary, 180) : ''));
  }
  if (kind === 'image') {
    var url = event.url || event.path;
    var img = el('img', { src: url, alt: event.caption || 'render', title: 'click to show in the viewport' });
    img.addEventListener('click', function () { pause(); showImage(url); });
    return el('div', { class: 'ev-image' }, img, el('span', { text: event.caption || '' }));
  }
  return el('div', { class: 'ev-text', text: JSON.stringify(event) });
}

function formatToolCall(event) {
  var args = event.args || {};
  var parts = [];
  Object.keys(args).forEach(function (key) {
    var value = args[key];
    var text = typeof value === 'string' ? value : JSON.stringify(value);
    parts.push(key + '=' + shorten(text, 46));
  });
  if (parts.length > 6) parts = parts.slice(0, 6).concat(['…']);
  return (event.name || 'tool') + '(' + parts.join(', ') + ')';
}

/* ====================================================================== *
 * wiring
 * ====================================================================== */

/* Escape and a click outside close a <details> popover; onOpen refreshes it. */
function wirePopover(details, onOpen) {
  if (!details) return;
  document.addEventListener('mousedown', function (ev) {
    if (details.open && !details.contains(ev.target)) details.open = false;
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && details.open) details.open = false;
  });
  if (onOpen) details.addEventListener('toggle', function () { if (details.open) onOpen(); });
}

function wire() {
  $('btn-new').addEventListener('click', function () {
    var form = $('new-effect-form');
    form.open = true;
    form.scrollIntoView({ block: 'nearest' });
    $('new-name').focus();
    $('new-name').select();
  });
  $('btn-create').addEventListener('click', createEffect);
  $('btn-save').addEventListener('click', saveEffect);
  $('btn-undo').addEventListener('click', function () { runHistory('undo'); });
  $('btn-redo').addEventListener('click', function () { runHistory('redo'); });
  $('btn-refresh-effects').addEventListener('click', function () { guard(refreshEffects(), 'effects'); });

  $('btn-generate').addEventListener('click', startGeneration);
  $('btn-cancel-job').addEventListener('click', cancelJob);
  $('gen-prompt').addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); startGeneration(); }
  });

  $('btn-play').addEventListener('click', togglePlay);
  $('btn-preview').addEventListener('click', function () { glActive() ? renderReference() : renderPreview(true); });
  $('btn-reference-close').addEventListener('click', function () { $('gl-reference').hidden = true; });
  $('btn-snapshot').addEventListener('click', function () {
    if (!glActive()) { toast('snapshot needs the GPU viewer', 'warn'); return; }
    var name = ((S.status && S.status.active_effect && S.status.active_effect.name) || 'aetherfx');
    S.gl.snapshot(name.replace(/[^A-Za-z0-9_-]+/g, '_').toLowerCase() + '_' + currentTime().toFixed(2) + 's.png');
  });
  $('btn-random').addEventListener('click', randomizeEffect);
  $('btn-controls-reset').addEventListener('click', resetAllControls);
  wireCamera();
  wireStage();
  $('sel-speed').addEventListener('change', function () {
    S.speed = parseFloat($('sel-speed').value) || 1;
    try { localStorage.setItem('aetherfx.speed', String(S.speed)); } catch (err) { /* private mode */ }
    if (glActive() && S.playing) S.gl.play(S.fps, S.loop, currentTime(), S.speed);
  });
  try {
    var savedSpeed = parseFloat(localStorage.getItem('aetherfx.speed'));
    if (savedSpeed > 0 && $('sel-speed').querySelector('option[value="' + savedSpeed + '"]')) {
      S.speed = savedSpeed;
      $('sel-speed').value = String(savedSpeed);
    }
  } catch (err) { /* storage unavailable: keep 1x */ }

  $('chk-loop').addEventListener('change', function () {
    S.loop = $('chk-loop').checked;
    if (glActive() && S.playing) S.gl.play(S.fps, S.loop, currentTime(), S.speed);
  });

  var slider = $('frame-slider');
  slider.addEventListener('input', function () { pause(); setIndex(parseInt(slider.value, 10) || 0); });

  $('sel-fps').addEventListener('change', function () {
    S.fps = parseInt($('sel-fps').value, 10) || 24;
    syncTransportRange();
    if (glActive()) {
      if (S.playing) S.gl.play(S.fps, S.loop, currentTime(), S.speed);
      return;
    }
    if (S.preview) invalidatePreview();
    showCurrentFrame();
  });
  $('sel-size').addEventListener('change', function () {
    if (glActive()) { S.gl.setResolution($('sel-size').value); return; }
    S.size = parseSize($('sel-size').value);
    if (S.preview) invalidatePreview(); else showCurrentFrame();
  });
  window.addEventListener('resize', onViewportResize);

  document.addEventListener('keydown', function (ev) {
    var key = ev.key;
    if (ev.metaKey || ev.ctrlKey) {
      if (key === 's' || key === 'S') { ev.preventDefault(); saveEffect(); return; }
      if (key === 'z' || key === 'Z') { ev.preventDefault(); runHistory(ev.shiftKey ? 'redo' : 'undo'); return; }
      return;
    }
    var target = ev.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' ||
      target.tagName === 'SELECT' || target.isContentEditable)) return;
    if (key === '/') {   /* focus the search field of the view that is open */
      ev.preventDefault();
      var field = $(S.view === 'community' ? 'com-search' : 'lib-search');
      if (field) { field.focus(); field.select(); }
      return;
    }
    if (S.view === 'community') return;   /* transport keys belong to the studio view */
    if (key === ' ' || key === 'Spacebar') { ev.preventDefault(); togglePlay(); }
    else if (key === 'ArrowLeft') { ev.preventDefault(); step(-1); }
    else if (key === 'ArrowRight') { ev.preventDefault(); step(1); }
    else if (key === 'Home') { ev.preventDefault(); pause(); setIndex(0); }
    else if (key === 'End') { ev.preventDefault(); pause(); setIndex(timelineMax()); }
  });

  wireAttachments();

  /* -- top navigation and the two live search fields ---------------- */
  $('nav-studio').addEventListener('click', function () { showView('studio'); });
  $('nav-community').addEventListener('click', function () { showView('community'); });

  S.librarySearch = '';
  S.communitySearch = '';
  var readLibrary = wireSearch('lib-search', 'lib-search-clear', 'aetherfx.search.library', function (query) {
    S.librarySearch = query;
    renderLibrary();
  });
  var readCommunity = wireSearch('com-search', 'com-search-clear', 'aetherfx.search.community', function (query) {
    S.communitySearch = query;
    renderCommunity();
  });
  S.librarySearch = readLibrary();
  S.communitySearch = readCommunity();

  /* Remember which sections the user collapsed. */
  ['sec-core', 'sec-mine'].forEach(function (id) {
    var node = $(id);
    if (!node) return;
    try {
      var saved = window.sessionStorage.getItem('aetherfx.' + id);
      if (saved !== null) node.open = saved === '1';
    } catch (err) { /* private mode */ }
    node.addEventListener('toggle', function () {
      try { window.sessionStorage.setItem('aetherfx.' + id, node.open ? '1' : '0'); } catch (err) { /* ignore */ }
    });
  });

  /* Stage and Export are <details>; make them behave like real popovers. */
  wirePopover($('stage-pop'));
  wirePopover($('export-pop'), loadExportTargets);
  $('btn-export').addEventListener('click', function (ev) {
    if (exportEnabled()) return;
    ev.preventDefault();
    toast('load or generate an effect first', 'warn');
  });
  syncExportButton();

  window.addEventListener('beforeunload', function () { resetPreview(); });
}

var statusPending = false;
function pollStatus() {
  if (statusPending) return;
  statusPending = true;
  refreshStatus().then(function () { statusPending = false; }, function () { statusPending = false; });
}

function init() {
  /* The viewer module is deferred, so it is usually already there; if not,
   * its ready event attaches it and the studio upgrades in place. */
  attachViewer(window.aetherViewer);
  window.addEventListener('aether-viewer-ready', function (event) { attachViewer(event.detail); });
  wire();
  S.fps = parseInt($('sel-fps').value, 10) || 24;
  S.size = parseSize($('sel-size').value);
  S.loop = $('chk-loop').checked;

  /* Open something as soon as the engine answers: an already-open effect, else the first example.
   * If the page loads while the studio is still starting, retry on the next status polls. */
  var attempts = 0;
  function startup() {
    return refreshStatus().then(function (status) {
      var ready = status && status.engine && status.engine.ok;
      if (!ready) {
        if (++attempts < 30) setTimeout(startup, 2000);
        return null;
      }
      return refreshEffects().then(function (lists) {
        var hasOpen = lists && lists.open && lists.open.length;
        if (hasOpen) return refreshEffect().then(function () { if (!S.preview) startViewing(); });
        if (lists && lists.examples && lists.examples.length) {
          return loadEffect(lists.examples[0].path);   /* loadEffect already starts the view */
        }
        return null;
      });
    }).catch(function (err) {
      toast('startup: ' + (err && err.message ? err.message : 'failed'), 'error');
    });
  }
  startup();

  S.statusTimer = setInterval(pollStatus, 4000);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
