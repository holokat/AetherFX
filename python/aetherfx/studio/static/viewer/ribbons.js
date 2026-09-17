/* Trails, beams and decals.
 *
 * Trails and beams arrive as polylines; the viewer turns each one into a
 * camera-facing triangle strip on the CPU every frame (the strip has to face
 * the camera, and the camera moves).  All the ribbons of one node share one
 * geometry and one draw call.
 */

import * as THREE from 'three';
import { RIBBON_FRAGMENT, RIBBON_VERTEX } from './shaders.js';
import { LAYER_TRANSPARENT, applyBlend, colorOf } from './particles.js';

const TRAIL_STRIDE = 12;      // pos3, width, age_norm, u, color4, opacity, emissive

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

/* side = tangent x (camera - point), i.e. the strip turns to face the camera. */
function sideVector(tangent, x, y, z, cameraPosition, twistRadians) {
  TMP_VIEW.set(cameraPosition.x - x, cameraPosition.y - y, cameraPosition.z - z);
  TMP_SIDE.crossVectors(tangent, TMP_VIEW);
  if (TMP_SIDE.lengthSq() < 1e-12) TMP_SIDE.set(1, 0, 0);
  TMP_SIDE.normalize();
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

/* One beam polyline: xyz triplets plus node-level width / colour / pulse. */
function appendBeam(builder, data, count, cameraPosition, options) {
  if (count < 2) return;
  builder.ensure(builder.vertexCount + count * 2, builder.indexCount + (count - 1) * 6);
  const color = options.color, width = options.width, emissive = options.emissive;
  const pulse = options.pulsePhase;
  let previous = -1;
  for (let i = 0; i < count; i++) {
    const o = i * 3;
    const x = data[o], y = data[o + 1], z = data[o + 2];
    const before = Math.max(0, i - 1) * 3;
    const after = Math.min(count - 1, i + 1) * 3;
    TMP_TANGENT.set(data[after] - data[before], data[after + 1] - data[before + 1], data[after + 2] - data[before + 2]);
    if (TMP_TANGENT.lengthSq() < 1e-12) TMP_TANGENT.set(0, 1, 0);
    const along = count > 1 ? i / (count - 1) : 0;
    let boost = 1.0;
    if (pulse >= 0) {
      const d = along - pulse;
      boost += 2.5 * Math.exp(-(d * d) / 0.006);
    }
    const side = sideVector(TMP_TANGENT, x, y, z, cameraPosition, 0);
    const base = builder.pushPair(x, y, z, side.x, side.y, side.z, width * 0.5, along,
                                  color[0] * boost, color[1] * boost, color[2] * boost,
                                  (color[3] === undefined ? 1 : color[3]) * options.alpha, emissive * boost);
    if (previous >= 0) builder.link(previous, base);
    previous = base;
  }
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
  constructor(scene) {
    this.builder = new StripBuilder();
    this.material = makeRibbonMaterial();
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
      const emissive = typeof beam.emissive === 'number' ? beam.emissive : 4;
      const width = typeof beam.width === 'number' ? beam.width : 0.05;
      const pulse = typeof beam.pulse_phase === 'number' ? beam.pulse_phase : -1;
      // two passes: a wide soft halo and a thin bright core
      [['beam:' + beam.id + ':halo', width * 2.6, 0.32, emissive * 0.55],
       ['beam:' + beam.id + ':core', width * 0.55, 1.0, emissive * 1.8]].forEach(([key, w, alpha, emit]) => {
        seen.add(key);
        const node = this.node(key);
        const builder = node.builder;
        builder.begin();
        (beam.polylines || []).forEach(function (polyline) {
          appendBeam(builder, polyline, polyline.length / 3 | 0, cameraPosition,
                     { width: w, color: color, alpha: alpha, emissive: emit, pulsePhase: pulse });
        });
        builder.end();
        configureMaterial(node.material, desc, blend, context, desc.base_texture, false);
        node.mesh.visible = builder.indexCount > 0;
        node.mesh.renderOrder = node.material.renderOrder;
      });
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
