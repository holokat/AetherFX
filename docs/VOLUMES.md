# Volumes: procedural raymarched volumetrics (implemented, M1.5)

The `volume` node has two modes. `mode: procedural` (the default) is a
**GPU/CPU-raymarched procedural density field**: a small bounded volume whose
density is a closed-form function of position, time and a handful of
agent-facing parameters. That is what nebulae, auras, smoke columns and magic
clouds are made of here, and it is implemented end to end - vocabulary,
runtime, C ABI, three.js viewer and the reference renderer.
`mode: simulation` is still the Tier 3 placeholder for a future fluid solver
and still compiles with warning W104.

The fastest route to that look was never a fluid solver: it is the field below.
"March steps", "filament scale", "carve threshold" and "spiral arms" are
exactly the knobs the best real-time sandboxes expose, and they stay inside our
architecture - the agent authors parameters, the renderer owns the shader.

## Vocabulary (volume node)

```
mode: enum = procedural [procedural, simulation]   simulation = the future fluid path (W104 until then)
shape: enum = sphere [sphere, column, disc, ring, nebula, cone]
radius: float = 1.5 [0..] (A)          shape radius (m)
height: float = 2.0 [0..] (A)          column/cone height, disc/ring thickness (m)
density: float = 1.0 [0..] (A)         extinction per metre
emission: float = 1.0 [0..] (A)        HDR emission multiplier (bloom feeds on it)
color: color = 0.6,0.3,1.0,1.0 (A)     base tint
color_hot: color = 1,1,1,1             tint at the densest core
filament_scale: float = 2.0 [0.1..]    noise frequency (1/m)
strands: float = 0.5 [0..1]            0 = soft clouds, 1 = stringy filaments (ridged noise blend)
carve: float = 0.45 [0..1]             density threshold: higher carves more holes
softness: float = 0.6 [0..1]           edge falloff of the bounding shape
spiral_arms: int = 0 [0..8]            azimuthal arm count (nebula/disc)
arm_sharpness: float = 1.5 [0.1..]
twist: float = 0 (A)                   radians of twist per metre of height
spin: float = 0 (A)                    revolutions per second around the up axis
climb: float = 0 (A)                   m/s upward advection of the noise field
scatter: float = 0.3 [0..1]            single-scatter lighting weight from scene lights
march_steps: int = 48 [8..192]         quality/speed knob (a backend may clamp it)
```
Plus the transform group (position/rotation/scale) and the window group.
The existing physical parameters (temperature, fuel, dissipation, buoyancy,
vorticity, cooling, expansion, combustion_rate) stay for `mode: simulation`.

## Shapes

`radius` is always the horizontal radius; `height` means something different
per shape, and each shape has a local box the field never leaves. Every backend
agrees on this table - it is `aether::volume_shape_extent()` in
`src/core/src/frame_state.cpp`, mirrored by `volumeExtent()` in
`static/viewer/volumes.js`.

| shape | SDF | `height` is | local half-extents |
|---|---|---|---|
| `sphere` | `\|p\| - r` | unused | `(r, r, r)` |
| `column` | `max(\|p.xz\| - r, \|p.y\| - h/2)` | the full height | `(r, h/2, r)` |
| `disc` | a cylinder cut to a slab `h/4` thick | four times the slab thickness | `(r, h/8, r)` |
| `ring` | a torus of major radius `r`, tube radius `h/4` | four times the tube radius | `(r + h/4, h/4, r + h/4)` |
| `nebula` | ellipsoid with radii `(r, h/2, r)` | the vertical diameter | `(r, h/2, r)` |
| `cone` | base radius `r` at `y = -h/2`, apex at `y = +h/2` | the full height | `(r, h/2, r)` |

They are approximate SDFs on purpose: they drive a soft mask, not a surface.

## Density function (shared by every backend)

```
p_local  = inverse(world_transform) * p
shape_sdf = signed distance of `shape` (see the table above)
mask     = 1 - smoothstep(-softness*r, 0, shape_sdf)      0 outside the shape, so the box bounds it exactly
q        = p_local rotated about Y by (2pi*spin*time + twist*p.y), then shifted down by climb*time
n_soft   = 0.5 + 0.5 * fbm3(q * filament_scale, seed)               4 octaves, lacunarity 2, gain 0.5
n_ridged = 1 - |fbm3(q * filament_scale * 1.7, seed+1)|
noise    = mix(n_soft, n_ridged, strands)
arms     = spiral_arms > 0 ? pow(0.5 + 0.5*cos(spiral_arms*atan2(q.z,q.x) - arm_sharpness*length(q.xz)), arm_sharpness) : 1
d        = mask * arms * smoothstep(carve, carve + 0.25, noise) * density
```
`mask` is evaluated first and the noise is skipped when it is zero, which is
most of the box for a sphere or a nebula.

Colour: `mix(color, color_hot, saturate(d * 2))`; emission `* emission`;
lighting: `scatter * sum(lights)` with the same falloff as particles.

The reference implementation is `src/render/src/volume_field.hpp`
(`VolumeField::density_local`, plus `volume_density(state, world_p, time)` as
the plain entry point). `static/viewer/volumes.js` is its GLSL translation.

## Marching

Every backend integrates the same emission/absorption model along the ray, in
world metres, front to back:

```
alpha_i  = 1 - exp(-d * segment)                 segment = metres between samples
tint     = mix(color, color_hot, saturate(d*2))
radiance = tint * emission + tint * scatter * sum(light colour * intensity * falloff)
acc     += radiance * transmittance * alpha_i
transmittance *= 1 - alpha_i
```

Using `1 - exp(-d * segment)` rather than a per-step constant is what makes the
result **independent of the step count**: a 32-step CPU frame and a 48-step
viewer frame differ in the detail the march resolves, not in overall opacity.
Rays stop early once transmittance drops below 0.02, and the accumulated colour
is already premultiplied, so it composites `ONE, ONE_MINUS_SRC_ALPHA`.

## Backends

* **three.js viewer** (`static/viewer/volumes.js`): a box mesh scaled to the
  shape's local bounds with a raymarching `ShaderMaterial`. Back faces, so the
  camera may sit inside the box; `march_steps` clamped to 8..96; the first
  sample jittered by a per-pixel hash to hide banding; noise is hash-based value
  noise with the CPU's octave count, lacunarity and gain. It never writes or
  tests depth - occlusion comes from clamping the exit point against the opaque
  depth texture the soft particles already produce, per sample. Drawn at
  `renderOrder` 5, i.e. after the opaque pass and before the particle systems.
* **Reference renderer** (`draw_volumes` in `src/render/src/software_renderer.cpp`):
  the same function marched per pixel on the CPU between the opaque pass and the
  transparents, at `min(march_steps, 32)` samples, over the volume's projected
  screen rect only, respecting the opaque depth buffer. Deterministic, no
  threads, fixed summation order.
* **Unreal**: a translucent material with the same node math; the bridge passes
  the parameters as material parameters on a box mesh. `aetherfx_volume_info`
  (docs/ENGINE_INTEGRATION.md) hands a host everything it needs.

## Why this and not a fluid sim first

A fluid sim (M2) produces motion the closed-form field cannot (rolling
smoke, licking fire), but its output must still be raymarched to look good.
Building the raymarcher first means the sim slots in later as another
density source (`mode: simulation`) with the same look pipeline.
