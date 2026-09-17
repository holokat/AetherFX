#pragma once
// The only thing a renderer sees. Filled by a runtime every step.
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "aether/core/enums.hpp"
#include "aether/core/math.hpp"
#include "aether/core/render_types.hpp"

namespace aether {

// Structure-of-arrays particle storage. All arrays have length count().
struct ParticleBuffer {
    std::string system_id;   // particle_system node id
    RenderMode render_mode = RenderMode::Billboard;
    BlendMode blend = BlendMode::Additive;
    std::string material_id;  // ResourceSet key or ""
    std::string sprite_id;    // texture resource key or "" (renderer uses default soft sprite)
    std::string mesh_id;      // for RenderMode::Mesh
    float velocity_stretch = 0.0f;
    bool align_to_velocity = false;
    float soft_particle_distance = 0.1f;
    bool sort = false;
    int sprite_columns = 1;
    int sprite_rows = 1;
    float sprite_fps = 0.0f;

    std::vector<Vec3> position;
    std::vector<Vec3> previous_position;
    std::vector<Vec3> velocity;
    std::vector<Vec3> acceleration;
    std::vector<float> age;
    std::vector<float> lifetime;
    std::vector<float> size;              // world-space diameter
    std::vector<float> rotation;          // radians (roll about view axis / mesh yaw)
    std::vector<float> angular_velocity;  // rad/s
    std::vector<Color> color;             // linear rgb, alpha unused (see opacity)
    std::vector<float> opacity;
    std::vector<float> emissive;          // HDR multiplier
    std::vector<float> mass;
    std::vector<float> custom0;
    std::vector<float> custom1;
    std::vector<uint32_t> seed;
    // Mesh particles (RenderMode::Mesh). Billboard systems leave these at their
    // defaults, which reproduce the pre-orientation behaviour exactly.
    std::vector<Vec4> orientation;  // rotation quaternion (x,y,z,w); identity (0,0,0,1) when unused
    std::vector<Vec3> scale3;       // per-axis multipliers on `size`; (1,1,1) when unused
    std::vector<uint32_t> variant;  // baked mesh variant index ("<mesh_id>" for 0, "<mesh_id>#k" otherwise)

    size_t count() const { return position.size(); }
    void clear();
    void reserve(size_t n);
    void resize(size_t n);
    void swap_remove(size_t i);  // O(1) removal; order changes (runtime must keep spawn-order semantics itself)
    void push_default();
};

struct LightState {
    std::string id;
    LightType type = LightType::Point;
    Vec3 position;
    Vec3 direction{0, -1, 0};
    Color color{1, 1, 1, 1};
    float intensity = 10.0f;
    float radius = 5.0f;
    float cone_angle_deg = 45.0f;
    Vec2 area_size{1, 1};
    bool cast_shadows = false;
};

struct BeamState {
    std::string id;
    std::vector<std::vector<Vec3>> polylines;  // [0] main beam, others are branches
    float width = 0.05f;
    Color color{1, 1, 1, 1};
    float emissive = 4.0f;
    BlendMode blend = BlendMode::Additive;
    std::string material_id;
    float pulse_phase = 0.0f;   // [0,1) along the beam, negative = no pulse
};

struct TrailVertex {
    Vec3 position;
    float width = 0.1f;
    float age = 0.0f;       // seconds since this vertex was laid
    float normalized_age = 0.0f;
    float u = 0.0f;         // distance-based UV coordinate
    Color color{1, 1, 1, 1};
    float opacity = 1.0f;
    float emissive = 0.0f;
};
struct TrailState {
    std::string id;
    std::vector<std::vector<TrailVertex>> ribbons;  // one per tracked source (oldest vertex first)
    BlendMode blend = BlendMode::Additive;
    std::string material_id;
    float twist_deg = 0.0f;
};

struct DecalState {
    std::string id;
    Vec3 position;
    Vec3 normal{0, 1, 0};
    Vec2 size{1, 1};
    float rotation_deg = 0.0f;
    Color color{1, 1, 1, 1};
    float opacity = 1.0f;
    float emissive = 0.0f;
    bool circle = true;
    BlendMode blend = BlendMode::Alpha;
    std::string material_id;
    std::string texture_id;
};

struct MeshInstanceState {
    std::string id;
    std::string mesh_id;
    std::string material_id;
    Mat4 transform;
    Color color{1, 1, 1, 1};
    float emissive = 0.0f;
    bool visible = true;
};

// A bounded volume. `mode` picks which half of this struct matters:
//   "procedural" - a raymarched closed-form density field (docs/VOLUMES.md); every
//                  field below is resolved and a renderer can draw it on its own.
//   "simulation" - the V1 fluid stub: id, bounds, density and temperature only.
struct VolumeState {
    std::string id;
    std::string volume_type;
    Vec3 bounds_min, bounds_max;  // world-space AABB of the shape
    float density = 0.0f;
    float temperature = 0.0f;
    std::string backend;  // "procedural_volume" or "volume_stub"

    std::string mode = "procedural";
    std::string shape = "sphere";
    float radius = 1.5f;
    float height = 2.0f;
    float emission = 1.0f;
    Color color{0.6f, 0.3f, 1.0f, 1.0f};
    Color color_hot{1, 1, 1, 1};
    float filament_scale = 2.0f;
    float strands = 0.5f;
    float carve = 0.45f;
    float softness = 0.6f;
    int spiral_arms = 0;
    float arm_sharpness = 1.5f;
    float twist = 0.0f;   // rad per metre of height
    float spin = 0.0f;    // revolutions per second about the local up axis
    float climb = 0.0f;   // m/s of upward noise advection
    float scatter = 0.3f;
    int march_steps = 48;
    Mat4 transform;       // local -> world (the node's world transform)
    uint32_t seed = 0;    // the node's derived stream seed, truncated
    float time = 0.0f;    // effect time the field is evaluated at
};

// Shape constants shared by every volume backend, as fractions of `height`:
// a disc is a slab this thick, a ring's tube has this radius.
inline constexpr float kVolumeDiscThickness = 0.25f;
inline constexpr float kVolumeRingThickness = 0.25f;

// Half-extents, in the volume's local space, of the box that tightly contains a
// procedural `shape`. The density function is exactly zero outside it, so this is
// both the runtime's AABB source and the box every raymarching backend marches
// through. Every backend (CPU renderer, three.js viewer, engine bridges) must agree
// with this, so it lives here rather than in one renderer.
Vec3 volume_shape_extent(std::string_view shape, float radius, float height);

struct FrameState {
    double time = 0.0;
    uint64_t frame_index = 0;
    std::vector<ParticleBuffer> particles;
    std::vector<LightState> lights;
    std::vector<BeamState> beams;
    std::vector<TrailState> trails;
    std::vector<DecalState> decals;
    std::vector<MeshInstanceState> meshes;
    std::vector<VolumeState> volumes;
    std::vector<PostEffectState> post_effects;
    std::optional<CameraDesc> camera;  // from the effect's camera node when present

    size_t total_particles() const;
    const ParticleBuffer* find_particles(std::string_view system_id) const;
    // Deterministic content hash over all state (float bits), for determinism tests.
    uint64_t hash() const;
};

}  // namespace aether
