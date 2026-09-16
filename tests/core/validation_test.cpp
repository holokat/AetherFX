// Negative tests: every validation code in docs/VOCABULARY.md must fire on a
// document that breaks the corresponding rule, and must not fire otherwise.
#include <algorithm>
#include <functional>
#include <string>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"

using namespace aether;

namespace {

// A minimal document that validates with zero errors and zero warnings.
nlohmann::json base_document() {
    return nlohmann::json::parse(R"({
      "schema_version": "0.1.0",
      "name": "Base",
      "duration": 2.0,
      "seed": 1,
      "timeline": {"phases": [{"name": "activation", "start": 0.0, "end": 1.0}]},
      "layers": [{"id": "main", "name": "Main", "role": "primary"}],
      "nodes": [
        {"id": "ps", "type": "particle_system", "layer": "main", "parameters": {"max_particles": 100}},
        {"id": "em", "type": "emitter", "layer": "main", "parameters": {}, "inputs": {"particle": "ps"}}
      ]
    })");
}

Diagnostics validate_document(const nlohmann::json& j) { return validate(effect_from_json(j)); }

bool has_code(const Diagnostics& d, const std::string& code) {
    return std::any_of(d.items.begin(), d.items.end(), [&](const Diagnostic& i) { return i.code == code; });
}

bool has_code_on(const Diagnostics& d, const std::string& code, const std::string& node) {
    return std::any_of(d.items.begin(), d.items.end(),
                       [&](const Diagnostic& i) { return i.code == code && i.node && *i.node == node; });
}

bool has_code_on_param(const Diagnostics& d, const std::string& code, const std::string& node,
                       const std::string& param) {
    return std::any_of(d.items.begin(), d.items.end(), [&](const Diagnostic& i) {
        return i.code == code && i.node && *i.node == node && i.param && *i.param == param;
    });
}

// Applies `mutate` to a copy of the base document and validates it.
Diagnostics mutated(const std::function<void(nlohmann::json&)>& mutate) {
    nlohmann::json j = base_document();
    mutate(j);
    return validate_document(j);
}

nlohmann::json& node_named(nlohmann::json& j, const std::string& id) {
    for (auto& n : j.at("nodes"))
        if (n.at("id") == id) return n;
    throw std::runtime_error("no node " + id);
}

}  // namespace

TEST_CASE("the base document is clean", "[core][validation]") {
    Diagnostics d = validate_document(base_document());
    INFO(d.summary());
    CHECK(d.ok());
    CHECK(d.error_count() == 0);
    CHECK(d.warning_count() == 0);
    CHECK(d.to_json().at("ok") == true);
}

TEST_CASE("E001 duplicate node id", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"ps","type":"material"})"));
    });
    CHECK(has_code_on(d, "E001", "ps"));
    CHECK_FALSE(d.ok());
    // Effect::add_node throws the same code on the API boundary
    Effect e;
    Node n;
    n.id = "x";
    n.type = NodeType::Material;
    e.add_node(n);
    try {
        e.add_node(n);
        FAIL("expected E001");
    } catch (const Error& err) {
        CHECK(err.code() == "E001");
    }
}

TEST_CASE("E002 invalid id format", "[core][validation]") {
    for (const char* bad : {"Bad", "1bad", "with-dash", "with.dot", "with space", "_leading", ""}) {
        CAPTURE(bad);
        Diagnostics d = mutated([&](nlohmann::json& j) { node_named(j, "ps").at("id") = bad; });
        CHECK(has_code(d, "E002"));
    }
    for (const char* good : {"a", "flame_ps", "spark2", "a_1_b"}) {
        CAPTURE(good);
        Diagnostics d = mutated([&](nlohmann::json& j) {
            node_named(j, "ps").at("id") = good;
            node_named(j, "em").at("inputs").at("particle") = good;
        });
        CHECK_FALSE(has_code(d, "E002"));
    }
}

TEST_CASE("E003 unknown node type", "[core][validation]") {
    nlohmann::json j = base_document();
    node_named(j, "ps").at("type") = "hypercube";
    try {
        effect_from_json(j);
        FAIL("expected E003");
    } catch (const Error& e) {
        CHECK(e.code() == "E003");
        CHECK(std::string(e.what()).find("hypercube") != std::string::npos);
    }
}

TEST_CASE("E004 unknown parameter", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"wobble", 3}}; });
    CHECK(has_code_on_param(d, "E004", "em", "wobble"));
    // a parameter that belongs to a different node type is still unknown here
    Diagnostics other = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"max_segments", 3}}; });
    CHECK(has_code_on_param(other, "E004", "em", "max_segments"));
}

TEST_CASE("E005 parameter type mismatch", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"radius", "wide"}}; });
    CHECK(has_code_on_param(d, "E005", "em", "radius"));

    Diagnostics wrong_vector =
        mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"direction", 3.0}}; });
    CHECK(has_code_on_param(wrong_vector, "E005", "em", "direction"));

    Diagnostics wrong_curve = mutated(
        [](nlohmann::json& j) { node_named(j, "ps").at("parameters")["size_over_life"] = 0.5; });
    CHECK(has_code_on_param(wrong_curve, "E005", "ps", "size_over_life"));

    // ... and a keyframe value of the wrong type is reported too
    Effect e = effect_from_json(base_document());
    Parameter p{Value(1.0f)};
    p.set_keyframe(0.0, Value(1.0f));
    p.set_keyframe(1.0, Value(Vec3{1, 2, 3}));
    e.find_node("em")->parameters["rate"] = p;
    Diagnostics keyframes = validate(e);
    CHECK(has_code_on_param(keyframes, "E005", "em", "rate"));
    // an int where a float is expected is fine
    Diagnostics promoted = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"radius", 2}}; });
    CHECK_FALSE(has_code(promoted, "E005"));
}

TEST_CASE("E006 value out of range", "[core][validation]") {
    Diagnostics above = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"spread", 400.0}}; });
    CHECK(has_code_on_param(above, "E006", "em", "spread"));
    Diagnostics below = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"radius", -1.0}}; });
    CHECK(has_code_on_param(below, "E006", "em", "radius"));
    Diagnostics integral =
        mutated([](nlohmann::json& j) { node_named(j, "ps").at("parameters") = {{"max_particles", 0}}; });
    CHECK(has_code_on_param(integral, "E006", "ps", "max_particles"));
    Diagnostics opacity =
        mutated([](nlohmann::json& j) { node_named(j, "ps").at("parameters")["opacity"] = 4.0; });
    CHECK(has_code_on_param(opacity, "E006", "ps", "opacity"));

    // keyframe values are range checked as well
    Effect e = effect_from_json(base_document());
    Parameter p{Value(1.0f)};
    p.set_keyframe(0.0, Value(1.0f));
    p.set_keyframe(1.0, Value(-5.0f));
    e.find_node("em")->parameters["rate"] = p;
    CHECK(has_code_on_param(validate(e), "E006", "em", "rate"));

    // values on the boundary are accepted
    Diagnostics edge = mutated([](nlohmann::json& j) {
        node_named(j, "em").at("parameters") = {{"spread", 180.0}, {"inherit_velocity", 1.0}, {"radius", 0.0}};
    });
    CHECK_FALSE(has_code(edge, "E006"));
}

TEST_CASE("E007 invalid enum value", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"shape", "banana"}}; });
    CHECK(has_code_on_param(d, "E007", "em", "shape"));
    Diagnostics blend = mutated([](nlohmann::json& j) { node_named(j, "ps").at("parameters")["blend"] = "screen"; });
    CHECK(has_code_on_param(blend, "E007", "ps", "blend"));
    Diagnostics ok = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"shape", "ring"}}; });
    CHECK_FALSE(has_code(ok, "E007"));
}

TEST_CASE("E008 unresolved reference", "[core][validation]") {
    Diagnostics input = mutated([](nlohmann::json& j) { node_named(j, "em").at("inputs").at("particle") = "ghost"; });
    CHECK(has_code_on_param(input, "E008", "em", "particle"));

    Diagnostics parent = mutated([](nlohmann::json& j) { node_named(j, "em")["parent"] = "ghost"; });
    CHECK(has_code_on_param(parent, "E008", "em", "parent"));

    Diagnostics multi = mutated([](nlohmann::json& j) {
        node_named(j, "ps")["inputs"] = {{"forces", nlohmann::json::array({"ghost"})}};
    });
    CHECK(has_code_on_param(multi, "E008", "ps", "forces"));

    // an event target that does not exist
    Diagnostics event = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(
            nlohmann::json::parse(R"({"id":"ev","type":"event","inputs":{"targets":["em","ghost"]}})"));
    });
    CHECK(has_code_on_param(event, "E008", "ev", "targets"));
}

TEST_CASE("E009 port type mismatch", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"mat","type":"material"})"));
        node_named(j, "em").at("inputs").at("particle") = "mat";
    });
    CHECK(has_code_on_param(d, "E009", "em", "particle"));

    Diagnostics forces = mutated([](nlohmann::json& j) {
        node_named(j, "ps")["inputs"] = {{"forces", nlohmann::json::array({"em"})}};
    });
    CHECK(has_code_on_param(forces, "E009", "ps", "forces"));

    // a parent that cannot be a spatial parent
    Diagnostics parent = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"mat","type":"material"})"));
        node_named(j, "em")["parent"] = "mat";
    });
    CHECK(has_code_on_param(parent, "E009", "em", "parent"));
}

TEST_CASE("E010 required input missing", "[core][validation]") {
    Diagnostics emitter = mutated([](nlohmann::json& j) { node_named(j, "em").erase("inputs"); });
    CHECK(has_code_on_param(emitter, "E010", "em", "particle"));

    // ... unless the emitter feeds a volume
    Diagnostics volume_source = mutated([](nlohmann::json& j) {
        node_named(j, "em").erase("inputs");
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"vol","type":"volume","inputs":{"sources":["em"]}})"));
    });
    CHECK_FALSE(has_code_on_param(volume_source, "E010", "em", "particle"));

    Diagnostics trail = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"tr","type":"trail"})"));
    });
    CHECK(has_code_on_param(trail, "E010", "tr", "source"));

    Diagnostics event = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"ev","type":"event"})"));
    });
    CHECK(has_code_on_param(event, "E010", "ev", "targets"));

    // event.source is only required for the particle triggers
    Diagnostics on_time = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(
            R"({"id":"ev","type":"event","parameters":{"trigger":"on_time"},"inputs":{"targets":["em"]}})"));
    });
    CHECK_FALSE(has_code_on_param(on_time, "E010", "ev", "source"));
    for (const char* trigger : {"on_spawn", "on_death", "on_collision", "on_distance"}) {
        CAPTURE(trigger);
        Diagnostics needs_source = mutated([&](nlohmann::json& j) {
            nlohmann::json ev = nlohmann::json::parse(R"({"id":"ev","type":"event","inputs":{"targets":["em"]}})");
            ev["parameters"] = {{"trigger", trigger}};
            j.at("nodes").push_back(ev);
        });
        CHECK(has_code_on_param(needs_source, "E010", "ev", "source"));
    }
}

TEST_CASE("E011 multi-input given to a single port", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"ps2","type":"particle_system"})"));
        node_named(j, "em").at("inputs").at("particle") = nlohmann::json::array({"ps", "ps2"});
    });
    CHECK(has_code_on_param(d, "E011", "em", "particle"));
    // a list of one on a single port is fine
    Diagnostics single = mutated(
        [](nlohmann::json& j) { node_named(j, "em").at("inputs").at("particle") = nlohmann::json::array({"ps"}); });
    CHECK_FALSE(has_code(single, "E011"));
    // multi ports accept lists
    Diagnostics multi = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"g","type":"force"})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"w","type":"force"})"));
        node_named(j, "ps")["inputs"] = {{"forces", nlohmann::json::array({"g", "w"})}};
    });
    CHECK_FALSE(has_code(multi, "E011"));
}

TEST_CASE("E012 cycle in the parent chain", "[core][validation]") {
    Diagnostics pair = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"a","type":"mesh","parent":"b"})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"b","type":"mesh","parent":"a"})"));
    });
    CHECK(has_code_on_param(pair, "E012", "a", "parent"));
    CHECK(has_code_on_param(pair, "E012", "b", "parent"));

    Diagnostics self = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"a","type":"mesh","parent":"a"})"));
    });
    CHECK(has_code_on_param(self, "E012", "a", "parent"));

    Diagnostics chain = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"a","type":"mesh","parent":"b"})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"b","type":"mesh","parent":"c"})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"c","type":"mesh","parent":"a"})"));
    });
    CHECK(has_code(chain, "E012"));

    // a valid chain is not a cycle
    Diagnostics fine = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"a","type":"mesh"})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"b","type":"mesh","parent":"a"})"));
    });
    CHECK_FALSE(has_code(fine, "E012"));
}

TEST_CASE("E013 cycle in the input graph", "[core][validation]") {
    // particle_system -> trail -> source -> particle_system is a real cycle
    Diagnostics d = mutated([](nlohmann::json& j) {
        node_named(j, "ps")["inputs"] = {{"trail", "tr"}};
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"tr","type":"trail","inputs":{"source":"ps"}})"));
    });
    CHECK(has_code_on(d, "E013", "ps"));
    CHECK(has_code_on(d, "E013", "tr"));

    Effect cyclic = effect_from_json([] {
        nlohmann::json j = base_document();
        node_named(j, "ps")["inputs"] = {{"trail", "tr"}};
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"tr","type":"trail","inputs":{"source":"ps"}})"));
        return j;
    }());
    try {
        topological_order(cyclic);
        FAIL("expected E013");
    } catch (const Error& e) {
        CHECK(e.code() == "E013");
    }

    // a longer cycle through three nodes
    Diagnostics longer = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(
            nlohmann::json::parse(R"({"id":"m1","type":"material","inputs":{"base_texture":"t1"}})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"t1","type":"texture"})"));
        node_named(j, "t1")["parent"] = "c1";
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"c1","type":"collider","inputs":{"mesh":"me1"}})"));
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"me1","type":"mesh","inputs":{"material":"m1"}})"));
    });
    CHECK(has_code(longer, "E013"));

    // the base document has no cycle
    CHECK_FALSE(has_code(validate_document(base_document()), "E013"));
}

TEST_CASE("E014 unknown layer", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em").at("layer") = "ghost_layer"; });
    CHECK(has_code_on_param(d, "E014", "em", "layer"));
    CHECK_FALSE(has_code(validate_document(base_document()), "E014"));
}

TEST_CASE("E015 effect duration invalid", "[core][validation]") {
    CHECK(has_code(mutated([](nlohmann::json& j) { j.at("duration") = 0.0; }), "E015"));
    CHECK(has_code(mutated([](nlohmann::json& j) { j.at("duration") = -1.0; }), "E015"));
    CHECK_FALSE(has_code(mutated([](nlohmann::json& j) { j.at("duration") = 0.01; }), "E015"));
}

TEST_CASE("E016 timeline phase invalid", "[core][validation]") {
    CHECK(has_code(mutated([](nlohmann::json& j) { j.at("timeline").at("phases")[0].at("end") = 0.0; }), "E016"));
    CHECK(has_code(mutated([](nlohmann::json& j) { j.at("timeline").at("phases")[0].at("end") = -1.0; }), "E016"));
    CHECK(has_code(mutated([](nlohmann::json& j) { j.at("timeline").at("phases")[0].at("end") = 99.0; }), "E016"));
    CHECK(has_code(mutated([](nlohmann::json& j) {
                       j.at("timeline").at("phases")[0].at("start") = -1.0;
                       j.at("timeline").at("phases")[0].at("end") = 0.5;
                   }),
                   "E016"));
    CHECK_FALSE(has_code(mutated([](nlohmann::json& j) { j.at("timeline").at("phases")[0].at("end") = 2.0; }), "E016"));
}

TEST_CASE("E017 unknown port", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em").at("inputs")["bogus"] = "ps"; });
    CHECK(has_code_on_param(d, "E017", "em", "bogus"));
    // an unknown *output* port on the referenced node is also E017
    Diagnostics output = mutated([](nlohmann::json& j) { node_named(j, "em").at("inputs").at("particle") = "ps.nope"; });
    CHECK(has_code_on_param(output, "E017", "em", "particle"));
    Diagnostics good = mutated([](nlohmann::json& j) { node_named(j, "em").at("inputs").at("particle") = "ps.particles"; });
    CHECK_FALSE(has_code(good, "E017"));
}

TEST_CASE("E018 keyframe time invalid", "[core][validation]") {
    Diagnostics negative = mutated([](nlohmann::json& j) {
        node_named(j, "em")["parameters"] = nlohmann::json::parse(
            R"({"rate":{"value":1,"track":[{"time":-1,"value":0},{"time":1,"value":10}]}})");
    });
    CHECK(has_code_on_param(negative, "E018", "em", "rate"));

    Diagnostics beyond = mutated([](nlohmann::json& j) {
        node_named(j, "em")["parameters"] = nlohmann::json::parse(
            R"({"rate":{"value":1,"track":[{"time":0,"value":0},{"time":9,"value":10}]}})");
    });
    CHECK(has_code_on_param(beyond, "E018", "em", "rate"));

    Diagnostics unsorted = mutated([](nlohmann::json& j) {
        node_named(j, "em")["parameters"] = nlohmann::json::parse(
            R"({"rate":{"value":1,"track":[{"time":1.5,"value":0},{"time":0.5,"value":10}]}})");
    });
    CHECK(has_code_on_param(unsorted, "E018", "em", "rate"));

    // a track that ends exactly at the effect duration is valid
    Diagnostics edge = mutated([](nlohmann::json& j) {
        node_named(j, "em")["parameters"] = nlohmann::json::parse(
            R"({"rate":{"value":1,"track":[{"time":0,"value":0},{"time":2,"value":10}]}})");
    });
    CHECK_FALSE(has_code(edge, "E018"));
}

TEST_CASE("E019 curve/gradient keys out of range or unsorted", "[core][validation]") {
    Diagnostics above = mutated(
        [](nlohmann::json& j) { node_named(j, "ps").at("parameters")["size_over_life"] = nlohmann::json::parse("[[0,1],[2,0]]"); });
    CHECK(has_code_on_param(above, "E019", "ps", "size_over_life"));

    Diagnostics below = mutated([](nlohmann::json& j) {
        node_named(j, "ps").at("parameters")["opacity_over_life"] = nlohmann::json::parse("[[-0.5,1],[1,0]]");
    });
    CHECK(has_code_on_param(below, "E019", "ps", "opacity_over_life"));

    Diagnostics gradient = mutated([](nlohmann::json& j) {
        node_named(j, "ps").at("parameters")["color_over_life"] =
            nlohmann::json::parse("[[0,[1,1,1,1]],[1.5,[0,0,0,1]]]");
    });
    CHECK(has_code_on_param(gradient, "E019", "ps", "color_over_life"));

    // out-of-order keys built in memory (json input is sorted on the way in)
    Effect e = effect_from_json(base_document());
    Curve out_of_order;
    out_of_order.keys = {{1.0f, 0.0f, Interp::Linear}, {0.0f, 1.0f, Interp::Linear}};
    e.find_node("ps")->parameters["size_over_life"] = Parameter{Value(out_of_order)};
    CHECK(has_code_on_param(validate(e), "E019", "ps", "size_over_life"));

    Gradient gradient_out_of_order;
    gradient_out_of_order.keys = {{1.0f, Color::white()}, {0.5f, Color::black()}};
    e.find_node("ps")->parameters["color_over_life"] = Parameter{Value(gradient_out_of_order)};
    CHECK(has_code_on_param(validate(e), "E019", "ps", "color_over_life"));

    CHECK_FALSE(has_code(validate_document(base_document()), "E019"));
}

TEST_CASE("E020 schema version unsupported", "[core][validation]") {
    Diagnostics newer = mutated([](nlohmann::json& j) { j.at("schema_version") = "0.2.0"; });
    CHECK(has_code(newer, "E020"));
    Diagnostics older = mutated([](nlohmann::json& j) { j.at("schema_version") = "0.0.1"; });
    CHECK(has_code(older, "E020"));
    CHECK_FALSE(has_code(validate_document(base_document()), "E020"));
}

TEST_CASE("W001 unknown phase reference", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em")["parameters"] = {{"phase", "ghost"}}; });
    CHECK(has_code_on_param(d, "W001", "em", "phase"));
    CHECK(d.ok());  // a warning, not an error
    Diagnostics known = mutated([](nlohmann::json& j) { node_named(j, "em")["parameters"] = {{"phase", "activation"}}; });
    CHECK_FALSE(has_code(known, "W001"));
    Diagnostics empty = mutated([](nlohmann::json& j) { node_named(j, "em")["parameters"] = {{"phase", ""}}; });
    CHECK_FALSE(has_code(empty, "W001"));
}

TEST_CASE("W002 unused node", "[core][validation]") {
    for (const char* type : {"material", "texture", "noise", "force", "collider", "curve"}) {
        CAPTURE(type);
        Diagnostics d = mutated([&](nlohmann::json& j) {
            nlohmann::json n = nlohmann::json::parse(R"({"id":"orphan"})");
            n["type"] = type;
            j.at("nodes").push_back(n);
        });
        CHECK(has_code_on(d, "W002", "orphan"));
    }
    // an unreferenced particle system is unused
    Diagnostics ps = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"orphan","type":"particle_system"})"));
    });
    CHECK(has_code_on(ps, "W002", "orphan"));

    // an invisible mesh that nothing uses is unused; a visible one is renderable
    Diagnostics hidden = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(
            nlohmann::json::parse(R"({"id":"orphan","type":"mesh","parameters":{"visible":false}})"));
    });
    CHECK(has_code_on(hidden, "W002", "orphan"));
    Diagnostics visible = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"shown","type":"mesh"})"));
    });
    CHECK_FALSE(has_code(visible, "W002"));
    // referencing it clears the warning
    Diagnostics referenced = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(
            nlohmann::json::parse(R"({"id":"orphan","type":"mesh","parameters":{"visible":false}})"));
        node_named(j, "ps")["inputs"] = {{"mesh", "orphan"}};
    });
    CHECK_FALSE(has_code(referenced, "W002"));
    // renderable/analytic node types are never flagged
    for (const char* type : {"light", "decal", "beam", "camera", "post_effect", "emitter"}) {
        CAPTURE(type);
        Diagnostics d = mutated([&](nlohmann::json& j) {
            nlohmann::json n = nlohmann::json::parse(R"({"id":"lonely"})");
            n["type"] = type;
            if (std::string(type) == "emitter") n["inputs"] = {{"particle", "ps"}};
            j.at("nodes").push_back(n);
        });
        CHECK_FALSE(has_code_on(d, "W002", "lonely"));
    }
}

TEST_CASE("W003 particle budget", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) {
        node_named(j, "ps").at("parameters").at("max_particles") = 200000;
        for (int i = 0; i < 2; ++i) {
            nlohmann::json n = nlohmann::json::parse(R"({"type":"particle_system","parameters":{"max_particles":100000}})");
            n["id"] = "big" + std::to_string(i);
            j.at("nodes").push_back(n);
            nlohmann::json em = nlohmann::json::parse(R"({"type":"emitter"})");
            em["id"] = "big_em" + std::to_string(i);
            em["inputs"] = {{"particle", "big" + std::to_string(i)}};
            j.at("nodes").push_back(em);
        }
    });
    CHECK(has_code(d, "W003"));
    CHECK(d.ok());  // a budget warning is not an error
    CHECK_FALSE(has_code(validate_document(base_document()), "W003"));
}

TEST_CASE("W004 enabled node references a disabled node", "[core][validation]") {
    Diagnostics input = mutated([](nlohmann::json& j) { node_named(j, "ps")["enabled"] = false; });
    CHECK(has_code_on_param(input, "W004", "em", "particle"));

    Diagnostics parent = mutated([](nlohmann::json& j) {
        j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"core","type":"mesh","enabled":false})"));
        node_named(j, "em")["parent"] = "core";
    });
    CHECK(has_code_on_param(parent, "W004", "em", "parent"));

    // a disabled consumer of a disabled node is not a problem
    Diagnostics both = mutated([](nlohmann::json& j) {
        node_named(j, "ps")["enabled"] = false;
        node_named(j, "em")["enabled"] = false;
    });
    CHECK_FALSE(has_code(both, "W004"));
}

TEST_CASE("validate_node reports only that node's problems", "[core][validation]") {
    nlohmann::json j = base_document();
    node_named(j, "em").at("parameters") = {{"wobble", 1}};
    node_named(j, "ps").at("parameters").at("max_particles") = 0;
    Effect e = effect_from_json(j);

    Diagnostics emitter = validate_node(e, *e.find_node("em"));
    CHECK(has_code_on_param(emitter, "E004", "em", "wobble"));
    CHECK_FALSE(has_code(emitter, "E006"));

    Diagnostics system = validate_node(e, *e.find_node("ps"));
    CHECK(has_code_on_param(system, "E006", "ps", "max_particles"));
    CHECK_FALSE(has_code(system, "E004"));

    // the whole-effect pass finds both
    Diagnostics all = validate(e);
    CHECK(has_code(all, "E004"));
    CHECK(has_code(all, "E006"));
}

TEST_CASE("topological_order is deterministic and skips disabled nodes", "[core][validation]") {
    nlohmann::json j = base_document();
    j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"zz","type":"force"})"));
    j.at("nodes").push_back(nlohmann::json::parse(R"({"id":"aa","type":"force"})"));
    node_named(j, "ps")["inputs"] = {{"forces", nlohmann::json::array({"zz", "aa"})}};
    Effect e = effect_from_json(j);

    std::vector<NodeId> order = topological_order(e);
    CHECK(order.size() == 4);
    CHECK(order == topological_order(e));
    // ties are broken by node id, so "aa" comes before "zz"
    auto index_of = [&order](const std::string& id) {
        return std::distance(order.begin(), std::find(order.begin(), order.end(), id));
    };
    CHECK(index_of("aa") < index_of("zz"));
    CHECK(index_of("zz") < index_of("ps"));
    CHECK(index_of("ps") < index_of("em"));

    e.find_node("zz")->enabled = false;
    std::vector<NodeId> without = topological_order(e);
    CHECK(without.size() == 3);
    CHECK(std::find(without.begin(), without.end(), "zz") == without.end());

    // a reference to a missing node does not break ordering
    e.find_node("ps")->inputs["forces"].push_back(NodeRef{"ghost", ""});
    CHECK(topological_order(e).size() == 3);
}

TEST_CASE("diagnostics carry structured context", "[core][validation]") {
    Diagnostics d = mutated([](nlohmann::json& j) { node_named(j, "em").at("parameters") = {{"shape", "banana"}}; });
    const Diagnostic* found = nullptr;
    for (const Diagnostic& item : d.items)
        if (item.code == "E007") found = &item;
    REQUIRE(found != nullptr);
    CHECK(found->severity == Severity::Error);
    REQUIRE(found->node.has_value());
    CHECK(*found->node == "em");
    REQUIRE(found->param.has_value());
    CHECK(*found->param == "shape");
    CHECK(found->message.find("banana") != std::string::npos);
    nlohmann::json j = found->to_json();
    CHECK(j.at("code") == "E007");
    CHECK(j.at("node") == "em");
    CHECK(j.at("severity") == "error");
    CHECK(d.summary().find("E007") != std::string::npos);
}
