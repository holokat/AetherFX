// The controls tools an agent and the studio drive: docs/AGENT_API.md
// "controls", docs/CONTROLS.md.
#include <filesystem>
#include <string>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"
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

nlohmann::json call(Session& session, const std::string& tool, nlohmann::json args = nlohmann::json::object()) {
    return ToolRegistry::standard().call(session, tool, args);
}

// A fire with one layer, an emitter, a particle system and a light.
Session make_session() {
    Session session(output_dir());
    call(session, "create_effect", {{"name", "Knobs"}, {"duration", 1.5}, {"seed", 7}});
    call(session, "create_layer", {{"id", "primary"}, {"name", "Flames"}, {"role", "primary"}});
    call(session, "create_particle_system",
         {{"id", "flame_ps"}, {"layer", "primary"}, {"parameters", {{"emissive", 2.0}, {"size", 0.2}}}});
    call(session, "create_emitter",
         {{"id", "flames"}, {"layer", "primary"}, {"parameters", {{"shape", "sphere"}, {"rate", 120}}}});
    call(session, "connect_nodes", {{"from", "flame_ps"}, {"to", "flames"}, {"port", "particle"}});
    call(session, "create_light", {{"id", "glow"}, {"layer", "primary"}, {"parameters", {{"intensity", 20.0}}}});
    return session;
}

const nlohmann::json* find_control(const nlohmann::json& result, const std::string& id) {
    for (const auto& control : result.at("controls"))
        if (control.at("id") == id) return &control;
    return nullptr;
}

float effective(Session& session, const std::string& node, const std::string& name) {
    const nlohmann::json result = call(session, "inspect_node", {{"node_id", node}});
    return result.at("effective_parameters").at(name).get<float>();
}

}  // namespace

TEST_CASE("an effect starts with no controls", "[tools][controls]") {
    Session session = make_session();
    const nlohmann::json listed = call(session, "list_controls");
    CHECK(listed.at("count") == 0);
    CHECK(listed.at("controls").empty());
    CHECK(listed.at("groups").empty());
}

TEST_CASE("generate_default_controls builds a Global group and one per layer", "[tools][controls]") {
    Session session = make_session();
    const nlohmann::json generated = call(session, "generate_default_controls");
    CHECK(generated.at("ok") == true);
    CHECK(generated.at("added").get<int>() > 0);
    CHECK(generated.at("replaced") == false);
    CHECK(generated.at("diagnostics").at("ok") == true);
    CHECK(generated.at("groups")[0] == "Global");

    const nlohmann::json* intensity = find_control(generated, "global_intensity");
    REQUIRE(intensity != nullptr);
    CHECK(intensity->at("label") == "Intensity");
    CHECK(intensity->at("min") == Approx(0.0));
    CHECK(intensity->at("max") == Approx(3.0));
    CHECK(intensity->at("value") == Approx(1.0));
    CHECK(intensity->at("unit") == "x");
    CHECK_FALSE(intensity->at("bindings").empty());
    CHECK(intensity->at("bindings")[0].at("op") == "multiply");

    const nlohmann::json* layer = find_control(generated, "primary_intensity");
    REQUIRE(layer != nullptr);
    CHECK(layer->at("group") == "Flames");

    // Running it again is idempotent: nothing is added twice.
    const nlohmann::json again = call(session, "generate_default_controls");
    CHECK(again.at("added") == 0);
    CHECK(again.at("count") == generated.at("count"));
}

TEST_CASE("set_control changes the render without touching the document", "[tools][controls]") {
    Session session = make_session();
    call(session, "generate_default_controls");
    const float authored = effective(session, "flame_ps", "emissive");

    const nlohmann::json set = call(session, "set_control", {{"id", "primary_intensity"}, {"value", 2.0}});
    CHECK(set.at("ok") == true);
    CHECK(set.at("control").at("value") == Approx(2.0));
    CHECK(set.at("diagnostics").at("ok") == true);

    // the authored value is untouched...
    CHECK(effective(session, "flame_ps", "emissive") == Approx(authored));
    // ... and the compiled plan is what moved.
    const nlohmann::json plan = call(session, "inspect_plan");
    CHECK(plan.at("controls").at("applied").get<int>() > 0);

    CHECK(call(session, "undo").at("ok") == true);
    CHECK(find_control(call(session, "list_controls"), "primary_intensity")->at("value") == Approx(1.0));
}

TEST_CASE("set_control refuses a value outside the range", "[tools][controls]") {
    Session session = make_session();
    call(session, "generate_default_controls");
    try {
        call(session, "set_control", {{"id", "global_intensity"}, {"value", 9.0}});
        FAIL("expected E024");
    } catch (const Error& error) {
        CHECK(error.code() == "E024");
    }
    CHECK(find_control(call(session, "list_controls"), "global_intensity")->at("value") == Approx(1.0));
}

TEST_CASE("an unknown control names the ones that exist", "[tools][controls]") {
    Session session = make_session();
    call(session, "generate_default_controls");
    try {
        call(session, "set_control", {{"id", "nope"}, {"value", 1.0}});
        FAIL("expected E021");
    } catch (const Error& error) {
        CHECK(error.code() == "E021");
        CHECK(std::string(error.what()).find("global_intensity") != std::string::npos);
    }
}

TEST_CASE("reset_controls puts everything back", "[tools][controls]") {
    Session session = make_session();
    call(session, "generate_default_controls");
    call(session, "set_control", {{"id", "global_intensity"}, {"value", 2.5}});
    call(session, "set_control", {{"id", "global_size"}, {"value", 0.5}});

    const nlohmann::json reset = call(session, "reset_controls");
    CHECK(reset.at("reset") == 2);
    for (const auto& control : reset.at("controls"))
        CHECK(control.at("value").get<double>() == Approx(control.at("default").get<double>()));

    // one at a time
    call(session, "set_control", {{"id", "global_size"}, {"value", 0.5}});
    const nlohmann::json one = call(session, "reset_controls", {{"id", "global_size"}});
    CHECK(one.at("reset") == 1);
}

TEST_CASE("add_control, update_control and remove_control author a knob", "[tools][controls]") {
    Session session = make_session();
    const nlohmann::json added =
        call(session, "add_control",
             {{"label", "Flame height"},
              {"group", "Flames"},
              {"bindings", {{{"node", "flame_ps"}, {"parameter", "size"}, {"op", "multiply"}}}}});
    CHECK(added.at("ok") == true);
    const std::string id = added.at("control").at("id");
    CHECK(id == "flame_height");
    CHECK(added.at("control").at("min") == Approx(0.0));
    CHECK(added.at("control").at("max") == Approx(3.0));
    CHECK(added.at("control").at("default") == Approx(1.0));
    CHECK(added.at("diagnostics").at("ok") == true);

    const nlohmann::json updated =
        call(session, "update_control", {{"id", id}, {"label", "Flame size"}, {"max", 4.0}, {"unit", "x"}});
    CHECK(updated.at("control").at("label") == "Flame size");
    CHECK(updated.at("control").at("max") == Approx(4.0));
    CHECK(updated.at("control").at("bindings").size() == 1);  // bindings survive an update that omits them

    const nlohmann::json removed = call(session, "remove_control", {{"id", id}});
    CHECK(removed.at("removed") == id);
    CHECK(removed.at("count") == 0);
}

TEST_CASE("a binding that does not fit comes back as a diagnostic", "[tools][controls]") {
    Session session = make_session();
    const nlohmann::json bad =
        call(session, "add_control",
             {{"label", "Nope"}, {"bindings", {{{"node", "flame_ps"}, {"parameter", "blend"}, {"op", "multiply"}}}}});
    CHECK(bad.at("ok") == true);  // the control is stored...
    bool found = false;
    for (const auto& item : bad.at("diagnostics").at("items"))
        if (item.value("code", std::string()) == "E023") found = true;
    CHECK(found);  // ... and the problem is reported, not thrown

    const nlohmann::json unknown =
        call(session, "add_control",
             {{"label", "Ghost"}, {"bindings", {{{"node", "missing"}, {"parameter", "size"}}}}});
    found = false;
    for (const auto& item : unknown.at("diagnostics").at("items"))
        if (item.value("code", std::string()) == "E022") found = true;
    CHECK(found);
}

TEST_CASE("a control with no bindings warns", "[tools][controls]") {
    Session session = make_session();
    const nlohmann::json added = call(session, "add_control", {{"label", "Lonely"}});
    bool found = false;
    for (const auto& item : added.at("diagnostics").at("items"))
        if (item.value("code", std::string()) == "W007") found = true;
    CHECK(found);
    CHECK(added.at("diagnostics").at("ok") == true);
}

TEST_CASE("generate_default_controls with replace rebuilds the set", "[tools][controls]") {
    Session session = make_session();
    call(session, "add_control", {{"label", "Mine"}});
    call(session, "generate_default_controls");
    CHECK(find_control(call(session, "list_controls"), "mine") != nullptr);

    call(session, "generate_default_controls", {{"replace", true}});
    const nlohmann::json listed = call(session, "list_controls");
    CHECK(find_control(listed, "mine") == nullptr);
    CHECK(find_control(listed, "global_intensity") != nullptr);
}

TEST_CASE("controls survive save and load", "[tools][controls]") {
    Session session = make_session();
    call(session, "generate_default_controls");
    call(session, "set_control", {{"id", "global_opacity"}, {"value", 0.4}});
    const std::filesystem::path path = output_dir() / "controls_round_trip.json";
    call(session, "save_effect", {{"path", path.string()}});

    Session other(output_dir());
    call(other, "load_effect", {{"path", path.string()}});
    const nlohmann::json listed = call(other, "list_controls");
    CHECK(listed.at("count").get<int>() > 0);
    CHECK(find_control(listed, "global_opacity")->at("value") == Approx(0.4));
}
