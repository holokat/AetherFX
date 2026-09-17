// Semantic validation against the SpecRegistry plus graph ordering.
// Every finding is a Diagnostic; codes are documented in docs/VOCABULARY.md.
#include "aether/core/validation.hpp"

#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <string>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"

namespace aether {
namespace {

constexpr int kParticleBudget = 250000;

bool valid_node_id(const std::string& id) {
    if (id.empty()) return false;
    if (id[0] < 'a' || id[0] > 'z') return false;
    for (char c : id) {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        if (!ok) return false;
    }
    return true;
}

// Node types a `parent` reference may point at (docs/VOCABULARY.md, common fields).
bool valid_parent_type(NodeType t) {
    switch (t) {
        case NodeType::Emitter:
        case NodeType::Mesh:
        case NodeType::Light:
        case NodeType::Decal:
        case NodeType::Curve:
        case NodeType::Camera:
        case NodeType::Force:
        case NodeType::Collider:
        case NodeType::Volume:
        case NodeType::Field:
        case NodeType::Beam:
            return true;
        default:
            return false;
    }
}

int node_int(const Node& n, const char* name, int fallback) {
    try { return param_int(n, name); } catch (const Error&) { return fallback; }
}
bool node_bool(const Node& n, const char* name, bool fallback) {
    try { return param_bool(n, name); } catch (const Error&) { return fallback; }
}
std::string node_enum(const Node& n, const char* name, const char* fallback) {
    try { return param_string(n, name); } catch (const Error&) { return std::string(fallback); }
}
// The literal string a parameter holds, if it holds one (never throws).
std::string raw_string(const Node& n, const char* name) {
    const Parameter* p = n.find_param(name);
    if (p == nullptr) return {};
    if (const std::string* s = std::get_if<std::string>(&p->value)) return *s;
    return {};
}

int component_count(ValueType t) {
    switch (t) {
        case ValueType::Int:
        case ValueType::Float: return 1;
        case ValueType::Vec2: return 2;
        case ValueType::Vec3: return 3;
        case ValueType::Vec4:
        case ValueType::Color: return 4;
        default: return 0;
    }
}

double component(const Value& v, int i) {
    switch (value_type_of(v)) {
        case ValueType::Int: return static_cast<double>(std::get<int>(v));
        case ValueType::Float: return static_cast<double>(std::get<float>(v));
        case ValueType::Vec2: return static_cast<double>(std::get<Vec2>(v)[i]);
        case ValueType::Vec3: return static_cast<double>(std::get<Vec3>(v)[i]);
        case ValueType::Vec4: return static_cast<double>(std::get<Vec4>(v)[i]);
        case ValueType::Color: return static_cast<double>(std::get<Color>(v)[i]);
        default: return 0.0;
    }
}

const char* component_name(ValueType t, int i) {
    static const char* xyzw[] = {"x", "y", "z", "w"};
    static const char* rgba[] = {"r", "g", "b", "a"};
    if (t == ValueType::Color) return rgba[i];
    return xyzw[i];
}

// E005 / E006 / E007 / E019 for one concrete value (constant or keyframe).
void check_value(const Node& node, const ParamSpec& ps, const Value& v, const std::string& context, Diagnostics& d) {
    if (!value_compatible(v, ps.type)) {
        d.error("E005",
                "parameter \"" + ps.name + "\"" + context + " expects " + std::string(to_string(ps.type)) +
                    " but got " + std::string(to_string(value_type_of(v))),
                node.id, ps.name);
        return;
    }
    if (ps.type == ValueType::Enum) {
        const std::string& s = std::get<std::string>(v);
        if (std::find(ps.enum_values.begin(), ps.enum_values.end(), s) == ps.enum_values.end()) {
            std::string allowed;
            for (const auto& e : ps.enum_values) allowed += (allowed.empty() ? "" : ", ") + e;
            d.error("E007", "parameter \"" + ps.name + "\"" + context + " has invalid value \"" + s +
                                "\"; expected one of [" + allowed + "]",
                    node.id, ps.name);
        }
    }
    if (ps.min || ps.max) {
        const ValueType vt = value_type_of(v);
        const int count = component_count(vt);
        for (int i = 0; i < count; ++i) {
            double c = component(v, i);
            const bool below = ps.min && c < *ps.min;
            const bool above = ps.max && c > *ps.max;
            if (!below && !above) continue;
            std::string where = count > 1 ? std::string(".") + component_name(vt, i) : std::string();
            std::string range = std::string("[") + (ps.min ? std::to_string(*ps.min) : std::string("-inf")) + ", " +
                                (ps.max ? std::to_string(*ps.max) : std::string("inf")) + "]";
            d.error("E006", "parameter \"" + ps.name + where + "\"" + context + " = " + std::to_string(c) +
                                " is outside " + range,
                    node.id, ps.name);
        }
    }
    if (ps.type == ValueType::Curve && std::holds_alternative<Curve>(v)) {
        const Curve& c = std::get<Curve>(v);
        float prev = -std::numeric_limits<float>::infinity();
        for (size_t i = 0; i < c.keys.size(); ++i) {
            if (c.keys[i].t < 0.0f || c.keys[i].t > 1.0f)
                d.error("E019", "curve \"" + ps.name + "\"" + context + " key " + std::to_string(i) + " has t=" +
                                    std::to_string(c.keys[i].t) + " outside [0,1]",
                        node.id, ps.name);
            if (c.keys[i].t < prev)
                d.error("E019", "curve \"" + ps.name + "\"" + context + " keys are not sorted by t", node.id, ps.name);
            prev = c.keys[i].t;
        }
    }
    if (ps.type == ValueType::Gradient && std::holds_alternative<Gradient>(v)) {
        const Gradient& g = std::get<Gradient>(v);
        float prev = -std::numeric_limits<float>::infinity();
        for (size_t i = 0; i < g.keys.size(); ++i) {
            if (g.keys[i].t < 0.0f || g.keys[i].t > 1.0f)
                d.error("E019", "gradient \"" + ps.name + "\"" + context + " key " + std::to_string(i) + " has t=" +
                                    std::to_string(g.keys[i].t) + " outside [0,1]",
                        node.id, ps.name);
            if (g.keys[i].t < prev)
                d.error("E019", "gradient \"" + ps.name + "\"" + context + " keys are not sorted by t", node.id, ps.name);
            prev = g.keys[i].t;
        }
    }
}

// Is this emitter wired into a volume's `sources` port? Then `particle` is optional.
bool feeds_a_volume(const Effect& effect, const std::string& emitter_id) {
    for (const Node& n : effect.nodes) {
        if (n.type != NodeType::Volume) continue;
        const std::vector<NodeRef>* refs = n.inputs_on("sources");
        if (refs == nullptr) continue;
        for (const NodeRef& r : *refs)
            if (r.node == emitter_id) return true;
    }
    return false;
}

bool needs_event_source(const std::string& trigger) {
    return trigger == "on_spawn" || trigger == "on_death" || trigger == "on_collision" || trigger == "on_distance";
}

// W002 candidates: pure data/provider nodes and invisible meshes.
bool is_unused_candidate(const Node& n) {
    switch (n.type) {
        case NodeType::Material:
        case NodeType::Texture:
        case NodeType::Noise:
        case NodeType::Force:
        case NodeType::Collider:
        case NodeType::Curve:
        case NodeType::ParticleSystem:
            return true;
        case NodeType::Mesh:
            return !node_bool(n, "visible", true);
        default:
            return false;
    }
}

void check_node(const Effect& effect, const Node& node, Diagnostics& d) {
    if (!valid_node_id(node.id))
        d.error("E002", "node id \"" + node.id + "\" does not match ^[a-z][a-z0-9_]*$", node.id);

    const NodeSpec& spec = SpecRegistry::instance().get(node.type);

    // --- parameters: E004, E005, E006, E007, E018, E019 ---------------------
    for (const auto& [name, param] : node.parameters) {
        const ParamSpec* ps = spec.find_param(name);
        if (ps == nullptr) {
            d.error("E004", "node type \"" + std::string(to_string(node.type)) + "\" has no parameter \"" + name + "\"",
                    node.id, name);
            continue;
        }
        check_value(node, *ps, param.value, "", d);
        double previous = -std::numeric_limits<double>::infinity();
        for (size_t i = 0; i < param.track.size(); ++i) {
            const Keyframe& kf = param.track[i];
            if (kf.time < 0.0 || kf.time > effect.duration)
                d.error("E018", "keyframe " + std::to_string(i) + " of \"" + name + "\" at t=" +
                                    std::to_string(kf.time) + " is outside [0, " + std::to_string(effect.duration) + "]",
                        node.id, name);
            if (i > 0 && kf.time < previous)
                d.error("E018", "keyframe track of \"" + name + "\" is not sorted by time", node.id, name);
            previous = kf.time;
            check_value(node, *ps, kf.value, " keyframe " + std::to_string(i), d);
        }
    }

    // --- layer: E014 --------------------------------------------------------
    if (node.layer && effect.find_layer(*node.layer) == nullptr)
        d.error("E014", "node references unknown layer \"" + *node.layer + "\"", node.id, "layer");

    // --- parent: E008, E009, E012, W004 -------------------------------------
    if (node.parent) {
        const Node* parent = effect.find_node(*node.parent);
        if (parent == nullptr) {
            d.error("E008", "parent \"" + *node.parent + "\" does not exist", node.id, "parent");
        } else {
            if (!valid_parent_type(parent->type))
                d.error("E009", "parent \"" + *node.parent + "\" is a " + std::string(to_string(parent->type)) +
                                    " which cannot be a spatial parent",
                        node.id, "parent");
            if (node.enabled && !parent->enabled)
                d.warning("W004", "enabled node references disabled parent \"" + *node.parent + "\"", node.id, "parent");
            // E012: walk up looking for a repeat.
            std::set<std::string> seen{node.id};
            const Node* cursor = parent;
            while (cursor != nullptr) {
                if (!seen.insert(cursor->id).second) {
                    d.error("E012", "cycle in the parent chain through \"" + cursor->id + "\"", node.id, "parent");
                    break;
                }
                cursor = cursor->parent ? effect.find_node(*cursor->parent) : nullptr;
            }
        }
    }

    // --- inputs: E008, E009, E011, E017, W004 -------------------------------
    for (const auto& [port_name, refs] : node.inputs) {
        const PortSpec* port = spec.find_input(port_name);
        if (port == nullptr)
            d.error("E017", "node type \"" + std::string(to_string(node.type)) + "\" has no input port \"" + port_name +
                                "\"",
                    node.id, port_name);
        if (port != nullptr && refs.size() > 1 && !port->multi)
            d.error("E011", "port \"" + port_name + "\" accepts a single reference but got " +
                                std::to_string(refs.size()),
                    node.id, port_name);
        for (const NodeRef& r : refs) {
            const Node* target = effect.find_node(r.node);
            if (target == nullptr) {
                d.error("E008", "port \"" + port_name + "\" references unknown node \"" + r.node + "\"", node.id,
                        port_name);
                continue;
            }
            if (port != nullptr && !port->accepts.empty() &&
                std::find(port->accepts.begin(), port->accepts.end(), target->type) == port->accepts.end()) {
                std::string allowed;
                for (NodeType t : port->accepts) allowed += (allowed.empty() ? "" : "|") + std::string(to_string(t));
                d.error("E009", "port \"" + port_name + "\" accepts " + allowed + " but \"" + r.node + "\" is a " +
                                    std::string(to_string(target->type)),
                        node.id, port_name);
            }
            if (!r.port.empty()) {
                const NodeSpec& target_spec = SpecRegistry::instance().get(target->type);
                if (std::find(target_spec.outputs.begin(), target_spec.outputs.end(), r.port) ==
                    target_spec.outputs.end())
                    d.error("E017", "node \"" + r.node + "\" (" + std::string(to_string(target->type)) +
                                        ") has no output port \"" + r.port + "\"",
                            node.id, port_name);
            }
            if (node.enabled && !target->enabled)
                d.warning("W004", "enabled node references disabled node \"" + r.node + "\" on port \"" + port_name +
                                      "\"",
                          node.id, port_name);
        }
    }

    // --- required inputs: E010 ---------------------------------------------
    for (const PortSpec& port : spec.inputs) {
        const std::vector<NodeRef>* refs = node.inputs_on(port.name);
        const bool present = refs != nullptr && !refs->empty();
        if (present) continue;
        bool required = port.required;
        if (node.type == NodeType::Emitter && port.name == "particle")
            required = !feeds_a_volume(effect, node.id);
        if (required)
            d.error("E010", "required input \"" + port.name + "\" is missing", node.id, port.name);
    }
    if (node.type == NodeType::Event) {
        const std::string trigger = node_enum(node, "trigger", "on_time");
        const std::vector<NodeRef>* source = node.inputs_on("source");
        if (needs_event_source(trigger) && (source == nullptr || source->empty()))
            d.error("E010", "trigger \"" + trigger + "\" requires a \"source\" input", node.id, "source");
    }

    // --- timeline binding: W001 --------------------------------------------
    if (spec.time_bound) {
        const std::string phase = raw_string(node, "phase");
        if (!phase.empty() && effect.timeline.find(phase) == nullptr)
            d.warning("W001", "node binds to unknown timeline phase \"" + phase + "\"", node.id, "phase");
    }
}

// --- controls: E021..E024, W007 ---------------------------------------------

// Which value types an op can fold into (docs/CONTROLS.md).
bool op_accepts(ControlOp op, ValueType type) {
    if (op == ControlOp::HueShift) return type == ValueType::Color || type == ValueType::Gradient;
    switch (type) {
        case ValueType::Int:
        case ValueType::Float:
        case ValueType::Vec2:
        case ValueType::Vec3:
        case ValueType::Vec4:
        case ValueType::Color:
            return true;
        default:
            return false;
    }
}

void check_controls(const Effect& effect, Diagnostics& d) {
    std::set<std::string> seen;
    for (const Control& control : effect.controls) {
        const std::string where = "control \"" + control.id + "\"";
        if (!valid_node_id(control.id))
            d.error("E021", "control id \"" + control.id + "\" does not match ^[a-z][a-z0-9_]*$");
        else if (!seen.insert(control.id).second)
            d.error("E021", "duplicate control id \"" + control.id + "\"");

        if (!(control.min < control.max))
            d.error("E024", where + " has min " + std::to_string(control.min) + " which is not below max " +
                                std::to_string(control.max));
        else {
            if (control.value < control.min || control.value > control.max)
                d.error("E024", where + " value " + std::to_string(control.value) + " is outside [" +
                                    std::to_string(control.min) + ", " + std::to_string(control.max) + "]");
            if (control.default_value < control.min || control.default_value > control.max)
                d.error("E024", where + " default " + std::to_string(control.default_value) + " is outside [" +
                                    std::to_string(control.min) + ", " + std::to_string(control.max) + "]");
        }
        if (!(control.step > 0.0)) d.error("E024", where + " step must be greater than 0");

        if (control.bindings.empty())
            d.warning("W007", where + " has no bindings, so moving it does nothing");

        for (const ControlBinding& binding : control.bindings) {
            // `$effect` addresses the document itself, not a node: today the
            // only property it exposes is `time_scale` (docs/CONTROLS.md 2.1).
            if (binding.node == kEffectBindingNode) {
                if (binding.parameter != kTimeScaleParameter) {
                    d.error("E022", where + " binds to \"" + std::string(kEffectBindingNode) + "." +
                                        binding.parameter + "\"; the effect only exposes \"" +
                                        std::string(kTimeScaleParameter) + "\"",
                            binding.node, binding.parameter);
                } else if (binding.op != ControlOp::Multiply && binding.op != ControlOp::Set) {
                    d.error("E023", where + " applies \"" + std::string(to_string(binding.op)) + "\" to \"" +
                                        std::string(kTimeScaleParameter) +
                                        "\", which takes multiply or set",
                            binding.node, binding.parameter);
                }
                continue;
            }
            const Node* node = effect.find_node(binding.node);
            if (node == nullptr) {
                d.error("E022", where + " binds to unknown node \"" + binding.node + "\"", binding.node,
                        binding.parameter);
                continue;
            }
            const ParamSpec* spec = SpecRegistry::instance().get(node->type).find_param(binding.parameter);
            if (spec == nullptr) {
                d.error("E022", where + " binds to \"" + binding.parameter + "\", which node type \"" +
                                    std::string(to_string(node->type)) + "\" does not have",
                        binding.node, binding.parameter);
                continue;
            }
            if (!op_accepts(binding.op, spec->type))
                d.error("E023", where + " applies \"" + std::string(to_string(binding.op)) + "\" to \"" +
                                    binding.parameter + "\", which is " + std::string(to_string(spec->type)) +
                                    "; multiply/add/set need a numeric parameter and hue_shift a colour or gradient",
                        binding.node, binding.parameter);
        }
    }
}

// Dependencies of a node: everything it references, deduplicated and sorted.
std::set<NodeId> dependencies_of(const Effect& effect, const Node& node, bool enabled_only) {
    std::set<NodeId> deps;
    auto consider = [&](const NodeId& id) {
        const Node* target = effect.find_node(id);
        if (target == nullptr) return;
        if (enabled_only && !target->enabled) return;
        deps.insert(id);
    };
    for (const auto& [port, refs] : node.inputs) {
        (void)port;
        for (const NodeRef& r : refs) consider(r.node);
    }
    if (node.parent) consider(*node.parent);
    return deps;
}

// Kahn with a min-heap on node id. `cycle_out` receives the nodes that never
// reached in-degree zero.
std::vector<NodeId> kahn(const Effect& effect, bool enabled_only, std::vector<NodeId>* cycle_out) {
    std::map<NodeId, int> indegree;
    std::map<NodeId, std::set<NodeId>> dependents;
    for (const Node& n : effect.nodes) {
        if (enabled_only && !n.enabled) continue;
        indegree.emplace(n.id, 0);
    }
    for (const Node& n : effect.nodes) {
        if (enabled_only && !n.enabled) continue;
        for (const NodeId& dep : dependencies_of(effect, n, enabled_only)) {
            if (indegree.find(dep) == indegree.end()) continue;
            if (dependents[dep].insert(n.id).second) ++indegree[n.id];
        }
    }
    std::priority_queue<NodeId, std::vector<NodeId>, std::greater<>> ready;
    for (const auto& [id, degree] : indegree)
        if (degree == 0) ready.push(id);

    std::vector<NodeId> order;
    order.reserve(indegree.size());
    while (!ready.empty()) {
        NodeId id = ready.top();
        ready.pop();
        order.push_back(id);
        auto it = dependents.find(id);
        if (it == dependents.end()) continue;
        for (const NodeId& next : it->second)
            if (--indegree[next] == 0) ready.push(next);
    }
    if (cycle_out != nullptr && order.size() != indegree.size()) {
        std::set<NodeId> emitted(order.begin(), order.end());
        for (const auto& [id, degree] : indegree) {
            (void)degree;
            if (emitted.find(id) == emitted.end()) cycle_out->push_back(id);
        }
    }
    return order;
}

}  // namespace

Diagnostics validate_node(const Effect& effect, const Node& node) {
    Diagnostics d;
    check_node(effect, node, d);
    return d;
}

Diagnostics validate(const Effect& effect) {
    Diagnostics d;

    // --- document level: E020, E015, E016 -----------------------------------
    if (effect.schema_version != kSchemaVersion)
        d.error("E020", "unsupported schema version \"" + effect.schema_version + "\"; this build supports \"" +
                            std::string(kSchemaVersion) + "\"");
    if (!(effect.duration > 0.0))
        d.error("E015", "effect duration must be > 0 (got " + std::to_string(effect.duration) + ")");
    if (!(effect.time_scale >= kMinTimeScale && effect.time_scale <= kMaxTimeScale))
        d.error("E015", "effect time_scale must be in [" + std::to_string(kMinTimeScale) + ", " +
                            std::to_string(kMaxTimeScale) + "] (got " + std::to_string(effect.time_scale) + ")");
    for (const TimelinePhase& p : effect.timeline.phases) {
        if (p.end <= p.start)
            d.error("E016", "phase \"" + p.name + "\" ends at " + std::to_string(p.end) + " which is not after its start " +
                                std::to_string(p.start));
        if (p.start < 0.0 || (effect.duration > 0.0 && p.end > effect.duration))
            d.error("E016", "phase \"" + p.name + "\" [" + std::to_string(p.start) + ", " + std::to_string(p.end) +
                                "] lies outside the effect duration [0, " + std::to_string(effect.duration) + "]");
    }

    // --- controls: E021..E024, W007 ------------------------------------------
    check_controls(effect, d);

    // --- node ids: E001 ------------------------------------------------------
    std::set<NodeId> seen;
    for (const Node& n : effect.nodes)
        if (!seen.insert(n.id).second) d.error("E001", "duplicate node id \"" + n.id + "\"", n.id);

    // --- per node ------------------------------------------------------------
    for (const Node& n : effect.nodes) check_node(effect, n, d);

    // --- E013 cycles in the input graph --------------------------------------
    std::vector<NodeId> cycle;
    kahn(effect, /*enabled_only=*/false, &cycle);
    for (const NodeId& id : cycle) d.error("E013", "node \"" + id + "\" takes part in a cycle in the input graph", id);

    // --- W002 unused nodes ---------------------------------------------------
    for (const Node& n : effect.nodes) {
        if (!is_unused_candidate(n)) continue;
        if (!effect.consumers_of(n.id).empty()) continue;
        d.warning("W002", "node \"" + n.id + "\" (" + std::string(to_string(n.type)) +
                              ") is never referenced and is not renderable on its own",
                  n.id);
    }

    // --- W003 particle budget ------------------------------------------------
    long long budget = 0;
    for (const Node& n : effect.nodes) {
        if (n.type != NodeType::ParticleSystem || !n.enabled) continue;
        budget += node_int(n, "max_particles", 10000);
    }
    if (budget > kParticleBudget)
        d.warning("W003", "total max_particles across particle systems is " + std::to_string(budget) +
                              ", above the " + std::to_string(kParticleBudget) + " budget");

    return d;
}

std::vector<NodeId> topological_order(const Effect& effect) {
    std::vector<NodeId> cycle;
    std::vector<NodeId> order = kahn(effect, /*enabled_only=*/true, &cycle);
    if (!cycle.empty()) {
        std::string ids;
        for (const NodeId& id : cycle) ids += (ids.empty() ? "" : ", ") + id;
        throw Error("E013", "cycle in the input graph involving: " + ids);
    }
    return order;
}

}  // namespace aether
