"""EAD -> tool calls, and applying a plan against the fake engine."""

from __future__ import annotations

import pytest

from aetherfx.ai.mock import canned_fire_aoe_ead
from aetherfx.client import Client
from aetherfx.ead import (
    EffectAnalysisDocument,
    GlobalAnalysis,
    ImplementationPlanEntry,
    LayerAnalysis,
    PlannedNode,
    Source,
)
from aetherfx.jsonrpc import ERROR_INVALID_PARAMS, AetherError, InMemoryTransport
from aetherfx.planner import (
    PlanExecutionError,
    ToolCall,
    apply_plan,
    ead_to_effect,
    ead_to_tool_calls,
)

from conftest import FakeEngine


@pytest.fixture
def calls() -> list[ToolCall]:
    return ead_to_tool_calls(canned_fire_aoe_ead("refs/fire_aoe.png"))


def named(calls: list[ToolCall], name: str) -> list[ToolCall]:
    return [call for call in calls if call.name == name]


class TestOrdering:
    def test_create_effect_is_first(self, calls: list[ToolCall]) -> None:
        assert calls[0].name == "create_effect"
        assert calls[0].args["duration"] == pytest.approx(4.5)

    def test_effect_name_comes_from_the_source_path(self, calls: list[ToolCall]) -> None:
        assert calls[0].args["name"] == "Fire Aoe"

    def test_effect_name_prefers_the_prompt(self) -> None:
        document = canned_fire_aoe_ead("refs/fire_aoe.png", "Molten ring of wrath")
        assert ead_to_tool_calls(document)[0].args["name"] == "Molten ring of wrath"

    def test_effect_name_falls_back_to_the_category(self) -> None:
        document = EffectAnalysisDocument(
            source=Source(kind="text"),
            **{"global": GlobalAnalysis(effect_category="ice_nova", probable_duration_s=2.0)},
        )
        assert ead_to_tool_calls(document)[0].args["name"] == "Ice Nova"

    def test_timeline_phases_follow_the_effect(self, calls: list[ToolCall]) -> None:
        phases = named(calls, "set_timeline_phase")
        assert [call.args["name"] for call in phases] == [
            "anticipation",
            "activation",
            "peak",
            "sustain",
            "decay",
        ]
        first_layer = next(index for index, call in enumerate(calls) if call.name == "create_layer")
        assert all(calls.index(call) < first_layer for call in phases)

    def test_layers_come_before_their_nodes(self, calls: list[ToolCall]) -> None:
        seen_layers: set[str] = set()
        for call in calls:
            if call.name == "create_layer":
                seen_layers.add(call.args["id"])
            elif call.name == "create_node":
                assert call.args["layer"] in seen_layers

    def test_connections_reference_existing_ids(self, calls: list[ToolCall]) -> None:
        created: set[str] = set()
        for call in calls:
            if call.name == "create_node":
                created.add(call.args["id"])
            elif call.name == "connect_nodes":
                assert call.args["from"] in created
                assert call.args["to"] in created

    def test_plan_is_deterministic(self) -> None:
        first = [(c.name, c.args) for c in ead_to_tool_calls(canned_fire_aoe_ead("a.png"))]
        second = [(c.name, c.args) for c in ead_to_tool_calls(canned_fire_aoe_ead("a.png"))]
        assert first == second


class TestLayersAndNodes:
    def test_one_layer_per_plan_entry_with_the_analysed_role(self, calls: list[ToolCall]) -> None:
        layers = named(calls, "create_layer")
        assert [call.args["id"] for call in layers] == [
            "telegraph",
            "ignition",
            "primary",
            "secondary",
            "interaction",
            "aftermath",
        ]
        assert all(call.args["role"] == call.args["id"] for call in layers)

    def test_layer_role_falls_back_to_custom_without_an_analysis(self) -> None:
        document = EffectAnalysisDocument(
            source=Source(kind="text"),
            **{"global": GlobalAnalysis(effect_category="aura", probable_duration_s=1.0)},
            implementation_plan=[
                ImplementationPlanEntry(layer="mystery", nodes=[PlannedNode(type="light")])
            ],
        )
        layer = named(ead_to_tool_calls(document), "create_layer")[0]
        assert layer.args["role"] == "custom"

    def test_node_ids_are_layer_plus_type_and_unique(self, calls: list[ToolCall]) -> None:
        ids = [call.args["id"] for call in named(calls, "create_node")]
        assert len(ids) == len(set(ids))
        assert "primary_emitter" in ids and "primary_emitter_2" in ids

    def test_node_ids_are_valid_engine_identifiers(self, calls: list[ToolCall]) -> None:
        import re

        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for call in named(calls, "create_node"):
            assert pattern.match(call.args["id"]), call.args["id"]
        for call in named(calls, "create_layer"):
            assert pattern.match(call.args["id"]), call.args["id"]

    def test_planned_parameters_pass_through_untouched(self, calls: list[ToolCall]) -> None:
        emitter = next(
            call for call in named(calls, "create_node") if call.args["id"] == "primary_emitter"
        )
        assert emitter.args["parameters"]["shape"] == "ring"
        assert emitter.args["parameters"]["radius"] == 3.0

    def test_explicit_planned_ids_are_honoured(self) -> None:
        document = EffectAnalysisDocument(
            source=Source(kind="text"),
            **{"global": GlobalAnalysis(effect_category="beam", probable_duration_s=1.0)},
            layers=[LayerAnalysis(id="primary", semantic_role="primary", primitive="beam")],
            implementation_plan=[
                ImplementationPlanEntry(
                    layer="primary", nodes=[PlannedNode.model_validate({"type": "beam", "id": "zap"})]
                )
            ],
        )
        assert named(ead_to_tool_calls(document), "create_node")[0].args["id"] == "zap"


class TestWiring:
    def test_particle_system_feeds_every_emitter(self, calls: list[ToolCall]) -> None:
        connections = {
            (call.args["from"], call.args["to"], call.args["port"]) for call in named(calls, "connect_nodes")
        }
        assert ("primary_particle_system", "primary_emitter", "particle") in connections
        assert ("primary_particle_system", "primary_emitter_2", "particle") in connections

    def test_material_and_texture_use_single_ports(self, calls: list[ToolCall]) -> None:
        connections = {
            (call.args["from"], call.args["to"], call.args["port"]) for call in named(calls, "connect_nodes")
        }
        assert ("primary_material", "primary_particle_system", "material") in connections
        assert ("primary_texture", "primary_particle_system", "sprite") in connections

    def test_every_force_is_attached_to_the_particle_system(self, calls: list[ToolCall]) -> None:
        forces = {
            call.args["from"]
            for call in named(calls, "connect_nodes")
            if call.args["port"] == "forces" and call.args["to"] == "primary_particle_system"
        }
        assert forces == {"primary_force", "primary_force_2"}

    def test_texture_feeds_a_decal_through_its_own_port(self, calls: list[ToolCall]) -> None:
        connections = {
            (call.args["from"], call.args["to"], call.args["port"]) for call in named(calls, "connect_nodes")
        }
        assert ("telegraph_texture", "telegraph_decal", "texture") in connections
        assert ("aftermath_texture", "aftermath_decal", "texture") in connections

    def test_wiring_never_crosses_plan_entries(self, calls: list[ToolCall]) -> None:
        layer_of = {call.args["id"]: call.args["layer"] for call in named(calls, "create_node")}
        for call in named(calls, "connect_nodes"):
            assert layer_of[call.args["from"]] == layer_of[call.args["to"]]

    def test_an_entry_without_a_consumer_emits_no_connections(self) -> None:
        document = EffectAnalysisDocument(
            source=Source(kind="text"),
            **{"global": GlobalAnalysis(effect_category="aura", probable_duration_s=1.0)},
            layers=[LayerAnalysis(id="interaction", semantic_role="interaction", primitive="light")],
            implementation_plan=[
                ImplementationPlanEntry(
                    layer="interaction",
                    nodes=[PlannedNode(type="light"), PlannedNode(type="post_effect")],
                )
            ],
        )
        assert named(ead_to_tool_calls(document), "connect_nodes") == []


class TestApplyPlan:
    def test_plan_builds_the_graph_on_the_fake_engine(
        self, fake_transport: InMemoryTransport, fake_engine: FakeEngine, calls: list[ToolCall]
    ) -> None:
        client = Client(transport=fake_transport)
        results = apply_plan(client, calls)
        assert len(results) == len(calls)
        assert fake_engine.effects[fake_engine.active]["name"] == "Fire Aoe"
        assert len(fake_engine.layers) == 6
        assert len(fake_engine.nodes) == len(named(calls, "create_node"))
        assert fake_engine.nodes["primary_emitter"]["inputs"]["particle"] == "primary_particle_system"
        assert fake_engine.nodes["primary_particle_system"]["inputs"]["forces"] == [
            "primary_force",
            "primary_force_2",
        ]

    def test_ead_to_effect_is_the_same_thing(
        self, fake_transport: InMemoryTransport, fake_engine: FakeEngine
    ) -> None:
        client = Client(transport=fake_transport)
        ead_to_effect(client, canned_fire_aoe_ead("refs/fire_aoe.png"))
        assert fake_engine.active is not None
        assert len(fake_engine.layers) == 6

    def test_stops_at_the_first_error_with_context(self, fake_engine: FakeEngine) -> None:
        def failing_create_node(params: dict) -> dict:
            if params.get("type") == "force":
                raise AetherError(
                    ERROR_INVALID_PARAMS,
                    "unknown parameter: force_type",
                    aether_code="E004",
                    node=params.get("id"),
                    param="force_type",
                )
            return fake_engine.create_node(params)

        handlers = fake_engine.handlers()
        handlers["create_node"] = failing_create_node
        client = Client(transport=InMemoryTransport(handlers=handlers))
        plan = ead_to_tool_calls(canned_fire_aoe_ead("refs/fire_aoe.png"))

        with pytest.raises(PlanExecutionError) as excinfo:
            apply_plan(client, plan)
        failure = excinfo.value
        assert failure.call.name == "create_node"
        assert failure.call.args["type"] == "force"
        assert isinstance(failure.error, AetherError)
        assert failure.error.aether_code == "E004"
        assert len(failure.results) == failure.index
        assert plan[failure.index] is failure.call
        assert "create_node" in str(failure)
