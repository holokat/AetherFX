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
* `docs/EAD.md` - the Effect Analysis Document
* `docs/DEPENDENCIES.md` - every dependency, version and license
* `docs/ROADMAP.md`

## Build

```bash
cmake --preset default && cmake --build --preset default && ctest --preset default
```

## Quick start

```bash
./build/bin/aetherfx validate examples/effects/fireball.json
./build/bin/aetherfx run examples/effects/fireball.json --out out/fireball --fps 24
./build/bin/aetherfx serve            # JSON-RPC 2.0 over stdio
```

Python / MCP:

```bash
cd python && python3.13 -m pip install -e '.[dev]'
aetherfx-mcp --binary ../build/bin/aetherfx
```

License: Apache-2.0 (see LICENSE). Third-party licenses in docs/DEPENDENCIES.md.
