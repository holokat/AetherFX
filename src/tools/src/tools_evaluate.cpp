// docs/AGENT_API.md "evaluate": comparing a render against a reference, and the
// self-check an agent runs after editing.
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/error.hpp"
#include "aether/core/validation.hpp"
#include "aether/render/image_io.hpp"
#include "aether/render/renderer.hpp"
#include "aether/sim/runtime.hpp"
#include "image_metrics.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

std::string percent(double fraction) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(1) << fraction * 100.0 << "%";
    return out.str();
}

// Renders one frame at `time` with the session defaults and returns its path.
std::filesystem::path render_for_evaluation(Session& session, Document& doc, const nlohmann::json& args, double time,
                                            const std::string& suffix, nlohmann::json& render_statistics) {
    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    if (time < runtime.time()) runtime.reset();
    runtime.simulate_to(time);
    const RenderSettings settings = render_settings_for(session, args);
    const CameraDesc camera = camera_for(session, args, runtime.state().camera);
    const Image image = session.renderer().render(runtime.state(), runtime.compiled().resources, camera, settings);
    render_statistics = session.renderer().last_statistics().to_json();
    const std::filesystem::path path =
        output_path(session, slugify(doc.effect.name) + suffix + "_t" + time_tag(time) + ".png");
    ensure_dir(path.parent_path());
    render::write_png(path, image);
    doc.last_simulation_statistics = runtime.statistics().to_json();
    doc.last_render_statistics = render_statistics;
    return path;
}

nlohmann::json compare_reference(Session& session, const nlohmann::json& args) {
    Document* doc = nullptr;
    const std::filesystem::path reference =
        resolve_input_path(session, require_string(args, "reference_path", "compare_reference"));
    std::error_code ec;
    if (!std::filesystem::is_regular_file(reference, ec))
        throw Error("io", "compare_reference: no reference image at \"" + reference.string() + "\"");

    std::filesystem::path render_path;
    nlohmann::json render_statistics;
    if (has_arg(args, "render_path")) {
        render_path = resolve_input_path(session, arg_string(args, "render_path"));
        if (!std::filesystem::is_regular_file(render_path, ec))
            throw Error("io", "compare_reference: no render at \"" + render_path.string() + "\"");
    } else if (has_arg(args, "time")) {
        doc = &session.document_for(args);
        render_path = render_for_evaluation(session, *doc, args, arg_number(args, "time", 0.0), "_compare",
                                            render_statistics);
    } else {
        throw Error("bad_argument",
                    "compare_reference: pass \"render_path\" to compare an existing image, or \"time\" to render "
                    "the active effect first");
    }

    nlohmann::json result = compare_image_files(reference, render_path);
    result["reference_path"] = reference.string();
    result["render_path"] = render_path.string();
    if (!render_statistics.is_null()) result["render_statistics"] = render_statistics;
    return result;
}

nlohmann::json evaluate_effect(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const double time = arg_number(args, "time", doc.effect.duration);

    nlohmann::json render_statistics;
    const std::filesystem::path path = render_for_evaluation(session, doc, args, time, "_evaluate", render_statistics);
    const nlohmann::json statistics = doc.last_simulation_statistics;
    const nlohmann::json stats = image_stats_file(path);

    const compiler::CompileOptions options;
    nlohmann::json budgets = nlohmann::json::array();
    const size_t total_alive = statistics.value("total_alive", size_t{0});
    if (total_alive > options.particle_budget)
        budgets.push_back("total particles " + std::to_string(total_alive) + " exceeds the budget of " +
                          std::to_string(options.particle_budget) + ": lower rate or max_particles");
    const double overdraw = render_statistics.value("overdraw", 0.0);
    if (overdraw > 8.0) {
        std::ostringstream note;
        note << std::fixed << std::setprecision(1) << "overdraw " << overdraw
             << "x: too many overlapping additive particles, reduce size, rate or opacity";
        budgets.push_back(note.str());
    }
    if (statistics.contains("systems") && statistics["systems"].is_array()) {
        for (const auto& system : statistics["systems"]) {
            const size_t dropped = system.value("dropped", size_t{0});
            if (dropped > 0)
                budgets.push_back("system " + system.value("system_id", std::string("?")) + " dropped " +
                                  std::to_string(dropped) + " spawns: raise max_particles or lower rate");
        }
    }

    nlohmann::json hints = nlohmann::json::array();
    if (total_alive == 0)
        hints.push_back("nothing is alive at t=" + time_tag(time) +
                        ": check emitter rate, burst_times and the node windows");
    const double coverage = stats.value("coverage", 0.0);
    if (coverage < 0.01)
        hints.push_back("the effect covers only " + percent(coverage) +
                        " of the frame: increase size, rate or the emitter radius, or move the camera closer");
    else if (coverage > 0.6)
        hints.push_back("the effect covers " + percent(coverage) +
                        " of the frame: it may be too large or too close to the camera");
    if (stats.value("max_luminance", 0.0) < 0.2)
        hints.push_back("the brightest pixel is dim: raise emissive or the material emissive_intensity");
    const Diagnostics diagnostics = validate(doc.effect);
    if (diagnostics.warning_count() > 0)
        hints.push_back(std::to_string(diagnostics.warning_count()) +
                        " validation warnings: call validate_effect to see them");

    return {{"statistics", statistics},
            {"budgets", std::move(budgets)},
            {"render_statistics", render_statistics},
            {"diagnostics", diagnostics.to_json()},
            {"score_hints", std::move(hints)},
            {"path", path.string()},
            {"image_stats", stats},
            {"time", time}};
}

}  // namespace

void register_evaluate_tools(ToolRegistry& registry) {
    registry.add({"compare_reference",
                  "Compare a render against a reference image and score the match. Both images are letterboxed to "
                  "256x256 and measured on coverage IoU, palette distance, luminance histogram distance, centroid "
                  "offset and radial energy profile; `score` is 1 for an indistinguishable match and drops as the "
                  "differences grow. `notes` say in words what to change. Pass `render_path` for an existing image, "
                  "or `time` to render the active effect first.",
                  make_schema({{"reference_path", prop("string", "Reference image to match.")},
                               {"render_path", prop("string", "Rendered image to compare; omit to use `time`.")},
                               {"time", prop("number", "Render the active effect at this time and compare that.")},
                               {"camera", prop("object", "Camera override for the render (see render_frame).")},
                               {"settings", prop("object", "RenderSettings subset for the render.")},
                               {"width", prop("integer", "Render width in pixels (default 512).")},
                               {"height", prop("integer", "Render height in pixels (default 512).")}},
                              {"reference_path"}),
                  false, "evaluate"},
                 compare_reference);

    registry.add({"evaluate_effect",
                  "Simulate to `time`, render one frame and report everything needed to judge the result: simulation "
                  "statistics, budget warnings (particle counts, overdraw, dropped spawns), render statistics, "
                  "validation diagnostics, the measured image and short hints on what to change next. Call it after "
                  "a round of edits to decide whether the effect is good enough.",
                  make_schema({{"time", prop("number", "Time to evaluate at (default: the effect duration).")},
                               {"camera", prop("object", "Camera override (see render_frame).")},
                               {"settings", prop("object", "RenderSettings subset.")},
                               {"width", prop("integer", "Render width in pixels (default 512).")},
                               {"height", prop("integer", "Render height in pixels (default 512).")}}),
                  false, "evaluate"},
                 evaluate_effect);
}

}  // namespace aether::tools
