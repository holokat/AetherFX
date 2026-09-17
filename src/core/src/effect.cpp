// Graph structure: node/layer/timeline lookups, mutation and transform
// composition. See docs/ARCHITECTURE.md section 3.
#include "aether/core/effect.hpp"

#include <algorithm>

#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"

namespace aether {

NodeRef NodeRef::parse(std::string_view s) {
    NodeRef r;
    size_t dot = s.find('.');
    if (dot == std::string_view::npos) {
        r.node = std::string(s);
    } else {
        r.node = std::string(s.substr(0, dot));
        r.port = std::string(s.substr(dot + 1));
    }
    return r;
}

const Parameter* Node::find_param(std::string_view name) const {
    auto it = parameters.find(std::string(name));
    return it == parameters.end() ? nullptr : &it->second;
}
Parameter* Node::find_param(std::string_view name) {
    auto it = parameters.find(std::string(name));
    return it == parameters.end() ? nullptr : &it->second;
}

std::optional<NodeRef> Node::input(std::string_view port) const {
    const std::vector<NodeRef>* refs = inputs_on(port);
    if (!refs || refs->empty()) return std::nullopt;
    return refs->front();
}

const std::vector<NodeRef>* Node::inputs_on(std::string_view port) const {
    auto it = inputs.find(std::string(port));
    return it == inputs.end() ? nullptr : &it->second;
}

const TimelinePhase* Timeline::find(std::string_view name) const {
    for (const auto& p : phases)
        if (p.name == name) return &p;
    return nullptr;
}
TimelinePhase* Timeline::find(std::string_view name) {
    for (auto& p : phases)
        if (p.name == name) return &p;
    return nullptr;
}

const Node* Effect::find_node(std::string_view id) const {
    for (const auto& n : nodes)
        if (n.id == id) return &n;
    return nullptr;
}
Node* Effect::find_node(std::string_view id) {
    for (auto& n : nodes)
        if (n.id == id) return &n;
    return nullptr;
}
const Layer* Effect::find_layer(std::string_view id) const {
    for (const auto& l : layers)
        if (l.id == id) return &l;
    return nullptr;
}
Layer* Effect::find_layer(std::string_view id) {
    for (auto& l : layers)
        if (l.id == id) return &l;
    return nullptr;
}

std::vector<const Node*> Effect::nodes_in_layer(std::string_view layer_id) const {
    std::vector<const Node*> out;
    for (const auto& n : nodes)
        if (n.layer && *n.layer == layer_id) out.push_back(&n);
    return out;
}

std::vector<const Node*> Effect::nodes_of_type(NodeType t) const {
    std::vector<const Node*> out;
    for (const auto& n : nodes)
        if (n.type == t) out.push_back(&n);
    return out;
}

std::vector<const Node*> Effect::consumers_of(std::string_view id) const {
    std::vector<const Node*> out;
    for (const auto& n : nodes) {
        bool uses = n.parent && *n.parent == id;
        if (!uses) {
            for (const auto& [port, refs] : n.inputs) {
                (void)port;
                for (const auto& r : refs)
                    if (r.node == id) { uses = true; break; }
                if (uses) break;
            }
        }
        if (uses) out.push_back(&n);
    }
    return out;
}

Node& Effect::add_node(Node n) {
    if (find_node(n.id) != nullptr)
        throw Error("E001", "duplicate node id \"" + n.id + "\"");
    nodes.push_back(std::move(n));
    return nodes.back();
}

bool Effect::remove_node(std::string_view id) {
    auto it = std::find_if(nodes.begin(), nodes.end(), [&](const Node& n) { return n.id == id; });
    if (it == nodes.end()) return false;
    nodes.erase(it);
    for (auto& n : nodes) {
        if (n.parent && *n.parent == id) n.parent.reset();
        for (auto port = n.inputs.begin(); port != n.inputs.end();) {
            auto& refs = port->second;
            refs.erase(std::remove_if(refs.begin(), refs.end(), [&](const NodeRef& r) { return r.node == id; }),
                       refs.end());
            if (refs.empty()) port = n.inputs.erase(port);
            else ++port;
        }
    }
    return true;
}

const Control* Effect::find_control(std::string_view id) const {
    for (const auto& c : controls)
        if (c.id == id) return &c;
    return nullptr;
}
Control* Effect::find_control(std::string_view id) {
    for (auto& c : controls)
        if (c.id == id) return &c;
    return nullptr;
}

std::string Effect::unique_control_id(std::string_view base) const {
    std::string candidate(base);
    if (candidate.empty()) candidate = "control";
    if (find_control(candidate) == nullptr) return candidate;
    for (int i = 2;; ++i) {
        std::string next = candidate + "_" + std::to_string(i);
        if (find_control(next) == nullptr) return next;
    }
}

NodeId Effect::unique_id(std::string_view base) const {
    std::string candidate(base);
    if (candidate.empty()) candidate = "node";
    if (!has_node(candidate)) return candidate;
    for (int i = 2;; ++i) {
        std::string next = std::string(base) + "_" + std::to_string(i);
        if (!has_node(next)) return next;
    }
}

Mat4 Effect::world_transform(const Node& node, double time) const {
    // Root-most first, guarding against parent cycles.
    std::vector<const Node*> chain;
    std::vector<NodeId> visited;
    const Node* cur = &node;
    while (cur != nullptr) {
        if (std::find(visited.begin(), visited.end(), cur->id) != visited.end()) break;
        visited.push_back(cur->id);
        chain.push_back(cur);
        if (chain.size() > nodes.size()) break;
        cur = cur->parent ? find_node(*cur->parent) : nullptr;
    }
    const SpecRegistry& registry = SpecRegistry::instance();
    Mat4 m = Mat4::identity();
    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
        const Node& n = **it;
        if (!registry.get(n.type).spatial) continue;  // non-spatial parents contribute identity
        m = m * Mat4::trs(param_vec3(n, "position", time), param_vec3(n, "rotation", time),
                          param_vec3(n, "scale", time));
    }
    return m;
}

}  // namespace aether
