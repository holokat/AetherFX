// The authoring workflow an agent actually performs, end to end, through the
// registry: build a graph, tune it, inspect it, undo it, save it.
#include <filesystem>
#include <string>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/tools/registry.hpp"

using namespace aether;
using namespace aether::tools;
using Catch::Approx;

namespace {

std::filesystem::path output_dir() {
    std::filesystem::path dir(AETHER_TEST_OUTPUT_DIR);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

std::filesystem::path example(const std::string& name) {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / name;
}

nlohmann::json call(Session& session, const std::string& tool, nlohmann::json args = nlohmann::json::object()) {
    return ToolRegistry::standard().call(session, tool, args);
}

// True when the diagnostics carry a finding with this code.
bool has_code(const nlohmann::json& diagnostics, const std::string& code) {
    if (!diagnostics.is_object() || !diagnostics.contains("items")) return false;
    for (const auto& item : diagnostics["items"])
        if (item.value("code", std::string()) == code) return true;
    return false;
}

// A minimal but complete fire: layer, emitter, particle system, upward force.
Session make_fire_session() {
    Session session(output_dir());
    call(session, "create_effect", {{"name", "Workflow Fire"}, {"duration", 1.5}, {"seed", 7}});
    call(session, "create_layer", {{"name", "Primary"}, {"role", "primary"}});
    call(session, "create_particle_system",
         {{"id", "flame_ps"}, {"layer", "primary"}, {"parameters", {{"lifetime", 0.6}, {"size", 0.2}}}});
    call(session, "create_emitter",
         {{"id", "flames"}, {"layer", "primary"}, {"parameters", {{"shape", "sphere"}, {"radius", 0.4}, {"rate", 120}}}});
    call(session, "connect_nodes", {{"from", "flame_ps"}, {"to", "flames"}, {"port", "particle"}});
    return session;
}

}  // namespace

TEST_CASE("an agent builds a working graph from nothing", "[tools][session]") {
    Session session(output_dir());

    const nlohmann::json created = call(session, "create_effect", {{"name", "Workflow Fire"}, {"duration", 1.5}});
    CHECK(created["effect_id"] == "fx_1");
    CHECK(created["effect"]["name"] == "Workflow Fire");
    CHECK(created["effect"]["duration"] == Approx(1.5));

    const nlohmann::json layer = call(session, "create_layer", {{"name", "Primary"}, {"role", "primary"}});
    CHECK(layer["layer"]["id"] == "primary");
    CHECK(layer["layer"]["role"] == "primary");

    const nlohmann::json system = call(session, "create_particle_system", {{"id", "flame_ps"}, {"layer", "primary"}});
    CHECK(system["node"]["type"] == "particle_system");
    CHECK(system["diagnostics"]["errors"] == 0);

    const nlohmann::json emitter =
        call(session, "create_emitter",
             {{"id", "flames"}, {"layer", "primary"}, {"parameters", {{"shape", "sphere"}, {"rate", 120}}}});
    CHECK(emitter["node"]["id"] == "flames");
    CHECK(emitter["node"]["parameters"]["rate"] == Approx(120.0));

    const nlohmann::json connected =
        call(session, "connect_nodes", {{"from", "flame_ps"}, {"to", "flames"}, {"port", "particle"}});
    CHECK(connected["node"]["inputs"]["particle"] == "flame_ps");
    CHECK(connected["diagnostics"]["errors"] == 0);

    CHECK(call(session, "validate_effect")["ok"] == true);
}

TEST_CASE("node ids default to a unique name derived from the type", "[tools][session]") {
    Session session(output_dir());
    call(session, "create_effect", {{"name", "Ids"}});
    CHECK(call(session, "create_emitter")["node"]["id"] == "emitter");
    CHECK(call(session, "create_emitter")["node"]["id"] == "emitter_2");
    CHECK(call(session, "create_node", {{"type", "force"}})["node"]["id"] == "force");
    CHECK_THROWS_AS(call(session, "create_emitter", {{"id", "emitter"}}), Error);
}

TEST_CASE("connect_nodes appends on multi ports and replaces on single ports", "[tools][session]") {
    Session session = make_fire_session();
    call(session, "create_force", {{"id", "gravity"}, {"parameters", {{"force_type", "gravity"}}}});
    call(session, "create_force", {{"id", "wind"}, {"parameters", {{"force_type", "wind"}}}});

    call(session, "connect_nodes", {{"from", "gravity"}, {"to", "flame_ps"}, {"port", "forces"}});
    const nlohmann::json two = call(session, "connect_nodes", {{"from", "wind"}, {"to", "flame_ps"}, {"port", "forces"}});
    CHECK(two["node"]["inputs"]["forces"] == nlohmann::json::array({"gravity", "wind"}));

    // A repeated connection is not duplicated.
    const nlohmann::json again =
        call(session, "connect_nodes", {{"from", "wind"}, {"to", "flame_ps"}, {"port", "forces"}});
    CHECK(again["node"]["inputs"]["forces"].size() == 2);

    call(session, "create_particle_system", {{"id", "other_ps"}});
    const nlohmann::json replaced =
        call(session, "connect_nodes", {{"from", "other_ps"}, {"to", "flames"}, {"port", "particle"}});
    CHECK(replaced["node"]["inputs"]["particle"] == "other_ps");

    SECTION("an unknown port is E017") {
        try {
            call(session, "connect_nodes", {{"from", "gravity"}, {"to", "flame_ps"}, {"port", "not_a_port"}});
            FAIL("expected E017");
        } catch (const Error& e) {
            CHECK(e.code() == "E017");
        }
    }
    SECTION("an unresolved source surfaces as an E008 diagnostic, not an exception") {
        const nlohmann::json result =
            call(session, "connect_nodes", {{"from", "ghost"}, {"to", "flame_ps"}, {"port", "forces"}});
        CHECK(has_code(result["diagnostics"], "E008"));
    }
    SECTION("a wrongly typed source surfaces as an E009 diagnostic") {
        const nlohmann::json result =
            call(session, "connect_nodes", {{"from", "flames"}, {"to", "flame_ps"}, {"port", "forces"}});
        CHECK(has_code(result["diagnostics"], "E009"));
    }
    SECTION("disconnect_nodes removes one reference or clears the port") {
        call(session, "disconnect_nodes", {{"to", "flame_ps"}, {"port", "forces"}, {"from", "gravity"}});
        CHECK(call(session, "inspect_node", {{"node_id", "flame_ps"}})["node"]["inputs"]["forces"] == "wind");
        const nlohmann::json cleared = call(session, "disconnect_nodes", {{"to", "flame_ps"}, {"port", "forces"}});
        CHECK_FALSE(cleared["node"]["inputs"].contains("forces"));
    }
}

TEST_CASE("set_parameter validates against the spec", "[tools][session]") {
    Session session = make_fire_session();

    SECTION("a valid value is stored and reported back") {
        const nlohmann::json result =
            call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"value", 800.0}});
        CHECK(result["value"] == Approx(800.0));
        CHECK(result["diagnostics"]["errors"] == 0);
        CHECK(call(session, "get_parameter", {{"node_id", "flames"}, {"name", "rate"}})["value"] == Approx(800.0));
    }
    SECTION("an invalid enum is kept and reported as an E007 diagnostic") {
        const nlohmann::json result =
            call(session, "set_parameter", {{"node_id", "flames"}, {"name", "shape"}, {"value", "triangle"}});
        CHECK(has_code(result["diagnostics"], "E007"));
        CHECK(result["diagnostics"]["errors"] == 1);
    }
    SECTION("an out of range value is reported as an E006 diagnostic") {
        const nlohmann::json result =
            call(session, "set_parameter", {{"node_id", "flames"}, {"name", "radius"}, {"value", -4.0}});
        CHECK(has_code(result["diagnostics"], "E006"));
    }
    SECTION("a wrong type is an E005 error with an example in the message") {
        try {
            call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"value", "fast"}});
            FAIL("expected E005");
        } catch (const Error& e) {
            CHECK(e.code() == "E005");
            CHECK(std::string(e.what()).find("float") != std::string::npos);
            CHECK(std::string(e.what()).find("example") != std::string::npos);
        }
    }
    SECTION("an unknown name is an E004 error naming the node and parameter") {
        try {
            call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rat"}, {"value", 1.0}});
            FAIL("expected E004");
        } catch (const ToolError& e) {
            CHECK(e.code() == "E004");
            CHECK(e.node() == "flames");
            CHECK(e.param() == "rat");
            CHECK(std::string(e.what()).find("rate") != std::string::npos);  // the suggestion
        }
    }
    SECTION("set_parameters applies all or nothing") {
        CHECK_THROWS_AS(call(session, "set_parameters",
                             {{"node_id", "flames"}, {"parameters", {{"rate", 50.0}, {"nonsense", 1}}}}),
                        Error);
        CHECK(call(session, "get_parameter", {{"node_id", "flames"}, {"name", "rate"}})["value"] == Approx(120.0));
        call(session, "set_parameters", {{"node_id", "flames"}, {"parameters", {{"rate", 50.0}, {"velocity", 3.0}}}});
        CHECK(call(session, "get_parameter", {{"node_id", "flames"}, {"name", "velocity"}})["value"] == Approx(3.0));
    }
    SECTION("reset_parameter falls back to the spec default") {
        call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"value", 800.0}});
        call(session, "reset_parameter", {{"node_id", "flames"}, {"name", "rate"}});
        const nlohmann::json parameter = call(session, "get_parameter", {{"node_id", "flames"}, {"name", "rate"}});
        CHECK(parameter["value"] == parameter["default"]);
        CHECK(parameter["set"] == false);
    }
}

TEST_CASE("keyframes animate a parameter over effect time", "[tools][session]") {
    Session session = make_fire_session();
    call(session, "set_keyframe", {{"node_id", "flames"}, {"name", "rate"}, {"time", 0.0}, {"value", 0.0}});
    const nlohmann::json keyed =
        call(session, "set_keyframe",
             {{"node_id", "flames"}, {"name", "rate"}, {"time", 1.0}, {"value", 400.0}, {"interp", "smooth"}});
    CHECK(keyed["track"].size() == 2);
    CHECK(keyed["animatable"] == true);

    const nlohmann::json at_half = call(session, "get_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"time", 0.5}});
    CHECK(at_half["animated"] == true);
    CHECK(at_half["track"].size() == 2);
    CHECK(at_half["value"].get<double>() > 0.0);
    CHECK(at_half["value"].get<double>() < 400.0);
    CHECK(at_half["spec"]["type"] == "float");
    CHECK(at_half["spec"]["animatable"] == true);
    CHECK(at_half["spec"]["units"] == "1/s");

    SECTION("keyframing a non-animatable parameter warns with W006") {
        const nlohmann::json result = call(
            session, "set_keyframe", {{"node_id", "flames"}, {"name", "max_particles"}, {"time", 0.2}, {"value", 10}});
        CHECK(result["animatable"] == false);
        CHECK(has_code(result["diagnostics"], "W006"));
        CHECK(result["diagnostics"]["errors"] == 0);
    }
    SECTION("remove_keyframe and clear_track undo the animation") {
        CHECK(call(session, "remove_keyframe", {{"node_id", "flames"}, {"name", "rate"}, {"time", 1.0}})["removed"] ==
              true);
        const nlohmann::json cleared =
            call(session, "clear_track", {{"node_id", "flames"}, {"name", "rate"}, {"time", 0.0}});
        CHECK(cleared["cleared"] == true);
        CHECK(call(session, "get_parameter", {{"node_id", "flames"}, {"name", "rate"}})["animated"] == false);
    }
    SECTION("set_parameter clears the track") {
        call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"value", 42.0}});
        const nlohmann::json parameter = call(session, "get_parameter", {{"node_id", "flames"}, {"name", "rate"}});
        CHECK(parameter["animated"] == false);
        CHECK(parameter["value"] == Approx(42.0));
    }
}

TEST_CASE("inspect_graph and inspect_node describe the graph", "[tools][session]") {
    Session session = make_fire_session();
    call(session, "create_force", {{"id", "buoyancy"}, {"parameters", {{"force_type", "buoyancy"}}}});
    call(session, "connect_nodes", {{"from", "buoyancy"}, {"to", "flame_ps"}, {"port", "forces"}});

    const nlohmann::json graph = call(session, "inspect_graph");
    CHECK(graph["name"] == "Workflow Fire");
    CHECK(graph["duration"] == Approx(1.5));
    CHECK(graph["seed"] == 7);
    CHECK(graph["nodes"].size() == 3);
    CHECK(graph["diagnostics"]["errors"] == 0);
    bool found_layer = false;
    for (const auto& layer : graph["layers"])
        if (layer["id"] == "primary") {
            found_layer = true;
            CHECK(layer["node_count"] == 2);
        }
    CHECK(found_layer);
    for (const auto& node : graph["nodes"]) {
        if (node["id"] != "flames") continue;
        CHECK(node["type"] == "emitter");
        CHECK(node["layer"] == "primary");
        CHECK(node["enabled"] == true);
        CHECK(node["inputs"]["particle"] == "flame_ps");
        CHECK(node["tier"].is_string());  // the compiler picked a tier
    }

    const nlohmann::json inspected = call(session, "inspect_node", {{"node_id", "flame_ps"}});
    CHECK(inspected["node"]["id"] == "flame_ps");
    CHECK(inspected["spec"]["parameters"].contains("lifetime"));
    CHECK(inspected["effective_parameters"]["blend"] == "additive");   // a default, filled in
    CHECK(inspected["effective_parameters"]["lifetime"] == Approx(0.6));
    CHECK(inspected["resolved_inputs"]["forces"][0]["node"] == "buoyancy");
    CHECK(inspected["resolved_inputs"]["forces"][0]["exists"] == true);
    CHECK(inspected["resolved_inputs"]["forces"][0]["type"] == "force");
    CHECK(inspected["consumers"] == nlohmann::json::array({"flames"}));
    CHECK(inspected["window"]["end"] == Approx(1.5));

    const nlohmann::json verbose = call(session, "inspect_graph", {{"verbose", true}});
    for (const auto& node : verbose["nodes"])
        if (node["id"] == "flames") CHECK(node["parameters"].contains("rate"));
}

TEST_CASE("duplicate_layer remaps the references between the copied nodes", "[tools][session]") {
    Session session = make_fire_session();
    call(session, "create_force", {{"id", "outside_force"}, {"parameters", {{"force_type", "gravity"}}}});
    call(session, "connect_nodes", {{"from", "outside_force"}, {"to", "flame_ps"}, {"port", "forces"}});

    const nlohmann::json copy = call(session, "duplicate_layer", {{"layer_id", "primary"}});
    CHECK(copy["layer"]["id"] == "primary_copy");
    CHECK(copy["nodes"].size() == 2);

    const nlohmann::json emitter = call(session, "inspect_node", {{"node_id", "flames_copy"}});
    CHECK(emitter["node"]["layer"] == "primary_copy");
    CHECK(emitter["node"]["inputs"]["particle"] == "flame_ps_copy");  // internal reference remapped

    const nlohmann::json system = call(session, "inspect_node", {{"node_id", "flame_ps_copy"}});
    CHECK(system["node"]["inputs"]["forces"] == "outside_force");  // external reference kept

    CHECK(call(session, "validate_effect")["ok"] == true);
    CHECK(call(session, "duplicate_layer", {{"layer_id", "primary"}, {"suffix", "_b"}})["layer"]["id"] == "primary_b");
}

TEST_CASE("delete_node reports the references it cleared", "[tools][session]") {
    Session session = make_fire_session();
    call(session, "create_mesh", {{"id", "core"}});
    call(session, "set_node_property", {{"node_id", "flames"}, {"parent", "core"}});

    const nlohmann::json deleted = call(session, "delete_node", {{"node_id", "flame_ps"}});
    CHECK(deleted["dangling"] == nlohmann::json::array({{{"node", "flames"}, {"port", "particle"}}}));
    CHECK(call(session, "inspect_graph")["nodes"].size() == 2);

    const nlohmann::json parent_gone = call(session, "delete_node", {{"node_id", "core"}});
    CHECK(parent_gone["dangling"] == nlohmann::json::array({{{"node", "flames"}, {"port", "parent"}}}));
    CHECK_THROWS_AS(call(session, "delete_node", {{"node_id", "core"}}), Error);
}

TEST_CASE("undo and redo restore the document byte for byte", "[tools][session]") {
    Session session = make_fire_session();
    const std::string before = effect_to_canonical_string(session.active().effect);

    call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"value", 999.0}});
    const std::string after = effect_to_canonical_string(session.active().effect);
    REQUIRE(before != after);

    const nlohmann::json undone = call(session, "undo");
    CHECK(undone["ok"] == true);
    CHECK(effect_to_canonical_string(session.active().effect) == before);

    const nlohmann::json redone = call(session, "redo");
    CHECK(redone["ok"] == true);
    CHECK(effect_to_canonical_string(session.active().effect) == after);

    SECTION("undo walks the whole history and then reports nothing left") {
        while (call(session, "undo")["ok"] == true) {
        }
        CHECK(call(session, "undo")["remaining"] == 0);
        CHECK(session.active().effect.nodes.empty());
        CHECK(call(session, "redo")["ok"] == true);
    }
    SECTION("a failed mutation leaves no trace and no undo step") {
        call(session, "undo");  // leave something in the redo stack
        const size_t depth = session.active().undo_stack.size();
        const size_t redo_depth = session.active().redo_stack.size();
        REQUIRE(redo_depth > 0);
        CHECK_THROWS_AS(call(session, "set_parameter", {{"node_id", "flames"}, {"name", "rate"}, {"value", "nope"}}),
                        Error);
        CHECK(session.active().undo_stack.size() == depth);
        CHECK(session.active().redo_stack.size() == redo_depth);
        CHECK(effect_to_canonical_string(session.active().effect) == before);
    }
}

TEST_CASE("save and load round-trip the document", "[tools][session]") {
    Session session = make_fire_session();
    const uint64_t hash = effect_hash(session.active().effect);

    const std::filesystem::path path = output_dir() / "workflow_round_trip.json";
    const nlohmann::json saved = call(session, "save_effect", {{"path", path.string()}});
    CHECK(saved["path"] == path.string());
    CHECK(std::filesystem::is_regular_file(path));
    CHECK(session.active().dirty == false);

    const nlohmann::json loaded = call(session, "load_effect", {{"path", path.string()}});
    CHECK(loaded["effect_id"] == "fx_2");
    CHECK(loaded["diagnostics"]["errors"] == 0);
    CHECK(effect_hash(session.active().effect) == hash);
    CHECK(call(session, "get_effect_json")["name"] == "Workflow Fire");

    SECTION("without a path it saves next to where it came from") {
        const nlohmann::json again = call(session, "save_effect");
        CHECK(again["path"] == path.string());
    }
    SECTION("several effects live side by side") {
        const nlohmann::json effects = call(session, "list_effects")["effects"];
        CHECK(effects.size() == 2);
        CHECK(effects[1]["active"] == true);
        call(session, "set_active_effect", {{"effect_id", "fx_1"}});
        CHECK(call(session, "list_effects")["active"] == "fx_1");
        CHECK(call(session, "delete_effect", {{"effect_id", "fx_1"}})["ok"] == true);
        CHECK(call(session, "list_effects")["effects"].size() == 1);
    }
}

TEST_CASE("describe_vocabulary is the agent's dictionary", "[tools][session]") {
    Session session(output_dir());
    const nlohmann::json vocabulary = call(session, "describe_vocabulary");
    REQUIRE(vocabulary.contains("node_types"));
    REQUIRE(vocabulary.contains("texture_ops"));
    CHECK(vocabulary["node_types"].size() == 18);
    CHECK(vocabulary["node_types"]["emitter"]["parameters"]["rate"]["type"] == "float");
    CHECK(vocabulary["node_types"]["emitter"]["inputs"].contains("particle"));
    CHECK(vocabulary["texture_ops"].size() > 5);
    CHECK(vocabulary.contains("validation_codes"));

    const nlohmann::json one = call(session, "describe_vocabulary", {{"node_type", "force"}});
    CHECK(one["node_types"].size() == 1);
    CHECK(one["node_types"].contains("force"));
    CHECK(one.contains("texture_ops"));
    CHECK_THROWS_AS(call(session, "describe_vocabulary", {{"node_type", "sparkles"}}), Error);
}

TEST_CASE("the timeline stages the effect and binds nodes to phases", "[tools][session]") {
    Session session = make_fire_session();
    const nlohmann::json created =
        call(session, "set_timeline_phase", {{"name", "activation"}, {"start", 0.0}, {"end", 0.3}});
    CHECK(created["created"] == true);
    CHECK(created["timeline"]["phases"].size() == 1);
    call(session, "set_timeline_phase", {{"name", "decay"}, {"start", 0.8}, {"end", 1.5}});

    const nlohmann::json updated =
        call(session, "set_timeline_phase", {{"name", "activation"}, {"start", 0.0}, {"end", 0.4}});
    CHECK(updated["created"] == false);
    CHECK(updated["timeline"]["phases"].size() == 2);

    call(session, "set_parameter", {{"node_id", "flames"}, {"name", "phase"}, {"value", "activation"}});
    const nlohmann::json timeline = call(session, "get_timeline");
    CHECK(timeline["duration"] == Approx(1.5));
    CHECK(timeline["phases"].size() == 2);
    CHECK(timeline["bound_nodes"]["activation"] == nlohmann::json::array({"flames"}));
    CHECK(timeline["bound_nodes"]["decay"].empty());

    CHECK(call(session, "remove_timeline_phase", {{"name", "decay"}})["removed"] == true);
    CHECK(call(session, "get_timeline")["phases"].size() == 1);
}

TEST_CASE("create_effect starts from a bundled example", "[tools][session]") {
    Session session(output_dir());
    const nlohmann::json created = call(session, "create_effect", {{"template", "fireball"}});
    CHECK(created["effect"]["name"] == "Fireball");
    CHECK(created["effect"]["duration"] == Approx(2.5));
    CHECK(created["effect"]["nodes"].size() == 22);
    CHECK(created["diagnostics"]["errors"] == 0);

    const nlohmann::json renamed =
        call(session, "create_effect", {{"template", "fireball.json"}, {"name", "My Fireball"}});
    CHECK(renamed["effect"]["name"] == "My Fireball");

    const nlohmann::json from_path = call(session, "create_effect", {{"template", example("fire_aoe.json").string()}});
    CHECK(from_path["effect"]["name"] == "Fire AOE");

    CHECK_THROWS_AS(call(session, "create_effect", {{"template", "no_such_template"}}), Error);
    CHECK(call(session, "create_effect", {{"name", "Blank"}})["effect"]["nodes"].empty());
}

TEST_CASE("effect properties and node properties can be edited", "[tools][session]") {
    Session session = make_fire_session();
    const nlohmann::json effect = call(
        session, "set_effect_property",
        {{"name", "Renamed"}, {"duration", 3.0}, {"seed", 99}, {"metadata", {{"prompt", "a wall of fire"}}}});
    CHECK(effect["effect"]["name"] == "Renamed");
    CHECK(effect["effect"]["duration"] == Approx(3.0));
    CHECK(effect["effect"]["seed"] == 99);
    CHECK(effect["effect"]["metadata"]["prompt"] == "a wall of fire");

    const nlohmann::json node = call(session, "set_node_property",
                                     {{"node_id", "flames"}, {"enabled", false}, {"seed", 3}, {"metadata", {{"why", "test"}}}});
    CHECK(node["node"]["enabled"] == false);
    CHECK(node["node"]["seed"] == 3);
    CHECK(node["node"]["metadata"]["why"] == "test");

    call(session, "set_node_property", {{"node_id", "flames"}, {"layer", ""}});
    CHECK_FALSE(call(session, "inspect_node", {{"node_id", "flames"}})["node"].contains("layer"));

    const nlohmann::json layer = call(session, "set_layer_property",
                                      {{"layer_id", "primary"}, {"name", "Core"}, {"role", "ignition"}});
    CHECK(layer["layer"]["name"] == "Core");
    CHECK(layer["layer"]["role"] == "ignition");
    CHECK_THROWS_AS(call(session, "set_layer_property", {{"layer_id", "primary"}, {"role", "nonsense"}}), Error);

    const nlohmann::json duplicated = call(session, "duplicate_node", {{"node_id", "flames"}});
    CHECK(duplicated["node"]["id"] == "flames_copy");

    const nlohmann::json removed = call(session, "delete_layer", {{"layer_id", "primary"}});
    CHECK(removed["removed_nodes"].empty());  // nodes survive by default
    CHECK(call(session, "inspect_graph")["layers"].empty());
}

TEST_CASE("unknown parameters survive as diagnostics instead of failing the call", "[tools][session]") {
    Session session(output_dir());
    call(session, "create_effect", {{"name", "Forgiving"}});
    const nlohmann::json created =
        call(session, "create_emitter", {{"id", "probe"}, {"parameters", {{"rate", 10.0}, {"rat", 5.0}}}});
    CHECK(created["node"]["id"] == "probe");
    CHECK(has_code(created["diagnostics"], "E004"));
    CHECK(call(session, "get_parameter", {{"node_id", "probe"}, {"name", "rate"}})["value"] == Approx(10.0));
}

TEST_CASE("effect_id selects a document explicitly", "[tools][session]") {
    Session session(output_dir());
    call(session, "create_effect", {{"name", "First"}});
    call(session, "create_effect", {{"name", "Second"}});
    CHECK(call(session, "inspect_graph")["name"] == "Second");
    CHECK(call(session, "inspect_graph", {{"effect_id", "fx_1"}})["name"] == "First");
    CHECK_THROWS_AS(call(session, "inspect_graph", {{"effect_id", "fx_99"}}), Error);
}
