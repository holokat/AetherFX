// docs/AGENT_API.md "inspect": reading the graph, a node, the plan and statistics.
#include <map>
#include <string>
#include <vector>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

// SpecRegistry::to_json() builds the whole vocabulary; do it once.
const nlohmann::json& vocabulary() {
    static const nlohmann::json json = SpecRegistry::instance().to_json();
    return json;
}

nlohmann::json consumers_json(const Effect& effect, const NodeId& id) {
    nlohmann::json consumers = nlohmann::json::array();
    for (const Node* node : effect.consumers_of(id)) consumers.push_back(node->id);
    return consumers;
}

nlohmann::json window_json(const Effect& effect, const Node& node) {
    const TimeWindow window = node_window(node);
    double start = window.start;
    double end = window.end;
    compiler::resolve_window(effect, node, start, end, nullptr);
    return {{"start", start}, {"end", end < 0.0 ? effect.duration : end}, {"until_effect_end", end < 0.0}};
}

// The compiled plan when it built, otherwise nullptr: tier/backend then read null
// instead of the tool failing, so inspect_graph works on a broken graph too.
const compiler::CompiledEffect* try_compile(Session& session, Document& doc, const nlohmann::json& args) {
    try {
        const compiler::CompiledEffect& compiled = session.compiled(doc, compile_options_for(doc, args));
        return compiled.ok() ? &compiled : nullptr;
    } catch (const std::exception&) {
        return nullptr;
    }
}

nlohmann::json inspect_graph(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const bool verbose = arg_bool(args, "verbose", false);
    const compiler::CompiledEffect* compiled = try_compile(session, doc, args);
    const nlohmann::json document = effect_to_json(doc.effect);

    std::map<std::string, size_t> node_counts;
    for (const Node& node : doc.effect.nodes)
        if (node.layer) node_counts[*node.layer] += 1;

    nlohmann::json layers = nlohmann::json::array();
    for (const Layer& layer : doc.effect.layers) {
        nlohmann::json entry = layer_json(layer);
        entry["node_count"] = node_counts.count(layer.id) != 0 ? node_counts[layer.id] : 0;
        layers.push_back(std::move(entry));
    }

    nlohmann::json nodes = nlohmann::json::array();
    for (size_t i = 0; i < doc.effect.nodes.size(); ++i) {
        const Node& node = doc.effect.nodes[i];
        const compiler::CompiledNode* planned = compiled != nullptr ? compiled->find(node.id) : nullptr;
        nlohmann::json entry{
            {"id", node.id},
            {"type", to_string(node.type)},
            {"layer", node.layer ? nlohmann::json(*node.layer) : nlohmann::json()},
            {"enabled", node.enabled},
            {"parent", node.parent ? nlohmann::json(*node.parent) : nlohmann::json()},
            {"inputs", document["nodes"][i].value("inputs", nlohmann::json::object())},
            {"consumers", consumers_json(doc.effect, node.id)},
            {"tier", planned != nullptr ? nlohmann::json(to_string(planned->tier)) : nlohmann::json()},
            {"backend", planned != nullptr ? nlohmann::json(planned->backend) : nlohmann::json()},
            {"window", planned != nullptr ? nlohmann::json{{"start", planned->start_time},
                                                           {"end", planned->end_time < 0.0 ? doc.effect.duration
                                                                                           : planned->end_time},
                                                           {"until_effect_end", planned->end_time < 0.0}}
                                          : window_json(doc.effect, node)}};
        if (verbose) entry["parameters"] = document["nodes"][i].value("parameters", nlohmann::json::object());
        nodes.push_back(std::move(entry));
    }

    return {{"effect_id", doc.id},
            {"name", doc.effect.name},
            {"duration", doc.effect.duration},
            {"seed", doc.effect.seed},
            {"timeline", document["timeline"]},
            {"layers", std::move(layers)},
            {"nodes", std::move(nodes)},
            {"compiled", compiled != nullptr},
            {"diagnostics", validate(doc.effect).to_json()}};
}

nlohmann::json inspect_node(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "inspect_node");
    const Node& node = require_node(doc.effect, node_id);
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    const compiler::CompiledEffect* compiled = try_compile(session, doc, args);
    const compiler::CompiledNode* planned = compiled != nullptr ? compiled->find(node.id) : nullptr;

    // Every parameter the vocabulary defines, with the value the runtime would see.
    nlohmann::json effective = nlohmann::json::object();
    for (const ParamSpec& param : spec.params) {
        const Parameter* parameter = node.find_param(param.name);
        effective[param.name] = value_to_json(parameter != nullptr ? parameter->eval(0.0) : param.default_value);
    }
    for (const auto& [name, parameter] : node.parameters)
        if (!effective.contains(name)) effective[name] = value_to_json(parameter.eval(0.0));

    nlohmann::json resolved = nlohmann::json::object();
    for (const auto& [port, refs] : node.inputs) {
        nlohmann::json entries = nlohmann::json::array();
        for (const NodeRef& ref : refs) {
            const Node* target = doc.effect.find_node(ref.node);
            entries.push_back({{"ref", ref.str()},
                               {"node", ref.node},
                               {"port", ref.port},
                               {"exists", target != nullptr},
                               {"type", target != nullptr ? nlohmann::json(to_string(target->type)) : nlohmann::json()},
                               {"enabled", target != nullptr ? nlohmann::json(target->enabled) : nlohmann::json()}});
        }
        resolved[port] = std::move(entries);
    }

    return {{"node", node_json(node)},
            {"spec", vocabulary()["node_types"][std::string(to_string(node.type))]},
            {"effective_parameters", std::move(effective)},
            {"resolved_inputs", std::move(resolved)},
            {"consumers", consumers_json(doc.effect, node.id)},
            {"tier", planned != nullptr ? nlohmann::json(to_string(planned->tier)) : nlohmann::json()},
            {"backend", planned != nullptr ? nlohmann::json(planned->backend) : nlohmann::json()},
            {"window", window_json(doc.effect, node)},
            {"diagnostics", validate_node(doc.effect, node).to_json()}};
}

nlohmann::json inspect_statistics(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const compiler::CompiledEffect* compiled = try_compile(session, doc, args);
    return {{"simulation", doc.last_simulation_statistics},
            {"render", doc.last_render_statistics},
            {"plan", compiled != nullptr ? compiled->plan_json() : nlohmann::json()}};
}

nlohmann::json inspect_plan(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const compiler::CompiledEffect& compiled = session.compiled(doc, compile_options_for(doc, args));
    nlohmann::json plan = compiled.plan_json();
    if (!plan.is_object()) plan = nlohmann::json{{"plan", std::move(plan)}};
    plan["ok"] = compiled.ok();
    plan["fixed_dt"] = compiled.fixed_dt;
    return plan;
}

nlohmann::json validate_effect(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const Diagnostics diagnostics = validate(doc.effect);
    return {{"diagnostics", diagnostics.to_json()}, {"ok", diagnostics.ok()}, {"summary", diagnostics.summary()}};
}

}  // namespace

void register_inspect_tools(ToolRegistry& registry) {
    registry.add({"inspect_graph",
                  "Return the whole effect as a readable summary: name, duration, seed, timeline, layers with their "
                  "node counts, and every node with its type, layer, enabled flag, parent, inputs, consumers and the "
                  "tier/backend/window the compiler picked for it, plus the validation diagnostics. This is the tool "
                  "to call first when you pick up an effect you did not build.",
                  make_schema({{"verbose", prop("boolean", "Also include each node's parameters (default false).")}}),
                  false, "inspect"},
                 inspect_graph);

    registry.add({"inspect_node",
                  "Return one node in full: its stored form, its vocabulary spec, every parameter with the effective "
                  "value (defaults filled in), the resolved input references with their target types, the nodes that "
                  "consume it, its tier and its resolved time window, plus diagnostics for that node alone.",
                  make_schema({{"node_id", prop("string", "Node to inspect.")}}, {"node_id"}), false, "inspect"},
                 inspect_node);

    registry.add({"inspect_statistics",
                  "Return the last known simulation statistics, the last render statistics and the compiled plan for "
                  "the active effect, without simulating or rendering again.",
                  make_schema({}), false, "inspect"},
                 inspect_statistics);

    registry.add({"inspect_plan",
                  "Compile the effect and return the execution plan: per node the tier, backend, resolved window, "
                  "seed and resolved references, plus tier counts, baked resources and compile diagnostics. Use it to "
                  "see what the engine will actually run, and why a node was downgraded.",
                  make_schema({}), false, "inspect"},
                 inspect_plan);

    registry.add({"validate_effect",
                  "Validate the effect against the vocabulary and return every finding with its code, message, node "
                  "and parameter (E0xx errors, W0xx warnings). Call it after a batch of edits, before simulating.",
                  make_schema({}), false, "inspect"},
                 validate_effect);
}

}  // namespace aether::tools
