/* Procedural raymarched volumes (docs/VOLUMES.md).
 *
 * One box mesh per `volume` node in the frame, scaled to the shape's local
 * bounds and carrying a ShaderMaterial that marches the density field between
 * the box's entry and exit points.  The field is the GLSL translation of
 * src/render/src/volume_field.hpp: same shape SDFs, same spin/twist/climb warp,
 * same soft/ridged fbm blend, same carve threshold and the same
 * `alpha = 1 - exp(-d * dt)` accumulation, which is what lets the viewer's 48
 * steps and the CPU renderer's 32 land on the same image.  The noise basis is
 * the one difference: value noise here, simplex there, with the same octave
 * count, lacunarity and gain.
 *
 * Compositing: the box never writes depth and never depth-tests (a volume is a
 * medium, not a surface).  Occlusion comes from clamping the exit point against
 * the opaque depth texture the soft particles already produce, so a volume
 * behind a mesh is cut by that mesh at the right distance instead of popping in
 * front of it.  The accumulated colour is premultiplied and blends ONE /
 * ONE_MINUS_SRC_ALPHA, at a renderOrder before the particle systems.
 */

import * as THREE from 'three';
import { LAYER_TRANSPARENT } from './particles.js';
import { MAX_LIGHTS } from './shaders.js';

/* Before ORDER_ALPHA (10) and ORDER_ADDITIVE (20) in particles.js: volumes are
 * the background medium the particles play in front of. */
export const VOLUME_ORDER = 5;

const MIN_STEPS = 8;
const MAX_STEPS = 96;          // also the hard loop bound in the shader below
const EARLY_OUT = 0.02;        // stop a ray once it is 98% opaque

/* Mirrors aether::kVolumeDiscThickness / kVolumeRingThickness
 * (src/core/include/aether/core/frame_state.hpp). */
const DISC_THICKNESS = 0.25;
const RING_THICKNESS = 0.25;

/* Shape index the shader switches on; the order is the vocabulary's. */
const SHAPES = { sphere: 0, column: 1, disc: 2, ring: 3, nebula: 4, cone: 5 };

/* Mirrors aether::volume_shape_extent(): the half-extents of the box the field
 * lives in.  The density is exactly zero outside it. */
export function volumeExtent(shape, radius, height) {
  const r = Math.max(0, radius || 0);
  const h = Math.max(0, height || 0);
  if (shape === 'column' || shape === 'cone' || shape === 'nebula') return [r, h * 0.5, r];
  if (shape === 'disc') return [r, h * DISC_THICKNESS * 0.5, r];
  if (shape === 'ring') {
    const minor = h * RING_THICKNESS;
    return [r + minor, minor, r + minor];
  }
  return [r, r, r];
}

function numberOf(value, fallback) {
  return typeof value === 'number' && isFinite(value) ? value : fallback;
}

const VOLUME_VERTEX = `
precision highp float;
varying vec3 vWorld;
void main() {
  vec4 world = modelMatrix * vec4(position, 1.0);
  vWorld = world.xyz;
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

const VOLUME_FRAGMENT = `
precision highp float;
#include <packing>

uniform mat4 uInverse;          // world -> volume local
uniform vec3 uExtent;           // local half-extents of the march box
uniform int uShape;
uniform int uSteps;
uniform int uArms;
uniform float uRadius;
uniform float uHeight;
uniform float uDensity;         // extinction per metre
uniform float uEmission;
uniform float uFilamentScale;
uniform float uStrands;
uniform float uCarve;
uniform float uSoftness;
uniform float uArmSharpness;
uniform float uTwist;           // radians per metre of height
uniform float uSpinAngle;       // 2pi * spin * time, folded on the CPU
uniform float uClimb;           // climb * time, folded on the CPU
uniform float uScatter;
uniform float uSeed;
uniform vec3 uColor;
uniform vec3 uColorHot;

uniform sampler2D uDepth;
uniform vec2 uResolution;
uniform float uNear;
uniform float uFar;

uniform int uLightCount;
uniform vec3 uLightPosition[${MAX_LIGHTS}];   // view space
uniform vec3 uLightColor[${MAX_LIGHTS}];
uniform float uLightRadius[${MAX_LIGHTS}];

varying vec3 vWorld;

/* Hash-based value noise. The CPU field uses simplex, so the two fields differ
   in detail but not in character: same octaves, lacunarity and gain. */
float hash13(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.zyx + 31.32);
  return fract((p.x + p.y) * p.z);
}

float vnoise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  float n000 = hash13(i);
  float n100 = hash13(i + vec3(1.0, 0.0, 0.0));
  float n010 = hash13(i + vec3(0.0, 1.0, 0.0));
  float n110 = hash13(i + vec3(1.0, 1.0, 0.0));
  float n001 = hash13(i + vec3(0.0, 0.0, 1.0));
  float n101 = hash13(i + vec3(1.0, 0.0, 1.0));
  float n011 = hash13(i + vec3(0.0, 1.0, 1.0));
  float n111 = hash13(i + vec3(1.0, 1.0, 1.0));
  return mix(mix(mix(n000, n100, u.x), mix(n010, n110, u.x), u.y),
             mix(mix(n001, n101, u.x), mix(n011, n111, u.x), u.y), u.z);
}

/* 4 octaves, lacunarity 2, gain 0.5, normalised to about [-1, 1]. */
float fbm(vec3 p, float seed) {
  vec3 offset = vec3(seed * 13.17, seed * 7.31, seed * 19.73);
  float sum = 0.0;
  float amplitude = 1.0;
  float norm = 0.0;
  float frequency = 1.0;
  for (int octave = 0; octave < 4; octave++) {
    sum += amplitude * (vnoise(p * frequency + offset) * 2.0 - 1.0);
    norm += amplitude;
    amplitude *= 0.5;
    frequency *= 2.0;
  }
  return sum / norm;
}

float shapeSdf(vec3 p) {
  float r = max(uRadius, 0.0);
  float h = max(uHeight, 1e-4);
  float xz = length(p.xz);
  if (uShape == 1) return max(xz - r, abs(p.y) - h * 0.5);
  if (uShape == 2) return max(xz - r, abs(p.y) - h * ${DISC_THICKNESS} * 0.5);
  if (uShape == 3) {
    float minor = h * ${RING_THICKNESS};
    return length(vec2(xz - r, p.y)) - minor;
  }
  if (uShape == 4) {
    float ry = max(1e-4, h * 0.5);
    float rx = max(1e-4, r);
    return (length(vec3(p.x / rx, p.y / ry, p.z / rx)) - 1.0) * min(rx, ry);
  }
  if (uShape == 5) {
    float taper = clamp(0.5 - p.y / h, 0.0, 1.0);
    return max(xz - r * taper, abs(p.y) - h * 0.5);
  }
  return length(p) - r;
}

float densityAt(vec3 p) {
  float sdf = shapeSdf(p);
  float edge = uSoftness * max(uRadius, 0.0);
  float mask = edge > 1e-6 ? 1.0 - smoothstep(-edge, 0.0, sdf) : (sdf < 0.0 ? 1.0 : 0.0);
  if (mask <= 0.0) return 0.0;         // the noise is the expensive half

  float angle = uSpinAngle + uTwist * p.y;
  float c = cos(angle);
  float s = sin(angle);
  vec3 q = vec3(p.x * c - p.z * s, p.y - uClimb, p.x * s + p.z * c);

  float scale = max(uFilamentScale, 1e-3);
  float soft = 0.5 + 0.5 * fbm(q * scale, uSeed);
  float ridged = 1.0 - abs(fbm(q * (scale * 1.7), uSeed + 1.0));
  float n = mix(soft, ridged, clamp(uStrands, 0.0, 1.0));

  float arms = 1.0;
  if (uArms > 0) {
    float sharp = max(uArmSharpness, 0.1);
    float wave = 0.5 + 0.5 * cos(float(uArms) * atan(q.z, q.x) - sharp * length(q.xz));
    arms = pow(max(wave, 0.0), sharp);
  }

  float carve = clamp(uCarve, 0.0, 1.0);
  return mask * arms * smoothstep(carve, carve + 0.25, n) * uDensity;
}

/* Per-pixel jitter of the first sample: without it a 48-step march bands. */
float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float notZero(float v) {
  return v >= 0.0 ? max(v, 1e-9) : min(v, -1e-9);
}

void main() {
  vec3 ro = cameraPosition;
  vec3 rd = normalize(vWorld - ro);
  vec3 lo = (uInverse * vec4(ro, 1.0)).xyz;
  vec3 ld = (uInverse * vec4(rd, 0.0)).xyz;   // t stays in world metres

  vec3 safe = vec3(notZero(ld.x), notZero(ld.y), notZero(ld.z));
  vec3 ta = (-uExtent - lo) / safe;
  vec3 tb = (uExtent - lo) / safe;
  vec3 lo3 = min(ta, tb);
  vec3 hi3 = max(ta, tb);
  float tEnter = max(max(lo3.x, lo3.y), max(lo3.z, 0.0));
  float tExit = min(min(hi3.x, hi3.y), hi3.z);

  // Opaque geometry cuts the ray: the camera sits at the view-space origin, so
  // the view-space sample position is simply the view ray times t.
  vec3 vrd = (viewMatrix * vec4(rd, 0.0)).xyz;
  vec2 screen = gl_FragCoord.xy / uResolution;
  float sceneViewZ = perspectiveDepthToViewZ(texture2D(uDepth, screen).x, uNear, uFar);
  tExit = min(tExit, sceneViewZ / min(vrd.z, -1e-6));
  if (tExit <= tEnter) discard;

  float steps = float(uSteps);
  float dt = (tExit - tEnter) / steps;
  float t = tEnter + dt * hash12(gl_FragCoord.xy);

  float transmittance = 1.0;
  vec3 accumulated = vec3(0.0);
  for (int i = 0; i < ${MAX_STEPS}; i++) {
    if (i >= uSteps) break;
    float d = densityAt(lo + ld * t);
    if (d > 0.0) {
      float a = 1.0 - exp(-d * dt);
      vec3 tint = mix(uColor, uColorHot, clamp(d * 2.0, 0.0, 1.0));
      vec3 radiance = tint * uEmission;
      if (uScatter > 0.0) {
        vec3 viewPoint = vrd * t;
        vec3 gathered = vec3(0.0);
        for (int l = 0; l < ${MAX_LIGHTS}; l++) {
          if (l >= uLightCount) break;
          vec3 toLight = uLightPosition[l] - viewPoint;
          float dist2 = dot(toLight, toLight);
          float dist = sqrt(dist2);
          float attenuation = 1.0 / (dist2 + 1.0);
          float radiusL = max(uLightRadius[l], 0.001);
          attenuation *= 1.0 - smoothstep(radiusL * 0.7, radiusL, dist);
          gathered += uLightColor[l] * attenuation;
        }
        radiance += tint * gathered * uScatter;
      }
      accumulated += radiance * (transmittance * a);
      transmittance *= 1.0 - a;
      if (transmittance < ${EARLY_OUT}) break;
    }
    t += dt;
  }

  float alpha = 1.0 - transmittance;
  if (alpha <= 0.0) discard;
  gl_FragColor = vec4(accumulated, alpha);    // premultiplied
}
`;

const TMP_TRANSFORM = new THREE.Matrix4();
const TMP_BOX = new THREE.Matrix4();

/* One box + material per volume id, reused between frames. */
export class VolumeRenderer {
  constructor(scene, shared) {
    this.scene = scene;
    this.shared = shared;
    this.geometry = new THREE.BoxGeometry(1, 1, 1);
    this.volumes = new Map();
    this.drawn = 0;
  }

  makeEntry() {
    const material = new THREE.ShaderMaterial({
      vertexShader: VOLUME_VERTEX,
      fragmentShader: VOLUME_FRAGMENT,
      uniforms: Object.assign({
        uInverse: { value: new THREE.Matrix4() },
        uExtent: { value: new THREE.Vector3(1, 1, 1) },
        uShape: { value: 0 },
        uSteps: { value: 48 },
        uArms: { value: 0 },
        uRadius: { value: 1.5 },
        uHeight: { value: 2 },
        uDensity: { value: 1 },
        uEmission: { value: 1 },
        uFilamentScale: { value: 2 },
        uStrands: { value: 0.5 },
        uCarve: { value: 0.45 },
        uSoftness: { value: 0.6 },
        uArmSharpness: { value: 1.5 },
        uTwist: { value: 0 },
        uSpinAngle: { value: 0 },
        uClimb: { value: 0 },
        uScatter: { value: 0.3 },
        uSeed: { value: 0 },
        uColor: { value: new THREE.Color(0.6, 0.3, 1.0) },
        uColorHot: { value: new THREE.Color(1, 1, 1) }
      }, this.shared),
      transparent: true,
      depthTest: false,        // occlusion comes from the depth texture, per sample
      depthWrite: false,
      side: THREE.BackSide,    // the entry face may be behind the camera
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      blendSrcAlpha: THREE.OneFactor,
      blendDstAlpha: THREE.OneMinusSrcAlphaFactor
    });
    const mesh = new THREE.Mesh(this.geometry, material);
    mesh.matrixAutoUpdate = false;
    mesh.frustumCulled = false;
    mesh.renderOrder = VOLUME_ORDER;
    mesh.layers.set(LAYER_TRANSPARENT);
    this.scene.add(mesh);
    return { mesh: mesh, material: material };
  }

  update(frame) {
    const seen = new Set();
    let drawn = 0;
    (frame.volumes || []).forEach((volume) => {
      if (volume.mode === 'simulation') return;          // the fluid stub carries no field
      const shape = volume.shape || 'sphere';
      const extent = volumeExtent(shape, numberOf(volume.radius, 1.5), numberOf(volume.height, 2));
      if (!(extent[0] > 0) || !(extent[1] > 0) || !(extent[2] > 0)) return;
      if (!(numberOf(volume.density, 0) > 0)) return;
      const transform = volume.transform;
      if (!Array.isArray(transform) || transform.length !== 16) return;

      seen.add(volume.id);
      let entry = this.volumes.get(volume.id);
      if (!entry) {
        entry = this.makeEntry();
        this.volumes.set(volume.id, entry);
      }

      TMP_TRANSFORM.fromArray(transform);
      TMP_BOX.makeScale(extent[0] * 2, extent[1] * 2, extent[2] * 2);
      entry.mesh.matrix.multiplyMatrices(TMP_TRANSFORM, TMP_BOX);
      entry.mesh.matrixWorldNeedsUpdate = true;

      const u = entry.material.uniforms;
      u.uInverse.value.copy(TMP_TRANSFORM).invert();
      u.uExtent.value.set(extent[0], extent[1], extent[2]);
      u.uShape.value = SHAPES[shape] !== undefined ? SHAPES[shape] : 0;
      u.uSteps.value = Math.max(MIN_STEPS, Math.min(MAX_STEPS, Math.round(numberOf(volume.march_steps, 48))));
      u.uArms.value = Math.max(0, Math.round(numberOf(volume.spiral_arms, 0)));
      u.uRadius.value = numberOf(volume.radius, 1.5);
      u.uHeight.value = numberOf(volume.height, 2);
      u.uDensity.value = numberOf(volume.density, 1);
      u.uEmission.value = numberOf(volume.emission, 1);
      u.uFilamentScale.value = numberOf(volume.filament_scale, 2);
      u.uStrands.value = numberOf(volume.strands, 0.5);
      u.uCarve.value = numberOf(volume.carve, 0.45);
      u.uSoftness.value = numberOf(volume.softness, 0.6);
      u.uArmSharpness.value = numberOf(volume.arm_sharpness, 1.5);
      u.uTwist.value = numberOf(volume.twist, 0);
      u.uScatter.value = numberOf(volume.scatter, 0.3);
      u.uSeed.value = numberOf(volume.seed, 0) % 4096;
      // spin and climb only ever appear as spin * time / climb * time, so the
      // frame's own time is folded in here rather than passed to the shader.
      const time = numberOf(volume.time, 0);
      u.uSpinAngle.value = Math.PI * 2 * numberOf(volume.spin, 0) * time;
      u.uClimb.value = numberOf(volume.climb, 0) * time;
      const color = volume.color || [0.6, 0.3, 1.0, 1.0];
      const hot = volume.color_hot || [1, 1, 1, 1];
      u.uColor.value.setRGB(color[0], color[1], color[2]);
      u.uColorHot.value.setRGB(hot[0], hot[1], hot[2]);
      entry.mesh.visible = true;
      drawn += 1;
    });

    this.volumes.forEach((entry, id) => {
      if (seen.has(id)) return;
      this.scene.remove(entry.mesh);
      entry.material.dispose();
      this.volumes.delete(id);
    });
    this.drawn = drawn;
    return drawn;
  }

  dispose() {
    this.volumes.forEach((entry) => {
      this.scene.remove(entry.mesh);
      entry.material.dispose();
    });
    this.volumes.clear();
    this.geometry.dispose();
  }
}
