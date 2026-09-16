"""End-to-end tests against the real engine binary.

Every test here is skipped unless ``build/bin/aetherfx`` exists *and* answers
``ping`` over JSON-RPC - a placeholder binary that prints "not yet implemented"
skips exactly like a missing one.  Run them once the C++ CLI lands:

    AETHERFX_BINARY=/path/to/aetherfx pytest tests/test_integration.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aetherfx.client import BINARY_ENV_VAR, Client
from aetherfx.jsonrpc import AetherError, SubprocessTransport, TransportError
from aetherfx.mcp_server import MCP_TOOLS

from conftest import ENGINE_BINARY


def _engine_path() -> str | None:
    """Return a usable engine path from the environment or the build tree."""
    from_env = os.environ.get(BINARY_ENV_VAR)
    if from_env and Path(from_env).is_file():
        return from_env
    return str(ENGINE_BINARY) if ENGINE_BINARY.is_file() else None


def _engine_responds(path: str) -> bool:
    """True when the binary answers ``ping`` with ``{"ok": true}``."""
    try:
        transport = SubprocessTransport(path)
    except (TransportError, OSError):
        return False
    try:
        return bool(transport.request("ping").get("ok"))
    except (AetherError, TransportError, OSError):
        return False
    finally:
        try:
            transport.close()
        except Exception:  # noqa: BLE001 - shutting down a broken engine
            pass


_PATH = _engine_path()
_LIVE = bool(_PATH) and _engine_responds(_PATH)

pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason=(
        f"the AetherFX engine is not available or does not answer ping "
        f"(looked at {_PATH or ENGINE_BINARY}); build the C++ CLI or set ${BINARY_ENV_VAR}"
    ),
)


@pytest.fixture
def engine(tmp_path: Path) -> Client:
    """A live client with its own output directory, closed after the test."""
    client = Client(binary=_PATH, output_dir=tmp_path / "out")
    try:
        yield client
    finally:
        client.close()


def test_ping(engine: Client) -> None:
    assert engine.ping()["ok"] is True


def test_tools_list_covers_the_mcp_surface(engine: Client) -> None:
    names = set(engine.tool_names())
    missing = [name for name in MCP_TOOLS if name not in names]
    assert not missing, f"the engine does not advertise: {missing}"


def test_create_node_set_parameter_inspect_graph(engine: Client) -> None:
    """The documented happy path: effect -> layer -> node -> parameter -> inspect."""
    created = engine.create_effect(name="Integration Fire", duration=2.0, seed=7)
    assert "effect_id" in created

    engine.create_layer(id="primary", name="Primary", role="primary")
    node = engine.create_node(
        type="emitter",
        id="fire_wall",
        layer="primary",
        parameters={"shape": "ring", "radius": 3.0},
    )["node"]
    assert node["id"] == "fire_wall"
    assert node["type"] == "emitter"

    engine.set_parameter(node_id="fire_wall", name="rate", value=1200.0)
    assert engine.get_parameter(node_id="fire_wall", name="rate")["value"] == pytest.approx(1200.0)

    graph = engine.inspect_graph()
    assert graph["name"] == "Integration Fire"
    assert graph["duration"] == pytest.approx(2.0)
    assert "fire_wall" in {node["id"] for node in graph["nodes"]}
    assert "primary" in {layer["id"] for layer in graph["layers"]}
    assert graph["diagnostics"]["errors"] == []


def test_unknown_parameter_reports_an_aether_code(engine: Client) -> None:
    engine.create_effect(name="Diagnostics", duration=1.0)
    engine.create_node(type="emitter", id="probe")
    with pytest.raises(AetherError) as excinfo:
        engine.set_parameter(node_id="probe", name="definitely_not_a_parameter", value=1)
    assert excinfo.value.aether_code == "E004"


def test_unknown_method_is_method_not_found(engine: Client) -> None:
    with pytest.raises(AetherError) as excinfo:
        engine.call("definitely_not_a_tool")
    assert excinfo.value.code == -32601


def test_close_shuts_the_process_down(tmp_path: Path) -> None:
    client = Client(binary=_PATH, output_dir=tmp_path)
    client.ping()
    transport = client.transport
    client.close()
    assert isinstance(transport, SubprocessTransport)
    assert transport.closed is True
