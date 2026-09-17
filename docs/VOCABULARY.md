# AetherFX Node Vocabulary (schema 0.1.0)

This is the constrained vocabulary an agent authors with. It is implemented by
`aether::SpecRegistry` (src/core) and exported to `schema/vocabulary.json`.
Keep the vocabulary small. Add a parameter only when an effect in
`examples/effects` needs it.

Notation: `name: type = default [min..max] (A)` - `(A)` means animatable by
keyframe track over effect time. Units are meters, seconds, degrees, linear
RGB in [0, inf) (emissive may exceed 1). `t` for curves/gradients is
normalized particle age in [0,1] unless stated otherwise.

Value types: `bool`, `int`, `float`, `vec2`, `vec3`, `vec4`, `color`
(rgba, linear), `string`, `enum`, `curve` (keys `[{t, v, interp}]`,
interp `linear|step|smooth`), `gradient` (keys `[{t, color}]`),
`ref` (node id, typed by the port), `float_list`, `vec3_list`, `json`.

## Common node fields

```
id: string          unique in effect, ^[a-z][a-z0-9_]*$
type: enum          one of the node types below
version: int = 1
enabled: bool = true
seed: int (optional; derived from effect seed + id when absent)
parent: ref (optional spatial parent: emitter|mesh|light|decal|curve|camera|force|collider|volume|field|beam)
layer: string (optional layer id)
parameters: {name: value | {"value": v, "track": [{"time": s, "value": v, "interp": "linear|step|smooth"}]}}
inputs: {port: ref | [ref]}
metadata: json (free-form; agents may store rationale here)
```

### Common parameter groups

**transform** (spatial nodes): `position: vec3 = 0,0,0 (A)`,
`rotation: vec3 = 0,0,0 (A)` euler degrees XYZ, `scale: vec3 = 1,1,1 (A)`.

**window** (time-bound nodes): `start_time: float = 0 [0..]`,
`duration: float = -1` (-1 = until effect end), `phase: string = ""`
(when set, the compiler overrides start_time/duration with the timeline
phase window; a warning is emitted if the phase does not exist).

## Node types

### emitter  (Tier 1 spawner)
Produces particles for one `particle_system`, or acts as a source for a volume.
transform, window, plus:
```
shape: enum = sphere   [point, sphere, hemisphere, box, disc, ring, cone, line, curve, mesh, volume]
radius: float = 0.5 [0..]            sphere/hemisphere/disc/ring/cone base
inner_radius: float = 0 [0..]         ring/disc (annulus)
size: vec3 = 1,1,1                    box extents (full)
angle: float = 30 [0..180]            cone half-angle (deg)
length: float = 1 [0..]               line length along +X (local)
surface_only: bool = false            emit on surface instead of volume
rate: float = 50 [0..] (A)            particles per second
burst_count: int = 0 [0..]            particles emitted at each burst time
burst_times: float_list = [0]         seconds relative to start_time
velocity: float = 1 (A)               initial speed (m/s)
velocity_variance: float = 0 [0..]    +/- uniform on speed
direction: vec3 = 0,1,0               local direction; zero vector = radial from shape center
spread: float = 0 [0..180]            cone half-angle around direction (deg)
inherit_velocity: float = 0 [0..1]    fraction of emitter's own velocity added
radial_velocity: float = 0            extra speed along (particle - center)
max_particles: int = 0 [0..]          0 = unlimited (system cap still applies)
```
inputs: `particle: particle_system` (required unless feeding a volume),
`shape_curve: curve` (shape=curve), `shape_mesh: mesh` (shape=mesh),
`shape_volume: volume` (shape=volume).
outputs: `particles` (stream), `spawn` (event source).

### particle_system  (Tier 1; Tier 2 when physics=rigid)
Defines what a particle is: initial state, evolution over life, rendering.
```
max_particles: int = 10000 [1..2000000]
lifetime: float = 1 [0.001..]
lifetime_variance: float = 0 [0..]
size: float = 0.1 [0..]
size_variance: float = 0 [0..]
size_over_life: curve = [{0,1},{1,1}]
rotation: float = 0                    initial roll (deg)
rotation_variance: float = 0
angular_velocity: float = 0            deg/s
angular_velocity_variance: float = 0
color: color = 1,1,1,1
color_over_life: gradient = [{0,(1,1,1,1)},{1,(1,1,1,1)}]  multiplies color
opacity: float = 1 [0..1]
opacity_over_life: curve = [{0,1},{1,0}]
emissive: float = 0 [0..]              emissive multiplier (HDR)
emissive_over_life: curve = [{0,1},{1,1}]
mass: float = 1 [0.0001..]
drag: float = 0 [0..]                  linear drag coefficient (1/s)
render_mode: enum = billboard [billboard, stretched_billboard, mesh, ribbon, none]
blend: enum = additive [additive, alpha, premultiplied]
velocity_stretch: float = 0 [0..]      stretched_billboard: length = size*(1+stretch*speed)
align_to_velocity: bool = false
soft_particle_distance: float = 0.1 [0..]
sort: bool = false                     back-to-front sort for alpha
sprite_columns: int = 1 [1..64]
sprite_rows: int = 1 [1..64]
sprite_fps: float = 0                  0 = map life to sheet
physics: enum = none [none, rigid]     rigid -> Tier 2 (V1 falls back to particles + collision)
bounce: float = 0.3 [0..1]
friction: float = 0.2 [0..1]
kill_on_collision: bool = false
collision_radius: float = 0            0 = use size*0.5
orientation: enum = upright [upright, random, velocity, tumble]   mesh particles only
tilt: float = 0 [0..180]               upright/velocity: random lean of the up axis (deg)
mesh_scale: vec3 = 1,1,1               per-axis multipliers on top of `size` (mesh particles)
mesh_scale_variance: vec3 = 0,0,0      +/- uniform per axis (result clamped to >= 0.05)
```
inputs: `material: material`, `sprite: texture`, `mesh: mesh` (render_mode=mesh),
`forces: force[]`, `colliders: collider[]`, `trail: trail` (per-particle trail).
outputs: `particles`, `on_spawn`, `on_death`, `on_collision` (event sources).

#### Mesh particle orientation

`orientation`, `tilt`, `mesh_scale` and `mesh_scale_variance` apply only to
`render_mode: mesh`. Billboard systems ignore them entirely and keep the
identity orientation, so `rotation` and `angular_velocity` keep their billboard
roll meaning and existing effects are untouched.

The runtime writes a rotation quaternion per particle every step; the renderer
draws each mesh particle as `translate(position) * rotation(quaternion) *
scale(size * mesh_scale)`. The yaw from `rotation` is folded into the
quaternion, so the renderer only ever reads the quaternion.

| orientation | per-particle rotation |
|---|---|
| `upright` | yaw of `rotation` about world +Y, composed with the tilt: `yaw * tilt`. This is the old behaviour when `tilt = 0`. |
| `random` | one uniformly random 3D rotation, fixed at spawn, never updated. |
| `velocity` | the shortest rotation taking mesh +Y onto `normalize(velocity)`, composed with the tilt. Below 0.05 m/s it falls back to `upright`. |
| `tumble` | a random orientation at spawn, then spun by `angular_velocity` (+/- `angular_velocity_variance`, deg/s) about one random axis per particle. |

`tilt` leans the up axis by a per-particle angle uniform in `[0, tilt]` around a
random azimuth - a cone of leans, which is what stops a field of spikes looking
like a row of fence posts. It is applied in mesh space, so `angular_velocity`
under `upright` makes a tilted mesh precess rather than wobble.

`mesh_scale` multiplies `size` per axis and is applied in mesh space (before the
rotation), so a stretch turns with the mesh. Each axis draws its own variance:
`mesh_scale.axis + U(-1,1) * mesh_scale_variance.axis`, clamped to at least
0.05. Tall thin crystals are `mesh_scale: 0.4,2.5,0.4`; squashed rocks are
`mesh_scale: 1.2,0.7,1.1`.

When the instanced `mesh` node bakes several variants, each particle picks one
with `variant = seed32 % variants` and keeps it for life.

Determinism: the orientation draws happen **after** every pre-existing spawn
draw (docs/RUNTIME.md section 3), in the order tilt angle, tilt azimuth, random
orientation, tumble axis, `mesh_scale_variance` x/y/z. Billboard systems skip
them. Every value is a pure function of the particle seed, so an effect written
before these parameters existed produces exactly the state it did before.

### force
```
force_type: enum = gravity [gravity, directional, radial, vortex, turbulence, curl_noise, drag, attractor, repulsor, wind, buoyancy]
strength: float = 9.81 (A)
direction: vec3 = 0,-1,0           gravity/directional/wind/vortex axis
position: vec3 = 0,0,0 (A)         radial/vortex/attractor/repulsor center
radius: float = 0 [0..]            0 = infinite; falloff applies inside radius
falloff: enum = none [none, linear, inverse_square, smooth]
frequency: float = 1 [0..]         turbulence/curl noise spatial frequency (1/m)
octaves: int = 2 [1..8]
speed: float = 0.5                 noise animation speed (1/s)
temperature: float = 1 [0..]       buoyancy: multiplies strength
start_time: float = 0
duration: float = -1
phase: string = ""
```
inputs: `noise: noise` (optional override for turbulence/curl). outputs: `force`.

### field  (data; V1 evaluates only `noise` fields)
```
field_type: enum = scalar [scalar, vector, density, temperature, fuel, velocity, sdf, noise]
bounds_min: vec3 = -1,-1,-1
bounds_max: vec3 = 1,1,1
resolution: int = 32 [4..512]
```
inputs: `source: noise|volume|mesh`. outputs: `field`.

### volume  (Tier 3; `mode: procedural` is raymarched, `mode: simulation` is a stub)
transform, window, plus:
```
mode: enum = procedural [procedural, simulation]   simulation = the future fluid solver (W104 until then)
volume_type: enum = smoke [smoke, fire, fog, dust, magic, generic_density]
```
`mode: procedural` - a raymarched closed-form density field, see docs/VOLUMES.md
for the function, the shape table and the marching model:
```
shape: enum = sphere [sphere, column, disc, ring, nebula, cone]
radius: float = 1.5 [0..] (A)          shape radius (m)
height: float = 2 [0..] (A)            column/cone height, disc/ring thickness (m)
density: float = 1 [0..] (A)           extinction per metre
emission: float = 1 [0..] (A)          HDR emission multiplier (bloom feeds on it)
color: color = 0.6,0.3,1,1 (A)         base tint
color_hot: color = 1,1,1,1             tint at the densest core
filament_scale: float = 2 [0.1..]      noise frequency (1/m)
strands: float = 0.5 [0..1]            0 = soft clouds, 1 = stringy filaments
carve: float = 0.45 [0..1]             density threshold: higher carves more holes
softness: float = 0.6 [0..1]           edge falloff of the bounding shape
spiral_arms: int = 0 [0..8]            azimuthal arm count (nebula/disc)
arm_sharpness: float = 1.5 [0.1..]
twist: float = 0 (A)                   radians of twist per metre of height
spin: float = 0 (A)                    revolutions per second around the up axis
climb: float = 0 (A)                   m/s upward advection of the noise field
scatter: float = 0.3 [0..1]            single-scatter weight from the scene lights
march_steps: int = 48 [8..192]         quality/speed knob (a backend may clamp it)
```
`mode: simulation` - the fluid domain (V1: reported, not solved):
```
bounds: vec3 = 2,2,2
voxel_size: float = 0.05 [0.005..1]
density: float = 1 [0..] (A)           injected density
temperature: float = 0 [0..] (A)
fuel: float = 0 [0..]
dissipation: float = 0.1 [0..]
buoyancy: float = 1
vorticity: float = 0.2 [0..]
cooling: float = 0.5 [0..]
expansion: float = 0 [0..]
combustion_rate: float = 1 [0..]
```
inputs: `sources: emitter[]`, `forces: force[]`, `colliders: collider[]`, `material: material`
(all four are for the simulation path; a procedural volume needs none of them).
outputs: `volume`.

### mesh  (Tier 0 renderable, or emitter shape)
transform, plus:
```
source: enum = primitive [primitive, imported, procedural, generated, particle_instanced]
primitive: enum = sphere [sphere, cube, plane, disc, ring, cone, cylinder, capsule, ribbon, tube,
                          crystal, rock, shard]
radius: float = 0.5 [0..]
inner_radius: float = 0 [0..]      ring
height: float = 1 [0..]            cone/cylinder/capsule/tube/crystal/shard
size: vec3 = 1,1,1                 cube/plane
segments: int = 24 [3..256]
variants: int = 1 [1..16]          crystal/rock/shard: seeded variants to bake
irregularity: float = 0.35 [0..1]  crystal/rock/shard: deviation from the ideal shape
path: string = ""                  imported (obj) - V1 loads OBJ only
color: color = 1,1,1,1 (A)
emissive: float = 0 [0..] (A)
visible: bool = true
start_time: float = 0
duration: float = -1
phase: string = ""
```
inputs: `material: material`. outputs: `mesh`.

#### Seeded primitives (`crystal`, `rock`, `shard`)

Procedural, flat-shaded and a pure function of `(parameters, seed)`. Like every
other primitive they are centred on the origin with +Y up, and their bounds fit
inside `x,z` in `[-radius, radius]` and `y` in `[-height/2, height/2]` (`rock`
uses `[-radius, radius]` on all three axes). A crystal's apex is therefore at
`+height/2` and its base at `-height/2`.

* `crystal` - an irregular n-gon spike. `segments` is clamped to 3..8 facets
  (5..8 reads best); vertices jitter by up to `irregularity * 0.5` in radius and
  a quarter of a facet in angle, the apex is offset laterally by up to
  `irregularity * 0.35 * radius`, and with probability `irregularity` a shorter
  twin spike is fused at the base at 60% of the height.
* `rock` - a low-poly boulder: an icosphere (20 facets, or 80 when
  `segments >= 10`) pushed along its normals by seeded fbm scaled by
  `irregularity * radius * 0.6`, then squashed by per-axis random factors in
  `[1 - 0.4*irregularity, 1 + 0.4*irregularity]`.
* `shard` - a thin angular flake: an irregular 4..6 sided outline with one sharp
  end, extruded to a thickness of `0.12 * radius`.

`variants` bakes that many meshes from `derive_seed(node stream, i)` under the
resource keys `"<mesh id>"`, `"<mesh id>#1"` ... `"<mesh id>#N-1"`. A
`particle_system` instancing the node spreads its particles across them (see
"Mesh particle orientation"); anything else uses variant 0. Other primitives
ignore `variants` and `irregularity` without a warning.

### curve  (Tier 0; paths for emitters, trails, beams, motion)
transform, plus:
```
points: vec3_list = [(0,0,0),(0,1,0)]
curve_type: enum = catmull_rom [linear, catmull_rom, bezier]
closed: bool = false
segments: int = 32 [1..1024]
noise_amplitude: float = 0 [0..]
noise_frequency: float = 1 [0..]
```
outputs: `curve`.

### trail  (Tier 0 ribbon following a source)
window, plus:
```
width: float = 0.1 [0..] (A)
lifetime: float = 0.5 [0.001..]
taper: curve = [{0,1},{1,0}]          width over segment age
opacity_over_life: curve = [{0,1},{1,0}]
color: color = 1,1,1,1 (A)
emissive: float = 0 [0..] (A)
noise_amplitude: float = 0 [0..]
noise_frequency: float = 1 [0..]
twist: float = 0                        deg over trail length
uv_scroll: float = 0                    UV units per second
min_vertex_distance: float = 0.02 [0.0001..]
max_segments: int = 64 [2..1024]
blend: enum = additive [additive, alpha, premultiplied]
```
inputs: `source: mesh|emitter|light|curve|particle_system` (required), `material: material`.
outputs: `trail`.

### beam  (Tier 0 analytic lightning/laser)
window, plus:
```
origin: vec3 = 0,0,0 (A)
target: vec3 = 0,2,0 (A)
width: float = 0.05 [0..] (A)
segments: int = 16 [1..256]
noise_amplitude: float = 0 [0..]        displacement of interior points (m)
noise_frequency: float = 4 [0..]
jitter_rate: float = 30 [0..]           re-randomizations per second (0 = static)
branching: int = 0 [0..16]              branch count
branch_probability: float = 0.5 [0..1]
branch_length: float = 0.3 [0..1]       fraction of main beam length
pulse_speed: float = 0                  pulse travel speed (beam lengths/s)
pulse_frequency: float = 0
color: color = 1,1,1,1 (A)
emissive: float = 4 [0..] (A)
blend: enum = additive [additive, alpha, premultiplied]
```
inputs: `origin_node: mesh|emitter|light|curve`, `target_node: mesh|emitter|light|curve`, `material: material`.
outputs: `beam`.

### light  (Tier 0)
transform, window, plus:
```
light_type: enum = point [point, spot, area]
intensity: float = 10 [0..] (A)         linear; ground irradiance ~ intensity/(d^2+1); with albedo 0.18, intensity 10 lights a 1 m pool to ~0.9 (use 2-6 for hand-held glows, 20-40 for room-scale fire, keep flashes under ~30)
radius: float = 5 [0..] (A)             influence radius (m)
color: color = 1,1,1,1 (A)
temperature: float = 0 [0..]            Kelvin; 0 = use color as-is
flicker_amplitude: float = 0 [0..1]
flicker_frequency: float = 12 [0..]
cone_angle: float = 45 [0..90]          spot
direction: vec3 = 0,-1,0                spot/area
area_size: vec2 = 1,1
cast_shadows: bool = false
```
outputs: `light`.

### decal  (Tier 0 projected sprite on ground/surface)
transform, window, plus:
```
shape: enum = circle [circle, rect]
size: vec2 = 1,1 (A)
color: color = 1,1,1,1 (A)
opacity: float = 1 [0..1] (A)
emissive: float = 0 [0..] (A)
fade_in: float = 0.1 [0..]
fade_out: float = 0.5 [0..]
projection_normal: vec3 = 0,1,0
blend: enum = alpha [additive, alpha, premultiplied]
```
inputs: `material: material`, `texture: texture`. outputs: `decal`.

### material
```
base_color: color = 1,1,1,1
opacity: float = 1 [0..1]
emissive_color: color = 1,1,1,1
emissive_intensity: float = 0 [0..]
blend: enum = additive [additive, alpha, premultiplied]
shading: enum = unlit [unlit, lit]
soft_particle: bool = true
depth_fade: float = 0.1 [0..]
distortion: float = 0 [0..]            screen-space refraction strength
fresnel_power: float = 0 [0..]         0 = off
uv_scroll: vec2 = 0,0
uv_rotate: float = 0                    deg/s
dissolve: float = 0 [0..1]              threshold against noise texture
erosion: float = 0 [0..1]               edge erosion width
double_sided: bool = true
```
inputs: `base_texture: texture`, `noise_texture: texture`, `gradient_texture: texture`,
`temperature_gradient: gradient` is a parameter not a port:
```
temperature_gradient: gradient = []     when non-empty, remaps luminance/temperature to color
```
outputs: `material`.

### noise  (parameter provider for forces/textures)
```
noise_type: enum = simplex [perlin, simplex, worley, fbm, curl, blue_noise, cellular]
frequency: float = 1 [0..]
octaves: int = 3 [1..8]
lacunarity: float = 2 [1..]
gain: float = 0.5 [0..1]
amplitude: float = 1
speed: float = 0                        animation along 4th dim (1/s)
offset: vec3 = 0,0,0
```
outputs: `noise`.

### collider
transform, plus:
```
collider_type: enum = plane [plane, sphere, box, capsule, mesh, sdf]
normal: vec3 = 0,1,0                    plane
radius: float = 0.5 [0..]               sphere/capsule
height: float = 1 [0..]                 capsule
size: vec3 = 1,1,1                      box
bounce: float = 0.3 [0..1]
friction: float = 0.2 [0..1]
kill_on_collision: bool = false
```
inputs: `mesh: mesh` (collider_type=mesh|sdf). outputs: `collider`, `on_collision`.

### event
```
trigger: enum = on_time [on_spawn, on_death, on_collision, on_distance, on_time, on_peak, on_impact]
time: float = 0                          on_time: absolute effect time
distance: float = 1 [0..]                on_distance: from source origin
probability: float = 1 [0..1]
max_triggers: int = 0 [0..]              0 = unlimited
burst_count: int = 0 [0..]               override burst on targets (0 = target's own burst_count)
inherit_position: bool = true            spawn at triggering particle position
inherit_velocity: float = 0 [0..1]
```
inputs: `source: particle_system|emitter|collider` (required for particle triggers),
`targets: emitter[]` (required). outputs: `event`.

### camera  (preview camera; effect may define one; tools use it by default)
```
position: vec3 = 0,1.5,5 (A)
target: vec3 = 0,1,0 (A)
up: vec3 = 0,1,0
fov: float = 45 [1..170]
near: float = 0.05 [0.0001..]
far: float = 200 [0.001..]
exposure: float = 1 [0..]
```
outputs: `camera`.

### post_effect  (use sparingly)
window, plus:
```
post_type: enum = bloom [bloom, distortion, heat_haze, chromatic_aberration, exposure_pulse]
intensity: float = 1 [0..] (A)
threshold: float = 1 [0..]        bloom
radius: float = 0.05 [0..1]       bloom (fraction of width) / haze
frequency: float = 8 [0..]        heat_haze / pulse
```
outputs: `post`.

### texture  (procedural texture graph or file; baked by the compiler)
```
source: enum = procedural [procedural, file]
path: string = ""               image file when source=file
width: int = 256 [1..4096]
height: int = 256 [1..4096]
frames: int = 1 [1..256]          animated textures (flipbook baked, `time` op input)
columns: int = 1 [1..64]          flipbook grid layout of a file texture; frames become columns*rows
rows: int = 1 [1..64]             flipbook grid layout of a file texture; frames become columns*rows
graph: json = {}                  procedural texture graph, see below
```
outputs: `texture`.

#### File textures (`source: file`)

The compiler loads the image and bakes it into the same `TextureResource` a
procedural graph produces, so a file texture is usable anywhere a procedural one
is (sprite, decal, material slot).

* `path` is absolute, or relative to the directory of the effect document it is
  written in (the tools layer passes that directory as `CompileOptions::base_dir`;
  with no document it is the working directory).
* 8-bit formats (PNG/JPG/TGA/BMP) are read as sRGB and decoded to linear; `.exr`
  is read as linear. Alpha is always linear.
* `columns` x `rows` describe a flipbook grid. The compiler unrolls the grid,
  left to right and top to bottom, into the engine's horizontal strip layout:
  `frames = columns * rows`, each frame `width/columns` by `height/rows` pixels.
  A grid the image does not divide evenly warns W105 and drops the trailing
  pixels.
* Without a grid, `frames > 1` means the file already is a horizontal strip.
* `width`/`height` are only applied when they are written on the node: the frames
  are then resampled to that size. Leave them out to keep the file's own
  resolution.
* A missing or unreadable file is error E102 and names the resolved path.

```json
{"id": "tex_fire", "type": "texture",
 "parameters": {"source": "file", "path": "../textures/fire_8x4.png",
                "columns": 8, "rows": 4}}
```

## Procedural texture graph (`texture.graph`)

```
{ "nodes": [ {"id": "n1", "op": "fbm", "params": {...}, "inputs": {"a": "n0"}} ...],
  "output": "n7" }
```
Every op produces an RGBA float image at the texture resolution. Ops:
`perlin`, `simplex`, `worley`, `fbm`, `curl`, `cellular`, `blue_noise`
(`frequency`, `octaves`, `lacunarity`, `gain`, `seed`, `animate` speed),
`gradient_linear` (`angle`, `start`, `end`), `gradient_radial` (`center`,
`radius`, `inner_radius`, `falloff` linear|smooth|quadratic),
`ring` (`radius`, `thickness`, `softness`), `cracks` (`density`, `width`,
`seed`), `voronoi` (`cells`, `mode` distance|id|edges), `erosion`
(`amount`, `noise` input), `distort` (`amount`, input `by`),
`flow_map` (from `a` vector-ish), `normal_from_height` (`strength`),
`channel_pack` (`r`,`g`,`b`,`a` inputs, `channel` selection),
`levels` (`in_low`, `in_high`, `out_low`, `out_high`, `gamma`),
`math` (`mode` add|multiply|subtract|max|min|screen|lerp, `factor`, inputs `a`,`b`),
`invert`, `dissolve_mask` (`threshold`, `softness`, input `a`),
`colorize` (`gradient`), `constant` (`color`), `time` (`speed`) provides
animation phase for animated noises,
`flame` (`frequency`, `speed`, `warp`, `width`, `sharpness`, `licks`, `seed`),
`fire_sim` (`seed`, `fuel`, `fuel_width`, `buoyancy`, `turbulence`,
`turbulence_scale`, `cooling`, `detail`, `speed`, `substeps`, `warmup`, `loop`,
`flicker`, `sharpness`).

`flame` is the fire sprite generator: bake it with `frames: 16..32` and feed it
through `levels` to pick the silhouette, then colour it with the material's
`temperature_gradient` or a `colorize` op. It stores the flame root at `v = 0`
and the tip at `v = 1`, which is what draws upright (the renderer's sprite V axis
points up the screen) and makes a `stretched_billboard` tip point along the
particle's velocity. Every time-carrying noise axis is wrapped onto a circle, so
the flipbook loops seamlessly. Fire ramp for `colorize` or
`material.temperature_gradient`:
`[[0,[0.05,0,0,1]], [0.25,[0.8,0.1,0,1]], [0.5,[1,0.45,0.05,1]], [0.75,[1,0.85,0.35,1]], [1,[1,1,0.85,1]]]`.

`fire_sim` is the other fire generator, and the one to reach for when the fire
has to look simulated rather than painted. `flame` draws one smooth tongue from
a noise-warped silhouette; `fire_sim` bakes a small deterministic 2D flame
simulation, so the shapes come out of the flow: tongues rise, neck, split, tear
off and burn out, the edges erode into rags, and the motion is coherent from
frame to frame. It costs about 0.3 s to bake 128x192 at 32 frames; `flame` is
far cheaper, so keep `flame` for small distant licks and spend `fire_sim` on the
fire the player is looking at. Same conventions as `flame`: single channel in
[0,1] written to rgb and alpha, root at `v = 0` and tip at `v = 1`, and a
`temperature_gradient` or `colorize` with the fire ramp above turns it into
fire.

One step of the sim is: velocity = `buoyancy` * heat upwards (accelerating with
height, which stretches the column) + `turbulence` * the curl of an animated
noise potential (`turbulence_scale` sets the eddy size, `detail` the octaves) +
an inward entrainment flow integrated from `dvx/dx = -dvy/dy` along each row,
which is what necks a plume and pinches puffs off it; then semi-Lagrangian
advection with bilinear sampling (cold air at the sides and above the tip, the
fuel bed repeated below the base); then cooling, whose rate grows with height
and is modulated by noise, plus a subtractive term that erases whatever is
nearly cold - that is what leaves ragged edges instead of a soft halo; then a
light diffusion; then the fuel band, `fuel_width` of the tile wide and lit to
`fuel` (>1 widens the white-hot core), broken into moving patches so tongues are
born apart, and scaled by `flicker` over time. One frame is captured every
`substeps` steps, after `warmup` steps. `sharpness` is an output S-curve about
0.5: it keeps the core white and crushes the haze. The bottom of the tile fades
in over a noise-broken edge so a big sprite has no hard straight line across its
root.

With `loop` (the default) every noise axis that carries time is wrapped onto a
circle, so the forcing is exactly periodic over the flipbook, and `warmup` is
raised to a whole loop. Cooling and outflow erase the initial state within a
loop, so the sim lands on the forcing's limit cycle and frame 0 continues frame
N-1 without a cross-fade - and therefore without the ghosting a cross-fade
would give. With `loop: false` the flipbook does not close; use it when the
sheet is played once.

```json
{"id": "tex_fire", "type": "texture",
 "parameters": {"width": 128, "height": 192, "frames": 32,
                "graph": {"nodes": [{"id": "fire", "op": "fire_sim",
                                     "params": {"seed": 3, "buoyancy": 1.7, "turbulence": 1.15,
                                                "cooling": 1.8, "fuel_width": 0.6}}],
                          "output": "fire"}}}
```

## Effect-level

```
schema_version: "0.1.0"
name: string
duration: float [0.01..]
seed: int
timeline: {"phases": [{"name": "anticipation|activation|peak|sustain|decay|<custom>", "start": s, "end": s}]}
layers: [{"id", "name", "role": telegraph|ignition|primary|secondary|interaction|aftermath|custom, "enabled", "metadata"}]
nodes: [...]
metadata: json   (agents should record source prompt / reference / analysis id here)
```

## Validation rules (implemented in `aether::validate`)

E001 duplicate node id, E002 invalid id format, E003 unknown node type,
E004 unknown parameter, E005 parameter type mismatch, E006 value out of
range, E007 invalid enum value, E008 unresolved reference, E009 port type
mismatch, E010 required input missing, E011 multi-input given to single port,
E012 cycle in parent chain, E013 cycle in input graph, E014 unknown layer,
E015 effect duration invalid, E016 timeline phase invalid (end<=start or
outside duration), E017 unknown port, E018 keyframe time invalid,
E019 curve/gradient keys not sorted or out of [0,1], E020 schema version
unsupported. W001 unknown phase reference, W002 unused node (no consumer and
not renderable), W003 max_particles budget high, W004 node disabled but
referenced, W005 deprecated parameter, W006 keyframe set on a parameter not
marked animatable (stored, but runtimes may ignore the track).
