# Volumes: procedural raymarched volumetrics (design, M1.5)

The `volume` node is a stub today (Tier 3 placeholder for a future fluid
simulation). The fastest route to nebula / aura / smoke-column / magic-cloud
quality is not a fluid solver but a **GPU-raymarched procedural density
field**: a small bounded volume whose density is a closed-form function of
position, time and a handful of agent-facing parameters. This is what the
best real-time sandboxes do ("march steps", "filament scale", "carve
threshold", "spiral arms" are exactly such parameters). It stays inside our
architecture: the agent authors parameters, the renderer owns the shader.

## Vocabulary extension (volume node)

```
mode: enum = procedural [procedural, simulation]   simulation = the future fluid path (W104 until then)
shape: enum = sphere [sphere, column, disc, ring, nebula, cone]
radius: float = 1.5 [0..] (A)          shape radius (m)
height: float = 2.0 [0..] (A)          column/cone height (m)
density: float = 1.0 [0..] (A)         overall opacity per metre
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
march_steps: int = 48 [8..192]         quality/speed knob (viewer may clamp)
```
Plus the transform group (position/rotation/scale) and the window group.
The existing physical parameters (temperature, fuel, dissipation, buoyancy,
vorticity, cooling, expansion, combustion_rate) stay for `mode: simulation`.

## Density function (shared by every backend)

```
p_local  = inverse(world_transform) * p
shape_sdf = signed distance of `shape` (sphere: |p|-r; column: max(|p.xz|-r, |p.y|-h/2); disc: thin cylinder; ring: torus; nebula: flattened ellipsoid; cone)
mask     = 1 - smoothstep(-softness*r, 0, shape_sdf)
q        = p_local rotated by spin*time about Y, sheared by twist*p.y, shifted by (0, -climb*time, 0)
n_soft   = fbm3(q * filament_scale, seed)            in [-1,1] -> [0,1]
n_ridged = 1 - |fbm3(q * filament_scale * 1.7, seed+1)|
noise    = mix(n_soft, n_ridged, strands)
arms     = spiral_arms > 0 ? pow(0.5 + 0.5*cos(spiral_arms*atan2(q.z,q.x) - arm_sharpness*length(q.xz)), arm_sharpness) : 1
d        = mask * arms * smoothstep(carve, carve + 0.25, noise) * density
```
Colour: `mix(color, color_hot, saturate(d * 2))`; emission `d * emission`;
lighting: `scatter * sum(lights)` with the same falloff as particles.

## Backends

* **three.js viewer**: a bounding-box mesh with a raymarching ShaderMaterial
  (front-face entry, `march_steps` samples, early exit at alpha 0.98,
  depth-tested against the scene depth texture, additive or premultiplied
  composite). Noise in-shader (hash-based fbm, same octaves as the CPU).
* **Reference renderer**: the same function marched per pixel on the CPU at
  reduced steps (quality knob) so tests and the studio's reference render
  agree with the viewer within tolerance.
* **Unreal**: a translucent material with the same node math; the bridge
  passes the parameters as material parameters on a box mesh.

## Why this and not a fluid sim first

A fluid sim (M2) produces motion the closed-form field cannot (rolling
smoke, licking fire), but its output must still be raymarched to look good.
Building the raymarcher first means the sim slots in later as another
density source (`mode: simulation`) with the same look pipeline.
