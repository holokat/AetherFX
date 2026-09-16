// docs/AGENT_API.md "graph": layers, nodes and connections.
#include <algorithm>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

std::string layer_role_names() {
    std::string names;
    for (int i = 0; i <= static_cast<int>(LayerRole::Custom); ++i)
        names += (names.empty() ? "" : ", ") + std::string(to_string(static_cast<LayerRole>(i)));
    return names;
}

std::string node_type_names() {
    std::string names;
    for (int i = 0; i < kNodeTypeCount; ++i)
        names += (names.empty() ? "" : ", ") + std::string(to_string(static_cast<NodeType>(i)));
    return names;
}

std::vector<NodeRef> refs_from_json(const nlohmann::json& value, const std::string& port) {
    std::vector<NodeRef> refs;
    if (value.is_string()) {
        if (!value.get<std::string>().empty()) refs.push_back(NodeRef::parse(value.get<std::string>()));
    } else if (value.is_array()) {
        for (const auto& entry : value) {
            if (!entry.is_string())
                throw Error("bad_argument", "input port \"" + port + "\" takes node ids as strings, got " +
                                                std::string(entry.type_name()));
            if (!entry.get<std::string>().empty()) refs.push_back(NodeRef::parse(entry.get<std::string>()));
        }
    } else {
        throw Error("bad_argument", "input port \"" + port + "\" must be a node id or a list of node ids, got " +
                                        std::string(value.type_name()));
    }
    return refs;
}

// Fills parameters/inputs/metadata that create_node and the typed wrappers share.
// Unknown parameter names and values that do not fit keep their raw JSON so
// validate_node reports E004/E005 instead of the call failing outright.
void apply_node_arguments(Node& node, const nlohmann::json& args) {
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    const nlohmann::json& parameters = arg(args, "parameters");
    if (!parameters.is_null()) {
        if (!parameters.is_object()) throw Error("bad_argument", "\"parameters\" must be an object of name -> value");
        for (const auto& [name, value] : parameters.items()) {
            const ParamSpec* param_spec = spec.find_param(name);
            if (param_spec == nullptr) {
                node.parameters[name] = Parameter{value};
                continue;
            }
            try {
                node.parameters[name] = parameter_from_json(value, param_spec->type);
            } catch (const Error&) {
                node.parameters[name] = Parameter{value};
            }
        }
    }
    const nlohmann::json& inputs = arg(args, "inputs");
    if (!inputs.is_null()) {
        if (!inputs.is_object()) throw Error("bad_argument", "\"inputs\" must be an object of port -> node id(s)");
        for (const auto& [port, value] : inputs.items()) {
            std::vector<NodeRef> refs = refs_from_json(value, port);
            if (!refs.empty()) node.inputs[port] = std::move(refs);
        }
    }
    if (has_arg(args, "metadata")) {
        const nlohmann::json& metadata = arg(args, "metadata");
        if (!metadata.is_object()) throw Error("bad_argument", "\"metadata\" must be an object");
        node.metadata = metadata;
    }
    if (has_arg(args, "seed")) node.seed = static_cast<uint32_t>(arg_int(args, "seed", 0));
    if (has_arg(args, "parent")) node.parent = arg_string(args, "parent");
    if (has_arg(args, "layer")) node.layer = arg_string(args, "layer");
    if (has_arg(args, "enabled")) node.enabled = arg_bool(args, "enabled", true);
}

nlohmann::json create_node_of_type(Session& session, const nlohmann::json& args, NodeType type) {
    Document& doc = session.document_for(args);
    Node node;
    node.type = type;
    node.id = has_arg(args, "id") ? arg_string(args, "id") : doc.effect.unique_id(to_string(type));
    apply_node_arguments(node, args);

    Mutation mutation(session, doc);
    Node& added = doc.effect.add_node(std::move(node));  // throws E001 on a duplicate id
    mutation.commit();

    nlohmann::json result = ok_with(validate_node(doc.effect, added));
    result["node"] = node_json(added);
    return result;
}

nlohmann::json create_node(Session& session, const nlohmann::json& args) {
    const std::string type_name = require_string(args, "type", "create_node");
    NodeType type{};
    if (!parse_node_type(type_name, type))
        throw Error("E003", "unknown node type \"" + type_name + "\"; the vocabulary has [" + node_type_names() + "]");
    return create_node_of_type(session, args, type);
}

nlohmann::json create_layer(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    Layer layer;
    layer.name = require_string(args, "name", "create_layer");
    const std::string role_name = require_string(args, "role", "create_layer");
    if (!parse_layer_role(role_name, layer.role))
        throw Error("bad_argument", "create_layer: unknown role \"" + role_name + "\"; expected one of [" +
                                        layer_role_names() + "]");
    layer.id = has_arg(args, "id") ? arg_string(args, "id") : slugify(layer.name);
    if (doc.effect.find_layer(layer.id) != nullptr)
        throw Error("E001", "a layer with id \"" + layer.id + "\" already exists; pass a different \"id\"");

    Mutation mutation(session, doc);
    doc.effect.layers.push_back(layer);
    mutation.commit();
    nlohmann::json result = ok_with(validate(doc.effect));
    result["layer"] = layer_json(doc.effect.layers.back());
    return result;
}

nlohmann::json delete_layer(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string layer_id = require_string(args, "layer_id", "delete_layer");
    require_layer(doc.effect, layer_id);
    const bool delete_nodes = arg_bool(args, "delete_nodes", false);

    Mutation mutation(session, doc);
    nlohmann::json removed = nlohmann::json::array();
    std::vector<NodeId> ids;
    for (const Node* node : doc.effect.nodes_in_layer(layer_id)) ids.push_back(node->id);
    for (const NodeId& id : ids) {
        if (delete_nodes) {
            doc.effect.remove_node(id);
            removed.push_back(id);
        } else if (Node* node = doc.effect.find_node(id)) {
            node->layer.reset();
        }
    }
    auto& layers = doc.effect.layers;
    layers.erase(std::remove_if(layers.begin(), layers.end(), [&](const Layer& l) { return l.id == layer_id; }),
                 layers.end());
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["removed_nodes"] = std::move(removed);
    result["layer_id"] = layer_id;
    return result;
}

nlohmann::json duplicate_layer(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string layer_id = require_string(args, "layer_id", "duplicate_layer");
    const Layer source = require_layer(doc.effect, layer_id);
    const std::string suffix = arg_string(args, "suffix", "_copy");
    const std::string new_id = has_arg(args, "new_id") ? arg_string(args, "new_id") : layer_id + suffix;
    if (doc.effect.find_layer(new_id) != nullptr)
        throw Error("E001", "a layer with id \"" + new_id + "\" already exists; pass a different \"new_id\"");

    // Plan the id remapping first so internal references can be rewritten.
    std::vector<Node> clones;
    std::map<NodeId, NodeId> remap;
    Effect planning = doc.effect;  // id allocation must see the ids taken by earlier clones
    for (const Node* node : doc.effect.nodes_in_layer(layer_id)) {
        Node clone = *node;
        clone.id = planning.unique_id(node->id + suffix);
        remap[node->id] = clone.id;
        planning.add_node(clone);
        clones.push_back(std::move(clone));
    }
    for (Node& clone : clones) {
        clone.layer = new_id;
        if (clone.parent) {
            auto it = remap.find(*clone.parent);
            if (it != remap.end()) clone.parent = it->second;
        }
        for (auto& [port, refs] : clone.inputs) {
            (void)port;
            for (NodeRef& ref : refs) {
                auto it = remap.find(ref.node);
                if (it != remap.end()) ref.node = it->second;  // references outside the layer are kept as they are
            }
        }
    }

    Mutation mutation(session, doc);
    Layer layer = source;
    layer.id = new_id;
    layer.name = source.name + " copy";
    doc.effect.layers.push_back(layer);
    nlohmann::json ids = nlohmann::json::array();
    for (Node& clone : clones) {
        ids.push_back(clone.id);
        doc.effect.add_node(std::move(clone));
    }
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["layer"] = layer_json(doc.effect.layers.back());
    result["nodes"] = std::move(ids);
    return result;
}

nlohmann::json set_layer_property(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string layer_id = require_string(args, "layer_id", "set_layer_property");
    require_layer(doc.effect, layer_id);

    Mutation mutation(session, doc);
    Layer& layer = require_layer(doc.effect, layer_id);
    if (has_arg(args, "name")) layer.name = arg_string(args, "name");
    if (has_arg(args, "role")) {
        const std::string role_name = arg_string(args, "role");
        if (!parse_layer_role(role_name, layer.role))
            throw Error("bad_argument", "set_layer_property: unknown role \"" + role_name + "\"; expected one of [" +
                                            layer_role_names() + "]");
    }
    if (has_arg(args, "enabled")) layer.enabled = arg_bool(args, "enabled", true);
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["layer"] = layer_json(require_layer(doc.effect, layer_id));
    return result;
}

nlohmann::json delete_node(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "delete_node");
    require_node(doc.effect, node_id);

    nlohmann::json dangling = nlohmann::json::array();
    for (const Node& node : doc.effect.nodes) {
        if (node.id == node_id) continue;
        if (node.parent && *node.parent == node_id) dangling.push_back({{"node", node.id}, {"port", "parent"}});
        for (const auto& [port, refs] : node.inputs) {
            const bool referenced = std::any_of(refs.begin(), refs.end(),
                                                [&](const NodeRef& ref) { return ref.node == node_id; });
            if (referenced) dangling.push_back({{"node", node.id}, {"port", port}});
        }
    }

    Mutation mutation(session, doc);
    doc.effect.remove_node(node_id);
    mutation.commit();

    nlohmann::json result = ok_with(validate(doc.effect));
    result["dangling"] = std::move(dangling);
    result["node_id"] = node_id;
    return result;
}

nlohmann::json duplicate_node(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "duplicate_node");
    Node clone = require_node(doc.effect, node_id);
    clone.id = has_arg(args, "new_id") ? arg_string(args, "new_id") : doc.effect.unique_id(node_id + "_copy");

    Mutation mutation(session, doc);
    Node& added = doc.effect.add_node(std::move(clone));
    mutation.commit();

    nlohmann::json result = ok_with(validate_node(doc.effect, added));
    result["node"] = node_json(added);
    return result;
}

nlohmann::json set_node_property(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string node_id = require_string(args, "node_id", "set_node_property");
    require_node(doc.effect, node_id);

    Mutation mutation(session, doc);
    Node& node = require_node(doc.effect, node_id);
    if (has_arg(args, "enabled")) node.enabled = arg_bool(args, "enabled", true);
    if (has_arg(args, "parent")) {
        const std::string parent = arg_string(args, "parent");
        if (parent.empty()) node.parent.reset();
        else node.parent = parent;
    }
    if (has_arg(args, "layer")) {
        const std::string layer = arg_string(args, "layer");
        if (layer.empty()) node.layer.reset();
        else node.layer = layer;
    }
    if (has_arg(args, "seed")) node.seed = static_cast<uint32_t>(arg_int(args, "seed", 0));
    if (has_arg(args, "metadata")) {
        const nlohmann::json& metadata = arg(args, "metadata");
        if (!metadata.is_object()) throw Error("bad_argument", "set_node_property: \"metadata\" must be an object");
        node.metadata = metadata;
    }
    mutation.commit();

    Node& updated = require_node(doc.effect, node_id);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["node"] = node_json(updated);
    return result;
}

const PortSpec& require_port(const Node& node, const std::string& port, const char* tool) {
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    if (const PortSpec* port_spec = spec.find_input(port)) return *port_spec;
    std::string known;
    for (const PortSpec& candidate : spec.inputs) known += (known.empty() ? "" : ", ") + candidate.name;
    throw ToolError("E017",
                    std::string(tool) + ": node type \"" + std::string(to_string(node.type)) + "\" has no input port \"" +
                        port + "\"" + (known.empty() ? "; it has no input ports" : "; its ports are [" + known + "]"),
                    node.id, port);
}

nlohmann::json connect_nodes(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string from = require_string(args, "from", "connect_nodes");
    const std::string to = require_string(args, "to", "connect_nodes");
    const std::string port = require_string(args, "port", "connect_nodes");
    const Node& target = require_node(doc.effect, to);
    const PortSpec& port_spec = require_port(target, port, "connect_nodes");
    const NodeRef ref = NodeRef::parse(from);

    Mutation mutation(session, doc);
    Node& node = require_node(doc.effect, to);
    std::vector<NodeRef>& refs = node.inputs[port];
    if (port_spec.multi) {
        if (std::find(refs.begin(), refs.end(), ref) == refs.end()) refs.push_back(ref);
    } else {
        refs.assign(1, ref);
    }
    mutation.commit();

    Node& updated = require_node(doc.effect, to);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["node"] = node_json(updated);
    result["port"] = port;
    result["multi"] = port_spec.multi;
    return result;
}

nlohmann::json disconnect_nodes(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string to = require_string(args, "to", "disconnect_nodes");
    const std::string port = require_string(args, "port", "disconnect_nodes");
    const Node& target = require_node(doc.effect, to);
    require_port(target, port, "disconnect_nodes");

    Mutation mutation(session, doc);
    Node& node = require_node(doc.effect, to);
    auto it = node.inputs.find(port);
    if (it != node.inputs.end()) {
        if (has_arg(args, "from")) {
            const NodeRef ref = NodeRef::parse(arg_string(args, "from"));
            auto& refs = it->second;
            refs.erase(std::remove_if(refs.begin(), refs.end(),
                                      [&](const NodeRef& r) { return r == ref || r.node == ref.node; }),
                       refs.end());
            if (refs.empty()) node.inputs.erase(it);
        } else {
            node.inputs.erase(it);
        }
    }
    mutation.commit();

    Node& updated = require_node(doc.effect, to);
    nlohmann::json result = ok_with(validate_node(doc.effect, updated));
    result["node"] = node_json(updated);
    return result;
}

nlohmann::json node_argument_schema(bool with_type) {
    nlohmann::json properties{
        {"id", prop("string", "Node id, ^[a-z][a-z0-9_]*$. Defaults to a unique id derived from the type.")},
        {"layer", prop("string", "Id of the layer this node belongs to.")},
        {"parent", prop("string", "Id of a spatial parent node; this node's transform is composed with it.")},
        {"parameters", prop("object", "Parameter values by name, using the JSON encoding of docs/VOCABULARY.md "
                                      "(a bare value, or {\"value\": v, \"track\": [{\"time\", \"value\", \"interp\"}]}).")},
        {"inputs", prop("object", "Input ports by name: a node id, or a list of node ids for multi ports.")},
        {"metadata", prop("object", "Free-form metadata; record your rationale here.")},
        {"seed", prop("integer", "Per-node random seed; derived from the effect seed when omitted.")},
        {"enabled", prop("boolean", "Whether the node takes part in the simulation (default true).")}};
    if (with_type) properties["type"] = prop("string", "Node type, e.g. \"emitter\" or \"particle_system\".");
    return make_schema(std::move(properties), with_type ? std::vector<std::string>{"type"} : std::vector<std::string>{});
}

}  // namespace

void register_graph_tools(ToolRegistry& registry) {
    registry.add({"create_layer",
                  "Create a semantic layer to group nodes by their role in the effect (telegraph, ignition, primary, "
                  "secondary, interaction, aftermath, custom). Layers carry no simulation behaviour; they make the "
                  "graph readable and let you duplicate a whole stage at once. Returns {ok, layer, diagnostics}.",
                  make_schema({{"name", prop("string", "Human readable layer name, e.g. \"Primary\".")},
                               {"role", prop("string", "One of telegraph, ignition, primary, secondary, interaction, "
                                                       "aftermath, custom.")},
                               {"id", prop("string", "Layer id; defaults to a slug of the name.")}},
                              {"name", "role"}),
                  true, "graph"},
                 create_layer);

    registry.add({"delete_layer",
                  "Delete a layer. By default its nodes survive and simply lose their layer; pass delete_nodes=true "
                  "to remove them (and every reference to them) as well. Returns {ok, removed_nodes, diagnostics}.",
                  make_schema({{"layer_id", prop("string", "Layer to delete.")},
                               {"delete_nodes", prop("boolean", "Also delete the nodes in the layer (default false).")}},
                              {"layer_id"}),
                  true, "graph"},
                 delete_layer);

    registry.add({"duplicate_layer",
                  "Copy a layer and every node in it. References between the copied nodes are remapped to the copies; "
                  "references to nodes outside the layer are kept. Use it to fork a working stage and vary it. "
                  "Returns {ok, layer, nodes, diagnostics}.",
                  make_schema({{"layer_id", prop("string", "Layer to duplicate.")},
                               {"new_id", prop("string", "Id of the new layer; defaults to <layer_id><suffix>.")},
                               {"suffix", prop("string", "Suffix appended to the copied node ids (default \"_copy\").")}},
                              {"layer_id"}),
                  true, "graph"},
                 duplicate_layer);

    registry.add({"set_layer_property",
                  "Rename a layer, change its role, or enable/disable it. Returns {ok, layer, diagnostics}.",
                  make_schema({{"layer_id", prop("string", "Layer to change.")},
                               {"name", prop("string", "New name.")},
                               {"role", prop("string", "New role (telegraph, ignition, primary, secondary, "
                                                       "interaction, aftermath, custom).")},
                               {"enabled", prop("boolean", "Whether the layer is enabled.")}},
                              {"layer_id"}),
                  true, "graph"},
                 set_layer_property);

    registry.add({"create_node",
                  "Create a node of any vocabulary type and add it to the active effect. Parameters and input ports "
                  "may be supplied in the same call. Unknown parameter names are kept and reported as diagnostics "
                  "(E004) rather than rejected, so one call always tells you everything that is wrong. "
                  "Returns {ok, node, diagnostics}.",
                  node_argument_schema(true), true, "graph"},
                 create_node);

    for (int i = 0; i < kNodeTypeCount; ++i) {
        const NodeType type = static_cast<NodeType>(i);
        const NodeSpec& spec = SpecRegistry::instance().get(type);
        const std::string type_name(to_string(type));
        registry.add({"create_" + type_name,
                      "Create a " + type_name + " node: " + spec.description +
                          " Same arguments as create_node without `type`. Returns {ok, node, diagnostics}.",
                      node_argument_schema(false), true, "graph"},
                     [type](Session& session, const nlohmann::json& args) {
                         return create_node_of_type(session, args, type);
                     });
    }

    registry.add({"delete_node",
                  "Delete a node and strip every reference to it from other nodes' input ports and parent links. "
                  "The cleared references come back as `dangling`, so you can reconnect them. "
                  "Returns {ok, dangling, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node to delete.")}}, {"node_id"}), true, "graph"},
                 delete_node);

    registry.add({"duplicate_node",
                  "Copy one node, including its parameters, inputs and metadata. The copy keeps the original's "
                  "references. Returns {ok, node, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node to duplicate.")},
                               {"new_id", prop("string", "Id of the copy; defaults to <node_id>_copy.")}},
                              {"node_id"}),
                  true, "graph"},
                 duplicate_node);

    registry.add({"set_node_property",
                  "Change the non-parameter properties of a node: enabled, spatial parent, layer, seed and metadata. "
                  "Pass an empty string to clear parent or layer. Returns {ok, node, diagnostics}.",
                  make_schema({{"node_id", prop("string", "Node to change.")},
                               {"enabled", prop("boolean", "Whether the node takes part in the simulation.")},
                               {"parent", prop("string", "Spatial parent node id, or \"\" to clear it.")},
                               {"layer", prop("string", "Layer id, or \"\" to clear it.")},
                               {"seed", prop("integer", "Per-node random seed.")},
                               {"metadata", prop("object", "Free-form metadata, replaces the current one.")}},
                              {"node_id"}),
                  true, "graph"},
                 set_node_property);

    registry.add({"connect_nodes",
                  "Connect the output of one node to an input port of another (for example an emitter's `particle` "
                  "port to a particle_system, or a force to a particle_system's `forces` port). Multi ports append "
                  "without duplicates; single ports are replaced. Unknown ports fail with E017; unresolved or "
                  "wrongly typed references come back as E008/E009 diagnostics. Returns {ok, node, diagnostics}.",
                  make_schema({{"from", prop("string", "Source node id, optionally \"node_id.output_port\".")},
                               {"to", prop("string", "Target node id that receives the connection.")},
                               {"port", prop("string", "Input port on the target node, e.g. \"forces\".")}},
                              {"from", "to", "port"}),
                  true, "graph"},
                 connect_nodes);

    registry.add({"disconnect_nodes",
                  "Remove one reference from an input port, or clear the whole port when `from` is omitted. "
                  "Returns {ok, node, diagnostics}.",
                  make_schema({{"to", prop("string", "Node whose input port is edited.")},
                               {"port", prop("string", "Input port to clear.")},
                               {"from", prop("string", "Only remove this source node id; omit to clear the port.")}},
                              {"to", "port"}),
                  true, "graph"},
                 disconnect_nodes);
}

}  // namespace aether::tools
