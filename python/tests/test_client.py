"""Client behaviour: dynamic access, typed wrappers, tool cache, binary lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from aetherfx.client import BINARY_ENV_VAR, Client, ToolInfo, find_binary
from aetherfx.jsonrpc import ERROR_INVALID_PARAMS, AetherError, InMemoryTransport, TransportError

from conftest import FakeEngine


@pytest.fixture
def client(fake_transport: InMemoryTransport) -> Client:
    return Client(transport=fake_transport)


class TestTypedWrappers:
    def test_create_effect_sends_documented_defaults(self, client: Client, fake_transport: InMemoryTransport) -> None:
        result = client.create_effect(name="Fire AOE", duration=4.5)
        assert result["effect"]["name"] == "Fire AOE"
        assert fake_transport.calls[0] == ("create_effect", {"name": "Fire AOE", "duration": 4.5, "seed": 1})

    def test_optional_arguments_are_omitted_when_none(self, client: Client, fake_transport: InMemoryTransport) -> None:
        client.create_effect(name="X")
        assert "template" not in fake_transport.calls[0][1]

    def test_create_node_passes_parameters_and_layer(self, client: Client, fake_engine: FakeEngine) -> None:
        client.create_effect(name="X")
        client.create_layer(id="primary", name="Primary", role="primary")
        node = client.create_node(
            type="emitter", id="fire_wall", layer="primary", parameters={"shape": "ring", "radius": 3.0}
        )["node"]
        assert node["parameters"] == {"shape": "ring", "radius": 3.0}
        assert fake_engine.nodes["fire_wall"]["layer"] == "primary"

    def test_typed_create_wrapper_per_node_type(self, client: Client, fake_transport: InMemoryTransport) -> None:
        client.create_effect(name="X")
        client.create_layer(id="primary", name="Primary", role="primary")
        client.create_particle_system(id="wall_ps", layer="primary", parameters={"max_particles": 6000})
        method, args = fake_transport.calls[-1]
        assert method == "create_particle_system"
        assert args == {"id": "wall_ps", "layer": "primary", "parameters": {"max_particles": 6000}}

    def test_every_vocabulary_type_has_a_create_wrapper(self, client: Client) -> None:
        from aetherfx.ead import NODE_TYPES

        for node_type in NODE_TYPES:
            assert callable(getattr(Client, f"create_{node_type}", None)), node_type

    def test_connect_nodes_renames_from_underscore(self, client: Client, fake_transport: InMemoryTransport) -> None:
        client.create_effect(name="X")
        client.create_node(type="particle_system", id="ps")
        client.create_node(type="emitter", id="em")
        client.connect_nodes(from_="ps", to="em", port="particle")
        assert fake_transport.calls[-1] == ("connect_nodes", {"from": "ps", "to": "em", "port": "particle"})

    def test_connect_nodes_accepts_the_wire_name(self, client: Client, fake_transport: InMemoryTransport) -> None:
        client.create_effect(name="X")
        client.create_node(type="particle_system", id="ps")
        client.create_node(type="emitter", id="em")
        client.connect_nodes(**{"from": "ps", "to": "em", "port": "particle"})
        assert fake_transport.calls[-1][1]["from"] == "ps"

    def test_disconnect_nodes_omits_from_when_clearing(self, client: Client, fake_transport: InMemoryTransport) -> None:
        client.disconnect_nodes(to="em", port="particle")
        assert fake_transport.calls[-1] == ("disconnect_nodes", {"to": "em", "port": "particle"})

    def test_connect_nodes_requires_all_three_references(self, client: Client) -> None:
        with pytest.raises(TypeError, match="requires"):
            client.connect_nodes(to="em", port="particle")
        with pytest.raises(TypeError, match="unexpected keyword"):
            client.connect_nodes(from_="ps", to="em", port="particle", nope=1)

    def test_paths_are_stringified(self, client: Client, fake_transport: InMemoryTransport, tmp_path: Path) -> None:
        client.save_effect(path=tmp_path / "fire.json")
        assert fake_transport.calls[-1][1]["path"] == str(tmp_path / "fire.json")


class TestDynamicAccess:
    def test_unknown_tool_is_reachable(self, client: Client, fake_transport: InMemoryTransport) -> None:
        fake_transport.handlers["some_future_tool"] = lambda params: {"echo": params}
        assert client.some_future_tool(value=3) == {"echo": {"value": 3}}

    def test_call_is_equivalent_to_the_wrapper(self, client: Client) -> None:
        client.call("create_effect", name="A")
        client.create_effect(name="B")
        assert client.list_effects is not None

    def test_dynamic_access_accepts_an_argument_called_name(
        self, client: Client, fake_transport: InMemoryTransport
    ) -> None:
        """The tool name is positional-only, so `name` is never shadowed."""
        client.call("create_effect", name="Shadowed")
        assert fake_transport.calls[-1] == ("create_effect", {"name": "Shadowed"})

    def test_dunder_attributes_still_raise(self, client: Client) -> None:
        with pytest.raises(AttributeError):
            _ = client._not_a_tool


class TestToolsCache:
    def test_tools_are_parsed_into_toolinfo(self, client: Client) -> None:
        tools = client.tools()
        assert all(isinstance(tool, ToolInfo) for tool in tools)
        create = client.tool("create_effect")
        assert create is not None and create.mutating is True and create.category == "effect"

    def test_tools_are_cached_until_refreshed(self, client: Client, fake_transport: InMemoryTransport) -> None:
        client.tools()
        client.tools()
        assert sum(1 for method, _ in fake_transport.calls if method == "tools/list") == 1
        client.tools(refresh=True)
        assert sum(1 for method, _ in fake_transport.calls if method == "tools/list") == 2

    def test_tool_names_preserve_engine_order(self, client: Client) -> None:
        assert client.tool_names()[0] == "describe_vocabulary"


class TestErrors:
    def test_engine_errors_surface_with_context(self, client: Client) -> None:
        client.create_effect(name="X")
        client.create_node(type="emitter", id="fire")
        with pytest.raises(AetherError) as excinfo:
            client.set_parameter(node_id="fire", name="bogus_param", value=1)
        assert excinfo.value.code == ERROR_INVALID_PARAMS
        assert excinfo.value.aether_code == "E004"
        assert excinfo.value.param == "bogus_param"

    def test_missing_binary_raises_a_clear_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(BINARY_ENV_VAR, raising=False)
        bare = Client(binary="/definitely/not/here/aetherfx")
        assert bare.binary == "/definitely/not/here/aetherfx"
        with pytest.raises(TransportError):
            bare.ping()

    def test_available_never_raises(self) -> None:
        assert Client(binary="/definitely/not/here/aetherfx").available() is False


class TestLifecycle:
    def test_context_manager_closes_the_transport(self, fake_transport: InMemoryTransport) -> None:
        with Client(transport=fake_transport) as engine:
            engine.ping()
        assert fake_transport.closed is True

    def test_connected_is_false_before_first_use(self) -> None:
        assert Client(binary="/nope/aetherfx").connected is False


class TestBinaryLookup:
    def test_explicit_argument_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(BINARY_ENV_VAR, "/from/env")
        assert find_binary("/explicit") == "/explicit"

    def test_environment_variable_is_next(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(BINARY_ENV_VAR, "/from/env")
        assert find_binary() == "/from/env"

    def test_repo_build_directory_is_searched(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv(BINARY_ENV_VAR, raising=False)
        fake_repo = tmp_path / "repo"
        binary = fake_repo / "build" / "bin" / "aetherfx"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setattr("aetherfx.client._repo_root", lambda: fake_repo)
        monkeypatch.chdir(tmp_path)
        assert find_binary() == str(binary)

    def test_a_non_executable_file_is_ignored(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv(BINARY_ENV_VAR, raising=False)
        binary = tmp_path / "build" / "bin" / "aetherfx"
        binary.parent.mkdir(parents=True)
        binary.write_text("not executable")
        binary.chmod(0o644)
        monkeypatch.setattr("aetherfx.client._repo_root", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aetherfx.client.shutil.which", lambda name: None)
        assert find_binary() is None

    def test_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv(BINARY_ENV_VAR, raising=False)
        monkeypatch.setattr("aetherfx.client._repo_root", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aetherfx.client.shutil.which", lambda name: f"/usr/local/bin/{name}")
        assert find_binary() == "/usr/local/bin/aetherfx"
