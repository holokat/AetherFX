// Controls are non-destructive: at their default values a controlled effect
// must compile and simulate to exactly the same thing as the same effect with
// no controls at all. Every document in examples/effects is checked, because a
// silent drift here would change every shipped effect. See docs/CONTROLS.md.
#include <filesystem>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/controls.hpp"
#include "aether/core/serialization.hpp"
#include "aether/sim/runtime.hpp"
#include "../support/example_path.hpp"

using namespace aether;
using Catch::Approx;

namespace {

std::vector<std::filesystem::path> example_files() {
    std::vector<std::filesystem::path> paths;
    const std::filesystem::path dir = std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects";
    for (const auto& entry : std::filesystem::directory_iterator(dir))
        if (entry.is_regular_file() && entry.path().extension() == ".json") paths.push_back(entry.path());
    std::sort(paths.begin(), paths.end());
    return paths;
}

compiler::CompileOptions fast_options() {
    compiler::CompileOptions options;
    // Baked textures are pure functions of the texture nodes, which no default
    // control touches; skipping them keeps this sweep a few seconds long.
    options.bake_textures = false;
    return options;
}

// Everything in the plan that is allowed to differ between the two compiles:
// the source hash (the documents *are* different) and the control counter.
nlohmann::json comparable_plan(const compiler::CompiledEffect& compiled) {
    nlohmann::json plan = compiled.plan_json();
    plan.erase("source_hash");
    plan.erase("controls");
    return plan;
}

std::vector<uint64_t> frame_hashes(const compiler::CompiledEffect& compiled, int frames) {
    std::vector<uint64_t> hashes;
    std::unique_ptr<sim::IRuntime> runtime = sim::create_cpu_runtime(compiled);
    for (int i = 0; i < frames; ++i) {
        runtime->step();
        hashes.push_back(runtime->state().hash());
    }
    return hashes;
}

}  // namespace

TEST_CASE("default controls change nothing in any example", "[sim][controls]") {
    const std::vector<std::filesystem::path> paths = example_files();
    REQUIRE(paths.size() >= 5);

    for (const std::filesystem::path& path : paths) {
        const std::string name = path.filename().string();
        CAPTURE(name);

        const Effect plain = load_effect_file(path);
        Effect controlled = plain;
        controlled.controls = generate_default_controls(controlled);
        CHECK_FALSE(controlled.controls.empty());
        // The document itself really is different, or the test would be vacuous.
        CHECK(effect_hash(controlled) != effect_hash(plain));

        const compiler::CompiledEffect a = compiler::compile(plain, fast_options());
        const compiler::CompiledEffect b = compiler::compile(controlled, fast_options());
        REQUIRE(a.diagnostics.error_count() == 0);
        INFO(b.diagnostics.summary());
        REQUIRE(b.diagnostics.error_count() == 0);
        CHECK(b.controls_applied == 0);

        // The compiled document: the controls ride along, the nodes do not move.  An example may ship
        // its own authored controls (at their defaults), so compare the two documents without either
        // side's control list.
        Effect folded = b.effect;
        folded.controls.clear();
        Effect baseline = a.effect;
        baseline.controls.clear();
        CHECK(effect_to_canonical_string(folded) == effect_to_canonical_string(baseline));
        CHECK(comparable_plan(b) == comparable_plan(a));

        // ... and the simulation it drives.
        CHECK(frame_hashes(b, 24) == frame_hashes(a, 24));
    }
}

TEST_CASE("a control that is moved does change the simulation", "[sim][controls]") {
    const Effect plain = load_effect_file(aether_example_file("fireball.json"));
    Effect controlled = plain;
    controlled.controls = generate_default_controls(controlled);
    Control* density = controlled.find_control("global_density");
    REQUIRE(density != nullptr);
    density->value = 0.25;

    const compiler::CompiledEffect a = compiler::compile(plain, fast_options());
    const compiler::CompiledEffect b = compiler::compile(controlled, fast_options());
    REQUIRE(b.diagnostics.error_count() == 0);
    CHECK(b.controls_applied > 0);
    CHECK(frame_hashes(b, 24) != frame_hashes(a, 24));

    // and the source document is untouched by the compile
    CHECK(effect_hash(controlled) != effect_hash(b.effect));
    CHECK(controlled.find_control("global_density")->value == 0.25);
}

// ---------------------------------------------------------------------------
// time_scale changes playback, never the simulation
// ---------------------------------------------------------------------------

TEST_CASE("time_scale leaves the simulation bit-identical", "[sim][controls]") {
    const Effect plain = load_effect_file(aether_example_file("fireball.json"));
    Effect fast = plain;
    fast.time_scale = 2.0;
    Effect slow = plain;
    slow.time_scale = 0.5;

    const compiler::CompiledEffect a = compiler::compile(plain, fast_options());
    const compiler::CompiledEffect b = compiler::compile(fast, fast_options());
    const compiler::CompiledEffect c = compiler::compile(slow, fast_options());
    REQUIRE(a.diagnostics.error_count() == 0);
    REQUIRE(b.diagnostics.error_count() == 0);
    REQUIRE(c.diagnostics.error_count() == 0);

    // time_scale is a host-side mapping from wall time to effect time, so the
    // fixed timestep, the seeds and every frame at a given EFFECT time are
    // untouched: 24 steps of the same dt hash the same in all three.
    CHECK(frame_hashes(b, 24) == frame_hashes(a, 24));
    CHECK(frame_hashes(c, 24) == frame_hashes(a, 24));
    CHECK(b.fixed_dt == a.fixed_dt);
    CHECK(c.fixed_dt == a.fixed_dt);

    // Only the wall clock the host must play them over differs.
    CHECK(b.wall_duration() == Approx(a.wall_duration() / 2.0));
    CHECK(c.wall_duration() == Approx(a.wall_duration() * 2.0));

    // The plan is the same except for the two time fields (and the hash of a
    // document that really is different).
    nlohmann::json pa = comparable_plan(a);
    nlohmann::json pb = comparable_plan(b);
    CHECK(pa.at("time_scale") != pb.at("time_scale"));
    pa.erase("time_scale");
    pa.erase("wall_duration");
    pb.erase("time_scale");
    pb.erase("wall_duration");
    CHECK(pa == pb);
}

TEST_CASE("the generated Speed control moves time_scale and nothing else", "[sim][controls]") {
    const Effect plain = load_effect_file(aether_example_file("fireball.json"));
    Effect controlled = plain;
    controlled.controls = generate_default_controls(controlled);
    Control* speed = controlled.find_control("global_speed");
    REQUIRE(speed != nullptr);
    speed->value = 2.0;

    const compiler::CompiledEffect a = compiler::compile(plain, fast_options());
    const compiler::CompiledEffect b = compiler::compile(controlled, fast_options());
    REQUIRE(b.diagnostics.error_count() == 0);
    CHECK(b.controls_applied == 1);
    CHECK(b.time_scale() == Approx(2.0));
    CHECK(frame_hashes(b, 24) == frame_hashes(a, 24));
}
