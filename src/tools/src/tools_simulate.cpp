// docs/AGENT_API.md "simulate": driving the deterministic runtime.
#include <algorithm>
#include <cmath>
#include <string>

#include "aether/core/error.hpp"
#include "aether/sim/runtime.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

nlohmann::json sample_json(const sim::IRuntime& runtime) {
    const sim::Statistics statistics = runtime.statistics();
    nlohmann::json per_system = nlohmann::json::object();
    for (const sim::SystemStatistics& system : statistics.systems) per_system[system.system_id] = system.alive;
    return {{"time", runtime.time()},
            {"frame", runtime.frame_index()},
            {"total_alive", statistics.total_alive},
            {"per_system", std::move(per_system)}};
}

nlohmann::json simulate(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    const double time = arg_number(args, "time", doc.effect.duration);
    runtime.reset();  // "runs from 0 to time"
    runtime.simulate_to(time);
    doc.last_simulation_statistics = runtime.statistics().to_json();
    return {{"statistics", doc.last_simulation_statistics},
            {"time", runtime.time()},
            {"frame", runtime.frame_index()},
            {"fixed_dt", runtime.fixed_dt()},
            {"backend", runtime.backend_name()}};
}

nlohmann::json simulate_range(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    const double start = require_number(args, "start", "simulate_range");
    const double end = require_number(args, "end", "simulate_range");
    if (end < start) throw Error("bad_argument", "simulate_range: \"end\" must not be before \"start\"");
    double sample_every = arg_number(args, "sample_every", runtime.fixed_dt());
    if (!(sample_every > 0.0)) sample_every = runtime.fixed_dt();

    if (runtime.time() > start) runtime.reset();
    runtime.simulate_to(start);

    nlohmann::json samples = nlohmann::json::array();
    samples.push_back(sample_json(runtime));
    const int steps = static_cast<int>(std::floor((end - start) / sample_every + 1e-9));
    for (int i = 1; i <= steps; ++i) {
        runtime.simulate_to(start + sample_every * i);
        samples.push_back(sample_json(runtime));
    }
    if (runtime.time() < end) {
        runtime.simulate_to(end);
        samples.push_back(sample_json(runtime));
    }

    doc.last_simulation_statistics = runtime.statistics().to_json();
    return {{"samples", std::move(samples)},
            {"statistics", doc.last_simulation_statistics},
            {"start", start},
            {"end", end},
            {"sample_every", sample_every}};
}

nlohmann::json step_simulation(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    const int frames = std::max(0, arg_int(args, "frames", 1));
    for (int i = 0; i < frames; ++i) runtime.step();
    doc.last_simulation_statistics = runtime.statistics().to_json();
    return {{"time", runtime.time()},
            {"frame", runtime.frame_index()},
            {"statistics", doc.last_simulation_statistics}};
}

nlohmann::json reset_simulation(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    runtime.reset();
    doc.last_simulation_statistics = runtime.statistics().to_json();
    return {{"ok", true}, {"time", runtime.time()}, {"frame", runtime.frame_index()}};
}

}  // namespace

void register_simulate_tools(ToolRegistry& registry) {
    registry.add({"simulate",
                  "Run the deterministic runtime from t=0 to `time` (the effect duration by default) and return the "
                  "simulation statistics: per system alive/spawned/died/peak/dropped counts, total particles and "
                  "step timings. Use it to check that an effect actually produces particles before rendering.",
                  make_schema({{"time", prop("number", "End time in seconds (default: the effect duration).")},
                               {"fixed_dt", prop("number", "Fixed timestep in seconds; it sticks for the session once set (default 1/60).")}}),
                  false, "simulate"},
                 simulate);

    registry.add({"simulate_range",
                  "Simulate from `start` to `end` and sample the live particle counts along the way, so you can see "
                  "how the effect builds and fades. Returns {samples:[{time,total_alive,per_system}], statistics}.",
                  make_schema({{"start", prop("number", "First sample time in seconds.")},
                               {"end", prop("number", "Last sample time in seconds.")},
                               {"sample_every", prop("number", "Sample spacing in seconds (default: the timestep).")},
                               {"fixed_dt", prop("number", "Fixed timestep in seconds; it sticks for the session once set (default 1/60).")}},
                              {"start", "end"}),
                  false, "simulate"},
                 simulate_range);

    registry.add({"step_simulation",
                  "Advance the runtime by a number of fixed steps from where it currently is, without resetting. "
                  "Returns {time, frame, statistics}.",
                  make_schema({{"frames", prop("integer", "Number of fixed steps to advance (default 1).")},
                               {"fixed_dt", prop("number", "Fixed timestep in seconds; it sticks for the session once set (default 1/60).")}}),
                  false, "simulate"},
                 step_simulation);

    registry.add({"reset_simulation",
                  "Rewind the runtime to t=0 with no particles alive. Returns {ok, time, frame}.",
                  make_schema({{"fixed_dt", prop("number", "Fixed timestep in seconds; it sticks for the session once set (default 1/60).")}}), false,
                  "simulate"},
                 reset_simulation);
}

}  // namespace aether::tools
