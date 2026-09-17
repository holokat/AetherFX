# Engine backlog from effect authoring (2026-09-17)

Gaps reported by the agents that authored Fire AOE, Fire Bolt, Lightning AOE, Holy AOE. Each
entry names the feature that was wanted and the workaround used, so the library effects can be
simplified once the feature lands.

## Renderer (GPU viewer) — in progress in the viewer-fidelity task
- DONE (3ade142): per-particle flipbook frames (CPU parity; the stream carries `age` seconds),
  `temperature_gradient` on sprites and mesh particles, tiling ribbon UVs with `uv_scroll`.
- `rotation` / `rotation_variance` are ignored for `stretched_billboard` in BOTH renderers (the
  velocity basis overwrites the roll); a spinning stretched sprite needs `render_mode: billboard`.
- The C API `aetherfx_material` struct stops at `noise_texture`: `temperature_gradient`, `uv_scroll`,
  `uv_rotate`, `gradient_texture` and `double_sided` never reach engine bridges (the studio viewer
  tops materials up from /api/effect; mesh particles are always double sided). Extend the struct.
- The CPU renderer's `draw_trail_segment` never samples a texture (ignores `u`, `twist`, and the
  temperature gradient); the viewer is ahead of the reference here.
- A trail header field for the tiling rate (`min_vertex_distance` or explicit) is missing; ribbons
  tile once per metre.
- Mesh particles ignore per-particle opacity (`opacity_over_life` does nothing; they pop).
  Workaround: `size_over_life`.
- Mesh fresnel intensity floored at `max(emissive_intensity, 0.35) * 2`, added after tone mapping;
  a subtle rim is impossible.
- Standalone `mesh` nodes and mesh particles have no texture map (base/emissive colour only).
- `material.uv_scroll` / `uv_rotate` not implemented in either renderer.
- Beam halo/core ratio fixed (halo `width*2.6` at alpha 0.32, core `width*0.55` at `emissive*1.8`);
  no width taper; the core draws at alpha 1.0 regardless of `emissive` (colour alpha is the only
  transparency handle).
- Decals are always flattened to the ground (only `rotation.y` survives): no standing quads.
- Procedural `disc` volume cost ~10 fps in Lightning AOE for little visual gain at `march_steps`
  10-48; volumes draw before all particles (coarse ordering).

- Mesh `fresnel_power` lights whole flat facets at grazing angles (no edge-only rim); the orb's
  mesh-instance fresnel is not implemented at all. Workaround: additive halo + cross-flare sprite.
- Trails have no animatable opacity; fade with `width` / `emissive` tracks.

- Standalone `mesh` node instances use a plain MeshStandardMaterial: `fresnel_power`, transmission,
  `dissolve`/`erosion` are ignored (only mesh *particles* get the full path).
- `trail` ribbons have no cross-width noise; wide ribbons read as flat hard-edged wedges.

## Runtime / vocabulary
- Particles are world-space only; nothing can rigidly follow a moving parent. Wanted: an emitter
  flag keeping spawned particles in the emitter's local frame (hero clusters are parented mesh
  nodes with keyframed TRS instead).
- A `mesh` node always renders variant 0. Wanted: a `variant` index parameter on `mesh`.
- `ring` emitters are XZ-only; a camera-facing ring needs `rotation: [90, 0, 0]`.
- `metadata.render_settings` cannot carry a camera (the studio drops it); framing must live in
  the `camera` node, and the studio viewer frames 1.45x wider than that camera.
- No tangential initial velocity on emitters (`direction` is a fixed local vector, zero = radial),
  so orbiting particles need parent chains with keyframed rotation. Wanted: `tangential_velocity`.
- `emitter.inherit_velocity` clamped to [0, 1]: no backward inheritance for trailing flames.
  Workaround: backward emitter `direction` + a windowed directional force.
- `mesh.radius` not animatable; animate `scale` on a child of a motion rig instead.
- Beams are analytic with one window: "dozens of arcs switching on and off" needs 44 beam nodes
  with step tracks on origin/target/width/emissive. Wanted: an arc emitter (N short-lived beams per
  second between sampled points) and beam endpoints bound to live particles (`origin_node` /
  `target_node` on a particle_system).
- Emitter with `shape: volume` samples the node's `bounds` box, not the procedural shape.

## Texture graph
- `distort` reads r/g of its `by` input and every noise op is grayscale, so displacement is
  diagonal-only unless two fbms are `channel_pack`ed; fbm's distribution around 0.5 is too narrow
  to displace without `levels`.
- All ops are centre-origin: no way to offset a motif inside a texture.
- `flame` bakes one smooth teardrop per frame (the "fake triangles" complaint). `fire_sim`
  (2D flame simulation flipbook) is being added.

## Schema / tests / studio
- DONE: `schema/effect.schema.json` now has the top-level `description`, and the loader keeps it
  instead of dropping it on a round trip.
- Controls (docs/CONTROLS.md) follow-ups: the shipped examples have none of their own, so the
  studio generates a default set per working copy - the library effects should ship 4-8 authored,
  named controls each. The Unreal plugin should surface them as instance parameters
  (docs/UNREAL.md section 9). `generate_default_controls` binds per layer only, so a material two
  layers share is reachable from Global alone.
- `tests/sim/analytic_test.cpp` pins Fire AOE camera values (position.y, target.y, fov) and
  `tests/compiler/compile_test.cpp` pins `mat_rock.base_color.r`; both should assert on tracks and
  structure, not on art values.
- Studio: one shared active effect per server; a second client creating/loading an effect
  redirects a worker's `create_node` into the wrong document. Mitigation in use: every tool already
  accepts an explicit `effect_id`; the worker passes it on every call. Wanted: make it mandatory for
  worker sessions.
- Studio: after a stream reconnect the viewport is empty unless playback was running (no `open`
  is re-sent).
- `app.js` declares `hexToLinear` twice with different signatures; the later wins.
- `tools/gl_capture.py` selects effects by clicking a Library row, so hidden built-ins (Fireball)
  cannot be captured by name.
- The studio viewport is roughly square in the default layout; briefs assuming 16:10 framing put
  the action outside the frame.

## CPU reference renderer
- Big sprites drawn from an animated flipbook show hard straight-edged triangles
  (`draw_particle_quad` animated-frame path; reproduces with `flame` and `fire_sim`, disappears with
  `frames: 1`). Likely part of the "fake triangles" complaint on CPU renders and flipbook exports.
- `rotation_variance` appears to have no effect on billboard particles (renders byte-identical).

## Authoring pitfalls worth fixing in the engine or the validator
- Texture ops carry alpha in all four channels and `erosion` lifts alpha to 1 everywhere, so a
  final `levels` with `in_low` near 0 renders sprites as solid opaque quads. Wanted: a validator
  warning when a sprite texture has no true black point, or alpha derived from luminance.
- `volume.softness` is edge softness: low values give a hard pill silhouette. Document it and
  consider renaming or adding a separate density-contrast parameter.
- The viewer multiplies light intensity by 3.2 with true inverse-square falloff while the docs
  describe `intensity / (d^2 + 1)`; a point light inside a mesh blows it out and `shading: lit`
  leaves a specular hotspot at roughness 0.55. Align the docs and the two renderers.

## Flipbook orientation (found by the Fire AOE second pass)
- `fire_sim` and `flame` sheets render upside down in BOTH renderers relative to docs/VOCABULARY.md
  (the doc says root at v = 0 draws upright; three.js `flipY` and the CPU path both put image row 0
  at the top of the sprite, so the fuel slab lands above the tongues). Fire AOE works around it by
  mirroring V in the texture graph (`half` / `vgr` / `byv` / `flip` nodes: `distort` by a packed
  (0.5, 1 - v) field with amount 2). Fix `fire_sim` to emit upright (it is new, nothing else depends on
  it), document `flame` as is, then delete the four mirror nodes from fire_aoe.json.
- `material.opacity` (not `particle_system.opacity`) drives the viewer shader's `uOpacity`;
  `soft_particle_distance: 0.05` acts as a hard depth clip against the ground.
- Library effects should each get 4-8 hand-named controls (the studio generates defaults per working
  copy until then).
