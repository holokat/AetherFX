#include "aether/core/frame_state.hpp"

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
}
void ParticleBuffer::reserve(size_t n) {
    position.reserve(n); previous_position.reserve(n); velocity.reserve(n); acceleration.reserve(n);
    age.reserve(n); lifetime.reserve(n); size.reserve(n); rotation.reserve(n); angular_velocity.reserve(n);
    color.reserve(n); opacity.reserve(n); emissive.reserve(n); mass.reserve(n); custom0.reserve(n); custom1.reserve(n); seed.reserve(n);
}
void ParticleBuffer::resize(size_t n) {
    position.resize(n); previous_position.resize(n); velocity.resize(n); acceleration.resize(n);
    age.resize(n, 0.0f); lifetime.resize(n, 1.0f); size.resize(n, 0.1f); rotation.resize(n, 0.0f);
    angular_velocity.resize(n, 0.0f); color.resize(n, Color::white()); opacity.resize(n, 1.0f);
    emissive.resize(n, 0.0f); mass.resize(n, 1.0f); custom0.resize(n, 0.0f); custom1.resize(n, 0.0f); seed.resize(n, 0u);
}
void ParticleBuffer::swap_remove(size_t i) {
    erase_swap(position, i); erase_swap(previous_position, i); erase_swap(velocity, i); erase_swap(acceleration, i);
    erase_swap(age, i); erase_swap(lifetime, i); erase_swap(size, i); erase_swap(rotation, i);
    erase_swap(angular_velocity, i); erase_swap(color, i); erase_swap(opacity, i); erase_swap(emissive, i);
    erase_swap(mass, i); erase_swap(custom0, i); erase_swap(custom1, i); erase_swap(seed, i);
}
void ParticleBuffer::push_default() { resize(count() + 1); }

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
    }
    for (const auto& l : lights) { hash_str(h, l.id); hash_bytes(h, &l.position, sizeof l.position); hash_bytes(h, &l.color, sizeof l.color); hash_bytes(h, &l.intensity, sizeof l.intensity); hash_bytes(h, &l.radius, sizeof l.radius); }
    for (const auto& b : beams) { hash_str(h, b.id); for (const auto& pl : b.polylines) hash_vec(h, pl); hash_bytes(h, &b.width, sizeof b.width); hash_bytes(h, &b.emissive, sizeof b.emissive); }
    for (const auto& t : trails) { hash_str(h, t.id); for (const auto& r : t.ribbons) hash_vec(h, r); }
    for (const auto& d : decals) { hash_str(h, d.id); hash_bytes(h, &d.position, sizeof d.position); hash_bytes(h, &d.size, sizeof d.size); hash_bytes(h, &d.opacity, sizeof d.opacity); }
    for (const auto& m : meshes) { hash_str(h, m.id); hash_bytes(h, m.transform.m.data(), sizeof(float) * 16); hash_bytes(h, &m.emissive, sizeof m.emissive); }
    for (const auto& v : volumes) { hash_str(h, v.id); hash_bytes(h, &v.density, sizeof v.density); }
    return h;
}

}  // namespace aether
