# Roadmap

Milestones are vertical slices; each one keeps the agent loop working end to
end before adding depth.

## M0 Foundation (done 2026-09-17, CPU only)

* Vocabulary, typed graph, JSON IO with versioning, validator, spec registry
  exporting `schema/*.json`.
* Compiler with tier selection and resource baking.
* Deterministic CPU particle runtime (Tier 1) with emitter shapes, forces
  (all vocabulary force types), plane/sphere/box colliders, lifetime
  modulation, events (on_time/on_spawn/on_death/on_collision), analytic
  Tier 0 nodes (beam, trail, decal, light, mesh, curve), volume stub.
* Software reference renderer: HDR linear, additive/alpha/premultiplied,
  soft particles, billboards, stretched billboards, mesh instances, ribbons,
  beams, decals, point lights on ground, bloom, filmic tonemap, PNG/EXR.
* Procedural textures (noise, gradients, rings, cracks, voronoi, levels,
  math, dissolve, normal_from_height, channel_pack), mesh primitives.
* Agent tool API, CLI (`tool`, `serve`, `run`, `validate`, `describe`),
  Python client, MCP server, EAD schema, analyzer interface with mock and
  Claude adapters, metric-based reference comparison.
* Flipbook / frame sequence export.
* Tests: unit, determinism (hash equality across runs), golden image hashes,
  example effects load/validate/compile/simulate/render, JSON-RPC round trip.

## M1 GPU

* wgpu-native device bootstrap (headless compute smoke test) -> GPU particle
  runtime matching the CPU reference within tolerance -> GPU renderer with
  the same IRenderer interface. Slang for shader authoring, WGSL/SPIR-V/MSL
  cross-compilation. Dear ImGui editor with live preview.

## M2 Volumetrics and physics

* Done (M1.5): procedural raymarched volumes - `volume` with `mode: procedural`,
  the same closed-form density field in the reference renderer and the GPU
  viewer (docs/VOLUMES.md).
* OpenVDB/NanoVDB grids, coarse Eulerian smoke/fire (density, temperature,
  fuel, buoyancy, vorticity confinement) behind `mode: simulation`, feeding the
  raymarcher that already exists.
* Jolt rigid debris behind the physics abstraction (Tier 2).

## M3 Reference reconstruction

* Segmentation adapters (open-weight models), EAD -> graph planner, vision
  evaluation loop with change suggestions, turntable/contact sheet review.

## M4 Export

* Flipbook + material manifest, mesh optimization (meshoptimizer), USD and
  MaterialX interchange, Niagara / Unity VFX Graph description exporters.
