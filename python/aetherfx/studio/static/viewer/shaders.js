/* GLSL for the viewer's own materials, plus the generated fallback noise.
 *
 * Everything here renders into an HDR (half-float) buffer: fragment colours may
 * exceed 1 so the bloom pass has something to find.  Tone mapping happens once,
 * in the OutputPass, never here.
 */

import * as THREE from 'three';

export const MAX_LIGHTS = 8;

/* ------------------------------------------------------------------ *
 * particles: one instanced camera-facing quad per particle
 * ------------------------------------------------------------------ */

export const BILLBOARD_VERTEX = `
precision highp float;

attribute vec2 corner;          // -0.5 .. 0.5, the quad
attribute vec3 iPosition;
attribute vec3 iVelocity;
attribute float iSize;          // diameter, metres
attribute float iRotation;      // radians, screen space
attribute vec4 iColor;
attribute float iEmissive;
attribute float iAge;           // age_norm 0..1

uniform float uStretch;         // velocity_stretch
uniform float uStretched;       // 1 = stretched_billboard

varying vec2 vLocal;            // corner, for the sphere normal and the mask
varying vec2 vUv;               // 0..1 inside the sprite cell
varying vec4 vColor;
varying float vEmissive;
varying float vAge;
varying vec3 vViewPos;

void main() {
  vec4 mv = modelViewMatrix * vec4(iPosition, 1.0);
  vec2 offset;

  if (uStretched > 0.5) {
    // Align the quad to the velocity as it projects on screen and stretch it.
    // The basis must keep the same handedness as the unrotated quad: build it
    // basis as (axis rotated -90, axis) so its determinant stays +1, otherwise
    // the corners wind backwards, the sprite mirrors and back-face culling
    // throws the whole system away.
    vec3 velView = (modelViewMatrix * vec4(iVelocity, 0.0)).xyz;
    vec2 axis = velView.xy;
    axis = (dot(axis, axis) > 1e-10) ? normalize(axis) : vec2(0.0, 1.0);
    vec2 side = vec2(axis.y, -axis.x);
    float stretched = iSize * (1.0 + uStretch * length(iVelocity));
    offset = side * (corner.x * iSize) + axis * (corner.y * stretched);
  } else {
    float c = cos(iRotation), s = sin(iRotation);
    offset = vec2(corner.x * c - corner.y * s, corner.x * s + corner.y * c) * iSize;
  }

  mv.xy += offset;
  vLocal = corner;
  vUv = corner + 0.5;
  vColor = iColor;
  vEmissive = iEmissive;
  vAge = iAge;
  vViewPos = mv.xyz;
  gl_Position = projectionMatrix * mv;
}
`;

export const BILLBOARD_FRAGMENT = `
precision highp float;
#include <packing>

uniform vec3 uBaseColor;
uniform float uOpacity;
uniform vec3 uEmissiveColor;
uniform float uEmissiveIntensity;
uniform float uPremultiply;

uniform sampler2D uSprite;
uniform float uHasSprite;
uniform vec4 uSheet;            // columns, rows, texture frames, sprite fps
uniform float uTime;

uniform sampler2D uNoise;
uniform float uDissolve;
uniform float uErosion;

uniform sampler2D uDepth;
uniform vec2 uResolution;
uniform float uNear;
uniform float uFar;
uniform float uSoftDistance;

uniform float uLit;
uniform vec3 uAmbient;
uniform int uLightCount;
uniform vec3 uLightPosition[${MAX_LIGHTS}];   // view space
uniform vec3 uLightColor[${MAX_LIGHTS}];
uniform float uLightRadius[${MAX_LIGHTS}];

varying vec2 vLocal;
varying vec2 vUv;
varying vec4 vColor;
varying float vEmissive;
varying float vAge;
varying vec3 vViewPos;

/* Flipbook: a sprite grid inside the cell, then the texture's own frame strip. */
vec2 sheetUv(vec2 uv) {
  float cols = max(uSheet.x, 1.0);
  float rows = max(uSheet.y, 1.0);
  float texFrames = max(uSheet.z, 1.0);
  float grid = cols * rows;
  float total = max(grid, texFrames);
  float index = (uSheet.w > 0.0) ? mod(floor(uTime * uSheet.w), total)
                                 : floor(clamp(vAge, 0.0, 0.9999) * total);
  if (grid > 1.0) {
    float c = mod(index, cols);
    float r = floor(index / cols);
    uv = (uv + vec2(c, rows - 1.0 - r)) / vec2(cols, rows);
    index = 0.0;
  }
  // Stay just inside the frame so bilinear filtering cannot fetch the next one.
  if (texFrames > 1.0) uv.x = (index + clamp(uv.x, 0.002, 0.998)) / texFrames;
  return uv;
}

void main() {
  float radius = length(vLocal) * 2.0;

  vec4 texel = vec4(1.0);
  if (uHasSprite > 0.5) {
    texel = texture2D(uSprite, sheetUv(vUv));
  } else {
    // A soft round puff so untextured systems still look like smoke, not squares.
    // Squared falloff: only the exact centre is opaque, so stacks of additive
    // particles build up gradually instead of clipping immediately.
    float falloff = smoothstep(1.0, 0.0, radius);
    texel.a = falloff * falloff;
  }

  vec3 rgb = texel.rgb * uBaseColor * vColor.rgb;
  float alpha = texel.a * vColor.a * uOpacity;
  if (alpha <= 0.0) discard;

  // erosion / dissolve against a noise field, widening with age
  if (uDissolve > 0.0 || uErosion > 0.0) {
    float noise = texture2D(uNoise, vUv * 1.7 + vec2(vAge * 0.07, 0.0)).r;
    float threshold = (uDissolve > 0.0) ? uDissolve * (0.25 + 0.75 * vAge) : 0.5 * uErosion * vAge;
    float edge = max(0.02, uErosion);
    alpha *= smoothstep(threshold - edge, threshold + edge, noise);
  }

  // soft particles: fade where the quad intersects what is already in the depth buffer
  if (uSoftDistance > 0.0) {
    vec2 screen = gl_FragCoord.xy / uResolution;
    float sceneZ = -perspectiveDepthToViewZ(texture2D(uDepth, screen).x, uNear, uFar);
    float particleZ = -vViewPos.z;
    alpha *= clamp((sceneZ - particleZ) / uSoftDistance, 0.0, 1.0);
  }

  vec3 lit = rgb;
  if (uLit > 0.5) {
    // Treat the sprite as a sphere: the normal falls off the quad's local position.
    vec2 n2 = vLocal * 2.0;
    float z2 = max(0.0, 1.0 - dot(n2, n2));
    vec3 normal = vec3(n2, sqrt(z2));
    vec3 light = uAmbient;
    for (int i = 0; i < ${MAX_LIGHTS}; i++) {
      if (i >= uLightCount) break;
      vec3 toLight = uLightPosition[i] - vViewPos;
      float dist2 = dot(toLight, toLight);
      float dist = sqrt(dist2);
      float attenuation = 1.0 / (dist2 + 1.0);
      float radiusI = max(uLightRadius[i], 0.001);
      attenuation *= 1.0 - smoothstep(radiusI * 0.7, radiusI, dist);
      vec3 direction = toLight / max(dist, 1e-4);
      float wrapped = clamp((dot(normal, direction) + 0.6) / 1.6, 0.0, 1.0);
      float rim = pow(1.0 - clamp(normal.z, 0.0, 1.0), 2.0);
      light += uLightColor[i] * attenuation * (wrapped + rim * 0.35);
    }
    lit = rgb * light;
  }

  // Emissive stays in HDR so the bloom pass can pick it up.
  lit += rgb * uEmissiveColor * (vEmissive + uEmissiveIntensity);

  gl_FragColor = vec4(lit * mix(1.0, alpha, uPremultiply), alpha);
}
`;

/* ------------------------------------------------------------------ *
 * ribbons: trails, beams and decals share one unlit HDR material
 * ------------------------------------------------------------------ */

export const RIBBON_VERTEX = `
precision highp float;
attribute vec4 color;           // rgb * opacity in .a
attribute float emissive;
varying vec4 vColor;
varying vec2 vUv;
varying float vEmissive;
void main() {
  vColor = color;
  vUv = uv;
  vEmissive = emissive;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const RIBBON_FRAGMENT = `
precision highp float;
uniform vec3 uBaseColor;
uniform vec3 uEmissiveColor;
uniform float uEmissiveIntensity;
uniform sampler2D uTexture;
uniform float uHasTexture;
uniform float uPremultiply;
uniform float uCircle;          // decals with no texture: round mask
varying vec4 vColor;
varying vec2 vUv;
varying float vEmissive;
void main() {
  vec4 texel = vec4(1.0);
  if (uHasTexture > 0.5) texel = texture2D(uTexture, vUv);
  else if (uCircle > 0.5) texel.a = smoothstep(1.0, 0.55, length(vUv - 0.5) * 2.0);

  vec3 rgb = texel.rgb * uBaseColor * vColor.rgb;
  float alpha = texel.a * vColor.a;
  if (alpha <= 0.0) discard;
  vec3 out_ = rgb * (1.0 + uEmissiveColor * (vEmissive + uEmissiveIntensity));
  gl_FragColor = vec4(out_ * mix(1.0, alpha, uPremultiply), alpha);
}
`;

/* ------------------------------------------------------------------ *
 * beams: a lightning bolt, shaded across the ribbon
 * ------------------------------------------------------------------ */

/* The cross-section is three additive layers: a white-hot core the bloom takes
 * over, a coloured inner glow at three times its radius, and the wide faint outer
 * glow that fills the rest of the ribbon.  The CPU reference renderer evaluates
 * the identical function per pixel (beam_cross_section in software_renderer.cpp),
 * so the two renderers agree.  The strip is extruded to the *outer* radius and
 * `uCoreFrac` / `uInnerFrac` say where the other two layers sit inside it.
 *
 * The per-vertex `emissive` attribute carries intensity * path fade, and `color.a`
 * the beam's alpha; `position` is the extruded ribbon corner. */
export const BEAM_VERTEX = `
precision highp float;
attribute vec4 color;
attribute float emissive;       // per-vertex intensity * path fade
varying vec4 vColor;
varying vec2 vUv;
varying float vGain;
void main() {
  vColor = color;
  vUv = uv;                     // x = along the bolt, y = across it
  vGain = emissive;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const BEAM_FRAGMENT = `
precision highp float;
uniform vec3 uColor;
uniform float uEmissive;
uniform float uCoreFrac;        // core radius / ribbon radius; 0 = layer off
uniform float uInnerFrac;       // inner glow radius / ribbon radius
uniform float uOuterFrac;       // outer glow radius / ribbon radius
uniform float uPulse;           // [0,1) travelling pulse, < 0 = none
uniform float uPremultiply;
varying vec4 vColor;
varying vec2 vUv;
varying float vGain;

float beamKernel(float t, float r) {
  if (r <= 0.0) return 0.0;
  float x = min(t / max(r, 1e-3), 1.0);
  float f = 1.0 - x * x;
  return f * f;
}

void main() {
  float t = abs(vUv.y * 2.0 - 1.0);
  float core = beamKernel(t, uCoreFrac) * 2.2;
  float inner = beamKernel(t, uInnerFrac) * 0.8;
  float outer = beamKernel(t, uOuterFrac) * 0.28;
  float luminance = core + inner + outer;
  if (luminance <= 0.0) discard;

  vec3 tint = uColor * vColor.rgb;
  vec3 hot = mix(tint, vec3(1.0), 0.85);
  vec3 rgb = hot * core + tint * (inner + outer);

  float gain = vGain;
  if (uPulse >= 0.0) {
    float d = abs(vUv.x - uPulse);
    d = min(d, 1.0 - d);
    gain *= 1.0 + 3.0 * exp(-(d * d) / 0.0036);
  }
  float alpha = clamp(luminance * gain, 0.0, 1.0) * vColor.a;
  if (alpha <= 0.0) discard;
  vec3 out_ = rgb * (uEmissive * gain);
  gl_FragColor = vec4(out_ * mix(1.0, alpha, uPremultiply), alpha);
}
`;

/* An impact flare: the same three-layer falloff, radial instead of across a
 * ribbon, on a camera-facing quad. */
export const BEAM_FLARE_FRAGMENT = `
precision highp float;
uniform vec3 uColor;
uniform float uEmissive;
uniform float uCoreFrac;
uniform float uInnerFrac;
uniform float uOuterFrac;
uniform float uPulse;
uniform float uPremultiply;
varying vec4 vColor;
varying vec2 vUv;
varying float vGain;

float beamKernel(float t, float r) {
  if (r <= 0.0) return 0.0;
  float x = min(t / max(r, 1e-3), 1.0);
  float f = 1.0 - x * x;
  return f * f;
}

void main() {
  float t = length(vUv * 2.0 - 1.0);
  if (t >= 1.0) discard;
  float core = beamKernel(t, uCoreFrac) * 2.2;
  float inner = beamKernel(t, uInnerFrac) * 0.8;
  float outer = beamKernel(t, uOuterFrac) * 0.28;
  float luminance = core + inner + outer;
  vec3 tint = uColor * vColor.rgb;
  vec3 rgb = mix(tint, vec3(1.0), 0.85) * core + tint * (inner + outer);
  float alpha = clamp(luminance * vGain, 0.0, 1.0) * vColor.a;
  if (alpha <= 0.0) discard;
  vec3 out_ = rgb * (uEmissive * vGain);
  gl_FragColor = vec4(out_ * mix(1.0, alpha, uPremultiply), alpha);
}
`;

/* ------------------------------------------------------------------ *
 * bloom input clamp
 * ------------------------------------------------------------------ */

/* Additive fire stacks up fast: 20 overlapping emissive puffs reach luminance
 * in the hundreds.  UnrealBloomPass blurs that down a five-level mip pyramid,
 * so one small very hot core smears across the entire frame and the image
 * washes out - which is not what the CPU reference renderer does.  Every real
 * engine caps what the bloom pyramid is allowed to see; this does the same by
 * swapping the pass's high-pass material for one that clamps first.  The rest
 * of the image (and therefore the tonemapped core) is untouched.
 *
 * It patches a vendored shader by text, so it checks that the patch applied and
 * leaves the pass alone if a future three.js rewrites the shader. */
export function installBloomClamp(bloomPass, LuminosityHighPassShader, clampMax) {
  const uniforms = bloomPass.highPassUniforms;
  const source = LuminosityHighPassShader.fragmentShader;
  const anchor = 'float v = luminance( texel.xyz );';
  if (source.indexOf(anchor) < 0 || source.indexOf('uniform float smoothWidth;') < 0) return false;
  uniforms.clampMax = { value: clampMax };
  const fragmentShader = source
    .replace('uniform float smoothWidth;', 'uniform float smoothWidth;\n\t\tuniform float clampMax;')
    .replace(anchor, [
      'float peak = max( max( texel.r, texel.g ), texel.b );',
      'if ( peak > clampMax ) texel.rgb *= clampMax / peak;',
      anchor
    ].join('\n\t\t\t'));
  bloomPass.materialHighPassFilter = new THREE.ShaderMaterial({
    uniforms: uniforms,
    vertexShader: LuminosityHighPassShader.vertexShader,
    fragmentShader: fragmentShader
  });
  return true;
}

/* ------------------------------------------------------------------ *
 * heat haze: a cheap screen-space wobble driven by post_effect intensity
 * ------------------------------------------------------------------ */

export const HeatHazeShader = {
  name: 'HeatHazeShader',
  uniforms: {
    tDiffuse: { value: null },
    uIntensity: { value: 0.0 },
    uTime: { value: 0.0 },
    uFrequency: { value: 6.0 }
  },
  vertexShader: `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
  `,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    uniform float uIntensity;
    uniform float uTime;
    uniform float uFrequency;
    varying vec2 vUv;
    void main() {
      float wobble = sin(vUv.y * uFrequency * 18.0 + uTime * 4.0) * cos(vUv.x * uFrequency * 11.0 - uTime * 3.0);
      vec2 offset = vec2(wobble, sin(vUv.x * uFrequency * 21.0 + uTime * 5.0)) * uIntensity * 0.012;
      gl_FragColor = texture2D(tDiffuse, vUv + offset);
    }
  `
};

/* ------------------------------------------------------------------ *
 * built-in noise (used when a material names no noise_texture)
 * ------------------------------------------------------------------ */

function hash2(x, y, seed) {
  let h = Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(seed, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function valueNoise(x, y, period, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const wrap = (n) => ((n % period) + period) % period;
  const a = hash2(wrap(xi), wrap(yi), seed);
  const b = hash2(wrap(xi + 1), wrap(yi), seed);
  const c = hash2(wrap(xi), wrap(yi + 1), seed);
  const d = hash2(wrap(xi + 1), wrap(yi + 1), seed);
  return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
}

/* A 64x64 tiling fbm, luminance in every channel: the default dissolve mask. */
export function makeNoiseTexture(size) {
  size = size || 64;
  const data = new Uint8Array(size * size * 4);
  let lo = Infinity, hi = -Infinity;
  const raw = new Float32Array(size * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let value = 0, amplitude = 0.5, period = 4;
      for (let octave = 0; octave < 4; octave++) {
        value += amplitude * valueNoise(x / size * period, y / size * period, period, octave + 1);
        amplitude *= 0.5;
        period *= 2;
      }
      raw[y * size + x] = value;
      if (value < lo) lo = value;
      if (value > hi) hi = value;
    }
  }
  const span = Math.max(1e-5, hi - lo);
  for (let i = 0; i < raw.length; i++) {
    const v = Math.round(((raw[i] - lo) / span) * 255);
    data[i * 4] = v; data[i * 4 + 1] = v; data[i * 4 + 2] = v; data[i * 4 + 3] = 255;
  }
  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.minFilter = texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}
