// Controls are folded in before anything reads a parameter, so they reach the
// resolved windows and the baked resources, not just the runtime.
// See docs/CONTROLS.md.
#include <filesystem>
#include <string>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/controls.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"

using namespace aether;
using Catch::Approx;

namespace {

Effect load_example(const std::string& name) {
    return load_effect_file(std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / name);
}

compiler::CompileOptions no_textures() {
    compiler::CompileOptions options;
    options.bake_textures = false;
    return options;
}

Control multiplier(std::string id, std::vector<ControlBinding> bindings, double value) {
    Control c;
    c.id = std::move(id);
    c.label = "Test";
    c.group = "Global";
    c.min = 0.0;
    c.max = 3.0;
    c.default_value = 1.0;
    c.value = value;
    c.step = 0.01;
    c.unit = "x";
    c.bindings = std::move(bindings);
    return c;
}

}  // namespace

TEST_CASE("a control reaches the compiled document and the plan", "[compiler][controls]") {
    Effect effect = load_example("fire_aoe.json");
    const float before = param_float(*effect.find_node("flame_ps"), "emissive");
    effect.controls.push_back(multiplier("hot", {{"flame_ps", "emissive", ControlOp::Multiply}}, 2.0));

    const compiler::CompiledEffect compiled = compiler::compile(effect, no_textures());
    REQUIRE(compiled.diagnostics.error_count() == 0);
    CHECK(compiled.controls_applied == 1);
    CHECK(param_float(*compiled.effect.find_node("flame_ps"), "emissive") == Approx(before * 2.0f));
    CHECK(compiled.plan_json().at("controls").at("count") == 1);
    CHECK(compiled.plan_json().at("controls").at("applied") == 1);

    // the source document is left exactly as the author wrote it
    CHECK(param_float(*effect.find_node("flame_ps"), "emissive") == Approx(before));
}

TEST_CASE("a control reaches baked resources", "[compiler][controls]") {
    Effect effect = load_example("fire_aoe.json");
    // rock_mesh is invisible: it is a geometry source, so its size control binds
    // `radius`, which is what build_mesh bakes.
    effect.controls.push_back(multiplier("bigger", {{"rock_mesh", "radius", ControlOp::Multiply}}, 2.0));

    const compiler::CompiledEffect plain = compiler::compile(load_example("fire_aoe.json"), no_textures());
    const compiler::CompiledEffect scaled = compiler::compile(effect, no_textures());
    REQUIRE(scaled.diagnostics.error_count() == 0);

    const MeshData& a = plain.resources.meshes.at("rock_mesh");
    const MeshData& b = scaled.resources.meshes.at("rock_mesh");
    REQUIRE(a.positions.size() == b.positions.size());
    REQUIRE_FALSE(a.positions.empty());
    CHECK(b.positions[0].x == Approx(a.positions[0].x * 2.0f).margin(1e-5));
}

TEST_CASE("a control folds into keyframe tracks before windows are resolved", "[compiler][controls]") {
    Effect effect = load_example("fire_aoe.json");
    Node& emitter = *effect.find_node("fire_wall");
    const double start = param_float(emitter, "start_time");
    effect.controls.push_back(multiplier("later", {{"fire_wall", "start_time", ControlOp::Add}}, 0.5));
    effect.controls.back().bindings[0].op = ControlOp::Add;
    effect.controls.back().min = -1.0;
    effect.controls.back().default_value = 0.0;

    const compiler::CompiledEffect compiled = compiler::compile(effect, no_textures());
    REQUIRE(compiled.diagnostics.error_count() == 0);
    const compiler::CompiledNode* node = compiled.find("fire_wall");
    REQUIRE(node != nullptr);
    CHECK(node->start_time == Approx(start + 0.5));
}
