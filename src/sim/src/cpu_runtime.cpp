// The CPU reference runtime. This file implements docs/RUNTIME.md exactly:
// step order (section 1), seeding (2), emitter spawn (3), forces (4),
// integration and collision (5), over-life modulation (6), analytic nodes (7),
// events (8) and statistics (9). Determinism rules: docs/ARCHITECTURE.md
// section 6 - PCG32 only, fixed timestep, no wall clock in state, no threads,
// no unordered iteration that affects results.
#include "aether/sim/runtime.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/core/rng.hpp"
#include "aether/core/spec.hpp"
#include "aether/procedural/mesh_primitives.hpp"
#include "aether/procedural/noise.hpp"

namespace aether::sim {

nlohmann::json Statistics::to_json() const {
    nlohmann::json systems_json = nlohmann::json::array();
    for (const SystemStatistics& s : systems)
        systems_json.push_back({{"system_id", s.system_id},
                                {"alive", s.alive},
                                {"spawned_total", s.spawned_total},
                                {"died_total", s.died_total},
                                {"peak_alive", s.peak_alive},
                                {"capacity", s.capacity},
                                {"dropped", s.dropped},
                                {"collisions", s.collisions}});
    return {{"time", time},
            {"frame", frame},
            {"systems", std::move(systems_json)},
            {"total_alive", total_alive},
            {"total_spawned", total_spawned},
            {"events_fired", events_fired},
            {"last_step_ms", last_step_ms},
            {"total_step_ms", total_step_ms}};
}

namespace {

using compiler::CompiledEffect;
using compiler::CompiledNode;

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------

constexpr float kTiny = 1e-12f;
// Normal approach speed above which an existing contact reports again
// (docs/RUNTIME.md section 5).
constexpr float kImpactSpeed = 0.1f;

uint32_t seed32_of(uint64_t seed) { return static_cast<uint32_t>(seed ^ (seed >> 32)); }

// smoothstep that degrades to a hard edge when the width is zero.
float fade_ramp(float width, float x) {
    if (!(width > 0.0f)) return x > 0.0f ? 1.0f : 0.0f;
    return smoothstep(0.0f, width, x);
}

Vec3 rotate_around(Vec3 v, Vec3 axis, float radians) {
    const Vec3 a = normalize(axis);
    const float c = std::cos(radians), s = std::sin(radians);
    return v * c + cross(a, v) * s + a * (dot(a, v) * (1.0f - c));
}

// Value in [-1, 1] that is a smooth function of time only (docs/RUNTIME.md
// section 7, light flicker).
float time_flicker(uint32_t seed, double t, float frequency) {
    if (!(frequency > 0.0f)) return 0.0f;
    const double cell = t * static_cast<double>(frequency);
    const double base = std::floor(cell);
    const float f = static_cast<float>(cell - base);
    const float a = procedural::hash_noise2(Vec2{static_cast<float>(base), 0.0f}, seed) * 2.0f - 1.0f;
    const float b = procedural::hash_noise2(Vec2{static_cast<float>(base) + 1.0f, 0.0f}, seed) * 2.0f - 1.0f;
    return lerp(a, b, smootherstep(f));
}

enum class Shape { Point, Sphere, Hemisphere, Box, Disc, Ring, Cone, Line, Curve, Mesh, Volume };

Shape parse_shape(const std::string& s) {
    if (s == "point") return Shape::Point;
    if (s == "hemisphere") return Shape::Hemisphere;
    if (s == "box") return Shape::Box;
    if (s == "disc") return Shape::Disc;
    if (s == "ring") return Shape::Ring;
    if (s == "cone") return Shape::Cone;
    if (s == "line") return Shape::Line;
    if (s == "curve") return Shape::Curve;
    if (s == "mesh") return Shape::Mesh;
    if (s == "volume") return Shape::Volume;
    return Shape::Sphere;
}

enum class ForceKind { Gravity, Directional, Radial, Vortex, Turbulence, CurlNoise, Drag, Attractor, Repulsor, Wind, Buoyancy };

ForceKind parse_force(const std::string& s) {
    if (s == "directional") return ForceKind::Directional;
    if (s == "radial") return ForceKind::Radial;
    if (s == "vortex") return ForceKind::Vortex;
    if (s == "turbulence") return ForceKind::Turbulence;
    if (s == "curl_noise") return ForceKind::CurlNoise;
    if (s == "drag") return ForceKind::Drag;
    if (s == "attractor") return ForceKind::Attractor;
    if (s == "repulsor") return ForceKind::Repulsor;
    if (s == "wind") return ForceKind::Wind;
    if (s == "buoyancy") return ForceKind::Buoyancy;
    return ForceKind::Gravity;
}

enum class Falloff { None, Linear, InverseSquare, Smooth };

Falloff parse_falloff(const std::string& s) {
    if (s == "linear") return Falloff::Linear;
    if (s == "inverse_square") return Falloff::InverseSquare;
    if (s == "smooth") return Falloff::Smooth;
    return Falloff::None;
}

// docs/RUNTIME.md section 4.
float falloff_of(Falloff kind, float radius, float d) {
    switch (kind) {
        case Falloff::None: return 1.0f;
        case Falloff::Linear: return radius > 0.0f ? std::max(0.0f, 1.0f - d / radius) : 1.0f;
        case Falloff::InverseSquare: {
            const float base = 1.0f / (1.0f + d * d);
            return radius > 0.0f ? base * std::max(0.0f, 1.0f - d / radius) : base;
        }
        case Falloff::Smooth: return radius > 0.0f ? 1.0f - smoothstep(0.0f, radius, d) : 1.0f;
    }
    return 1.0f;
}

enum class ColliderKind { Plane, Sphere, Box, Capsule, Unsupported };

ColliderKind parse_collider(const std::string& s) {
    if (s == "sphere") return ColliderKind::Sphere;
    if (s == "box") return ColliderKind::Box;
    if (s == "capsule") return ColliderKind::Capsule;
    if (s == "mesh" || s == "sdf") return ColliderKind::Unsupported;
    return ColliderKind::Plane;
}

enum class Trigger { OnSpawn, OnDeath, OnCollision, OnDistance, OnTime, OnPeak };

Trigger parse_trigger(const std::string& s) {
    if (s == "on_spawn") return Trigger::OnSpawn;
    if (s == "on_death") return Trigger::OnDeath;
    if (s == "on_collision" || s == "on_impact") return Trigger::OnCollision;
    if (s == "on_distance") return Trigger::OnDistance;
    if (s == "on_peak") return Trigger::OnPeak;
    return Trigger::OnTime;
}

// ---------------------------------------------------------------------------
// per-node runtime state
// ---------------------------------------------------------------------------

struct ForceRuntime {
    const Node* node = nullptr;
    ForceKind kind = ForceKind::Gravity;
    Falloff falloff = Falloff::None;
    double start = 0.0, end = -1.0;
    Vec3 direction{0, -1, 0};
    float radius = 0.0f;
    float temperature = 1.0f;
    procedural::NoiseNodeParams noise;
    std::string noise_type;  // owned; NoiseNodeParams::noise_type is non-owning
    // NoiseNodeParams with `noise_type` pointing at this force's own string.
    procedural::NoiseNodeParams noise_params() const {
        procedural::NoiseNodeParams params = noise;
        params.noise_type = noise_type.c_str();
        return params;
    }
    uint32_t noise_seed = 0;
    // per-step
    bool active = false;
    float strength = 0.0f;
    Vec3 position{};
    Vec3 dir_scaled{};  // normalize(direction) * strength
};

struct ColliderRuntime {
    const Node* node = nullptr;
    ColliderKind kind = ColliderKind::Plane;
    float radius = 0.5f, height = 1.0f;
    Vec3 half_size{0.5f, 0.5f, 0.5f};
    Vec3 local_normal{0, 1, 0};
    float bounce = 0.3f, friction = 0.2f;
    bool kill = false;
    // per-step world placement
    Vec3 position{};
    Vec3 normal{0, 1, 0};
    Mat4 world, inverse_world;
};

struct SpawnBatch {
    size_t emitter = 0;
    uint32_t count = 0;
    bool has_position = false;
    Vec3 position{};
    Vec3 extra_velocity{};
};

struct EmitterRuntime {
    const Node* node = nullptr;
    size_t system = SIZE_MAX;
    uint64_t stream_seed = 0;
    double start = 0.0, end = -1.0;
    Shape shape = Shape::Sphere;
    float radius = 0.5f, inner_radius = 0.0f, cone_angle_rad = 0.0f, line_length = 1.0f;
    Vec3 box_size{1, 1, 1};
    bool surface_only = false;
    int burst_count = 0;
    std::vector<float> burst_times;
    float velocity_variance = 0.0f, spread_rad = 0.0f, inherit_velocity = 0.0f, radial_velocity = 0.0f;
    Vec3 direction{0, 1, 0};
    bool radial_direction = false;
    long long max_particles = 0;
    MeshData* shape_mesh = nullptr;
    const Node* shape_curve = nullptr;
    std::vector<Vec3> curve_points;
    procedural::CurveType curve_type = procedural::CurveType::CatmullRom;
    bool curve_closed = false;
    const Node* shape_volume = nullptr;
    // state
    double accumulator = 0.0;
    uint64_t spawn_index = 0;   // monotonic request counter: seeds the particle RNG
    uint64_t emitted = 0;       // particles actually created (max_particles cap)
    bool inside_window = false;
    bool has_previous_center = false;
    Vec3 previous_center{};
    // per-step
    Mat4 world;
    Vec3 center{};
    Vec3 world_velocity{};
    float velocity = 1.0f;
};

struct TrailRibbon {
    std::vector<Vec3> base_position;
    std::vector<float> age;
    std::vector<float> cumulative;
    bool seen = false;
};

struct TrailRuntime {
    const Node* node = nullptr;
    const CompiledNode* cn = nullptr;
    double start = 0.0, end = -1.0;
    uint32_t seed = 0;
    const Node* source = nullptr;
    size_t source_system = SIZE_MAX;
    std::vector<Vec3> curve_points;
    float lifetime = 0.5f, min_vertex_distance = 0.02f, noise_amplitude = 0.0f, noise_frequency = 1.0f;
    float uv_scroll = 0.0f, twist = 0.0f;
    int max_segments = 64;
    Curve taper, opacity_over_life;
    BlendMode blend = BlendMode::Additive;
    TrailRibbon single;
    std::map<uint32_t, TrailRibbon> per_particle;
};

struct EventRuntime {
    const Node* node = nullptr;
    Trigger trigger = Trigger::OnTime;
    double when = 0.0;
    float distance = 1.0f;
    float probability = 1.0f;
    long long max_triggers = 0;
    int burst_count = 0;
    bool inherit_position = true;
    float inherit_velocity = 0.0f;
    uint64_t seed = 0;
    size_t source_system = SIZE_MAX;
    std::vector<size_t> targets;  // emitter indices
    int distance_bit = -1;
    size_t triggers = 0;
    bool fired = false;
};

struct SystemRuntime {
    const Node* node = nullptr;
    const CompiledNode* cn = nullptr;
    size_t buffer = 0;
    size_t max_particles = 10000;
    float lifetime = 1.0f, lifetime_variance = 0.0f;
    float size = 0.1f, size_variance = 0.0f;
    float rotation = 0.0f, rotation_variance = 0.0f;
    float angular_velocity = 0.0f, angular_velocity_variance = 0.0f;
    float opacity = 1.0f, emissive = 0.0f, mass = 1.0f, drag = 0.0f;
    Color color{1, 1, 1, 1};
    Curve size_over_life, opacity_over_life, emissive_over_life;
    Gradient color_over_life;
    bool kill_on_collision = false;
    float collision_radius = 0.0f;
    float bounce = 0.3f, friction = 0.2f;
    std::vector<size_t> forces, colliders;
    std::vector<size_t> spawn_events, death_events, collision_events, distance_events;
    std::vector<size_t> trails;
    // private per-particle arrays, parallel to the ParticleBuffer
    std::vector<float> base_size, base_opacity, base_emissive;
    std::vector<Color> base_color;
    std::vector<Vec3> spawn_position;
    std::vector<uint64_t> seed64;
    std::vector<uint32_t> distance_mask;
    // bit j = "was in contact with colliders[j] at the end of the previous
    // step" (docs/RUNTIME.md section 5, resting contacts); colliders beyond
    // the first 32 on one system are not contact-tracked.
    std::vector<uint32_t> contact_mask;
    // spawn queues
    std::vector<SpawnBatch> emitter_batches, event_batches;
    // statistics
    size_t spawned_total = 0, died_total = 0, peak_alive = 0, dropped = 0, collisions = 0;
};

// ---------------------------------------------------------------------------
// CpuRuntime
// ---------------------------------------------------------------------------

class CpuRuntime final : public IRuntime {
public:
    explicit CpuRuntime(const CompiledEffect& compiled) : compiled_(compiled) {
        dt_ = compiled_.fixed_dt > 0.0 ? compiled_.fixed_dt : 1.0 / 60.0;
        build();
        reset();
    }

    std::string backend_name() const override { return "cpu"; }
    void reset() override;
    void step() override;
    void simulate_to(double target) override {
        while (time_ + dt_ * 0.5 < target) step();
    }
    double time() const override { return time_; }
    uint64_t frame_index() const override { return frame_; }
    double fixed_dt() const override { return dt_; }
    const FrameState& state() const override { return state_; }
    Statistics statistics() const override;
    const CompiledEffect& compiled() const override { return compiled_; }

private:
    void build();
    void rebuild_state();

    // step phases
    void update_analytic(double t1);
    void update_emitters(double t0, double t1);
    void update_systems(double t1);
    void update_system(SystemRuntime& s, double t1);
    void update_time_events(double t0, double t1);

    // analytic node emitters
    void emit_light(const CompiledNode& cn, const Node& n, double t1);
    void emit_beam(const CompiledNode& cn, const Node& n, double t1);
    void emit_mesh(const CompiledNode& cn, const Node& n, double t1);
    void emit_decal(const CompiledNode& cn, const Node& n, double t1);
    void emit_camera(const Node& n, double t1);
    void emit_post_effect(const CompiledNode& cn, const Node& n, double t1);
    void emit_volume(const CompiledNode& cn, const Node& n, double t1);
    void update_trail(TrailRuntime& tr, double t1);

    // particle plumbing
    void run_spawn_batch(SystemRuntime& s, const SpawnBatch& batch, double t1);
    void spawn_one(SystemRuntime& s, EmitterRuntime& em, const SpawnBatch& batch, uint64_t k, double t1);
    Vec3 sample_shape(EmitterRuntime& em, Pcg32& rng) const;
    void accumulate_forces(SystemRuntime& s, ParticleBuffer& b, double t1);
    void collide(SystemRuntime& s, ParticleBuffer& b, std::vector<uint8_t>& dead, size_t& dead_count);
    void modulate(SystemRuntime& s, ParticleBuffer& b);
    void remove_dead(SystemRuntime& s, const std::vector<uint8_t>& dead);
    void fire_particle_event(EventRuntime& ev, Vec3 position, Vec3 velocity, uint64_t particle_seed);
    void queue_event_bursts(EventRuntime& ev, Vec3 position, Vec3 velocity, bool has_position);

    bool active_at(const CompiledNode& cn, double t) const {
        if (!SpecRegistry::instance().get(cn.type).time_bound) return true;
        return t >= cn.start_time && (cn.end_time < 0.0 || t < cn.end_time);
    }
    Vec3 node_world_position(const Node& n, double t) const {
        return compiled_.effect.world_transform(n, t).translation_part();
    }

    CompiledEffect compiled_;  // owned: the runtime outlives the caller's copy and needs mutable meshes
    FrameState state_;
    double dt_ = 1.0 / 60.0;
    double time_ = 0.0;
    uint64_t frame_ = 0;
    double last_step_ms_ = 0.0;
    double total_step_ms_ = 0.0;
    size_t events_fired_ = 0;

    std::vector<SystemRuntime> systems_;
    std::vector<EmitterRuntime> emitters_;
    std::vector<ForceRuntime> forces_;
    std::vector<ColliderRuntime> colliders_;
    std::vector<TrailRuntime> trails_;
    std::vector<EventRuntime> events_;
    std::map<NodeId, size_t> system_of_node_, emitter_of_node_, force_of_node_, collider_of_node_;
    struct AnalyticEntry {
        const CompiledNode* cn;
        const Node* node;
    };
    std::vector<AnalyticEntry> analytic_order_;
    const Node* camera_node_ = nullptr;
    std::vector<uint8_t> dead_scratch_;
    std::vector<Vec3> arrival_velocity_;  // velocity before this step's integration
};

// ---------------------------------------------------------------------------
// construction
// ---------------------------------------------------------------------------

void CpuRuntime::build() {
    const Effect& effect = compiled_.effect;

    // pass 1: forces, colliders, particle systems (indices other nodes refer to)
    for (const CompiledNode& cn : compiled_.nodes) {
        const Node* n = effect.find_node(cn.id);
        if (n == nullptr) continue;
        if (cn.type == NodeType::Force) {
            ForceRuntime f;
            f.node = n;
            f.kind = parse_force(param_string(*n, "force_type"));
            f.falloff = parse_falloff(param_string(*n, "falloff"));
            f.start = cn.start_time;
            f.end = cn.end_time;
            f.direction = param_vec3(*n, "direction");
            f.radius = param_float(*n, "radius");
            f.temperature = param_float(*n, "temperature");
            const Node* noise_node = cn.noise_node.empty() ? nullptr : effect.find_node(cn.noise_node);
            if (noise_node != nullptr) {
                f.noise_type = param_string(*noise_node, "noise_type");
                f.noise.frequency = param_float(*noise_node, "frequency");
                f.noise.octaves = param_int(*noise_node, "octaves");
                f.noise.lacunarity = param_float(*noise_node, "lacunarity");
                f.noise.gain = param_float(*noise_node, "gain");
                f.noise.amplitude = param_float(*noise_node, "amplitude");
                f.noise.speed = param_float(*noise_node, "speed");
                f.noise.offset = param_vec3(*noise_node, "offset");
                const CompiledNode* noise_cn = compiled_.find(noise_node->id);
                f.noise_seed = seed32_of(noise_cn != nullptr ? noise_cn->seed : cn.seed);
            } else {
                f.noise_type = f.kind == ForceKind::CurlNoise ? "curl" : "fbm";
                f.noise.frequency = param_float(*n, "frequency");
                f.noise.octaves = param_int(*n, "octaves");
                f.noise.lacunarity = 2.0f;
                f.noise.gain = 0.5f;
                f.noise.amplitude = 1.0f;
                f.noise.speed = param_float(*n, "speed");
                f.noise.offset = Vec3::zero();
                f.noise_seed = seed32_of(cn.seed);
            }
            force_of_node_[cn.id] = forces_.size();
            forces_.push_back(std::move(f));
        } else if (cn.type == NodeType::Collider) {
            ColliderRuntime c;
            c.node = n;
            c.kind = parse_collider(param_string(*n, "collider_type"));
            c.radius = param_float(*n, "radius");
            c.height = param_float(*n, "height");
            c.half_size = param_vec3(*n, "size");
            c.local_normal = param_vec3(*n, "normal");
            c.bounce = param_float(*n, "bounce");
            c.friction = param_float(*n, "friction");
            c.kill = param_bool(*n, "kill_on_collision");
            collider_of_node_[cn.id] = colliders_.size();
            colliders_.push_back(std::move(c));
        } else if (cn.type == NodeType::ParticleSystem) {
            SystemRuntime s;
            s.node = n;
            s.cn = &cn;
            s.buffer = systems_.size();
            s.max_particles = static_cast<size_t>(std::max(1, param_int(*n, "max_particles")));
            s.lifetime = param_float(*n, "lifetime");
            s.lifetime_variance = param_float(*n, "lifetime_variance");
            s.size = param_float(*n, "size");
            s.size_variance = param_float(*n, "size_variance");
            s.rotation = param_float(*n, "rotation");
            s.rotation_variance = param_float(*n, "rotation_variance");
            s.angular_velocity = param_float(*n, "angular_velocity");
            s.angular_velocity_variance = param_float(*n, "angular_velocity_variance");
            s.opacity = param_float(*n, "opacity");
            s.emissive = param_float(*n, "emissive");
            s.mass = param_float(*n, "mass");
            s.drag = param_float(*n, "drag");
            s.color = param_color(*n, "color");
            s.size_over_life = param_curve(*n, "size_over_life");
            s.opacity_over_life = param_curve(*n, "opacity_over_life");
            s.emissive_over_life = param_curve(*n, "emissive_over_life");
            s.color_over_life = param_gradient(*n, "color_over_life");
            s.kill_on_collision = param_bool(*n, "kill_on_collision");
            s.collision_radius = param_float(*n, "collision_radius");
            s.bounce = param_float(*n, "bounce");
            s.friction = param_float(*n, "friction");
            for (const NodeId& id : cn.forces) {
                auto it = force_of_node_.find(id);
                if (it != force_of_node_.end()) s.forces.push_back(it->second);
            }
            for (const NodeId& id : cn.colliders) {
                auto it = collider_of_node_.find(id);
                if (it != collider_of_node_.end() && colliders_[it->second].kind != ColliderKind::Unsupported)
                    s.colliders.push_back(it->second);
            }
            system_of_node_[cn.id] = systems_.size();
            systems_.push_back(std::move(s));
        }
    }

    // particle buffers, in the compiled order of the particle_system nodes
    state_.particles.resize(systems_.size());
    for (SystemRuntime& s : systems_) {
        ParticleBuffer& b = state_.particles[s.buffer];
        b.system_id = s.node->id;
        parse_render_mode(param_string(*s.node, "render_mode"), b.render_mode);
        parse_blend_mode(param_string(*s.node, "blend"), b.blend);
        b.material_id = s.cn->material_id;
        b.sprite_id = s.cn->sprite_id;
        b.mesh_id = s.cn->mesh_id;
        b.velocity_stretch = param_float(*s.node, "velocity_stretch");
        b.align_to_velocity = param_bool(*s.node, "align_to_velocity");
        b.soft_particle_distance = param_float(*s.node, "soft_particle_distance");
        b.sort = param_bool(*s.node, "sort");
        b.sprite_columns = param_int(*s.node, "sprite_columns");
        b.sprite_rows = param_int(*s.node, "sprite_rows");
        b.sprite_fps = param_float(*s.node, "sprite_fps");
    }

    // pass 2: emitters, trails, events, analytic order
    for (const CompiledNode& cn : compiled_.nodes) {
        const Node* n = compiled_.effect.find_node(cn.id);
        if (n == nullptr) continue;
        switch (cn.type) {
            case NodeType::Emitter: {
                EmitterRuntime em;
                em.node = n;
                em.stream_seed = cn.seed;
                em.start = cn.start_time;
                em.end = cn.end_time;
                em.shape = parse_shape(param_string(*n, "shape"));
                em.radius = param_float(*n, "radius");
                em.inner_radius = param_float(*n, "inner_radius");
                em.cone_angle_rad = deg_to_rad(param_float(*n, "angle"));
                em.line_length = param_float(*n, "length");
                em.box_size = param_vec3(*n, "size");
                em.surface_only = param_bool(*n, "surface_only");
                em.burst_count = param_int(*n, "burst_count");
                em.burst_times = param_float_list(*n, "burst_times");
                em.velocity_variance = param_float(*n, "velocity_variance");
                em.spread_rad = deg_to_rad(param_float(*n, "spread"));
                if (em.shape == Shape::Cone) em.spread_rad = em.cone_angle_rad;
                em.inherit_velocity = param_float(*n, "inherit_velocity");
                em.radial_velocity = param_float(*n, "radial_velocity");
                em.direction = param_vec3(*n, "direction");
                em.radial_direction = length_squared(em.direction) < kTiny;
                if (!em.radial_direction) em.direction = normalize(em.direction);
                em.max_particles = param_int(*n, "max_particles");
                if (!cn.shape_mesh.empty()) {
                    auto it = compiled_.resources.meshes.find(cn.shape_mesh);
                    if (it != compiled_.resources.meshes.end()) {
                        em.shape_mesh = &it->second;
                        em.shape_mesh->build_area_table();
                    }
                }
                if (!cn.shape_curve.empty()) {
                    em.shape_curve = compiled_.effect.find_node(cn.shape_curve);
                    if (em.shape_curve != nullptr) {
                        em.curve_points = param_vec3_list(*em.shape_curve, "points");
                        procedural::parse_curve_type(param_string(*em.shape_curve, "curve_type"), em.curve_type);
                        em.curve_closed = param_bool(*em.shape_curve, "closed");
                    }
                }
                if (!cn.sources.empty()) em.shape_volume = compiled_.effect.find_node(cn.sources.front());
                auto it = system_of_node_.find(cn.particle_system);
                if (it != system_of_node_.end()) em.system = it->second;
                emitter_of_node_[cn.id] = emitters_.size();
                emitters_.push_back(std::move(em));
                break;
            }
            case NodeType::Trail: {
                TrailRuntime tr;
                tr.node = n;
                tr.cn = &cn;
                tr.start = cn.start_time;
                tr.end = cn.end_time;
                tr.seed = seed32_of(cn.seed);
                tr.source = cn.source_node.empty() ? nullptr : compiled_.effect.find_node(cn.source_node);
                if (tr.source != nullptr && tr.source->type == NodeType::Curve)
                    tr.curve_points = param_vec3_list(*tr.source, "points");
                auto it = system_of_node_.find(cn.particle_system);
                if (it != system_of_node_.end()) tr.source_system = it->second;
                tr.lifetime = std::max(0.001f, param_float(*n, "lifetime"));
                tr.min_vertex_distance = std::max(1e-4f, param_float(*n, "min_vertex_distance"));
                tr.noise_amplitude = param_float(*n, "noise_amplitude");
                tr.noise_frequency = param_float(*n, "noise_frequency");
                tr.uv_scroll = param_float(*n, "uv_scroll");
                tr.twist = param_float(*n, "twist");
                tr.max_segments = std::max(2, param_int(*n, "max_segments"));
                tr.taper = param_curve(*n, "taper");
                tr.opacity_over_life = param_curve(*n, "opacity_over_life");
                parse_blend_mode(param_string(*n, "blend"), tr.blend);
                trails_.push_back(std::move(tr));
                break;
            }
            case NodeType::Event: {
                EventRuntime ev;
                ev.node = n;
                ev.trigger = parse_trigger(param_string(*n, "trigger"));
                ev.when = static_cast<double>(param_float(*n, "time"));
                if (ev.trigger == Trigger::OnPeak) {
                    const TimelinePhase* peak = compiled_.effect.timeline.find("peak");
                    ev.when = peak != nullptr ? peak->start : compiled_.effect.duration * 0.5;
                }
                ev.distance = param_float(*n, "distance");
                ev.probability = param_float(*n, "probability");
                ev.max_triggers = param_int(*n, "max_triggers");
                ev.burst_count = param_int(*n, "burst_count");
                ev.inherit_position = param_bool(*n, "inherit_position");
                ev.inherit_velocity = param_float(*n, "inherit_velocity");
                ev.seed = cn.seed;
                if (!cn.source_node.empty()) {
                    auto it = system_of_node_.find(cn.source_node);
                    if (it != system_of_node_.end()) ev.source_system = it->second;
                }
                for (const NodeId& id : cn.targets) {
                    auto it = emitter_of_node_.find(id);
                    if (it != emitter_of_node_.end()) ev.targets.push_back(it->second);
                }
                events_.push_back(std::move(ev));
                break;
            }
            case NodeType::Camera:
                if (camera_node_ == nullptr) camera_node_ = n;
                break;
            default:
                break;
        }
        switch (cn.type) {
            case NodeType::Light:
            case NodeType::Beam:
            case NodeType::Mesh:
            case NodeType::Decal:
            case NodeType::PostEffect:
            case NodeType::Volume:
                analytic_order_.push_back(AnalyticEntry{&cn, n});
                break;
            default:
                break;
        }
    }

    // wire events back to the systems that observe them
    for (size_t i = 0; i < events_.size(); ++i) {
        EventRuntime& ev = events_[i];
        if (ev.source_system == SIZE_MAX) continue;
        SystemRuntime& s = systems_[ev.source_system];
        switch (ev.trigger) {
            case Trigger::OnSpawn: s.spawn_events.push_back(i); break;
            case Trigger::OnDeath: s.death_events.push_back(i); break;
            case Trigger::OnCollision: s.collision_events.push_back(i); break;
            case Trigger::OnDistance:
                if (s.distance_events.size() < 32) {
                    ev.distance_bit = static_cast<int>(s.distance_events.size());
                    s.distance_events.push_back(i);
                }
                break;
            default: break;
        }
    }
    // wire per-particle trails back to their systems
    for (size_t i = 0; i < trails_.size(); ++i)
        if (trails_[i].source_system != SIZE_MAX) systems_[trails_[i].source_system].trails.push_back(i);

    state_.trails.resize(trails_.size());
    for (size_t i = 0; i < trails_.size(); ++i) {
        state_.trails[i].id = trails_[i].node->id;
        state_.trails[i].blend = trails_[i].blend;
        state_.trails[i].material_id = trails_[i].cn->material_id;
        state_.trails[i].twist_deg = trails_[i].twist;
    }
}

void CpuRuntime::rebuild_state() {
    for (SystemRuntime& s : systems_) {
        ParticleBuffer& b = state_.particles[s.buffer];
        b.clear();
        b.reserve(std::min<size_t>(s.max_particles, 1u << 16));
        s.base_size.clear();
        s.base_opacity.clear();
        s.base_emissive.clear();
        s.base_color.clear();
        s.spawn_position.clear();
        s.seed64.clear();
        s.distance_mask.clear();
        s.contact_mask.clear();
        s.emitter_batches.clear();
        s.event_batches.clear();
        s.spawned_total = s.died_total = s.peak_alive = s.dropped = s.collisions = 0;
    }
    for (EmitterRuntime& em : emitters_) {
        em.accumulator = 0.0;
        em.spawn_index = 0;
        em.emitted = 0;
        em.inside_window = false;
        em.has_previous_center = false;
        em.previous_center = Vec3::zero();
        em.world = Mat4::identity();
        em.center = Vec3::zero();
        em.world_velocity = Vec3::zero();
    }
    for (EventRuntime& ev : events_) {
        ev.triggers = 0;
        ev.fired = false;
    }
    for (size_t i = 0; i < trails_.size(); ++i) {
        trails_[i].single = TrailRibbon{};
        trails_[i].per_particle.clear();
        state_.trails[i].ribbons.clear();
    }
    state_.lights.clear();
    state_.beams.clear();
    state_.decals.clear();
    state_.meshes.clear();
    state_.volumes.clear();
    state_.post_effects.clear();
    state_.camera.reset();
    events_fired_ = 0;
    last_step_ms_ = 0.0;
    total_step_ms_ = 0.0;
}

void CpuRuntime::reset() {
    time_ = 0.0;
    frame_ = 0;
    state_.time = 0.0;
    state_.frame_index = 0;
    rebuild_state();
    // Populate the analytic scene at t = 0 so that state() is renderable
    // immediately after reset() (no particles yet, per docs/RUNTIME.md).
    update_analytic(0.0);
}

// ---------------------------------------------------------------------------
// step
// ---------------------------------------------------------------------------

void CpuRuntime::step() {
    const auto wall_start = std::chrono::steady_clock::now();
    const double t0 = frame_ == 0 ? -1e-9 : time_;
    const double t1 = static_cast<double>(frame_ + 1) * dt_;

    update_analytic(t1);
    update_emitters(t0, t1);
    update_systems(t1);
    update_time_events(t0, t1);

    // trails are laid after the systems have moved (docs/RUNTIME.md 4g)
    for (TrailRuntime& tr : trails_) update_trail(tr, t1);

    frame_ += 1;
    time_ = static_cast<double>(frame_) * dt_;
    state_.time = time_;
    state_.frame_index = frame_;
    for (SystemRuntime& s : systems_)
        s.peak_alive = std::max(s.peak_alive, state_.particles[s.buffer].count());

    const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - wall_start).count();
    last_step_ms_ = ms;
    total_step_ms_ += ms;
}

// ---------------------------------------------------------------------------
// analytic nodes (docs/RUNTIME.md section 7)
// ---------------------------------------------------------------------------

void CpuRuntime::update_analytic(double t1) {
    state_.lights.clear();
    state_.beams.clear();
    state_.decals.clear();
    state_.meshes.clear();
    state_.volumes.clear();
    state_.post_effects.clear();

    for (const AnalyticEntry& entry : analytic_order_) {
        const CompiledNode& cn = *entry.cn;
        const Node& n = *entry.node;
        if (!active_at(cn, t1)) continue;
        switch (cn.type) {
            case NodeType::Light: emit_light(cn, n, t1); break;
            case NodeType::Beam: emit_beam(cn, n, t1); break;
            case NodeType::Mesh: emit_mesh(cn, n, t1); break;
            case NodeType::Decal: emit_decal(cn, n, t1); break;
            case NodeType::PostEffect: emit_post_effect(cn, n, t1); break;
            case NodeType::Volume: emit_volume(cn, n, t1); break;
            default: break;
        }
    }
    if (camera_node_ != nullptr) emit_camera(*camera_node_, t1);
    // Analytic trails are refreshed by step(); reset() only needs the scene.
}

void CpuRuntime::emit_light(const CompiledNode& cn, const Node& n, double t1) {
    LightState l;
    l.id = n.id;
    parse_light_type(param_string(n, "light_type"), l.type);
    const Mat4 world = compiled_.effect.world_transform(n, t1);
    l.position = world.translation_part();
    l.direction = normalize(world.transform_vector(param_vec3(n, "direction")));
    if (length_squared(l.direction) < kTiny) l.direction = Vec3{0, -1, 0};
    Color color = param_color(n, "color", t1);
    const float temperature = param_float(n, "temperature");
    if (temperature > 0.0f) color = Color::from_temperature(temperature) * color;
    l.color = color;
    const float amplitude = param_float(n, "flicker_amplitude");
    float intensity = param_float(n, "intensity", t1);
    if (amplitude > 0.0f)
        intensity *= 1.0f + amplitude * time_flicker(seed32_of(cn.seed), t1, param_float(n, "flicker_frequency"));
    l.intensity = intensity;
    l.radius = param_float(n, "radius", t1);
    l.cone_angle_deg = param_float(n, "cone_angle");
    l.area_size = param_vec2(n, "area_size");
    l.cast_shadows = param_bool(n, "cast_shadows");
    state_.lights.push_back(std::move(l));
}

void CpuRuntime::emit_beam(const CompiledNode& cn, const Node& n, double t1) {
    Vec3 origin = param_vec3(n, "origin", t1);
    Vec3 target = param_vec3(n, "target", t1);
    if (const Node* o = cn.origin_node.empty() ? nullptr : compiled_.effect.find_node(cn.origin_node))
        origin = node_world_position(*o, t1);
    if (const Node* tg = cn.target_node.empty() ? nullptr : compiled_.effect.find_node(cn.target_node))
        target = node_world_position(*tg, t1);

    const int segments = std::max(1, param_int(n, "segments"));
    const float amplitude = param_float(n, "noise_amplitude");
    const float frequency = param_float(n, "noise_frequency");
    const float jitter_rate = param_float(n, "jitter_rate");
    const double jitter_key = jitter_rate > 0.0f ? std::floor(t1 * static_cast<double>(jitter_rate)) : 0.0;
    const uint32_t seed = seed32_of(cn.seed);
    const procedural::FbmParams fbm{3, 2.0f, 0.5f, procedural::NoiseBasis::Simplex};
    const float key = static_cast<float>(jitter_key);

    const auto polyline = [&](Vec3 a, Vec3 b, int segs, float amp) {
        std::vector<Vec3> points;
        points.reserve(static_cast<size_t>(segs) + 1);
        const Vec3 span = b - a;
        const float span_length = length(span);
        const Vec3 forward = span_length > kEpsilon ? span / span_length : Vec3{0, 1, 0};
        const Vec3 u = orthogonal(forward);
        const Vec3 v = cross(forward, u);
        for (int i = 0; i <= segs; ++i) {
            const float f = static_cast<float>(i) / static_cast<float>(segs);
            Vec3 p = a + span * f;
            if (i > 0 && i < segs && amp > 0.0f) {
                const float along = f * span_length * frequency;
                const float n1 = procedural::fbm3(Vec3{along, 0.0f, key}, seed, fbm);
                const float n2 = procedural::fbm3(Vec3{along, 17.0f, key}, seed, fbm);
                p += u * (amp * n1) + v * (amp * n2);
            }
            points.push_back(p);
        }
        return points;
    };

    BeamState bs;
    bs.id = n.id;
    bs.width = param_float(n, "width", t1);
    bs.color = param_color(n, "color", t1);
    bs.emissive = param_float(n, "emissive", t1);
    parse_blend_mode(param_string(n, "blend"), bs.blend);
    bs.material_id = cn.material_id;
    const float pulse_speed = param_float(n, "pulse_speed");
    bs.pulse_phase = pulse_speed > 0.0f ? fract(static_cast<float>(t1) * pulse_speed) : -1.0f;
    const float pulse_frequency = param_float(n, "pulse_frequency");
    if (pulse_frequency > 0.0f)
        bs.emissive *= 0.75f + 0.25f * std::sin(kTwoPi * pulse_frequency * static_cast<float>(t1));

    const std::vector<Vec3> main = polyline(origin, target, segments, amplitude);
    bs.polylines.push_back(main);
    const int branching = param_int(n, "branching");
    if (branching > 0 && segments > 1) {
        const float branch_probability = param_float(n, "branch_probability");
        const float branch_length = param_float(n, "branch_length");
        const Vec3 span = target - origin;
        const float span_length = length(span);
        const Vec3 forward = span_length > kEpsilon ? span / span_length : Vec3{0, 1, 0};
        Pcg32 rng(derive_seed(cn.seed, static_cast<uint64_t>(static_cast<int64_t>(jitter_key)) + 1ULL));
        for (int i = 0; i < branching; ++i) {
            const int index = rng.range_int(1, segments - 1);
            const bool spawn = rng.chance(branch_probability);
            const Vec3 axis = rng.unit_vector();
            const float angle = deg_to_rad(rng.range(25.0f, 60.0f));
            if (!spawn) continue;
            const Vec3 start = main[static_cast<size_t>(index)];
            const Vec3 direction = rotate_around(forward, axis, angle);
            bs.polylines.push_back(polyline(start, start + direction * (branch_length * span_length), 4, amplitude * 0.6f));
        }
    }
    state_.beams.push_back(std::move(bs));
}

void CpuRuntime::emit_mesh(const CompiledNode& cn, const Node& n, double t1) {
    if (!param_bool(n, "visible")) return;
    MeshInstanceState mi;
    mi.id = n.id;
    mi.mesh_id = cn.mesh_id;
    mi.material_id = cn.material_id;
    mi.transform = compiled_.effect.world_transform(n, t1);
    mi.color = param_color(n, "color", t1);
    mi.emissive = param_float(n, "emissive", t1);
    mi.visible = true;
    state_.meshes.push_back(std::move(mi));
}

void CpuRuntime::emit_decal(const CompiledNode& cn, const Node& n, double t1) {
    DecalState d;
    d.id = n.id;
    const Mat4 world = compiled_.effect.world_transform(n, t1);
    d.position = world.translation_part();
    d.normal = normalize(world.transform_vector(param_vec3(n, "projection_normal")));
    if (length_squared(d.normal) < kTiny) d.normal = Vec3::up();
    d.size = param_vec2(n, "size", t1);
    d.rotation_deg = param_vec3(n, "rotation", t1).y;
    d.color = param_color(n, "color", t1);
    float opacity = param_float(n, "opacity", t1);
    opacity *= fade_ramp(param_float(n, "fade_in"), static_cast<float>(t1 - cn.start_time));
    if (cn.end_time >= 0.0) opacity *= fade_ramp(param_float(n, "fade_out"), static_cast<float>(cn.end_time - t1));
    d.opacity = opacity;
    d.emissive = param_float(n, "emissive", t1);
    d.circle = param_string(n, "shape") != "rect";
    parse_blend_mode(param_string(n, "blend"), d.blend);
    d.material_id = cn.material_id;
    d.texture_id = cn.texture_id;
    state_.decals.push_back(std::move(d));
}

void CpuRuntime::emit_camera(const Node& n, double t1) {
    CameraDesc c;
    c.position = param_vec3(n, "position", t1);
    c.target = param_vec3(n, "target", t1);
    c.up = param_vec3(n, "up");
    c.fov_deg = param_float(n, "fov");
    c.near_plane = param_float(n, "near");
    c.far_plane = param_float(n, "far");
    c.exposure = param_float(n, "exposure");
    state_.camera = c;
}

void CpuRuntime::emit_post_effect(const CompiledNode& cn, const Node& n, double t1) {
    (void)cn;
    PostEffectState p;
    p.id = n.id;
    p.post_type = param_string(n, "post_type");
    p.intensity = param_float(n, "intensity", t1);
    p.threshold = param_float(n, "threshold");
    p.radius = param_float(n, "radius");
    p.frequency = param_float(n, "frequency");
    p.time = t1;
    state_.post_effects.push_back(std::move(p));
}

void CpuRuntime::emit_volume(const CompiledNode& cn, const Node& n, double t1) {
    VolumeState v;
    v.id = n.id;
    v.volume_type = param_string(n, "volume_type");
    const Vec3 bounds = param_vec3(n, "bounds");
    const Mat4 world = compiled_.effect.world_transform(n, t1);
    const Vec3 a = world.transform_point(bounds * -0.5f);
    const Vec3 b = world.transform_point(bounds * 0.5f);
    v.bounds_min = vmin(a, b);
    v.bounds_max = vmax(a, b);
    v.density = param_float(n, "density", t1);
    v.temperature = param_float(n, "temperature", t1);
    v.backend = cn.backend;
    state_.volumes.push_back(std::move(v));
}

// ---------------------------------------------------------------------------
// trails (docs/RUNTIME.md section 7, "trail")
// ---------------------------------------------------------------------------

namespace {

void advance_ribbon(TrailRibbon& r, Vec3 position, float dt, float lifetime, float min_distance, int max_segments) {
    for (float& a : r.age) a += dt;
    size_t drop = 0;
    while (drop < r.age.size() && r.age[drop] > lifetime) ++drop;
    if (drop > 0) {
        r.age.erase(r.age.begin(), r.age.begin() + static_cast<long>(drop));
        r.base_position.erase(r.base_position.begin(), r.base_position.begin() + static_cast<long>(drop));
        r.cumulative.erase(r.cumulative.begin(), r.cumulative.begin() + static_cast<long>(drop));
    }
    const bool lay = r.base_position.empty() || distance(position, r.base_position.back()) >= min_distance;
    if (lay) {
        const float travelled = r.base_position.empty()
                                    ? 0.0f
                                    : r.cumulative.back() + distance(position, r.base_position.back());
        r.base_position.push_back(position);
        r.age.push_back(0.0f);
        r.cumulative.push_back(travelled);
    }
    const size_t cap = static_cast<size_t>(max_segments) + 1;
    if (r.base_position.size() > cap) {
        const size_t excess = r.base_position.size() - cap;
        r.age.erase(r.age.begin(), r.age.begin() + static_cast<long>(excess));
        r.base_position.erase(r.base_position.begin(), r.base_position.begin() + static_cast<long>(excess));
        r.cumulative.erase(r.cumulative.begin(), r.cumulative.begin() + static_cast<long>(excess));
    }
}

void build_ribbon(const TrailRibbon& r, const TrailRuntime& tr, float width, Color color, float emissive, double t1,
                  std::vector<TrailVertex>& out) {
    out.clear();
    out.reserve(r.base_position.size());
    const procedural::FbmParams fbm{3, 2.0f, 0.5f, procedural::NoiseBasis::Simplex};
    const size_t n = r.base_position.size();
    for (size_t i = 0; i < n; ++i) {
        TrailVertex v;
        const float normalized_age = saturate(r.age[i] / tr.lifetime);
        v.age = r.age[i];
        v.normalized_age = normalized_age;
        v.width = width * tr.taper.eval(normalized_age);
        v.opacity = tr.opacity_over_life.eval(normalized_age);
        v.color = color;
        v.emissive = emissive;
        v.u = r.cumulative[i] + tr.uv_scroll * static_cast<float>(t1);
        v.position = r.base_position[i];
        if (tr.noise_amplitude > 0.0f && n > 1) {
            const Vec3 a = r.base_position[i > 0 ? i - 1 : i];
            const Vec3 b = r.base_position[i + 1 < n ? i + 1 : i];
            const Vec3 tangent = normalize(b - a);
            const Vec3 u = orthogonal(tangent);
            const Vec3 w = cross(tangent, u);
            const float s = r.cumulative[i] * tr.noise_frequency;
            v.position += u * (tr.noise_amplitude * procedural::fbm3(Vec3{s, 0.0f, 0.0f}, tr.seed, fbm)) +
                          w * (tr.noise_amplitude * procedural::fbm3(Vec3{s, 31.0f, 0.0f}, tr.seed, fbm));
        }
        out.push_back(v);
    }
}

}  // namespace

void CpuRuntime::update_trail(TrailRuntime& tr, double t1) {
    const size_t index = static_cast<size_t>(&tr - trails_.data());
    TrailState& ts = state_.trails[index];
    if (!active_at(*tr.cn, t1)) {
        ts.ribbons.clear();
        return;
    }
    const Node& n = *tr.node;
    const float width = param_float(n, "width", t1);
    const Color color = param_color(n, "color", t1);
    const float emissive = param_float(n, "emissive", t1);
    const float dt = static_cast<float>(dt_);

    if (tr.source_system != SIZE_MAX) {
        // One ribbon per live particle, keyed by the particle seed, capped at 512.
        const ParticleBuffer& b = state_.particles[systems_[tr.source_system].buffer];
        const size_t count = std::min<size_t>(b.count(), 512);
        for (auto& [key, ribbon] : tr.per_particle) {
            (void)key;
            ribbon.seen = false;
        }
        ts.ribbons.resize(count);
        for (size_t i = 0; i < count; ++i) {
            TrailRibbon& ribbon = tr.per_particle[b.seed[i]];
            ribbon.seen = true;
            advance_ribbon(ribbon, b.position[i], dt, tr.lifetime, tr.min_vertex_distance, tr.max_segments);
            build_ribbon(ribbon, tr, width, color, emissive, t1, ts.ribbons[i]);
        }
        for (auto it = tr.per_particle.begin(); it != tr.per_particle.end();)
            it = it->second.seen ? std::next(it) : tr.per_particle.erase(it);
        return;
    }

    if (tr.source == nullptr) {
        ts.ribbons.clear();
        return;
    }
    Vec3 position;
    if (tr.source->type == NodeType::Curve && !tr.curve_points.empty())
        position = compiled_.effect.world_transform(*tr.source, t1).transform_point(tr.curve_points.front());
    else
        position = node_world_position(*tr.source, t1);
    advance_ribbon(tr.single, position, dt, tr.lifetime, tr.min_vertex_distance, tr.max_segments);
    ts.ribbons.resize(1);
    build_ribbon(tr.single, tr, width, color, emissive, t1, ts.ribbons[0]);
}

// ---------------------------------------------------------------------------
// emitters (docs/RUNTIME.md sections 1.3 and 3)
// ---------------------------------------------------------------------------

void CpuRuntime::update_emitters(double t0, double t1) {
    for (SystemRuntime& s : systems_) s.emitter_batches.clear();

    for (size_t i = 0; i < emitters_.size(); ++i) {
        EmitterRuntime& em = emitters_[i];
        em.world = compiled_.effect.world_transform(*em.node, t1);
        em.center = em.world.translation_part();
        em.world_velocity = em.has_previous_center ? (em.center - em.previous_center) / static_cast<float>(dt_)
                                                   : Vec3::zero();
        em.previous_center = em.center;
        em.has_previous_center = true;
        em.velocity = param_float(*em.node, "velocity", t1);
        if (em.system == SIZE_MAX) continue;

        const bool inside = t1 >= em.start && (em.end < 0.0 || t1 < em.end);
        if (inside && !em.inside_window) em.accumulator = 0.0;
        em.inside_window = inside;

        uint32_t count = 0;
        if (inside) {
            const float rate = param_float(*em.node, "rate", t1);
            if (rate > 0.0f) {
                em.accumulator += static_cast<double>(rate) * dt_;
                const double whole = std::floor(em.accumulator);
                if (whole > 0.0) {
                    count += static_cast<uint32_t>(whole);
                    em.accumulator -= whole;
                }
            }
        }
        if (em.burst_count > 0) {
            for (float offset : em.burst_times) {
                const double when = em.start + static_cast<double>(offset);
                if (t0 < when && when <= t1) count += static_cast<uint32_t>(em.burst_count);
            }
        }
        if (count > 0) {
            SpawnBatch batch;
            batch.emitter = i;
            batch.count = count;
            systems_[em.system].emitter_batches.push_back(batch);
        }
    }
}

Vec3 CpuRuntime::sample_shape(EmitterRuntime& em, Pcg32& rng) const {
    switch (em.shape) {
        case Shape::Point:
            return Vec3::zero();
        case Shape::Sphere:
            return (em.surface_only ? rng.unit_vector() : rng.in_unit_sphere()) * em.radius;
        case Shape::Hemisphere: {
            Vec3 p = (em.surface_only ? rng.unit_vector() : rng.in_unit_sphere()) * em.radius;
            p.y = std::fabs(p.y);
            return p;
        }
        case Shape::Box: {
            if (!em.surface_only) {
                const float x = rng.signed_unit() * 0.5f * em.box_size.x;
                const float y = rng.signed_unit() * 0.5f * em.box_size.y;
                const float z = rng.signed_unit() * 0.5f * em.box_size.z;
                return {x, y, z};
            }
            const Vec3 h = em.box_size * 0.5f;
            const float axy = std::fabs(h.x * h.y), ayz = std::fabs(h.y * h.z), axz = std::fabs(h.x * h.z);
            const float total = 2.0f * (axy + ayz + axz);
            float pick = rng.next_float() * (total > 0.0f ? total : 1.0f);
            const float u = rng.signed_unit(), v = rng.signed_unit();
            if (pick < 2.0f * ayz) return {pick < ayz ? h.x : -h.x, u * h.y, v * h.z};
            pick -= 2.0f * ayz;
            if (pick < 2.0f * axz) return {u * h.x, pick < axz ? h.y : -h.y, v * h.z};
            pick -= 2.0f * axz;
            return {u * h.x, v * h.y, pick < axy ? h.z : -h.z};
        }
        case Shape::Disc:
        case Shape::Ring: {
            const float u = rng.next_float();
            const float angle = rng.next_float() * kTwoPi;
            float r;
            if (em.shape == Shape::Ring && em.inner_radius >= em.radius) {
                r = em.radius;
            } else {
                const float inner = std::min(em.inner_radius, em.radius);
                r = std::sqrt(lerp(inner * inner, em.radius * em.radius, u));
            }
            return {r * std::cos(angle), 0.0f, r * std::sin(angle)};
        }
        case Shape::Cone: {
            const float r = std::sqrt(rng.next_float()) * em.radius;
            const float angle = rng.next_float() * kTwoPi;
            return {r * std::cos(angle), 0.0f, r * std::sin(angle)};
        }
        case Shape::Line:
            return {rng.next_float() * em.line_length - em.line_length * 0.5f, 0.0f, 0.0f};
        case Shape::Curve: {
            const float t = rng.next_float();
            if (em.curve_points.empty()) return Vec3::zero();
            return procedural::evaluate_curve(em.curve_points, em.curve_type, em.curve_closed, t);
        }
        case Shape::Mesh:
            if (em.shape_mesh == nullptr) return Vec3::zero();
            return procedural::sample_surface(*em.shape_mesh, rng);
        case Shape::Volume: {
            const float x = rng.signed_unit(), y = rng.signed_unit(), z = rng.signed_unit();
            if (em.shape_volume == nullptr) return Vec3::zero();
            const Vec3 bounds = param_vec3(*em.shape_volume, "bounds");
            return Vec3{x, y, z} * bounds * 0.5f;
        }
    }
    return Vec3::zero();
}

void CpuRuntime::spawn_one(SystemRuntime& s, EmitterRuntime& em, const SpawnBatch& batch, uint64_t k, double t1) {
    ParticleBuffer& b = state_.particles[s.buffer];
    const uint64_t particle_seed = derive_seed(em.stream_seed, k);
    Pcg32 rng(particle_seed);

    // (1) shape position
    const Vec3 local = sample_shape(em, rng);
    Vec3 position;
    Vec3 center;
    if (em.shape == Shape::Volume && em.shape_volume != nullptr) {
        position = compiled_.effect.world_transform(*em.shape_volume, t1).transform_point(local);
        center = node_world_position(*em.shape_volume, t1);
    } else if (batch.has_position) {
        position = batch.position + em.world.transform_vector(local);
        center = batch.position;
    } else {
        position = em.world.transform_point(local);
        center = em.center;
    }
    if (b.count() >= s.max_particles) {
        ++s.dropped;
        return;
    }

    // (2) direction
    Vec3 base_direction;
    if (em.radial_direction) {
        base_direction = normalize(local);
        if (length_squared(base_direction) < kTiny) base_direction = rng.unit_vector();
    } else {
        base_direction = em.direction;
    }
    const Vec3 tangent = orthogonal(base_direction);
    const Vec3 bitangent = cross(base_direction, tangent);
    const Vec3 cone = rng.in_cone_z(em.spread_rad);
    const Vec3 local_direction = tangent * cone.x + bitangent * cone.y + base_direction * cone.z;
    Vec3 direction = normalize(em.world.transform_vector(local_direction));
    if (length_squared(direction) < kTiny) direction = base_direction;

    // (3)..(7) variances, in the order fixed by docs/RUNTIME.md section 3
    const float speed = em.velocity + rng.signed_unit() * em.velocity_variance;
    const float lifetime = std::max(0.001f, s.lifetime + rng.signed_unit() * s.lifetime_variance);
    const float size = std::max(0.0f, s.size + rng.signed_unit() * s.size_variance);
    const float rotation = deg_to_rad(s.rotation + rng.signed_unit() * s.rotation_variance);
    const float angular = deg_to_rad(s.angular_velocity + rng.signed_unit() * s.angular_velocity_variance);

    Vec3 velocity = direction * speed;
    if (em.radial_velocity != 0.0f) {
        const Vec3 radial = position - center;
        if (length_squared(radial) > kTiny) velocity += normalize(radial) * em.radial_velocity;
    }
    if (em.inherit_velocity != 0.0f) velocity += em.world_velocity * em.inherit_velocity;
    velocity += batch.extra_velocity;

    b.position.push_back(position);
    b.previous_position.push_back(position);
    b.velocity.push_back(velocity);
    b.acceleration.push_back(Vec3::zero());
    b.age.push_back(0.0f);
    b.lifetime.push_back(lifetime);
    b.size.push_back(size);
    b.rotation.push_back(rotation);
    b.angular_velocity.push_back(angular);
    b.color.push_back(s.color);
    b.opacity.push_back(s.opacity);
    b.emissive.push_back(s.emissive);
    b.mass.push_back(s.mass);
    b.custom0.push_back(0.0f);
    b.custom1.push_back(0.0f);
    b.seed.push_back(seed32_of(particle_seed));

    s.base_size.push_back(size);
    s.base_opacity.push_back(s.opacity);
    s.base_emissive.push_back(s.emissive);
    s.base_color.push_back(s.color);
    s.spawn_position.push_back(position);
    s.seed64.push_back(particle_seed);
    s.distance_mask.push_back(0u);
    s.contact_mask.push_back(0u);
    ++s.spawned_total;
    ++em.emitted;

    for (size_t ei : s.spawn_events) fire_particle_event(events_[ei], position, velocity, particle_seed);
}

void CpuRuntime::run_spawn_batch(SystemRuntime& s, const SpawnBatch& batch, double t1) {
    EmitterRuntime& em = emitters_[batch.emitter];
    uint64_t count = batch.count;
    if (em.max_particles > 0) {
        const uint64_t cap = static_cast<uint64_t>(em.max_particles);
        count = em.emitted >= cap ? 0 : std::min<uint64_t>(count, cap - em.emitted);
    }
    for (uint64_t i = 0; i < count; ++i) {
        const uint64_t k = em.spawn_index++;
        spawn_one(s, em, batch, k, t1);
    }
}

// ---------------------------------------------------------------------------
// forces (docs/RUNTIME.md section 4)
// ---------------------------------------------------------------------------

void CpuRuntime::accumulate_forces(SystemRuntime& s, ParticleBuffer& b, double t1) {
    const size_t n = b.count();
    for (size_t i = 0; i < n; ++i) b.acceleration[i] = Vec3::zero();
    if (n == 0) return;

    for (size_t fi : s.forces) {
        const ForceRuntime& f = forces_[fi];
        if (!f.active) continue;
        switch (f.kind) {
            case ForceKind::Gravity:
            case ForceKind::Directional: {
                const Vec3 a = f.dir_scaled;
                for (size_t i = 0; i < n; ++i) b.acceleration[i] += a;
                break;
            }
            case ForceKind::Wind: {
                const Vec3 target = f.dir_scaled;
                for (size_t i = 0; i < n; ++i) b.acceleration[i] += (target - b.velocity[i]) / b.mass[i];
                break;
            }
            case ForceKind::Drag: {
                for (size_t i = 0; i < n; ++i) b.acceleration[i] -= b.velocity[i] * f.strength;
                break;
            }
            case ForceKind::Buoyancy: {
                const Vec3 a{0.0f, f.strength * f.temperature, 0.0f};
                for (size_t i = 0; i < n; ++i) b.acceleration[i] += a;
                break;
            }
            case ForceKind::Radial:
            case ForceKind::Repulsor:
            case ForceKind::Attractor: {
                const float sign_of = f.kind == ForceKind::Attractor ? -1.0f : 1.0f;
                for (size_t i = 0; i < n; ++i) {
                    const Vec3 r = b.position[i] - f.position;
                    const float d = length(r);
                    if (d <= kEpsilon) continue;
                    b.acceleration[i] += (r / d) * (sign_of * f.strength * falloff_of(f.falloff, f.radius, d));
                }
                break;
            }
            case ForceKind::Vortex: {
                const Vec3 axis = normalize(f.direction);
                for (size_t i = 0; i < n; ++i) {
                    const Vec3 r = b.position[i] - f.position;
                    const Vec3 rp = r - axis * dot(r, axis);
                    const float d = length(rp);
                    if (d <= kEpsilon) continue;
                    const Vec3 tangential = cross(axis, rp / d);
                    b.acceleration[i] += normalize(tangential) * (f.strength * falloff_of(f.falloff, f.radius, d));
                }
                break;
            }
            case ForceKind::Turbulence: {
                const procedural::NoiseNodeParams params = f.noise_params();
                const float when = static_cast<float>(t1);
                for (size_t i = 0; i < n; ++i)
                    b.acceleration[i] +=
                        procedural::evaluate_noise_vector(params, b.position[i], when, f.noise_seed) * f.strength;
                break;
            }
            case ForceKind::CurlNoise: {
                if (f.noise_type != "curl") {
                    const procedural::NoiseNodeParams params = f.noise_params();
                    const float when = static_cast<float>(t1);
                    for (size_t i = 0; i < n; ++i)
                        b.acceleration[i] +=
                            procedural::evaluate_noise_vector(params, b.position[i], when, f.noise_seed) * f.strength;
                    break;
                }
                const procedural::FbmParams fbm{f.noise.octaves, 2.0f, 0.5f, procedural::NoiseBasis::Simplex};
                const float when = static_cast<float>(t1) * f.noise.speed;
                const float frequency = f.noise.frequency;
                for (size_t i = 0; i < n; ++i)
                    b.acceleration[i] +=
                        procedural::curl4(b.position[i] * frequency, when, f.noise_seed, fbm) * f.strength;
                break;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// collision (docs/RUNTIME.md section 5)
// ---------------------------------------------------------------------------

namespace {

// Signed distance to a box with `half` extents plus the outward normal.
float box_distance(Vec3 p, Vec3 half, Vec3& normal) {
    const Vec3 q = vabs(p) - half;
    const Vec3 outside = vmax(q, Vec3::zero());
    const float outside_length = length(outside);
    const float inside = std::min(std::max(q.x, std::max(q.y, q.z)), 0.0f);
    if (outside_length > 0.0f) {
        normal = normalize(Vec3{outside.x * sign(p.x), outside.y * sign(p.y), outside.z * sign(p.z)});
    } else {
        int axis = 0;
        if (q.y > q.x) axis = 1;
        if (q.z > q[axis]) axis = 2;
        normal = Vec3::zero();
        normal[axis] = sign(p[axis]) != 0.0f ? sign(p[axis]) : 1.0f;
    }
    if (length_squared(normal) < kTiny) normal = Vec3::up();
    return outside_length + inside;
}

}  // namespace

void CpuRuntime::collide(SystemRuntime& s, ParticleBuffer& b, std::vector<uint8_t>& dead, size_t& dead_count) {
    if (s.colliders.empty()) return;
    const size_t n = b.count();
    for (size_t slot = 0; slot < s.colliders.size(); ++slot) {
        const ColliderRuntime& c = colliders_[s.colliders[slot]];
        const bool kill = c.kill || s.kill_on_collision;
        // The material combines both nodes (docs/RUNTIME.md section 5), so
        // either side can zero the response.
        const float bounce = std::sqrt(std::max(0.0f, s.bounce * c.bounce));
        const float friction = std::sqrt(std::max(0.0f, s.friction * c.friction));
        // Contact bit for this collider; slots past 32 are not tracked and
        // therefore always report (no system in the vocabulary needs that many).
        const uint32_t contact_bit = slot < 32 ? (1u << static_cast<uint32_t>(slot)) : 0u;
        for (size_t i = 0; i < n; ++i) {
            if (dead[i]) continue;
            const float collision_radius = s.collision_radius > 0.0f ? s.collision_radius : b.size[i] * 0.5f;
            const Vec3 p = b.position[i];
            float sd = 0.0f;
            Vec3 normal{0, 1, 0};
            switch (c.kind) {
                case ColliderKind::Plane:
                    normal = c.normal;
                    sd = dot(p - c.position, normal) - collision_radius;
                    break;
                case ColliderKind::Sphere: {
                    const Vec3 r = p - c.position;
                    const float d = length(r);
                    normal = d > kEpsilon ? r / d : Vec3::up();
                    sd = d - c.radius - collision_radius;
                    break;
                }
                case ColliderKind::Box: {
                    const Vec3 local = c.inverse_world.transform_point(p);
                    Vec3 local_normal;
                    sd = box_distance(local, c.half_size, local_normal) - collision_radius;
                    normal = normalize(c.world.transform_vector(local_normal));
                    if (length_squared(normal) < kTiny) normal = Vec3::up();
                    break;
                }
                case ColliderKind::Capsule: {
                    const Vec3 local = c.inverse_world.transform_point(p);
                    const float half = c.height * 0.5f;
                    const float y = clamp(local.y, -half, half);
                    const Vec3 closest{0.0f, y, 0.0f};
                    const Vec3 r = local - closest;
                    const float d = length(r);
                    const Vec3 local_normal = d > kEpsilon ? r / d : Vec3::up();
                    sd = d - c.radius - collision_radius;
                    normal = normalize(c.world.transform_vector(local_normal));
                    if (length_squared(normal) < kTiny) normal = Vec3::up();
                    break;
                }
                case ColliderKind::Unsupported:
                    continue;
            }
            if (sd >= 0.0f) {
                s.contact_mask[i] &= ~contact_bit;
                continue;
            }

            // The push-out and the velocity response happen on every
            // overlapping step, so particles never sink through a surface.
            b.position[i] = p + normal * (-sd);
            const float vn = dot(b.velocity[i], normal);
            if (vn < 0.0f)
                b.velocity[i] = (b.velocity[i] - normal * vn) * (1.0f - friction) - normal * (vn * bounce);

            // A resting particle re-penetrates by the velocity it picks up
            // from acceleration within the step, so the impact speed is
            // measured before this step's integration: a particle that is
            // already in contact only reports again when it is driven into
            // the surface at more than kImpactSpeed.
            const bool was_touching = (s.contact_mask[i] & contact_bit) != 0u;
            s.contact_mask[i] |= contact_bit;
            const float approach = -dot(arrival_velocity_[i], normal);
            if (was_touching && approach <= kImpactSpeed) continue;

            ++s.collisions;
            for (size_t ei : s.collision_events)
                fire_particle_event(events_[ei], b.position[i], b.velocity[i], s.seed64[i]);
            if (kill) {
                for (size_t ei : s.death_events)
                    fire_particle_event(events_[ei], b.position[i], b.velocity[i], s.seed64[i]);
                dead[i] = 1;
                ++dead_count;
                ++s.died_total;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// over-life modulation (docs/RUNTIME.md section 6)
// ---------------------------------------------------------------------------

void CpuRuntime::modulate(SystemRuntime& s, ParticleBuffer& b) {
    const size_t n = b.count();
    for (size_t i = 0; i < n; ++i) {
        const float u = saturate(b.lifetime[i] > 0.0f ? b.age[i] / b.lifetime[i] : 1.0f);
        b.size[i] = s.base_size[i] * s.size_over_life.eval(u);
        const Color modulation = s.color_over_life.eval(u);
        const Color base = s.base_color[i];
        b.color[i] = Color{base.r * modulation.r, base.g * modulation.g, base.b * modulation.b, base.a};
        b.opacity[i] = s.base_opacity[i] * s.opacity_over_life.eval(u);
        b.emissive[i] = s.base_emissive[i] * s.emissive_over_life.eval(u);
        b.custom0[i] = u;
        b.custom1[i] = 0.0f;
    }
}

void CpuRuntime::remove_dead(SystemRuntime& s, const std::vector<uint8_t>& dead) {
    ParticleBuffer& b = state_.particles[s.buffer];
    const size_t n = b.count();
    size_t w = 0;
    for (size_t r = 0; r < n; ++r) {
        if (dead[r]) continue;
        if (w != r) {
            b.position[w] = b.position[r];
            b.previous_position[w] = b.previous_position[r];
            b.velocity[w] = b.velocity[r];
            b.acceleration[w] = b.acceleration[r];
            b.age[w] = b.age[r];
            b.lifetime[w] = b.lifetime[r];
            b.size[w] = b.size[r];
            b.rotation[w] = b.rotation[r];
            b.angular_velocity[w] = b.angular_velocity[r];
            b.color[w] = b.color[r];
            b.opacity[w] = b.opacity[r];
            b.emissive[w] = b.emissive[r];
            b.mass[w] = b.mass[r];
            b.custom0[w] = b.custom0[r];
            b.custom1[w] = b.custom1[r];
            b.seed[w] = b.seed[r];
            s.base_size[w] = s.base_size[r];
            s.base_opacity[w] = s.base_opacity[r];
            s.base_emissive[w] = s.base_emissive[r];
            s.base_color[w] = s.base_color[r];
            s.spawn_position[w] = s.spawn_position[r];
            s.seed64[w] = s.seed64[r];
            s.distance_mask[w] = s.distance_mask[r];
            s.contact_mask[w] = s.contact_mask[r];
        }
        ++w;
    }
    b.position.resize(w);
    b.previous_position.resize(w);
    b.velocity.resize(w);
    b.acceleration.resize(w);
    b.age.resize(w);
    b.lifetime.resize(w);
    b.size.resize(w);
    b.rotation.resize(w);
    b.angular_velocity.resize(w);
    b.color.resize(w);
    b.opacity.resize(w);
    b.emissive.resize(w);
    b.mass.resize(w);
    b.custom0.resize(w);
    b.custom1.resize(w);
    b.seed.resize(w);
    s.base_size.resize(w);
    s.base_opacity.resize(w);
    s.base_emissive.resize(w);
    s.base_color.resize(w);
    s.spawn_position.resize(w);
    s.seed64.resize(w);
    s.distance_mask.resize(w);
    s.contact_mask.resize(w);
}

// ---------------------------------------------------------------------------
// events (docs/RUNTIME.md section 8)
// ---------------------------------------------------------------------------

void CpuRuntime::queue_event_bursts(EventRuntime& ev, Vec3 position, Vec3 velocity, bool has_position) {
    ++ev.triggers;
    ++events_fired_;
    for (size_t ti : ev.targets) {
        EmitterRuntime& target = emitters_[ti];
        if (target.system == SIZE_MAX) continue;
        SpawnBatch batch;
        batch.emitter = ti;
        const int count = ev.burst_count > 0 ? ev.burst_count : (target.burst_count > 0 ? target.burst_count : 1);
        batch.count = static_cast<uint32_t>(std::max(0, count));
        batch.has_position = ev.inherit_position && has_position;
        batch.position = position;
        batch.extra_velocity = velocity * ev.inherit_velocity;
        if (batch.count > 0) systems_[target.system].event_batches.push_back(batch);
    }
}

void CpuRuntime::fire_particle_event(EventRuntime& ev, Vec3 position, Vec3 velocity, uint64_t particle_seed) {
    if (ev.max_triggers > 0 && ev.triggers >= static_cast<size_t>(ev.max_triggers)) return;
    if (ev.probability < 1.0f) {
        // The particle's own stream, advanced past the spawn draws by one
        // derivation (see the note in the runtime report).
        Pcg32 rng(derive_seed(particle_seed, ev.seed));
        if (!rng.chance(ev.probability)) return;
    }
    queue_event_bursts(ev, position, velocity, true);
}

void CpuRuntime::update_time_events(double t0, double t1) {
    for (EventRuntime& ev : events_) {
        if (ev.trigger != Trigger::OnTime && ev.trigger != Trigger::OnPeak) continue;
        if (ev.fired) continue;
        if (!(t0 < ev.when && ev.when <= t1)) continue;
        ev.fired = true;
        if (ev.max_triggers > 0 && ev.triggers >= static_cast<size_t>(ev.max_triggers)) continue;
        if (ev.probability < 1.0f) {
            Pcg32 rng(derive_seed(ev.seed, frame_));
            if (!rng.chance(ev.probability)) continue;
        }
        queue_event_bursts(ev, Vec3::zero(), Vec3::zero(), false);
    }
}

// ---------------------------------------------------------------------------
// particle systems (docs/RUNTIME.md section 1.4)
// ---------------------------------------------------------------------------

void CpuRuntime::update_systems(double t1) {
    // Per-step force and collider placement, shared by every system.
    for (ForceRuntime& f : forces_) {
        f.active = t1 >= f.start && (f.end < 0.0 || t1 < f.end);
        if (!f.active) continue;
        f.strength = param_float(*f.node, "strength", t1);
        f.position = param_vec3(*f.node, "position", t1);
        f.dir_scaled = normalize(f.direction) * f.strength;
    }
    for (ColliderRuntime& c : colliders_) {
        c.world = compiled_.effect.world_transform(*c.node, t1);
        c.inverse_world = c.world.inverse();
        c.position = c.world.translation_part();
        c.normal = normalize(c.world.transform_vector(c.local_normal));
        if (length_squared(c.normal) < kTiny) c.normal = Vec3::up();
    }
    for (SystemRuntime& s : systems_) update_system(s, t1);
}

void CpuRuntime::update_system(SystemRuntime& s, double t1) {
    ParticleBuffer& b = state_.particles[s.buffer];
    const float dt = static_cast<float>(dt_);

    // (a) age, kill, fire on_death, compact
    {
        const size_t n = b.count();
        dead_scratch_.assign(n, 0u);
        size_t dead_count = 0;
        for (size_t i = 0; i < n; ++i) {
            b.age[i] += dt;
            if (b.age[i] < b.lifetime[i]) continue;
            dead_scratch_[i] = 1;
            ++dead_count;
            ++s.died_total;
            for (size_t ei : s.death_events)
                fire_particle_event(events_[ei], b.position[i], b.velocity[i], s.seed64[i]);
        }
        if (dead_count > 0) remove_dead(s, dead_scratch_);
    }

    // (b) spawn: emitter requests first, then event bursts, in firing order.
    // Spawning fires on_spawn events, which may queue new bursts on this very
    // system; those are left in the queue and spawned on the next step.
    for (size_t i = 0; i < s.emitter_batches.size(); ++i) {
        const SpawnBatch batch = s.emitter_batches[i];
        run_spawn_batch(s, batch, t1);
    }
    s.emitter_batches.clear();
    const size_t pending = s.event_batches.size();
    for (size_t i = 0; i < pending; ++i) {
        const SpawnBatch batch = s.event_batches[i];
        run_spawn_batch(s, batch, t1);
    }
    s.event_batches.erase(s.event_batches.begin(), s.event_batches.begin() + static_cast<long>(pending));

    const size_t n = b.count();
    if (n > 0) {
        // (c) forces
        accumulate_forces(s, b, t1);

        // (d) semi-implicit Euler integration
        const float damping = std::max(0.0f, 1.0f - s.drag * dt);
        if (!s.colliders.empty()) arrival_velocity_ = b.velocity;  // impact speed for section 5
        for (size_t i = 0; i < n; ++i) {
            b.velocity[i] += b.acceleration[i] * dt;
            b.velocity[i] *= damping;
            b.previous_position[i] = b.position[i];
            b.position[i] += b.velocity[i] * dt;
            b.rotation[i] += b.angular_velocity[i] * dt;
        }

        // (e) collision
        dead_scratch_.assign(n, 0u);
        size_t dead_count = 0;
        collide(s, b, dead_scratch_, dead_count);

        // on_distance, once per particle per event
        for (size_t ei : s.distance_events) {
            EventRuntime& ev = events_[ei];
            if (ev.distance_bit < 0) continue;
            const uint32_t bit = 1u << static_cast<uint32_t>(ev.distance_bit);
            const float threshold = ev.distance * ev.distance;
            for (size_t i = 0; i < n; ++i) {
                if (dead_scratch_[i] || (s.distance_mask[i] & bit) != 0u) continue;
                if (length_squared(b.position[i] - s.spawn_position[i]) < threshold) continue;
                s.distance_mask[i] |= bit;
                fire_particle_event(ev, b.position[i], b.velocity[i], s.seed64[i]);
            }
        }
        if (dead_count > 0) remove_dead(s, dead_scratch_);
    }

    // (f) over-life modulation
    modulate(s, b);
}

// ---------------------------------------------------------------------------
// statistics (docs/RUNTIME.md section 9)
// ---------------------------------------------------------------------------

Statistics CpuRuntime::statistics() const {
    Statistics stats;
    stats.time = time_;
    stats.frame = frame_;
    stats.systems.reserve(systems_.size());
    for (const SystemRuntime& s : systems_) {
        SystemStatistics ss;
        ss.system_id = s.node->id;
        ss.alive = state_.particles[s.buffer].count();
        ss.spawned_total = s.spawned_total;
        ss.died_total = s.died_total;
        ss.peak_alive = s.peak_alive;
        ss.capacity = s.max_particles;
        ss.dropped = s.dropped;
        ss.collisions = s.collisions;
        stats.total_alive += ss.alive;
        stats.total_spawned += ss.spawned_total;
        stats.systems.push_back(std::move(ss));
    }
    stats.events_fired = events_fired_;
    stats.last_step_ms = last_step_ms_;
    stats.total_step_ms = total_step_ms_;
    return stats;
}

}  // namespace

std::unique_ptr<IRuntime> create_cpu_runtime(const compiler::CompiledEffect& compiled) {
    if (!compiled.ok())
        throw Error("E100", "cannot create a runtime for an effect that failed to compile: " +
                                compiled.diagnostics.summary());
    return std::make_unique<CpuRuntime>(compiled);
}

}  // namespace aether::sim
