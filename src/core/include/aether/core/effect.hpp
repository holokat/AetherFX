#pragma once
// The VFX graph. See docs/ARCHITECTURE.md section 3 and docs/VOCABULARY.md.
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/enums.hpp"
#include "aether/core/value.hpp"

namespace aether {

using NodeId = std::string;

// "node_id" or "node_id.port".
struct NodeRef {
    NodeId node;
    std::string port;  // empty = default output
    static NodeRef parse(std::string_view s);
    std::string str() const { return port.empty() ? node : node + "." + port; }
    bool operator==(const NodeRef&) const = default;
};

struct Node {
    NodeId id;
    NodeType type = NodeType::Emitter;
    int version = 1;
    bool enabled = true;
    std::optional<uint32_t> seed;
    std::optional<NodeId> parent;
    std::optional<std::string> layer;
    std::map<std::string, Parameter> parameters;            // only explicitly set parameters
    std::map<std::string, std::vector<NodeRef>> inputs;     // port -> refs (single ports hold 1)
    nlohmann::json metadata = nlohmann::json::object();

    bool has_param(std::string_view name) const { return parameters.find(std::string(name)) != parameters.end(); }
    const Parameter* find_param(std::string_view name) const;
    Parameter* find_param(std::string_view name);
    // First ref on a port, if any.
    std::optional<NodeRef> input(std::string_view port) const;
    const std::vector<NodeRef>* inputs_on(std::string_view port) const;
};

struct Layer {
    std::string id;
    std::string name;
    LayerRole role = LayerRole::Custom;
    bool enabled = true;
    nlohmann::json metadata = nlohmann::json::object();
};

struct TimelinePhase {
    std::string name;  // anticipation|activation|peak|sustain|decay|custom name
    double start = 0.0;
    double end = 0.0;
};
struct Timeline {
    std::vector<TimelinePhase> phases;
    const TimelinePhase* find(std::string_view name) const;
    TimelinePhase* find(std::string_view name);
};

struct Effect {
    std::string schema_version = "0.1.0";
    std::string name = "untitled";
    double duration = 2.0;
    uint32_t seed = 1;
    Timeline timeline;
    std::vector<Layer> layers;
    std::vector<Node> nodes;  // order is authoring order; compiler computes execution order
    nlohmann::json metadata = nlohmann::json::object();

    const Node* find_node(std::string_view id) const;
    Node* find_node(std::string_view id);
    const Layer* find_layer(std::string_view id) const;
    Layer* find_layer(std::string_view id);
    bool has_node(std::string_view id) const { return find_node(id) != nullptr; }
    std::vector<const Node*> nodes_in_layer(std::string_view layer_id) const;
    std::vector<const Node*> nodes_of_type(NodeType t) const;
    // All nodes that reference `id` on any input port or as parent.
    std::vector<const Node*> consumers_of(std::string_view id) const;
    // Adds and returns the node; throws Error("E001") on duplicate id.
    Node& add_node(Node n);
    // Removes the node and every reference to it (inputs, parent). Returns false if absent.
    bool remove_node(std::string_view id);
    // Generates "<base>", "<base>_2", "<base>_3"... that is not in use.
    NodeId unique_id(std::string_view base) const;
    // World transform of a spatial node at `time`, composing `parent` chains
    // (position/rotation/scale parameters). Identity for non-spatial nodes.
    Mat4 world_transform(const Node& node, double time) const;
};

}  // namespace aether
