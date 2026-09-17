// docs/AGENT_API.md "controls": the effect's named numeric knobs.
// A control is a non-destructive scale/offset on node parameters, folded into
// the document by the compiler; see docs/CONTROLS.md.
#include <algorithm>
#include <set>
#include <string>
#include <vector>

#include "aether/core/controls.hpp"
#include "aether/core/error.hpp"
#include "aether/core/validation.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

Control& require_control(Effect& effect, const std::string& id, const char* tool) {
    if (Control* control = effect.find_control(id)) return *control;
    std::string known;
    for (const Control& c : effect.controls) known += (known.empty() ? "" : ", ") + c.id;
    throw ToolError("E021", std::string(tool) + ": no control \"" + id + "\"" +
                                (known.empty() ? "; this effect has none, call generate_default_controls"
                                               : "; controls are: " + known));
}

// Groups in the order the studio shows them: "Global" first, then whatever the
// controls declare, in document order.
nlohmann::json groups_json(const Effect& effect) {
    nlohmann::json groups = nlohmann::json::array();
    std::set<std::string> seen;
    for (const Control& control : effect.controls) {
        const std::string group = control.group.empty() ? "Global" : control.group;
        if (seen.insert(group).second) groups.push_back(group);
    }
    std::stable_sort(groups.begin(), groups.end(),
                     [](const nlohmann::json& a, const nlohmann::json& b) { return a == "Global" && b != "Global"; });
    return groups;
}

nlohmann::json controls_json(const Effect& effect) {
    nlohmann::json controls = nlohmann::json::array();
    for (const Control& control : effect.controls) controls.push_back(control_to_json(control));
    return controls;
}

nlohmann::json list_result(const Effect& effect) {
    return {{"controls", controls_json(effect)}, {"groups", groups_json(effect)}, {"count", effect.controls.size()}};
}

// Reads the control fields an add_/update_ call may carry onto `control`.
// `defaults` is true for add_control, where an absent field takes the struct
// default instead of keeping the current value.
void read_fields(Control& control, const nlohmann::json& args, bool defaults) {
    if (has_arg(args, "label") || defaults) control.label = arg_string(args, "label", control.label);
    if (has_arg(args, "group") || defaults) control.group = arg_string(args, "group", control.group);
    if (has_arg(args, "unit") || defaults) control.unit = arg_string(args, "unit", control.unit);
    control.min = arg_number(args, "min", control.min);
    control.max = arg_number(args, "max", control.max);
    control.default_value = arg_number(args, "default", control.default_value);
    control.step = arg_number(args, "step", control.step);
    control.value = arg_number(args, "value", has_arg(args, "default") && !has_arg(args, "value")
                                                  ? control.default_value
                                                  : control.value);
    if (control.label.empty()) control.label = control.id;

    if (!has_arg(args, "bindings")) return;
    const nlohmann::json& bindings = arg(args, "bindings");
    if (!bindings.is_array()) throw Error("bad_argument", "\"bindings\" must be an array of {node, parameter, op}");
    control.bindings.clear();
    for (const nlohmann::json& entry : bindings) {
        if (!entry.is_object())
            throw Error("bad_argument", "each binding must be an object {node, parameter, op?}");
        ControlBinding binding;
        binding.node = entry.value("node", std::string());
        binding.parameter = entry.value("parameter", std::string());
        const std::string op = entry.value("op", std::string("multiply"));
        if (!parse_control_op(op, binding.op))
            throw ToolError("E023", "unknown control op \"" + op + "\"; expected multiply, add, set or hue_shift");
        if (binding.node.empty() || binding.parameter.empty())
            throw Error("bad_argument", "each binding needs a \"node\" and a \"parameter\"");
        control.bindings.push_back(std::move(binding));
    }
}

// --- tools ------------------------------------------------------------------

nlohmann::json list_controls(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    return list_result(doc.effect);
}

nlohmann::json set_control(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string id = require_string(args, "id", "set_control");
    const double value = require_number(args, "value", "set_control");
    Control& existing = require_control(doc.effect, id, "set_control");
    if (value < existing.min || value > existing.max)
        throw ToolError("E024", "set_control: " + std::to_string(value) + " is outside the range of control \"" + id +
                                    "\" [" + std::to_string(existing.min) + ", " + std::to_string(existing.max) + "]");

    Mutation mutation(session, doc);
    doc.effect.find_control(id)->value = value;
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["control"] = control_to_json(*doc.effect.find_control(id));
    return result;
}

nlohmann::json reset_controls(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string id = arg_string(args, "id");
    if (!id.empty()) require_control(doc.effect, id, "reset_controls");

    Mutation mutation(session, doc);
    int reset = 0;
    for (Control& control : doc.effect.controls) {
        if (!id.empty() && control.id != id) continue;
        if (control.value == control.default_value) continue;
        control.value = control.default_value;
        ++reset;
    }
    mutation.commit();

    nlohmann::json result = list_result(doc.effect);
    result["ok"] = true;
    result["reset"] = reset;
    return result;
}

nlohmann::json add_control(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string label = require_string(args, "label", "add_control");
    std::string id = arg_string(args, "id");
    if (id.empty()) id = doc.effect.unique_control_id(slugify(label));
    if (doc.effect.find_control(id) != nullptr)
        throw ToolError("E021", "add_control: a control with id \"" + id + "\" already exists");

    Control control;
    control.id = id;
    control.label = label;
    read_fields(control, args, /*defaults=*/true);

    Mutation mutation(session, doc);
    doc.effect.controls.push_back(std::move(control));
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["control"] = control_to_json(*doc.effect.find_control(id));
    return result;
}

nlohmann::json update_control(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string id = require_string(args, "id", "update_control");
    require_control(doc.effect, id, "update_control");

    Mutation mutation(session, doc);
    read_fields(*doc.effect.find_control(id), args, /*defaults=*/false);
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["control"] = control_to_json(*doc.effect.find_control(id));
    return result;
}

nlohmann::json remove_control(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string id = require_string(args, "id", "remove_control");
    require_control(doc.effect, id, "remove_control");

    Mutation mutation(session, doc);
    std::vector<Control>& controls = doc.effect.controls;
    controls.erase(std::remove_if(controls.begin(), controls.end(),
                                  [&](const Control& c) { return c.id == id; }),
                   controls.end());
    mutation.commit();

    nlohmann::json result = list_result(doc.effect);
    result["ok"] = true;
    result["removed"] = id;
    return result;
}

nlohmann::json generate_default_controls_tool(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const bool replace = arg_bool(args, "replace", false);
    std::vector<Control> generated = generate_default_controls(doc.effect);

    Mutation mutation(session, doc);
    int added = 0;
    if (replace) {
        added = static_cast<int>(generated.size());
        doc.effect.controls = std::move(generated);
    } else {
        // Keep everything the author wrote, including its current value, and
        // only fill in the knobs that are missing.
        for (Control& control : generated) {
            if (doc.effect.find_control(control.id) != nullptr) continue;
            doc.effect.controls.push_back(std::move(control));
            ++added;
        }
    }
    mutation.commit();

    nlohmann::json result = list_result(doc.effect);
    result["ok"] = true;
    result["added"] = added;
    result["replaced"] = replace;
    result["diagnostics"] = validate(doc.effect).to_json();
    return result;
}

}  // namespace

void register_control_tools(ToolRegistry& registry) {
    registry.add({"list_controls",
                  "List the effect's controls: the named numeric knobs an author or a game can move without editing "
                  "the graph. Each one has {id, label, group, min, max, default, value, step, unit, bindings} where "
                  "every binding is {node, parameter, op} with op multiply|add|set|hue_shift. `group` is the UI "
                  "section (\"Global\", or a layer name). Returns {controls, groups, count}.",
                  make_schema({}),
                  false, "controls"},
                 list_controls);

    registry.add({"set_control",
                  "Move one control to a value inside its [min, max]. Nothing in the graph is rewritten: the "
                  "compiler folds controls into a copy of the document, so the authored parameter values stay as "
                  "they are and the change is undoable and reversible. Returns {ok, control, diagnostics}.",
                  make_schema({{"id", prop("string", "Control id, e.g. \"flames_intensity\".")},
                               {"value", prop("number", "New value, inside the control's [min, max].")}},
                              {"id", "value"}),
                  true, "controls"},
                 set_control);

    registry.add({"reset_controls",
                  "Put every control back to its default (or just `id` when given). Returns {ok, reset, controls}.",
                  make_schema({{"id", prop("string", "Reset only this control. Omit for all of them.")}}),
                  true, "controls"},
                 reset_controls);

    registry.add({"add_control",
                  "Add a control. Give it a human label (\"Flame height\", \"Ember amount\"), a group (\"Global\" or "
                  "the layer name) and the bindings it drives: [{node, parameter, op}] with op multiply (scalars, "
                  "every component of a vector, the rgb of a colour), add, set, or hue_shift (degrees, colours and "
                  "gradients). Multipliers use min 0, max 3, default 1, unit \"x\". Returns {ok, control, "
                  "diagnostics}; E022/E023 mean a binding does not fit its parameter.",
                  make_schema({{"label", prop("string", "Human label shown on the slider, e.g. \"Flame height\".")},
                               {"id", prop("string", "Control id (^[a-z][a-z0-9_]*$). Derived from the label when omitted.")},
                               {"group", prop("string", "UI section: \"Global\" or a layer name.")},
                               {"min", prop("number", "Lowest value (default 0).")},
                               {"max", prop("number", "Highest value (default 3).")},
                               {"default", prop("number", "Value the reset button returns to (default 1).")},
                               {"value", prop("number", "Current value (defaults to `default`).")},
                               {"step", prop("number", "Slider/arrow-key increment (default 0.01).")},
                               {"unit", prop("string", "Unit shown next to the number, e.g. \"x\" or \"deg\".")},
                               {"bindings", prop("array", "[{node, parameter, op}] this control drives.")}},
                              {"label"}),
                  true, "controls"},
                 add_control);

    registry.add({"update_control",
                  "Change a control's definition: any of label, group, min, max, default, value, step, unit, and "
                  "bindings (given, the list replaces the current one). Returns {ok, control, diagnostics}.",
                  make_schema({{"id", prop("string", "Control to update.")},
                               {"label", prop("string", "New label.")},
                               {"group", prop("string", "New group.")},
                               {"min", prop("number", "New minimum.")},
                               {"max", prop("number", "New maximum.")},
                               {"default", prop("number", "New default.")},
                               {"value", prop("number", "New current value.")},
                               {"step", prop("number", "New increment.")},
                               {"unit", prop("string", "New unit.")},
                               {"bindings", prop("array", "Replacement [{node, parameter, op}] list.")}},
                              {"id"}),
                  true, "controls"},
                 update_control);

    registry.add({"remove_control",
                  "Delete a control. The parameters it was bound to keep the values the author gave them. "
                  "Returns {ok, removed, controls}.",
                  make_schema({{"id", prop("string", "Control to remove.")}}, {"id"}),
                  true, "controls"},
                 remove_control);

    registry.add({"generate_default_controls",
                  "Build a sensible control set for this effect: a Global group (Intensity, Size, Density, Opacity, "
                  "Hue) and one group per layer (Intensity, Size, Density, Opacity), bound by node type - emissive "
                  "and light intensity for Intensity, particle/decal/beam size for Size, emitter rate and bursts for "
                  "Density, opacity and volume density for Opacity, every colour and gradient for Hue. Existing "
                  "controls are kept unless `replace` is true. Use it as a starting point, then rename and trim with "
                  "update_control/remove_control. Returns {ok, added, replaced, controls, groups}.",
                  make_schema({{"replace", prop("boolean", "Replace the whole control set instead of filling in what "
                                                           "is missing (default false).")}}),
                  true, "controls"},
                 generate_default_controls_tool);
}

}  // namespace aether::tools
