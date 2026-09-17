#pragma once
// Effect -> CompiledEffect. See docs/ARCHITECTURE.md section 4.
#include <filesystem>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/diagnostics.hpp"
#include "aether/core/effect.hpp"
#include "aether/core/resources.hpp"

namespace aether::compiler {

enum class Tier { Analytic = 0, Particles = 1, Physics = 2, Volumetric = 3 };
std::string_view to_string(Tier t);

struct CompiledNode {
    NodeId id;
    NodeType type = NodeType::Emitter;
    Tier tier = Tier::Analytic;
    std::string backend;          // "analytic", "cpu_particles", "cpu_particles_collision", "volume_stub", ...
    double start_time = 0.0;      // resolved window (phase applied)
    double end_time = -1.0;       // -1 = effect end
    uint64_t seed = 0;            // derived stream seed
    // Resolved references (ids of enabled nodes only), so the runtime never re-resolves ports.
    std::vector<NodeId> forces;
    std::vector<NodeId> colliders;
    std::vector<NodeId> sources;      // volume sources / event sources
    std::vector<NodeId> targets;      // event targets
    NodeId particle_system;           // emitter -> particle_system
    NodeId trail;                     // particle_system -> trail
    std::string material_id;          // ResourceSet keys ("" = default)
    std::string sprite_id;
    std::string mesh_id;
    int mesh_variants = 1;            // mesh nodes: baked seeded variants ("<id>", "<id>#1" .. "<id>#N-1")
    std::string texture_id;
    NodeId shape_curve;               // emitter shape=curve / trail source curve
    NodeId shape_mesh;
    NodeId origin_node, target_node;  // beam
    NodeId source_node;               // trail / event source
    NodeId noise_node;                // force noise override
    nlohmann::json to_json() const;
};

struct CompileOptions {
    double fixed_dt = 1.0 / 60.0;
    size_t particle_budget = 250000;   // W003 above this total
    bool bake_textures = true;
    bool allow_errors = false;         // when true, compile proceeds past validation errors (tools' dry-run)
    // Directory that relative `texture.path` (source: file) values resolve against.
    // Empty = the process working directory. The tools layer sets it to the
    // directory of the document the effect was loaded from.
    std::filesystem::path base_dir;
};

struct CompiledEffect {
    Effect effect;                      // deep copy of the source at compile time
    ResourceSet resources;
    std::vector<CompiledNode> nodes;    // execution order (topological, ties by id); enabled nodes only
    Diagnostics diagnostics;
    double fixed_dt = 1.0 / 60.0;
    uint64_t source_hash = 0;           // effect_hash at compile time
    int controls_applied = 0;           // bindings that changed a parameter in `effect`

    // The resolved playback speed and the wall-clock seconds the effect lasts
    // at it. Both read `effect`, which already has the controls folded in, so a
    // host never has to re-apply anything (docs/RUNTIME.md 11).
    double time_scale() const { return effect.time_scale; }
    double wall_duration() const { return effect.wall_duration(); }

    const CompiledNode* find(std::string_view id) const;
    std::vector<const CompiledNode*> of_type(NodeType t) const;
    nlohmann::json plan_json() const;   // {"nodes":[...], "tiers": {...counts}, "resources": {...}, "diagnostics": ...}
    bool ok() const { return diagnostics.ok(); }
};

// Never mutates `source`. `CompiledEffect::effect` is a copy with the document's
// controls folded in (docs/CONTROLS.md), which is what every later stage reads.
// Throws Error only for programmer errors; content problems are reported in
// diagnostics (and, unless allow_errors, nodes is left empty when there are
// validation errors).
CompiledEffect compile(const Effect& source, const CompileOptions& options = {});

// Resolves a node's window using the effect timeline when `phase` is set.
// Emits W001 when the phase is unknown. Exposed for tools/inspect.
void resolve_window(const Effect& effect, const Node& node, double& start, double& end, Diagnostics* diag);

}  // namespace aether::compiler
