"""The MCP surface: exactly the curated tools, in the documented order."""

from __future__ import annotations

import base64
import functools
import json
from pathlib import Path
from typing import Any, Callable

import anyio
import mcp.types as types
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from aetherfx.ai.mock import MockAnalyzer
from aetherfx.client import Client
from aetherfx.jsonrpc import ERROR_INVALID_PARAMS, AetherError, InMemoryTransport
from aetherfx.mcp_server import AI_TOOLS, MCP_TOOLS, STATIC_TOOL_DEFINITIONS, AetherMCPServer, build_parser

from conftest import FakeEngine

EXPECTED_ORDER = list(MCP_TOOLS) + list(AI_TOOLS)


@pytest.fixture
def server(fake_transport: InMemoryTransport) -> AetherMCPServer:
    return AetherMCPServer(Client(transport=fake_transport), analyzer=MockAnalyzer())


def call(server: AetherMCPServer, tool: str, /, **arguments: Any) -> types.CallToolResult:
    """Run one tool call synchronously."""
    return anyio.run(functools.partial(server.call_tool, tool, arguments))


def result_json(result: types.CallToolResult) -> Any:
    """Decode the first text block of a tool result."""
    return json.loads(result.content[0].text)


class TestToolList:
    def test_curated_names_in_documented_order(self, server: AetherMCPServer) -> None:
        assert [tool.name for tool in server.tool_definitions()] == EXPECTED_ORDER

    def test_surface_stays_small(self) -> None:
        assert len(MCP_TOOLS) == 28
        assert len(EXPECTED_ORDER) == 30

    def test_non_curated_engine_tools_are_filtered_out(self, server: AetherMCPServer) -> None:
        names = [tool.name for tool in server.tool_definitions()]
        assert "inspect_plan" not in names
        assert "render_turntable" not in names
        assert "delete_effect" not in names

    def test_descriptions_and_schemas_come_from_the_engine(self, server: AetherMCPServer) -> None:
        tools = {tool.name: tool for tool in server.tool_definitions()}
        assert tools["create_effect"].description == "Create an effect and make it active."
        assert tools["create_effect"].input_schema["required"] == ["name"]

    def test_tools_the_engine_omits_fall_back_to_static_definitions(self, server: AetherMCPServer) -> None:
        tools = {tool.name: tool for tool in server.tool_definitions()}
        assert tools["undo"].description == STATIC_TOOL_DEFINITIONS["undo"]
        assert tools["undo"].input_schema == {"type": "object", "additionalProperties": True}

    def test_every_tool_has_a_description(self, server: AetherMCPServer) -> None:
        assert all(tool.description for tool in server.tool_definitions())

    def test_static_definitions_cover_every_curated_tool(self) -> None:
        assert set(STATIC_TOOL_DEFINITIONS) == set(MCP_TOOLS)

    def test_list_is_cached_until_refreshed(self, server: AetherMCPServer, fake_transport: InMemoryTransport) -> None:
        server.tool_definitions()
        server.tool_definitions()
        assert sum(1 for method, _ in fake_transport.calls if method == "tools/list") == 1


class TestOfflineFallback:
    def test_server_starts_without_a_binary(self) -> None:
        offline = AetherMCPServer(Client(binary="/definitely/not/here/aetherfx"), analyzer=MockAnalyzer())
        assert [tool.name for tool in offline.tool_definitions()] == EXPECTED_ORDER
        assert offline.engine_error is not None
        assert "aetherfx" in offline.engine_error

    def test_engine_calls_report_the_missing_engine(self) -> None:
        offline = AetherMCPServer(Client(binary="/definitely/not/here/aetherfx"), analyzer=MockAnalyzer())
        result = call(offline, "create_effect", name="X")
        assert result.is_error is True
        assert "engine is unavailable" in result.content[0].text

    def test_ai_tools_still_work_offline(self, tmp_path: Path, make_image: Callable[..., Path]) -> None:
        offline = AetherMCPServer(Client(binary="/definitely/not/here/aetherfx"), analyzer=MockAnalyzer())
        image = make_image("ref.png")
        result = call(offline, "analyze_reference", image_path=str(image))
        assert result.is_error is not True
        assert result_json(result)["global"]["effect_category"] == "fire_aoe"


class TestEngineCalls:
    def test_forwards_arguments_and_returns_json(
        self, server: AetherMCPServer, fake_transport: InMemoryTransport
    ) -> None:
        result = call(server, "create_effect", name="Fire AOE", duration=4.5)
        assert fake_transport.calls[-1] == ("create_effect", {"name": "Fire AOE", "duration": 4.5})
        assert result_json(result)["effect"]["name"] == "Fire AOE"
        assert result.structured_content is not None

    def test_engine_errors_become_tool_errors_with_the_aether_code(self, server: AetherMCPServer) -> None:
        call(server, "create_effect", name="X")
        call(server, "create_node", type="emitter", id="fire")
        result = call(server, "set_parameter", node_id="fire", name="bogus_param", value=1)
        assert result.is_error is True
        text = result.content[0].text
        assert "E004" in text and "bogus_param" in text and "set_parameter failed" in text

    def test_unknown_tool_is_rejected(self, server: AetherMCPServer) -> None:
        result = call(server, "delete_effect", effect_id="e0")
        assert result.is_error is True
        assert "unknown tool" in result.content[0].text


class TestRenderImages:
    def _server_returning(self, path: str, key: str, tool: str) -> AetherMCPServer:
        transport = InMemoryTransport(handlers={tool: lambda _params: {key: path, "time": 1.0}})
        return AetherMCPServer(Client(transport=transport), analyzer=MockAnalyzer())

    def test_render_frame_attaches_the_png(self, make_image: Callable[..., Path]) -> None:
        image = make_image("frame.png")
        result = call(self._server_returning(str(image), "path", "render_frame"), "render_frame", time=1.0)
        assert [block.type for block in result.content] == ["text", "image"]
        block = result.content[1]
        assert block.mime_type == "image/png"
        assert base64.standard_b64decode(block.data) == image.read_bytes()

    def test_render_preview_attaches_the_contact_sheet(self, make_image: Callable[..., Path]) -> None:
        sheet = make_image("sheet.png")
        server = self._server_returning(str(sheet), "contact_sheet", "render_preview")
        result = call(server, "render_preview", fps=12)
        assert [block.type for block in result.content] == ["text", "image"]
        assert result_json(result)["contact_sheet"] == str(sheet)

    def test_unreadable_image_degrades_to_a_note(self) -> None:
        server = self._server_returning("/nope/missing.png", "path", "render_frame")
        result = call(server, "render_frame", time=0.0)
        assert [block.type for block in result.content] == ["text", "text"]
        assert "could not be read" in result.content[1].text
        assert result.is_error is not True


class TestAiTools:
    def test_analyze_reference_returns_a_valid_ead(
        self, server: AetherMCPServer, make_image: Callable[..., Path]
    ) -> None:
        from aetherfx.ead import EffectAnalysisDocument

        result = call(server, "analyze_reference", image_path=str(make_image("ref.png")), prompt="bigger")
        payload = result_json(result)
        EffectAnalysisDocument.model_validate(payload)
        assert payload["source"]["prompt"] == "bigger"

    def test_analyze_reference_requires_an_existing_file(self, server: AetherMCPServer) -> None:
        result = call(server, "analyze_reference", image_path="/nope/missing.png")
        assert result.is_error is True
        assert "no such image" in result.content[0].text

    def test_analyze_reference_requires_image_path(self, server: AetherMCPServer) -> None:
        result = call(server, "analyze_reference")
        assert result.is_error is True
        assert "image_path" in result.content[0].text

    def test_plan_from_ead_builds_the_graph(self, server: AetherMCPServer, fake_engine: FakeEngine) -> None:
        from aetherfx.ai.mock import canned_fire_aoe_ead

        document = canned_fire_aoe_ead("refs/fire_aoe.png")
        result = call(server, "plan_from_ead", ead_json=json.dumps(document.to_json_dict()))
        payload = result_json(result)
        assert payload["effect_id"] == fake_engine.active
        assert payload["call_count"] == len(payload["calls"])
        assert payload["calls"][0]["name"] == "create_effect"
        assert len(fake_engine.layers) == 6

    def test_plan_from_ead_accepts_an_object(self, server: AetherMCPServer, fake_engine: FakeEngine) -> None:
        from aetherfx.ai.mock import canned_fire_aoe_ead

        result = call(server, "plan_from_ead", ead_json=canned_fire_aoe_ead().to_json_dict())
        assert result.is_error is not True
        assert fake_engine.active is not None

    def test_plan_from_ead_rejects_an_invalid_document(self, server: AetherMCPServer) -> None:
        result = call(server, "plan_from_ead", ead_json={"source": {"kind": "image"}})
        assert result.is_error is True
        assert "did not validate" in result.content[0].text

    def test_plan_from_ead_reports_where_it_stopped(self, fake_engine: FakeEngine) -> None:
        from aetherfx.ai.mock import canned_fire_aoe_ead

        def failing_create_node(params: dict) -> dict:
            if params.get("type") == "texture":
                raise AetherError(
                    ERROR_INVALID_PARAMS, "unknown parameter: graph", aether_code="E004", param="graph"
                )
            return fake_engine.create_node(params)

        handlers = fake_engine.handlers()
        handlers["create_node"] = failing_create_node
        server = AetherMCPServer(Client(transport=InMemoryTransport(handlers=handlers)), analyzer=MockAnalyzer())
        result = call(server, "plan_from_ead", ead_json=canned_fire_aoe_ead().to_json_dict())
        assert result.is_error is True
        text = result.content[0].text
        assert "stopped at call" in text and "E004" in text


class TestCli:
    def test_parser_exposes_the_documented_flags(self) -> None:
        args = build_parser().parse_args(
            ["--binary", "/bin/aetherfx", "--output-dir", "/tmp/out", "--analyzer", "claude"]
        )
        assert (args.binary, args.output_dir, args.analyzer) == ("/bin/aetherfx", "/tmp/out", "claude")

    def test_flags_default_to_none(self) -> None:
        args = build_parser().parse_args([])
        assert (args.binary, args.output_dir, args.analyzer) == (None, None, None)


class TestProtocolRoundTrip:
    """Drive the real MCP server over the SDK's in-memory streams."""

    def test_list_and_call_over_mcp(self, fake_transport: InMemoryTransport) -> None:
        aether = AetherMCPServer(Client(transport=fake_transport), analyzer=MockAnalyzer())
        collected: dict[str, Any] = {}

        async def scenario() -> None:
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                server = aether.build_server()
                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(
                        functools.partial(
                            server.run,
                            server_streams[0],
                            server_streams[1],
                            server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )
                    async with ClientSession(client_streams[0], client_streams[1]) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        collected["names"] = [tool.name for tool in listed.tools]
                        created = await session.call_tool("create_effect", {"name": "Fire AOE", "duration": 4.5})
                        collected["created"] = json.loads(created.content[0].text)
                    task_group.cancel_scope.cancel()

        anyio.run(scenario)
        assert collected["names"] == EXPECTED_ORDER
        assert collected["created"]["effect"]["name"] == "Fire AOE"
