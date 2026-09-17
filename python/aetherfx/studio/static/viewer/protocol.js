/* Wire protocol for the live viewer.
 *
 * One binary WebSocket message per frame:
 *     [u32 LE header_length][header JSON utf-8][blob]
 * The blob holds float32 / uint32 arrays at byte offsets named by the header,
 * so a frame becomes typed-array *views* over the received ArrayBuffer with no
 * copying.  See python/aetherfx/studio/stream.py for the authoritative spec.
 */

const DECODER = new TextDecoder();
const ARRAY_TYPES = { f32: Float32Array, u32: Uint32Array };

/* A view over the blob, or a copy when the encoder left the offset unaligned
 * (typed arrays demand a multiple of their element size). */
function arrayView(buffer, base, ref, count) {
  const Ctor = ARRAY_TYPES[ref.dtype] || Float32Array;
  const components = ref.components || 1;
  const start = base + (ref.offset | 0);
  const length = count * components;
  if (length <= 0) return new Ctor(0);
  if (start % Ctor.BYTES_PER_ELEMENT === 0) return new Ctor(buffer, start, length);
  return new Ctor(buffer.slice(start, start + length * Ctor.BYTES_PER_ELEMENT));
}

/* Decode one frame message into {time, frame, fps, systems, lights, ...} where
 * every system carries `views`: {position, velocity, size, ...} typed arrays. */
export function decodeFrame(buffer) {
  const head = new DataView(buffer);
  const headerLength = head.getUint32(0, true);
  const header = JSON.parse(DECODER.decode(new Uint8Array(buffer, 4, headerLength)));
  const base = 4 + headerLength;

  const systems = (header.systems || []).map(function (system) {
    const count = system.count | 0;
    const views = {};
    const refs = system.arrays || {};
    for (const name in refs) {
      if (!Object.prototype.hasOwnProperty.call(refs, name)) continue;
      views[name] = arrayView(buffer, base, refs[name], count);
    }
    return Object.assign({}, system, { count: count, views: views });
  });

  /* A beam's live paths (or its ghosts): one contiguous vertex run of 5 floats
   * per vertex (xyz, width in metres, intensity) and one record array of 4
   * floats per path (first vertex, vertex count, branch depth, fade).  Two
   * typed-array views, whatever the bolt's fractal depth - a lightning AOE is
   * ~620 paths a frame, and one object each was pure garbage. */
  const EMPTY_F32 = new Float32Array(0);
  const beamGroup = function (group) {
    if (!group) return { count: 0, vertices: EMPTY_F32, records: EMPTY_F32 };
    return {
      count: group.count | 0,
      vertices: arrayView(buffer, base, { offset: group.vertices, dtype: 'f32', components: 5 }, group.total | 0),
      records: arrayView(buffer, base, { offset: group.offset, dtype: 'f32', components: 4 }, group.count | 0)
    };
  };

  const beams = (header.beams || []).map(function (beam) {
    return Object.assign({}, beam, {
      paths: beamGroup(beam.paths),
      ghosts: beamGroup(beam.ghosts)
    });
  });

  const trails = (header.trails || []).map(function (trail) {
    return Object.assign({}, trail, {
      ribbons: (trail.ribbons || []).map(function (r) {
        return arrayView(buffer, base, { offset: r.offset, dtype: 'f32', components: 12 }, r.count | 0);
      })
    });
  });

  return Object.assign({}, header, { systems: systems, beams: beams, trails: trails });
}

/* Reconnecting WebSocket client that speaks the studio's command language. */
export class StreamClient {
  constructor(options) {
    const noop = function () {};
    this.url = options.url;
    this.onFrame = options.onFrame || noop;
    this.onResources = options.onResources || noop;
    this.onState = options.onState || noop;
    this.onError = options.onError || noop;
    this.onOpen = options.onOpen || noop;
    this.onClose = options.onClose || noop;
    this.socket = null;
    this.pending = [];
    this.attempts = 0;
    this.stopped = false;
    this.retryTimer = null;
  }

  get connected() {
    return !!this.socket && this.socket.readyState === WebSocket.OPEN;
  }

  connect() {
    if (this.stopped || this.socket) return;
    let socket;
    try {
      socket = new WebSocket(this.url);
    } catch (err) {
      this.scheduleRetry();
      return;
    }
    socket.binaryType = 'arraybuffer';
    this.socket = socket;

    socket.addEventListener('open', () => {
      this.attempts = 0;
      const queued = this.pending;
      this.pending = [];
      queued.forEach((message) => this.send(message));
      this.onOpen();
    });

    socket.addEventListener('message', (event) => {
      if (typeof event.data === 'string') {
        let payload;
        try { payload = JSON.parse(event.data); } catch (err) { return; }
        if (payload.type === 'resources') this.onResources(payload);
        else if (payload.type === 'state') this.onState(payload);
        else if (payload.type === 'error') this.onError(payload);
        return;
      }
      try {
        this.onFrame(decodeFrame(event.data));
      } catch (err) {
        this.onError({ code: 'decode_failed', message: String((err && err.message) || err) });
      }
    });

    socket.addEventListener('close', () => {
      this.socket = null;
      this.onClose();
      this.scheduleRetry();
    });

    socket.addEventListener('error', () => { /* 'close' does the recovery */ });
  }

  scheduleRetry() {
    if (this.stopped || this.retryTimer) return;
    const delay = Math.min(8000, 400 * Math.pow(1.7, this.attempts++));
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this.connect(); }, delay);
  }

  send(message) {
    if (this.connected) this.socket.send(JSON.stringify(message));
    else if (this.pending.length < 8) this.pending.push(message);
  }

  open() { this.send({ type: 'open' }); }
  resources() { this.send({ type: 'resources' }); }
  play(fps, loop, time, speed) {
    this.send({ type: 'play', fps: fps, loop: !!loop, time: time, speed: (speed > 0 ? speed : 1) });
  }
  pause() { this.send({ type: 'pause' }); }
  seek(time) { this.send({ type: 'seek', time: time }); }
  step(frames) { this.send({ type: 'step', frames: frames }); }

  close() {
    this.stopped = true;
    if (this.retryTimer) { clearTimeout(this.retryTimer); this.retryTimer = null; }
    if (this.socket) { try { this.socket.close(); } catch (err) { /* already gone */ } this.socket = null; }
  }
}
