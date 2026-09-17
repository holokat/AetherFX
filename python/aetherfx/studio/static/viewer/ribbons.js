/* Trails, beams and decals.
 *
 * Trails and beams arrive as polylines; the viewer turns each one into a
 * camera-facing triangle strip on the CPU every frame (the strip has to face
 * the camera, and the camera moves).  A trail node owns one strip and one draw
 * call; every beam in the frame - every path, every afterglow ghost, both
 * cross-section passes - shares one strip per blend mode, because everything
 * that used to be a per-beam uniform now travels in a vertex attribute.
 */

import * as THREE from 'three';
import { RIBBON_FRAGMENT, RIBBON_VERTEX } from './shaders.js';
import { LAYER_TRANSPARENT, applyBlend, colorOf } from './particles.js';

const TRAIL_STRIDE = 12;      // pos3, width, age_norm, u, color4, opacity, emissive
const BEAM_STRIDE = 5;        // pos3, width (m), intensity
const BEAM_RECORD = 4;        // one path inside a beam group: first vertex, count, depth, fade

/* Texture tiles per metre along a trail.
 *
 * The runtime already writes the only U a trail has: `u = cumulative distance +
 * uv_scroll * time` (docs/RUNTIME.md section 7), in metres, accumulated along
 * the source's whole path and never rebased when old vertices are dropped.  So
 * the texture stays anchored to the ground the emitter covered and `uv_scroll`
 * slides it along at its own rate - as long as the sampler wraps, which is what
 * resources.js sets wrapS to.  One tile per metre is the natural rate for a
 * coordinate the engine hands over in metres; a trail that wants another one
 * would need `min_vertex_distance` (or a tiling factor) in the trail header,
 * which the stream does not carry today. */
const TRAIL_U_PER_METRE = 1.0;

/* ------------------------------------------------------------------ *
 * the batched beam material
 * ------------------------------------------------------------------ */

/* Same cross-section as docs/RUNTIME.md section 11 and as the CPU reference
 * renderer's `beam_cross_section`, but every term that used to be a uniform -
 * the tint, the emissive gain, the pulse phase and the three layer radii - is a
 * vertex attribute, so a frame's 40-odd bolts draw as one mesh instead of 40.
 * The values are constant along a path and a triangle never spans two paths, so
 * the interpolators hand the fragment shader exactly the authored numbers.
 *
 * (`shaders.js` still holds the one-beam-per-uniform BEAM_* pair these are
 * derived from; keep the two in step - the kernel and its weights are the
 * renderer-parity contract.) */
const BEAM_BATCH_VERTEX = `
precision highp float;
attribute vec4 color;           // base_color * beam colour in .rgb, alpha in .a
attribute float gain;           // per-vertex intensity * path fade
attribute float beamEmissive;   // the beam's emissive, per vertex
attribute float pulse;          // [0,1) travelling pulse, < 0 = none
attribute vec3 frac;            // core / inner / outer radius, as fractions of the ribbon's
varying vec4 vColor;
varying vec2 vUv;
varying float vGain;
varying float vEmissive;
varying float vPulse;
varying vec3 vFrac;
void main() {
  vColor = color;
  vUv = uv;                     // x = along the bolt, y = across it
  vGain = gain;
  vEmissive = beamEmissive;
  vPulse = pulse;
  vFrac = frac;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const BEAM_KERNEL = `
float beamKernel(float t, float r) {
  if (r <= 0.0) return 0.0;
  float x = min(t / max(r, 1e-3), 1.0);
  float f = 1.0 - x * x;
  return f * f;
}
`;

const BEAM_BATCH_VARYINGS = `
varying vec4 vColor;
varying vec2 vUv;
varying float vGain;
varying float vEmissive;
varying float vPulse;
varying vec3 vFrac;
`;

const BEAM_BATCH_FRAGMENT = `
precision highp float;
uniform float uPremultiply;
${BEAM_BATCH_VARYINGS}
${BEAM_KERNEL}
void main() {
  float t = abs(vUv.y * 2.0 - 1.0);
  float core = beamKernel(t, vFrac.x) * 2.2;
  float inner = beamKernel(t, vFrac.y) * 0.8;
  float outer = beamKernel(t, vFrac.z) * 0.28;
  float luminance = core + inner + outer;
  if (luminance <= 0.0) discard;

  vec3 tint = vColor.rgb;
  vec3 hot = mix(tint, vec3(1.0), 0.85);
  vec3 rgb = hot * core + tint * (inner + outer);

  float gain = vGain;
  if (vPulse >= 0.0) {
    float d = abs(vUv.x - vPulse);
    d = min(d, 1.0 - d);
    gain *= 1.0 + 3.0 * exp(-(d * d) / 0.0036);
  }
  float alpha = clamp(luminance * gain, 0.0, 1.0) * vColor.a;
  if (alpha <= 0.0) discard;
  vec3 out_ = rgb * (vEmissive * gain);
  gl_FragColor = vec4(out_ * mix(1.0, alpha, uPremultiply), alpha);
}
`;

/* An impact flare: the same three-layer falloff, radial instead of across a
 * ribbon, on a camera-facing quad. */
const BEAM_BATCH_FLARE_FRAGMENT = `
precision highp float;
uniform float uPremultiply;
${BEAM_BATCH_VARYINGS}
${BEAM_KERNEL}
void main() {
  float t = length(vUv * 2.0 - 1.0);
  if (t >= 1.0) discard;
  float core = beamKernel(t, vFrac.x) * 2.2;
  float inner = beamKernel(t, vFrac.y) * 0.8;
  float outer = beamKernel(t, vFrac.z) * 0.28;
  float luminance = core + inner + outer;
  vec3 tint = vColor.rgb;
  vec3 rgb = mix(tint, vec3(1.0), 0.85) * core + tint * (inner + outer);
  float alpha = clamp(luminance * vGain, 0.0, 1.0) * vColor.a;
  if (alpha <= 0.0) discard;
  vec3 out_ = rgb * (vEmissive * vGain);
  gl_FragColor = vec4(out_ * mix(1.0, alpha, uPremultiply), alpha);
}
`;

/* A flare's cross-section is the profile the CPU renderer uses radially. */
const FLARE_CORE_FRAC = 0.22;
const FLARE_INNER_FRAC = 0.66;

/* ------------------------------------------------------------------ *
 * strip builders
 * ------------------------------------------------------------------ */

const TRAIL_LAYOUT = [['position', 3], ['color', 4], ['uv', 2], ['emissive', 1]];
const BEAM_LAYOUT = [['position', 3], ['color', 4], ['uv', 2], ['gain', 1],
                     ['beamEmissive', 1], ['pulse', 1], ['frac', 3]];

/* One dynamic BufferGeometry filled in place every frame.
 *
 * The buffers grow by doubling and never shrink, so a steady scene reaches its
 * capacity within the first frames and from then on the builder allocates
 * nothing at all: the typed arrays are written over, `addUpdateRange` uploads
 * only the part in use, and `setDrawRange` says how much of it to draw.  Nothing
 * here creates a geometry, a mesh or a Float32Array per frame, which is what
 * keeps `renderer.info.memory.geometries` flat and the garbage collector out of
 * the frame. */
class StripBuilder {
  constructor(layout) {
    this.layout = layout;
    this.arrays = {};
    this.capacity = 0;
    this.indexCapacity = 0;
    this.vertexCount = 0;
    this.indexCount = 0;
    this.geometry = new THREE.BufferGeometry();
    this.ensure(1024, 2048);
  }

  /* Growing happens mid-build, so everything written so far is carried over. */
  ensure(vertices, indices) {
    if (vertices > this.capacity) {
      const capacity = Math.max(1024, vertices, this.capacity * 2);
      const written = this.vertexCount;
      for (let i = 0; i < this.layout.length; i++) {
        const name = this.layout[i][0];
        const components = this.layout[i][1];
        const previous = this.arrays[name];
        const next = new Float32Array(capacity * components);
        if (previous && written) next.set(previous.subarray(0, written * components));
        this.arrays[name] = next;
        this.geometry.setAttribute(name, new THREE.BufferAttribute(next, components).setUsage(THREE.DynamicDrawUsage));
      }
      this.capacity = capacity;
    }
    if (indices > this.indexCapacity) {
      const capacity = Math.max(2048, indices, this.indexCapacity * 2);
      const next = new Uint32Array(capacity);
      if (this.index && this.indexCount) next.set(this.index.subarray(0, this.indexCount));
      this.index = next;
      this.geometry.setIndex(new THREE.BufferAttribute(this.index, 1).setUsage(THREE.DynamicDrawUsage));
      this.indexCapacity = capacity;
    }
  }

  begin() { this.vertexCount = 0; this.indexCount = 0; }

  link(previousBase, base) {
    const i = this.index;
    let o = this.indexCount;
    i[o] = previousBase; i[o + 1] = previousBase + 1; i[o + 2] = base;
    i[o + 3] = previousBase + 1; i[o + 4] = base + 1; i[o + 5] = base;
    this.indexCount += 6;
  }

  end() {
    const geometry = this.geometry;
    const vertices = this.vertexCount;
    for (let i = 0; i < this.layout.length; i++) {
      const attribute = geometry.getAttribute(this.layout[i][0]);
      // Upload only what is in use: the buffer stays at the high-water mark, and
      // a full bufferSubData of a megabyte-sized batch every frame is exactly
      // the cost this batching is here to remove.
      attribute.clearUpdateRanges();
      if (vertices) attribute.addUpdateRange(0, vertices * this.layout[i][1]);
      attribute.needsUpdate = true;
    }
    const index = geometry.index;
    index.clearUpdateRanges();
    if (this.indexCount) index.addUpdateRange(0, this.indexCount);
    index.needsUpdate = true;
    geometry.setDrawRange(0, this.indexCount);
    geometry.boundingSphere = null;
    geometry.boundingBox = null;
  }
}

class TrailBuilder extends StripBuilder {
  constructor() { super(TRAIL_LAYOUT); }

  /* Two vertices per sample, offset along the strip's side vector. */
  pushPair(x, y, z, sx, sy, sz, half, u, r, g, b, a, emissive) {
    const base = this.vertexCount;
    const p = this.arrays.position, c = this.arrays.color, t = this.arrays.uv, e = this.arrays.emissive;
    let o = base * 3;
    p[o] = x - sx * half; p[o + 1] = y - sy * half; p[o + 2] = z - sz * half;
    p[o + 3] = x + sx * half; p[o + 4] = y + sy * half; p[o + 5] = z + sz * half;
    o = base * 4;
    c[o] = r; c[o + 1] = g; c[o + 2] = b; c[o + 3] = a;
    c[o + 4] = r; c[o + 5] = g; c[o + 6] = b; c[o + 7] = a;
    o = base * 2;
    t[o] = u; t[o + 1] = 0; t[o + 2] = u; t[o + 3] = 1;
    e[base] = emissive; e[base + 1] = emissive;
    this.vertexCount += 2;
    return base;
  }
}

class BeamBuilder extends StripBuilder {
  constructor() { super(BEAM_LAYOUT); }

  /* `gain` is the vertex's own (intensity * path fade); `style` is everything
   * the beam says about itself, which used to be a material's uniforms. */
  pushPair(x, y, z, sx, sy, sz, half, u, gain, style) {
    const base = this.vertexCount;
    const a = this.arrays;
    const p = a.position, c = a.color, t = a.uv, g = a.gain, e = a.beamEmissive, u2 = a.pulse, f = a.frac;
    let o = base * 3;
    p[o] = x - sx * half; p[o + 1] = y - sy * half; p[o + 2] = z - sz * half;
    p[o + 3] = x + sx * half; p[o + 4] = y + sy * half; p[o + 5] = z + sz * half;
    o = base * 4;
    c[o] = style.r; c[o + 1] = style.g; c[o + 2] = style.b; c[o + 3] = style.a;
    c[o + 4] = style.r; c[o + 5] = style.g; c[o + 6] = style.b; c[o + 7] = style.a;
    o = base * 2;
    t[o] = u; t[o + 1] = 0; t[o + 2] = u; t[o + 3] = 1;
    g[base] = gain; g[base + 1] = gain;
    e[base] = style.emissive; e[base + 1] = style.emissive;
    u2[base] = style.pulse; u2[base + 1] = style.pulse;
    o = base * 3;
    f[o] = style.core; f[o + 1] = style.inner; f[o + 2] = style.outer;
    f[o + 3] = style.core; f[o + 4] = style.inner; f[o + 5] = style.outer;
    this.vertexCount += 2;
    return base;
  }
}

const TMP_A = new THREE.Vector3();
const TMP_B = new THREE.Vector3();
const TMP_SIDE = new THREE.Vector3();
const TMP_VIEW = new THREE.Vector3();
const TMP_TANGENT = new THREE.Vector3();
const TMP_AXIS = new THREE.Vector3();
const TMP_PREV_SIDE = new THREE.Vector3();

/* side = tangent x (camera - point), i.e. the strip turns to face the camera.
 *
 * `previous` is the side vector of the vertex before, and matters on a fractal
 * bolt where segments are far shorter than the glow is wide: a segment pointing
 * at the camera makes the cross product vanish (so its direction is noise), and a
 * sharp reversal flips the side vector, folding the quad into a bow-tie that
 * draws as a long hard triangle.  Keeping the previous vector in the first case
 * and flipping to agree with it in the second removes both. */
function sideVector(tangent, x, y, z, cameraPosition, twistRadians, previous) {
  TMP_VIEW.set(cameraPosition.x - x, cameraPosition.y - y, cameraPosition.z - z);
  TMP_SIDE.crossVectors(tangent, TMP_VIEW);
  const degenerate = TMP_SIDE.lengthSq() < 0.0009 * tangent.lengthSq() * TMP_VIEW.lengthSq();
  if (degenerate && previous && previous.lengthSq() > 0) TMP_SIDE.copy(previous);
  else if (TMP_SIDE.lengthSq() < 1e-12) TMP_SIDE.set(1, 0, 0);
  TMP_SIDE.normalize();
  if (previous && previous.lengthSq() > 0 && TMP_SIDE.dot(previous) < 0) TMP_SIDE.multiplyScalar(-1);
  if (twistRadians) {
    TMP_AXIS.copy(tangent).normalize();
    TMP_SIDE.applyAxisAngle(TMP_AXIS, twistRadians);
  }
  return TMP_SIDE;
}

/* One trail ribbon: 12 floats per vertex straight out of the frame message. */
function appendTrail(builder, data, count, cameraPosition, twistDegrees) {
  if (count < 2) return;
  builder.ensure(builder.vertexCount + count * 2, builder.indexCount + (count - 1) * 6);
  let previous = -1;
  for (let i = 0; i < count; i++) {
    const o = i * TRAIL_STRIDE;
    const x = data[o], y = data[o + 1], z = data[o + 2];
    const width = data[o + 3];
    const u = data[o + 5] * TRAIL_U_PER_METRE;
    const alpha = data[o + 9] * data[o + 10];
    const before = Math.max(0, i - 1) * TRAIL_STRIDE;
    const after = Math.min(count - 1, i + 1) * TRAIL_STRIDE;
    TMP_TANGENT.set(data[after] - data[before], data[after + 1] - data[before + 1], data[after + 2] - data[before + 2]);
    if (TMP_TANGENT.lengthSq() < 1e-12) TMP_TANGENT.set(0, 1, 0);
    const twist = twistDegrees ? (twistDegrees * Math.PI / 180) * (i / (count - 1)) : 0;
    const side = sideVector(TMP_TANGENT, x, y, z, cameraPosition, twist);
    const base = builder.pushPair(x, y, z, side.x, side.y, side.z, width * 0.5, u,
                                  data[o + 6], data[o + 7], data[o + 8], alpha, data[o + 11]);
    if (previous >= 0) builder.link(previous, base);
    previous = base;
  }
}

/* Scratch for one strip's vertex indices, reused by every path of every frame:
 * a `[]` per path per pass is ~1300 short-lived arrays a frame on a bolt-heavy
 * effect, which is the garbage that shows up as a long task. */
let STRIP_INDEX = new Int32Array(1024);

/* Vertex indices of one strip through a beam path, written into STRIP_INDEX;
 * returns how many.  The path starts at float `origin` inside its group's
 * vertex run, and the indices are absolute float offsets into that run.  `scale`
 * is the ribbon's half-width per unit of vertex width; when `decimate` is set, a
 * vertex is kept only once the path has travelled the ribbon's full width since
 * the last one.  That is what keeps the wide outer glow from becoming a fan of
 * spikes on a fractal bolt, whose segments are far shorter than the glow is
 * wide. */
function beamStripIndices(data, origin, count, scale, decimate) {
  if (STRIP_INDEX.length < count) STRIP_INDEX = new Int32Array(Math.max(count, STRIP_INDEX.length * 2));
  const out = STRIP_INDEX;
  let n = 0;
  out[n++] = origin;
  if (decimate) {
    let travelled = 0;
    for (let i = 1; i < count - 1; i++) {
      const o = origin + i * BEAM_STRIDE, p = o - BEAM_STRIDE;
      const dx = data[o] - data[p], dy = data[o + 1] - data[p + 1], dz = data[o + 2] - data[p + 2];
      travelled += Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (travelled >= 2 * scale * data[o + 3]) { out[n++] = o; travelled = 0; }
    }
  } else {
    for (let i = 1; i < count - 1; i++) out[n++] = origin + i * BEAM_STRIDE;
  }
  out[n++] = origin + (count - 1) * BEAM_STRIDE;
  return n;
}

/* One strip of one beam path, `count` vertices from vertex `first` of its
 * group's run: 5 floats per vertex (xyz, width in metres, intensity).  The
 * per-vertex `gain` carries intensity * path fade; the fragment shader places
 * the glow layers across the ribbon, exactly as the CPU renderer does. */
function appendBeam(builder, data, first, count, cameraPosition, scale, decimate, fade, style) {
  if (count < 2) return;
  const origin = first * BEAM_STRIDE;
  const n = beamStripIndices(data, origin, count, scale, decimate);
  if (n < 2) return;
  const index = STRIP_INDEX;
  builder.ensure(builder.vertexCount + n * 2, builder.indexCount + (n - 1) * 6);
  const span = 1 / ((count - 1) * BEAM_STRIDE);
  let previous = -1;
  TMP_PREV_SIDE.set(0, 0, 0);
  for (let k = 0; k < n; k++) {
    const o = index[k];
    const x = data[o], y = data[o + 1], z = data[o + 2];
    const before = index[k > 0 ? k - 1 : 0];
    const after = index[k + 1 < n ? k + 1 : n - 1];
    TMP_TANGENT.set(data[after] - data[before], data[after + 1] - data[before + 1], data[after + 2] - data[before + 2]);
    if (TMP_TANGENT.lengthSq() < 1e-12) TMP_TANGENT.set(0, 1, 0);
    const side = sideVector(TMP_TANGENT, x, y, z, cameraPosition, 0, TMP_PREV_SIDE);
    TMP_PREV_SIDE.copy(side);
    const base = builder.pushPair(x, y, z, side.x, side.y, side.z, data[o + 3] * scale,
                                  (o - origin) * span, data[o + 4] * fade, style);
    if (previous >= 0) builder.link(previous, base);
    previous = base;
  }
}

/* Every path of one beam group (its live paths, or its ghosts). */
function appendGroup(builder, group, cameraPosition, scale, decimate, style) {
  if (!group || !group.count) return;
  const records = group.records, vertices = group.vertices;
  for (let i = 0; i < group.count; i++) {
    const o = i * BEAM_RECORD;
    appendBeam(builder, vertices, records[o], records[o + 1], cameraPosition, scale, decimate, records[o + 3], style);
  }
}

/* An impact flare: one camera-facing quad, shaded radially by the flare shader. */
function appendFlare(builder, flare, camera, style) {
  const position = flare.position || [0, 0, 0];
  const radius = typeof flare.radius === 'number' ? flare.radius : 0;
  if (!(radius > 0)) return;
  builder.ensure(builder.vertexCount + 4, builder.indexCount + 6);
  const gain = typeof flare.intensity === 'number' ? flare.intensity : 1;
  TMP_A.set(1, 0, 0).applyQuaternion(camera.quaternion).multiplyScalar(radius);
  TMP_B.set(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(radius);
  // (-,-) (+,-) then (-,+) (+,+): two pushPair rows the strip linker joins.
  const bottom = builder.pushPair(position[0] - TMP_B.x, position[1] - TMP_B.y, position[2] - TMP_B.z,
                                  TMP_A.x, TMP_A.y, TMP_A.z, 1, 0, gain, style);
  const top = builder.pushPair(position[0] + TMP_B.x, position[1] + TMP_B.y, position[2] + TMP_B.z,
                               TMP_A.x, TMP_A.y, TMP_A.z, 1, 1, gain, style);
  builder.link(bottom, top);
}

/* ------------------------------------------------------------------ *
 * shared unlit HDR material
 * ------------------------------------------------------------------ */

export function makeRibbonMaterial() {
  const material = new THREE.ShaderMaterial({
    vertexShader: RIBBON_VERTEX,
    fragmentShader: RIBBON_FRAGMENT,
    uniforms: {
      uBaseColor: { value: new THREE.Color(1, 1, 1) },
      uEmissiveColor: { value: new THREE.Color(1, 1, 1) },
      uEmissiveIntensity: { value: 0 },
      uTexture: { value: null },
      uHasTexture: { value: 0 },
      uPremultiply: { value: 0 },
      uCircle: { value: 0 }
    },
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false
  });
  return material;
}

/* The beam / flare material.  The cross-section lives in the fragment shader and
 * everything that varies per beam lives in the vertex attributes, so a whole
 * frame of bolts is one strip with one draw call instead of five per beam. */
export function makeBeamMaterial(flare) {
  return new THREE.ShaderMaterial({
    vertexShader: BEAM_BATCH_VERTEX,
    fragmentShader: flare ? BEAM_BATCH_FLARE_FRAGMENT : BEAM_BATCH_FRAGMENT,
    uniforms: { uPremultiply: { value: 0 } },
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false
  });
}

function configureMaterial(material, desc, blend, context, textureId, circle) {
  material.uniforms.uBaseColor.value.copy(colorOf(desc.base_color, [1, 1, 1]));
  material.uniforms.uEmissiveColor.value.copy(colorOf(desc.emissive_color, [1, 1, 1]));
  material.uniforms.uEmissiveIntensity.value = typeof desc.emissive_intensity === 'number' ? desc.emissive_intensity : 0;
  const texture = context.texture(textureId) || context.texture(desc.base_texture);
  material.uniforms.uTexture.value = texture;
  material.uniforms.uHasTexture.value = texture ? 1 : 0;
  material.uniforms.uPremultiply.value = blend === 'premultiplied' ? 1 : 0;
  material.uniforms.uCircle.value = circle ? 1 : 0;
  applyBlend(material, blend);
  material.renderOrder = blend === 'additive' ? 20 : 10;
}

/* ------------------------------------------------------------------ *
 * trails and beams
 * ------------------------------------------------------------------ */

/* One trail node: its own material (texture, tint, emissive), its own strip. */
class RibbonNode {
  constructor(scene, builder, material) {
    this.builder = builder;
    this.material = material;
    this.mesh = new THREE.Mesh(this.builder.geometry, this.material);
    this.mesh.frustumCulled = false;
    this.mesh.layers.set(LAYER_TRANSPARENT);
    scene.add(this.mesh);
    this.scene = scene;
  }

  dispose() {
    this.scene.remove(this.mesh);
    this.builder.geometry.dispose();
    this.material.dispose();
  }
}

/* One bucket of beam geometry: everything in the frame that blends the same way.
 * These live for as long as the renderer does - an empty frame draws nothing
 * rather than disposing the buffers, so nothing churns when the bolts flicker
 * out and back. */
class BeamBatch extends RibbonNode {
  constructor(scene, blend, flare) {
    const material = makeBeamMaterial(flare);
    material.uniforms.uPremultiply.value = blend === 'premultiplied' ? 1 : 0;
    applyBlend(material, blend);
    material.renderOrder = blend === 'additive' ? 20 : 10;
    super(scene, new BeamBuilder(), material);
    this.mesh.renderOrder = material.renderOrder;
    this.mesh.visible = false;
  }

  finish() {
    this.builder.end();
    this.mesh.visible = this.builder.indexCount > 0;
  }
}

/* Per-beam style, reused every frame: the numbers the batched shader reads out
 * of its vertex attributes. */
const BEAM_STYLE = { r: 1, g: 1, b: 1, a: 1, emissive: 4, pulse: -1, core: 0, inner: 0, outer: 0 };

/* rgb from a material / beam colour without allocating a THREE.Color per beam. */
const WHITE_RGB = [1, 1, 1];
function rgbOf(value, fallback) {
  return Array.isArray(value) && value.length >= 3 ? value : fallback;
}

export class RibbonRenderer {
  constructor(scene) {
    this.scene = scene;
    this.nodes = new Map();        // trails, keyed by 'trail:<id>'
    this.batches = new Map();      // beams, keyed by '<blend>' / '<blend>:flare'
    this.seen = new Set();
  }

  node(key) {
    let node = this.nodes.get(key);
    if (!node) {
      node = new RibbonNode(this.scene, new TrailBuilder(), makeRibbonMaterial());
      this.nodes.set(key, node);
    }
    return node;
  }

  batch(blend, flare) {
    const key = flare ? blend + ':flare' : blend;
    let batch = this.batches.get(key);
    if (!batch) { batch = new BeamBatch(this.scene, blend, flare); this.batches.set(key, batch); }
    return batch;
  }

  update(frame, context) {
    const seen = this.seen;
    seen.clear();
    const cameraPosition = context.camera.position;

    (frame.trails || []).forEach((trail) => {
      const key = 'trail:' + trail.id;
      seen.add(key);
      const node = this.node(key);
      const builder = node.builder;
      builder.begin();
      (trail.ribbons || []).forEach(function (ribbon) {
        appendTrail(builder, ribbon, ribbon.length / TRAIL_STRIDE | 0, cameraPosition, trail.twist_deg || 0);
      });
      builder.end();
      const desc = context.materials[trail.material] || {};
      configureMaterial(node.material, desc, trail.blend || desc.blend || 'additive', context, desc.base_texture, false);
      node.mesh.visible = builder.indexCount > 0;
      node.mesh.renderOrder = node.material.renderOrder;
    });

    this.batches.forEach(function (batch) { batch.builder.begin(); });
    const beams = frame.beams || [];
    const style = BEAM_STYLE;
    for (let b = 0; b < beams.length; b++) {
      const beam = beams[b];
      const desc = context.materials[beam.material] || {};
      const blend = beam.blend || desc.blend || 'additive';
      const color = beam.color || WHITE_RGB;
      const base = rgbOf(desc.base_color, WHITE_RGB);
      const opacity = typeof desc.opacity === 'number' ? desc.opacity : 1;
      let emissive = typeof beam.emissive === 'number' ? beam.emissive : 4;
      if (typeof desc.emissive_intensity === 'number') emissive *= 1 + desc.emissive_intensity;
      style.r = base[0] * color[0];
      style.g = base[1] * color[1];
      style.b = base[2] * color[2];
      style.a = (color[3] === undefined ? 1 : color[3]) * opacity;
      style.emissive = emissive;
      style.pulse = typeof beam.pulse_phase === 'number' ? beam.pulse_phase : -1;
      // A bolt keeps re-rolling its paths through the dark gaps between flashes.
      // `blend(rgb * emissive * gain, alpha)` is exactly zero at alpha 0 (every
      // fragment discards) and, under additive blending, at emissive 0 - so there
      // is nothing to build. `stream.py`'s beam_is_dark() drops these before they
      // reach the wire; this is the same test for a source that does not.
      if (style.a === 0 || (style.emissive === 0 && blend === 'additive')) continue;

      const coreWidth = typeof beam.core_width === 'number' ? beam.core_width : 0.55;
      const glowWidth = typeof beam.glow_width === 'number' ? beam.glow_width : 2.6;
      // Radii as fractions of `width`. A ribbon wider than its segments are long
      // rasterises as a fan of spikes, so a wide outer glow gets its own strip
      // through a path decimated to its own width; the two sum to exactly the
      // one-pass cross-section (docs/RUNTIME.md section 11).
      const coreR = 0.5 * coreWidth;
      const innerR = 1.5 * coreWidth;
      const outerR = 0.5 * glowWidth;
      const split = outerR > innerR * 1.05 && innerR > 1e-5;
      const builder = this.batch(blend, false).builder;
      // Ghosts first (they sit behind the live bolt), then the bolt.
      if (split) {
        style.core = coreR / innerR; style.inner = 1; style.outer = 0;
        this.appendPaths(builder, beam, cameraPosition, innerR, false, style);
        style.core = 0; style.inner = 0; style.outer = 1;
        this.appendPaths(builder, beam, cameraPosition, outerR, true, style);
      } else {
        const scale = Math.max(innerR, outerR, 1e-5);
        style.core = Math.min(coreR / scale, 1);
        style.inner = Math.min(innerR / scale, 1);
        style.outer = Math.min(outerR / scale, 1);
        this.appendPaths(builder, beam, cameraPosition, scale, false, style);
      }

      const flares = beam.flares;
      if (flares && flares.length) {
        const flareBuilder = this.batch(blend, true).builder;
        style.core = FLARE_CORE_FRAC; style.inner = FLARE_INNER_FRAC; style.outer = 1; style.pulse = -1;
        for (let f = 0; f < flares.length; f++) appendFlare(flareBuilder, flares[f], context.camera, style);
      }
    }
    this.batches.forEach(function (batch) { batch.finish(); });

    this.nodes.forEach((node, key) => {
      if (seen.has(key)) return;
      node.dispose();
      this.nodes.delete(key);
    });
  }

  /* Ghosts then live paths, both strips of the same pass into the same buffer.
   * A group is two typed arrays: the vertex run and one 4-float record per path
   * (first vertex, vertex count, branch depth, fade). */
  appendPaths(builder, beam, cameraPosition, scale, decimate, style) {
    appendGroup(builder, beam.ghosts, cameraPosition, scale, decimate, style);
    appendGroup(builder, beam.paths, cameraPosition, scale, decimate, style);
  }

  dispose() {
    this.nodes.forEach((node) => node.dispose());
    this.nodes.clear();
    this.batches.forEach((batch) => batch.dispose());
    this.batches.clear();
  }
}

/* ------------------------------------------------------------------ *
 * decals: a quad lying on the ground
 * ------------------------------------------------------------------ */

const DECAL_GEOMETRY = new THREE.PlaneGeometry(1, 1);

export class DecalRenderer {
  constructor(scene) {
    this.scene = scene;
    this.decals = new Map();
  }

  update(frame, context) {
    const seen = new Set();
    (frame.decals || []).forEach((decal) => {
      const key = decal.id;
      seen.add(key);
      let entry = this.decals.get(key);
      if (!entry) {
        const material = makeRibbonMaterial();
        const mesh = new THREE.Mesh(DECAL_GEOMETRY, material);
        mesh.layers.set(LAYER_TRANSPARENT);
        mesh.rotation.x = -Math.PI / 2;
        this.scene.add(mesh);
        entry = { mesh: mesh, material: material };
        this.decals.set(key, entry);
      }
      const desc = context.materials[decal.material] || {};
      const blend = decal.blend || desc.blend || 'alpha';
      const position = decal.position || [0, 0, 0];
      const size = decal.size || [1, 1];
      entry.mesh.position.set(position[0], (position[1] || 0) + 0.005, position[2]);
      entry.mesh.rotation.set(-Math.PI / 2, 0, -(decal.rotation_deg || 0) * Math.PI / 180);
      entry.mesh.scale.set(size[0] || 1, size[1] || 1, 1);

      // the ribbon material reads colour from the vertex attribute; a decal has one
      const color = decal.color || [1, 1, 1, 1];
      const alpha = (color[3] === undefined ? 1 : color[3]) * (typeof decal.opacity === 'number' ? decal.opacity : 1);
      setFlatColor(entry.mesh, color, alpha, typeof decal.emissive === 'number' ? decal.emissive : 0);
      configureMaterial(entry.material, desc, blend, context, decal.texture, decal.circle !== false && !decal.texture);
      entry.mesh.renderOrder = entry.material.renderOrder - 5;   // under the particles
      entry.mesh.visible = alpha > 0.001;
    });
    this.decals.forEach((entry, key) => {
      if (seen.has(key)) return;
      this.scene.remove(entry.mesh);
      entry.material.dispose();
      this.decals.delete(key);
    });
  }

  dispose() {
    this.decals.forEach((entry) => { this.scene.remove(entry.mesh); entry.material.dispose(); });
    this.decals.clear();
  }
}

/* The ribbon shader reads per-vertex colour; give the decal quad its own copy. */
function setFlatColor(mesh, color, alpha, emissive) {
  let geometry = mesh.geometry;
  if (geometry === DECAL_GEOMETRY) {
    geometry = DECAL_GEOMETRY.clone();
    mesh.geometry = geometry;
  }
  let attribute = geometry.getAttribute('color');
  const count = geometry.getAttribute('position').count;
  if (!attribute) {
    attribute = new THREE.BufferAttribute(new Float32Array(count * 4), 4);
    geometry.setAttribute('color', attribute);
    geometry.setAttribute('emissive', new THREE.BufferAttribute(new Float32Array(count), 1));
  }
  const emissiveAttribute = geometry.getAttribute('emissive');
  for (let i = 0; i < count; i++) {
    attribute.array[i * 4] = color[0];
    attribute.array[i * 4 + 1] = color[1];
    attribute.array[i * 4 + 2] = color[2];
    attribute.array[i * 4 + 3] = alpha;
    emissiveAttribute.array[i] = emissive;
  }
  attribute.needsUpdate = true;
  emissiveAttribute.needsUpdate = true;
}
