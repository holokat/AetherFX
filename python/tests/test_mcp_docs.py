"""Documentation entry points for agents: MCP resources/prompts and the HTTP docs routes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aetherfx import mcp_docs
from aetherfx.mcp_server import AetherMCPServer
from aetherfx.studio.server import StudioConfig, create_app

from test_studio import BINARY, EXAMPLES_DIR


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    """A studio with a real engine, no generator backend, MCP mounted."""
    config = StudioConfig(output_dir=tmp_path_factory.mktemp("studio-docs"), examples_dir=EXAMPLES_DIR,
                          binary=BINARY, generator="none", port=8778)
    with TestClient(create_app(config)) as test_client:
        yield test_client


class _NoEngine:
    """No engine binary: the server falls back to its static tool definitions."""

    def tools(self):
        raise OSError("no engine in this test")


def _server() -> AetherMCPServer:
    return AetherMCPServer(_NoEngine())  # type: ignore[arg-type]


def test_guides_are_listed_and_readable_as_mcp_resources():
    server = _server()
    listed = asyncio.run(server.list_resources(None, None)).resources  # type: ignore[arg-type]
    uris = [str(r.uri) for r in listed]
    assert "aetherfx://docs/getting-started" in uris and "aetherfx://docs/authoring-guide" in uris
    assert all(r.mime_type == "text/markdown" for r in listed)

    guide = asyncio.run(server.read_resource(None, SimpleNamespace(uri="aetherfx://docs/authoring-guide")))  # type: ignore[arg-type]
    text = guide.contents[0].text
    assert "HOUSE STYLE" in text and "impact" in text.lower()

    start = asyncio.run(server.read_resource(None, SimpleNamespace(uri="aetherfx://docs/getting-started")))  # type: ignore[arg-type]
    assert "claude mcp add" in start.contents[0].text and "create_effect" in start.contents[0].text

    with pytest.raises(ValueError):
        asyncio.run(server.read_resource(None, SimpleNamespace(uri="aetherfx://docs/nope")))  # type: ignore[arg-type]


def test_prompts_carry_the_guide_and_the_task():
    server = _server()
    names = [p.name for p in asyncio.run(server.list_prompts(None, None)).prompts]  # type: ignore[arg-type]
    assert names == ["create_effect", "match_reference", "polish_effect"]
    result = asyncio.run(server.get_prompt(None, SimpleNamespace(name="create_effect",  # type: ignore[arg-type]
                                                                   arguments={"description": "a frost nova"})))
    text = result.messages[0].content.text
    assert "a frost nova" in text and "HOUSE STYLE" in text and "describe_vocabulary" in text
    with pytest.raises(ValueError):
        asyncio.run(server.get_prompt(None, SimpleNamespace(name="nope", arguments={})))  # type: ignore[arg-type]


def test_docs_page_is_only_for_plain_gets():
    assert mcp_docs.wants_docs_page("GET", "text/html,application/xhtml+xml")
    assert mcp_docs.wants_docs_page("GET", "")
    assert not mcp_docs.wants_docs_page("GET", "application/json, text/event-stream")   # a real MCP client
    assert not mcp_docs.wants_docs_page("POST", "text/html")


def test_html_escapes_tool_text_and_lists_every_tool():
    tools = [{"name": "render_frame", "description": "Render <b>one</b> frame", "input_schema": {
        "type": "object", "properties": {"time": {"type": "number", "description": "seconds"}}, "required": ["time"]}}]
    page = mcp_docs.docs_html("http://127.0.0.1:8770/mcp", "http://127.0.0.1:8770", tools)
    assert "render_frame" in page and "&lt;b&gt;one&lt;/b&gt;" in page and "<b>one</b>" not in page
    assert "claude mcp add --transport http aetherfx http://127.0.0.1:8770/mcp" in page
    payload = json.loads(mcp_docs.tools_json(tools, "http://x/mcp"))
    assert payload["tools"][0]["name"] == "render_frame" and payload["resources"] and payload["prompts"]


def test_studio_serves_docs_for_agents_and_browsers(client: TestClient):
    html = client.get("/mcp", headers={"Accept": "text/html"})
    assert html.status_code == 200 and "AetherFX for agents" in html.text and "describe_vocabulary" in html.text
    llms = client.get("/llms.txt")
    assert llms.status_code == 200 and "/mcp/docs/authoring-guide.md" in llms.text
    assert client.get("/mcp/docs/authoring-guide.md").status_code == 200
    assert client.get("/mcp/docs/nope.md").status_code == 404
    everything = client.get("/mcp/docs.md")
    assert everything.status_code == 200 and "aetherfx://docs/vocabulary" in everything.text
    tools = client.get("/mcp/tools.json").json()
    assert len(tools["tools"]) >= 30 and tools["endpoint"].endswith("/mcp")
