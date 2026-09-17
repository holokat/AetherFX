// Effect -> CompiledEffect: window resolution, execution order, tier/backend
// selection, reference resolution and resource baking.
// See docs/ARCHITECTURE.md section 4 and docs/RUNTIME.md.
#include "aether/compiler/compiled_effect.hpp"

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <map>
#include <string>
#include <string_view>
#include <vector>

#include "aether/core/controls.hpp"
#include "aether/core/error.hpp"
#include "aether/core/rng.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"
#include "aether/imageio/image_io.hpp"
#include "aether/procedural/mesh_primitives.hpp"
#include "aether/procedural/texture_graph.hpp"

namespace aether::compiler {
namespace {

// --------------------------------------------------------------------------
// reference resolution (enabled nodes only; validation already warned W004)
// --------------------------------------------------------------------------

const Node* enabled_node(const Effect& effect, const NodeId& id) {
    const Node* n = effect.find_node(id);
    return (n != nullptr && n->enabled) ? n : nullptr;
}

NodeId ref_on(const Effect& effect, const Node& node, std::string_view port) {
    std::optional<NodeRef> r = node.input(port);
    if (!r) return {};
    const Node* n = enabled_node(effect, r->node);
    return n != nullptr ? n->id : NodeId{};
}

std::vector<NodeId> refs_on(const Effect& effect, const Node& node, std::string_view port) {
    std::vector<NodeId> out;
    const std::vector<NodeRef>* refs = node.inputs_on(port);
    if (refs == nullptr) return out;
    out.reserve(refs->size());
    for (const NodeRef& r : *refs)
        if (const Node* n = enabled_node(effect, r.node)) out.push_back(n->id);
    return out;
}

// The trail node that follows `system`, either through the system's own
// `trail` port or through a trail node whose `source` is the system.
NodeId trail_of_system(const Effect& effect, const Node& system) {
    NodeId direct = ref_on(effect, system, "trail");
    if (!direct.empty()) return direct;
    for (const Node& n : effect.nodes) {
        if (n.type != NodeType::Trail || !n.enabled) continue;
        std::optional<NodeRef> src = n.input("source");
        if (src && src->node == system.id) return n.id;
    }
    return {};
}

uint32_t seed32_of(uint64_t seed) { return static_cast<uint32_t>(seed ^ (seed >> 32)); }

std::string hex64(uint64_t v) {
    char buf[24];
    std::snprintf(buf, sizeof buf, "%016llx", static_cast<unsigned long long>(v));
    return std::string(buf);
}

// --------------------------------------------------------------------------
// resources
// --------------------------------------------------------------------------

// `variant_seed` only matters for the seeded primitives (crystal/rock/shard).
MeshData build_mesh(const Node& n, uint32_t variant_seed, Diagnostics& diag) {
    const std::string source = param_string(n, "source");
    const float radius = param_float(n, "radius");
    const float inner_radius = param_float(n, "inner_radius");
    const float height = param_float(n, "height");
    const Vec3 size = param_vec3(n, "size");
    const int segments = param_int(n, "segments");

    if (source == "imported") {
        const std::string path = param_string(n, "path");
        try {
            return procedural::load_obj(path);
        } catch (const Error& err) {
            diag.error("E101", "mesh \"" + n.id + "\" could not import \"" + path + "\": " + err.what(), n.id, "path");
            return procedural::make_sphere(radius, segments);
        }
    }
    if (source != "primitive") {
        diag.warning("W106", "mesh \"" + n.id + "\" uses source \"" + source +
                                 "\", which V1 does not generate; a sphere primitive is used instead",
                     n.id, "source");
        return procedural::make_sphere(radius, segments);
    }

    const std::string primitive = param_string(n, "primitive");
    const float irregularity = param_float(n, "irregularity");
    if (primitive == "crystal") return procedural::make_crystal(radius, height, segments, irregularity, variant_seed);
    if (primitive == "rock") return procedural::make_rock(radius, segments, irregularity, variant_seed);
    if (primitive == "shard") return procedural::make_shard(radius, height, irregularity, variant_seed);
    if (primitive == "cube") return procedural::make_cube(size);
    if (primitive == "plane") return procedural::make_plane(Vec2{size.x, size.z}, 1);
    if (primitive == "disc") return procedural::make_disc(radius, inner_radius, segments);
    if (primitive == "ring") return procedural::make_ring(radius, inner_radius, segments);
    if (primitive == "cone") return procedural::make_cone(radius, height, segments);
    if (primitive == "cylinder") return procedural::make_cylinder(radius, height, segments);
    if (primitive == "capsule") return procedural::make_capsule(radius, height, segments);
    if (primitive == "ribbon" || primitive == "tube") {
        const std::vector<Vec3> path{{0.0f, -0.5f * height, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.5f * height, 0.0f}};
        return primitive == "tube" ? procedural::make_tube(path, radius, segments)
                                   : procedural::make_ribbon(path, radius * 2.0f);
    }
    return procedural::make_sphere(radius, segments);
}

// crystal/rock/shard are the only seeded primitives, so they are the only ones
// for which `variants` means anything; every other primitive ignores it.
bool bakes_variants(const Node& n) {
    if (param_string(n, "source") != "primitive") return false;
    const std::string primitive = param_string(n, "primitive");
    return primitive == "crystal" || primitive == "rock" || primitive == "shard";
}

MaterialDesc build_material(const Effect& effect, const Node& n) {
    MaterialDesc m;
    m.id = n.id;
    m.base_color = param_color(n, "base_color");
    m.opacity = param_float(n, "opacity");
    m.emissive_color = param_color(n, "emissive_color");
    m.emissive_intensity = param_float(n, "emissive_intensity");
    parse_blend_mode(param_string(n, "blend"), m.blend);
    parse_shading(param_string(n, "shading"), m.shading);
    m.soft_particle = param_bool(n, "soft_particle");
    m.depth_fade = param_float(n, "depth_fade");
    m.distortion = param_float(n, "distortion");
    m.fresnel_power = param_float(n, "fresnel_power");
    m.uv_scroll = param_vec2(n, "uv_scroll");
    m.uv_rotate = param_float(n, "uv_rotate");
    m.dissolve = param_float(n, "dissolve");
    m.erosion = param_float(n, "erosion");
    m.double_sided = param_bool(n, "double_sided");
    m.temperature_gradient = param_gradient(n, "temperature_gradient");
    m.base_texture = ref_on(effect, n, "base_texture");
    m.noise_texture = ref_on(effect, n, "noise_texture");
    m.gradient_texture = ref_on(effect, n, "gradient_texture");
    return m;
}

// source: file. `path` is absolute or relative to options.base_dir (the directory of the
// document the effect came from; empty = the working directory). A columns x rows grid is
// unrolled into the engine's horizontal strip layout (frames = columns*rows); `frames` alone
// means the file already is a horizontal strip. Explicit width/height resample every frame.
void bake_file_texture(const Node& n, const CompileOptions& options, ResourceSet& resources, Diagnostics& diag) {
    const std::string path_text = param_string(n, "path");
    if (path_text.empty()) {
        diag.error("E102", "texture \"" + n.id + "\" has source \"file\" but an empty path", n.id, "path");
        return;
    }
    std::filesystem::path path(path_text);
    if (path.is_relative() && !options.base_dir.empty()) path = options.base_dir / path;

    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec)) {
        diag.error("E102", "texture \"" + n.id + "\" file not found: \"" + path.string() + "\"", n.id, "path");
        return;
    }
    Image source;
    try {
        source = imageio::read_image(path);
    } catch (const Error& err) {
        diag.error("E102", "texture \"" + n.id + "\" could not read \"" + path.string() + "\": " + err.what(),
                   n.id, "path");
        return;
    }
    if (source.empty()) {
        diag.error("E102", "texture \"" + n.id + "\" read an empty image from \"" + path.string() + "\"",
                   n.id, "path");
        return;
    }

    const int columns = std::max(1, param_int(n, "columns"));
    const int rows = std::max(1, param_int(n, "rows"));
    int grid_columns = columns;
    int grid_rows = rows;
    int frames = columns * rows;
    if (frames == 1) {
        // No grid: `frames` > 1 means the file is already a horizontal strip.
        frames = std::max(1, param_int(n, "frames"));
        grid_columns = frames;
        grid_rows = 1;
    }
    const int frame_width = source.width / grid_columns;
    const int frame_height = source.height / grid_rows;
    if (frame_width <= 0 || frame_height <= 0) {
        diag.error("E102", "texture \"" + n.id + "\" is " + std::to_string(source.width) + "x" +
                               std::to_string(source.height) + ", too small for a " +
                               std::to_string(grid_columns) + "x" + std::to_string(grid_rows) + " flipbook grid",
                   n.id, "columns");
        return;
    }
    if (frames > 1 && (source.width % grid_columns != 0 || source.height % grid_rows != 0)) {
        diag.warning("W105", "texture \"" + n.id + "\" is " + std::to_string(source.width) + "x" +
                                 std::to_string(source.height) + ", which a " + std::to_string(grid_columns) + "x" +
                                 std::to_string(grid_rows) + " grid does not divide evenly; the trailing pixels "
                                 "are dropped",
                     n.id, "columns");
    }

    // width/height only resample when the author asked for a specific size.
    const int target_width = n.has_param("width") ? std::max(1, param_int(n, "width")) : frame_width;
    const int target_height = n.has_param("height") ? std::max(1, param_int(n, "height")) : frame_height;
    const bool resize = target_width != frame_width || target_height != frame_height;

    TextureResource resource;
    resource.frames = frames;
    resource.image.resize(target_width * frames, target_height, Color::transparent());
    Image cell(frame_width, frame_height);
    Image scaled;
    for (int f = 0; f < frames; ++f) {
        const int cx = (f % grid_columns) * frame_width;
        const int cy = (f / grid_columns) * frame_height;
        for (int y = 0; y < frame_height; ++y)
            for (int x = 0; x < frame_width; ++x) cell.set(x, y, source.get(cx + x, cy + y));
        if (resize) scaled = procedural::resample(cell, target_width, target_height);
        const Image& out_cell = resize ? scaled : cell;
        const int x0 = f * target_width;
        for (int y = 0; y < target_height; ++y)
            for (int x = 0; x < target_width; ++x) resource.image.set(x0 + x, y, out_cell.get(x, y));
    }
    resources.textures[n.id] = std::move(resource);
}

void bake_texture(const Node& n, uint64_t seed, const CompileOptions& options, ResourceSet& resources,
                  Diagnostics& diag) {
    if (param_string(n, "source") == "file") {
        bake_file_texture(n, options, resources, diag);
        return;
    }
    const nlohmann::json graph = param_json(n, "graph");
    const Diagnostics graph_diag = procedural::validate_texture_graph(graph);
    bool rejected = false;
    for (const Diagnostic& item : graph_diag.items) {
        std::string message = "texture \"" + n.id + "\" graph: " + item.message;
        if (item.node) message += " (graph node \"" + *item.node + "\")";
        diag.error(item.code, message, n.id, item.param ? item.param : std::optional<std::string>("graph"));
        rejected = true;
    }
    if (rejected) return;

    procedural::TextureBakeOptions bake;
    bake.width = param_int(n, "width");
    bake.height = param_int(n, "height");
    bake.frames = param_int(n, "frames");
    bake.seed = seed32_of(seed);
    try {
        resources.textures[n.id] = procedural::bake_texture_graph(graph, bake);
    } catch (const Error& err) {
        diag.error("E102", "texture \"" + n.id + "\" failed to bake: " + err.what(), n.id, "graph");
    }
}

}  // namespace

// --------------------------------------------------------------------------
// CompiledNode / CompiledEffect
// --------------------------------------------------------------------------

std::string_view to_string(Tier t) {
    switch (t) {
        case Tier::Analytic: return "analytic";
        case Tier::Particles: return "particles";
        case Tier::Physics: return "physics";
        case Tier::Volumetric: return "volumetric";
    }
    return "analytic";
}

nlohmann::json CompiledNode::to_json() const {
    nlohmann::json j;
    j["id"] = id;
    j["type"] = std::string(aether::to_string(type));
    j["tier"] = std::string(to_string(tier));
    j["backend"] = backend;
    j["start_time"] = start_time;
    j["end_time"] = end_time;
    j["seed"] = seed;
    j["forces"] = forces;
    j["colliders"] = colliders;
    j["sources"] = sources;
    j["targets"] = targets;
    j["particle_system"] = particle_system;
    j["trail"] = trail;
    j["material_id"] = material_id;
    j["sprite_id"] = sprite_id;
    j["mesh_id"] = mesh_id;
    j["mesh_variants"] = mesh_variants;
    j["texture_id"] = texture_id;
    j["shape_curve"] = shape_curve;
    j["shape_mesh"] = shape_mesh;
    j["origin_node"] = origin_node;
    j["target_node"] = target_node;
    j["source_node"] = source_node;
    j["noise_node"] = noise_node;
    return j;
}

const CompiledNode* CompiledEffect::find(std::string_view id) const {
    for (const CompiledNode& n : nodes)
        if (n.id == id) return &n;
    return nullptr;
}

std::vector<const CompiledNode*> CompiledEffect::of_type(NodeType t) const {
    std::vector<const CompiledNode*> out;
    for (const CompiledNode& n : nodes)
        if (n.type == t) out.push_back(&n);
    return out;
}

nlohmann::json CompiledEffect::plan_json() const {
    nlohmann::json j;
    j["fixed_dt"] = fixed_dt;
    j["source_hash"] = hex64(source_hash);
    j["controls"] = {{"count", effect.controls.size()}, {"applied", controls_applied}};

    nlohmann::json node_array = nlohmann::json::array();
    std::map<std::string, size_t> backend_counts;
    size_t tier_counts[4] = {0, 0, 0, 0};
    for (const CompiledNode& n : nodes) {
        node_array.push_back(n.to_json());
        ++tier_counts[static_cast<int>(n.tier)];
        ++backend_counts[n.backend];
    }
    j["nodes"] = std::move(node_array);
    j["tiers"] = {{"analytic", tier_counts[0]},
                  {"particles", tier_counts[1]},
                  {"physics", tier_counts[2]},
                  {"volumetric", tier_counts[3]}};
    nlohmann::json backends = nlohmann::json::object();
    for (const auto& [name, count] : backend_counts) backends[name] = count;
    j["backends"] = std::move(backends);

    nlohmann::json textures = nlohmann::json::array();
    for (const auto& [id, tex] : resources.textures) {
        (void)tex;
        textures.push_back(id);
    }
    nlohmann::json meshes = nlohmann::json::array();
    for (const auto& [id, mesh] : resources.meshes) {
        (void)mesh;
        meshes.push_back(id);
    }
    nlohmann::json materials = nlohmann::json::array();
    for (const auto& [id, mat] : resources.materials) {
        (void)mat;
        materials.push_back(id);
    }
    j["resources"] = {{"textures", std::move(textures)}, {"meshes", std::move(meshes)},
                      {"materials", std::move(materials)}};
    j["diagnostics"] = diagnostics.to_json();
    return j;
}

// --------------------------------------------------------------------------
// window resolution
// --------------------------------------------------------------------------

void resolve_window(const Effect& effect, const Node& node, double& start, double& end, Diagnostics* diag) {
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    if (!spec.time_bound) {
        start = 0.0;
        end = -1.0;
        return;
    }
    const TimeWindow fallback = node_window(node);
    const std::string phase = param_string(node, "phase");
    if (!phase.empty()) {
        if (const TimelinePhase* p = effect.timeline.find(phase)) {
            start = p->start;
            end = p->end;
            return;
        }
        if (diag != nullptr)
            diag->warning("W001", "node \"" + node.id + "\" references unknown timeline phase \"" + phase +
                                      "\"; falling back to start_time/duration",
                          node.id, "phase");
    }
    start = fallback.start;
    end = fallback.end;
}

// --------------------------------------------------------------------------
// compile
// --------------------------------------------------------------------------

CompiledEffect compile(const Effect& source, const CompileOptions& options) {
    CompiledEffect compiled;
    compiled.effect = source;  // deep copy; `source` is never mutated
    compiled.fixed_dt = options.fixed_dt;
    compiled.source_hash = effect_hash(source);
    compiled.diagnostics = validate(source);
    if (!compiled.diagnostics.ok() && !options.allow_errors) return compiled;

    // Controls fold into the copy, after validation and before anything reads a
    // parameter, so every stage below (windows, resources, runtime, export)
    // sees one already-resolved document. At their default values this is a
    // no-op, which is what keeps a controlled effect bit-identical to the same
    // effect without controls (docs/CONTROLS.md).
    compiled.controls_applied = apply_controls(compiled.effect);
    const Effect& effect = compiled.effect;

    std::vector<NodeId> order;
    try {
        order = topological_order(effect);
    } catch (const Error& err) {
        compiled.diagnostics.error(err.code(), err.what());
        return compiled;
    }

    Diagnostics& diag = compiled.diagnostics;
    compiled.nodes.reserve(order.size());
    long long particle_total = 0;

    for (const NodeId& id : order) {
        const Node* node = effect.find_node(id);
        if (node == nullptr || !node->enabled) continue;
        const Node& n = *node;

        CompiledNode cn;
        cn.id = n.id;
        cn.type = n.type;
        resolve_window(effect, n, cn.start_time, cn.end_time, &diag);
        cn.seed = derive_seed(effect.seed, n.id, n.seed);

        // --- resolved references -------------------------------------------
        switch (n.type) {
            case NodeType::Emitter:
                cn.particle_system = ref_on(effect, n, "particle");
                cn.shape_curve = ref_on(effect, n, "shape_curve");
                cn.shape_mesh = ref_on(effect, n, "shape_mesh");
                if (NodeId volume = ref_on(effect, n, "shape_volume"); !volume.empty())
                    cn.sources.push_back(volume);  // shape=volume bounds provider
                break;
            case NodeType::ParticleSystem:
                cn.forces = refs_on(effect, n, "forces");
                cn.colliders = refs_on(effect, n, "colliders");
                cn.material_id = ref_on(effect, n, "material");
                cn.sprite_id = ref_on(effect, n, "sprite");
                cn.mesh_id = ref_on(effect, n, "mesh");
                cn.trail = trail_of_system(effect, n);
                break;
            case NodeType::Force:
                cn.noise_node = ref_on(effect, n, "noise");
                break;
            case NodeType::Field:
                cn.sources = refs_on(effect, n, "source");
                cn.source_node = ref_on(effect, n, "source");
                break;
            case NodeType::Volume:
                cn.sources = refs_on(effect, n, "sources");
                cn.forces = refs_on(effect, n, "forces");
                cn.colliders = refs_on(effect, n, "colliders");
                cn.material_id = ref_on(effect, n, "material");
                break;
            case NodeType::Mesh:
                cn.material_id = ref_on(effect, n, "material");
                cn.mesh_id = n.id;  // ResourceSet key
                break;
            case NodeType::Trail:
                cn.source_node = ref_on(effect, n, "source");
                cn.material_id = ref_on(effect, n, "material");
                if (const Node* src = enabled_node(effect, cn.source_node)) {
                    if (src->type == NodeType::Curve) cn.shape_curve = src->id;
                    if (src->type == NodeType::ParticleSystem) cn.particle_system = src->id;
                }
                break;
            case NodeType::Beam:
                cn.origin_node = ref_on(effect, n, "origin_node");
                cn.target_node = ref_on(effect, n, "target_node");
                cn.material_id = ref_on(effect, n, "material");
                break;
            case NodeType::Decal:
                cn.material_id = ref_on(effect, n, "material");
                cn.texture_id = ref_on(effect, n, "texture");
                break;
            case NodeType::Material:
                cn.material_id = n.id;  // ResourceSet key
                cn.texture_id = ref_on(effect, n, "base_texture");
                break;
            case NodeType::Texture:
                cn.texture_id = n.id;  // ResourceSet key
                break;
            case NodeType::Collider:
                cn.shape_mesh = ref_on(effect, n, "mesh");
                break;
            case NodeType::Event:
                cn.source_node = ref_on(effect, n, "source");
                if (!cn.source_node.empty()) cn.sources.push_back(cn.source_node);
                cn.targets = refs_on(effect, n, "targets");
                break;
            default:
                break;
        }

        // --- tier and backend ----------------------------------------------
        switch (n.type) {
            case NodeType::Emitter:
                cn.tier = Tier::Particles;
                cn.backend = "cpu_particles";
                break;
            case NodeType::ParticleSystem: {
                particle_total += param_int(n, "max_particles");
                if (param_string(n, "physics") == "rigid") {
                    cn.tier = Tier::Physics;
                    cn.backend = "cpu_particles_collision";
                    diag.warning("W102", "rigid physics falls back to particle collision in V1 (node \"" + n.id + "\")",
                                 n.id, "physics");
                } else {
                    cn.tier = Tier::Particles;
                    cn.backend = cn.colliders.empty() ? "cpu_particles" : "cpu_particles_collision";
                }
                break;
            }
            case NodeType::Volume:
                cn.tier = Tier::Volumetric;
                // mode: procedural is a fully implemented raymarched density field
                // (docs/VOLUMES.md); only the fluid path is still a stub.
                if (param_string(n, "mode") == "simulation") {
                    cn.backend = "volume_stub";
                    diag.warning("W104", "volume \"" + n.id + "\" uses the V1 stub backend: it reports statistics only",
                                 n.id, "mode");
                } else {
                    cn.backend = "procedural_volume";
                }
                break;
            default:
                cn.tier = Tier::Analytic;
                cn.backend = "analytic";
                break;
        }

        // --- per-type notes -------------------------------------------------
        if (n.type == NodeType::Collider) {
            const std::string collider_type = param_string(n, "collider_type");
            if (collider_type == "mesh" || collider_type == "sdf")
                diag.warning("W101", "collider \"" + n.id + "\" of type \"" + collider_type +
                                         "\" is compiled but ignored by the V1 runtime",
                             n.id, "collider_type");
        }
        if (n.type == NodeType::Trail && !cn.particle_system.empty())
            diag.warning("W103", "trail \"" + n.id + "\" follows particle system \"" + cn.particle_system +
                                     "\"; the V1 runtime emits at most 512 ribbons",
                         n.id, "source");

        compiled.nodes.push_back(std::move(cn));
    }

    // --- resources ----------------------------------------------------------
    for (CompiledNode& cn : compiled.nodes) {
        const Node* node = effect.find_node(cn.id);
        if (node == nullptr) continue;
        switch (node->type) {
            case NodeType::Texture:
                if (options.bake_textures) bake_texture(*node, cn.seed, options, compiled.resources, diag);
                break;
            case NodeType::Mesh: {
                // Seeded primitives bake `variants` meshes under "<id>", "<id>#1", ...
                // "<id>#N-1", each from derive_seed(node stream, i).
                cn.mesh_variants = bakes_variants(*node) ? clamp(param_int(*node, "variants"), 1, 16) : 1;
                for (int i = 0; i < cn.mesh_variants; ++i) {
                    MeshData mesh = build_mesh(*node, seed32_of(derive_seed(cn.seed, static_cast<uint64_t>(i))), diag);
                    mesh.build_area_table();
                    compiled.resources.meshes[i == 0 ? node->id : node->id + "#" + std::to_string(i)] = std::move(mesh);
                }
                break;
            }
            case NodeType::Material:
                compiled.resources.materials[node->id] = build_material(effect, *node);
                break;
            default:
                break;
        }
    }

    diag.info("I001", "total max_particles across enabled particle systems: " + std::to_string(particle_total));
    return compiled;
}

}  // namespace aether::compiler
