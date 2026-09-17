"""MCP stdio server exposing the curated AetherFX tool surface.

docs/AGENT_API.md lists a deliberately small subset of the engine's tools for
MCP - :data:`MCP_TOOLS`, in that exact order.  Everything else stays reachable
through the CLI and :class:`aetherfx.Client`.  Two AI-side tools are added on
top, implemented in Python rather than by the engine:

* ``analyze_reference`` - run the configured
  :class:`~aetherfx.ai.base.ReferenceAnalyzer` on an image and return the EAD.
* ``plan_from_ead`` - turn an EAD into graph tool calls and apply them.

Descriptions and input schemas come from the engine's own ``tools/list`` at
startup, so the MCP surface can never drift from the registry.  When the binary
is missing the server still starts, advertising :data:`STATIC_TOOL_DEFINITIONS`
with permissive schemas, and every engine call returns a clear error saying the
engine is unavailable.  A stub server that starts is far more useful to an agent
than one that refuses to load.

Render tools return their JSON result *and* the image, so the agent can see what
it made: ``render_frame`` attaches ``path`` and ``render_preview`` attaches
``contact_sheet``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any, Sequence

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.context import ServerRequestContext
from mcp.server.stdio import stdio_server

from .ai.base import ReferenceAnalyzer
from .ai.registry import get_analyzer
from .client import Client
from .ead import EffectAnalysisDocument
from .jsonrpc import AetherError, JsonDict, TransportError
from .planner import PlanExecutionError, apply_plan, ead_to_tool_calls

__all__ = [
    "MCP_TOOLS",
    "AI_TOOLS",
    "STATIC_TOOL_DEFINITIONS",
    "AetherMCPServer",
    "build_parser",
    "main",
]

#: The curated engine tools, in the order docs/AGENT_API.md lists them.
MCP_TOOLS: tuple[str, ...] = (
    "describe_vocabulary",
    "create_effect",
    "load_effect",
    "save_effect",
    "list_effects",
    "inspect_graph",
    "inspect_node",
    "create_layer",
    "create_node",
    "delete_node",
    "duplicate_layer",
    "set_parameter",
    "set_parameters",
    "get_parameter",
    "set_keyframe",
    "list_controls",
    "set_control",
    "reset_controls",
    "generate_default_controls",
    "connect_nodes",
    "disconnect_nodes",
    "set_timeline_phase",
    "simulate",
    "render_frame",
    "render_preview",
    "inspect_statistics",
    "compare_reference",
    "evaluate_effect",
    "export_effect",
    "undo",
    "redo",
    "get_effect_json",
)

#: Python-side tools served by this process, appended after the engine tools.
AI_TOOLS: tuple[str, ...] = ("analyze_reference", "plan_from_ead")

#: Tools whose result also carries an image, and the result key holding its path.
_IMAGE_RESULT_KEYS: dict[str, str] = {
    "render_frame": "path",
    "render_preview": "contact_sheet",
}

_PERMISSIVE_SCHEMA: JsonDict = {"type": "object", "additionalProperties": True}

#: Fallback descriptions, used when the engine is not reachable at startup.
#: Kept in step with the tables in docs/AGENT_API.md.
STATIC_TOOL_DEFINITIONS: dict[str, str] = {
    "describe_vocabulary": "Return the node vocabulary (all node specs, or one via node_type) plus texture_ops.",
    "create_effect": "Create an effect (name, duration=2, seed=1, template?) and make it active.",
    "load_effect": "Load an effect document from path and make it active.",
    "save_effect": "Save the active effect as JSON to path (defaults to the session output directory).",
    "list_effects": "List loaded effects: effect_id, name, path, dirty, active.",
    "inspect_graph": "Return the whole graph: name, duration, seed, timeline, layers, nodes, diagnostics.",
    "inspect_node": "Return one node with its spec, effective parameters, resolved inputs, consumers, tier and window.",
    "create_layer": "Create a semantic layer (name, role: telegraph|ignition|primary|secondary|interaction|aftermath|custom, id?).",
    "create_node": "Create a node of any vocabulary type (type, id?, layer?, parent?, parameters?, inputs?, metadata?, seed?).",
    "delete_node": "Delete a node and strip references to it; returns the dangling ports that were cleared.",
    "duplicate_layer": "Duplicate a layer and its nodes, remapping internal references.",
    "set_parameter": "Set one constant parameter on a node, clearing any keyframe track on it.",
    "set_parameters": "Set several parameters on a node at once.",
    "get_parameter": "Read a parameter: value, default, animated, track and spec (optionally sampled at a time).",
    "set_keyframe": "Add or replace a keyframe on a parameter's effect-time track (interp: linear|step|smooth).",
    "list_controls": "List the effect's controls: named numeric knobs {id, label, group, min, max, default, value, step, unit, bindings}.",
    "set_control": "Move one control to a value inside its [min, max]; the authored parameters are untouched.",
    "reset_controls": "Put every control (or just id) back to its default value.",
    "generate_default_controls": "Build a sensible control set: a Global group and one group per layer (replace=false keeps existing controls).",
    "connect_nodes": "Connect node 'from' into node 'to' at input 'port'; appends on multi ports, replaces on single ports.",
    "disconnect_nodes": "Remove one reference from to.port, or clear the port entirely.",
    "set_timeline_phase": "Create or update a timeline phase with absolute start and end in seconds.",
    "simulate": "Simulate from 0 to time (default: the effect duration) and return statistics.",
    "render_frame": "Render one frame at time to a PNG and return its path, statistics and image stats.",
    "render_preview": "Render a frame sequence and a contact sheet across the effect timeline.",
    "inspect_statistics": "Return the last known simulation, render and plan statistics.",
    "compare_reference": "Compare a render against a reference image: coverage IoU, palette, histogram, centroid, radial profile, score.",
    "evaluate_effect": "Return statistics, budget warnings, render statistics, diagnostics and score hints.",
    "export_effect": "Export the effect as json, flipbook or frames.",
    "undo": "Undo the last mutating tool call.",
    "redo": "Redo the last undone tool call.",
    "get_effect_json": "Return the full effect document as JSON.",
}

_ANALYZE_REFERENCE_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "image_path": {"type": "string", "description": "Path to the reference image (PNG, JPEG, GIF or WebP)."},
        "prompt": {
            "type": "string",
            "description": "Optional extra direction for the analyzer, e.g. 'treat it as a top-down ice AOE'.",
        },
    },
    "required": ["image_path"],
    "additionalProperties": False,
}

_PLAN_FROM_EAD_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "ead_json": {
            "type": ["string", "object"],
            "description": "An Effect Analysis Document, as a JSON string or an object (schema/ead.schema.json).",
        }
    },
    "required": ["ead_json"],
    "additionalProperties": False,
}

_AI_TOOL_DEFINITIONS: dict[str, tuple[str, JsonDict]] = {
    "analyze_reference": (
        "Analyse a reference image into an Effect Analysis Document (EAD) using the configured analyzer. "
        "Motion and timing read off a still image are marked inferred; check the confidence values.",
        _ANALYZE_REFERENCE_SCHEMA,
    ),
    "plan_from_ead": (
        "Turn an Effect Analysis Document into graph tool calls and apply them: creates the effect, its "
        "timeline phases, its layers, its nodes and the conventional connections between them.",
        _PLAN_FROM_EAD_SCHEMA,
    ),
}


def _text(payload: Any) -> types.TextContent:
    """Wrap a JSON-serialisable payload in an MCP text block."""
    if isinstance(payload, str):
        return types.TextContent(type="text", text=payload)
    return types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))


def _error_result(message: str) -> types.CallToolResult:
    """Build a failed :class:`types.CallToolResult` carrying ``message``."""
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


def _describe_aether_error(name: str, error: AetherError) -> str:
    """Render an engine error for an agent, keeping the aether_code visible."""
    parts = [f"{name} failed: {error.message}"]
    for label, value in (
        ("aether_code", error.aether_code),
        ("node", error.node),
        ("param", error.param),
    ):
        if value is not None:
            parts.append(f"{label}={value}")
    parts.append(f"jsonrpc_code={error.code}")
    return " | ".join(parts)


def _image_content(path: str | os.PathLike[str]) -> types.ImageContent | None:
    """Read a rendered PNG and wrap it as base64 image content, or return None."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    suffix = Path(path).suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        suffix, "image/png"
    )
    return types.ImageContent(
        type="image", data=base64.standard_b64encode(data).decode("ascii"), mime_type=mime
    )


class AetherMCPServer:
    """Serves the curated AetherFX surface over MCP.

    Parameters
    ----------
    client:
        The engine client.  Its transport is created lazily, so constructing the
        server never requires a built binary.
    analyzer:
        A ready-made analyzer, or ``None`` to resolve ``analyzer_name`` lazily.
    analyzer_name:
        ``"mock"`` or ``"claude"``; ``None`` means ``$AETHERFX_ANALYZER``.

    The MCP handlers are :meth:`list_tools` and :meth:`call_tool`; both are
    plain coroutines so they can be unit-tested without a transport.
    """

    def __init__(
        self,
        client: Client,
        analyzer: ReferenceAnalyzer | None = None,
        analyzer_name: str | None = None,
    ) -> None:
        self.client = client
        self.analyzer_name = analyzer_name
        self._analyzer = analyzer
        #: Set when ``tools/list`` could not be read at startup; reported on call.
        self.engine_error: str | None = None
        self._tool_cache: list[types.Tool] | None = None

    # -- analyzer ----------------------------------------------------------

    @property
    def analyzer(self) -> ReferenceAnalyzer:
        """The configured analyzer, built on first use."""
        if self._analyzer is None:
            self._analyzer = get_analyzer(self.analyzer_name)
        return self._analyzer

    # -- tool definitions --------------------------------------------------

    def _engine_tool_definitions(self) -> tuple[dict[str, types.Tool], str | None]:
        """Read ``tools/list`` and keep the curated entries, or report why not."""
        try:
            infos = {info.name: info for info in self.client.tools()}
        except (AetherError, TransportError, OSError) as exc:
            return {}, str(exc)
        found: dict[str, types.Tool] = {}
        for name in MCP_TOOLS:
            info = infos.get(name)
            if info is None:
                continue
            schema = info.input_schema if isinstance(info.input_schema, dict) and info.input_schema else dict(
                _PERMISSIVE_SCHEMA
            )
            found[name] = types.Tool(
                name=name,
                description=info.description or STATIC_TOOL_DEFINITIONS.get(name, name),
                input_schema=schema,
            )
        return found, None

    def tool_definitions(self, refresh: bool = False) -> list[types.Tool]:
        """Return the curated tools in documented order, then the AI tools.

        Engine tools the running binary does not advertise (or all of them, when
        the binary is missing) fall back to :data:`STATIC_TOOL_DEFINITIONS` with
        a permissive object schema, so the list is always complete and always in
        the same order.
        """
        if self._tool_cache is not None and not refresh:
            return list(self._tool_cache)

        from_engine, error = self._engine_tool_definitions()
        self.engine_error = error

        tools: list[types.Tool] = []
        for name in MCP_TOOLS:
            tool = from_engine.get(name)
            if tool is None:
                tool = types.Tool(
                    name=name,
                    description=STATIC_TOOL_DEFINITIONS.get(name, name),
                    input_schema=dict(_PERMISSIVE_SCHEMA),
                )
            tools.append(tool)
        for name in AI_TOOLS:
            description, schema = _AI_TOOL_DEFINITIONS[name]
            tools.append(types.Tool(name=name, description=description, input_schema=dict(schema)))

        self._tool_cache = tools
        return list(tools)

    # -- dispatch ----------------------------------------------------------

    def _call_engine(self, name: str, arguments: JsonDict) -> types.CallToolResult:
        """Forward one curated tool call to the engine and shape the result."""
        try:
            result = self.client.call_with(name, arguments)
        except AetherError as exc:
            return _error_result(_describe_aether_error(name, exc))
        except (TransportError, OSError) as exc:
            return _error_result(f"{name} failed: the AetherFX engine is unavailable: {exc}")

        content: list[Any] = [_text(result)]
        image_key = _IMAGE_RESULT_KEYS.get(name)
        if image_key:
            path = result.get(image_key)
            if isinstance(path, str):
                image = _image_content(path)
                if image is not None:
                    content.append(image)
                else:
                    content.append(_text(f"note: {name} returned {image_key}={path!r} but it could not be read"))
        return types.CallToolResult(content=content, structured_content=result)

    def _call_analyze_reference(self, arguments: JsonDict) -> types.CallToolResult:
        """Run the configured analyzer on an image and return the EAD."""
        image_path = arguments.get("image_path")
        if not isinstance(image_path, str) or not image_path:
            return _error_result("analyze_reference failed: 'image_path' is required and must be a string")
        if not Path(image_path).is_file():
            return _error_result(f"analyze_reference failed: no such image: {image_path}")
        prompt = arguments.get("prompt")
        try:
            document = self.analyzer.analyze_reference(image_path, prompt if isinstance(prompt, str) else None)
        except Exception as exc:  # noqa: BLE001 - adapters raise their own error types
            return _error_result(f"analyze_reference failed: {type(exc).__name__}: {exc}")
        payload = document.to_json_dict()
        return types.CallToolResult(content=[_text(payload)], structured_content=payload)

    def _call_plan_from_ead(self, arguments: JsonDict) -> types.CallToolResult:
        """Validate an EAD, translate it into tool calls and apply them."""
        raw = arguments.get("ead_json")
        if raw is None:
            return _error_result("plan_from_ead failed: 'ead_json' is required")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            document = EffectAnalysisDocument.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - json + pydantic errors
            return _error_result(f"plan_from_ead failed: the EAD did not validate: {exc}")

        calls = ead_to_tool_calls(document)
        try:
            results = apply_plan(self.client, calls)
        except PlanExecutionError as exc:
            detail = (
                _describe_aether_error(exc.call.name, exc.error)
                if isinstance(exc.error, AetherError)
                else f"{exc.call.name} failed: {exc.error}"
            )
            return _error_result(
                f"plan_from_ead stopped at call {exc.index + 1} of {len(calls)}: {detail}\n"
                f"arguments: {json.dumps(exc.call.args, ensure_ascii=False)}"
            )
        except (TransportError, OSError) as exc:
            return _error_result(f"plan_from_ead failed: the AetherFX engine is unavailable: {exc}")

        effect_id = results[0].get("effect_id") if results else None
        payload: JsonDict = {
            "effect_id": effect_id,
            "calls": [{"name": call.name, "args": call.args} for call in calls],
            "call_count": len(calls),
        }
        return types.CallToolResult(content=[_text(payload)], structured_content=payload)

    async def call_tool(self, name: str, arguments: JsonDict | None) -> types.CallToolResult:
        """Dispatch one MCP tool call.  Never raises; failures become tool errors."""
        args: JsonDict = dict(arguments or {})
        if name == "analyze_reference":
            return self._call_analyze_reference(args)
        if name == "plan_from_ead":
            return self._call_plan_from_ead(args)
        if name in MCP_TOOLS:
            return self._call_engine(name, args)
        return _error_result(
            f"unknown tool: {name!r}. Available: {', '.join(MCP_TOOLS + AI_TOOLS)}"
        )

    # -- MCP handlers ------------------------------------------------------

    async def list_tools(
        self,
        _context: ServerRequestContext[Any],
        _params: types.PaginatedRequestParams | None = None,
    ) -> types.ListToolsResult:
        """MCP ``tools/list`` handler."""
        return types.ListToolsResult(tools=self.tool_definitions())

    async def handle_call_tool(
        self,
        _context: ServerRequestContext[Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """MCP ``tools/call`` handler."""
        return await self.call_tool(params.name, dict(params.arguments or {}))

    def build_server(self) -> Server[Any]:
        """Build the low-level MCP :class:`Server` wired to this instance."""
        return Server(
            "aetherfx",
            version="0.1.0",
            title="AetherFX",
            instructions=(
                "Author real-time game VFX as a node graph. Call describe_vocabulary first to get the "
                "exact node types and parameter names, build with create_layer/create_node/connect_nodes, "
                "then simulate and render_frame to see the result. Every mutating tool returns diagnostics: "
                "read them. analyze_reference turns a reference image into an Effect Analysis Document and "
                "plan_from_ead builds the graph from it."
            ),
            on_list_tools=self.list_tools,
            on_call_tool=self.handle_call_tool,
        )

    async def run_stdio(self) -> None:
        """Serve MCP over stdio until the client disconnects."""
        server = self.build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    async def run_http(self, host: str = "127.0.0.1", port: int = 8765, path: str = "/mcp") -> None:
        """Serve MCP over streamable HTTP at ``http://host:port/path`` until interrupted.

        Use this to keep one long-lived engine session that several clients (Claude
        Code, Claude Desktop, scripts) can attach to by URL instead of each spawning
        their own engine over stdio.  Binds to localhost by default.
        """
        import contextlib  # noqa: PLC0415 - only needed when actually serving
        import uvicorn  # noqa: PLC0415
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager  # noqa: PLC0415
        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.routing import Route  # noqa: PLC0415

        server = self.build_server()
        manager = StreamableHTTPSessionManager(app=server)

        class _McpEndpoint:
            """Raw ASGI endpoint (a class, so Starlette does not wrap it as a request handler)."""

            async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                await manager.handle_request(scope, receive, send)

        @contextlib.asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            async with manager.run():
                yield

        # An exact Route (not a Mount) so clients are never redirected between
        # "/mcp" and "/mcp/"; several MCP clients do not follow redirects on POST.
        async def index(_request: Any) -> Any:
            from starlette.responses import PlainTextResponse  # noqa: PLC0415

            return PlainTextResponse(
                "AetherFX MCP server is running.\n\n"
                f"MCP endpoint (streamable HTTP): http://{host}:{port}{path}\n"
                "This endpoint speaks the Model Context Protocol; open it from an MCP client, not a browser.\n\n"
                f"Claude Code:    claude mcp add --transport http aetherfx http://{host}:{port}{path}\n"
                f'Claude Desktop: {{"mcpServers": {{"aetherfx": {{"url": "http://{host}:{port}{path}"}}}}}}\n'
                f"Python:         aetherfx.Client / mcp.client.streamable_http.streamable_http_client(\"http://{host}:{port}{path}\")\n"
            )

        app = Starlette(
            routes=[
                Route("/", index, methods=["GET"]),
                Route(path, _McpEndpoint(), methods=["GET", "POST", "DELETE"]),
            ],
            lifespan=lifespan,
        )
        app.router.redirect_slashes = False
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        await uvicorn.Server(config).serve()


def build_parser() -> argparse.ArgumentParser:
    """Build the ``aetherfx-mcp`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="aetherfx-mcp",
        description="MCP stdio server for the AetherFX VFX engine.",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help="Path to the aetherfx executable (default: $AETHERFX_BINARY, build/bin/aetherfx, then PATH).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Session output directory for rendered frames and saved effects.",
    )
    parser.add_argument(
        "--analyzer",
        default=None,
        help="Reference analyzer: 'mock' (default, offline) or 'claude' (default: $AETHERFX_ANALYZER).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default; launched by the MCP client) or http (long-lived streamable HTTP server).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="http transport bind address (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="http transport port (default 8765).")
    parser.add_argument("--path", default="/mcp", help="http transport URL path (default /mcp).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for ``aetherfx-mcp``."""
    import anyio  # noqa: PLC0415 - only needed when actually serving

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    client = Client(binary=args.binary, output_dir=args.output_dir)
    server = AetherMCPServer(client, analyzer_name=args.analyzer)
    # Warm the tool list so a missing binary is logged once, at startup, on
    # stderr - stdout belongs to the MCP transport.
    server.tool_definitions()
    if server.engine_error:
        print(
            f"aetherfx-mcp: the engine is not reachable ({server.engine_error}); "
            "serving static tool definitions. Engine calls will return an error until it is built.",
            file=sys.stderr,
        )
    try:
        if args.transport == "http":
            print(f"aetherfx-mcp: serving MCP over HTTP at http://{args.host}:{args.port}{args.path}", file=sys.stderr)
            anyio.run(server.run_http, args.host, args.port, args.path)
        else:
            anyio.run(server.run_stdio)
    finally:
        client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - `python -m aetherfx.mcp_server`
    raise SystemExit(main())
