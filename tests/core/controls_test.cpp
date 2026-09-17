// Effect controls: round-trip, validation codes, the value transforms and the
// default set. docs/CONTROLS.md is the contract these assert.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/controls.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"

using namespace aether;
using Catch::Approx;

namespace {

// A tiny but complete effect: one layer, an emitter feeding a particle system,
// a material and a light, which is enough for every default control.
Effect make_effect() {
    Effect e;
    e.name = "Controls";
    e.duration = 2.0;
    e.layers.push_back(Layer{"primary", "Flames", LayerRole::Primary, true, nlohmann::json::object()});

    Node ps;
    ps.id = "flame_ps";
    ps.type = NodeType::ParticleSystem;
    ps.layer = "primary";
    ps.parameters["emissive"] = Parameter{2.0f};
    ps.parameters["size"] = Parameter{0.4f};
    ps.parameters["opacity"] = Parameter{0.8f};
    ps.parameters["color"] = Parameter{Color{1.0f, 0.4f, 0.05f, 1.0f}};
    ps.parameters["max_particles"] = Parameter{4000};
    e.add_node(std::move(ps));

    Node emitter;
    emitter.id = "flames";
    emitter.type = NodeType::Emitter;
    emitter.layer = "primary";
    emitter.parameters["rate"] = Parameter{100.0f};
    emitter.parameters["burst_count"] = Parameter{40};
    emitter.inputs["particle"] = {NodeRef::parse("flame_ps")};
    e.add_node(std::move(emitter));

    Node material;
    material.id = "mat_fire";
    material.type = NodeType::Material;
    material.parameters["emissive_intensity"] = Parameter{3.0f};
    e.add_node(std::move(material));

    Node light;
    light.id = "fire_light";
    light.type = NodeType::Light;
    light.layer = "primary";
    light.parameters["intensity"] = Parameter{20.0f};
    light.parameters["radius"] = Parameter{6.0f};
    e.add_node(std::move(light));
    return e;
}

Control multiplier(std::string id, std::vector<ControlBinding> bindings, double value = 1.0) {
    Control c;
    c.id = std::move(id);
    c.label = "Intensity";
    c.group = "Flames";
    c.min = 0.0;
    c.max = 3.0;
    c.default_value = 1.0;
    c.value = value;
    c.step = 0.01;
    c.unit = "x";
    c.bindings = std::move(bindings);
    return c;
}

float param(const Effect& e, const char* node, const char* name) {
    return param_float(*e.find_node(node), name);
}

bool has_code(const Diagnostics& d, const std::string& code) {
    for (const Diagnostic& item : d.items)
        if (item.code == code) return true;
    return false;
}

}  // namespace

TEST_CASE("controls round-trip through the document", "[core][controls]") {
    Effect e = make_effect();
    e.description = "a small fire";
    e.controls.push_back(multiplier("flames_intensity",
                                    {{"flame_ps", "emissive", ControlOp::Multiply},
                                     {"fire_light", "intensity", ControlOp::Multiply}},
                                    1.8));

    const nlohmann::json json = effect_to_json(e);
    REQUIRE(json.contains("controls"));
    CHECK(json.at("controls").size() == 1);
    CHECK(json.at("controls")[0].at("id") == "flames_intensity");
    CHECK(json.at("controls")[0].at("bindings")[0].at("op") == "multiply");
    CHECK(json.at("description") == "a small fire");

    const Effect back = effect_from_json(json);
    REQUIRE(back.controls.size() == 1);
    CHECK(back.controls[0] == e.controls[0]);
    CHECK(back.description == "a small fire");
    CHECK(effect_to_json(back) == json);
}

TEST_CASE("an effect without controls serialises exactly as before", "[core][controls]") {
    const Effect e = make_effect();
    const nlohmann::json json = effect_to_json(e);
    CHECK_FALSE(json.contains("controls"));
    CHECK_FALSE(json.contains("description"));
}

TEST_CASE("multiply scales constants, keyframes and vectors", "[core][controls]") {
    Effect e = make_effect();
    Node& emitter = *e.find_node("flames");
    emitter.parameters["rate"].set_keyframe(0.0, 0.0f);
    emitter.parameters["rate"].set_keyframe(0.5, 200.0f);
    emitter.parameters["rate"].set_keyframe(1.0, 50.0f);

    e.controls.push_back(multiplier("density", {{"flames", "rate", ControlOp::Multiply},
                                                {"flames", "burst_count", ControlOp::Multiply}}, 2.0));
    REQUIRE(apply_controls(e) == 2);

    const Parameter& rate = e.find_node("flames")->parameters.at("rate");
    REQUIRE(rate.track.size() == 3);
    CHECK(std::get<float>(rate.track[1].value) == Approx(400.0f));
    CHECK(std::get<float>(rate.track[2].value) == Approx(100.0f));
    // burst_count is an int: the product is rounded, not truncated.
    CHECK(param_int(*e.find_node("flames"), "burst_count") == 80);
}

TEST_CASE("multiply on a colour leaves alpha alone", "[core][controls]") {
    Effect e = make_effect();
    e.find_node("flame_ps")->parameters["color"] = Parameter{Color{0.5f, 0.25f, 0.125f, 0.7f}};
    e.controls.push_back(multiplier("tint", {{"flame_ps", "color", ControlOp::Multiply}}, 2.0));
    REQUIRE(apply_controls(e) == 1);

    const Color c = param_color(*e.find_node("flame_ps"), "color");
    CHECK(c.r == Approx(1.0f));
    CHECK(c.g == Approx(0.5f));
    CHECK(c.b == Approx(0.25f));
    CHECK(c.a == Approx(0.7f));
}

TEST_CASE("results are clamped into the parameter's own range", "[core][controls]") {
    Effect e = make_effect();
    e.controls.push_back(multiplier("op", {{"flame_ps", "opacity", ControlOp::Multiply}}, 3.0));
    apply_controls(e);
    CHECK(param(e, "flame_ps", "opacity") == Approx(1.0f));  // opacity max is 1
    CHECK(validate(e).ok());
}

TEST_CASE("hue_shift rotates colours and gradients, keeping alpha", "[core][controls]") {
    Effect e = make_effect();
    e.find_node("flame_ps")->parameters["color"] = Parameter{Color{1.0f, 0.0f, 0.0f, 0.5f}};
    e.find_node("flame_ps")->parameters["color_over_life"] =
        Parameter{Gradient{{0.0f, Color{1.0f, 0.0f, 0.0f, 1.0f}}, {1.0f, Color{0.5f, 0.5f, 0.5f, 1.0f}}}};

    Control hue = multiplier("hue", {{"flame_ps", "color", ControlOp::HueShift},
                                     {"flame_ps", "color_over_life", ControlOp::HueShift}}, 120.0);
    hue.min = -180.0;
    hue.max = 180.0;
    hue.default_value = 0.0;
    hue.unit = "deg";
    e.controls.push_back(std::move(hue));
    REQUIRE(apply_controls(e) == 2);

    const Color c = param_color(*e.find_node("flame_ps"), "color");
    CHECK(c.r == Approx(0.0f));
    CHECK(c.g == Approx(1.0f));  // red + 120 deg = green
    CHECK(c.b == Approx(0.0f));
    CHECK(c.a == Approx(0.5f));

    const Gradient g = param_gradient(*e.find_node("flame_ps"), "color_over_life");
    CHECK(g.keys[0].color.g == Approx(1.0f));
    // a grey key has no hue to rotate
    CHECK(g.keys[1].color.r == Approx(0.5f));
    CHECK(g.keys[1].color.g == Approx(0.5f));
}

TEST_CASE("add and set fold in as their names say", "[core][controls]") {
    Effect e = make_effect();
    Control add = multiplier("boost", {{"flame_ps", "emissive", ControlOp::Add}}, 1.5);
    add.bindings[0].op = ControlOp::Add;
    e.controls.push_back(std::move(add));
    Control set = multiplier("exact", {{"fire_light", "intensity", ControlOp::Set}}, 2.5);
    set.bindings[0].op = ControlOp::Set;
    e.controls.push_back(std::move(set));

    REQUIRE(apply_controls(e) == 2);
    CHECK(param(e, "flame_ps", "emissive") == Approx(3.5f));
    CHECK(param(e, "fire_light", "intensity") == Approx(2.5f));
}

TEST_CASE("controls at their identity value change nothing", "[core][controls]") {
    Effect plain = make_effect();
    Effect controlled = make_effect();
    controlled.controls = generate_default_controls(controlled);
    REQUIRE_FALSE(controlled.controls.empty());

    CHECK(apply_controls(controlled) == 0);
    controlled.controls.clear();
    CHECK(effect_to_canonical_string(controlled) == effect_to_canonical_string(plain));
}

TEST_CASE("generated defaults cover Global and every layer", "[core][controls]") {
    const Effect e = make_effect();
    const std::vector<Control> controls = generate_default_controls(e);

    std::vector<std::string> ids;
    for (const Control& c : controls) ids.push_back(c.id);
    CHECK(std::find(ids.begin(), ids.end(), "global_intensity") != ids.end());
    CHECK(std::find(ids.begin(), ids.end(), "global_hue") != ids.end());
    CHECK(std::find(ids.begin(), ids.end(), "primary_density") != ids.end());

    for (const Control& c : controls) {
        CAPTURE(c.id);
        CHECK_FALSE(c.bindings.empty());
        CHECK_FALSE(c.label.empty());
        CHECK(c.min < c.max);
        CHECK(c.value == c.default_value);
        if (c.id == "global_hue") {
            CHECK(c.min == Approx(-180.0));
            CHECK(c.max == Approx(180.0));
            CHECK(c.default_value == Approx(0.0));
            CHECK(c.unit == "deg");
        } else {
            CHECK(c.min == Approx(0.0));
            CHECK(c.max == Approx(3.0));
            CHECK(c.default_value == Approx(1.0));
            CHECK(c.unit == "x");
        }
    }

    // Global intensity reaches the particle system, the material and the light.
    const Control* intensity = nullptr;
    for (const Control& c : controls)
        if (c.id == "global_intensity") intensity = &c;
    REQUIRE(intensity != nullptr);
    std::vector<std::string> targets;
    for (const ControlBinding& b : intensity->bindings) targets.push_back(b.node + "." + b.parameter);
    CHECK(std::find(targets.begin(), targets.end(), "flame_ps.emissive") != targets.end());
    CHECK(std::find(targets.begin(), targets.end(), "mat_fire.emissive_intensity") != targets.end());
    CHECK(std::find(targets.begin(), targets.end(), "fire_light.intensity") != targets.end());

    // The layer group only reaches the nodes in that layer.
    const Control* layer_intensity = nullptr;
    for (const Control& c : controls)
        if (c.id == "primary_intensity") layer_intensity = &c;
    REQUIRE(layer_intensity != nullptr);
    CHECK(layer_intensity->group == "Flames");
    for (const ControlBinding& b : layer_intensity->bindings) CHECK(b.node != "mat_fire");
}

TEST_CASE("generated defaults validate and are deterministic", "[core][controls]") {
    Effect e = make_effect();
    e.controls = generate_default_controls(e);
    const Diagnostics d = validate(e);
    INFO(d.summary());
    CHECK(d.ok());
    CHECK_FALSE(has_code(d, "W007"));

    const Effect again = make_effect();
    CHECK(generate_default_controls(again) == e.controls);
}

TEST_CASE("control definitions are validated", "[core][controls]") {
    SECTION("bad id") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("Bad Id", {{"flame_ps", "emissive", ControlOp::Multiply}}));
        CHECK(has_code(validate(e), "E021"));
    }
    SECTION("duplicate id") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("twice", {{"flame_ps", "emissive", ControlOp::Multiply}}));
        e.controls.push_back(multiplier("twice", {{"flame_ps", "size", ControlOp::Multiply}}));
        CHECK(has_code(validate(e), "E021"));
    }
    SECTION("unknown node") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("ghost", {{"nope", "emissive", ControlOp::Multiply}}));
        const Diagnostics d = validate(e);
        CHECK(has_code(d, "E022"));
    }
    SECTION("unknown parameter") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("ghost", {{"flame_ps", "nope", ControlOp::Multiply}}));
        CHECK(has_code(validate(e), "E022"));
    }
    SECTION("non-numeric parameter") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("blend", {{"flame_ps", "blend", ControlOp::Multiply}}));
        CHECK(has_code(validate(e), "E023"));
    }
    SECTION("hue_shift on a number") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("hue", {{"flame_ps", "emissive", ControlOp::HueShift}}));
        CHECK(has_code(validate(e), "E023"));
    }
    SECTION("value out of range") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("hot", {{"flame_ps", "emissive", ControlOp::Multiply}}, 9.0));
        CHECK(has_code(validate(e), "E024"));
    }
    SECTION("empty range") {
        Effect e = make_effect();
        Control c = multiplier("flat", {{"flame_ps", "emissive", ControlOp::Multiply}});
        c.min = 2.0;
        c.max = 1.0;
        e.controls.push_back(std::move(c));
        CHECK(has_code(validate(e), "E024"));
    }
    SECTION("no bindings is only a warning") {
        Effect e = make_effect();
        e.controls.push_back(multiplier("lonely", {}));
        const Diagnostics d = validate(e);
        CHECK(d.ok());
        CHECK(has_code(d, "W007"));
    }
}

TEST_CASE("a binding that cannot apply is skipped, not fatal", "[core][controls]") {
    Effect e = make_effect();
    e.controls.push_back(multiplier("mixed", {{"nope", "emissive", ControlOp::Multiply},
                                              {"flame_ps", "nope", ControlOp::Multiply},
                                              {"flame_ps", "emissive", ControlOp::Multiply}},
                                    2.0));
    CHECK(apply_controls(e) == 1);
    CHECK(param(e, "flame_ps", "emissive") == Approx(4.0f));
}

TEST_CASE("a control only materialises a parameter when it changes it", "[core][controls]") {
    Effect e = make_effect();
    // `width` is not set on this system... it does not have one; use a light's
    // radius, which is set, and a decal's emissive, which is not.
    Node decal;
    decal.id = "scorch";
    decal.type = NodeType::Decal;
    e.add_node(std::move(decal));
    e.controls.push_back(multiplier("intensity", {{"scorch", "emissive", ControlOp::Multiply}}, 2.0));
    // emissive defaults to 0, so doubling it is still 0 and nothing is written.
    CHECK(apply_controls(e) == 0);
    CHECK_FALSE(e.find_node("scorch")->has_param("emissive"));
}
