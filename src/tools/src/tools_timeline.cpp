// docs/AGENT_API.md "timeline": named phases and the nodes bound to them.
#include <algorithm>
#include <map>
#include <string>
#include <vector>

#include "aether/core/controls.hpp"
#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"

#include "tool_support.hpp"

namespace aether::tools {
namespace {

// The literal `phase` parameter of a time-bound node, if it has one.
std::string node_phase(const Node& node) {
    const Parameter* parameter = node.find_param("phase");
    if (parameter == nullptr) return {};
    if (const std::string* name = std::get_if<std::string>(&parameter->value)) return *name;
    return {};
}

nlohmann::json set_timeline_phase(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string name = require_string(args, "name", "set_timeline_phase");
    const double start = require_number(args, "start", "set_timeline_phase");
    const double end = require_number(args, "end", "set_timeline_phase");

    Mutation mutation(session, doc);
    TimelinePhase* phase = doc.effect.timeline.find(name);
    const bool created = phase == nullptr;
    if (created) {
        doc.effect.timeline.phases.push_back(TimelinePhase{name, start, end});
        // Phases read best in time order, and the compiler resolves them by name.
        std::stable_sort(doc.effect.timeline.phases.begin(), doc.effect.timeline.phases.end(),
                         [](const TimelinePhase& a, const TimelinePhase& b) { return a.start < b.start; });
    } else {
        phase->start = start;
        phase->end = end;
    }
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["timeline"] = timeline_json(doc.effect.timeline);
    result["created"] = created;
    return result;
}

nlohmann::json remove_timeline_phase(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string name = require_string(args, "name", "remove_timeline_phase");

    Mutation mutation(session, doc);
    auto& phases = doc.effect.timeline.phases;
    const size_t before = phases.size();
    phases.erase(std::remove_if(phases.begin(), phases.end(), [&](const TimelinePhase& p) { return p.name == name; }),
                 phases.end());
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["timeline"] = timeline_json(doc.effect.timeline);
    result["removed"] = before != doc.effect.timeline.phases.size();
    return result;
}

nlohmann::json get_timeline(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    nlohmann::json bound = nlohmann::json::object();
    for (const TimelinePhase& phase : doc.effect.timeline.phases) bound[phase.name] = nlohmann::json::array();

    nlohmann::json unknown = nlohmann::json::array();
    for (const Node& node : doc.effect.nodes) {
        const std::string phase = node_phase(node);
        if (phase.empty()) continue;
        if (!bound.contains(phase)) {
            bound[phase] = nlohmann::json::array();
            unknown.push_back(phase);
        }
        bound[phase].push_back(node.id);
    }
    // Phases and durations are effect seconds; `wall_duration` is how long the
    // effect takes to play at its own `time_scale` (docs/RUNTIME.md 11). The
    // speed is reported *resolved*, i.e. with the document's controls folded in
    // the way compile() folds them, so this is the number a host would use.
    // get_effect_json is where the authored value lives.
    Effect resolved = doc.effect;
    apply_controls(resolved);
    return {{"duration", doc.effect.duration},
            {"time_scale", resolved.time_scale},
            {"wall_duration", resolved.wall_duration()},
            {"phases", timeline_json(doc.effect.timeline)["phases"]},
            {"bound_nodes", std::move(bound)},
            {"unknown_phases", std::move(unknown)}};
}

}  // namespace

void register_timeline_tools(ToolRegistry& registry) {
    registry.add({"set_timeline_phase",
                  "Create or move a named timeline phase with absolute start/end times in seconds. Use the standard "
                  "names anticipation, activation, peak, sustain and decay (any subset) so time-bound nodes can bind "
                  "their window to a phase by setting their `phase` parameter. Returns {ok, timeline, diagnostics}.",
                  make_schema({{"name", prop("string", "Phase name, e.g. \"activation\".")},
                               {"start", prop("number", "Phase start in seconds.")},
                               {"end", prop("number", "Phase end in seconds (must be after start).")}},
                              {"name", "start", "end"}),
                  true, "timeline"},
                 set_timeline_phase);

    registry.add({"remove_timeline_phase",
                  "Remove a timeline phase. Nodes that still reference it keep their `phase` parameter and will "
                  "report a W001 warning when compiled. Returns {ok, timeline, diagnostics}.",
                  make_schema({{"name", prop("string", "Phase name to remove.")}}, {"name"}), true, "timeline"},
                 remove_timeline_phase);

    registry.add({"get_timeline",
                  "Return the effect duration, its resolved time_scale and wall_duration (duration / time_scale: "
                  "how long it takes to play, with any Speed control folded in), the timeline phases and, per "
                  "phase, the ids of the nodes bound to it "
                  "(nodes whose `phase` parameter names it). Use it to see how the effect is staged in time.",
                  make_schema({}), false, "timeline"},
                 get_timeline);
}

}  // namespace aether::tools
