# aetherfx (Python)

The agent's side of the AetherFX boundary: a typed client for the engine's tool
API, an MCP server, optional AI reference analyzers and the non-AI evaluation
metrics.

| module | what it is |
|---|---|
| `aetherfx.jsonrpc` | newline-delimited JSON-RPC 2.0 over `aetherfx serve`; `SubprocessTransport`, `InMemoryTransport`, `AetherError` |
| `aetherfx.client` | `Client` - one typed wrapper per tool in [docs/AGENT_API.md](../docs/AGENT_API.md), plus dynamic access to anything new |
| `aetherfx.ead` | pydantic models for the Effect Analysis Document ([docs/EAD.md](../docs/EAD.md)); generates `schema/ead.schema.json` |
| `aetherfx.ai` | `ReferenceAnalyzer` adapters: `MockAnalyzer` (offline, default) and `ClaudeAnalyzer` (vision) |
| `aetherfx.evaluate` | coverage IoU, palette distance, luminance histogram, centroid offset, radial profile - no AI |
| `aetherfx.planner` | EAD -> graph tool calls -> a built effect |
| `aetherfx.mcp_server` | the curated MCP surface, console script `aetherfx-mcp` |

The engine is optional at import time: nothing here spawns a process until you
make a call, and the MCP server starts (and says so) even when the binary has
not been built yet.

## Install

Python >= 3.10.

```bash
cd python
/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"          # add ,claude for the Claude adapter:
.venv/bin/python -m pip install -e ".[dev,claude]"
```

Extras: `claude` pulls in the `anthropic` SDK, `dev` pulls in pytest.

## Finding the engine

`Client()` resolves the binary in this order:

1. the `binary=` argument
2. `$AETHERFX_BINARY`
3. `<repo>/build/bin/aetherfx`, then `./build/bin/aetherfx`, then `../build/bin/aetherfx`
4. `aetherfx` on `PATH`

```bash
export AETHERFX_BINARY=/path/to/build/bin/aetherfx
```

## Python usage

```python
from aetherfx import Client

with Client(output_dir="out") as engine:
    engine.create_effect(name="Fire AOE", duration=4.5, seed=7)
    engine.set_timeline_phase(name="peak", start=1.0, end=1.6)

    engine.create_layer(id="primary", name="Primary", role="primary")
    engine.create_particle_system(
        id="wall_ps", layer="primary",
        parameters={"max_particles": 6000, "lifetime": 0.7, "blend": "additive"},
    )
    engine.create_emitter(
        id="fire_wall", layer="primary",
        parameters={"shape": "ring", "radius": 3.0, "inner_radius": 2.5, "rate": 2200},
    )
    engine.connect_nodes(from_="wall_ps", to="fire_wall", port="particle")

    engine.simulate(time=1.6)
    frame = engine.render_frame(time=1.6, width=512, height=512)
    print(frame["path"], engine.inspect_graph()["diagnostics"])
```

Any tool is reachable even without a wrapper - `engine.call("some_new_tool", x=1)`
or `engine.some_new_tool(x=1)` - and `engine.tools()` returns the live
`tools/list` (cached).

Errors carry the engine's own diagnostic codes:

```python
from aetherfx import AetherError

try:
    engine.set_parameter(node_id="fire_wall", name="raet", value=10)
except AetherError as exc:
    print(exc.aether_code, exc.node, exc.param)   # E004 fire_wall raet
```

### Reference analysis and planning

```python
from aetherfx import Client, ead_to_effect
from aetherfx.ai import get_analyzer

analyzer = get_analyzer()                      # "mock" unless $AETHERFX_ANALYZER says otherwise
ead = analyzer.analyze_reference("refs/fire_aoe.png", prompt="top-down, stylised")

with Client() as engine:
    ead_to_effect(engine, ead)                 # create_effect -> phases -> layers -> nodes -> wiring
    frame = engine.render_frame(time=1.2)

verdict = analyzer.evaluate_render("refs/fire_aoe.png", frame["path"], ead)
for change in verdict.suggestions:
    print(change.node, change.parameter, change.current, "->", change.proposed, change.reason)
```

Inspect a plan before running it:

```python
from aetherfx import ead_to_tool_calls
for call in ead_to_tool_calls(ead):
    print(call.name, call.args)
```

### Evaluation without AI

```python
from aetherfx.evaluate import compare_images, image_stats

print(image_stats("render.png")["coverage"])
result = compare_images("refs/fire_aoe.png", "render.png")
print(result["score"], result["notes"])
```

Every formula is specified in the module docstrings; the C++ `compare_reference`
tool implements the same metrics and must produce the same numbers.

## Analyzer configuration

| variable | meaning | default |
|---|---|---|
| `AETHERFX_ANALYZER` | `mock` or `claude` | `mock` |
| `AETHERFX_CLAUDE_MODEL` | model id for `ClaudeAnalyzer` | `claude-sonnet-5` |
| `ANTHROPIC_API_KEY` | API key for `ClaudeAnalyzer` | - |

`MockAnalyzer` is deterministic and offline: it always returns the same fire-AOE
analysis, mirroring `examples/effects/fire_aoe.json`. `ClaudeAnalyzer` sends the
image plus the generated EAD JSON Schema and validates the reply with pydantic,
retrying once with the validation error fed back. Its API key comes from the
constructor or `ANTHROPIC_API_KEY` and from nowhere else.

```python
from aetherfx.ai import get_analyzer
analyzer = get_analyzer("claude", model="claude-opus-5")
```

## MCP server

`aetherfx-mcp` speaks MCP over stdio and exposes exactly the curated surface
from docs/AGENT_API.md, in that order, plus `analyze_reference` and
`plan_from_ead`. `render_frame` and `render_preview` return the JSON result and
the image, so the agent can see the frame it rendered.

```bash
aetherfx-mcp --binary /path/to/aetherfx --output-dir ./out --analyzer mock
```

All three flags are optional and fall back to the environment variables above.

### Claude Code

```bash
claude mcp add aetherfx \
  --env AETHERFX_BINARY=/path/to/VFX Engine/build/bin/aetherfx \
  -- /path/to/VFX Engine/python/.venv/bin/aetherfx-mcp --output-dir /path/to/VFX Engine/out
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aetherfx": {
      "command": "/path/to/VFX Engine/python/.venv/bin/aetherfx-mcp",
      "args": ["--output-dir", "/path/to/VFX Engine/out"],
      "env": {
        "AETHERFX_BINARY": "/path/to/VFX Engine/build/bin/aetherfx",
        "AETHERFX_ANALYZER": "mock"
      }
    }
  }
}
```

Use the venv's `aetherfx-mcp` (or `python -m aetherfx.mcp_server`) so the server
runs with its dependencies. If the engine binary is missing the server still
starts, logs one line to stderr and returns a clear error from engine calls.

## Regenerating the EAD schema

`schema/ead.schema.json` is generated from `aetherfx.ead`; a test fails if it
drifts.

```bash
.venv/bin/python -c "from aetherfx.ead import export_json_schema; print(export_json_schema())" > /dev/null
# or:
.venv/bin/python -m aetherfx.ead
```

## Tests

```bash
.venv/bin/python -m pytest -q          # unit tests; integration tests skip themselves
.venv/bin/python -m pytest -v tests/test_evaluate.py
```

`tests/test_integration.py` runs against the real binary and is skipped unless
`build/bin/aetherfx` (or `$AETHERFX_BINARY`) exists **and** answers `ping` - a
placeholder binary skips exactly like a missing one.
