// docs/AGENT_API.md "parameters": constants, keyframe tracks and their specs.
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

nlohmann::json track_json(const Parameter& parameter) {
    nlohmann::json track = nlohmann::json::array();
    for (const Keyframe& key : parameter.track)
        track.push_back({{"time", key.time}, {"value", value_to_json(key.value)}, {"interp", to_string(key.interp)}});
    return track;
}

nlohmann::json param_spec_json(const ParamSpec& spec) {
    nlohmann::json enum_values = nlohmann::json::array();
    for (const std::string& value : spec.enum_values) enum_values.push_back(value);
    return {{"name", spec.name},
            {"type", to_string(spec.type)},
            {"default", value_to_json(spec.default_value)},
            {"min", spec.min ? nlohmann::json(*spec.min) : nlohmann::json()},
            {"max", spec.max ? nlohmann::json(*spec.max) : nlohmann::json()},
            {"enum", std::move(enum_values)},
            {"animatable", spec.animatable},
            {"description", spec.description},
            {"units", spec.units}};
}

// Sets one parameter in place; shared by set_parameter and set_parameters.
void assign_parameter(Node& node, const std::string& name, const nlohmann::json& value) {
    const ParamSpec& spec = require_param_spec(node, name);
    node.parameters[name] = Parameter{parse_value(node, spec, value)};  // a constant clears the track
}

nlohmann::json set_parameter(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "set_parameter");
    const std::string name = require_string(args, "name", "set_parameter");
    if (!has_arg(args, "value")) throw Error("bad_argument", "set_parameter: argument \"value\" is required");
    const Node& node = require_node(doc.effect, node_id);
    require_param_spec(node, name);  // E004 before anything is touched

    Mutation mutation(session, doc);
    Node& target = require_node(doc.effect, node_id);
    assign_parameter(target, name, args.at("value"));
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["node_id"] = node_id;
    result["name"] = name;
    result["value"] = value_to_json(updated.parameters.at(name).value);
    return result;
}

nlohmann::json set_parameters(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "set_parameters");
    const nlohmann::json& parameters = arg(args, "parameters");
    if (!parameters.is_object()) throw Error("bad_argument", "set_parameters: \"parameters\" must be an object");
    const Node& node = require_node(doc.effect, node_id);
    for (const auto& [name, value] : parameters.items()) {
        (void)value;
        require_param_spec(node, name);  // report every unknown name before mutating
    }

    Mutation mutation(session, doc);
    Node& target = require_node(doc.effect, node_id);
    for (const auto& [name, value] : parameters.items()) assign_parameter(target, name, value);
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["node"] = node_json(updated);
    return result;
}

nlohmann::json get_parameter(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "get_parameter");
    const std::string name = require_string(args, "name", "get_parameter");
    const Node& node = require_node(doc.effect, node_id);
    const double time = arg_number(args, "time", 0.0);

    const ParamSpec* spec = SpecRegistry::instance().get(node.type).find_param(name);
    const Parameter* parameter = node.find_param(name);
    if (spec == nullptr) {
        // Unknown names are kept verbatim by the loader, so report what is stored
        // instead of pretending the parameter does not exist.
        if (parameter == nullptr) require_param_spec(node, name);  // throws E004
        return {{"value", value_to_json(parameter->eval(time))},
                {"default", nlohmann::json()},
                {"animated", parameter->animated()},
                {"track", track_json(*parameter)},
                {"spec", nlohmann::json()},
                {"known", false}};
    }
    return {{"value", value_to_json(parameter != nullptr ? parameter->eval(time) : spec->default_value)},
            {"default", value_to_json(spec->default_value)},
            {"animated", parameter != nullptr && parameter->animated()},
            {"track", parameter != nullptr ? track_json(*parameter) : nlohmann::json::array()},
            {"spec", param_spec_json(*spec)},
            {"set", parameter != nullptr},
            {"time", time},
            {"known", true}};
}

nlohmann::json reset_parameter(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "reset_parameter");
    const std::string name = require_string(args, "name", "reset_parameter");
    require_node(doc.effect, node_id);

    Mutation mutation(session, doc);
    Node& node = require_node(doc.effect, node_id);
    const bool removed = node.parameters.erase(name) > 0;
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    const ParamSpec* spec = SpecRegistry::instance().get(updated.type).find_param(name);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["removed"] = removed;
    result["node_id"] = node_id;
    result["name"] = name;
    result["value"] = spec != nullptr ? value_to_json(spec->default_value) : nlohmann::json();
    return result;
}

nlohmann::json set_keyframe(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "set_keyframe");
    const std::string name = require_string(args, "name", "set_keyframe");
    const double time = require_number(args, "time", "set_keyframe");
    if (!has_arg(args, "value")) throw Error("bad_argument", "set_keyframe: argument \"value\" is required");
    const std::string interp_name = arg_string(args, "interp", "linear");
    Interp interp = Interp::Linear;
    if (!parse_interp(interp_name, interp))
        throw Error("bad_argument", "set_keyframe: unknown interp \"" + interp_name + "\"; expected linear, step or smooth");

    const Node& node = require_node(doc.effect, node_id);
    const ParamSpec& spec = require_param_spec(node, name);
    const Value value = parse_value(node, spec, args.at("value"));

    Mutation mutation(session, doc);
    Node& target = require_node(doc.effect, node_id);
    auto it = target.parameters.find(name);
    if (it == target.parameters.end()) it = target.parameters.emplace(name, Parameter{spec.default_value}).first;
    it->second.set_keyframe(time, value, interp);
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    Diagnostics diagnostics = validate_node(doc.effect, updated);
    if (!spec.animatable)
        diagnostics.warning("W006",
                            "parameter \"" + name + "\" is not marked animatable; the keyframe track is stored but "
                            "the runtime samples this parameter once",
                            node_id, name);
    nlohmann::json result = ok_with(diagnostics);
    result["node_id"] = node_id;
    result["name"] = name;
    result["track"] = track_json(updated.parameters.at(name));
    result["animatable"] = spec.animatable;
    return result;
}

nlohmann::json remove_keyframe(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "remove_keyframe");
    const std::string name = require_string(args, "name", "remove_keyframe");
    const double time = require_number(args, "time", "remove_keyframe");
    const Node& node = require_node(doc.effect, node_id);
    if (node.find_param(name) == nullptr)
        throw ToolError("E018", "node \"" + node_id + "\" has no keyframes on \"" + name + "\"", node_id, name);

    Mutation mutation(session, doc);
    Node& target = require_node(doc.effect, node_id);
    const bool removed = target.parameters.at(name).remove_keyframe(time, 1e-6);
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["removed"] = removed;
    result["node_id"] = node_id;
    result["name"] = name;
    result["track"] = track_json(updated.parameters.at(name));
    return result;
}

nlohmann::json clear_track(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "clear_track");
    const std::string name = require_string(args, "name", "clear_track");
    const double time = arg_number(args, "time", 0.0);
    require_node(doc.effect, node_id);

    Mutation mutation(session, doc);
    Node& target = require_node(doc.effect, node_id);
    bool cleared = false;
    auto it = target.parameters.find(name);
    if (it != target.parameters.end() && it->second.animated()) {
        // Keep what the curve evaluated to, so clearing a track never jumps.
        it->second.value = it->second.eval(time);
        it->second.track.clear();
        cleared = true;
    }
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["cleared"] = cleared;
    result["node_id"] = node_id;
    result["name"] = name;
    const Parameter* parameter = updated.find_param(name);
    result["value"] = parameter != nullptr ? value_to_json(parameter->value) : nlohmann::json();
    return result;
}

}  // namespace

void register_parameter_tools(ToolRegistry& registry) {
    registry.add({"set_parameter",
                  "Set one parameter to a constant value, clearing any keyframe track on it. Values use the JSON "
                  "encoding of docs/VOCABULARY.md (numbers, [x,y,z] vectors, [r,g,b,a] or \"#rrggbb\" colours, "
                  "[[t,v],...] curves, [[t,[r,g,b,a]],...] gradients). An unknown name fails with E004 and a wrong "
                  "type with E005; out-of-range values and invalid enums come back as E006/E007 diagnostics. "
                  "Returns {ok, node_id, name, value, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameter.")},
                               {"name", prop("string", "Parameter name, e.g. \"rate\".")},
                               {"value", prop_any("New value, in the JSON encoding for the parameter's type.")}},
                              {"node_id", "name", "value"}),
                  true, "parameters"},
                 set_parameter);

    registry.add({"set_parameters",
                  "Set several parameters on one node in a single undo step. Every name is checked before anything "
                  "changes, so the call either applies completely or not at all. Returns {ok, node, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameters.")},
                               {"parameters", prop("object", "Object of parameter name -> value.")}},
                              {"node_id", "parameters"}),
                  true, "parameters"},
                 set_parameters);

    registry.add({"get_parameter",
                  "Read one parameter: its value (evaluated at `time` when it is keyframed), the spec default, "
                  "whether it is animated, its keyframe track, and its spec (type, min, max, enum values, animatable, "
                  "description, units). Use it before set_parameter when you are unsure of the range or the unit.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameter.")},
                               {"name", prop("string", "Parameter name.")},
                               {"time", prop("number", "Effect time in seconds to evaluate at (default 0).")}},
                              {"node_id", "name"}),
                  false, "parameters"},
                 get_parameter);

    registry.add({"reset_parameter",
                  "Remove an explicit parameter value so the node falls back to the vocabulary default. "
                  "Returns {ok, removed, value, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameter.")},
                               {"name", prop("string", "Parameter name to reset.")}},
                              {"node_id", "name"}),
                  true, "parameters"},
                 reset_parameter);

    registry.add({"set_keyframe",
                  "Insert or replace a keyframe on a parameter at an absolute effect time, turning it into an "
                  "animated track. Keyframe tracks animate over effect seconds; use a curve/gradient parameter "
                  "instead for over-lifetime modulation. Keyframing a parameter the vocabulary does not mark "
                  "animatable is allowed but returns a W006 warning. Returns {ok, track, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameter.")},
                               {"name", prop("string", "Parameter name.")},
                               {"time", prop("number", "Keyframe time in effect seconds.")},
                               {"value", prop_any("Value at that time, in the parameter's JSON encoding.")},
                               {"interp", prop("string", "Interpolation from this key to the next: linear (default), "
                                                         "step or smooth.")}},
                              {"node_id", "name", "time", "value"}),
                  true, "parameters"},
                 set_keyframe);

    registry.add({"remove_keyframe",
                  "Remove the keyframe at a given time (within 1e-6 s) from a parameter's track. "
                  "Returns {ok, removed, track, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameter.")},
                               {"name", prop("string", "Parameter name.")},
                               {"time", prop("number", "Time of the keyframe to remove.")}},
                              {"node_id", "name", "time"}),
                  true, "parameters"},
                 remove_keyframe);

    registry.add({"clear_track",
                  "Drop a parameter's whole keyframe track, keeping the value it had at `time` as the new constant. "
                  "Returns {ok, cleared, value, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node that owns the parameter.")},
                               {"name", prop("string", "Parameter name.")},
                               {"time", prop("number", "Time whose value is kept as the constant (default 0).")}},
                              {"node_id", "name"}),
                  true, "parameters"},
                 clear_track);
}

}  // namespace aether::tools
