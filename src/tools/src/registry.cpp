// ToolRegistry: registration, argument validation against the input schema, and
// the JSON-RPC 2.0 boundary. See docs/AGENT_API.md.
#include "aether/tools/registry.hpp"

#include <algorithm>
#include <array>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

// docs/AGENT_API.md section order; unknown categories sort last, then by name.
int category_rank(const std::string& category) {
    static const std::array<const char*, 10> kOrder{"effect",  "graph",   "parameters", "timeline", "simulate",
                                                    "render",  "inspect", "evaluate",   "io",       "history"};
    for (size_t i = 0; i < kOrder.size(); ++i)
        if (category == kOrder[i]) return static_cast<int>(i);
    return static_cast<int>(kOrder.size());
}

bool json_matches_type(const nlohmann::json& value, const std::string& type) {
    if (type == "string") return value.is_string();
    if (type == "number") return value.is_number();
    if (type == "integer")
        return value.is_number_integer() ||
               (value.is_number_float() && value.get<double>() == std::floor(value.get<double>()));
    if (type == "boolean") return value.is_boolean();
    if (type == "object") return value.is_object();
    if (type == "array") return value.is_array();
    if (type == "null") return value.is_null();
    return true;  // unconstrained
}

std::vector<std::string> schema_types(const nlohmann::json& property) {
    std::vector<std::string> types;
    auto it = property.find("type");
    if (it == property.end()) return types;
    if (it->is_string()) {
        types.push_back(it->get<std::string>());
    } else if (it->is_array()) {
        for (const auto& t : *it)
            if (t.is_string()) types.push_back(t.get<std::string>());
    }
    return types;
}

std::string type_list(const std::vector<std::string>& types) {
    std::string out;
    for (const std::string& t : types) out += (out.empty() ? "" : " or ") + t;
    return out;
}

void validate_arguments(const ToolSpec& spec, const nlohmann::json& args) {
    const nlohmann::json& schema = spec.input_schema;
    if (!schema.is_object()) return;
    const nlohmann::json properties = schema.value("properties", nlohmann::json::object());

    auto required_it = schema.find("required");
    if (required_it != schema.end() && required_it->is_array()) {
        for (const auto& entry : *required_it) {
            if (!entry.is_string()) continue;
            const std::string key = entry.get<std::string>();
            if (has_arg(args, key.c_str())) continue;
            std::string description;
            if (properties.contains(key)) description = properties.at(key).value("description", std::string());
            throw Error("bad_argument", "tool \"" + spec.name + "\": missing required argument \"" + key + "\"" +
                                            (description.empty() ? "" : " (" + description + ")"));
        }
    }

    const bool additional = schema.value("additionalProperties", true);
    for (const auto& [key, value] : args.items()) {
        auto property = properties.find(key);
        if (property == properties.end()) {
            if (additional || key == "effect_id") continue;
            std::string known;
            for (const auto& [name, unused] : properties.items()) {
                (void)unused;
                known += (known.empty() ? "" : ", ") + name;
            }
            throw Error("bad_argument",
                        "tool \"" + spec.name + "\": unknown argument \"" + key + "\"; accepted arguments: " + known);
        }
        if (value.is_null()) continue;  // an explicitly null optional means "not given"
        const std::vector<std::string> types = schema_types(*property);
        if (types.empty()) continue;
        const bool ok = std::any_of(types.begin(), types.end(),
                                    [&](const std::string& t) { return json_matches_type(value, t); });
        if (!ok)
            throw Error("bad_argument", "tool \"" + spec.name + "\": argument \"" + key + "\" must be " +
                                            type_list(types) + ", got " + std::string(value.type_name()) + " " +
                                            value.dump());
    }
}

nlohmann::json error_response(const nlohmann::json& id, int code, const std::string& message,
                              nlohmann::json data = nlohmann::json()) {
    nlohmann::json error{{"code", code}, {"message", message}};
    if (!data.is_null()) error["data"] = std::move(data);
    return {{"jsonrpc", "2.0"}, {"id", id}, {"error", std::move(error)}};
}

}  // namespace

void ToolRegistry::add(ToolSpec spec, ToolHandler handler) {
    const std::string name = spec.name;
    tools_[name] = std::make_pair(std::move(spec), std::move(handler));
}

const ToolSpec* ToolRegistry::find(const std::string& name) const {
    auto it = tools_.find(name);
    return it == tools_.end() ? nullptr : &it->second.first;
}

std::vector<ToolSpec> ToolRegistry::list() const {
    std::vector<ToolSpec> out;
    out.reserve(tools_.size());
    for (const auto& [name, entry] : tools_) out.push_back(entry.first);
    std::stable_sort(out.begin(), out.end(), [](const ToolSpec& a, const ToolSpec& b) {
        const int ra = category_rank(a.category);
        const int rb = category_rank(b.category);
        if (ra != rb) return ra < rb;
        if (a.category != b.category) return a.category < b.category;
        return a.name < b.name;
    });
    return out;
}

nlohmann::json ToolRegistry::list_json() const {
    nlohmann::json tools = nlohmann::json::array();
    for (const ToolSpec& spec : list())
        tools.push_back({{"name", spec.name},
                         {"description", spec.description},
                         {"input_schema", spec.input_schema},
                         {"mutating", spec.mutating},
                         {"category", spec.category}});
    return {{"tools", std::move(tools)}};
}

nlohmann::json ToolRegistry::call(Session& session, const std::string& name, const nlohmann::json& args) const {
    auto it = tools_.find(name);
    if (it == tools_.end()) throw Error("unknown_tool", "unknown tool \"" + name + "\"; call tools/list for the full set");
    const nlohmann::json arguments = args.is_object() ? args : nlohmann::json::object();
    validate_arguments(it->second.first, arguments);
    nlohmann::json result = it->second.second(session, arguments);
    if (!result.is_object()) result = nlohmann::json{{"result", std::move(result)}};
    return result;
}

const ToolRegistry& ToolRegistry::standard() {
    static const ToolRegistry registry = [] {
        ToolRegistry r;
        register_effect_tools(r);
        register_graph_tools(r);
        register_parameter_tools(r);
        register_timeline_tools(r);
        register_simulate_tools(r);
        register_render_tools(r);
        register_inspect_tools(r);
        register_evaluate_tools(r);
        register_io_tools(r);
        register_history_tools(r);
        return r;
    }();
    return registry;
}

nlohmann::json jsonrpc_error_object(const std::exception& e) {
    if (const auto* tool_error = dynamic_cast<const ToolError*>(&e)) {
        nlohmann::json data{{"aether_code", tool_error->code()}};
        if (tool_error->node()) data["node"] = *tool_error->node();
        if (tool_error->param()) data["param"] = *tool_error->param();
        return {{"code", -32602}, {"message", e.what()}, {"data", std::move(data)}};
    }
    if (const auto* error = dynamic_cast<const Error*>(&e))
        return {{"code", -32602}, {"message", e.what()}, {"data", {{"aether_code", error->code()}}}};
    return {{"code", -32603}, {"message", std::string("internal error: ") + e.what()}};
}

nlohmann::json handle_jsonrpc(Session& session, const ToolRegistry& registry, const nlohmann::json& request) {
    if (!request.is_object())
        return error_response(nlohmann::json(), -32700, "parse error: a JSON-RPC request must be a JSON object");

    const nlohmann::json id = request.contains("id") ? request.at("id") : nlohmann::json();
    if (!request.contains("method") || !request.at("method").is_string())
        return error_response(id, -32600, "invalid request: \"method\" must be a string");
    const std::string method = request.at("method").get<std::string>();

    nlohmann::json params = nlohmann::json::object();
    if (request.contains("params") && request.at("params").is_object()) params = request.at("params");

    try {
        if (method == "ping") return {{"jsonrpc", "2.0"}, {"id", id}, {"result", {{"ok", true}}}};
        if (method == "shutdown") return {{"jsonrpc", "2.0"}, {"id", id}, {"result", {{"ok", true}}}};
        if (method == "tools/list") return {{"jsonrpc", "2.0"}, {"id", id}, {"result", registry.list_json()}};
        if (registry.find(method) == nullptr)
            return error_response(id, -32601, "unknown method: \"" + method + "\"; call tools/list for the tool set");
        return {{"jsonrpc", "2.0"}, {"id", id}, {"result", registry.call(session, method, params)}};
    } catch (const std::exception& e) {
        nlohmann::json error = jsonrpc_error_object(e);
        return {{"jsonrpc", "2.0"}, {"id", id}, {"error", std::move(error)}};
    }
}

nlohmann::json handle_jsonrpc_line(Session& session, const ToolRegistry& registry, const std::string& line) {
    nlohmann::json request;
    try {
        request = nlohmann::json::parse(line);
    } catch (const std::exception& e) {
        return error_response(nlohmann::json(), -32700, std::string("parse error: ") + e.what());
    }
    return handle_jsonrpc(session, registry, request);
}

}  // namespace aether::tools
