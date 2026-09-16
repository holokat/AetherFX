#pragma once
// The Agent Tool API. Every operation an agent can perform is registered here
// exactly once; CLI, JSON-RPC, Python and MCP all go through call().
#include <functional>
#include <map>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/tools/session.hpp"

namespace aether::tools {

struct ToolSpec {
    std::string name;
    std::string description;      // written for an LLM reader: what, when, what it returns
    nlohmann::json input_schema;  // JSON Schema object for the arguments
    bool mutating = false;        // records an undo step
    std::string category;         // "effect", "graph", "parameters", "timeline", "simulate", "render", "inspect", "evaluate", "io", "history"
};

using ToolHandler = std::function<nlohmann::json(Session&, const nlohmann::json& args)>;

class ToolRegistry {
public:
    void add(ToolSpec spec, ToolHandler handler);
    const ToolSpec* find(const std::string& name) const;
    std::vector<ToolSpec> list() const;  // sorted by category then name
    nlohmann::json list_json() const;
    // Validates args against input_schema (required keys + primitive types),
    // runs the handler. Throws aether::Error with a code on failure.
    nlohmann::json call(Session& session, const std::string& name, const nlohmann::json& args) const;

    // The full standard tool set (docs/AGENT_API.md). Built once.
    static const ToolRegistry& standard();

private:
    std::map<std::string, std::pair<ToolSpec, ToolHandler>> tools_;
};

// JSON-RPC 2.0 request -> response. Method = tool name; params = args object.
// Also serves "tools/list" and "ping". Never throws; errors become JSON-RPC errors
// {code, message, data: {aether_code}}.
nlohmann::json handle_jsonrpc(Session& session, const ToolRegistry& registry, const nlohmann::json& request);

}  // namespace aether::tools
