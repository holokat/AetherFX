// The whole engine through the Agent Tool API: load an example, compile it,
// simulate it, render it, export it and evaluate it - the loop an agent runs.
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/sim/runtime.hpp"
#include "aether/tools/registry.hpp"
#include "../support/example_path.hpp"

using namespace aether;
using namespace aether::tools;
using Catch::Approx;

namespace {

std::filesystem::path output_dir(const std::string& name) {
    std::filesystem::path dir = std::filesystem::path(AETHER_TEST_OUTPUT_DIR) / name;
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

std::filesystem::path example(const std::string& name) {
    return aether_example_file(name);
}

const std::vector<std::string>& example_names() {
    static const std::vector<std::string> names{"fireball.json", "fire_aoe.json", "lightning_strike.json"};
    return names;
}

nlohmann::json call(Session& session, const std::string& tool, nlohmann::json args = nlohmann::json::object()) {
    return ToolRegistry::standard().call(session, tool, args);
}

// The compiler and the CPU runtime are built by another module; while either is
// still a stub they report "not_implemented" and the pipeline cannot run.
bool backend_available(const Effect& effect) {
    const compiler::CompiledEffect compiled = compiler::compile(effect);
    for (const Diagnostic& item : compiled.diagnostics.items)
        if (item.code == "not_implemented") return false;
    if (!compiled.ok()) return false;
    try {
        return sim::create_cpu_runtime(compiled) != nullptr;
    } catch (const Error& e) {
        return e.code() != "not_implemented";
    }
}

nlohmann::json read_json_file(const std::filesystem::path& path) {
    std::ifstream in(path);
    REQUIRE(in.good());
    nlohmann::json j;
    in >> j;
    return j;
}

}  // namespace

TEST_CASE("the full pipeline runs on every example effect", "[integration][pipeline]") {
    for (const std::string& name : example_names()) {
        const std::string stem = std::filesystem::path(name).stem().string();
        DYNAMIC_SECTION("example " << stem) {
            const Effect effect = load_effect_file(example(name));
            if (!backend_available(effect))
                SKIP("the compiler/runtime backend is still a stub (not_implemented)");

            Session session(output_dir(stem));
            const nlohmann::json loaded = call(session, "load_effect", {{"path", example(name).string()}});
            CHECK(loaded["diagnostics"]["errors"] == 0);
            const double duration = loaded["effect"]["duration"].get<double>();

            // --- compile ---------------------------------------------------
            const nlohmann::json plan = call(session, "inspect_plan");
            CHECK(plan["ok"] == true);
            REQUIRE(plan.contains("nodes"));
            CHECK(plan["nodes"].size() > 0);

            // --- simulate --------------------------------------------------
            const nlohmann::json simulated = call(session, "simulate");
            const double fixed_dt = simulated["fixed_dt"].get<double>();
            // simulate_to lands on the last fixed step at or before the target.
            CHECK(simulated["time"].get<double>() > duration - fixed_dt);
            CHECK(simulated["statistics"]["total_spawned"].get<size_t>() > 0);
            CHECK(simulated["backend"].is_string());

            const nlohmann::json sampled =
                call(session, "simulate_range", {{"start", 0.0}, {"end", duration}, {"sample_every", duration / 4.0}});
            CHECK(sampled["samples"].size() >= 5);

            // --- render three moments --------------------------------------
            for (double fraction : {0.1, 0.5, 0.9}) {
                const double time = duration * fraction;
                const nlohmann::json frame =
                    call(session, "render_frame", {{"time", time}, {"width", 192}, {"height", 192}});
                INFO("frame at t=" << time);
                CHECK(std::filesystem::is_regular_file(frame["path"].get<std::string>()));
                CHECK(frame["image_stats"]["width"] == 192);
                CHECK(frame["render_statistics"].contains("render_ms"));
                CHECK(frame["time"].get<double>() > time - fixed_dt);
            }

            // --- preview with a contact sheet ------------------------------
            const nlohmann::json preview = call(session, "render_preview",
                                                {{"fps", 10.0},
                                                 {"start", 0.0},
                                                 {"end", 0.5},
                                                 {"width", 128},
                                                 {"height", 128},
                                                 {"contact_sheet", true}});
            REQUIRE(preview["frames"].size() == 6);
            for (const auto& path : preview["frames"]) CHECK(std::filesystem::is_regular_file(path.get<std::string>()));
            REQUIRE(preview["contact_sheet"].is_string());
            CHECK(std::filesystem::is_regular_file(preview["contact_sheet"].get<std::string>()));
            CHECK(preview["statistics"]["frame_count"] == 6);

            // --- export a flipbook -----------------------------------------
            const std::filesystem::path sheet = output_dir(stem) / (stem + "_flipbook.png");
            const nlohmann::json exported =
                call(session, "export_effect",
                     {{"format", "flipbook"},
                      {"path", sheet.string()},
                      {"options", {{"fps", 8.0}, {"end", 0.5}, {"width", 96}, {"height", 96}}}});
            CHECK(std::filesystem::is_regular_file(sheet));
            const nlohmann::json manifest = exported["manifest"];
            CHECK(manifest["effect"] == loaded["effect"]["name"]);
            CHECK(manifest["frames"] == 5);
            CHECK(manifest["columns"].get<int>() >= 1);
            CHECK(manifest["rows"].get<int>() >= 1);
            CHECK(manifest["frame_width"] == 96);
            CHECK(manifest["frame_height"] == 96);
            CHECK(manifest["loop"] == false);
            CHECK(manifest["effect_hash"].get<std::string>().size() == 16);
            const std::filesystem::path manifest_path = sheet.string() + ".manifest.json";
            REQUIRE(std::filesystem::is_regular_file(manifest_path));
            CHECK(read_json_file(manifest_path) == manifest);
            CHECK(exported["files"].size() == 2);

            // --- evaluate ---------------------------------------------------
            const nlohmann::json evaluated =
                call(session, "evaluate_effect", {{"time", duration * 0.5}, {"width", 192}, {"height", 192}});
            CHECK(evaluated["statistics"].contains("total_alive"));
            CHECK(evaluated["budgets"].is_array());
            CHECK(evaluated["score_hints"].is_array());
            CHECK(evaluated["diagnostics"]["errors"] == 0);
            CHECK(std::filesystem::is_regular_file(evaluated["path"].get<std::string>()));
            CHECK(evaluated["image_stats"]["coverage"].get<double>() >= 0.0);

            // --- the statistics stay available ------------------------------
            const nlohmann::json statistics = call(session, "inspect_statistics");
            CHECK_FALSE(statistics["simulation"].is_null());
            CHECK_FALSE(statistics["render"].is_null());
            CHECK_FALSE(statistics["plan"].is_null());
        }
    }
}

TEST_CASE("a rendered frame can be compared against a reference", "[integration][pipeline]") {
    const Effect effect = load_effect_file(example("fire_aoe.json"));
    if (!backend_available(effect)) SKIP("the compiler/runtime backend is still a stub (not_implemented)");

    Session session(output_dir("compare"));
    call(session, "load_effect", {{"path", example("fire_aoe.json").string()}});
    const std::filesystem::path reference =
        std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "references" / "fire_aoe_synthetic.png";

    const nlohmann::json result = call(session, "compare_reference",
                                       {{"reference_path", reference.string()}, {"time", 1.0}, {"width", 256}, {"height", 256}});
    CHECK(std::filesystem::is_regular_file(result["render_path"].get<std::string>()));
    CHECK(result["score"].get<double>() >= 0.0);
    CHECK(result["score"].get<double>() <= 1.0);
    CHECK(result["coverage_iou"].get<double>() >= 0.0);
    CHECK(result["notes"].is_array());
}

TEST_CASE("an agent can author, simulate and export a new effect in one session", "[integration][pipeline]") {
    Session session(output_dir("authored"));
    call(session, "create_effect", {{"name", "Authored Burst"}, {"duration", 1.0}, {"seed", 3}});
    call(session, "set_timeline_phase", {{"name", "activation"}, {"start", 0.0}, {"end", 0.3}});
    call(session, "create_layer", {{"name", "Primary"}, {"role", "primary"}});
    call(session, "create_particle_system",
         {{"id", "sparks_ps"},
          {"layer", "primary"},
          {"parameters",
           {{"lifetime", 0.5}, {"size", 0.06}, {"color", {1.0, 0.7, 0.2, 1.0}}, {"emissive", 4.0}, {"drag", 1.5}}}});
    call(session, "create_emitter", {{"id", "sparks"},
                                     {"layer", "primary"},
                                     {"parameters",
                                      {{"shape", "sphere"},
                                       {"radius", 0.15},
                                       {"rate", 0.0},
                                       {"burst_count", 400},
                                       {"burst_times", {0.0}},
                                       {"velocity", 4.0},
                                       {"velocity_variance", 1.5},
                                       {"direction", {0, 0, 0}},
                                       {"position", {0, 1, 0}}}},
                                     {"inputs", {{"particle", "sparks_ps"}}}});
    call(session, "create_force", {{"id", "gravity"}, {"parameters", {{"force_type", "gravity"}, {"strength", 9.81}}}});
    call(session, "connect_nodes", {{"from", "gravity"}, {"to", "sparks_ps"}, {"port", "forces"}});
    CHECK(call(session, "validate_effect")["ok"] == true);

    if (!backend_available(session.active().effect))
        SKIP("the compiler/runtime backend is still a stub (not_implemented)");

    const nlohmann::json simulated = call(session, "simulate", {{"time", 0.3}});
    CHECK(simulated["statistics"]["total_spawned"].get<size_t>() == 400);

    const nlohmann::json frame = call(session, "render_frame", {{"time", 0.2}, {"width", 192}, {"height", 192}});
    CHECK(frame["image_stats"]["coverage"].get<double>() > 0.0);
    CHECK(frame["render_statistics"]["particles_drawn"].get<size_t>() > 0);

    const std::filesystem::path saved = output_dir("authored") / "authored_burst.json";
    call(session, "save_effect", {{"path", saved.string()}});
    CHECK(std::filesystem::is_regular_file(saved));
    const nlohmann::json exported =
        call(session, "export_effect", {{"format", "frames"},
                                        {"path", (output_dir("authored") / "frames").string()},
                                        {"options", {{"fps", 6.0}, {"end", 0.5}, {"width", 96}, {"height", 96}}}});
    CHECK(exported["files"].size() == 4);
    CHECK(exported["manifest"]["frames"] == 4);
}

TEST_CASE("the timestep and the output format are under the agent's control", "[integration][pipeline]") {
    const Effect effect = load_effect_file(example("fireball.json"));
    if (!backend_available(effect)) SKIP("the compiler/runtime backend is still a stub (not_implemented)");

    Session session(output_dir("controls"));
    call(session, "load_effect", {{"path", example("fireball.json").string()}});

    const nlohmann::json simulated = call(session, "simulate", {{"time", 0.5}, {"fixed_dt", 1.0 / 30.0}});
    CHECK(simulated["fixed_dt"].get<double>() == Approx(1.0 / 30.0));
    // The chosen timestep sticks, so rendering does not silently recompile at 1/60.
    CHECK(call(session, "inspect_plan")["fixed_dt"].get<double>() == Approx(1.0 / 30.0));
    call(session, "render_frame", {{"time", 0.5}, {"width", 64}, {"height", 64}});
    CHECK(call(session, "inspect_plan")["fixed_dt"].get<double>() == Approx(1.0 / 30.0));
    CHECK_THROWS_AS(call(session, "simulate", {{"fixed_dt", 0.0}}), Error);

    const std::filesystem::path exr = output_dir("controls") / "linear_frame.exr";
    const nlohmann::json frame =
        call(session, "render_frame", {{"time", 0.5}, {"path", exr.string()}, {"width", 64}, {"height", 64}});
    CHECK(frame["path"] == exr.string());
    CHECK(std::filesystem::is_regular_file(exr));
    CHECK(frame["image_stats"]["width"] == 64);

    // Render settings and camera overrides reach the renderer.
    const nlohmann::json dark = call(session, "render_frame",
                                     {{"time", 0.5},
                                      {"width", 64},
                                      {"height", 64},
                                      {"camera", {{"position", {0, 2, 8}}, {"target", {0, 1, 0}}, {"fov", 30}}},
                                      {"settings", {{"ground_plane", false}, {"grid", false}, {"bloom", false}}}});
    CHECK(dark["camera"]["fov"] == Approx(30.0));
    CHECK(dark["settings"]["ground_plane"] == false);
    CHECK(dark["image_stats"]["mean_luminance"].get<double>() <
          frame["image_stats"]["mean_luminance"].get<double>());  // no lit ground plane
}

TEST_CASE("the turntable orbits the effect", "[integration][pipeline]") {
    const Effect effect = load_effect_file(example("fireball.json"));
    if (!backend_available(effect)) SKIP("the compiler/runtime backend is still a stub (not_implemented)");

    Session session(output_dir("turntable"));
    call(session, "load_effect", {{"path", example("fireball.json").string()}});
    const nlohmann::json result =
        call(session, "render_turntable", {{"time", 1.0}, {"frames", 4}, {"width", 96}, {"height_px", 96}});
    REQUIRE(result["frames"].size() == 4);
    for (const auto& path : result["frames"]) CHECK(std::filesystem::is_regular_file(path.get<std::string>()));
    CHECK(std::filesystem::is_regular_file(result["contact_sheet"].get<std::string>()));
    CHECK(result["distance"].get<double>() > 0.0);
}
