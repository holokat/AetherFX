#pragma once
#include "aether/core/diagnostics.hpp"
#include "aether/core/effect.hpp"

namespace aether {

// Full semantic validation against SpecRegistry. Never throws for content
// problems; every finding is a Diagnostic (codes in docs/VOCABULARY.md).
Diagnostics validate(const Effect& effect);

// Validates a single node in the context of the effect (used by tools for
// fast feedback after set_parameter / connect_nodes).
Diagnostics validate_node(const Effect& effect, const Node& node);

// Execution order of enabled nodes: dependencies (inputs, parent) first, ties
// broken by node id. Throws Error("E013") on cycles.
std::vector<NodeId> topological_order(const Effect& effect);

}  // namespace aether
