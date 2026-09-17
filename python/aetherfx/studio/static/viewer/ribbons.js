/* Trails, beams and decals.
 *
 * Trails and beams arrive as polylines; the viewer turns each one into a
 * camera-facing triangle strip on the CPU every frame (the strip has to face
 * the camera, and the camera moves).  All the ribbons of one node share one
 * geometry and one draw call.
 */

import * as THREE from 'three';
import { BEAM_FLARE_FRAGMENT, BEAM_FRAGMENT, BEAM_VERTEX, RIBBON_FRAGMENT, RIBBON_VERTEX } from './shaders.js';
import { LAYER_TRANSPARENT, applyBlend, colorOf } from './particles.js';

const TRAIL_STRIDE = 12;      // pos3, width, age_norm, u, color4, opacity, emissive
const BEAM_STRIDE = 5;        // pos3, width (m), intensity

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
 * strip builder
 * ------------------------------------------------------------------ */

class StripBuilder {
  constructor() {
    this.capacity = 0;
    this.indexCapacity = 0;
    this.vertexCount = 0;
    this.indexCount = 0;
    this.geometry = new THREE.BufferGeometry();
    this.ensure(256, 512);
  }

  /* Growing happens mid-build, so everything written so far is carried over. */
  ensure(vertices, indices) {
    if (vertices > this.capacity) {
      const capacity = Math.max(256, Math.ceil(vertices * 1.5));
      const written = this.vertexCount;
      const grow = (previous, components) => {
        const next = new Float32Array(capacity * components);
        if (previous && written) next.set(previous.subarray(0, written * components));
        return next;
      };
      this.position = grow(this.position, 3);
      this.color = grow(this.color, 4);
      this.uv = grow(this.uv, 2);
      this.emissive = grow(this.emissive, 1);
      this.geometry.setAttribute('position', new THREE.BufferAttribute(this.position, 3).setUsage(THREE.DynamicDrawUsage));
      this.geometry.setAttribute('color', new THREE.BufferAttribute(this.color, 4).setUsage(THREE.DynamicDrawUsage));
      this.geometry.setAttribute('uv', new THREE.BufferAttribute(this.uv, 2).setUsage(THREE.DynamicDrawUsage));
      this.geometry.setAttribute('emissive', new THREE.BufferAttribute(this.emissive, 1).setUsage(THREE.DynamicDrawUsage));
      this.capacity = capacity;
    }
    if (indices > this.indexCapacity) {
      const capacity = Math.max(512, Math.ceil(indices * 1.5));
      const next = new Uint32Array(capacity);
      if (this.index && this.indexCount) next.set(this.index.subarray(0, this.indexCount));
      this.index = next;
      this.geometry.setIndex(new THREE.BufferAttribute(this.index, 1).setUsage(THREE.DynamicDrawUsage));
      this.indexCapacity = capacity;
    }
  }

  begin() { this.vertexCount = 0; this.indexCount = 0; }

  /* Two vertices per sample, offset along the strip's side vector. */
  pushPair(x, y, z, sx, sy, sz, half, u, r, g, b, a, emissive) {
    const base = this.vertexCount;
    const p = this.position, c = this.color, t = this.uv, e = this.emissive;
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

  link(previousBase, base) {
    const i = this.index;
    let o = this.indexCount;
    i[o] = previousBase; i[o + 1] = previousBase + 1; i[o + 2] = base;
    i[o + 3] = previousBase + 1; i[o + 4] = base + 1; i[o + 5] = base;
    this.indexCount += 6;
  }

  end() {
    const geometry = this.geometry;
    ['position', 'color', 'uv', 'emissive'].forEach(function (name) {
      const attribute = geometry.getAttribute(name);
      attribute.needsUpdate = true;
      attribute.clearUpdateRanges();
    });
    geometry.index.needsUpdate = true;
    geometry.setDrawRange(0, this.indexCount);
    geometry.boundingSphere = null;
    geometry.boundingBox = null;
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

/* Vertex indices of one strip through a beam path.  `scale` is the ribbon's
 * half-width per unit of vertex width; when `decimate` is set, a vertex is kept
 * only once the path has travelled the ribbon's full width since the last one.
 * That is what keeps the wide outer glow from becoming a fan of spikes on a
 * fractal bolt, whose segments are far shorter than the glow is wide. */
function beamStripIndices(data, count, scale, decimate) {
  const out = [0];
  if (decimate) {
    let travelled = 0;
    for (let i = 1; i < count - 1; i++) {
      const o = i * BEAM_STRIDE, p = (i - 1) * BEAM_STRIDE;
      const dx = data[o] - data[p], dy = data[o + 1] - data[p + 1], dz = data[o + 2] - data[p + 2];
      travelled += Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (travelled >= 2 * scale * data[o + 3]) { out.push(i); travelled = 0; }
    }
  } else {
    for (let i = 1; i < count - 1; i++) out.push(i);
  }
  out.push(count - 1);
  return out;
}

/* One strip of one beam path: 5 floats per vertex (xyz, width in metres,
 * intensity).  The per-vertex "emissive" attribute carries intensity * path fade;
 * the fragment shader places the glow layers across the ribbon, exactly as the CPU
 * renderer does. */
function appendBeam(builder, data, count, cameraPosition, options) {
  if (count < 2) return;
  const index = beamStripIndices(data, count, options.scale, options.decimate);
  if (index.length < 2) return;
  builder.ensure(builder.vertexCount + index.length * 2, builder.indexCount + (index.length - 1) * 6);
  const color = options.color;
  const alpha = (color[3] === undefined ? 1 : color[3]) * options.alpha;
  const fade = options.fade;
  let previous = -1;
  TMP_PREV_SIDE.set(0, 0, 0);
  for (let k = 0; k < index.length; k++) {
    const o = index[k] * BEAM_STRIDE;
    const x = data[o], y = data[o + 1], z = data[o + 2];
    const before = index[Math.max(0, k - 1)] * BEAM_STRIDE;
    const after = index[Math.min(index.length - 1, k + 1)] * BEAM_STRIDE;
    TMP_TANGENT.set(data[after] - data[before], data[after + 1] - data[before + 1], data[after + 2] - data[before + 2]);
    if (TMP_TANGENT.lengthSq() < 1e-12) TMP_TANGENT.set(0, 1, 0);
    const along = count > 1 ? index[k] / (count - 1) : 0;
    const side = sideVector(TMP_TANGENT, x, y, z, cameraPosition, 0, TMP_PREV_SIDE);
    TMP_PREV_SIDE.copy(side);
    const base = builder.pushPair(x, y, z, side.x, side.y, side.z, data[o + 3] * options.scale, along,
                                  color[0], color[1], color[2], alpha, data[o + 4] * fade);
    if (previous >= 0) builder.link(previous, base);
    previous = base;
  }
}

/* An impact flare: one camera-facing quad, shaded radially by the flare shader. */
function appendFlare(builder, flare, camera, options) {
  const position = flare.position || [0, 0, 0];
  const radius = typeof flare.radius === 'number' ? flare.radius : 0;
  if (!(radius > 0)) return;
  builder.ensure(builder.vertexCount + 4, builder.indexCount + 6);
  const color = options.color;
  const alpha = (color[3] === undefined ? 1 : color[3]) * options.alpha;
  const gain = typeof flare.intensity === 'number' ? flare.intensity : 1;
  TMP_A.set(1, 0, 0).applyQuaternion(camera.quaternion).multiplyScalar(radius);
  TMP_B.set(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(radius);
  // (-,-) (+,-) then (-,+) (+,+): two pushPair rows the strip linker joins.
  const bottom = builder.pushPair(position[0] - TMP_B.x, position[1] - TMP_B.y, position[2] - TMP_B.z,
                                  TMP_A.x, TMP_A.y, TMP_A.z, 1, 0,
                                  color[0], color[1], color[2], alpha, gain);
  const top = builder.pushPair(position[0] + TMP_B.x, position[1] + TMP_B.y, position[2] + TMP_B.z,
                               TMP_A.x, TMP_A.y, TMP_A.z, 1, 1,
                               color[0], color[1], color[2], alpha, gain);
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

/* The beam / flare material: the cross-section lives in the fragment shader so
 * the bolt is one strip with one draw call instead of a stack of ribbons. */
export function makeBeamMaterial(flare) {
  return new THREE.ShaderMaterial({
    vertexShader: BEAM_VERTEX,
    fragmentShader: flare ? BEAM_FLARE_FRAGMENT : BEAM_FRAGMENT,
    uniforms: {
      uColor: { value: new THREE.Color(1, 1, 1) },
      uEmissive: { value: 4 },
      uCoreFrac: { value: 0.21 },
      uInnerFrac: { value: 0.63 },
      uOuterFrac: { value: 1 },
      uPulse: { value: -1 },
      uPremultiply: { value: 0 }
    },
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false
  });
}

function configureBeamMaterial(material, style, blend) {
  material.uniforms.uColor.value.copy(style.color);
  material.uniforms.uEmissive.value = style.emissive;
  material.uniforms.uCoreFrac.value = style.coreFrac;
  material.uniforms.uInnerFrac.value = style.innerFrac;
  material.uniforms.uOuterFrac.value = style.outerFrac;
  material.uniforms.uPulse.value = style.pulse;
  material.uniforms.uPremultiply.value = style.premultiply;
  applyBlend(material, blend);
  material.renderOrder = blend === 'additive' ? 20 : 10;
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
 * trails
 * ------------------------------------------------------------------ */

class RibbonNode {
  constructor(scene, material) {
    this.builder = new StripBuilder();
    this.material = material || makeRibbonMaterial();
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

export class RibbonRenderer {
  constructor(scene) {
    this.scene = scene;
    this.nodes = new Map();
  }

  node(key) {
    let node = this.nodes.get(key);
    if (!node) { node = new RibbonNode(this.scene); this.nodes.set(key, node); }
    return node;
  }

  beamNode(key, flare) {
    let node = this.nodes.get(key);
    if (!node) { node = new RibbonNode(this.scene, makeBeamMaterial(flare)); this.nodes.set(key, node); }
    return node;
  }

  update(frame, context) {
    const seen = new Set();
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

    (frame.beams || []).forEach((beam) => {
      const desc = context.materials[beam.material] || {};
      const blend = beam.blend || desc.blend || 'additive';
      const color = beam.color || [1, 1, 1, 1];
      let emissive = typeof beam.emissive === 'number' ? beam.emissive : 4;
      const opacity = typeof desc.opacity === 'number' ? desc.opacity : 1;
      if (typeof desc.emissive_intensity === 'number') emissive *= 1 + desc.emissive_intensity;
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
      const passes = split
        ? [{ scale: innerR, core: coreR / innerR, inner: 1, outer: 0, decimate: false },
           { scale: outerR, core: 0, inner: 0, outer: 1, decimate: true }]
        : [(function () {
            const scale = Math.max(innerR, outerR, 1e-5);
            return { scale: scale, core: Math.min(coreR / scale, 1), inner: Math.min(innerR / scale, 1),
                     outer: Math.min(outerR / scale, 1), decimate: false };
          })()];
      const pulse = typeof beam.pulse_phase === 'number' ? beam.pulse_phase : -1;
      const baseColor = colorOf(desc.base_color, [1, 1, 1]);
      const premultiply = blend === 'premultiplied' ? 1 : 0;
      // Ghosts first (they sit behind the live bolt), then the bolt, then the flares.
      passes.forEach((pass, index) => {
        [['ghosts', beam.ghosts || []], ['paths', beam.paths || []]].forEach(([which, paths]) => {
          const key = 'beam:' + beam.id + ':' + which + ':' + index;
          seen.add(key);
          const node = this.beamNode(key, false);
          const builder = node.builder;
          builder.begin();
          paths.forEach(function (path) {
            appendBeam(builder, path.vertices, path.count, cameraPosition,
                       { color: color, alpha: opacity, scale: pass.scale, decimate: pass.decimate,
                         fade: path.fade });
          });
          builder.end();
          configureBeamMaterial(node.material, {
            color: baseColor, emissive: emissive, coreFrac: pass.core, innerFrac: pass.inner,
            outerFrac: pass.outer, pulse: pulse, premultiply: premultiply
          }, blend);
          node.mesh.visible = builder.indexCount > 0;
          node.mesh.renderOrder = node.material.renderOrder;
        });
      });
      const style = { color: baseColor, emissive: emissive, coreFrac: 0.22, innerFrac: 0.66,
                      outerFrac: 1, pulse: -1, premultiply: premultiply };

      const flareKey = 'beam:' + beam.id + ':flares';
      seen.add(flareKey);
      const flareNode = this.beamNode(flareKey, true);
      flareNode.builder.begin();
      (beam.flares || []).forEach((flare) => {
        appendFlare(flareNode.builder, flare, context.camera, { color: color, alpha: opacity });
      });
      flareNode.builder.end();
      configureBeamMaterial(flareNode.material, style, blend);
      flareNode.mesh.visible = flareNode.builder.indexCount > 0;
      flareNode.mesh.renderOrder = flareNode.material.renderOrder;
    });

    this.nodes.forEach((node, key) => {
      if (seen.has(key)) return;
      node.dispose();
      this.nodes.delete(key);
    });
  }

  dispose() {
    this.nodes.forEach((node) => node.dispose());
    this.nodes.clear();
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
