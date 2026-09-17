#include "aether/core/frame_state.hpp"

#include <algorithm>
#include <cstring>

namespace aether {
namespace {
template <class T> void erase_swap(std::vector<T>& v, size_t i) {
    if (v.empty()) return;
    v[i] = v.back();
    v.pop_back();
}
void hash_bytes(uint64_t& h, const void* data, size_t n) {
    const unsigned char* p = static_cast<const unsigned char*>(data);
    for (size_t i = 0; i < n; ++i) { h ^= p[i]; h *= 1099511628211ULL; }
}
template <class T> void hash_vec(uint64_t& h, const std::vector<T>& v) {
    size_t n = v.size();
    hash_bytes(h, &n, sizeof n);
    if (!v.empty()) hash_bytes(h, v.data(), v.size() * sizeof(T));
}
void hash_str(uint64_t& h, const std::string& s) { hash_bytes(h, s.data(), s.size()); }
}  // namespace

void ParticleBuffer::clear() {
    position.clear(); previous_position.clear(); velocity.clear(); acceleration.clear();
    age.clear(); lifetime.clear(); size.clear(); rotation.clear(); angular_velocity.clear();
    color.clear(); opacity.clear(); emissive.clear(); mass.clear(); custom0.clear(); custom1.clear(); seed.clear();
    orientation.clear(); scale3.clear(); variant.clear();
}
void ParticleBuffer::reserve(size_t n) {
    position.reserve(n); previous_position.reserve(n); velocity.reserve(n); acceleration.reserve(n);
    age.reserve(n); lifetime.reserve(n); size.reserve(n); rotation.reserve(n); angular_velocity.reserve(n);
    color.reserve(n); opacity.reserve(n); emissive.reserve(n); mass.reserve(n); custom0.reserve(n); custom1.reserve(n); seed.reserve(n);
    orientation.reserve(n); scale3.reserve(n); variant.reserve(n);
}
void ParticleBuffer::resize(size_t n) {
    position.resize(n); previous_position.resize(n); velocity.resize(n); acceleration.resize(n);
    age.resize(n, 0.0f); lifetime.resize(n, 1.0f); size.resize(n, 0.1f); rotation.resize(n, 0.0f);
    angular_velocity.resize(n, 0.0f); color.resize(n, Color::white()); opacity.resize(n, 1.0f);
    emissive.resize(n, 0.0f); mass.resize(n, 1.0f); custom0.resize(n, 0.0f); custom1.resize(n, 0.0f); seed.resize(n, 0u);
    orientation.resize(n, Vec4{0.0f, 0.0f, 0.0f, 1.0f}); scale3.resize(n, Vec3::one()); variant.resize(n, 0u);
}
void ParticleBuffer::swap_remove(size_t i) {
    erase_swap(position, i); erase_swap(previous_position, i); erase_swap(velocity, i); erase_swap(acceleration, i);
    erase_swap(age, i); erase_swap(lifetime, i); erase_swap(size, i); erase_swap(rotation, i);
    erase_swap(angular_velocity, i); erase_swap(color, i); erase_swap(opacity, i); erase_swap(emissive, i);
    erase_swap(mass, i); erase_swap(custom0, i); erase_swap(custom1, i); erase_swap(seed, i);
    erase_swap(orientation, i); erase_swap(scale3, i); erase_swap(variant, i);
}
void ParticleBuffer::push_default() { resize(count() + 1); }

// docs/VOLUMES.md "Shapes": the half-extents each shape occupies in local space.
// `height` means different things per shape (column/cone: full height; disc: full
// thickness; ring/nebula: minor radius / vertical diameter), so the mapping is
// written out once here and mirrored by every backend.
Vec3 volume_shape_extent(std::string_view shape, float radius, float height) {
    const float r = std::max(0.0f, radius);
    const float h = std::max(0.0f, height);
    if (shape == "column" || shape == "cone") return Vec3{r, h * 0.5f, r};
    if (shape == "disc") return Vec3{r, h * kVolumeDiscThickness * 0.5f, r};
    if (shape == "ring") {
        const float minor = h * kVolumeRingThickness;
        return Vec3{r + minor, minor, r + minor};
    }
    if (shape == "nebula") return Vec3{r, h * 0.5f, r};
    return Vec3{r, r, r};  // sphere
}

size_t FrameState::total_particles() const {
    size_t n = 0;
    for (const auto& p : particles) n += p.count();
    return n;
}
const ParticleBuffer* FrameState::find_particles(std::string_view system_id) const {
    for (const auto& p : particles) if (p.system_id == system_id) return &p;
    return nullptr;
}

uint64_t FrameState::hash() const {
    uint64_t h = 1469598103934665603ULL;
    hash_bytes(h, &time, sizeof time);
    hash_bytes(h, &frame_index, sizeof frame_index);
    for (const auto& p : particles) {
        hash_str(h, p.system_id);
        hash_vec(h, p.position); hash_vec(h, p.previous_position); hash_vec(h, p.velocity); hash_vec(h, p.acceleration);
        hash_vec(h, p.age); hash_vec(h, p.lifetime); hash_vec(h, p.size); hash_vec(h, p.rotation);
        hash_vec(h, p.angular_velocity); hash_vec(h, p.color); hash_vec(h, p.opacity); hash_vec(h, p.emissive);
        hash_vec(h, p.mass); hash_vec(h, p.custom0); hash_vec(h, p.custom1); hash_vec(h, p.seed);
        hash_vec(h, p.orientation); hash_vec(h, p.scale3); hash_vec(h, p.variant);
    }
    for (const auto& l : lights) { hash_str(h, l.id); hash_bytes(h, &l.position, sizeof l.position); hash_bytes(h, &l.color, sizeof l.color); hash_bytes(h, &l.intensity, sizeof l.intensity); hash_bytes(h, &l.radius, sizeof l.radius); }
    for (const auto& b : beams) {
        hash_str(h, b.id);
        for (const auto& p : b.paths) { hash_vec(h, p.points); hash_vec(h, p.width); hash_vec(h, p.intensity); hash_bytes(h, &p.depth, sizeof p.depth); hash_bytes(h, &p.fade, sizeof p.fade); }
        for (const auto& g : b.ghosts) { hash_vec(h, g.points); hash_vec(h, g.width); hash_vec(h, g.intensity); hash_bytes(h, &g.depth, sizeof g.depth); hash_bytes(h, &g.fade, sizeof g.fade); }
        for (const auto& f : b.flares) hash_bytes(h, &f, sizeof f);
        hash_bytes(h, &b.width, sizeof b.width); hash_bytes(h, &b.emissive, sizeof b.emissive);
        hash_bytes(h, &b.core_width, sizeof b.core_width); hash_bytes(h, &b.glow_width, sizeof b.glow_width);
    }
    for (const auto& t : trails) { hash_str(h, t.id); for (const auto& r : t.ribbons) hash_vec(h, r); }
    for (const auto& d : decals) { hash_str(h, d.id); hash_bytes(h, &d.position, sizeof d.position); hash_bytes(h, &d.size, sizeof d.size); hash_bytes(h, &d.opacity, sizeof d.opacity); }
    for (const auto& m : meshes) { hash_str(h, m.id); hash_bytes(h, m.transform.m.data(), sizeof(float) * 16); hash_bytes(h, &m.emissive, sizeof m.emissive); }
    for (const auto& v : volumes) {
        hash_str(h, v.id); hash_str(h, v.mode); hash_str(h, v.shape); hash_str(h, v.backend);
        hash_bytes(h, &v.bounds_min, sizeof v.bounds_min); hash_bytes(h, &v.bounds_max, sizeof v.bounds_max);
        hash_bytes(h, &v.density, sizeof v.density); hash_bytes(h, &v.temperature, sizeof v.temperature);
        hash_bytes(h, &v.radius, sizeof v.radius); hash_bytes(h, &v.height, sizeof v.height);
        hash_bytes(h, &v.emission, sizeof v.emission);
        hash_bytes(h, &v.color, sizeof v.color); hash_bytes(h, &v.color_hot, sizeof v.color_hot);
        hash_bytes(h, &v.filament_scale, sizeof v.filament_scale); hash_bytes(h, &v.strands, sizeof v.strands);
        hash_bytes(h, &v.carve, sizeof v.carve); hash_bytes(h, &v.softness, sizeof v.softness);
        hash_bytes(h, &v.spiral_arms, sizeof v.spiral_arms); hash_bytes(h, &v.arm_sharpness, sizeof v.arm_sharpness);
        hash_bytes(h, &v.twist, sizeof v.twist); hash_bytes(h, &v.spin, sizeof v.spin);
        hash_bytes(h, &v.climb, sizeof v.climb); hash_bytes(h, &v.scatter, sizeof v.scatter);
        hash_bytes(h, &v.march_steps, sizeof v.march_steps);
        hash_bytes(h, v.transform.m.data(), sizeof(float) * 16);
        hash_bytes(h, &v.seed, sizeof v.seed); hash_bytes(h, &v.time, sizeof v.time);
    }
    return h;
}

}  // namespace aether
