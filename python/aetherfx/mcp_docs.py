"""Documentation for agents that connect to AetherFX over MCP.

Three audiences, one source of truth:

* an MCP client gets the guides as **resources** (``aetherfx://docs/...``) and task **prompts**
  (wired up in :mod:`aetherfx.mcp_server`);
* an agent or a person with only a URL gets plain markdown and JSON over HTTP
  (``/llms.txt``, ``/mcp/docs.md``, ``/mcp/tools.json``);
* a browser that opens the MCP endpoint gets a readable page instead of a protocol error
  (:func:`docs_html`, :func:`wants_docs_page`).

Everything is generated from the live tool definitions and the same authoring guide the studio's
own generator follows, so it cannot drift from what the server actually does.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

#: Repository ``docs/`` when running from a checkout (absent in a bare install).
_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

WORKFLOW = """\
1. `describe_vocabulary` once: the exact node types, parameter names, ranges and texture ops.
2. `create_effect`, then `create_layer` / `create_node` / `connect_nodes` / `set_parameter` to build the graph.
   Every mutating tool returns diagnostics: read them and fix errors before moving on.
3. `validate_effect`, then `render_frame` (or a contact sheet) and LOOK at the image. Iterate like a VFX artist:
   silhouette, readability, colour, timing. At least three passes.
4. Give the effect named style controls (`generate_default_controls`, `add_control`), then `save_effect`.
   Built-in library effects are read-only: work on your own document and save it under a new name.
Pass `effect_id` on every call when several clients share one studio."""


@dataclass(frozen=True)
class DocResource:
    """One guide exposed as an MCP resource and as markdown over HTTP."""

    slug: str
    title: str
    description: str
    load: Callable[[], str]

    @property
    def uri(self) -> str:
        return f"aetherfx://docs/{self.slug}"


def _read_doc(name: str, fallback: str) -> Callable[[], str]:
    def load() -> str:
        path = _DOCS_DIR / name
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return fallback
    return load


def _authoring_guide() -> str:
    try:
        from .studio import authoring_guide  # noqa: PLC0415 - constants only, no web stack import
    except Exception as exc:  # noqa: BLE001
        return f"(authoring guide unavailable: {exc})"
    parts = ["# AetherFX authoring guide\n", authoring_guide.CONVENTIONS.strip()]
    recipes = getattr(authoring_guide, "RECIPES", "")
    if recipes and recipes.strip() not in parts[1]:
        parts.append(recipes.strip())
    reference = getattr(authoring_guide, "REFERENCE_INSTRUCTIONS", "")
    if reference:
        parts.append("## Working from a reference image\n\n" + reference.strip())
    return "\n\n".join(parts) + "\n"


def doc_resources() -> list[DocResource]:
    """The guides, most useful first."""
    missing = "(this guide ships with the source checkout; see the repository's docs/ folder)"
    return [
        DocResource("getting-started", "Getting started", "How to connect, the authoring workflow and the tool catalogue.",
                    lambda: getting_started_markdown()),
        DocResource("authoring-guide", "Authoring guide",
                    "Conventions, proven recipes and the house style every effect must follow.", _authoring_guide),
        DocResource("vocabulary", "Vocabulary", "Node types, parameters and texture-graph ops.",
                    _read_doc("VOCABULARY.md", missing)),
        DocResource("controls", "Effect controls", "Named style sliders stored in the effect and applied at compile time.",
                    _read_doc("CONTROLS.md", missing)),
        DocResource("volumes", "Procedural volumes", "The raymarched volume node.", _read_doc("VOLUMES.md", missing)),
        DocResource("runtime", "Runtime semantics", "Determinism, timing, particles, beams, trails.",
                    _read_doc("RUNTIME.md", missing)),
        DocResource("agent-api", "Agent tool API", "Every engine tool with arguments and results.",
                    _read_doc("AGENT_API.md", missing)),
    ]


def find_resource(uri: str) -> DocResource | None:
    text = str(uri).rstrip("/")
    for resource in doc_resources():
        if resource.uri == text:
            return resource
    return None


# ---------------------------------------------------------------------------------------------
# tool catalogue
# ---------------------------------------------------------------------------------------------

def tool_records(tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Plain dictionaries from MCP ``Tool`` objects (or already-plain mappings)."""
    records = []
    for tool in tools:
        if isinstance(tool, dict):
            name, description = tool.get("name"), tool.get("description")
            schema = tool.get("input_schema") or tool.get("inputSchema") or {}
        else:
            name, description = getattr(tool, "name", None), getattr(tool, "description", None)
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
        if not name:
            continue
        records.append({"name": str(name), "description": str(description or "").strip(), "input_schema": schema})
    return records


def _arguments(schema: dict[str, Any]) -> list[tuple[str, str, bool, str]]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    rows = []
    for name, spec in (properties or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        kind = spec.get("type") or ("enum" if "enum" in spec else "any")
        if isinstance(kind, list):
            kind = " | ".join(str(k) for k in kind)
        if "enum" in spec:
            kind = f"{kind}: " + ", ".join(str(v) for v in spec["enum"][:8])
        rows.append((str(name), str(kind), name in required, str(spec.get("description") or "").strip()))
    return rows


def tools_markdown(tools: Iterable[Any]) -> str:
    lines = []
    for record in tool_records(tools):
        lines.append(f"### `{record['name']}`\n")
        if record["description"]:
            lines.append(record["description"] + "\n")
        rows = _arguments(record["input_schema"])
        if rows:
            lines.append("| argument | type | required | description |\n|---|---|---|---|")
            for name, kind, required, description in rows:
                lines.append(f"| `{name}` | {kind} | {'yes' if required else 'no'} | {description} |")
            lines.append("")
    return "\n".join(lines)


def tools_json(tools: Iterable[Any], endpoint: str | None = None) -> str:
    payload = {"server": "aetherfx", "endpoint": endpoint, "tools": tool_records(tools),
               "resources": [{"uri": r.uri, "title": r.title, "description": r.description} for r in doc_resources()],
               "prompts": [{"name": p["name"], "description": p["description"]} for p in PROMPTS]}
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------------------------

def connect_markdown(endpoint: str) -> str:
    return f"""\
## Connect

The MCP endpoint is `{endpoint}` (streamable HTTP). A stdio server is available as `aetherfx-mcp`.

**Claude Code**

```bash
claude mcp add --transport http aetherfx {endpoint}
```

or in a project `.mcp.json`:

```json
{{"mcpServers": {{"aetherfx": {{"type": "http", "url": "{endpoint}"}}}}}}
```

**Claude Desktop / Cursor / other MCP clients** (JSON config)

```json
{{"mcpServers": {{"aetherfx": {{"url": "{endpoint}"}}}}}}
```

**Codex CLI** (`~/.codex/config.toml`), over stdio:

```toml
[mcp_servers.aetherfx]
command = "aetherfx-mcp"
```

When the client is attached to a running studio, everything the agent builds appears live in the studio viewport.
"""


def getting_started_markdown(endpoint: str = "http://127.0.0.1:8770/mcp", tools: Iterable[Any] | None = None) -> str:
    resources = "\n".join(f"- `{r.uri}`: {r.description}" for r in doc_resources())
    prompts = "\n".join(f"- `{p['name']}`: {p['description']}" for p in PROMPTS)
    out = [
        "# AetherFX for agents\n",
        "AetherFX is a game-VFX authoring engine built to be driven by an AI agent. An effect is a graph of typed "
        "nodes (emitters, particle systems, forces, beams, trails, decals, lights, volumes, procedural textures and "
        "meshes) in one deterministic JSON document. You never write shaders: you call tools, look at the renders "
        "the tools return, and iterate.\n",
        connect_markdown(endpoint),
        "## Workflow\n\n" + WORKFLOW + "\n",
        "## Guides (MCP resources)\n\n" + resources + "\n\nThe same guides are served as markdown at "
        "`/mcp/docs/<slug>.md`, and the whole set at `/mcp/docs.md`.\n",
        "## Prompts\n\n" + prompts + "\n",
    ]
    if tools is not None:
        out.append("## Tools\n\n" + tools_markdown(tools))
    return "\n".join(out)


def full_markdown(endpoint: str, tools: Iterable[Any]) -> str:
    tools = list(tools)
    sections = [getting_started_markdown(endpoint, tools)]
    for resource in doc_resources():
        if resource.slug == "getting-started":
            continue
        sections.append(f"\n\n---\n\n<!-- {resource.uri} -->\n\n" + resource.load())
    return "".join(sections)


def llms_txt(base_url: str, endpoint: str) -> str:
    """The llms.txt index: what this server is and where the machine-readable docs live."""
    lines = [
        "# AetherFX",
        "",
        "> AI-native game VFX authoring. Agents build effects by calling tools over the Model Context Protocol; "
        "the engine validates, simulates and renders deterministically.",
        "",
        f"MCP endpoint (streamable HTTP): {endpoint}",
        "",
        "## Docs",
        "",
        f"- [Getting started]({base_url}/mcp/docs/getting-started.md): connect, workflow, tool catalogue",
    ]
    for resource in doc_resources():
        if resource.slug != "getting-started":
            lines.append(f"- [{resource.title}]({base_url}/mcp/docs/{resource.slug}.md): {resource.description}")
    lines += [
        f"- [Everything in one file]({base_url}/mcp/docs.md)",
        f"- [Tool catalogue as JSON]({base_url}/mcp/tools.json)",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------------------------

PROMPTS: list[dict[str, Any]] = [
    {"name": "create_effect", "description": "Author a new effect from a text description.",
     "arguments": [{"name": "description", "description": "What the effect should look like and do.", "required": True},
                   {"name": "duration", "description": "Target duration in seconds (optional).", "required": False}]},
    {"name": "match_reference", "description": "Reconstruct an effect from a reference image or concept sheet.",
     "arguments": [{"name": "description", "description": "What the reference shows and what matters most.", "required": True},
                   {"name": "reference_path", "description": "Path of the reference image for analyze_reference.",
                    "required": False}]},
    {"name": "polish_effect", "description": "Critique the active effect like a senior VFX artist and improve it.",
     "arguments": [{"name": "notes", "description": "What to improve (optional).", "required": False}]},
]


def prompt_text(name: str, arguments: dict[str, Any] | None) -> str | None:
    args = {k: str(v) for k, v in (arguments or {}).items() if v is not None}
    guide = _authoring_guide()
    if name == "create_effect":
        task = f"Create a new AetherFX effect: {args.get('description', '').strip() or '(describe the effect)'}"
        if args.get("duration"):
            task += f"\nTarget duration: {args['duration']} s."
    elif name == "match_reference":
        task = ("Reconstruct this effect from the reference. First call analyze_reference"
                + (f" on {args['reference_path']}" if args.get("reference_path") else "")
                + ", read the Effect Analysis Document, then build it (plan_from_ead can draft the graph) and compare "
                "your renders against the reference side by side.\nReference notes: "
                + (args.get("description", "").strip() or "(none)"))
    elif name == "polish_effect":
        task = ("Render the active effect at several times, critique it like a senior VFX artist (silhouette, "
                "readability, colour, timing, house style), then fix what you find and show the improvement."
                + (f"\nNotes: {args['notes']}" if args.get("notes") else ""))
    else:
        return None
    return f"{guide}\n\n# Task\n\n{task}\n\n# Workflow\n\n{WORKFLOW}\n"


# ---------------------------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------------------------

def wants_docs_page(method: str, accept: str) -> bool:
    """A browser opening the MCP endpoint: a GET that does not ask for an event stream."""
    return method.upper() == "GET" and "text/event-stream" not in (accept or "").lower()


_STYLE = """
:root { color-scheme: dark; }
body { margin: 0; background: #0a0a0c; color: #d7d7de; font: 15px/1.6 -apple-system, "Inter", "Segoe UI", sans-serif; }
main { max-width: 920px; margin: 0 auto; padding: 48px 24px 96px; }
h1 { font-size: 28px; margin: 0 0 8px; color: #fff; letter-spacing: -0.01em; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.12em; color: #8b8b98; margin: 44px 0 14px; }
p.lead { color: #a4a4b3; margin: 0 0 8px; max-width: 70ch; }
code, pre { font: 13px/1.55 "JetBrains Mono", "SF Mono", Menlo, monospace; }
code { background: #15151b; border: 1px solid #23232c; border-radius: 5px; padding: 1px 6px; color: #e6e6f0; }
pre { background: #101015; border: 1px solid #23232c; border-radius: 10px; padding: 14px 16px; overflow-x: auto; margin: 8px 0 18px; }
pre code { background: none; border: 0; padding: 0; }
.endpoint { display: inline-block; margin: 14px 0 6px; padding: 8px 12px; border-radius: 9px; background: #15121f;
  border: 1px solid #3b2f6b; color: #c9b8ff; }
a { color: #a78bfa; text-decoration: none; } a:hover { text-decoration: underline; }
ol, ul { padding-left: 20px; } li { margin: 4px 0; }
.label { color: #8b8b98; font-size: 12px; margin: 14px 0 2px; }
table { width: 100%; border-collapse: collapse; margin: 4px 0 8px; }
td, th { text-align: left; vertical-align: top; padding: 9px 10px; border-top: 1px solid #1c1c24; }
th { color: #8b8b98; font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; border-top: 0; }
td.name { white-space: nowrap; } td.args { color: #9a9aa8; font-size: 13px; }
.links a { margin-right: 18px; }
"""


def docs_html(endpoint: str, base_url: str, tools: Iterable[Any]) -> str:
    esc = html.escape
    records = tool_records(tools)
    rows = []
    for record in records:
        args = ", ".join(f"{name}{'' if required else '?'}" for name, _kind, required, _d in _arguments(record["input_schema"]))
        first_line = record["description"].split("\n")[0]
        rows.append(f"<tr><td class='name'><code>{esc(record['name'])}</code></td><td>{esc(first_line)}"
                    f"<div class='args'>{esc(args)}</div></td></tr>")
    workflow = "".join(f"<li>{_inline(esc(line.split('. ', 1)[1] if '. ' in line[:4] else line))}</li>"
                       for line in _workflow_items())
    guides = "".join(f"<li><a href='{esc(base_url)}/mcp/docs/{esc(r.slug)}.md'>{esc(r.title)}</a> "
                     f"<code>{esc(r.uri)}</code><br><span class='args'>{esc(r.description)}</span></li>"
                     for r in doc_resources())
    prompts = "".join(f"<li><code>{esc(p['name'])}</code>: {esc(p['description'])}</li>" for p in PROMPTS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AetherFX MCP</title><style>{_STYLE}</style></head><body><main>
<h1>AetherFX for agents</h1>
<p class="lead">This address is a Model Context Protocol endpoint. Point an MCP client at it and your agent can author
game VFX by calling tools, look at the renders the tools return, and iterate. Whatever it builds appears live in the studio.</p>
<div class="endpoint"><code>{esc(endpoint)}</code></div>
<p class="links"><a href="{esc(base_url)}/llms.txt">llms.txt</a><a href="{esc(base_url)}/mcp/docs.md">all docs as markdown</a>
<a href="{esc(base_url)}/mcp/tools.json">tools.json</a><a href="{esc(base_url)}/">open the studio</a></p>

<h2>Connect</h2>
<div class="label">Claude Code</div>
<pre><code>claude mcp add --transport http aetherfx {esc(endpoint)}</code></pre>
<div class="label">Project .mcp.json</div>
<pre><code>{esc(json.dumps({"mcpServers": {"aetherfx": {"type": "http", "url": endpoint}}}, indent=2))}</code></pre>
<div class="label">Claude Desktop, Cursor and other MCP clients</div>
<pre><code>{esc(json.dumps({"mcpServers": {"aetherfx": {"url": endpoint}}}, indent=2))}</code></pre>
<div class="label">Codex CLI, ~/.codex/config.toml (stdio)</div>
<pre><code>[mcp_servers.aetherfx]
command = "aetherfx-mcp"</code></pre>

<h2>Workflow</h2>
<ol>{workflow}</ol>

<h2>Guides, also available as MCP resources</h2>
<ul>{guides}</ul>

<h2>Prompts</h2>
<ul>{prompts}</ul>

<h2>Tools ({len(records)})</h2>
<table><tr><th>tool</th><th>what it does</th></tr>{''.join(rows)}</table>

<p class="args" style="margin-top:48px">AetherFX is open source under the MIT license.
Started by <a href="https://x.com/cogentgene1" target="_blank" rel="noopener noreferrer">Gene</a>.</p>
</main></body></html>"""


def _workflow_items() -> list[str]:
    items: list[str] = []
    for line in WORKFLOW.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0].isdigit() and ". " in stripped[:4]:
            items.append(stripped)
        elif items:
            items[-1] += " " + stripped
        else:
            items.append(stripped)
    return items


def _inline(escaped: str) -> str:
    """`code` spans in already-escaped text."""
    parts = escaped.split("`")
    return "".join(f"<code>{part}</code>" if index % 2 else part for index, part in enumerate(parts))
