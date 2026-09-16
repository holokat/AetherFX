#pragma once
// Deterministic RNG. PCG32 (O'Neill), implemented from the published
// algorithm. Every node owns a stream derived from (effect seed, node id,
// node seed); every particle gets a seed derived from its node stream and
// spawn index. Never use std::rand / std::random_device in the runtime.
#include <cstdint>
#include <optional>
#include <string_view>

#include "aether/core/math.hpp"

namespace aether {

class Pcg32 {
public:
    Pcg32() { seed(0x853c49e6748fea9bULL, 0xda3e39cb94b95bdbULL); }
    explicit Pcg32(uint64_t seed_value, uint64_t stream = 0) { seed(seed_value, stream); }

    void seed(uint64_t s, uint64_t stream) {
        state_ = 0u;
        inc_ = (stream << 1u) | 1u;
        next_u32();
        state_ += s;
        next_u32();
    }
    uint32_t next_u32() {
        uint64_t old = state_;
        state_ = old * 6364136223846793005ULL + inc_;
        uint32_t xorshifted = static_cast<uint32_t>(((old >> 18u) ^ old) >> 27u);
        uint32_t rot = static_cast<uint32_t>(old >> 59u);
        return (xorshifted >> rot) | (xorshifted << ((-rot) & 31u));
    }
    // Uniform in [0, 1).
    float next_float() { return static_cast<float>(next_u32() >> 8) * (1.0f / 16777216.0f); }
    // Uniform in [a, b).
    float range(float a, float b) { return a + (b - a) * next_float(); }
    // Uniform in [-1, 1).
    float signed_unit() { return next_float() * 2.0f - 1.0f; }
    // Uniform integer in [a, b] inclusive.
    int range_int(int a, int b) {
        if (b <= a) return a;
        uint32_t span = static_cast<uint32_t>(b - a) + 1u;
        return a + static_cast<int>(next_u32() % span);
    }
    // value +/- variance (uniform).
    float vary(float value, float variance) { return variance > 0.0f ? value + signed_unit() * variance : value; }
    bool chance(float probability) { return next_float() < probability; }
    // Uniform direction on the unit sphere.
    Vec3 unit_vector() {
        float z = signed_unit();
        float a = next_float() * kTwoPi;
        float r = std::sqrt(std::max(0.0f, 1.0f - z * z));
        return {r * std::cos(a), r * std::sin(a), z};
    }
    // Uniform point in the unit ball.
    Vec3 in_unit_sphere() {
        Vec3 d = unit_vector();
        float r = std::cbrt(next_float());
        return d * r;
    }
    // Uniform point in the unit disc (xy).
    Vec2 in_unit_disc() {
        float a = next_float() * kTwoPi;
        float r = std::sqrt(next_float());
        return {r * std::cos(a), r * std::sin(a)};
    }
    // Direction within a cone of half-angle `half_angle_rad` around +Z (uniform on the cap).
    Vec3 in_cone_z(float half_angle_rad) {
        float cos_max = std::cos(half_angle_rad);
        float z = lerp(cos_max, 1.0f, next_float());
        float a = next_float() * kTwoPi;
        float r = std::sqrt(std::max(0.0f, 1.0f - z * z));
        return {r * std::cos(a), r * std::sin(a), z};
    }
    // Standard normal (Box-Muller, consumes two draws).
    float gaussian() {
        float u1 = std::max(next_float(), 1e-7f);
        float u2 = next_float();
        return std::sqrt(-2.0f * std::log(u1)) * std::cos(kTwoPi * u2);
    }
    uint64_t state() const { return state_; }
    uint64_t inc() const { return inc_; }

private:
    uint64_t state_ = 0;
    uint64_t inc_ = 0;
};

// Seed for a node's stream. Stable across runs and platforms.
inline uint64_t derive_seed(uint32_t effect_seed, std::string_view node_id, std::optional<uint32_t> node_seed) {
    uint64_t h = hash_combine(static_cast<uint64_t>(effect_seed) * 0x9E3779B97F4A7C15ULL, fnv1a_64(node_id));
    if (node_seed) h = hash_combine(h, static_cast<uint64_t>(*node_seed) + 0x632BE59BD9B4E019ULL);
    return h;
}
// Seed for the i-th child of a stream (e.g. per particle, per burst, per branch).
inline uint64_t derive_seed(uint64_t base, uint64_t index) { return hash_combine(base, index * 0xBF58476D1CE4E5B9ULL + 1ULL); }

}  // namespace aether
