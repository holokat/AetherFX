#pragma once
// Deterministic noise. Pure functions of (position, seed); no static state.
// Used by forces (turbulence/curl), textures, beams and trails.
#include <cstdint>

#include "aether/core/math.hpp"

namespace aether::procedural {

enum class NoiseBasis { Perlin, Simplex, Worley, Cellular };

// Gradient noises return approximately [-1, 1].
float perlin2(Vec2 p, uint32_t seed);
float perlin3(Vec3 p, uint32_t seed);
float simplex2(Vec2 p, uint32_t seed);
float simplex3(Vec3 p, uint32_t seed);
float simplex4(Vec4 p, uint32_t seed);   // 4th component used for animation

// Worley / cellular: F1 distance in [0, ~1.5]; `f2` receives F2 when non-null.
float worley2(Vec2 p, uint32_t seed, float* f2 = nullptr);
float worley3(Vec3 p, uint32_t seed, float* f2 = nullptr);
// Cell id noise: constant random value in [0,1) per cell.
float cellular2(Vec2 p, uint32_t seed);
float cellular3(Vec3 p, uint32_t seed);

struct FbmParams {
    int octaves = 3;
    float lacunarity = 2.0f;
    float gain = 0.5f;
    NoiseBasis basis = NoiseBasis::Simplex;
};
// Normalized so the output stays within about [-1, 1] for any octave count.
float fbm2(Vec2 p, uint32_t seed, const FbmParams& params);
float fbm3(Vec3 p, uint32_t seed, const FbmParams& params);
float fbm4(Vec4 p, uint32_t seed, const FbmParams& params);

// Divergence-free vector field from the curl of three offset fbm potentials.
Vec3 curl3(Vec3 p, uint32_t seed, const FbmParams& params, float epsilon = 1e-3f);
// Animated variant: potential sampled in 4D at (p, time).
Vec3 curl4(Vec3 p, float time, uint32_t seed, const FbmParams& params, float epsilon = 1e-3f);

// Blue-noise-like value per integer cell (hash based, not true blue noise).
float hash_noise2(Vec2 cell, uint32_t seed);
float hash_noise3(Vec3 cell, uint32_t seed);

// Dispatch matching the `noise` node vocabulary (docs/VOCABULARY.md).
struct NoiseNodeParams {
    // perlin, simplex, worley, fbm, curl, blue_noise, cellular
    const char* noise_type = "simplex";
    float frequency = 1.0f;
    int octaves = 3;
    float lacunarity = 2.0f;
    float gain = 0.5f;
    float amplitude = 1.0f;
    float speed = 0.0f;
    Vec3 offset{0, 0, 0};
};
float evaluate_noise_scalar(const NoiseNodeParams& n, Vec3 p, float time, uint32_t seed);
Vec3 evaluate_noise_vector(const NoiseNodeParams& n, Vec3 p, float time, uint32_t seed);  // curl for "curl", gradient-ish otherwise

}  // namespace aether::procedural
