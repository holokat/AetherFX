// Effect document IO: structural JSON conversion, migration, canonical text
// and content hashing. Semantic problems are left to validate().
#include "aether/core/serialization.hpp"

#include <array>
#include <fstream>
#include <stdexcept>

#include "aether/core/controls.hpp"
#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"

namespace aether {
namespace {

// Ordered chain of document migrations. Adding a version means appending one
// entry: {from, to, fn}; migrate_effect_json walks the chain until it reaches
// kSchemaVersion.
struct Migration {
    const char* from;
    const char* to;
    void (*apply)(nlohmann::json&);
};

const std::vector<Migration>& migrations() {
    static const std::vector<Migration> chain{};  // 0.1.0 is the first schema
    return chain;
}

std::array<int, 3> parse_semver(const std::string& s) {
    std::array<int, 3> out{0, 0, 0};
    size_t begin = 0;
    for (int i = 0; i < 3 && begin <= s.size(); ++i) {
        size_t dot = s.find('.', begin);
        std::string part = s.substr(begin, dot == std::string::npos ? std::string::npos : dot - begin);
        try {
            out[static_cast<size_t>(i)] = part.empty() ? 0 : std::stoi(part);
        } catch (const std::exception&) {
            out[static_cast<size_t>(i)] = 0;
        }
        if (dot == std::string::npos) break;
        begin = dot + 1;
    }
    return out;
}

int compare_semver(const std::string& a, const std::string& b) {
    std::array<int, 3> va = parse_semver(a), vb = parse_semver(b);
    for (size_t i = 0; i < 3; ++i) {
        if (va[i] < vb[i]) return -1;
        if (va[i] > vb[i]) return 1;
    }
    return 0;
}

std::vector<NodeRef> read_refs(const nlohmann::json& j, const std::string& node_id, const std::string& port) {
    std::vector<NodeRef> refs;
    if (j.is_string()) {
        refs.push_back(NodeRef::parse(j.get<std::string>()));
    } else if (j.is_array()) {
        for (const auto& e : j) {
            if (!e.is_string())
                throw Error("E005", "node \"" + node_id + "\" input \"" + port + "\" must contain node id strings");
            refs.push_back(NodeRef::parse(e.get<std::string>()));
        }
    } else {
        throw Error("E005", "node \"" + node_id + "\" input \"" + port + "\" must be a node id or a list of node ids");
    }
    return refs;
}

Node node_from_json(const nlohmann::json& j) {
    if (!j.is_object()) throw Error("E005", "a node must be a JSON object");
    Node n;
    auto id_it = j.find("id");
    if (id_it == j.end() || !id_it->is_string()) throw Error("E005", "a node needs a string \"id\"");
    n.id = id_it->get<std::string>();

    auto type_it = j.find("type");
    if (type_it == j.end() || !type_it->is_string())
        throw Error("E003", "node \"" + n.id + "\" needs a string \"type\"");
    if (!parse_node_type(type_it->get<std::string>(), n.type))
        throw Error("E003", "unknown node type \"" + type_it->get<std::string>() + "\" on node \"" + n.id + "\"");
    const NodeSpec& spec = SpecRegistry::instance().get(n.type);

    if (j.contains("version")) {
        if (!j.at("version").is_number_integer()) throw Error("E005", "node \"" + n.id + "\": \"version\" must be an integer");
        n.version = j.at("version").get<int>();
    }
    if (j.contains("enabled")) {
        if (!j.at("enabled").is_boolean()) throw Error("E005", "node \"" + n.id + "\": \"enabled\" must be a bool");
        n.enabled = j.at("enabled").get<bool>();
    }
    if (j.contains("seed") && !j.at("seed").is_null()) {
        if (!j.at("seed").is_number_integer()) throw Error("E005", "node \"" + n.id + "\": \"seed\" must be an integer");
        n.seed = j.at("seed").get<uint32_t>();
    }
    if (j.contains("parent") && !j.at("parent").is_null()) {
        if (!j.at("parent").is_string()) throw Error("E005", "node \"" + n.id + "\": \"parent\" must be a node id");
        n.parent = NodeRef::parse(j.at("parent").get<std::string>()).node;
    }
    if (j.contains("layer") && !j.at("layer").is_null()) {
        if (!j.at("layer").is_string()) throw Error("E005", "node \"" + n.id + "\": \"layer\" must be a layer id");
        n.layer = j.at("layer").get<std::string>();
    }
    if (j.contains("metadata") && !j.at("metadata").is_null()) n.metadata = j.at("metadata");

    auto params_it = j.find("parameters");
    if (params_it != j.end() && !params_it->is_null()) {
        if (!params_it->is_object()) throw Error("E005", "node \"" + n.id + "\": \"parameters\" must be an object");
        for (const auto& [name, value] : params_it->items()) {
            const ParamSpec* ps = spec.find_param(name);
            // Unknown parameters and values that do not fit their declared type are
            // kept verbatim as json so validate() can report E004 / E005 instead of
            // the document failing to load at all.
            if (ps == nullptr) {
                n.parameters[name] = Parameter{value};
                continue;
            }
            try {
                n.parameters[name] = parameter_from_json(value, ps->type);
            } catch (const Error&) {
                n.parameters[name] = Parameter{value};
            }
        }
    }

    auto inputs_it = j.find("inputs");
    if (inputs_it != j.end() && !inputs_it->is_null()) {
        if (!inputs_it->is_object()) throw Error("E005", "node \"" + n.id + "\": \"inputs\" must be an object");
        for (const auto& [port, value] : inputs_it->items()) {
            std::vector<NodeRef> refs = read_refs(value, n.id, port);
            if (!refs.empty()) n.inputs[port] = std::move(refs);
        }
    }
    return n;
}

nlohmann::json node_to_json(const Node& n) {
    nlohmann::json j{{"id", n.id}, {"type", to_string(n.type)}};
    if (n.version != 1) j["version"] = n.version;
    if (!n.enabled) j["enabled"] = false;
    if (n.seed) j["seed"] = *n.seed;
    if (n.parent) j["parent"] = *n.parent;
    if (n.layer) j["layer"] = *n.layer;
    if (!n.parameters.empty()) {
        nlohmann::json params = nlohmann::json::object();
        for (const auto& [name, p] : n.parameters) params[name] = parameter_to_json(p);
        j["parameters"] = std::move(params);
    }
    if (!n.inputs.empty()) {
        nlohmann::json inputs = nlohmann::json::object();
        for (const auto& [port, refs] : n.inputs) {
            if (refs.empty()) continue;
            if (refs.size() == 1) {
                inputs[port] = refs.front().str();
            } else {
                nlohmann::json arr = nlohmann::json::array();
                for (const auto& r : refs) arr.push_back(r.str());
                inputs[port] = std::move(arr);
            }
        }
        if (!inputs.empty()) j["inputs"] = std::move(inputs);
    }
    if (!n.metadata.is_null() && !(n.metadata.is_object() && n.metadata.empty())) j["metadata"] = n.metadata;
    return j;
}

}  // namespace

Effect effect_from_json(const nlohmann::json& j) {
    if (!j.is_object()) throw Error("E005", "an effect document must be a JSON object");
    Effect e;
    if (j.contains("schema_version")) {
        if (!j.at("schema_version").is_string()) throw Error("E005", "\"schema_version\" must be a string");
        e.schema_version = j.at("schema_version").get<std::string>();
    } else {
        e.schema_version = kSchemaVersion;
    }
    if (j.contains("name")) {
        if (!j.at("name").is_string()) throw Error("E005", "\"name\" must be a string");
        e.name = j.at("name").get<std::string>();
    }
    if (j.contains("description") && !j.at("description").is_null()) {
        if (!j.at("description").is_string()) throw Error("E005", "\"description\" must be a string");
        e.description = j.at("description").get<std::string>();
    }
    if (j.contains("duration")) {
        if (!j.at("duration").is_number()) throw Error("E005", "\"duration\" must be a number");
        e.duration = j.at("duration").get<double>();
    }
    if (j.contains("time_scale") && !j.at("time_scale").is_null()) {
        if (!j.at("time_scale").is_number()) throw Error("E005", "\"time_scale\" must be a number");
        // Out of range is E015 from validate(), not a load failure: a document
        // with a silly speed still opens so it can be seen and fixed.
        e.time_scale = j.at("time_scale").get<double>();
    }
    if (j.contains("seed")) {
        if (!j.at("seed").is_number_integer()) throw Error("E005", "\"seed\" must be an integer");
        e.seed = j.at("seed").get<uint32_t>();
    }

    auto timeline_it = j.find("timeline");
    if (timeline_it != j.end() && !timeline_it->is_null()) {
        if (!timeline_it->is_object()) throw Error("E005", "\"timeline\" must be an object");
        auto phases_it = timeline_it->find("phases");
        if (phases_it != timeline_it->end() && !phases_it->is_null()) {
            if (!phases_it->is_array()) throw Error("E005", "\"timeline.phases\" must be an array");
            for (const auto& p : *phases_it) {
                if (!p.is_object() || !p.contains("name") || !p.at("name").is_string())
                    throw Error("E005", "a timeline phase needs a string \"name\"");
                TimelinePhase phase;
                phase.name = p.at("name").get<std::string>();
                if (p.contains("start")) {
                    if (!p.at("start").is_number()) throw Error("E005", "phase \"start\" must be a number");
                    phase.start = p.at("start").get<double>();
                }
                if (p.contains("end")) {
                    if (!p.at("end").is_number()) throw Error("E005", "phase \"end\" must be a number");
                    phase.end = p.at("end").get<double>();
                }
                e.timeline.phases.push_back(std::move(phase));
            }
        }
    }

    auto layers_it = j.find("layers");
    if (layers_it != j.end() && !layers_it->is_null()) {
        if (!layers_it->is_array()) throw Error("E005", "\"layers\" must be an array");
        for (const auto& l : *layers_it) {
            if (!l.is_object() || !l.contains("id") || !l.at("id").is_string())
                throw Error("E005", "a layer needs a string \"id\"");
            Layer layer;
            layer.id = l.at("id").get<std::string>();
            layer.name = l.contains("name") && l.at("name").is_string() ? l.at("name").get<std::string>() : layer.id;
            if (l.contains("role") && l.at("role").is_string())
                parse_layer_role(l.at("role").get<std::string>(), layer.role);  // unknown -> custom
            if (l.contains("enabled") && l.at("enabled").is_boolean()) layer.enabled = l.at("enabled").get<bool>();
            if (l.contains("metadata") && !l.at("metadata").is_null()) layer.metadata = l.at("metadata");
            e.layers.push_back(std::move(layer));
        }
    }

    auto nodes_it = j.find("nodes");
    if (nodes_it != j.end() && !nodes_it->is_null()) {
        if (!nodes_it->is_array()) throw Error("E005", "\"nodes\" must be an array");
        // Duplicates are kept so validate() can report E001 against the document.
        for (const auto& n : *nodes_it) e.nodes.push_back(node_from_json(n));
    }

    auto controls_it = j.find("controls");
    if (controls_it != j.end() && !controls_it->is_null()) {
        if (!controls_it->is_array()) throw Error("E005", "\"controls\" must be an array");
        // Duplicates and dangling bindings are kept so validate() can report
        // E021/E022 against the document instead of the file failing to load.
        for (const auto& c : *controls_it) e.controls.push_back(control_from_json(c));
    }

    if (j.contains("metadata") && !j.at("metadata").is_null()) e.metadata = j.at("metadata");
    return e;
}

nlohmann::json effect_to_json(const Effect& e) {
    nlohmann::json phases = nlohmann::json::array();
    for (const auto& p : e.timeline.phases) phases.push_back({{"name", p.name}, {"start", p.start}, {"end", p.end}});

    nlohmann::json layers = nlohmann::json::array();
    for (const auto& l : e.layers) {
        nlohmann::json lj{{"id", l.id}, {"name", l.name}, {"role", to_string(l.role)}};
        if (!l.enabled) lj["enabled"] = false;
        if (!l.metadata.is_null() && !(l.metadata.is_object() && l.metadata.empty())) lj["metadata"] = l.metadata;
        layers.push_back(std::move(lj));
    }

    nlohmann::json nodes = nlohmann::json::array();
    for (const auto& n : e.nodes) nodes.push_back(node_to_json(n));

    nlohmann::json j{{"schema_version", e.schema_version},
                     {"name", e.name},
                     {"duration", e.duration},
                     {"seed", e.seed},
                     {"timeline", {{"phases", std::move(phases)}}},
                     {"layers", std::move(layers)},
                     {"nodes", std::move(nodes)}};
    // Optional members are only written when they carry something, so a
    // document without controls stays byte-for-byte what it was.
    if (!e.description.empty()) j["description"] = e.description;
    // 1.0 is "plays at its authored speed", so the key stays out of every
    // document that never asked for anything else and effect_hash is unmoved.
    if (e.time_scale != 1.0) j["time_scale"] = e.time_scale;
    if (!e.controls.empty()) {
        nlohmann::json controls = nlohmann::json::array();
        for (const Control& c : e.controls) controls.push_back(control_to_json(c));
        j["controls"] = std::move(controls);
    }
    if (!e.metadata.is_null() && !(e.metadata.is_object() && e.metadata.empty())) j["metadata"] = e.metadata;
    return j;
}

std::vector<std::string> migrate_effect_json(nlohmann::json& j) {
    if (!j.is_object()) throw Error("E005", "an effect document must be a JSON object");
    std::vector<std::string> applied;
    if (!j.contains("schema_version") || !j.at("schema_version").is_string()) {
        j["schema_version"] = kSchemaVersion;
        applied.push_back("added schema_version");
    }
    std::string version = j.at("schema_version").get<std::string>();
    while (version != kSchemaVersion) {
        if (compare_semver(version, kSchemaVersion) > 0)
            throw Error("E020", "schema version \"" + version + "\" is newer than the supported \"" +
                                    std::string(kSchemaVersion) + "\"");
        const Migration* step = nullptr;
        for (const auto& m : migrations())
            if (version == m.from) { step = &m; break; }
        if (step == nullptr)
            throw Error("E020", "no migration path from schema version \"" + version + "\" to \"" +
                                    std::string(kSchemaVersion) + "\"");
        step->apply(j);
        version = step->to;
        j["schema_version"] = version;
        applied.push_back(std::string("migrated ") + step->from + " -> " + step->to);
    }
    return applied;
}

Effect load_effect_file(const std::filesystem::path& path) {
    std::ifstream in(path);
    if (!in) throw Error("io_error", "cannot open effect file \"" + path.string() + "\"");
    nlohmann::json j;
    try {
        in >> j;
    } catch (const nlohmann::json::exception& ex) {
        throw Error("bad_json", "cannot parse \"" + path.string() + "\": " + ex.what());
    }
    migrate_effect_json(j);
    return effect_from_json(j);
}

void save_effect_file(const Effect& e, const std::filesystem::path& path, int indent) {
    if (path.has_parent_path() && !path.parent_path().empty())
        std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path);
    if (!out) throw Error("io_error", "cannot write effect file \"" + path.string() + "\"");
    out << effect_to_json(e).dump(indent) << "\n";
    if (!out) throw Error("io_error", "failed while writing \"" + path.string() + "\"");
}

std::string effect_to_canonical_string(const Effect& e) { return effect_to_json(e).dump(); }

uint64_t effect_hash(const Effect& e) { return fnv1a_64(effect_to_canonical_string(e)); }

}  // namespace aether
