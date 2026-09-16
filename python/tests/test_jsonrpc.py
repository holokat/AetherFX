"""JSON-RPC framing and error mapping."""

from __future__ import annotations

import json

import pytest

from aetherfx.jsonrpc import (
    ERROR_INTERNAL,
    ERROR_INVALID_PARAMS,
    ERROR_METHOD_NOT_FOUND,
    AetherError,
    InMemoryTransport,
    TransportError,
    build_request,
    parse_response,
)


class TestEnvelope:
    def test_request_envelope_matches_the_protocol(self) -> None:
        request = build_request(7, "create_effect", {"name": "Fire"})
        assert request == {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "create_effect",
            "params": {"name": "Fire"},
        }

    def test_params_are_always_an_object(self) -> None:
        assert build_request(1, "ping", None)["params"] == {}

    def test_one_message_per_line_utf8(self) -> None:
        line = json.dumps(build_request(1, "create_effect", {"name": "Feuerball"}), ensure_ascii=False)
        assert "\n" not in line
        assert json.loads(line)["params"]["name"] == "Feuerball"


class TestParseResponse:
    def test_result_object_is_returned(self) -> None:
        assert parse_response({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}, "ping") == {"ok": True}

    def test_null_result_becomes_empty_dict(self) -> None:
        assert parse_response({"jsonrpc": "2.0", "id": 1, "result": None}, "ping") == {}

    def test_error_member_maps_to_aether_error_with_data(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": ERROR_INVALID_PARAMS,
                "message": "unknown parameter: rate",
                "data": {"aether_code": "E004", "node": "flames", "param": "rate"},
            },
        }
        with pytest.raises(AetherError) as excinfo:
            parse_response(message, "set_parameter")
        error = excinfo.value
        assert error.code == ERROR_INVALID_PARAMS
        assert error.message == "unknown parameter: rate"
        assert (error.aether_code, error.node, error.param) == ("E004", "flames", "rate")
        assert "E004" in str(error) and "flames" in str(error)

    def test_error_data_keys_are_optional(self) -> None:
        message = {"jsonrpc": "2.0", "id": 1, "error": {"code": ERROR_INTERNAL, "message": "boom"}}
        with pytest.raises(AetherError) as excinfo:
            parse_response(message, "simulate")
        assert excinfo.value.aether_code is None
        assert excinfo.value.node is None
        assert excinfo.value.param is None

    def test_missing_result_and_error_is_a_transport_error(self) -> None:
        with pytest.raises(TransportError, match="neither 'result' nor 'error'"):
            parse_response({"jsonrpc": "2.0", "id": 1}, "ping")

    def test_non_object_result_is_a_transport_error(self) -> None:
        with pytest.raises(TransportError, match="must be a JSON object"):
            parse_response({"jsonrpc": "2.0", "id": 1, "result": [1, 2]}, "list_effects")


class TestInMemoryTransport:
    def test_builtin_ping(self) -> None:
        assert InMemoryTransport().request("ping") == {"ok": True}

    def test_builtin_tools_list_advertises_handlers(self) -> None:
        transport = InMemoryTransport(handlers={"simulate": lambda _params: {"statistics": {}}})
        names = [tool["name"] for tool in transport.request("tools/list")["tools"]]
        assert names == ["simulate"]

    def test_an_explicit_tool_list_is_the_whole_list(self) -> None:
        """A fake engine may implement more methods than it advertises."""
        transport = InMemoryTransport(
            handlers={"simulate": lambda _params: {}, "undo": lambda _params: {"ok": True}},
            tools=[{"name": "simulate", "description": "documented", "category": "simulate"}],
        )
        tools = transport.request("tools/list")["tools"]
        assert [tool["name"] for tool in tools] == ["simulate"]
        assert tools[0]["description"] == "documented"
        assert transport.request("undo") == {"ok": True}

    def test_shutdown_closes_the_transport(self) -> None:
        transport = InMemoryTransport()
        assert transport.request("shutdown") == {"ok": True}
        assert transport.closed is True

    def test_unknown_method_is_method_not_found(self) -> None:
        with pytest.raises(AetherError) as excinfo:
            InMemoryTransport().request("nope")
        assert excinfo.value.code == ERROR_METHOD_NOT_FOUND

    def test_handler_errors_round_trip_through_the_envelope(self) -> None:
        def handler(_params: dict) -> dict:
            raise AetherError(
                ERROR_INVALID_PARAMS, "value out of range", aether_code="E006", node="wall", param="radius"
            )

        transport = InMemoryTransport(handlers={"set_parameter": handler})
        with pytest.raises(AetherError) as excinfo:
            transport.request("set_parameter", {"node_id": "wall", "name": "radius", "value": -1})
        assert (excinfo.value.aether_code, excinfo.value.node, excinfo.value.param) == ("E006", "wall", "radius")

    def test_calls_are_recorded_in_order(self) -> None:
        transport = InMemoryTransport(handlers={"simulate": lambda params: {"time": params.get("time")}})
        transport.request("simulate", {"time": 1.0})
        transport.request("ping")
        assert transport.calls == [("simulate", {"time": 1.0}), ("ping", {})]

    def test_non_serialisable_results_are_rejected(self) -> None:
        transport = InMemoryTransport(handlers={"bad": lambda _params: {"value": object()}})
        with pytest.raises(TypeError):
            transport.request("bad")

    def test_ids_increase_monotonically(self) -> None:
        transport = InMemoryTransport()
        transport.request("ping")
        transport.request("ping")
        assert transport._next_id == 2  # noqa: SLF001 - asserting the framing contract
