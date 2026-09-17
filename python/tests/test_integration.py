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
    # "errors" is a count. The only expected error is E010: this emitter has no particle input.
    error_codes = [d["code"] for d in graph["diagnostics"]["items"] if d["severity"] == "error"]
    assert error_codes == ["E010"]
    assert graph["diagnostics"]["errors"] == 1


def test_controls_scale_an_effect_without_editing_it(engine: Client) -> None:
    """The controls surface end to end: generate, list, set, reset (docs/CONTROLS.md)."""
    engine.create_effect(name="Knobs", duration=1.5, seed=3)
    engine.create_layer(id="primary", name="Flames", role="primary")
    engine.create_particle_system(id="flame_ps", layer="primary", parameters={"emissive": 2.0, "size": 0.2})
    engine.create_emitter(id="flames", layer="primary", parameters={"shape": "sphere", "rate": 120.0})
    engine.connect_nodes(from_="flame_ps", to="flames", port="particle")

    assert engine.list_controls()["count"] == 0
    generated = engine.generate_default_controls()
    assert generated["added"] > 0
    assert generated["groups"][0] == "Global"

    listed = engine.list_controls()
    by_id = {control["id"]: control for control in listed["controls"]}
    assert "global_intensity" in by_id
    assert by_id["primary_intensity"]["group"] == "Flames"
    assert by_id["global_intensity"]["value"] == pytest.approx(1.0)

    moved = engine.set_control(id="global_intensity", value=2.0)
    assert moved["control"]["value"] == pytest.approx(2.0)
    # the authored value is untouched: a control is not an edit of the graph
    assert engine.get_parameter(node_id="flame_ps", name="emissive")["value"] == pytest.approx(2.0)

    with pytest.raises(AetherError) as excinfo:
        engine.set_control(id="global_intensity", value=99.0)
    assert excinfo.value.aether_code == "E024"

    added = engine.add_control(
        label="Flame height",
        group="Flames",
        bindings=[{"node": "flame_ps", "parameter": "size", "op": "multiply"}],
    )
    assert added["control"]["id"] == "flame_height"
    assert engine.update_control(id="flame_height", max=4.0)["control"]["max"] == pytest.approx(4.0)
    assert engine.remove_control(id="flame_height")["removed"] == "flame_height"

    assert engine.reset_controls()["reset"] == 1
    assert engine.list_controls()["controls"][0]["value"] == pytest.approx(1.0)


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
