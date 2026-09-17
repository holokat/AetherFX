/* Particle systems: instanced billboards and instanced meshes.
 *
 * One renderable per frame system id, kept alive between frames so buffers are
 * reused; capacity grows geometrically and `instanceCount` follows the frame's
 * particle count, which the engine changes constantly.
 */

import * as THREE from 'three';
import { BILLBOARD_FRAGMENT, BILLBOARD_VERTEX, MAX_LIGHTS } from './shaders.js';

export const LAYER_OPAQUE = 0;
export const LAYER_TRANSPARENT = 1;

/* alpha first, then additive on top (per the brief and how the CPU renderer reads) */
const ORDER_ALPHA = 10;
const ORDER_ADDITIVE = 20;

const QUAD_CORNERS = new Float32Array([-0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, 0.5]);
const QUAD_INDEX = [0, 1, 2, 0, 2, 3];

/* Instance attribute layout: name -> components in the frame's arrays. */
const BILLBOARD_ATTRIBUTES = [
  ['iPosition', 'position', 3, [0, 0, 0]],
  ['iVelocity', 'velocity', 3, [0, 0, 0]],
  ['iSize', 'size', 1, [0.1]],
  ['iRotation', 'rotation', 1, [0]],
  ['iColor', 'color', 4, [1, 1, 1, 1]],
  ['iEmissive', 'emissive', 1, [0]],
  ['iAge', 'age_norm', 1, [0]]
];

export function applyBlend(material, blend) {
  material.transparent = true;
  material.depthWrite = false;
  material.depthTest = true;
  if (blend === 'additive') {
    material.blending = THREE.AdditiveBlending;
  } else if (blend === 'premultiplied') {
    material.blending = THREE.CustomBlending;
    material.blendEquation = THREE.AddEquation;
    material.blendSrc = THREE.OneFactor;
    material.blendDst = THREE.OneMinusSrcAlphaFactor;
    material.blendSrcAlpha = THREE.OneFactor;
    material.blendDstAlpha = THREE.OneMinusSrcAlphaFactor;
  } else {
    material.blending = THREE.NormalBlending;
  }
  return material;
}

export function colorOf(value, fallback) {
  const rgb = Array.isArray(value) && value.length >= 3 ? value : (fallback || [1, 1, 1]);
  return new THREE.Color(rgb[0], rgb[1], rgb[2]);
}

function numberOf(value, fallback) {
  return typeof value === 'number' && isFinite(value) ? value : fallback;
}

/* Uniform objects every particle material shares by identity: set once per frame. */
export function makeSharedUniforms() {
  const position = [], color = [], radius = [];
  for (let i = 0; i < MAX_LIGHTS; i++) {
    position.push(new THREE.Vector3());
    color.push(new THREE.Color(0, 0, 0));
    radius.push(1);
  }
  return {
    uTime: { value: 0 },
    uDepth: { value: null },
    uResolution: { value: new THREE.Vector2(1, 1) },
    uNear: { value: 0.1 },
    uFar: { value: 1000 },
    uAmbient: { value: new THREE.Color(0.05, 0.055, 0.07) },
    uLightCount: { value: 0 },
    uLightPosition: { value: position },
    uLightColor: { value: color },
    uLightRadius: { value: radius }
  };
}

/* ------------------------------------------------------------------ *
 * billboards
 * ------------------------------------------------------------------ */

class BillboardSystem {
  constructor(shared, noiseTexture) {
    this.shared = shared;
    this.capacity = 0;
    this.attributes = {};
    this.geometry = new THREE.InstancedBufferGeometry();
    this.geometry.setIndex(QUAD_INDEX);
    this.geometry.setAttribute('corner', new THREE.BufferAttribute(QUAD_CORNERS, 2));
    this.geometry.instanceCount = 0;

    this.material = new THREE.ShaderMaterial({
      vertexShader: BILLBOARD_VERTEX,
      fragmentShader: BILLBOARD_FRAGMENT,
      uniforms: Object.assign({
        uBaseColor: { value: new THREE.Color(1, 1, 1) },
        uOpacity: { value: 1 },
        uEmissiveColor: { value: new THREE.Color(1, 1, 1) },
        uEmissiveIntensity: { value: 0 },
        uPremultiply: { value: 0 },
        uSprite: { value: null },
        uHasSprite: { value: 0 },
        uSheet: { value: new THREE.Vector4(1, 1, 1, 0) },
        uNoise: { value: noiseTexture },
        uDissolve: { value: 0 },
        uErosion: { value: 0 },
        uSoftDistance: { value: 0 },
        uStretch: { value: 0 },
        uStretched: { value: 0 },
        uLit: { value: 0 }
      }, shared),
      depthWrite: false,
      transparent: true,
      side: THREE.DoubleSide          // a camera-facing quad is never a back face
    });
    this.mesh = new THREE.Mesh(this.geometry, this.material);
    this.mesh.frustumCulled = false;                  // instances live in the attributes
    this.mesh.layers.set(LAYER_TRANSPARENT);
    this.keys = new Float32Array(0);      // view depth per instance, for sorting
  }

  ensure(count) {
    // capacity 0 means the attributes do not exist yet, so an empty system
    // still has to allocate before anything writes into it.
    if (this.capacity > 0 && count <= this.capacity) return;
    const capacity = Math.max(256, Math.ceil(count * 1.5));
    BILLBOARD_ATTRIBUTES.forEach(([name, , components]) => {
      const attribute = new THREE.InstancedBufferAttribute(new Float32Array(capacity * components), components);
      attribute.setUsage(THREE.DynamicDrawUsage);
      this.attributes[name] = attribute;
      this.geometry.setAttribute(name, attribute);
    });
    this.capacity = capacity;
    this.keys = new Float32Array(capacity);
  }

  /* Back-to-front instance order for alpha systems that ask for it. */
  sortOrder(positions, count, viewMatrix) {
    const m = viewMatrix.elements;
    const keys = this.keys;
    const indices = [];
    for (let i = 0; i < count; i++) {
      const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
      keys[i] = -(m[2] * x + m[6] * y + m[10] * z + m[14]);   // distance along the view axis
      indices.push(i);
    }
    indices.sort((a, b) => keys[b] - keys[a]);
    return indices;
  }

  writeAttributes(system, count, order) {
    BILLBOARD_ATTRIBUTES.forEach(([name, source, components, fallback]) => {
      const attribute = this.attributes[name];
      const target = attribute.array;
      const view = system.views[source];
      if (!view || view.length < count * components) {
        for (let i = 0; i < count; i++) {
          for (let c = 0; c < components; c++) target[i * components + c] = fallback[c];
        }
      } else if (order) {
        for (let i = 0; i < count; i++) {
          const src = order[i] * components, dst = i * components;
          for (let c = 0; c < components; c++) target[dst + c] = view[src + c];
        }
      } else {
        target.set(view.subarray(0, count * components));
      }
      attribute.needsUpdate = true;
      attribute.clearUpdateRanges();
      attribute.addUpdateRange(0, count * components);
    });
  }

  update(system, context) {
    const count = Math.max(0, system.count | 0);
    this.ensure(count);
    const material = context.materials[system.material] || {};
    const blend = system.blend || material.blend || 'additive';
    const sorted = system.sort && blend !== 'additive' && count > 1
      ? this.sortOrder(system.views.position, count, context.camera.matrixWorldInverse)
      : null;
    this.writeAttributes(system, count, sorted);
    this.geometry.instanceCount = count;

    const uniforms = this.material.uniforms;
    uniforms.uBaseColor.value.copy(colorOf(material.base_color, [1, 1, 1]));
    uniforms.uOpacity.value = numberOf(material.opacity, 1);
    uniforms.uEmissiveColor.value.copy(colorOf(material.emissive_color, [1, 1, 1]));
    uniforms.uEmissiveIntensity.value = numberOf(material.emissive_intensity, 0);
    uniforms.uDissolve.value = numberOf(material.dissolve, 0);
    uniforms.uErosion.value = numberOf(material.erosion, 0);
    uniforms.uNoise.value = context.texture(material.noise_texture) || context.noiseTexture;
    uniforms.uLit.value = material.shading === 'lit' ? 1 : 0;
    uniforms.uPremultiply.value = blend === 'premultiplied' ? 1 : 0;
    uniforms.uStretch.value = numberOf(system.velocity_stretch, 0);
    uniforms.uStretched.value = (system.render_mode === 'stretched_billboard' || system.align_to_velocity) ? 1 : 0;

    const softOn = context.softParticles && material.soft_particle !== false;
    uniforms.uSoftDistance.value = softOn
      ? Math.max(0, numberOf(system.soft_particle_distance, numberOf(material.depth_fade, 0.1)))
      : 0;

    const sprite = context.texture(system.sprite) || context.texture(material.base_texture);
    uniforms.uSprite.value = sprite;
    uniforms.uHasSprite.value = sprite ? 1 : 0;
    const info = context.textureInfo(system.sprite) || context.textureInfo(material.base_texture) || {};
    uniforms.uSheet.value.set(
      Math.max(1, system.sprite_columns || 1),
      Math.max(1, system.sprite_rows || 1),
      Math.max(1, info.frames || 1),
      Math.max(0, system.sprite_fps || 0)
    );

    applyBlend(this.material, blend);
    this.mesh.renderOrder = blend === 'additive' ? ORDER_ADDITIVE : ORDER_ALPHA;
    this.mesh.visible = count > 0;
    return count;
  }

  dispose() {
    this.geometry.dispose();
    this.material.dispose();
  }
}

/* ------------------------------------------------------------------ *
 * mesh particles
 * ------------------------------------------------------------------ */

const TMP_POSITION = new THREE.Vector3();
const TMP_QUATERNION = new THREE.Quaternion();
const TMP_SCALE = new THREE.Vector3();
const TMP_MATRIX = new THREE.Matrix4();
const TMP_COLOR = new THREE.Color();

/* A fresnel rim on top of the standard material, in HDR so it blooms. */
function addFresnel(material, power, color, intensity) {
  material.userData.fresnel = {
    power: { value: power },
    color: { value: color.clone() },
    intensity: { value: Math.max(intensity, 0.35) * 2.0 }
  };
  material.onBeforeCompile = function (shader) {
    shader.uniforms.uFresnelPower = material.userData.fresnel.power;
    shader.uniforms.uFresnelColor = material.userData.fresnel.color;
    shader.uniforms.uFresnelIntensity = material.userData.fresnel.intensity;
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        uniform float uFresnelPower;
        uniform vec3 uFresnelColor;
        uniform float uFresnelIntensity;`)
      .replace('#include <dithering_fragment>', `#include <dithering_fragment>
        {
          vec3 viewDir = normalize(vViewPosition);
          float rim = pow(1.0 - clamp(dot(normalize(normal), viewDir), 0.0, 1.0), uFresnelPower);
          gl_FragColor.rgb += rim * uFresnelColor * uFresnelIntensity;
        }`);
  };
  material.customProgramCacheKey = () => 'fresnel' + power.toFixed(2);
}

class MeshParticleSystem {
  constructor(scene) {
    this.scene = scene;
    this.byVariant = new Map();       // variant index -> InstancedMesh
    this.material = null;
    this.materialKey = null;          // "<material id>|<blend>"; a change rebuilds it
  }

  buildMaterial(desc, blend) {
    const base = colorOf(desc.base_color, [1, 1, 1]);
    const luminance = base.r * 0.3 + base.g * 0.6 + base.b * 0.1;
    const lit = desc.shading === 'lit';
    // Light, lit, alpha-blended materials read as ice/glass: physical transmission suits them.
    const physical = lit && blend === 'alpha' && luminance > 0.55;
    const material = physical
      ? new THREE.MeshPhysicalMaterial({ transmission: 0.55, thickness: 0.4, roughness: 0.25, metalness: 0.0, ior: 1.31 })
      : new THREE.MeshStandardMaterial({ roughness: lit ? 0.55 : 1.0, metalness: 0.0 });
    material.color = base;
    material.emissive = colorOf(desc.emissive_color, [1, 1, 1]).multiplyScalar(numberOf(desc.emissive_intensity, 0));
    material.emissiveIntensity = 1.0;
    material.side = desc.double_sided === false ? THREE.FrontSide : THREE.DoubleSide;
    material.opacity = numberOf(desc.opacity, 1);
    if (!lit) { material.emissive = base.clone().multiplyScalar(Math.max(1.0, numberOf(desc.emissive_intensity, 1))); }
    if (blend === 'additive' || blend === 'premultiplied' || material.opacity < 1 || physical) {
      applyBlend(material, blend);
      if (physical) material.depthWrite = true;
    }
    const power = numberOf(desc.fresnel_power, 0);
    if (power > 0) addFresnel(material, power, colorOf(desc.emissive_color, [1, 1, 1]), numberOf(desc.emissive_intensity, 0));
    return material;
  }

  meshFor(variant, geometry, transparent) {
    let mesh = this.byVariant.get(variant);
    if (mesh && mesh.geometry !== geometry) { this.remove(variant); mesh = null; }
    if (!mesh) {
      mesh = new THREE.InstancedMesh(geometry, this.material, 64);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.frustumCulled = false;
      mesh.count = 0;
      this.byVariant.set(variant, mesh);
      this.scene.add(mesh);
    }
    mesh.material = this.material;
    mesh.layers.set(transparent ? LAYER_TRANSPARENT : LAYER_OPAQUE);
    return mesh;
  }

  grow(variant, mesh, needed, transparent) {
    if (needed <= mesh.instanceMatrix.count) return mesh;
    const geometry = mesh.geometry;
    this.remove(variant);
    const grown = new THREE.InstancedMesh(geometry, this.material, Math.max(64, Math.ceil(needed * 1.5)));
    grown.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    grown.frustumCulled = false;
    grown.count = 0;
    grown.layers.set(transparent ? LAYER_TRANSPARENT : LAYER_OPAQUE);
    this.byVariant.set(variant, grown);
    this.scene.add(grown);
    return grown;
  }

  remove(variant) {
    const mesh = this.byVariant.get(variant);
    if (!mesh) return;
    this.scene.remove(mesh);
    mesh.dispose();
    this.byVariant.delete(variant);
  }

  update(system, context) {
    const count = Math.max(0, system.count | 0);
    const desc = context.materials[system.material] || {};
    const blend = system.blend || desc.blend || 'alpha';
    const transparent = blend === 'additive' || blend === 'premultiplied' || numberOf(desc.opacity, 1) < 1;
    const geometries = context.meshGeometries(system.mesh);
    if (!geometries || !geometries.length) {
      this.byVariant.forEach((mesh) => { mesh.count = 0; });
      return 0;
    }
    if (!this.material || this.materialKey !== system.material + '|' + blend) {
      if (this.material) this.material.dispose();
      this.material = this.buildMaterial(desc, blend);
      this.materialKey = system.material + '|' + blend;
    }

    // bucket the instances by variant so each InstancedMesh gets a contiguous run
    const variants = system.views.variant;
    const buckets = new Map();
    for (let i = 0; i < count; i++) {
      const v = variants ? Math.min(geometries.length - 1, variants[i] | 0) : 0;
      let bucket = buckets.get(v);
      if (!bucket) { bucket = []; buckets.set(v, bucket); }
      bucket.push(i);
    }

    const positions = system.views.position;
    const sizes = system.views.size;
    const scale3 = system.views.scale3;
    const orientation = system.views.orientation;
    const colors = system.views.color;

    this.byVariant.forEach((mesh, variant) => { if (!buckets.has(variant)) mesh.count = 0; });

    buckets.forEach((indices, variant) => {
      let mesh = this.meshFor(variant, geometries[variant], transparent);
      mesh = this.grow(variant, mesh, indices.length, transparent);
      for (let n = 0; n < indices.length; n++) {
        const i = indices[n];
        TMP_POSITION.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
        if (orientation) TMP_QUATERNION.set(orientation[i * 4], orientation[i * 4 + 1], orientation[i * 4 + 2], orientation[i * 4 + 3]);
        else TMP_QUATERNION.identity();
        const size = sizes ? sizes[i] : 1;
        if (scale3) TMP_SCALE.set(scale3[i * 3] * size, scale3[i * 3 + 1] * size, scale3[i * 3 + 2] * size);
        else TMP_SCALE.set(size, size, size);
        TMP_MATRIX.compose(TMP_POSITION, TMP_QUATERNION, TMP_SCALE);
        mesh.setMatrixAt(n, TMP_MATRIX);
        if (colors) {
          TMP_COLOR.setRGB(colors[i * 4], colors[i * 4 + 1], colors[i * 4 + 2]);
          mesh.setColorAt(n, TMP_COLOR);
        }
      }
      mesh.count = indices.length;
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.renderOrder = transparent ? (blend === 'additive' ? ORDER_ADDITIVE : ORDER_ALPHA) : 0;
    });
    return count;
  }

  dispose() {
    this.byVariant.forEach((mesh) => { this.scene.remove(mesh); mesh.dispose(); });
    this.byVariant.clear();
    if (this.material) this.material.dispose();
  }
}

/* ------------------------------------------------------------------ *
 * the collection
 * ------------------------------------------------------------------ */

export class ParticleRenderer {
  constructor(scene, shared, noiseTexture) {
    this.scene = scene;
    this.shared = shared;
    this.noiseTexture = noiseTexture;
    this.systems = new Map();
    this.drawn = 0;
  }

  update(frame, context) {
    const seen = new Set();
    let drawn = 0;
    (frame.systems || []).forEach((system) => {
      if (system.render_mode === 'none') return;
      const key = system.id + '|' + system.render_mode;
      seen.add(key);
      let renderable = this.systems.get(key);
      if (!renderable) {
        renderable = system.render_mode === 'mesh'
          ? new MeshParticleSystem(this.scene)
          : new BillboardSystem(this.shared, this.noiseTexture);
        if (renderable.mesh) this.scene.add(renderable.mesh);
        this.systems.set(key, renderable);
      }
      drawn += renderable.update(system, context) || 0;
    });
    this.systems.forEach((renderable, key) => {
      if (seen.has(key)) return;
      if (renderable.mesh) this.scene.remove(renderable.mesh);
      renderable.dispose();
      this.systems.delete(key);
    });
    this.drawn = drawn;
    return drawn;
  }

  dispose() {
    this.systems.forEach((renderable) => {
      if (renderable.mesh) this.scene.remove(renderable.mesh);
      renderable.dispose();
    });
    this.systems.clear();
  }
}
