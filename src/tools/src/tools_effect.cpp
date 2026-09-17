// docs/AGENT_API.md "effect": documents, effect properties and the vocabulary.
#include <filesystem>
#include <string>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"
#include "aether/procedural/texture_graph.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

// `template`: "empty", the name of a file in examples/effects ("fire_bolt",
// "fire_bolt.json"), or an absolute/relative path to a document.
std::filesystem::path resolve_template(const std::string& name) {
    std::filesystem::path direct(name);
    std::error_code ec;
    if (direct.is_absolute() || name.find('/') != std::string::npos) {
        if (std::filesystem::is_regular_file(direct, ec)) return direct;
        throw Error("bad_argument", "create_effect: no template document at \"" + name + "\"");
    }
    const std::filesystem::path dir = examples_dir() / "effects";
    const std::filesystem::path with_extension =
        dir / (name.size() > 5 && name.substr(name.size() - 5) == ".json" ? name : name + ".json");
    if (std::filesystem::is_regular_file(with_extension, ec)) return with_extension;

    std::string available;
    if (std::filesystem::is_directory(dir, ec)) {
        for (const auto& entry : std::filesystem::directory_iterator(dir, ec)) {
            if (entry.path().extension() != ".json") continue;
            available += (available.empty() ? "" : ", ") + entry.path().stem().string();
        }
    }
    throw Error("bad_argument", "create_effect: unknown template \"" + name + "\"; use \"empty\"" +
                                    (available.empty() ? "" : " or one of [" + available + "]") + ", or a file path");
}

nlohmann::json effect_summary(const Session& session, const Document& doc, const std::string& active_id) {
    return {{"effect_id", doc.id},
            {"name", doc.effect.name},
            {"path", doc.path ? nlohmann::json(doc.path->string()) : nlohmann::json()},
            {"dirty", doc.dirty},
            {"active", doc.id == active_id},
            {"nodes", doc.effect.nodes.size()},
            {"output_dir", session.output_dir().string()}};
}

nlohmann::json create_effect(Session& session, const nlohmann::json& args) {
    const std::string template_name = arg_string(args, "template", "empty");
    Effect effect;
    if (!template_name.empty() && template_name != "empty") effect = load_effect_file(resolve_template(template_name));

    if (has_arg(args, "name")) effect.name = require_string(args, "name", "create_effect");
    else if (template_name.empty() || template_name == "empty") effect.name = "untitled";
    if (has_arg(args, "duration")) effect.duration = arg_number(args, "duration", effect.duration);
    else if (template_name.empty() || template_name == "empty") effect.duration = 2.0;
    if (has_arg(args, "time_scale")) effect.time_scale = arg_number(args, "time_scale", effect.time_scale);
    if (has_arg(args, "seed")) effect.seed = static_cast<uint32_t>(arg_int(args, "seed", 1));
    else if (template_name.empty() || template_name == "empty") effect.seed = 1;

    Document& doc = session.create(std::move(effect));
    doc.dirty = true;
    return {{"effect_id", doc.id},
            {"effect", effect_to_json(doc.effect)},
            {"diagnostics", validate(doc.effect).to_json()}};
}

nlohmann::json delete_effect(Session& session, const nlohmann::json& args) {
    const std::string id = require_string(args, "effect_id", "delete_effect");
    session.get(id);  // throws when unknown
    const bool removed = session.close(id);
    return {{"ok", removed}, {"effect_id", id}, {"remaining", session.ids().size()}};
}

nlohmann::json list_effects(Session& session, const nlohmann::json&) {
    std::string active_id;
    try {
        active_id = session.active().id;
    } catch (const Error&) {
        active_id.clear();
    }
    nlohmann::json effects = nlohmann::json::array();
    for (const std::string& id : session.ids()) {
        Document* doc = session.find(id);
        if (doc != nullptr) effects.push_back(effect_summary(session, *doc, active_id));
    }
    return {{"effects", std::move(effects)}, {"active", active_id.empty() ? nlohmann::json() : nlohmann::json(active_id)}};
}

nlohmann::json set_active_effect(Session& session, const nlohmann::json& args) {
    const std::string id = require_string(args, "effect_id", "set_active_effect");
    session.set_active(id);
    return {{"ok", true}, {"effect_id", id}, {"name", session.active().effect.name}};
}

nlohmann::json set_effect_property(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    Mutation mutation(session, doc);
    if (has_arg(args, "name")) doc.effect.name = require_string(args, "name", "set_effect_property");
    if (has_arg(args, "duration")) doc.effect.duration = require_number(args, "duration", "set_effect_property");
    if (has_arg(args, "time_scale"))
        doc.effect.time_scale = require_number(args, "time_scale", "set_effect_property");
    if (has_arg(args, "seed")) doc.effect.seed = static_cast<uint32_t>(arg_int(args, "seed", static_cast<int>(doc.effect.seed)));
    if (has_arg(args, "metadata")) {
        const nlohmann::json& metadata = arg(args, "metadata");
        if (!metadata.is_object()) throw Error("bad_argument", "set_effect_property: \"metadata\" must be an object");
        doc.effect.metadata = metadata;
    }
    mutation.commit();
    nlohmann::json result = ok_with(validate(doc.effect));
    result["effect"] = effect_to_json(doc.effect);
    return result;
}

nlohmann::json describe_vocabulary(Session&, const nlohmann::json& args) {
    nlohmann::json vocabulary = SpecRegistry::instance().to_json();
    if (has_arg(args, "node_type")) {
        const std::string type_name = require_string(args, "node_type", "describe_vocabulary");
        if (!vocabulary["node_types"].contains(type_name)) {
            std::string known;
            for (const auto& [name, unused] : vocabulary["node_types"].items()) {
                (void)unused;
                known += (known.empty() ? "" : ", ") + name;
            }
            throw Error("E003", "unknown node type \"" + type_name + "\"; the vocabulary has [" + known + "]");
        }
        nlohmann::json one = nlohmann::json::object();
        one[type_name] = vocabulary["node_types"][type_name];
        vocabulary["node_types"] = std::move(one);
    }
    vocabulary["texture_ops"] = procedural::texture_ops_json();
    return vocabulary;
}

nlohmann::json get_effect_json(Session& session, const nlohmann::json& args) {
    return effect_to_json(session.document_for(args).effect);
}

}  // namespace

void register_effect_tools(ToolRegistry& registry) {
    registry.add({"create_effect",
                  "Create a new effect document and make it the active one. Use `template` to start from one of the "
                  "bundled examples (\"fire_bolt\", \"fire_aoe\", \"lightning_strike\") or a path to an effect .json; "
                  "\"empty\" (the default) starts from nothing. Returns {effect_id, effect, diagnostics}.",
                  make_schema({{"name", prop("string", "Effect name, e.g. \"Fire AOE\" (default \"untitled\").")},
                               {"duration", prop("number", "Effect duration in seconds (default 2).")},
                               {"time_scale", prop("number", "Playback speed (default 1, range 0.1..8): a host maps "
                                                             "wall time to effect time as wall * time_scale, so the "
                                                             "effect lasts duration / time_scale seconds.")},
                               {"seed", prop("integer", "Master random seed (default 1).")},
                               {"template", prop("string", "\"empty\" (default), an example name, or a file path.")}},
                              {}),
                  true, "effect"},
                 create_effect);

    registry.add({"delete_effect",
                  "Close an effect document and drop its undo history and caches. Another loaded effect becomes "
                  "active. Returns {ok, effect_id, remaining}.",
                  make_schema({}, {"effect_id"}), true, "effect"},
                 delete_effect);

    registry.add({"list_effects",
                  "List the effects loaded in this session with their id, name, file path, unsaved-changes flag and "
                  "which one is active. Call this when you are unsure which effect the other tools will act on.",
                  make_schema({}), false, "effect"},
                 list_effects);

    registry.add({"set_active_effect",
                  "Make an already loaded effect the active one, so tools called without `effect_id` act on it. "
                  "Returns {ok, effect_id, name}.",
                  make_schema({}, {"effect_id"}), false, "effect"},
                 set_active_effect);

    registry.add({"set_effect_property",
                  "Change document level properties: name, duration (seconds), time_scale (playback speed), master "
                  "seed and free-form metadata "
                  "(record the source prompt or reference here). Returns {ok, effect, diagnostics}.",
                  make_schema({{"name", prop("string", "New effect name.")},
                               {"duration", prop("number", "New duration in seconds (> 0).")},
                               {"time_scale", prop("number", "New playback speed in [0.1, 8]. The simulation is "
                                                             "unchanged; the effect simply plays back over "
                                                             "duration / time_scale seconds of wall clock.")},
                               {"seed", prop("integer", "New master seed; changes every derived random stream.")},
                               {"metadata", prop("object", "Free-form metadata object, replaces the current one.")}}),
                  true, "effect"},
                 set_effect_property);

    registry.add({"describe_vocabulary",
                  "Return the authoring vocabulary: every node type with its parameters (type, default, range, enum "
                  "values, animatable, units), input ports and outputs, plus the value types, layer roles, validation "
                  "codes and the procedural texture ops. Call this first when you do not know the exact parameter "
                  "names; pass `node_type` to get one type only.",
                  make_schema({{"node_type", prop("string", "Limit the answer to one node type, e.g. \"emitter\".")}}),
                  false, "effect"},
                 describe_vocabulary);

    registry.add({"get_effect_json",
                  "Return the complete effect document as JSON, exactly as save_effect would write it. Use it to read "
                  "the authored state verbatim; inspect_graph is the friendlier summary.",
                  make_schema({}), false, "effect"},
                 get_effect_json);
}

}  // namespace aether::tools
