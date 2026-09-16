// The registry is the contract docs/AGENT_API.md describes: exactly these tools,
// every one of them documented well enough for an agent to call it blind.
#include <algorithm>
#include <set>
#include <string>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/tools/registry.hpp"

using namespace aether;
using namespace aether::tools;

namespace {

// docs/AGENT_API.md, section by section. Hard-coded on purpose: a tool that
// appears or disappears must be a deliberate edit of the document and this list.
const std::vector<std::string>& documented_tools() {
    static const std::vector<std::string> names{
        // effect
        "create_effect", "delete_effect", "list_effects", "set_active_effect", "set_effect_property",
        "describe_vocabulary", "get_effect_json",
        // graph
        "create_layer", "delete_layer", "duplicate_layer", "set_layer_property", "create_node", "create_emitter",
        "create_particle_system", "create_volume", "create_force", "create_field", "create_mesh", "create_curve",
        "create_trail", "create_beam", "create_light", "create_decal", "create_material", "create_event",
        "create_noise", "create_collider", "create_camera", "create_post_effect", "create_texture", "delete_node",
        "duplicate_node", "set_node_property", "connect_nodes", "disconnect_nodes",
        // parameters
        "set_parameter", "set_parameters", "get_parameter", "reset_parameter", "set_keyframe", "remove_keyframe",
        "clear_track",
        // timeline
        "set_timeline_phase", "remove_timeline_phase", "get_timeline",
        // simulate
        "simulate", "simulate_range", "step_simulation", "reset_simulation",
        // render
        "render_frame", "render_preview", "render_turntable", "inspect_render",
        // inspect
        "inspect_graph", "inspect_node", "inspect_statistics", "inspect_plan", "validate_effect",
        // evaluate
        "compare_reference", "evaluate_effect",
        // io
        "save_effect", "load_effect", "export_effect",
        // history
        "undo", "redo"};
    return names;
}

// The curated MCP surface (docs/AGENT_API.md "MCP surface"), which the Python
// server advertises; every name in it must exist here.
const std::vector<std::string>& mcp_surface() {
    static const std::vector<std::string> names{
        "describe_vocabulary", "create_effect",  "load_effect",     "save_effect",       "list_effects",
        "inspect_graph",       "inspect_node",   "create_layer",    "create_node",       "delete_node",
        "duplicate_layer",     "set_parameter",  "set_parameters",  "get_parameter",     "set_keyframe",
        "connect_nodes",       "disconnect_nodes", "set_timeline_phase", "simulate",     "render_frame",
        "render_preview",      "inspect_statistics", "compare_reference", "evaluate_effect", "export_effect",
        "undo",                "redo",           "get_effect_json"};
    return names;
}

}  // namespace

TEST_CASE("the registry holds exactly the documented tools", "[tools][registry]") {
    const ToolRegistry& registry = ToolRegistry::standard();
    std::set<std::string> registered;
    for (const ToolSpec& spec : registry.list()) registered.insert(spec.name);

    for (const std::string& name : documented_tools()) {
        INFO("documented tool: " << name);
        CHECK(registry.find(name) != nullptr);
    }
    std::set<std::string> documented(documented_tools().begin(), documented_tools().end());
    for (const std::string& name : registered) {
        INFO("registered tool not in docs/AGENT_API.md: " << name);
        CHECK(documented.count(name) == 1);
    }
    CHECK(registered.size() == documented.size());
}

TEST_CASE("the MCP surface is reachable", "[tools][registry]") {
    const ToolRegistry& registry = ToolRegistry::standard();
    for (const std::string& name : mcp_surface()) {
        INFO("MCP tool: " << name);
        CHECK(registry.find(name) != nullptr);
    }
}

TEST_CASE("every tool is documented for an agent", "[tools][registry]") {
    const std::set<std::string> categories{"effect",  "graph",   "parameters", "timeline", "simulate",
                                           "render",  "inspect", "evaluate",   "io",       "history"};
    for (const ToolSpec& spec : ToolRegistry::standard().list()) {
        INFO("tool: " << spec.name);
        CHECK(spec.description.size() >= 20);
        CHECK(categories.count(spec.category) == 1);
        REQUIRE(spec.input_schema.is_object());
        CHECK(spec.input_schema.value("type", std::string()) == "object");
        REQUIRE(spec.input_schema.contains("properties"));
        CHECK(spec.input_schema.at("properties").is_object());
        CHECK(spec.input_schema.contains("required"));
        for (const auto& [name, property] : spec.input_schema.at("properties").items()) {
            INFO("property: " << name);
            CHECK(property.contains("type"));
            CHECK(property.value("description", std::string()).size() >= 8);
        }
        for (const auto& required : spec.input_schema.at("required")) {
            INFO("required: " << required);
            CHECK(spec.input_schema.at("properties").contains(required.get<std::string>()));
        }
    }
}

TEST_CASE("list_json reports name, description, schema, mutating and category", "[tools][registry]") {
    const nlohmann::json listing = ToolRegistry::standard().list_json();
    REQUIRE(listing.contains("tools"));
    REQUIRE(listing["tools"].is_array());
    CHECK(listing["tools"].size() == documented_tools().size());
    for (const auto& entry : listing["tools"]) {
        CHECK(entry.contains("name"));
        CHECK(entry.contains("description"));
        CHECK(entry.contains("input_schema"));
        CHECK(entry.at("mutating").is_boolean());
        CHECK(entry.at("category").is_string());
    }
    // Sorted by the docs/AGENT_API.md section order, then by name.
    CHECK(listing["tools"].front().at("category") == "effect");
    CHECK(listing["tools"].back().at("category") == "history");
}

TEST_CASE("mutating tools are the ones that change the document", "[tools][registry]") {
    const ToolRegistry& registry = ToolRegistry::standard();
    const std::vector<std::string> mutating{"create_effect",   "delete_effect", "create_layer", "create_node",
                                            "create_emitter",  "delete_node",   "connect_nodes", "set_parameter",
                                            "set_keyframe",    "set_timeline_phase", "load_effect", "duplicate_layer"};
    for (const std::string& name : mutating) {
        INFO(name);
        REQUIRE(registry.find(name) != nullptr);
        CHECK(registry.find(name)->mutating);
    }
    const std::vector<std::string> read_only{"inspect_graph", "inspect_node", "simulate",   "render_frame",
                                             "save_effect",   "undo",         "redo",       "get_parameter",
                                             "evaluate_effect", "export_effect", "list_effects"};
    for (const std::string& name : read_only) {
        INFO(name);
        REQUIRE(registry.find(name) != nullptr);
        CHECK_FALSE(registry.find(name)->mutating);
    }
}

TEST_CASE("call validates required arguments and primitive types", "[tools][registry]") {
    const ToolRegistry& registry = ToolRegistry::standard();
    Session session;

    SECTION("unknown tool") {
        CHECK_THROWS_AS(registry.call(session, "no_such_tool", nlohmann::json::object()), Error);
        try {
            registry.call(session, "no_such_tool", nlohmann::json::object());
        } catch (const Error& e) {
            CHECK(e.code() == "unknown_tool");
        }
    }
    SECTION("missing required argument") {
        try {
            registry.call(session, "create_node", nlohmann::json::object());
            FAIL("expected a bad_argument error");
        } catch (const Error& e) {
            CHECK(e.code() == "bad_argument");
            CHECK(std::string(e.what()).find("type") != std::string::npos);
        }
    }
    SECTION("wrong primitive type") {
        try {
            registry.call(session, "create_effect", {{"name", "x"}, {"duration", "two seconds"}});
            FAIL("expected a bad_argument error");
        } catch (const Error& e) {
            CHECK(e.code() == "bad_argument");
            CHECK(std::string(e.what()).find("duration") != std::string::npos);
            CHECK(std::string(e.what()).find("number") != std::string::npos);
        }
    }
    SECTION("unknown argument") {
        try {
            registry.call(session, "create_effect", {{"name", "x"}, {"colour", "red"}});
            FAIL("expected a bad_argument error");
        } catch (const Error& e) {
            CHECK(e.code() == "bad_argument");
            CHECK(std::string(e.what()).find("colour") != std::string::npos);
        }
    }
    SECTION("integers are accepted where a number is required") {
        CHECK_NOTHROW(registry.call(session, "create_effect", {{"name", "x"}, {"duration", 2}}));
    }
    SECTION("a null optional means absent") {
        CHECK_NOTHROW(registry.call(session, "create_effect", {{"name", "x"}, {"template", nullptr}}));
    }
}

TEST_CASE("tools without an active effect fail with a helpful code", "[tools][registry]") {
    Session session;
    try {
        ToolRegistry::standard().call(session, "inspect_graph", nlohmann::json::object());
        FAIL("expected no_active_effect");
    } catch (const Error& e) {
        CHECK(e.code() == "no_active_effect");
    }
}
