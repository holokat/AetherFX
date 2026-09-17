# GPU viewer

The studio's viewport is a live three.js renderer fed by the deterministic
engine over a WebSocket. It draws what the simulation produces at display rate,
with HDR bloom, ACES tone mapping, PBR meshes, soft particles and orbiting -
the CPU reference renderer (`/api/frame`, `/api/preview`) is still there and is
what the **Reference** button compares against.

```
engine (C++ / libaetherfx)
  |  NativeFrameSource            python/aetherfx/studio/native_source.py
  v
FrameSource protocol             python/aetherfx/studio/stream.py
  |  encode_frame() -> one binary message per frame
  v
WS /ws/stream                    python/aetherfx/studio/stream_server.py
  |  + GET /api/stream/texture/<id>.png, /api/stream/mesh/<id>.json
  v
StreamClient / decodeFrame       static/viewer/protocol.js
  |  typed-array views over the received ArrayBuffer (no copy)
  v
GLViewer                         static/viewer/viewer.js
  |  particles.js  ribbons.js  volumes.js  scene.js  resources.js  shaders.js
  v
<canvas id="gl-viewport">        inside #viewport, next to the fallback <img>
```

## The protocol

`python/aetherfx/studio/stream.py` is the authority: it documents the frame
message (`[u32 header length][header JSON][blob]`), the resources message and
the client commands (`open`, `play`, `pause`, `seek`, `step`, `resources`).
Read that first; everything below is transport and rendering.

Two details the viewer depends on:

* **The blob is 4-byte aligned.** `encode_frame` pads the JSON header with
  spaces so the blob starts on a multiple of four, which is what lets the
  browser build `Float32Array` *views* over the socket's `ArrayBuffer` instead
  of copying every array every frame. `protocol.js` still copies if it ever sees
  an unaligned offset, so a source that forgets the padding degrades rather than
  throws.
* **Playback is paced by the wall clock, and frames are dropped, not queued.**
  `_Connection._play_loop` derives the next target time from `time.monotonic()`,
  so a slow frame or a slow client skips ahead. The viewer never shows a backlog.

## Plugging in a frame source

`create_frame_source()` returns `NativeFrameSource` when
`aetherfx.studio.native_source` imports (i.e. `libaetherfx` is built) and
`MockFrameSource` otherwise. One source per WebSocket connection; sources are
never shared, so two tabs scrub independently. Anything satisfying the
`FrameSource` protocol works.

The HTTP resource routes read a studio-level cache of the *last opened*
resources, filled by the WebSocket handler (a plain `GET` has no socket to key
off, and resources belong to the effect rather than to a viewer). The exact
dictionary shapes `stream_server` consumes from `resources()`:

```python
{
  "textures": {
    "tex_flame": {
      "width": 2304, "height": 160,     # the whole strip
      "frames": 24, "frame_width": 96,  # frames laid side by side, left to right
      "png": b"\x89PNG...",             # sRGB PNG bytes; served verbatim
    },
  },
  "meshes": {
    "rock_mesh": {
      "positions": [...], "normals": [...], "uvs": [...], "indices": [...],
      # index k is the geometry a particle whose `variant` == k wants;
      # variants[0] is the base mesh
      "variants": [{"positions": ..., "normals": ..., "uvs": ..., "indices": ...}, ...],
    },
  },
  "materials": {"mat_fire": {...MaterialDesc fields...}},
  "effect": {"name": ..., "duration": ..., "fixed_dt": ..., "seed": ...},
  "render_settings": {...}, "camera": {...} | None, "timeline": {...},
}
```

Arrays may be python lists or numpy arrays of any shape; `mesh_payload()`
flattens them. `public_resources()` strips the PNG bytes out of the message the
browser receives and replaces them with `/api/stream/texture/<id>.png`; a
texture entry that already carries a `url` is passed through untouched. An
entry the source does not provide simply 404s, and the viewer draws untextured.

## What the shaders do

**Billboards** (`particles.js`, `shaders.js`) - one `InstancedBufferGeometry`
quad per system, instance attributes straight out of the frame's arrays:

* view-space quad sized by `size` (a diameter), rotated by `rotation`;
* `stretched_billboard` aligns the quad to the projected velocity and stretches
  it to `size * (1 + velocity_stretch * |v|)`. The basis is built with a
  determinant of +1 on purpose: a mirrored basis winds the corners backwards,
  which mirrors the sprite and (with single-sided materials) culls the system
  entirely;
* sprite sheets: a `sprite_columns x sprite_rows` grid inside the cell, then the
  texture's own `frames` strip, indexed by `sprite_fps` or by `age_norm`. Strip
  textures are loaded **without mipmaps** - the engine lays frames side by side,
  so mip level 2 averages neighbouring frames and a flame becomes orange fog;
* blending per system (falling back to the material): `additive` ->
  `AdditiveBlending`, `alpha` -> `NormalBlending`, `premultiplied` ->
  `CustomBlending(ONE, ONE_MINUS_SRC_ALPHA)`, always with `depthWrite` off;
* colour = particle colour x `base_color`, alpha = `opacity` x sprite alpha;
* emissive adds `colour * emissive_color * (emissive + emissive_intensity)` in
  HDR, so the bloom pass has something to find;
* **soft particles**: an opaque-only depth pre-pass renders into a
  `WebGLRenderTarget` with a `DepthTexture`; the fragment fades by
  `(sceneDepth - particleDepth) / soft_particle_distance`;
* **lit puffs** when `material.shading == "lit"`: a sphere normal from the
  quad-local position, wrapped Lambert plus a rim term against the frame's point
  lights (up to 8, falloff `1/(d^2+1)` windowed by `radius`) plus ambient;
* **dissolve / erosion**: `threshold = dissolve * (0.25 + 0.75 * age_norm)`
  (or `0.5 * erosion * age_norm` when `dissolve` is 0), `edge = max(0.02,
  erosion)`, `alpha *= smoothstep(threshold - edge, threshold + edge, noise)`
  against `material.noise_texture` or a generated 64x64 fbm `DataTexture`;
* alpha systems that ask for it are sorted back to front on the CPU each frame,
  and additive systems draw after alpha (`renderOrder` 20 vs 10).

**Mesh particles** - one `InstancedMesh` per (system, variant), instance matrix
= translate x quaternion(`orientation`) x scale(`size` x `scale3`), per-instance
colour through `instanceColor`, `MeshStandardMaterial` (or
`MeshPhysicalMaterial` with `transmission` for light, lit, alpha-blended
materials, which is what makes ice read as ice), and a fresnel rim injected with
`onBeforeCompile`: `pow(1 - dot(N, V), fresnel_power) * emissive_color *
max(emissive_intensity, 0.35) * 2`, matching the CPU renderer's rim.

**Mesh instances** (`scene.js`) - one `Mesh` per node instance, transform copied
from the frame's column-major 16-float matrix.

**Trails and beams** (`ribbons.js`) - camera-facing triangle strips rebuilt on
the CPU every frame (the strip has to face the camera, and the camera moves).
Trails read the 12-float vertex layout directly (pos3, width, age_norm, u,
colour4, opacity, emissive) and honour `twist_deg`; beams draw twice, a wide
soft halo and a thin bright core, with `pulse_phase` brightening a gaussian
travelling along the polyline. All the ribbons of one node share one geometry
and one draw call.

**Volumes** (`volumes.js`) - one box mesh per procedural `volume` in the frame,
scaled to the shape's local half-extents and raymarched in the fragment shader.
The GLSL is a translation of `src/render/src/volume_field.hpp`: same shape SDFs,
same spin/twist/climb warp, same soft/ridged fbm blend by `strands`, same
`carve` threshold and the same `alpha = 1 - exp(-d * dt)` accumulation, so the
viewer's 48 steps and the CPU renderer's 32 land on the same image. Only the
noise basis differs - hash-based value noise here, simplex there, with the same
octave count (4), lacunarity (2) and gain (0.5).

* the box uses `THREE.BackSide` so a camera inside the volume still gets a
  fragment; entry and exit come from a slab test in the volume's local space, so
  a rotated or non-uniformly scaled node is handled by the transform alone;
* it neither writes nor tests depth. Occlusion is per sample: the exit point is
  clamped against the same opaque depth texture the soft particles read, which
  is why a volume behind a mesh is cut by that mesh at the right distance
  instead of popping in front of it;
* `march_steps` is clamped to 8..96 and the first sample is jittered by a
  per-pixel hash - without the jitter a 48-step march bands visibly;
* single scattering reuses the particle light uniforms (view-space positions,
  pre-scaled colours, `1/(d^2+1)` windowed by `radius`), weighted by `scatter`;
* rays stop at 98% opacity, and the premultiplied result blends
  `CustomBlending(ONE, ONE_MINUS_SRC_ALPHA)` at `renderOrder` 5 - after the
  opaque pass, before the particle systems (10/20).

Volumes arrive as **plain JSON in the frame header**, not in the binary blob: a
procedural volume is about twenty-five scalars, so there is nothing worth
packing. `volumeExtent()` in `volumes.js` mirrors `aether::volume_shape_extent()`;
the two have to agree or the box would clip the field.

**Decals** - a quad on the ground at y + 0.005, rotated by `rotation_deg`,
textured or masked to a circle.

**Lights** - a `PointLight` (or `SpotLight`) per frame light. The engine
documents `ground = albedo * intensity / (d^2 + 1)`; three.js is physical and
gives `albedo / PI * intensity / d^2`, so matching them is a constant of about
PI. That is the stage's `light_scale` (default 3.2), exposed on the Stage
popover so it can be nudged against a reference render.

**Post** - `EffectComposer` on a half-float target:
`RenderPass -> heat haze (ShaderPass, enabled by a heat_haze post effect) ->
UnrealBloomPass -> OutputPass (ACES + sRGB) -> SMAAPass`. Bloom strength is
`bloom_intensity * 1.5`, radius is `bloom_radius * 10` clamped to [0, 1],
threshold 1.0. A `bloom` post effect in the frame overrides all three.

The one place the viewer departs from a stock UnrealBloomPass is a **bloom input
clamp** (`installBloomClamp`). Additive fire stacks up fast - twenty overlapping
emissive puffs reach luminance in the hundreds - and UnrealBloomPass blurs that
down a five-level mip pyramid, so one small very hot core smears across the
whole frame. The CPU reference renderer instead blurs its bright pass with a
bounded gaussian (`sigma = bloom_radius * width / 2`, `src/render/src/software_renderer.cpp`).
Capping what the pyramid may see (at 3.0, measured against `/api/frame`) is what
keeps the two in the same family. It patches the vendored high-pass shader by
text and checks that the patch applied, so a future three.js that rewrites the
shader loses the clamp rather than breaking.

## Extending materials

Everything a material can say lives in two places:

* **Billboards**: add a uniform to the `ShaderMaterial` in
  `BillboardSystem`'s constructor, read it in `BILLBOARD_FRAGMENT`
  (`static/viewer/shaders.js`), and set it from the material description in
  `BillboardSystem.update()`. The material description is the engine's
  `MaterialDesc` verbatim - see docs/VOCABULARY.md for the field list.
* **Meshes**: `MeshParticleSystem.buildMaterial()` decides which three.js
  material class to use and configures it; `addFresnel()` shows the
  `onBeforeCompile` pattern for anything the standard material does not cover.
  The material is rebuilt when the material id or the blend mode changes, so it
  is safe to read any field there.

Uniforms shared by every particle material (depth texture, resolution, camera
planes, time, lights) come from `makeSharedUniforms()` and are set once per
frame in `GLViewer.renderFrame`; add to that object rather than to each system.

## Frontend integration

`static/viewer/bootstrap.js` publishes `window.aetherViewer`, a small facade:
`available`, `play/pause/seek/step`, `reload`/`reloadAndFrame`, `setStage`,
`setResolution`, `resetView`, `snapshot`, `camera`, `duration`, plus `onTime`
and `onStatus` callbacks and `gl` (the `GLViewer` itself, for console poking).
`static/app.js` knows nothing about three.js; it calls that facade and falls
back to the original image viewport when `available` is false.

* transport: play/pause/loop/scrub/fps drive the socket; the slider and readout
  follow the frame messages;
* `size`: `fit` uses the device pixel ratio (capped at 2), `1x` and `0.5x` pin
  it, and the numeric options scale to that long-side resolution. The canvas is
  always the viewport's CSS size;
* every edit calls `invalidatePreview()`, which sends `open` again, so parameter
  changes show within a frame;
* `Reference` renders the current time through `/api/frame` with the live
  OrbitControls camera and shows it in a corner thumbnail;
* `Snapshot` downloads the canvas as a PNG.

## Vendored three.js

`static/vendor/three/` holds `build/three.module.js`, `build/three.core.js` and
the handful of `examples/jsm` addons the viewer imports (OrbitControls,
EffectComposer, RenderPass, ShaderPass, UnrealBloomPass, OutputPass, SMAAPass
and their shaders). `index.html` maps them with an import map; there is no build
step and nothing is fetched at runtime. The version and licence are recorded in
`static/vendor/three/VERSION`, `static/vendor/three/LICENSE` and
docs/DEPENDENCIES.md.

**A note on editing shader source**: the GLSL lives in JavaScript template
literals, so a backtick in a GLSL comment silently ends the string and the whole
module fails to parse. `node --check` on a `.js` file will not always catch it -
copy the file to `.mjs` and check that, which parses it the way the browser does.

## Measured cost

three.js r186, Apple M2, a 912x1390 drawing buffer (1.27 MPix), full post chain,
timed with `EXT_disjoint_timer_query_webgl2`:

| scene | CPU scene update | GPU | draw calls |
|---|---|---|---|
| fire_aoe at peak, ~2000 particles | - | 1.8 ms | 40 |
| 20 000 stretched billboards + 500 mesh instances (8 variants) | 0.40 ms | 3.5 ms | 38 |
