"""Shared fixtures: a fake engine, image factories and repository paths.

The fake engine is deliberately small - it models just enough of the C++
``Session`` (an active effect, layers, nodes, ports) for the client, planner and
MCP tests to assert real behaviour rather than mock call counts.  It is *not* a
second implementation of the engine and must not grow into one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest
from PIL import Image

from aetherfx.jsonrpc import ERROR_INVALID_PARAMS, AetherError, InMemoryTransport, JsonDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_BINARY = REPO_ROOT / "build" / "bin" / "aetherfx"
EXAMPLES_DIR = REPO_ROOT / "examples" / "effects"
REFERENCES_DIR = REPO_ROOT / "examples" / "references"
FIRE_AOE_REFERENCE = REFERENCES_DIR / "fire_aoe_synthetic.png"
SCHEMA_PATH = REPO_ROOT / "schema" / "ead.schema.json"


#: Documented tools the fake does not model; they simply echo their arguments.
ECHO_TOOLS: tuple[str, ...] = (
    "delete_effect",
    "list_effects",
    "set_active_effect",
    "set_effect_property",
    "describe_vocabulary",
    "get_effect_json",
    "delete_layer",
    "duplicate_layer",
    "set_layer_property",
    "create_emitter",
    "create_particle_system",
    "create_volume",
    "create_force",
    "create_field",
    "create_mesh",
    "create_curve",
    "create_trail",
    "create_beam",
    "create_light",
    "create_decal",
    "create_material",
    "create_event",
    "create_noise",
    "create_collider",
    "create_camera",
    "create_post_effect",
    "create_texture",
    "delete_node",
    "duplicate_node",
    "set_node_property",
    "disconnect_nodes",
    "set_parameters",
    "reset_parameter",
    "set_keyframe",
    "remove_keyframe",
    "clear_track",
    "remove_timeline_phase",
    "get_timeline",
    "simulate",
    "simulate_range",
    "step_simulation",
    "reset_simulation",
    "render_frame",
    "render_preview",
    "render_turntable",
    "inspect_render",
    "inspect_node",
    "inspect_statistics",
    "inspect_plan",
    "validate_effect",
    "compare_reference",
    "evaluate_effect",
    "save_effect",
    "load_effect",
    "export_effect",
    "redo",
)


class FakeEngine:
    """A minimal stand-in for the C++ ``Session``, driven over the fake transport.

    It understands the tools the planner emits plus a handful of read tools, and
    it enforces the two rules the tests care about: node ids are unique, and a
    connection may only reference nodes that already exist.
    """

    def __init__(self) -> None:
        self.effects: dict[str, JsonDict] = {}
        self.active: str | None = None
        self.layers: dict[str, JsonDict] = {}
        self.nodes: dict[str, JsonDict] = {}
        self.phases: list[JsonDict] = []
        self.connections: list[tuple[str, str, str]] = []
        self.undo_depth = 0

    # -- helpers -----------------------------------------------------------

    def _require_effect(self) -> str:
        if self.active is None:
            raise AetherError(ERROR_INVALID_PARAMS, "no active effect", aether_code="E015")
        return self.active

    def _node(self, node_id: str) -> JsonDict:
        node = self.nodes.get(node_id)
        if node is None:
            raise AetherError(
                ERROR_INVALID_PARAMS, f"unresolved reference: {node_id}", aether_code="E008", node=node_id
            )
        return node

    # -- tools -------------------------------------------------------------

    def create_effect(self, params: JsonDict) -> JsonDict:
        effect_id = f"effect_{len(self.effects)}"
        effect = {
            "effect_id": effect_id,
            "name": params.get("name", "Untitled"),
            "duration": params.get("duration", 2.0),
            "seed": params.get("seed", 1),
        }
        self.effects[effect_id] = effect
        self.active = effect_id
        self.undo_depth += 1
        return {"effect_id": effect_id, "effect": effect}

    def set_timeline_phase(self, params: JsonDict) -> JsonDict:
        self._require_effect()
        name = params["name"]
        self.phases = [phase for phase in self.phases if phase["name"] != name]
        self.phases.append({"name": name, "start": params["start"], "end": params["end"]})
        self.undo_depth += 1
        return {"timeline": {"phases": list(self.phases)}}

    def create_layer(self, params: JsonDict) -> JsonDict:
        self._require_effect()
        layer_id = params.get("id") or params["name"].lower().replace(" ", "_")
        layer = {"id": layer_id, "name": params["name"], "role": params["role"], "enabled": True}
        self.layers[layer_id] = layer
        self.undo_depth += 1
        return {"layer": layer}

    def create_node(self, params: JsonDict) -> JsonDict:
        self._require_effect()
        node_id = params.get("id") or f"{params['type']}_{len(self.nodes)}"
        if node_id in self.nodes:
            raise AetherError(
                ERROR_INVALID_PARAMS, f"duplicate node id: {node_id}", aether_code="E001", node=node_id
            )
        layer = params.get("layer")
        if layer is not None and layer not in self.layers:
            raise AetherError(
                ERROR_INVALID_PARAMS, f"unknown layer: {layer}", aether_code="E014", node=node_id
            )
        node = {
            "id": node_id,
            "type": params["type"],
            "layer": layer,
            "enabled": True,
            "parameters": dict(params.get("parameters") or {}),
            "inputs": dict(params.get("inputs") or {}),
        }
        self.nodes[node_id] = node
        self.undo_depth += 1
        return {"node": node, "diagnostics": {"errors": [], "warnings": []}}

    def connect_nodes(self, params: JsonDict) -> JsonDict:
        source = self._node(params["from"])
        target = self._node(params["to"])
        port = params["port"]
        if port in {"forces", "colliders", "sources", "targets"}:
            target["inputs"].setdefault(port, []).append(source["id"])
        else:
            target["inputs"][port] = source["id"]
        self.connections.append((source["id"], target["id"], port))
        self.undo_depth += 1
        return {"node": target, "diagnostics": {"errors": [], "warnings": []}}

    def set_parameter(self, params: JsonDict) -> JsonDict:
        node = self._node(params["node_id"])
        name = params["name"]
        if name.startswith("bogus"):
            raise AetherError(
                ERROR_INVALID_PARAMS,
                f"unknown parameter: {name}",
                aether_code="E004",
                node=node["id"],
                param=name,
            )
        node["parameters"][name] = params["value"]
        self.undo_depth += 1
        return {
            "node_id": node["id"],
            "name": name,
            "value": params["value"],
            "diagnostics": {"errors": [], "warnings": []},
        }

    def get_parameter(self, params: JsonDict) -> JsonDict:
        node = self._node(params["node_id"])
        name = params["name"]
        return {
            "value": node["parameters"].get(name),
            "default": None,
            "animated": False,
            "track": [],
            "spec": {"name": name, "type": "float"},
        }

    def inspect_graph(self, _params: JsonDict) -> JsonDict:
        effect_id = self._require_effect()
        effect = self.effects[effect_id]
        return {
            "name": effect["name"],
            "duration": effect["duration"],
            "seed": effect["seed"],
            "timeline": {"phases": list(self.phases)},
            "layers": [
                {**layer, "node_count": sum(1 for n in self.nodes.values() if n["layer"] == layer["id"])}
                for layer in self.layers.values()
            ],
            "nodes": [
                {"id": n["id"], "type": n["type"], "layer": n["layer"], "enabled": True, "inputs": n["inputs"]}
                for n in self.nodes.values()
            ],
            "diagnostics": {"errors": [], "warnings": []},
        }

    def undo(self, _params: JsonDict) -> JsonDict:
        self.undo_depth = max(0, self.undo_depth - 1)
        return {"ok": True, "remaining": self.undo_depth}

    def echo(self, params: JsonDict) -> JsonDict:
        """Accept a documented tool this fake does not model and echo its args."""
        return {"ok": True, "args": dict(params)}

    def handlers(self) -> dict[str, Callable[[JsonDict], Any]]:
        """Method name -> handler, for :class:`InMemoryTransport`.

        Modelled tools get real behaviour; every other tool in
        docs/AGENT_API.md echoes its arguments, which is enough to assert that
        the client sends the right argument names.
        """
        modelled: dict[str, Callable[[JsonDict], Any]] = {
            "create_effect": self.create_effect,
            "set_timeline_phase": self.set_timeline_phase,
            "create_layer": self.create_layer,
            "create_node": self.create_node,
            "connect_nodes": self.connect_nodes,
            "set_parameter": self.set_parameter,
            "get_parameter": self.get_parameter,
            "inspect_graph": self.inspect_graph,
            "undo": self.undo,
        }
        echoed = {name: self.echo for name in ECHO_TOOLS if name not in modelled}
        return {**echoed, **modelled}


#: Tool descriptors the fake engine advertises through ``tools/list``.
FAKE_TOOL_LIST: list[JsonDict] = [
    {
        "name": "describe_vocabulary",
        "description": "Return the node vocabulary plus texture_ops.",
        "input_schema": {"type": "object", "properties": {"node_type": {"type": "string"}}},
        "mutating": False,
        "category": "effect",
    },
    {
        "name": "create_effect",
        "description": "Create an effect and make it active.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "duration": {"type": "number", "default": 2},
                "seed": {"type": "integer", "default": 1},
                "template": {"type": "string"},
            },
            "required": ["name"],
        },
        "mutating": True,
        "category": "effect",
    },
    {
        "name": "create_node",
        "description": "Create a node of any vocabulary type.",
        "input_schema": {
            "type": "object",
            "properties": {"type": {"type": "string"}, "id": {"type": "string"}},
            "required": ["type"],
        },
        "mutating": True,
        "category": "graph",
    },
    {
        "name": "render_frame",
        "description": "Render one frame at time to a PNG.",
        "input_schema": {"type": "object", "properties": {"time": {"type": "number"}}, "required": ["time"]},
        "mutating": False,
        "category": "render",
    },
    {
        "name": "inspect_plan",
        "description": "Not part of the MCP surface; must be filtered out.",
        "input_schema": {"type": "object"},
        "mutating": False,
        "category": "inspect",
    },
]


@pytest.fixture
def fake_engine() -> FakeEngine:
    """A fresh :class:`FakeEngine`."""
    return FakeEngine()


@pytest.fixture
def fake_transport(fake_engine: FakeEngine) -> InMemoryTransport:
    """An :class:`InMemoryTransport` wired to :class:`FakeEngine`."""
    return InMemoryTransport(handlers=fake_engine.handlers(), tools=FAKE_TOOL_LIST)


@pytest.fixture
def make_image(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes small synthetic PNGs for the metric tests."""

    def factory(
        name: str,
        kind: str = "disc",
        centre: tuple[int, int] = (128, 128),
        radius: int = 60,
        color: tuple[int, int, int] = (255, 140, 30),
        size: tuple[int, int] = (256, 256),
    ) -> Path:
        width, height = size
        array = np.zeros((height, width, 3), dtype=np.uint8)
        if kind == "disc":
            ys, xs = np.mgrid[0:height, 0:width]
            mask = (xs - centre[0]) ** 2 + (ys - centre[1]) ** 2 <= radius * radius
            for channel, value in enumerate(color):
                array[..., channel] = np.where(mask, value, 0)
        elif kind == "fill":
            for channel, value in enumerate(color):
                array[..., channel] = value
        elif kind != "blank":  # pragma: no cover - guards typos in tests
            raise ValueError(f"unknown image kind: {kind}")
        path = tmp_path / name
        Image.fromarray(array).save(path)
        return path

    return factory
