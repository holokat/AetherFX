# AetherFX

AI-native, open-source authoring system for real-time game VFX. The agent is
a first-class author: it builds effects from a small, strongly typed
vocabulary, simulates and renders them deterministically, looks at the
result, and iterates.

```
reference / prompt -> analysis (EAD) -> VFX graph -> validate -> compile
    -> simulate -> render -> evaluate -> modify graph -> ... -> export
```

* `docs/ARCHITECTURE.md` - the contract every module follows
* `docs/VOCABULARY.md` - node types and parameters
* `docs/AGENT_API.md` - the tool API (CLI / JSON-RPC / Python / MCP)
* `docs/ENGINE_INTEGRATION.md` - the C ABI for game engines (libaetherfx)
* `docs/EAD.md` - the Effect Analysis Document
* `docs/DEPENDENCIES.md` - every dependency, version and license
* `docs/ROADMAP.md`

## Status (M0 foundation, 2026-09-17)

Working end to end on CPU: typed vocabulary (18 node types, 246 parameters),
JSON graph with validation (E001-E020, W001-W006), compiler with tier
selection and resource baking, deterministic particle runtime (forces,
collisions, events, analytic beams/trails/decals/lights), software HDR
renderer (soft particles, bloom, post), procedural textures, 65-tool agent
API over JSON-RPC, CLI, Python client, MCP server, metric-based reference
comparison, flipbook/frames/video export. GPU (wgpu-native) is bootstrapped
but not yet used for simulation or rendering. Volumes (Tier 3) are procedural
raymarched density fields on both the CPU renderer and the GPU viewer
(docs/VOLUMES.md); the fluid-solver mode and rigid physics (Tier 2) are stubs
with compile warnings. See docs/ROADMAP.md.

## Studio

`aetherfx-studio` is a local web app (default http://127.0.0.1:8770) that
spawns the engine itself. It is a library and a generator: the Library lists
the built-in effects (protected, never overwritten) and your own - Fireball,
Fire AOE, Ice AOE, Shadow AOE, Lightning Strike and Void Nebula (a procedural
raymarched volume, see docs/VOLUMES.md), in `examples/effects/`; clicking one
opens a working copy that auto-plays, every parameter is editable live, the
camera orbits/zooms with the mouse, stage controls set ground/background/bloom,
Random mutates values for exploration, and "Save as" adds a new library entry.
The Generate box builds or modifies effects from a text prompt. Generation is done by an AI agent driving the same tool API and
looking at its own renders; the backend is picked automatically: Anthropic
API key, a logged-in Claude Code (Agent SDK), or a worker attached from an
interactive Claude Code session (`aetherfx.studio.worker_cli`).

```bash
python/.venv/bin/aetherfx-studio --port 8770 --output-dir out/studio
```

## Build

```bash
cmake --preset default && cmake --build --preset default && ctest --preset default
```

The build also produces `libaetherfx`, the C ABI game engines link against
(`build/src/capi/libaetherfx.dylib|.so|.dll`, plus `libaetherfx_static.a` for
static linking). It exposes the same deterministic simulation the studio uses -
load, compile, step, read the per-frame particle/light/beam/trail/decal/mesh
buffers - behind one C99 header, `src/capi/include/aetherfx/aetherfx.h`, with
`aetherfx_*` as its only exported symbols. The engine renders; the library
simulates. See `docs/ENGINE_INTEGRATION.md` for Unreal, Unity and Godot.

## Quick start

```bash
./build/bin/aetherfx validate examples/effects/fireball.json
./build/bin/aetherfx run examples/effects/fire_aoe.json --out out/fire_aoe --fps 24 --video
./build/bin/aetherfx render examples/effects/lightning_strike.json --time 0.05 --out out/bolt.png
./build/bin/aetherfx export examples/effects/fireball.json --format flipbook --out out/fireball_flipbook.png
./build/bin/aetherfx tools            # list the 65 agent tools
./build/bin/aetherfx serve            # JSON-RPC 2.0 over stdio (used by the Python client and MCP server)
```

## Bringing your own flipbooks

Fire, smoke and explosion sprites read best as real flipbooks. Point a texture
node at an image file and give it the sheet's grid; the compiler loads it and
unrolls the grid into the engine's animated-sprite layout, so `frames` becomes
`columns * rows` and the sprite plays over the particle's life (or at
`sprite_fps`).

```json
{"id": "tex_fire", "type": "texture",
 "parameters": {"source": "file", "path": "../textures/fire_8x4.png",
                "columns": 8, "rows": 4}}
```

`path` is absolute, or relative to the directory of the effect document - keep
sheets next to the effects, for example in `examples/textures/`. PNG/JPG/TGA/BMP
are read as sRGB and decoded to linear, `.exr` is read as linear HDR. Add
`width`/`height` only when you want the frames resampled. If you would rather
generate the sheet, the `flame` texture op bakes a seamless, animated fire
flipbook procedurally (see `examples/effects/fire_aoe.json`).

Python / MCP:

```bash
cd python && python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev,claude]'
AETHERFX_BINARY=../build/bin/aetherfx .venv/bin/pytest -q
.venv/bin/aetherfx-mcp --binary ../build/bin/aetherfx --output-dir ../out   # MCP server (stdio)
```

MCP configuration for Claude Code / Claude Desktop is in `python/README.md`.
The `AETHERFX_ANALYZER` environment variable selects the reference-analysis
adapter (`mock` by default, `claude` with `ANTHROPIC_API_KEY`).

License: Apache-2.0 (see LICENSE). Third-party licenses in docs/DEPENDENCIES.md.
