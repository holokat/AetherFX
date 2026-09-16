// The JSON-RPC boundary: framing, ids, built-ins and the error mapping the
// Python client (python/aetherfx/jsonrpc.py) is written against.
#include <string>

#include <catch2/catch_test_macros.hpp>

#include "aether/tools/registry.hpp"

using namespace aether;
using namespace aether::tools;

namespace {

nlohmann::json request(const std::string& method, nlohmann::json params = nlohmann::json::object(),
                       nlohmann::json id = 1) {
    return {{"jsonrpc", "2.0"}, {"id", std::move(id)}, {"method", method}, {"params", std::move(params)}};
}

nlohmann::json answer(Session& session, const nlohmann::json& message) {
    return handle_jsonrpc(session, ToolRegistry::standard(), message);
}

}  // namespace

TEST_CASE("ping answers ok", "[tools][jsonrpc]") {
    Session session;
    const nlohmann::json response = answer(session, request("ping"));
    CHECK(response["jsonrpc"] == "2.0");
    CHECK(response["id"] == 1);
    CHECK(response["result"]["ok"] == true);
}

TEST_CASE("tools/list answers the registry", "[tools][jsonrpc]") {
    Session session;
    const nlohmann::json response = answer(session, request("tools/list"));
    REQUIRE(response.contains("result"));
    REQUIRE(response["result"]["tools"].is_array());
    CHECK(response["result"]["tools"].size() == ToolRegistry::standard().list().size());
    CHECK(response["result"]["tools"][0].contains("input_schema"));
}

TEST_CASE("shutdown answers ok so the client can close cleanly", "[tools][jsonrpc]") {
    Session session;
    CHECK(answer(session, request("shutdown"))["result"]["ok"] == true);
}

TEST_CASE("an unknown method is -32601", "[tools][jsonrpc]") {
    Session session;
    const nlohmann::json response = answer(session, request("definitely_not_a_tool"));
    REQUIRE(response.contains("error"));
    CHECK(response["error"]["code"] == -32601);
    CHECK(std::string(response["error"]["message"]).find("definitely_not_a_tool") != std::string::npos);
    CHECK_FALSE(response.contains("result"));
}

TEST_CASE("bad parameters are -32602 with an aether_code", "[tools][jsonrpc]") {
    Session session;
    answer(session, request("create_effect", {{"name", "Diagnostics"}, {"duration", 1.0}}));
    answer(session, request("create_node", {{"type", "emitter"}, {"id", "probe"}}));

    SECTION("unknown parameter carries E004 with node and param") {
        const nlohmann::json response = answer(
            session, request("set_parameter", {{"node_id", "probe"}, {"name", "definitely_not_a_parameter"}, {"value", 1}}));
        REQUIRE(response.contains("error"));
        CHECK(response["error"]["code"] == -32602);
        CHECK(response["error"]["data"]["aether_code"] == "E004");
        CHECK(response["error"]["data"]["node"] == "probe");
        CHECK(response["error"]["data"]["param"] == "definitely_not_a_parameter");
    }
    SECTION("a wrong value type carries E005") {
        const nlohmann::json response =
            answer(session, request("set_parameter", {{"node_id", "probe"}, {"name", "rate"}, {"value", "fast"}}));
        CHECK(response["error"]["code"] == -32602);
        CHECK(response["error"]["data"]["aether_code"] == "E005");
    }
    SECTION("an unknown node carries E008") {
        const nlohmann::json response =
            answer(session, request("set_parameter", {{"node_id", "ghost"}, {"name", "rate"}, {"value", 1.0}}));
        CHECK(response["error"]["code"] == -32602);
        CHECK(response["error"]["data"]["aether_code"] == "E008");
    }
    SECTION("a missing argument carries bad_argument") {
        const nlohmann::json response = answer(session, request("set_parameter", {{"node_id", "probe"}}));
        CHECK(response["error"]["code"] == -32602);
        CHECK(response["error"]["data"]["aether_code"] == "bad_argument");
    }
}

TEST_CASE("a non-object request is a parse error", "[tools][jsonrpc]") {
    Session session;
    for (const nlohmann::json& malformed : {nlohmann::json("not a request"), nlohmann::json(42),
                                            nlohmann::json::array({1, 2}), nlohmann::json()}) {
        const nlohmann::json response = handle_jsonrpc(session, ToolRegistry::standard(), malformed);
        REQUIRE(response.contains("error"));
        CHECK(response["error"]["code"] == -32700);
        CHECK(response["id"].is_null());
    }
}

TEST_CASE("an unparsable line is a parse error", "[tools][jsonrpc]") {
    Session session;
    const nlohmann::json response = handle_jsonrpc_line(session, ToolRegistry::standard(), "{not json");
    REQUIRE(response.contains("error"));
    CHECK(response["error"]["code"] == -32700);
}

TEST_CASE("a request without a method is an invalid request", "[tools][jsonrpc]") {
    Session session;
    const nlohmann::json response =
        handle_jsonrpc(session, ToolRegistry::standard(), {{"jsonrpc", "2.0"}, {"id", 7}});
    REQUIRE(response.contains("error"));
    CHECK(response["error"]["code"] == -32600);
    CHECK(response["id"] == 7);
}

TEST_CASE("ids are echoed verbatim for ints and strings", "[tools][jsonrpc]") {
    Session session;
    CHECK(answer(session, request("ping", nlohmann::json::object(), 17))["id"] == 17);
    CHECK(answer(session, request("ping", nlohmann::json::object(), "abc"))["id"] == "abc");
    CHECK(answer(session, request("ping", nlohmann::json::object(), nullptr))["id"].is_null());
}

TEST_CASE("missing or invalid params are treated as an empty object", "[tools][jsonrpc]") {
    Session session;
    CHECK(handle_jsonrpc(session, ToolRegistry::standard(),
                         {{"jsonrpc", "2.0"}, {"id", 1}, {"method", "list_effects"}})
              .contains("result"));
    CHECK(handle_jsonrpc(session, ToolRegistry::standard(),
                         {{"jsonrpc", "2.0"}, {"id", 1}, {"method", "list_effects"}, {"params", "nonsense"}})
              .contains("result"));
}

TEST_CASE("handle_jsonrpc never throws", "[tools][jsonrpc]") {
    Session session;
    CHECK_NOTHROW(answer(session, request("simulate")));                 // no active effect
    CHECK_NOTHROW(answer(session, request("render_frame", {{"time", 0}})));
    CHECK_NOTHROW(answer(session, request("inspect_render", {{"path", "/nope/none.png"}})));
}

TEST_CASE("results are always JSON objects", "[tools][jsonrpc]") {
    Session session;
    answer(session, request("create_effect", {{"name", "Shapes"}}));
    for (const char* method : {"list_effects", "get_effect_json", "inspect_graph", "get_timeline", "validate_effect"}) {
        const nlohmann::json response = answer(session, request(method));
        INFO(method);
        REQUIRE(response.contains("result"));
        CHECK(response["result"].is_object());
    }
}
