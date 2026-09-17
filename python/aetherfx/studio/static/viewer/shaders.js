/* GLSL for the viewer's own materials, plus the generated fallback noise.
 *
 * Everything here renders into an HDR (half-float) buffer: fragment colours may
 * exceed 1 so the bloom pass has something to find.  Tone mapping happens once,
 * in the OutputPass, never here.
 */

import * as THREE from 'three';

export const MAX_LIGHTS = 8;

/* Keys a material.temperature_gradient may carry into the shader.  The ramps the
 * vocabulary documents have five; a longer gradient is resampled onto this many
 * evenly spaced taps by particles.js. */
export const MAX_GRADIENT_KEYS = 8;

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
attribute float iAgeSeconds;    // age in seconds; 0 when the stream omits it

uniform float uStretch;         // velocity_stretch, 0 unless the mode stretches
uniform float uAlignVelocity;   // 1 = stretched_billboard or align_to_velocity

varying vec2 vLocal;            // corner, for the sphere normal and the mask
varying vec2 vUv;               // 0..1 inside the sprite cell
varying vec4 vColor;
varying float vEmissive;
varying float vAge;
varying float vAgeSeconds;
varying vec3 vViewPos;

void main() {
  vec4 mv = modelViewMatrix * vec4(iPosition, 1.0);

  // The quad's two view-plane axes, built exactly the way draw_particle_quad()
  // in src/render/src/software_renderer.cpp builds them: the particle's roll
  // first, then the projected velocity when there is one to align to.  axisA
  // spans corner.x, axisB spans corner.y.
  float c = cos(iRotation), s = sin(iRotation);
  vec2 axisA = vec2(c, s);
  vec2 axisB = vec2(-s, c);
  float sizeA = iSize;
  float sizeB = iSize;

  if (uAlignVelocity > 0.5) {
    vec3 velView = (modelViewMatrix * vec4(iVelocity, 0.0)).xyz;
    vec2 axis = velView.xy;
    // The CPU renderer replaces the roll basis only when the velocity actually
    // projects onto the screen; a particle flying straight at the camera keeps
    // its roll, which is where rotation / rotation_variance survives for a
    // stretched billboard.  Same 1e-5 length threshold.
    if (dot(axis, axis) > 1e-10) {
      axis = normalize(axis);
      // The basis must keep the same handedness as the unrotated quad: (axis
      // rotated -90, axis) has determinant +1, otherwise the corners wind
      // backwards, the sprite mirrors and back-face culling throws the whole
      // system away.
      axisB = axis;
      axisA = vec2(axis.y, -axis.x);
      sizeB = iSize * (1.0 + uStretch * length(iVelocity));
    }
  }

  mv.xy += axisA * (corner.x * sizeA) + axisB * (corner.y * sizeB);
  vLocal = corner;
  vUv = corner + 0.5;
  vColor = iColor;
  vEmissive = iEmissive;
  vAge = iAge;
  vAgeSeconds = iAgeSeconds;
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
uniform float uHasAgeSeconds;   // 1 = iAgeSeconds is real (see particles.js)
uniform float uTime;

uniform int uTempCount;                             // material.temperature_gradient
uniform float uTempT[${MAX_GRADIENT_KEYS}];
uniform vec3 uTempColor[${MAX_GRADIENT_KEYS}];

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
varying float vAgeSeconds;
varying vec3 vViewPos;

/* Positive modulo, overflow-free for a large floor(age * fps) - the same shape
 * as sprite_frame()'s "frame -= floor(frame / frames) * frames" on the CPU. */
float wrapIndex(float index, float count) {
  return index - floor(index / count) * count;
}

/* The flipbook clock is per particle, never a global time uniform: sprite_cell()
 * and sprite_frame() in src/render/src/software_renderer.cpp read that
 * particle's own age, so a system's sprites spread across the flipbook instead
 * of stepping together and a non-looping strip never pops at wrap.
 *
 *   sprite_fps > 0 : floor(age_seconds * fps), wrapped  (playback from birth)
 *   sprite_fps = 0 : floor(age_norm * n)                (once over the life)
 *
 * The cells of a sprite_columns x sprite_rows grid wrap in both cases; the
 * texture's own frame strip clamps in the age-mapped one.  age_seconds only
 * exists when the stream carries a per-particle "age" array, so without it the
 * fps branch falls back to the age-mapped one - still per particle, and still
 * monotonic, rather than one shared frame for the whole system. */
float sheetIndex(float count) {
  if (uSheet.w > 0.0 && uHasAgeSeconds > 0.5) return floor(vAgeSeconds * uSheet.w);
  return floor(clamp(vAge, 0.0, 1.0) * count);
}

/* Flipbook: a sprite grid inside the cell, then the texture's own frame strip. */
vec2 sheetUv(vec2 uv) {
  float cols = max(uSheet.x, 1.0);
  float rows = max(uSheet.y, 1.0);
  float texFrames = max(uSheet.z, 1.0);
  float cells = cols * rows;
  if (cells > 1.0) {
    float cell = wrapIndex(sheetIndex(cells), cells);
    float c = mod(cell, cols);
    float r = floor(cell / cols);
    uv = (uv + vec2(c, rows - 1.0 - r)) / vec2(cols, rows);
  }
  if (texFrames > 1.0) {
    float frame = clamp(wrapIndex(sheetIndex(texFrames), texFrames), 0.0, texFrames - 1.0);
    // Stay just inside the frame so bilinear filtering cannot fetch the next one.
    uv.x = (frame + clamp(uv.x, 0.002, 0.998)) / texFrames;
  }
  return uv;
}

/* material.temperature_gradient, evaluated the way aether::Gradient::eval does
 * (src/core/src/curve.cpp): piecewise linear between keys, clamped outside the
 * key range, white when the gradient is empty.  shade_particle() multiplies it
 * into the particle colour at 1 - age/lifetime, so a puff is white-hot at birth
 * and falls through orange to the ramp's dark end as it dies. */
vec3 temperatureTint(float t) {
  if (uTempCount <= 0) return vec3(1.0);
  vec3 tint = uTempColor[0];
  for (int i = 1; i < ${MAX_GRADIENT_KEYS}; i++) {
    if (i >= uTempCount) break;
    if (t <= uTempT[i - 1]) break;
    float span = uTempT[i] - uTempT[i - 1];
    float u = (t >= uTempT[i] || span <= 0.0) ? 1.0 : (t - uTempT[i - 1]) / span;
    tint = mix(uTempColor[i - 1], uTempColor[i], u);
  }
  return tint;
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

  vec3 rgb = texel.rgb * uBaseColor * vColor.rgb * temperatureTint(1.0 - clamp(vAge, 0.0, 1.0));
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
