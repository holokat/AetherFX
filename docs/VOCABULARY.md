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
```
inputs: `material: material`, `sprite: texture`, `mesh: mesh` (render_mode=mesh),
`forces: force[]`, `colliders: collider[]`, `trail: trail` (per-particle trail).
outputs: `particles`, `on_spawn`, `on_death`, `on_collision` (event sources).

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

### volume  (Tier 3; V1 backend is a stub that reports statistics)
transform, window, plus:
```
volume_type: enum = smoke [smoke, fire, fog, dust, magic, generic_density]
bounds: vec3 = 2,2,2
voxel_size: float = 0.05 [0.005..1]
density: float = 1 [0..] (A)
temperature: float = 0 [0..] (A)
fuel: float = 0 [0..]
dissipation: float = 0.1 [0..]
buoyancy: float = 1
vorticity: float = 0.2 [0..]
cooling: float = 0.5 [0..]
expansion: float = 0 [0..]
combustion_rate: float = 1 [0..]
```
inputs: `sources: emitter[]`, `forces: force[]`, `colliders: collider[]`, `material: material`.
outputs: `volume`.

### mesh  (Tier 0 renderable, or emitter shape)
transform, plus:
```
source: enum = primitive [primitive, imported, procedural, generated, particle_instanced]
primitive: enum = sphere [sphere, cube, plane, disc, ring, cone, cylinder, capsule, ribbon, tube]
radius: float = 0.5 [0..]
inner_radius: float = 0 [0..]      ring
height: float = 1 [0..]            cone/cylinder/capsule/tube
size: vec3 = 1,1,1                 cube/plane
segments: int = 24 [3..256]
path: string = ""                  imported (obj) - V1 loads OBJ only
color: color = 1,1,1,1 (A)
emissive: float = 0 [0..] (A)
visible: bool = true
start_time: float = 0
duration: float = -1
phase: string = ""
```
inputs: `material: material`. outputs: `mesh`.

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
intensity: float = 10 [0..] (A)         radiometric-ish, linear
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
path: string = ""
width: int = 256 [1..4096]
height: int = 256 [1..4096]
frames: int = 1 [1..256]          animated textures (flipbook baked, `time` op input)
graph: json = {}                  procedural texture graph, see below
```
outputs: `texture`.

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
animation phase for animated noises.

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
