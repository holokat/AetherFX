# AetherFX Interchange Package (`aetherfx-package` v1)

The package is what an engine importer reads. `export_effect(format:
"package", path: "<dir>")` writes a directory of plain files - JSON, PNG, OBJ -
that describe one effect completely enough to rebuild it in Unreal, Unity,
Godot or anything else, with no AetherFX code in the loop. Everything the
compiler resolves (time windows, seeds, references, baked textures and meshes)
is already resolved here; the importer only has to map our vocabulary onto its
own particle system.

Produce one with:

```
aetherfx export examples/effects/fire_aoe.json --format package --out fire_aoe.aetherfx
```

or, through the tool API,
`export_effect {"format": "package", "path": "fire_aoe.aetherfx", "options": {...}}`.

## 1. Layout

```
fire_aoe.aetherfx/
  manifest.json            what this package is, and every file in it
  effect.json              the source document (canonical, round-trips through load_effect)
  runtime.json             the resolved description an importer plays
  textures/<id>.png        baked textures, 8-bit sRGB (+ <id>.exr when options.exr)
  meshes/<id>.obj          baked meshes; seeded variants are <id>_v1.obj ... <id>_vN.obj
  preview/frame_<t>.png    three rendered moments (0.25, 0.5 and 0.75 of the duration)
  preview/contact_sheet.png  the same three frames side by side
```

`meshes/` is omitted when `options.obj` is false, `preview/` when
`options.preview` is false. Paths inside the JSON are always relative to the
package directory and always use `/`.

## 2. Options

| option | default | effect |
|---|---|---|
| `fps` | `30` | sampling rate for animated parameters (`samples` on a keyframed parameter) |
| `curve_samples` | `32` | length of the `samples` table on every curve and gradient |
| `exr` | `false` | also write `textures/<id>.exr` (linear half-float RGBA, the unclamped source data) |
| `preview` | `true` | render `preview/` |
| `obj` | `true` | write `meshes/`. With `false` the `meshes` table still describes the geometry (counts, bounds, variant count) but `file` is `null` and `files` is empty: a package never names a file it did not write |
| `width`, `height`, `camera`, `settings` | preview only | passed to the renderer for the preview images |

## 3. `manifest.json`

```json
{
  "format": "aetherfx-package",
  "version": 1,
  "effect": "Fire AOE",
  "duration": 3.0,
  "time_scale": 1.0,
  "wall_duration": 3.0,
  "seed": 7,
  "fixed_dt": 0.016666666666666666,
  "generator": "aetherfx 0.1.0",
  "source_hash": "9c15f997cf355341",
  "files": {
    "effect": "effect.json",
    "runtime": "runtime.json",
    "textures": ["textures/tex_puff.png", "..."],
    "meshes": ["meshes/rock_mesh.obj", "..."],
    "preview": ["preview/frame_0.750.png", "...", "preview/contact_sheet.png"]
  },
  "units": {"length": "meter", "up": "y", "handedness": "right"}
}
```

| field | type | meaning |
|---|---|---|
| `format` | string | always `"aetherfx-package"`; refuse anything else |
| `version` | int | package version, currently `1`; a reader that knows v1 must refuse a higher number |
| `effect` | string | the effect's display name (not an id, not a file name) |
| `duration` | number | effect length in seconds |
| `seed` | int | the effect seed every node seed is derived from |
| `fixed_dt` | number | the simulation timestep the package was compiled with (seconds) |
| `generator` | string | `"aetherfx <version>"` |
| `source_hash` | string | 16 hex digits: the content hash of `effect.json`. Two packages with the same `source_hash` describe the same effect |
| `files` | object | every file written, package-relative. `textures`/`meshes`/`preview` are arrays and may be empty |
| `units` | object | `length` is always metres, `up` always `y`, `handedness` always `right` |
| `notes` | string[] | optional; present only when something was skipped (for example a preview that could not simulate) |

## 4. `effect.json`

The authoring document, byte-identical to what `save_effect` writes: schema
version, name, duration, seed, timeline, layers, nodes with their *authored*
parameters and ports, and free-form metadata. It is the round-trip copy - edit
it and re-import it into AetherFX. An engine importer should read
`runtime.json` instead; `effect.json` is there so a package is never a lossy
export. Its grammar is docs/VOCABULARY.md.

## 5. `runtime.json`

```json
{
  "format": "aetherfx-runtime",
  "version": 1,
  "effect": "Fire AOE",
  "duration": 3.0,
  "time_scale": 1.0,
  "wall_duration": 3.0,
  "seed": 7,
  "fixed_dt": 0.016666666666666666,
  "source_hash": "9c15f997cf355341",
  "sampling": {"fps": 30.0, "curve_samples": 32},
  "timeline": {"phases": [{"name": "peak", "start": 0.8, "end": 1.6}]},
  "layers": [...],
  "controls": [...],
  "nodes": [...],
  "textures": [...],
  "meshes": [...],
  "materials": [...],
  "render_settings": {...},
  "diagnostics": {...}
}
```

`format`, `version`, `effect`, `duration`, `time_scale`, `wall_duration`,
`seed`, `fixed_dt` and `source_hash` repeat the manifest so `runtime.json`
stands alone. `sampling` records the options the sample tables below were
produced with.

**Times in this file are effect seconds.** `duration`, the timeline phases, the
node windows and every `samples` table are all on the effect's own clock.
`time_scale` is how an importer maps a wall clock onto it - `effect_time =
wall_time * time_scale` - and `wall_duration` is `duration / time_scale`, how
long an instance actually lasts. It is already *resolved*: a Speed control on
the document is folded in before the package is written, so an importer uses
the number as it stands (docs/RUNTIME.md 11).

```
play_time += delta_seconds * runtime.time_scale;   // then simulate_to(play_time)
```

### 5.1 `timeline`

`{"phases": [{"name", "start", "end"}]}` in authoring order. Phase names are
`anticipation`, `activation`, `peak`, `sustain`, `decay` or a custom string.
Phases are informational for an importer: node windows are already resolved
against them (5.3).

### 5.2 `layers`

```json
{"id": "primary", "name": "Flames", "role": "primary", "enabled": true}
```

`role` is one of `telegraph`, `ignition`, `primary`, `secondary`,
`interaction`, `aftermath`, `custom`. Layers are a grouping for authors and for
budget culling (drop `secondary` on low settings); they impose no ordering.

### 5.3 `nodes`

One entry per **enabled** node, in the compiler's execution order (topological,
ties broken by id). Disabled nodes are not in this list; they are still in
`effect.json`.

```json
{
  "id": "fire_wall",
  "type": "emitter",
  "layer": "primary",
  "parent": null,
  "tier": "particles",
  "backend": "cpu_particles",
  "window": {"start": 0.4, "end": -1.0},
  "seed": 1727330435204492494,
  "parameters": { ... },
  "resolved": { ... }
}
```

| field | type | meaning |
|---|---|---|
| `id` | string | unique within the effect, `^[a-z][a-z0-9_]*$` |
| `type` | string | one of the 18 node types in docs/VOCABULARY.md |
| `layer` | string or null | layer id |
| `parent` | string or null | spatial parent; compose transforms down the chain |
| `tier` | string | `analytic`, `particles`, `physics`, `volumetric` - how expensive the node is |
| `backend` | string | the backend our runtime picked (`analytic`, `cpu_particles`, `cpu_particles_collision`, `volume_stub`); advisory for an importer |
| `window` | object | `{start, end}` in seconds, **already resolved**: if the node named a timeline phase, that phase's window is here. `end = -1` means "open: until the effect ends" |
| `seed` | uint64 | the node's stream seed, derived from the effect seed and the node id. Reuse it and an importer's own noise stays stable across re-imports |
| `parameters` | object | every parameter of this node type (5.4) |
| `resolved` | object | node-type-specific resolved references (5.5); absent for types that have none |

### 5.4 `parameters`

**Every** parameter the vocabulary defines for the node type is present, with
the value a runtime would see - a parameter the author never touched carries
its spec default, so an importer never needs our defaults table. Parameters
stored on the node that the vocabulary does not know are appended unchanged.

Value encodings (identical to docs/VOCABULARY.md):

| type | JSON |
|---|---|
| bool, int, float, string, enum | the scalar |
| vec2 / vec3 / vec4 | `[x, y]` / `[x, y, z]` / `[x, y, z, w]` |
| color | `[r, g, b]`, **linear**, with `a` appended only when it is not 1 |
| float_list | `[v, ...]` |
| vec3_list | `[[x, y, z], ...]` |
| json | as authored (for example a `texture.graph`) |

Three parameters carry more than a bare value:

**Animated** (the author set keyframes over effect time):

```json
"rate": {
  "value": 0.0,
  "track": [{"time": 0.4, "value": 0.0}, {"time": 1.0, "value": 1300.0}, ...],
  "samples": [[0.4, 0.0], [0.433333, 300.0], ..., [3.0, 0.0]]
}
```

* `value` - the constant used before the first keyframe.
* `track` - the authored keyframes: `time` in seconds, `value` in this
  parameter's own encoding, optional `interp` (`linear` by default, or `step`
  or `smooth`). Use this for an exact rebuild.
* `samples` - `[time, value]` pairs at `sampling.fps` across the node's
  resolved window (an open window is sampled to the effect duration), with the
  end of the window always included. Use this if your engine wants a baked
  curve. Times are rounded to 6 decimals and strictly increasing.

A parameter with no keyframes is written as its bare value, never wrapped.

**Curve** (over-lifetime modulation, `t` is normalized particle age in 0..1):

```json
"size_over_life": {
  "keys": [[0.0, 0.35], [0.25, 1.0], [1.0, 0.25]],
  "samples": [0.35, 0.4339, ..., 0.25]
}
```

`keys` is `[t, v]` per key, or `[t, v, "step"|"smooth"]` when any key in the
curve uses a non-linear interpolation. `samples` holds `curve_samples` values
at `t = i / (n - 1)`, so `samples[i]` can be indexed directly or uploaded as a
1D LUT. An empty `keys` array means the curve is unused and evaluates to 1.

**Gradient** (over-lifetime colour):

```json
"color_over_life": {
  "keys": [[0.0, [1.0, 0.85, 0.4]], [1.0, [0.18, 0.015, 0.0]]],
  "samples": [[1.0, 0.85, 0.4], ..., [0.18, 0.015, 0.0]]
}
```

Same shape, with linear colours instead of floats. An empty `keys` array means
the gradient is unused and evaluates to white.

**Refs.** A parameter whose type is a node reference is written as the target
id, or `null` when it is unset or points at a disabled node. In practice
references live on ports, and those appear under `resolved`.

### 5.5 `resolved`

The compiler's answer to "what does this node actually point at". Every value
is an id of an **enabled** node, or `null`; list-valued ports are arrays and
may be empty. This replaces walking the `inputs` ports in `effect.json`.

| node type | keys |
|---|---|
| `emitter` | `particle_system`, `shape` (the shape enum, repeated for convenience), `shape_mesh`, `shape_curve` |
| `particle_system` | `material`, `sprite` (a texture id), `mesh`, `mesh_variants` (int: how many baked variants the mesh has), `forces` (ids), `colliders` (ids), `trail` |
| `material` | `base_texture`, `noise_texture`, `gradient_texture` |
| `decal` | `texture`, `material` |
| `trail` | `source`, `particle_system` (set when the source is a particle system), `material` |
| `beam` | `origin_node`, `target_node`, `material` |
| `event` | `source`, `targets` (ids) |
| `force` | `noise` |
| `mesh` | `mesh` (its own resource id), `variants` (int), `material` |
| `collider` | `mesh` |
| `volume` | `sources`, `forces`, `colliders`, `material` |
| `field` | `source` |

Other node types (`light`, `camera`, `post_effect`, `curve`, `noise`,
`texture`) have no `resolved` block.

### 5.6 `textures`

One entry per baked texture, sorted by id.

```json
{
  "id": "tex_puff",
  "file": "textures/tex_puff.png",
  "width": 1024, "height": 128,
  "frames": 8, "frame_width": 128,
  "channels": 4,
  "color_space": "linear",
  "encoding": "srgb8",
  "usage": "sprite"
}
```

| field | meaning |
|---|---|
| `id` | the texture node id; this is what `resolved.sprite` / `resolved.texture` / `material.base_texture` refer to |
| `file` | package-relative PNG path |
| `width`, `height` | pixel size of the whole file |
| `frames` | animation frames, `1` for a still texture |
| `frame_width` | `width / frames`; frame *i* occupies columns `[i * frame_width, (i + 1) * frame_width)`. Animated textures stay one horizontal strip - never re-tile them into a grid |
| `channels` | always `4` (RGBA) |
| `color_space` | always `"linear"`: the *data* is linear, so an importer must decode it back to linear on load |
| `encoding` | how `file` stores that data: `"srgb8"` - the PNG is 8-bit with an sRGB transfer curve applied and no tonemapping, exposure or filmic grading |
| `usage` | `sprite`, `decal`, `noise` or `gradient`, decided by the port that references the texture (a texture used more than once reports the first of that list). Import hint only |
| `exr` | present only with `options.exr`: a linear half-float RGBA EXR holding the unclamped source values. Prefer it for HDR sprites |

### 5.7 `meshes`

One entry per mesh node that baked geometry.

```json
{
  "id": "rock_mesh",
  "file": "meshes/rock_mesh.obj",
  "variants": 8,
  "files": ["meshes/rock_mesh.obj", "meshes/rock_mesh_v1.obj", "..."],
  "vertex_count": 240,
  "triangle_count": 80,
  "bounds": {"min": [-0.385, -0.5, -0.5], "max": [0.393, 0.426, 0.487]}
}
```

| field | meaning |
|---|---|
| `id` | the mesh node id, referenced by `resolved.mesh` |
| `file` | variant 0, the mesh anything that is not instancing uses |
| `variants` | how many seeded variants were baked (`crystal`, `rock` and `shard` primitives only; every other primitive bakes 1) |
| `files` | all variants in order, `files[0] == file`. Our resource keys are `<id>` and `<id>#k`; on disk that is `<id>.obj` and `<id>_v<k>.obj`, because `#` is not portable in a file name |
| `vertex_count`, `triangle_count`, `bounds` | describe **variant 0**. The other variants are the same primitive with a different seed, so they are close but not identical; read the OBJ if you need exact numbers |

A `particle_system` whose `resolved.mesh_variants` is greater than 1 picks one
variant per particle and keeps it for the particle's life; our runtime uses
`variant = particle_seed32 % variants`.

### 5.8 `materials`

One entry per material node, sorted by id. These are the baked `MaterialDesc`
values, not the authored parameters, so ranges and enums are already resolved.

| field | type | meaning |
|---|---|---|
| `id` | string | material node id, referenced by `resolved.material` |
| `blend` | enum | `additive`, `alpha`, `premultiplied` |
| `shading` | enum | `unlit`, `lit` |
| `base_color` | color | linear tint |
| `opacity` | float | 0..1, multiplies alpha |
| `emissive_color` | color | linear |
| `emissive_intensity` | float | HDR multiplier on `emissive_color`; 0 means not emissive |
| `fresnel_power` | float | rim term exponent; 0 = off |
| `dissolve` | float | 0..1 threshold against `noise_texture` |
| `erosion` | float | 0..1 softness of the dissolve edge |
| `distortion` | float | screen-space refraction strength; 0 = off |
| `soft_particle` | bool | fade against scene depth |
| `depth_fade` | float | soft-particle fade distance in metres |
| `base_texture` | id or null | colour map |
| `noise_texture` | id or null | the map `dissolve`/`erosion` read |
| `gradient_texture` | id or null | colour ramp lookup |
| `uv_scroll` | vec2 | UV units per second |
| `uv_rotate` | float | degrees per second |
| `double_sided` | bool | disable backface culling |
| `temperature_gradient` | gradient keys | `[]` when unused; otherwise `[[t, color], ...]` remapping luminance to colour |

### 5.9 `render_settings`

The preview/authoring render intent, present only when the effect carries
`metadata.render_settings`; otherwise `null`. It is the engine defaults with
the author's overrides applied, so it is a complete `RenderSettings` object:
`background` (linear colour), `ground_plane`, `grid`, `ground_albedo`, `bloom`,
`bloom_threshold`, `bloom_intensity`, `bloom_radius`, `exposure`, `tonemap`,
`soft_particles`, `motion_blur`, `supersample`, `width`, `height`. An importer
should treat `background`, `exposure` and the bloom values as "what this effect
was tuned against" and ignore `width`/`height`.

### 5.10 `controls`

The effect's named numeric knobs with the values this export was made at, in
document order and in the same shape as `effect.json` (docs/CONTROLS.md):

```json
{"id": "flames_intensity", "label": "Intensity", "group": "Flames",
 "min": 0.0, "max": 3.0, "default": 1.0, "value": 2.0, "step": 0.01, "unit": "x",
 "bindings": [{"node": "flame_ps", "parameter": "emissive", "op": "multiply"}]}
```

An importer does **not** have to apply them: every `parameters` object in 5.4 is
already resolved with the controls folded in, so the package describes the
effect as it was exported. The list is there so an importer can surface the same
sliders as instance parameters, and so a round trip through `effect.json` keeps
them. The array is present and empty for an effect that has no controls.

### 5.11 `diagnostics`

The compile report: `{"ok", "errors", "warnings", "items": [{"code",
"severity", "message", "node"?, "param"?}]}`. `ok: false` means the effect had
validation errors and the package was exported anyway - import it with care.
Warning codes worth surfacing to a user are listed in docs/VOCABULARY.md
("Validation rules").

## 6. `textures/<id>.png`

8-bit RGBA PNG. The baked linear image is written through the sRGB transfer
curve with **no** filmic tonemap, exposure or grading, so decoding the PNG back
to linear recovers the baked values in 0..1 exactly (values above 1 are
clipped - use `options.exr` if the texture is HDR). An animated texture is one
horizontal strip of `frames` cells; see `frame_width` in 5.6.

## 7. `meshes/<id>.obj`

A minimal Wavefront OBJ: two `#` comment lines, one `o <name>`, then `v`, `vt`,
`vn` and `f`. No material library, no groups, no smoothing groups.

* Positions are metres, `+Y` up, right-handed - the same convention as
  `manifest.units`. Meshes are centred on the origin.
* `vt` carries the engine's UVs **unchanged**: `v = 0` is the *top* row of the
  texture. Importers that use the OpenGL convention must flip to `1 - v`.
* Faces are always triangles and always reference all three arrays with the
  same index (`f a/a/a b/b/b c/c/c`), because our meshes are flat-shaded and
  every triangle owns its vertices. A mesh without uvs or normals degrades to
  `f a//a` / `f a`.
* Indices are 1-based, as OBJ requires.

Counting `v ` and `f ` lines is a valid way to check a file against
`vertex_count` and `triangle_count` for variant 0.

## 8. `preview/`

`frame_<t>.png` at 25%, 50% and 75% of the duration (`<t>` is the time in
seconds with three decimals, so `frame_0.750.png`), plus `contact_sheet.png`
holding the same three frames side by side at full resolution. They are
rendered with the effect's own camera and `metadata.render_settings`, so they
show the look the effect was authored against. They are documentation for a
human, never an input to the import.

## 9. Determinism and versioning

Exporting the same document twice produces byte-identical `manifest.json`,
`runtime.json` and geometry: node order, map keys, sample grids and float
formatting are all fixed, and nothing carries a timestamp. `source_hash` is the
identity of the source document, so an importer can skip a re-import cheaply,
and a changed `source_hash` with an unchanged `format`/`version` means content
moved, not schema.

Compatibility rule for readers: accept `format == "aetherfx-package"` with
`version <= 1`, ignore unknown keys (new fields are added, existing ones are
not repurposed), and treat a missing optional key as its documented default.

## 10. Importing, in short

1. Read `manifest.json`, check `format` and `version`.
2. Read `runtime.json`. Create one asset per entry in `textures`, `meshes` and
   `materials`.
3. For each node in `nodes` with `type == "particle_system"`, build an emitter
   module set from its `parameters` (`lifetime`, `size`, `size_over_life`,
   `color_over_life`, `opacity_over_life`, `render_mode`, `blend`, ...) and
   wire `resolved.material`, `resolved.sprite`, `resolved.mesh`,
   `resolved.forces` and `resolved.colliders`.
4. For each `emitter`, attach a spawn module to `resolved.particle_system`:
   shape from `parameters.shape` plus `radius` / `inner_radius` / `size` /
   `angle` / `length`, rate from `parameters.rate` (constant, or its `samples`
   as a baked curve), bursts from `burst_count` and `burst_times`, and gate the
   whole thing on `window`.
5. Handle the analytic types you support - `light`, `decal`, `beam`, `trail`,
   `mesh`, `post_effect` - and drop the rest; `tier` tells you what a node
   costs before you commit to it.
6. Use `seed` per node if you want re-imports to look the same, and `fixed_dt`
   if you want to match our simulation exactly.

The reference for what each parameter *means* is docs/VOCABULARY.md; the
reference for how our runtime evaluates them (spawn order, force integration,
over-life modulation) is docs/RUNTIME.md.
