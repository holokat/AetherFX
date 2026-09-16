# AetherFX Architecture

AetherFX is an AI-native, open-source authoring system for real-time game VFX.
The defining architectural rule is that **the agent is a first-class author**:
an AI agent constructs, inspects, modifies, simulates, renders, evaluates and
exports effects through a small, strongly typed, machine-readable vocabulary.

This document is the contract every module is built against. If code and this
document disagree, fix one of them in the same change.

## 1. The boundary

```
AI agent (any model, or a human, or a script)
   |  structured tool calls (JSON in / JSON out)
   v
Agent Tool API            src/tools      ToolRegistry + Session (undo/redo)
   |
   v
VFX Graph (Effect)        src/core       typed nodes, layers, timeline, JSON IO
   |
   v
Validator                 src/core       Diagnostics (errors/warnings, per node/param)
   |
   v
Compiler                  src/compiler   Effect -> CompiledEffect (tiers, resources, plan)
   |
   v
Deterministic Runtime     src/sim        IRuntime: reset/step -> FrameState
   |
   v
Renderer                  src/render     IRenderer: FrameState -> HDR Image
   |
   v
Preview frames / flipbook / stats -> back to the agent (vision evaluation)
```

Hard rules:

* The AI produces **descriptions** (graph edits). The engine executes them.
  No generated C++ per effect, no LLM access to GPU memory, no bespoke shader
  per effect. All behaviour is data on a fixed vocabulary.
* Everything an agent can do is a **tool** in the registry. The CLI, the
  Python client, the MCP server and the future GUI all call the same registry.
  There is no second code path.
* The runtime is **fully functional without any AI**. AI adapters live in
  `python/aetherfx/ai` behind an interface and are optional.
* **Determinism**: same effect JSON + same seed + same fixed timestep + same
  runtime backend => bit-identical FrameState sequence. See section 6.
* The graph is **ours**. USD/MaterialX are interchange formats, never the
  runtime representation.

## 2. Modules and dependency direction

```
core        <- (nothing but nlohmann/json)
procedural  <- core                 noise, procedural textures, mesh primitives
compiler    <- core, procedural     tier selection, resource baking, plan
sim         <- core, compiler       CPU reference runtime (GPU runtime later)
render      <- core                 software reference renderer, image IO
tools       <- core, compiler, sim, render, procedural   ToolRegistry, Session
cli         <- tools                `aetherfx` executable, JSON-RPC stdio server
gpu         <- core                 wgpu-native bootstrap (optional, AETHER_WITH_GPU)
python/     -> talks JSON-RPC to the cli; MCP server; AI adapters; evaluation
```

Arrows point at dependencies. A module may only include headers from itself
and modules to its right in the `<-` list. Every module is a CMake library
`aether_<name>` with public headers under `src/<name>/include/aether/<name>/`.

## 3. Data model (src/core)

* `Effect` - name, `schema_version`, `duration`, `seed`, `Timeline`,
  `layers`, `nodes`, `metadata`.
* `Node` - `id`, `type` (enum, one of the vocabulary), `version`, `enabled`,
  optional `seed`, optional `parent` (spatial parent node), optional `layer`,
  `parameters` (name -> `Parameter`), `inputs` (port -> list of node refs),
  `metadata`. Outputs are declared by the node's spec, not stored.
* `Parameter` - a constant `Value` plus an optional keyframe track over
  effect time. `Value` is a variant of bool/int/float/vec2/vec3/vec4/color/
  string/curve/gradient/string list/float list/vec3 list/json.
* `Curve` and `Gradient` are value types used for over-lifetime modulation
  (domain t in [0,1]). Keyframe tracks are for effect-time animation
  (domain seconds). These are different things and must not be conflated.
* `Layer` - semantic group (`telegraph`, `ignition`, `primary`, `secondary`,
  `interaction`, `aftermath`, `custom`). Nodes reference their layer by id.
* `Timeline` - named phases with absolute `[start, end]` in seconds:
  `anticipation`, `activation`, `peak`, `sustain`, `decay` (any subset).
  Time-bound nodes may declare `phase` to bind their window to a phase.
* `SpecRegistry` is the single source of truth for the vocabulary: per node
  type, the parameter specs (type, default, range, enum values, animatable,
  description) and the input/output ports (name, accepted node types, multi).
  `schema/vocabulary.json` and `schema/effect.schema.json` are generated from
  it and a test asserts they are up to date.
* References between nodes are strings: `"node_id"` or `"node_id.port"`.

See `docs/VOCABULARY.md` for the full node and parameter list.

## 4. Compiler (src/compiler)

`compile(Effect, CompileOptions) -> CompiledEffect`:

1. validate (fail on errors; warnings pass through)
2. resolve `phase` bindings into absolute start/end
3. resolve references, build the execution DAG, topological order
4. select a **tier** per node, the cheapest capable implementation:
   * Tier 0 Analytic: beam, trail, decal, light, mesh, curve, camera, post
   * Tier 1 Particles: emitter + particle_system (CPU now, GPU later)
   * Tier 2 Physics: particle_system with `physics: rigid` (Jolt later; V1
     falls back to Tier 1 with collision and emits a warning)
   * Tier 3 Volumetric: volume (V1: stub backend, statistics only, warning)
5. bake resources: procedural textures -> `Image`, mesh primitives ->
   `MeshData`, materials -> `MaterialDesc`, into a `ResourceSet`
6. emit `Diagnostics` (what was downgraded, what is unsupported, budgets)

The compiler never mutates the source `Effect`.

## 5. Runtime (src/sim)

`IRuntime` - `reset()`, `step(dt)`, `simulate_to(t)`, `state()`,
`statistics()`. The CPU runtime is the reference implementation; a GPU
runtime must produce the same FrameState within floating point tolerance
(tests compare against the CPU runtime).

`FrameState` is the only thing the renderer sees: SoA particle buffers per
system, light states, beam polylines, trail ribbons, decal states, mesh
instances, volume stubs, plus time and frame index. It carries resource ids
(material, texture, mesh) that index the `ResourceSet`.

Fixed timestep (default 1/60 s). `simulate_to(t)` steps in fixed increments
and never interpolates.

## 6. Determinism rules

* RNG is PCG32. Every node gets a stream seeded by
  `derive_seed(effect.seed, node.id, node.seed)`. Every particle gets a seed
  derived from its node stream and spawn index. No global RNG, ever.
* Fixed timestep. No wall-clock reads in the simulation.
* No unordered-container iteration that affects results. Node order is the
  compiled topological order, ties broken by node id.
* Multithreading (later) is only across independent systems; within a
  system, particle order is spawn order.
* Floating point: single precision in particle state, `double` for time.
  Do not use fast-math. Keep summation order fixed.

## 7. Renderer (src/render)

`IRenderer::render(FrameState, ResourceSet, CameraDesc, RenderSettings)
-> Image` (linear HDR float RGBA). The software renderer is the reference and
is what CI uses. It must support: HDR linear pipeline, additive / alpha /
premultiplied blending, soft particles (depth fade against a depth buffer),
billboards and velocity-stretched billboards, sprite textures, mesh
instances (flat/simple lit), ribbons/trails, beams, point lights lighting the
ground plane, bloom, exposure, filmic tonemap on output, optional ground
plane and grid for spatial reference. Output writers: PNG (sRGB, tonemapped)
and EXR (linear). The GPU renderer (wgpu-native + Slang) targets the same
interface.

## 8. Agent Tool API (src/tools)

`ToolRegistry` holds `ToolSpec {name, description, input_schema}` and a
handler `json(Session&, json)`. `Session` owns loaded effects, the active
effect, an undo/redo journal (whole-document JSON snapshots; documents are
small), compiled/simulation caches, and the output directory. Errors are
thrown as `aether::Error` and converted to structured JSON errors at the
boundary. The tool list is in `docs/AGENT_API.md`.

Transports: `aetherfx tool <name> '<json>'`, `aetherfx serve` (JSON-RPC 2.0,
newline-delimited, over stdio), Python `aetherfx.Client`, MCP server
`aetherfx.mcp_server`.

## 9. AI layer (python/aetherfx/ai)

`ReferenceAnalyzer` interface: `analyze_reference`, `segment_reference`,
`describe_effect`, `evaluate_render`. Adapters: `MockAnalyzer` (tests),
`ClaudeAnalyzer` (hosted multimodal), room for local models. Output is an
**Effect Analysis Document** (`schema/ead.schema.json`, see `docs/EAD.md`)
which the planner turns into graph edits. Inferred motion is always marked
inferred. `evaluate_render` also has a non-AI baseline (`aetherfx.evaluate`)
computing coverage IoU, palette distance, luminance histogram distance,
radial energy profile and centroid offset between a reference and a render.

## 10. Versioning

`schema_version` is semver. The loader migrates older documents forward
(`migrate_effect_json`). Node `version` lets a node type evolve its
parameters. Never silently reinterpret an old field.

## 11. Coding conventions

* C++20, namespace `aether`, `snake_case` functions/vars, `PascalCase`
  types, `UPPER_SNAKE` only for macros (avoid macros).
* Headers under `include/aether/<module>/`, one public header per concept.
* `#pragma once`. No global mutable state.
* Exceptions: `aether::Error` for invalid input at API boundaries;
  `Diagnostics` for validation/compile reporting (never throw for warnings).
* Tests: Catch2 v3, one test file per concept, `tests/<module>_*.cpp`.
  Determinism and golden-hash tests are mandatory for sim and render.
* No dependency without an entry in `docs/DEPENDENCIES.md`.
