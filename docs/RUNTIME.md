# Runtime Semantics (CPU reference, and the target for GPU parity)

This document defines exactly what one simulation step does. The CPU runtime
(src/sim) implements it; any later backend must match it within floating
point tolerance. Where the vocabulary leaves a choice, the choice is made
here. Units: meters, seconds, degrees in parameters (radians internally).

## 1. Step loop

`step()` advances from `t0 = time` to `t1 = t0 + dt` (fixed `dt`, default
1/60). Order, per compiled node in execution order (topological, ties by id):

1. Evaluate animated parameters at `t1` (keyframe tracks). Time is exact:
   `time = frame_index * dt`, never accumulated.
2. Analytic nodes (light, mesh, decal, beam, curve, camera, post_effect,
   volume stub) produce their FrameState entries if `t1` is inside their
   window; otherwise nothing. Bounded windows are `[start, end)`; an
   unbounded window (`end < 0`) is open-ended so the frame at exactly
   `t == duration` still renders. `reset()` evaluates the analytic scene at
   t = 0 so frame 0 is renderable without stepping.
3. Emitters compute spawn requests for the interval `(t0, t1]`:
   * continuous: `acc += rate(t1) * dt; n = floor(acc); acc -= n` while the
     emitter window contains `t1`; `acc` is reset to 0 when the window is
     entered.
   * bursts: each `burst_times[i]` fires when
     `t0 < window.start + burst_times[i] <= t1`. After `reset()`,
     `t0` is treated as `-1e-9` so a burst at exactly 0 fires on the first
     step. `burst_times: []` disables self-bursting (event targets).
   * `max_particles` on the emitter caps its lifetime total spawned.
4. Particle systems, in order: (a) age existing particles, kill dead ones
   (`age >= lifetime`, fire `on_death`), (b) spawn requested particles
   (fire `on_spawn`), (c) accumulate forces, (d) integrate, (e) collide
   (fire `on_collision`), (f) apply over-life modulation, (g) trails.
5. Events fire targets immediately within the same step (a burst on a
   target emitter is queued and spawned when that system runs; if the
   target's system already ran this step, it spawns next step).
6. Timeline events (`on_time`, `on_peak`) fire when `t0 < event_time <= t1`.

Particle storage is compacted **stably** (order preserved) when particles
die; new particles are appended in spawn order. Spawn order within a step:
emitters in execution order, then event bursts in the order they fired.

## 2. Randomness

* Emitter stream: `Pcg32(derive_seed(effect.seed, emitter.id, emitter.seed))`.
  Used only to derive per-particle seeds: particle with spawn index `k`
  (0-based, counting all particles ever spawned by that emitter) gets
  `seed_k = derive_seed(emitter_stream_seed, k)`. Its 32-bit form
  `uint32_t(seed_k ^ (seed_k >> 32))` is stored in `ParticleBuffer::seed`.
* All random draws for that particle (position on shape, direction, speed,
  lifetime, size, rotation, angular velocity variances) come from
  `Pcg32(seed_k)` in the fixed order listed in section 3. Nothing about
  other particles influences particle `k`: a GPU can reproduce it.
* Node-level randomness (beam jitter, light flicker, event probability) uses
  `Pcg32(derive_seed(node_stream, floor(time * rate)))` style hashing of
  time so it is a pure function of time, never of step count.

## 3. Emitter spawn

Draw order from the particle's RNG: (1) shape position, (2) direction,
(3) speed variance, (4) lifetime variance, (5) size variance, (6) rotation
variance, (7) angular velocity variance. Unused draws are still consumed.

Local position by `shape` (emitter local space):

| shape | position |
|---|---|
| point | 0 |
| sphere | uniform in ball of `radius` (surface_only: on the sphere) |
| hemisphere | sphere, then `y = abs(y)` |
| box | uniform in `size` (surface_only: on one face chosen by area) |
| disc | XZ plane, uniform by area in annulus `[inner_radius, radius]` |
| ring | same as disc; if `inner_radius >= radius` exactly on the circle |
| cone | uniform on base disc of `radius` (XZ); direction spread uses `angle` |
| line | `(u*length - length/2, 0, 0)`, u uniform |
| curve | `evaluate_curve(points, t)` with t uniform, in the curve node's local space, then the emitter transform |
| mesh | area-weighted surface sample of `shape_mesh` (mesh local space), then the emitter transform |
| volume | uniform in the volume node's bounds (transformed by the volume node) |

Local direction: if `direction` is the zero vector: `normalize(local_pos)`
(random unit vector when `local_pos` is ~0); otherwise `normalize(direction)`.
Then a cone spread of half-angle `spread` (shape=cone: `angle`) around it,
uniform on the cap. World: `dir_w = R_world * dir_local`,
`pos_w = M_world * pos_local`, where `M_world = Effect::world_transform`.

Velocity: `v = dir_w * (velocity +/- velocity_variance)
+ normalize(pos_w - center_w) * radial_velocity
+ inherit_velocity * emitter_velocity`, where `emitter_velocity` is
`(center_w(t1) - center_w(t0)) / dt` (zero on the first step).

Initial state: `lifetime = max(0.001, lifetime +/- variance)`,
`size_base = max(0, size +/- variance)`, `rotation = rad(rotation +/-
variance)`, `angular_velocity = rad(angular_velocity +/- variance)`,
`color_base = color`, `opacity_base = opacity`, `emissive_base = emissive`,
`mass`, `age = 0`, `previous_position = position`, `acceleration = 0`.
Systems keep the `*_base` values in private arrays; the ParticleBuffer
holds the modulated values (section 6).

System cap: if `alive == max_particles`, the spawn is dropped and counted
in `SystemStatistics::dropped`.

## 4. Forces (accelerations, m/s^2)

All forces are active only inside their own window. `falloff(d)` with
`radius r`: `none -> 1`; `linear -> max(0, 1 - d/r)`;
`inverse_square -> 1 / (1 + d*d)` (times the linear window when r > 0);
`smooth -> 1 - smoothstep(0, r, d)`. When `r == 0` every falloff is 1
except inverse_square.

| force_type | acceleration |
|---|---|
| gravity, directional | `normalize(direction) * strength` |
| wind | `(normalize(direction) * strength - v) / mass` (relaxes toward the wind velocity) |
| radial, repulsor | `normalize(p - position) * strength * falloff(|p - position|)` |
| attractor | `-normalize(p - position) * strength * falloff(d)` |
| vortex | axis `a = normalize(direction)`, `r = p - position`, `rp = r - a*dot(r,a)`, `normalize(cross(a, rp)) * strength * falloff(|rp|)` |
| turbulence | `evaluate_noise_vector(fbm gradient) * strength` with `frequency`, `octaves`, time `t1 * speed`, seed = node stream |
| curl_noise | `curl4(p, t1 * speed, seed, {octaves, 2, 0.5}) * strength` sampled at `p * frequency` |
| drag | `-v * strength` |
| buoyancy | `(0, 1, 0) * strength * temperature` |

A `noise` node connected to a force's `noise` port replaces the built-in
noise parameters (type, frequency, octaves, lacunarity, gain, amplitude,
speed, offset) for turbulence/curl.

## 5. Integration and collision

Semi-implicit Euler with the fixed `dt`:

```
v += a * dt
v *= max(0, 1 - drag * dt)          # particle_system.drag
previous_position = p
p += v * dt
rotation += angular_velocity * dt
age += dt (done in step 4a before spawning)
```

Collision radius `cr = collision_radius > 0 ? collision_radius : size*0.5`.
For each collider in order: compute signed distance `sd` from the particle
to the collider surface minus `cr` and the surface normal `n` pointing out:

* plane: `sd = dot(p - position, normal) - cr`
* sphere: `sd = |p - position| - radius - cr`, `n = normalize(p - position)`
* box: in collider local space (inverse world transform, `size` half
  extents), `sd = signed box distance - cr`, `n` = axis of least penetration
* capsule: distance to the segment of `height` along local Y minus radius
* mesh, sdf: unsupported in V1 (compiler warning W101; ignored)

If `sd < 0`: `p += n * (-sd)`; `vn = dot(v, n)`; if `vn < 0`:
`v = (v - n*vn) * (1 - friction) - n * vn * bounce`, where the material
terms combine both nodes: `bounce = sqrt(system.bounce * collider.bounce)`,
`friction = sqrt(system.friction * collider.friction)` (defaults give the
defaults; either side can zero it). `kill_on_collision` is the OR of both.
A **contact** is counted, and `on_collision` fires, only when the particle
was not already resting on that collider in the previous step or its
pre-response normal speed `-vn` exceeds 0.1 m/s (resting contacts do not
re-fire every step). If the particle is killed, `on_collision` fires first
and then `on_death`.

`physics: rigid` (Tier 2) falls back to this path in V1 with a compile
warning W102; rotation still integrates angular velocity.

## 6. Over-life modulation (written to the ParticleBuffer every step)

`u = age / lifetime` clamped to [0, 1].

```
size     = size_base * size_over_life(u)
color    = color_base * color_over_life(u)      (rgb)
opacity  = opacity_base * opacity_over_life(u)
emissive = emissive_base * emissive_over_life(u)
```

`custom0 = u` (normalized age, convenient for renderers), `custom1` = 0.

## 7. Analytic nodes

* **light**: position/direction from the world transform; `intensity *=
  1 + flicker_amplitude * n(t)` where `n(t)` is a hash-based value in
  [-1, 1] interpolated (smooth) between cells of `1/flicker_frequency`
  seconds; `color = temperature > 0 ? Color::from_temperature(temperature)
  * color : color`.
* **beam**: origin/target from `origin_node`/`target_node` world positions
  when connected, else the animatable params. Main polyline: `segments + 1`
  points on the straight line; interior points displaced perpendicular to
  the beam by `noise_amplitude * fbm(along * noise_frequency, jitter_key)`
  in two perpendicular directions, where `jitter_key = floor(t1 *
  jitter_rate)` (0 when jitter_rate = 0). Branches: `branching` candidates
  at interior points chosen by the beam RNG; each spawns with probability
  `branch_probability`, length `branch_length * |main|`, direction = main
  direction rotated by 25..60 degrees around a random axis, 4 segments,
  same displacement scheme, width 0.6x. `pulse_phase = pulse_speed > 0 ?
  fract(t1 * pulse_speed) : -1`; `pulse_frequency` > 0 modulates emissive by
  `0.75 + 0.25 * sin(2 pi f t1)`.
* **mesh** (visible=true): MeshInstanceState with the world transform.
* **decal**: opacity multiplied by `smoothstep(0, fade_in, t1 - start)` and
  `smoothstep(0, fade_out, end - t1)` when the window is bounded.
* **trail**: source position each step = world position of the source
  node (mesh/emitter/light/curve: curve uses its first point). A vertex is
  laid when the distance from the last vertex >= `min_vertex_distance` (the
  first vertex always). Vertex age increases; vertices older than `lifetime`
  are dropped from the front; at most `max_segments + 1` vertices. Per
  vertex: `normalized_age = age / lifetime`, `width = width_param *
  taper(normalized_age)`, `opacity = opacity_over_life(normalized_age)`,
  `color`, `emissive`, `u = cumulative distance * (1) + uv_scroll * t1`,
  position displaced by `noise_amplitude * fbm(distance * noise_frequency)`
  perpendicular to the tangent. `particle_system` sources: one ribbon per
  live particle (keyed by particle seed), capped at 512 ribbons; V1 may
  emit warning W103 and support only the first 512.
* **camera**: FrameState.camera from animated params.
* **post_effect**: PostEffectState inside its window.
* **volume**: VolumeState stub with density/temperature params (W104).
* **field**: no runtime output in V1.

## 8. Events

| trigger | fires |
|---|---|
| on_time | once when `t0 < time <= t1` (time events are processed after the systems, so their bursts spawn on the next step) |
| on_peak | once at the start of the `peak` phase if it exists, else at `duration/2` |
| on_spawn / on_death / on_collision (on_impact = alias) | per particle occurrence in `source` |
| on_distance | once per particle when `|p - spawn_position| >= distance` |

Each firing passes `probability` (particle events draw from
`Pcg32(derive_seed(particle_seed, event_seed))`, a pure function of the
particle and the event; time events use the event node stream) and
`max_triggers` (0 = unlimited, counted per event node). Action
per target emitter: burst of `count = event.burst_count > 0 ?
event.burst_count : target.burst_count > 0 ? target.burst_count : 1` at
`inherit_position ? trigger position : target's own position`, adding
`inherit_velocity * trigger velocity` to each spawned particle. Emitters
with `rate = 0` and `burst_times = []` spawn only through events.

## 9. Statistics

Per system: alive, spawned_total, died_total, peak_alive, capacity
(max_particles), dropped, collisions. Global: totals, events_fired, step
timings. Timings are the only non-deterministic values and are excluded
from `FrameState::hash()`.

## 10. Determinism test contract

`tests/sim`: simulate `examples/effects/fireball.json` for 60 steps twice
from fresh runtimes; the sequence of `FrameState::hash()` must be identical.
Changing `effect.seed` must change the hash. Simulating to `t` in one
`simulate_to` call must equal stepping manually.
