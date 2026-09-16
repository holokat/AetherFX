// Deterministic noise primitives.
//
// Every function here is a pure function of its arguments and the seed. All
// randomness comes from integer bit-mixing (lowbias32) of the integer lattice
// coordinates, so results are identical on every platform and across runs.
// No permutation tables are mutated, no floating point is used for hashing
// (no sin-based hashes), and there is no static mutable state.
#include "aether/procedural/noise.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace aether::procedural {
namespace {

// ---------------------------------------------------------------------------
// Integer hashing
// ---------------------------------------------------------------------------

// lowbias32 (Chris Wellons): a 32-bit integer bijection with excellent
// avalanche behaviour. Exact on every platform (unsigned wrap is defined).
constexpr uint32_t lowbias32(uint32_t x) {
    x ^= x >> 16;
    x *= 0x7feb352dU;
    x ^= x >> 15;
    x *= 0x846ca68bU;
    x ^= x >> 16;
    return x;
}

constexpr uint32_t u32_of(int32_t v) { return static_cast<uint32_t>(v); }

constexpr uint32_t kSaltX = 0x85ebca6bU;
constexpr uint32_t kSaltY = 0xc2b2ae35U;
constexpr uint32_t kSaltZ = 0x27d4eb2fU;
constexpr uint32_t kSaltW = 0x165667b1U;
constexpr uint32_t kSaltSeed = 0x9e3779b9U;

constexpr uint32_t hash_ints(uint32_t seed, int32_t a, int32_t b) {
    uint32_t h = lowbias32(seed * kSaltSeed + 0x632be59bU);
    h = lowbias32(h ^ (u32_of(a) * kSaltX));
    return lowbias32(h ^ (u32_of(b) * kSaltY));
}
constexpr uint32_t hash_ints(uint32_t seed, int32_t a, int32_t b, int32_t c) {
    uint32_t h = lowbias32(seed * kSaltSeed + 0x632be59bU);
    h = lowbias32(h ^ (u32_of(a) * kSaltX));
    h = lowbias32(h ^ (u32_of(b) * kSaltY));
    return lowbias32(h ^ (u32_of(c) * kSaltZ));
}
constexpr uint32_t hash_ints(uint32_t seed, int32_t a, int32_t b, int32_t c, int32_t d) {
    uint32_t h = lowbias32(seed * kSaltSeed + 0x632be59bU);
    h = lowbias32(h ^ (u32_of(a) * kSaltX));
    h = lowbias32(h ^ (u32_of(b) * kSaltY));
    h = lowbias32(h ^ (u32_of(c) * kSaltZ));
    return lowbias32(h ^ (u32_of(d) * kSaltW));
}

// Uniform float in [0,1) from 24 hash bits. Exactly representable, no rounding.
constexpr float unit_of(uint32_t h) { return static_cast<float>(h >> 8) * (1.0f / 16777216.0f); }

// Deterministic floor to int (no implicit rounding-mode dependence).
inline int ifloor(float v) { return static_cast<int>(std::floor(v)); }

// ---------------------------------------------------------------------------
// Gradient tables (constexpr, immutable)
// ---------------------------------------------------------------------------

constexpr float kR2 = 0.70710678f;  // 1/sqrt(2)
constexpr float kR3 = 0.57735027f;  // 1/sqrt(3)

// 8 unit vectors, 45 degrees apart.
constexpr float kGrad2[8][2] = {
    {1.0f, 0.0f},   {kR2, kR2},   {0.0f, 1.0f},   {-kR2, kR2},
    {-1.0f, 0.0f},  {-kR2, -kR2}, {0.0f, -1.0f},  {kR2, -kR2},
};

// Ken Perlin's 12 cube-edge gradients, normalized to unit length.
constexpr float kGrad3[12][3] = {
    {kR2, kR2, 0.0f},  {-kR2, kR2, 0.0f},  {kR2, -kR2, 0.0f},  {-kR2, -kR2, 0.0f},
    {kR2, 0.0f, kR2},  {-kR2, 0.0f, kR2},  {kR2, 0.0f, -kR2},  {-kR2, 0.0f, -kR2},
    {0.0f, kR2, kR2},  {0.0f, -kR2, kR2},  {0.0f, kR2, -kR2},  {0.0f, -kR2, -kR2},
};

// The 32 4D gradients (three +/-1 components and one zero), normalized.
constexpr float kGrad4[32][4] = {
    {0, kR3, kR3, kR3},    {0, kR3, kR3, -kR3},   {0, kR3, -kR3, kR3},   {0, kR3, -kR3, -kR3},
    {0, -kR3, kR3, kR3},   {0, -kR3, kR3, -kR3},  {0, -kR3, -kR3, kR3},  {0, -kR3, -kR3, -kR3},
    {kR3, 0, kR3, kR3},    {kR3, 0, kR3, -kR3},   {kR3, 0, -kR3, kR3},   {kR3, 0, -kR3, -kR3},
    {-kR3, 0, kR3, kR3},   {-kR3, 0, kR3, -kR3},  {-kR3, 0, -kR3, kR3},  {-kR3, 0, -kR3, -kR3},
    {kR3, kR3, 0, kR3},    {kR3, kR3, 0, -kR3},   {kR3, -kR3, 0, kR3},   {kR3, -kR3, 0, -kR3},
    {-kR3, kR3, 0, kR3},   {-kR3, kR3, 0, -kR3},  {-kR3, -kR3, 0, kR3},  {-kR3, -kR3, 0, -kR3},
    {kR3, kR3, kR3, 0},    {kR3, kR3, -kR3, 0},   {kR3, -kR3, kR3, 0},   {kR3, -kR3, -kR3, 0},
    {-kR3, kR3, kR3, 0},   {-kR3, kR3, -kR3, 0},  {-kR3, -kR3, kR3, 0},  {-kR3, -kR3, -kR3, 0},
};

inline float grad_dot2(uint32_t h, float dx, float dy) {
    const float* g = kGrad2[h & 7u];
    return g[0] * dx + g[1] * dy;
}
inline float grad_dot3(uint32_t h, float dx, float dy, float dz) {
    const float* g = kGrad3[h % 12u];
    return g[0] * dx + g[1] * dy + g[2] * dz;
}
inline float grad_dot4(uint32_t h, float dx, float dy, float dz, float dw) {
    const float* g = kGrad4[h & 31u];
    return g[0] * dx + g[1] * dy + g[2] * dz + g[3] * dw;
}

// Perlin's quintic fade.
inline float fade(float t) { return t * t * t * (t * (t * 6.0f - 15.0f) + 10.0f); }
inline float flerp(float a, float b, float t) { return a + (b - a) * t; }

// Perlin with unit gradients is bounded by sqrt(N)/2, so 2/sqrt(N) normalizes
// the theoretical range to [-1,1].
constexpr float kPerlin2Scale = 1.41421356f;
constexpr float kPerlin3Scale = 1.15470054f;
constexpr float kPerlin4Scale = 1.0f;

// Simplex kernel scales, calibrated by dense sampling with the unit gradient
// tables above so that the observed range sits just inside [-1, 1]
// (see the range tests in tests/procedural/noise_test.cpp).
constexpr float kSimplex2Scale = 99.0f;
constexpr float kSimplex3Scale = 44.0f;
constexpr float kSimplex4Scale = 45.0f;

// ---------------------------------------------------------------------------
// Worley helpers
// ---------------------------------------------------------------------------

struct WorleyResult {
    float f1 = 0.0f;
    float f2 = 0.0f;
    uint32_t cell_hash = 0u;  // hash of the cell owning the nearest feature point
};

inline Vec2 feature2(uint32_t h) {
    return {unit_of(h), unit_of(lowbias32(h ^ 0x9e3779b9U))};
}
inline Vec3 feature3(uint32_t h) {
    return {unit_of(h), unit_of(lowbias32(h ^ 0x9e3779b9U)), unit_of(lowbias32(h ^ 0x85ebca6bU))};
}
inline Vec4 feature4(uint32_t h) {
    return {unit_of(h), unit_of(lowbias32(h ^ 0x9e3779b9U)), unit_of(lowbias32(h ^ 0x85ebca6bU)),
            unit_of(lowbias32(h ^ 0xc2b2ae35U))};
}
inline float cell_value(uint32_t h) { return unit_of(lowbias32(h ^ 0x632be59bU)); }

inline void consider(float d2, uint32_t h, WorleyResult& out) {
    if (d2 < out.f1) {
        out.f2 = out.f1;
        out.f1 = d2;
        out.cell_hash = h;
    } else if (d2 < out.f2) {
        out.f2 = d2;
    }
}

WorleyResult worley_impl2(Vec2 p, uint32_t seed) {
    const int bx = ifloor(p.x), by = ifloor(p.y);
    WorleyResult r{1e30f, 1e30f, 0u};
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            const int cx = bx + dx, cy = by + dy;
            const uint32_t h = hash_ints(seed, cx, cy);
            const Vec2 f = feature2(h);
            const float ox = static_cast<float>(cx) + f.x - p.x;
            const float oy = static_cast<float>(cy) + f.y - p.y;
            consider(ox * ox + oy * oy, h, r);
        }
    }
    r.f1 = std::sqrt(r.f1);
    r.f2 = std::sqrt(r.f2);
    return r;
}

WorleyResult worley_impl3(Vec3 p, uint32_t seed) {
    const int bx = ifloor(p.x), by = ifloor(p.y), bz = ifloor(p.z);
    WorleyResult r{1e30f, 1e30f, 0u};
    for (int dz = -1; dz <= 1; ++dz) {
        for (int dy = -1; dy <= 1; ++dy) {
            for (int dx = -1; dx <= 1; ++dx) {
                const int cx = bx + dx, cy = by + dy, cz = bz + dz;
                const uint32_t h = hash_ints(seed, cx, cy, cz);
                const Vec3 f = feature3(h);
                const float ox = static_cast<float>(cx) + f.x - p.x;
                const float oy = static_cast<float>(cy) + f.y - p.y;
                const float oz = static_cast<float>(cz) + f.z - p.z;
                consider(ox * ox + oy * oy + oz * oz, h, r);
            }
        }
    }
    r.f1 = std::sqrt(r.f1);
    r.f2 = std::sqrt(r.f2);
    return r;
}

WorleyResult worley_impl4(Vec4 p, uint32_t seed) {
    const int bx = ifloor(p.x), by = ifloor(p.y), bz = ifloor(p.z), bw = ifloor(p.w);
    WorleyResult r{1e30f, 1e30f, 0u};
    for (int dw = -1; dw <= 1; ++dw) {
        for (int dz = -1; dz <= 1; ++dz) {
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    const int cx = bx + dx, cy = by + dy, cz = bz + dz, cw = bw + dw;
                    const uint32_t h = hash_ints(seed, cx, cy, cz, cw);
                    const Vec4 f = feature4(h);
                    const float ox = static_cast<float>(cx) + f.x - p.x;
                    const float oy = static_cast<float>(cy) + f.y - p.y;
                    const float oz = static_cast<float>(cz) + f.z - p.z;
                    const float ow = static_cast<float>(cw) + f.w - p.w;
                    consider(ox * ox + oy * oy + oz * oz + ow * ow, h, r);
                }
            }
        }
    }
    r.f1 = std::sqrt(r.f1);
    r.f2 = std::sqrt(r.f2);
    return r;
}

// ---------------------------------------------------------------------------
// fbm helpers
// ---------------------------------------------------------------------------

// Maps a basis onto a signed [-1,1] range so octaves can be summed uniformly.
inline float basis2(NoiseBasis b, Vec2 p, uint32_t seed) {
    switch (b) {
        case NoiseBasis::Perlin: return perlin2(p, seed);
        case NoiseBasis::Worley: return 1.0f - 2.0f * std::min(worley_impl2(p, seed).f1, 1.0f);
        case NoiseBasis::Cellular: return 2.0f * cell_value(worley_impl2(p, seed).cell_hash) - 1.0f;
        case NoiseBasis::Simplex: default: return simplex2(p, seed);
    }
}
inline float basis3(NoiseBasis b, Vec3 p, uint32_t seed) {
    switch (b) {
        case NoiseBasis::Perlin: return perlin3(p, seed);
        case NoiseBasis::Worley: return 1.0f - 2.0f * std::min(worley_impl3(p, seed).f1, 1.0f);
        case NoiseBasis::Cellular: return 2.0f * cell_value(worley_impl3(p, seed).cell_hash) - 1.0f;
        case NoiseBasis::Simplex: default: return simplex3(p, seed);
    }
}
inline float basis4(NoiseBasis b, Vec4 p, uint32_t seed) {
    switch (b) {
        case NoiseBasis::Perlin: return perlin4(p, seed);
        case NoiseBasis::Worley: return 1.0f - 2.0f * std::min(worley_impl4(p, seed).f1, 1.0f);
        case NoiseBasis::Cellular: return 2.0f * cell_value(worley_impl4(p, seed).cell_hash) - 1.0f;
        case NoiseBasis::Simplex: default: return simplex4(p, seed);
    }
}

inline int clamped_octaves(int octaves) { return clamp(octaves, 1, 12); }
inline float sane_lacunarity(float l) { return l > 0.0f ? l : 2.0f; }
inline float sane_gain(float g) { return clamp(g, 0.0f, 1.0f); }

// Offsets that decorrelate the three potential components of the curl field.
constexpr Vec3 kCurlOffsetA{0.0f, 0.0f, 0.0f};
constexpr Vec3 kCurlOffsetB{131.317f, -71.923f, 47.511f};
constexpr Vec3 kCurlOffsetC{-263.719f, 189.043f, -95.227f};

// Curl of an arbitrary vector potential by central differences. Using the same
// step for all three partials makes the discrete divergence of the result
// cancel exactly in exact arithmetic (the difference operators commute).
template <class Potential>
Vec3 curl_of(Potential&& potential, Vec3 p, float epsilon) {
    const float e = epsilon > 0.0f ? epsilon : 1e-3f;
    const float inv = 1.0f / (2.0f * e);
    const Vec3 xm = potential(Vec3{p.x - e, p.y, p.z});
    const Vec3 xp = potential(Vec3{p.x + e, p.y, p.z});
    const Vec3 ym = potential(Vec3{p.x, p.y - e, p.z});
    const Vec3 yp = potential(Vec3{p.x, p.y + e, p.z});
    const Vec3 zm = potential(Vec3{p.x, p.y, p.z - e});
    const Vec3 zp = potential(Vec3{p.x, p.y, p.z + e});
    return {((yp.z - ym.z) - (zp.y - zm.y)) * inv,
            ((zp.x - zm.x) - (xp.z - xm.z)) * inv,
            ((xp.y - xm.y) - (yp.x - ym.x)) * inv};
}

}  // namespace

// ---------------------------------------------------------------------------
// Perlin
// ---------------------------------------------------------------------------

float perlin2(Vec2 p, uint32_t seed) {
    const int xi = ifloor(p.x), yi = ifloor(p.y);
    const float fx = p.x - static_cast<float>(xi);
    const float fy = p.y - static_cast<float>(yi);
    const float u = fade(fx), v = fade(fy);
    const float n00 = grad_dot2(hash_ints(seed, xi, yi), fx, fy);
    const float n10 = grad_dot2(hash_ints(seed, xi + 1, yi), fx - 1.0f, fy);
    const float n01 = grad_dot2(hash_ints(seed, xi, yi + 1), fx, fy - 1.0f);
    const float n11 = grad_dot2(hash_ints(seed, xi + 1, yi + 1), fx - 1.0f, fy - 1.0f);
    return flerp(flerp(n00, n10, u), flerp(n01, n11, u), v) * kPerlin2Scale;
}

float perlin3(Vec3 p, uint32_t seed) {
    const int xi = ifloor(p.x), yi = ifloor(p.y), zi = ifloor(p.z);
    const float fx = p.x - static_cast<float>(xi);
    const float fy = p.y - static_cast<float>(yi);
    const float fz = p.z - static_cast<float>(zi);
    const float u = fade(fx), v = fade(fy), w = fade(fz);
    float corner[8];
    for (int i = 0; i < 8; ++i) {
        const int ox = i & 1, oy = (i >> 1) & 1, oz = (i >> 2) & 1;
        corner[i] = grad_dot3(hash_ints(seed, xi + ox, yi + oy, zi + oz), fx - static_cast<float>(ox),
                              fy - static_cast<float>(oy), fz - static_cast<float>(oz));
    }
    const float x0 = flerp(corner[0], corner[1], u);
    const float x1 = flerp(corner[2], corner[3], u);
    const float x2 = flerp(corner[4], corner[5], u);
    const float x3 = flerp(corner[6], corner[7], u);
    return flerp(flerp(x0, x1, v), flerp(x2, x3, v), w) * kPerlin3Scale;
}

float perlin4(Vec4 p, uint32_t seed) {
    const int xi = ifloor(p.x), yi = ifloor(p.y), zi = ifloor(p.z), wi = ifloor(p.w);
    const float fx = p.x - static_cast<float>(xi);
    const float fy = p.y - static_cast<float>(yi);
    const float fz = p.z - static_cast<float>(zi);
    const float fw = p.w - static_cast<float>(wi);
    const float u = fade(fx), v = fade(fy), s = fade(fz), t = fade(fw);
    float corner[16];
    for (int i = 0; i < 16; ++i) {
        const int ox = i & 1, oy = (i >> 1) & 1, oz = (i >> 2) & 1, ow = (i >> 3) & 1;
        corner[i] = grad_dot4(hash_ints(seed, xi + ox, yi + oy, zi + oz, wi + ow),
                              fx - static_cast<float>(ox), fy - static_cast<float>(oy),
                              fz - static_cast<float>(oz), fw - static_cast<float>(ow));
    }
    float lx[8];
    for (int i = 0; i < 8; ++i) lx[i] = flerp(corner[i * 2], corner[i * 2 + 1], u);
    float ly[4];
    for (int i = 0; i < 4; ++i) ly[i] = flerp(lx[i * 2], lx[i * 2 + 1], v);
    const float lz0 = flerp(ly[0], ly[1], s);
    const float lz1 = flerp(ly[2], ly[3], s);
    return flerp(lz0, lz1, t) * kPerlin4Scale;
}

// ---------------------------------------------------------------------------
// Simplex
// ---------------------------------------------------------------------------

float simplex2(Vec2 p, uint32_t seed) {
    constexpr float kF2 = 0.36602540f;  // 0.5*(sqrt(3)-1)
    constexpr float kG2 = 0.21132487f;  // (3-sqrt(3))/6
    const float skew = (p.x + p.y) * kF2;
    const int i = ifloor(p.x + skew), j = ifloor(p.y + skew);
    const float unskew = static_cast<float>(i + j) * kG2;
    const float x0 = p.x - (static_cast<float>(i) - unskew);
    const float y0 = p.y - (static_cast<float>(j) - unskew);
    const int i1 = x0 > y0 ? 1 : 0;
    const int j1 = x0 > y0 ? 0 : 1;
    const float x1 = x0 - static_cast<float>(i1) + kG2;
    const float y1 = y0 - static_cast<float>(j1) + kG2;
    const float x2 = x0 - 1.0f + 2.0f * kG2;
    const float y2 = y0 - 1.0f + 2.0f * kG2;

    float n = 0.0f;
    float t0 = 0.5f - x0 * x0 - y0 * y0;
    if (t0 > 0.0f) { t0 *= t0; n += t0 * t0 * grad_dot2(hash_ints(seed, i, j), x0, y0); }
    float t1 = 0.5f - x1 * x1 - y1 * y1;
    if (t1 > 0.0f) { t1 *= t1; n += t1 * t1 * grad_dot2(hash_ints(seed, i + i1, j + j1), x1, y1); }
    float t2 = 0.5f - x2 * x2 - y2 * y2;
    if (t2 > 0.0f) { t2 *= t2; n += t2 * t2 * grad_dot2(hash_ints(seed, i + 1, j + 1), x2, y2); }
    return kSimplex2Scale * n;
}

float simplex3(Vec3 p, uint32_t seed) {
    constexpr float kF3 = 1.0f / 3.0f;
    constexpr float kG3 = 1.0f / 6.0f;
    const float skew = (p.x + p.y + p.z) * kF3;
    const int i = ifloor(p.x + skew), j = ifloor(p.y + skew), k = ifloor(p.z + skew);
    const float unskew = static_cast<float>(i + j + k) * kG3;
    const float x0 = p.x - (static_cast<float>(i) - unskew);
    const float y0 = p.y - (static_cast<float>(j) - unskew);
    const float z0 = p.z - (static_cast<float>(k) - unskew);

    int i1, j1, k1, i2, j2, k2;
    if (x0 >= y0) {
        if (y0 >= z0)      { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 1; k2 = 0; }
        else if (x0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 0; k2 = 1; }
        else               { i1 = 0; j1 = 0; k1 = 1; i2 = 1; j2 = 0; k2 = 1; }
    } else {
        if (y0 < z0)       { i1 = 0; j1 = 0; k1 = 1; i2 = 0; j2 = 1; k2 = 1; }
        else if (x0 < z0)  { i1 = 0; j1 = 1; k1 = 0; i2 = 0; j2 = 1; k2 = 1; }
        else               { i1 = 0; j1 = 1; k1 = 0; i2 = 1; j2 = 1; k2 = 0; }
    }

    const float x1 = x0 - static_cast<float>(i1) + kG3;
    const float y1 = y0 - static_cast<float>(j1) + kG3;
    const float z1 = z0 - static_cast<float>(k1) + kG3;
    const float x2 = x0 - static_cast<float>(i2) + 2.0f * kG3;
    const float y2 = y0 - static_cast<float>(j2) + 2.0f * kG3;
    const float z2 = z0 - static_cast<float>(k2) + 2.0f * kG3;
    const float x3 = x0 - 1.0f + 3.0f * kG3;
    const float y3 = y0 - 1.0f + 3.0f * kG3;
    const float z3 = z0 - 1.0f + 3.0f * kG3;

    float n = 0.0f;
    float t0 = 0.6f - x0 * x0 - y0 * y0 - z0 * z0;
    if (t0 > 0.0f) { t0 *= t0; n += t0 * t0 * grad_dot3(hash_ints(seed, i, j, k), x0, y0, z0); }
    float t1 = 0.6f - x1 * x1 - y1 * y1 - z1 * z1;
    if (t1 > 0.0f) { t1 *= t1; n += t1 * t1 * grad_dot3(hash_ints(seed, i + i1, j + j1, k + k1), x1, y1, z1); }
    float t2 = 0.6f - x2 * x2 - y2 * y2 - z2 * z2;
    if (t2 > 0.0f) { t2 *= t2; n += t2 * t2 * grad_dot3(hash_ints(seed, i + i2, j + j2, k + k2), x2, y2, z2); }
    float t3 = 0.6f - x3 * x3 - y3 * y3 - z3 * z3;
    if (t3 > 0.0f) { t3 *= t3; n += t3 * t3 * grad_dot3(hash_ints(seed, i + 1, j + 1, k + 1), x3, y3, z3); }
    return kSimplex3Scale * n;
}

float simplex4(Vec4 p, uint32_t seed) {
    constexpr float kF4 = 0.30901699f;  // (sqrt(5)-1)/4
    constexpr float kG4 = 0.13819660f;  // (5-sqrt(5))/20
    const float skew = (p.x + p.y + p.z + p.w) * kF4;
    const int i = ifloor(p.x + skew), j = ifloor(p.y + skew);
    const int k = ifloor(p.z + skew), l = ifloor(p.w + skew);
    const float unskew = static_cast<float>(i + j + k + l) * kG4;
    const float x0 = p.x - (static_cast<float>(i) - unskew);
    const float y0 = p.y - (static_cast<float>(j) - unskew);
    const float z0 = p.z - (static_cast<float>(k) - unskew);
    const float w0 = p.w - (static_cast<float>(l) - unskew);

    // Rank the coordinates; rank r means the coordinate is the r-th largest.
    int rx = 0, ry = 0, rz = 0, rw = 0;
    if (x0 > y0) ++rx; else ++ry;
    if (x0 > z0) ++rx; else ++rz;
    if (x0 > w0) ++rx; else ++rw;
    if (y0 > z0) ++ry; else ++rz;
    if (y0 > w0) ++ry; else ++rw;
    if (z0 > w0) ++rz; else ++rw;

    const int i1 = rx >= 3 ? 1 : 0, j1 = ry >= 3 ? 1 : 0, k1 = rz >= 3 ? 1 : 0, l1 = rw >= 3 ? 1 : 0;
    const int i2 = rx >= 2 ? 1 : 0, j2 = ry >= 2 ? 1 : 0, k2 = rz >= 2 ? 1 : 0, l2 = rw >= 2 ? 1 : 0;
    const int i3 = rx >= 1 ? 1 : 0, j3 = ry >= 1 ? 1 : 0, k3 = rz >= 1 ? 1 : 0, l3 = rw >= 1 ? 1 : 0;

    const float x1 = x0 - static_cast<float>(i1) + kG4;
    const float y1 = y0 - static_cast<float>(j1) + kG4;
    const float z1 = z0 - static_cast<float>(k1) + kG4;
    const float w1 = w0 - static_cast<float>(l1) + kG4;
    const float x2 = x0 - static_cast<float>(i2) + 2.0f * kG4;
    const float y2 = y0 - static_cast<float>(j2) + 2.0f * kG4;
    const float z2 = z0 - static_cast<float>(k2) + 2.0f * kG4;
    const float w2 = w0 - static_cast<float>(l2) + 2.0f * kG4;
    const float x3 = x0 - static_cast<float>(i3) + 3.0f * kG4;
    const float y3 = y0 - static_cast<float>(j3) + 3.0f * kG4;
    const float z3 = z0 - static_cast<float>(k3) + 3.0f * kG4;
    const float w3 = w0 - static_cast<float>(l3) + 3.0f * kG4;
    const float x4 = x0 - 1.0f + 4.0f * kG4;
    const float y4 = y0 - 1.0f + 4.0f * kG4;
    const float z4 = z0 - 1.0f + 4.0f * kG4;
    const float w4 = w0 - 1.0f + 4.0f * kG4;

    float n = 0.0f;
    float t0 = 0.6f - x0 * x0 - y0 * y0 - z0 * z0 - w0 * w0;
    if (t0 > 0.0f) { t0 *= t0; n += t0 * t0 * grad_dot4(hash_ints(seed, i, j, k, l), x0, y0, z0, w0); }
    float t1 = 0.6f - x1 * x1 - y1 * y1 - z1 * z1 - w1 * w1;
    if (t1 > 0.0f) { t1 *= t1; n += t1 * t1 * grad_dot4(hash_ints(seed, i + i1, j + j1, k + k1, l + l1), x1, y1, z1, w1); }
    float t2 = 0.6f - x2 * x2 - y2 * y2 - z2 * z2 - w2 * w2;
    if (t2 > 0.0f) { t2 *= t2; n += t2 * t2 * grad_dot4(hash_ints(seed, i + i2, j + j2, k + k2, l + l2), x2, y2, z2, w2); }
    float t3 = 0.6f - x3 * x3 - y3 * y3 - z3 * z3 - w3 * w3;
    if (t3 > 0.0f) { t3 *= t3; n += t3 * t3 * grad_dot4(hash_ints(seed, i + i3, j + j3, k + k3, l + l3), x3, y3, z3, w3); }
    float t4 = 0.6f - x4 * x4 - y4 * y4 - z4 * z4 - w4 * w4;
    if (t4 > 0.0f) { t4 *= t4; n += t4 * t4 * grad_dot4(hash_ints(seed, i + 1, j + 1, k + 1, l + 1), x4, y4, z4, w4); }
    return kSimplex4Scale * n;
}

// ---------------------------------------------------------------------------
// Worley / cellular
// ---------------------------------------------------------------------------

float worley2(Vec2 p, uint32_t seed, float* f2) {
    const WorleyResult r = worley_impl2(p, seed);
    if (f2) *f2 = r.f2;
    return r.f1;
}
float worley3(Vec3 p, uint32_t seed, float* f2) {
    const WorleyResult r = worley_impl3(p, seed);
    if (f2) *f2 = r.f2;
    return r.f1;
}
float worley4(Vec4 p, uint32_t seed, float* f2) {
    const WorleyResult r = worley_impl4(p, seed);
    if (f2) *f2 = r.f2;
    return r.f1;
}

float cellular2(Vec2 p, uint32_t seed) { return cell_value(worley_impl2(p, seed).cell_hash); }
float cellular3(Vec3 p, uint32_t seed) { return cell_value(worley_impl3(p, seed).cell_hash); }
float cellular4(Vec4 p, uint32_t seed) { return cell_value(worley_impl4(p, seed).cell_hash); }

// ---------------------------------------------------------------------------
// fbm
// ---------------------------------------------------------------------------

float fbm2(Vec2 p, uint32_t seed, const FbmParams& params) {
    const int octaves = clamped_octaves(params.octaves);
    const float lacunarity = sane_lacunarity(params.lacunarity);
    const float gain = sane_gain(params.gain);
    float sum = 0.0f, norm = 0.0f, amp = 1.0f, freq = 1.0f;
    for (int o = 0; o < octaves; ++o) {
        sum += amp * basis2(params.basis, p * freq, seed + static_cast<uint32_t>(o) * 0x9e3779b9U);
        norm += amp;
        amp *= gain;
        freq *= lacunarity;
    }
    return norm > 0.0f ? sum / norm : 0.0f;
}

float fbm3(Vec3 p, uint32_t seed, const FbmParams& params) {
    const int octaves = clamped_octaves(params.octaves);
    const float lacunarity = sane_lacunarity(params.lacunarity);
    const float gain = sane_gain(params.gain);
    float sum = 0.0f, norm = 0.0f, amp = 1.0f, freq = 1.0f;
    for (int o = 0; o < octaves; ++o) {
        sum += amp * basis3(params.basis, p * freq, seed + static_cast<uint32_t>(o) * 0x9e3779b9U);
        norm += amp;
        amp *= gain;
        freq *= lacunarity;
    }
    return norm > 0.0f ? sum / norm : 0.0f;
}

float fbm4(Vec4 p, uint32_t seed, const FbmParams& params) {
    const int octaves = clamped_octaves(params.octaves);
    const float lacunarity = sane_lacunarity(params.lacunarity);
    const float gain = sane_gain(params.gain);
    float sum = 0.0f, norm = 0.0f, amp = 1.0f, freq = 1.0f;
    for (int o = 0; o < octaves; ++o) {
        sum += amp * basis4(params.basis, p * freq, seed + static_cast<uint32_t>(o) * 0x9e3779b9U);
        norm += amp;
        amp *= gain;
        freq *= lacunarity;
    }
    return norm > 0.0f ? sum / norm : 0.0f;
}

// ---------------------------------------------------------------------------
// Curl
// ---------------------------------------------------------------------------

Vec3 curl3(Vec3 p, uint32_t seed, const FbmParams& params, float epsilon) {
    const auto potential = [&](Vec3 q) {
        return Vec3{fbm3(q + kCurlOffsetA, seed, params), fbm3(q + kCurlOffsetB, seed, params),
                    fbm3(q + kCurlOffsetC, seed, params)};
    };
    return curl_of(potential, p, epsilon);
}

Vec3 curl4(Vec3 p, float time, uint32_t seed, const FbmParams& params, float epsilon) {
    const auto potential = [&](Vec3 q) {
        return Vec3{fbm4(Vec4{q + kCurlOffsetA, time}, seed, params),
                    fbm4(Vec4{q + kCurlOffsetB, time}, seed, params),
                    fbm4(Vec4{q + kCurlOffsetC, time}, seed, params)};
    };
    return curl_of(potential, p, epsilon);
}

// ---------------------------------------------------------------------------
// Hash noise
// ---------------------------------------------------------------------------

float hash_noise2(Vec2 cell, uint32_t seed) {
    return unit_of(hash_ints(seed, ifloor(cell.x), ifloor(cell.y)));
}
float hash_noise3(Vec3 cell, uint32_t seed) {
    return unit_of(hash_ints(seed, ifloor(cell.x), ifloor(cell.y), ifloor(cell.z)));
}
float hash_noise4(Vec4 cell, uint32_t seed) {
    return unit_of(hash_ints(seed, ifloor(cell.x), ifloor(cell.y), ifloor(cell.z), ifloor(cell.w)));
}

// ---------------------------------------------------------------------------
// `noise` node dispatch
// ---------------------------------------------------------------------------

namespace {

enum class NoiseKind { Perlin, Simplex, Worley, Fbm, Curl, BlueNoise, Cellular };

NoiseKind parse_kind(const char* s) {
    if (s == nullptr) return NoiseKind::Simplex;
    const std::string_view v{s};
    if (v == "perlin") return NoiseKind::Perlin;
    if (v == "worley") return NoiseKind::Worley;
    if (v == "fbm") return NoiseKind::Fbm;
    if (v == "curl") return NoiseKind::Curl;
    if (v == "blue_noise") return NoiseKind::BlueNoise;
    if (v == "cellular") return NoiseKind::Cellular;
    return NoiseKind::Simplex;
}

FbmParams fbm_params_of(const NoiseNodeParams& n) {
    FbmParams f;
    f.octaves = n.octaves;
    f.lacunarity = n.lacunarity;
    f.gain = n.gain;
    f.basis = NoiseBasis::Simplex;
    return f;
}

}  // namespace

float evaluate_noise_scalar(const NoiseNodeParams& n, Vec3 p, float time, uint32_t seed) {
    const NoiseKind kind = parse_kind(n.noise_type);
    const Vec3 q = (p + n.offset) * n.frequency;
    const float w = time * n.speed;
    const bool animated = n.speed != 0.0f;
    float v = 0.0f;
    switch (kind) {
        case NoiseKind::Perlin: v = animated ? perlin4(Vec4{q, w}, seed) : perlin3(q, seed); break;
        case NoiseKind::Simplex: v = animated ? simplex4(Vec4{q, w}, seed) : simplex3(q, seed); break;
        case NoiseKind::Fbm: {
            const FbmParams f = fbm_params_of(n);
            v = animated ? fbm4(Vec4{q, w}, seed, f) : fbm3(q, seed, f);
            break;
        }
        case NoiseKind::Worley: v = animated ? saturate(worley4(Vec4{q, w}, seed)) : saturate(worley3(q, seed)); break;
        case NoiseKind::Cellular: v = animated ? cellular4(Vec4{q, w}, seed) : cellular3(q, seed); break;
        case NoiseKind::BlueNoise:
            v = hash_noise3(Vec3{std::floor(q.x), std::floor(q.y), std::floor(q.z + w)}, seed);
            break;
        case NoiseKind::Curl: {
            const FbmParams f = fbm_params_of(n);
            v = length(animated ? curl4(q, w, seed, f) : curl3(q, seed, f));
            break;
        }
    }
    return v * n.amplitude;
}

Vec3 evaluate_noise_vector(const NoiseNodeParams& n, Vec3 p, float time, uint32_t seed) {
    const NoiseKind kind = parse_kind(n.noise_type);
    if (kind == NoiseKind::Curl) {
        const FbmParams f = fbm_params_of(n);
        const Vec3 q = (p + n.offset) * n.frequency;
        const Vec3 c = n.speed != 0.0f ? curl4(q, time * n.speed, seed, f) : curl3(q, seed, f);
        return c * n.amplitude;
    }
    // Everything else: the (normalized) gradient of the scalar field, sampled
    // with a step that is a fixed fraction of one noise period so the estimate
    // is stable at any frequency.
    const float h = 0.01f / std::max(std::fabs(n.frequency), 0.01f);
    NoiseNodeParams probe = n;
    probe.amplitude = 1.0f;
    const float gx = evaluate_noise_scalar(probe, p + Vec3{h, 0, 0}, time, seed) -
                     evaluate_noise_scalar(probe, p - Vec3{h, 0, 0}, time, seed);
    const float gy = evaluate_noise_scalar(probe, p + Vec3{0, h, 0}, time, seed) -
                     evaluate_noise_scalar(probe, p - Vec3{0, h, 0}, time, seed);
    const float gz = evaluate_noise_scalar(probe, p + Vec3{0, 0, h}, time, seed) -
                     evaluate_noise_scalar(probe, p - Vec3{0, 0, h}, time, seed);
    const Vec3 g{gx, gy, gz};
    const float len = length(g);
    if (len <= kEpsilon) return Vec3::zero();
    return (g / len) * n.amplitude;
}

}  // namespace aether::procedural
