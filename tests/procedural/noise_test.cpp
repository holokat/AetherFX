// Noise primitives: determinism, ranges, normalization, curl divergence.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <vector>

#include "aether/procedural/noise.hpp"

using namespace aether;
using namespace aether::procedural;

namespace {

// A deterministic, irrational-ish sample lattice so we never land exactly on
// integer coordinates (where several of these functions are exactly zero).
Vec3 sample_point(int i) {
    return {static_cast<float>(i) * 0.0731f - 3.0f, static_cast<float>(i) * 0.1373f + 1.5f,
            static_cast<float>(i) * 0.0517f - 0.75f};
}

}  // namespace

TEST_CASE("noise primitives are bit-identical across calls", "[procedural][noise]") {
    const FbmParams fbm_params{4, 2.0f, 0.5f, NoiseBasis::Simplex};
    for (int i = 0; i < 256; ++i) {
        const Vec3 p = sample_point(i);
        const Vec2 p2{p.x, p.y};
        const Vec4 p4{p.x, p.y, p.z, static_cast<float>(i) * 0.037f};

        REQUIRE(perlin2(p2, 7) == perlin2(p2, 7));
        REQUIRE(perlin3(p, 7) == perlin3(p, 7));
        REQUIRE(perlin4(p4, 7) == perlin4(p4, 7));
        REQUIRE(simplex2(p2, 7) == simplex2(p2, 7));
        REQUIRE(simplex3(p, 7) == simplex3(p, 7));
        REQUIRE(simplex4(p4, 7) == simplex4(p4, 7));
        REQUIRE(worley2(p2, 7) == worley2(p2, 7));
        REQUIRE(worley3(p, 7) == worley3(p, 7));
        REQUIRE(worley4(p4, 7) == worley4(p4, 7));
        REQUIRE(cellular2(p2, 7) == cellular2(p2, 7));
        REQUIRE(cellular3(p, 7) == cellular3(p, 7));
        REQUIRE(fbm2(p2, 7, fbm_params) == fbm2(p2, 7, fbm_params));
        REQUIRE(fbm3(p, 7, fbm_params) == fbm3(p, 7, fbm_params));
        REQUIRE(fbm4(p4, 7, fbm_params) == fbm4(p4, 7, fbm_params));
        REQUIRE(hash_noise2(p2, 7) == hash_noise2(p2, 7));
        REQUIRE(hash_noise3(p, 7) == hash_noise3(p, 7));

        const Vec3 c0 = curl3(p, 7, fbm_params);
        const Vec3 c1 = curl3(p, 7, fbm_params);
        REQUIRE(c0 == c1);
        REQUIRE(curl4(p, 0.25f, 7, fbm_params) == curl4(p, 0.25f, 7, fbm_params));
    }
}

TEST_CASE("different seeds produce different noise", "[procedural][noise]") {
    int perlin_differs = 0, simplex_differs = 0, worley_differs = 0, cellular_differs = 0, hash_differs = 0;
    for (int i = 0; i < 256; ++i) {
        const Vec3 p = sample_point(i);
        const Vec2 p2{p.x, p.y};
        if (perlin3(p, 1) != perlin3(p, 2)) ++perlin_differs;
        if (simplex3(p, 1) != simplex3(p, 2)) ++simplex_differs;
        if (worley2(p2, 1) != worley2(p2, 2)) ++worley_differs;
        if (cellular3(p, 1) != cellular3(p, 2)) ++cellular_differs;
        if (hash_noise3(p, 1) != hash_noise3(p, 2)) ++hash_differs;
    }
    REQUIRE(perlin_differs > 250);
    REQUIRE(simplex_differs > 250);
    REQUIRE(worley_differs > 250);
    REQUIRE(cellular_differs > 250);
    REQUIRE(hash_differs > 250);
}

TEST_CASE("gradient noise stays within [-1,1] and actually varies", "[procedural][noise]") {
    float lo = 1e9f, hi = -1e9f;
    for (uint32_t seed = 0; seed < 3; ++seed) {
        for (int i = 0; i < 90; ++i) {
            for (int j = 0; j < 90; ++j) {
                const Vec2 p2{static_cast<float>(i) * 0.137f, static_cast<float>(j) * 0.113f};
                const Vec3 p3{p2.x, p2.y, static_cast<float>(seed) * 1.7f};
                const Vec4 p4{p2.x, p2.y, 0.5f, static_cast<float>(seed) * 0.9f};
                for (float v : {perlin2(p2, seed), perlin3(p3, seed), perlin4(p4, seed), simplex2(p2, seed),
                                simplex3(p3, seed), simplex4(p4, seed)}) {
                    lo = std::min(lo, v);
                    hi = std::max(hi, v);
                }
            }
        }
    }
    REQUIRE(lo >= -1.0f);
    REQUIRE(hi <= 1.0f);
    REQUIRE(lo < -0.5f);  // it really does use the range
    REQUIRE(hi > 0.5f);
}

TEST_CASE("worley and cellular ranges", "[procedural][noise]") {
    float f1_lo = 1e9f, f1_hi = -1e9f, f2_hi = -1e9f;
    float cell_lo = 1e9f, cell_hi = -1e9f;
    for (int i = 0; i < 120; ++i) {
        for (int j = 0; j < 120; ++j) {
            const Vec2 p2{static_cast<float>(i) * 0.091f, static_cast<float>(j) * 0.077f};
            const Vec3 p3{p2.x, p2.y, 0.37f};
            float f2 = 0.0f;
            const float f1 = worley2(p2, 5, &f2);
            f1_lo = std::min(f1_lo, f1);
            f1_hi = std::max(f1_hi, f1);
            f2_hi = std::max(f2_hi, f2);
            REQUIRE(f2 >= f1);
            const float cell = cellular3(p3, 5);
            cell_lo = std::min(cell_lo, cell);
            cell_hi = std::max(cell_hi, cell);
        }
    }
    REQUIRE(f1_lo >= 0.0f);
    REQUIRE(f1_hi < 1.5f);
    REQUIRE(f2_hi < 1.6f);
    REQUIRE(cell_lo >= 0.0f);
    REQUIRE(cell_hi < 1.0f);
    REQUIRE(cell_hi - cell_lo > 0.8f);
}

TEST_CASE("cellular is constant inside a Voronoi cell", "[procedural][noise]") {
    // Two points a tiny distance apart almost always share a cell.
    int same = 0;
    for (int i = 0; i < 200; ++i) {
        const Vec3 p = sample_point(i);
        if (cellular3(p, 3) == cellular3(p + Vec3{1e-4f, 0.0f, 0.0f}, 3)) ++same;
    }
    REQUIRE(same > 190);
}

TEST_CASE("fbm stays normalized for any octave count", "[procedural][noise]") {
    for (int octaves = 1; octaves <= 8; ++octaves) {
        FbmParams params;
        params.octaves = octaves;
        params.basis = NoiseBasis::Simplex;
        float lo = 1e9f, hi = -1e9f;
        for (int i = 0; i < 120; ++i) {
            for (int j = 0; j < 120; ++j) {
                const Vec3 p{static_cast<float>(i) * 0.061f, static_cast<float>(j) * 0.053f, 1.25f};
                const float v = fbm3(p, 11, params);
                lo = std::min(lo, v);
                hi = std::max(hi, v);
            }
        }
        INFO("octaves = " << octaves);
        REQUIRE(lo >= -1.0f);
        REQUIRE(hi <= 1.0f);
        REQUIRE(hi - lo > 0.5f);
    }
    // Every basis is normalized the same way.
    for (NoiseBasis basis : {NoiseBasis::Perlin, NoiseBasis::Simplex, NoiseBasis::Worley, NoiseBasis::Cellular}) {
        FbmParams params;
        params.octaves = 5;
        params.basis = basis;
        for (int i = 0; i < 400; ++i) {
            const float v = fbm3(sample_point(i), 4, params);
            REQUIRE(v >= -1.0f);
            REQUIRE(v <= 1.0f);
        }
    }
}

TEST_CASE("curl is numerically divergence free", "[procedural][noise]") {
    FbmParams params;
    params.octaves = 3;
    // The curl is a *discrete* operator: its divergence cancels exactly when
    // measured with the same step it was built from.
    constexpr float kStep = 1e-3f;
    double worst_relative = 0.0;
    for (int i = 0; i < 200; ++i) {
        const Vec3 p = sample_point(i);
        const auto field = [&](Vec3 q) { return curl3(q, 17, params, kStep); };
        const Vec3 xp = field({p.x + kStep, p.y, p.z});
        const Vec3 xm = field({p.x - kStep, p.y, p.z});
        const Vec3 yp = field({p.x, p.y + kStep, p.z});
        const Vec3 ym = field({p.x, p.y - kStep, p.z});
        const Vec3 zp = field({p.x, p.y, p.z + kStep});
        const Vec3 zm = field({p.x, p.y, p.z - kStep});
        const double inv = 1.0 / (2.0 * static_cast<double>(kStep));
        const double dxx = static_cast<double>(xp.x - xm.x) * inv;
        const double dyy = static_cast<double>(yp.y - ym.y) * inv;
        const double dzz = static_cast<double>(zp.z - zm.z) * inv;
        const double scale = std::fabs(dxx) + std::fabs(dyy) + std::fabs(dzz);
        if (scale > 1e-3) {
            worst_relative = std::max(worst_relative, std::fabs(dxx + dyy + dzz) / scale);
        }
    }
    INFO("worst relative divergence = " << worst_relative);
    REQUIRE(worst_relative < 1e-2);
}

TEST_CASE("curl4 is divergence free and animates", "[procedural][noise]") {
    FbmParams params;
    params.octaves = 3;
    constexpr float kStep = 1e-3f;
    double worst_relative = 0.0;
    int moved = 0;
    for (int i = 0; i < 120; ++i) {
        const Vec3 p = sample_point(i);
        const auto field = [&](Vec3 q) { return curl4(q, 0.42f, 23, params, kStep); };
        const Vec3 xp = field({p.x + kStep, p.y, p.z});
        const Vec3 xm = field({p.x - kStep, p.y, p.z});
        const Vec3 yp = field({p.x, p.y + kStep, p.z});
        const Vec3 ym = field({p.x, p.y - kStep, p.z});
        const Vec3 zp = field({p.x, p.y, p.z + kStep});
        const Vec3 zm = field({p.x, p.y, p.z - kStep});
        const double inv = 1.0 / (2.0 * static_cast<double>(kStep));
        const double dxx = static_cast<double>(xp.x - xm.x) * inv;
        const double dyy = static_cast<double>(yp.y - ym.y) * inv;
        const double dzz = static_cast<double>(zp.z - zm.z) * inv;
        const double scale = std::fabs(dxx) + std::fabs(dyy) + std::fabs(dzz);
        if (scale > 1e-3) worst_relative = std::max(worst_relative, std::fabs(dxx + dyy + dzz) / scale);
        if (length(curl4(p, 0.0f, 23, params) - curl4(p, 1.3f, 23, params)) > 1e-3f) ++moved;
    }
    INFO("worst relative divergence = " << worst_relative);
    REQUIRE(worst_relative < 1e-2);
    REQUIRE(moved > 110);
}

TEST_CASE("simplex4 varies along the fourth axis", "[procedural][noise]") {
    int changed = 0;
    float max_delta = 0.0f;
    for (int i = 0; i < 200; ++i) {
        const Vec3 p = sample_point(i);
        const float a = simplex4(Vec4{p, 0.0f}, 13);
        const float b = simplex4(Vec4{p, 0.9f}, 13);
        if (a != b) ++changed;
        max_delta = std::max(max_delta, std::fabs(a - b));
    }
    REQUIRE(changed > 195);
    REQUIRE(max_delta > 0.2f);
    // ...and stays continuous in it.
    for (int i = 0; i < 100; ++i) {
        const Vec3 p = sample_point(i);
        const float a = simplex4(Vec4{p, 0.0f}, 13);
        const float b = simplex4(Vec4{p, 1e-4f}, 13);
        REQUIRE(std::fabs(a - b) < 1e-2f);
    }
}

TEST_CASE("hash noise is per-cell and spans [0,1)", "[procedural][noise]") {
    float lo = 1e9f, hi = -1e9f;
    for (int i = -40; i < 40; ++i) {
        for (int j = -40; j < 40; ++j) {
            const Vec3 cell{static_cast<float>(i), static_cast<float>(j), 3.0f};
            const float v = hash_noise3(cell, 9);
            lo = std::min(lo, v);
            hi = std::max(hi, v);
            // Anywhere inside the same integer cell gives the same value.
            REQUIRE(hash_noise3(cell + Vec3{0.25f, 0.75f, 0.5f}, 9) == v);
        }
    }
    REQUIRE(lo >= 0.0f);
    REQUIRE(hi < 1.0f);
    REQUIRE(lo < 0.05f);
    REQUIRE(hi > 0.95f);
}

TEST_CASE("evaluate_noise_scalar follows the noise node vocabulary", "[procedural][noise]") {
    NoiseNodeParams n;
    n.frequency = 2.0f;
    n.amplitude = 1.0f;

    SECTION("gradient types are signed and bounded by amplitude") {
        for (const char* type : {"perlin", "simplex", "fbm"}) {
            n.noise_type = type;
            float lo = 1e9f, hi = -1e9f;
            for (int i = 0; i < 400; ++i) {
                const float v = evaluate_noise_scalar(n, sample_point(i), 0.0f, 3);
                lo = std::min(lo, v);
                hi = std::max(hi, v);
            }
            INFO(type);
            REQUIRE(lo >= -1.0f);
            REQUIRE(hi <= 1.0f);
            REQUIRE(lo < 0.0f);
            REQUIRE(hi > 0.0f);
        }
    }

    SECTION("worley, cellular and blue_noise are in [0,1]") {
        for (const char* type : {"worley", "cellular", "blue_noise"}) {
            n.noise_type = type;
            for (int i = 0; i < 400; ++i) {
                const float v = evaluate_noise_scalar(n, sample_point(i), 0.0f, 3);
                INFO(type);
                REQUIRE(v >= 0.0f);
                REQUIRE(v <= 1.0f);
            }
        }
    }

    SECTION("amplitude scales the result") {
        n.noise_type = "simplex";
        NoiseNodeParams scaled = n;
        scaled.amplitude = 3.0f;
        for (int i = 0; i < 64; ++i) {
            const Vec3 p = sample_point(i);
            REQUIRE(evaluate_noise_scalar(scaled, p, 0.0f, 3) ==
                    Catch::Approx(evaluate_noise_scalar(n, p, 0.0f, 3) * 3.0f).margin(1e-5));
        }
    }

    SECTION("speed animates, and speed = 0 is static") {
        n.noise_type = "simplex";
        n.speed = 0.0f;
        for (int i = 0; i < 64; ++i) {
            const Vec3 p = sample_point(i);
            REQUIRE(evaluate_noise_scalar(n, p, 0.0f, 3) == evaluate_noise_scalar(n, p, 5.0f, 3));
        }
        n.speed = 1.0f;
        int moved = 0;
        for (int i = 0; i < 64; ++i) {
            const Vec3 p = sample_point(i);
            if (evaluate_noise_scalar(n, p, 0.0f, 3) != evaluate_noise_scalar(n, p, 1.0f, 3)) ++moved;
        }
        REQUIRE(moved > 60);
    }

    SECTION("offset translates the field") {
        n.noise_type = "simplex";
        NoiseNodeParams shifted = n;
        shifted.offset = Vec3{0.5f, -0.25f, 0.125f};
        for (int i = 0; i < 64; ++i) {
            const Vec3 p = sample_point(i);
            REQUIRE(evaluate_noise_scalar(shifted, p, 0.0f, 3) ==
                    evaluate_noise_scalar(n, p + shifted.offset, 0.0f, 3));
        }
    }
}

TEST_CASE("evaluate_noise_vector returns curl for curl and a gradient otherwise", "[procedural][noise]") {
    NoiseNodeParams n;
    n.frequency = 1.5f;
    n.amplitude = 2.0f;

    SECTION("curl matches curl3/curl4 scaled by amplitude") {
        n.noise_type = "curl";
        FbmParams params;
        params.octaves = n.octaves;
        params.lacunarity = n.lacunarity;
        params.gain = n.gain;
        for (int i = 0; i < 32; ++i) {
            const Vec3 p = sample_point(i);
            const Vec3 expected = curl3((p + n.offset) * n.frequency, 5, params) * n.amplitude;
            REQUIRE(evaluate_noise_vector(n, p, 0.0f, 5) == expected);
        }
    }

    SECTION("other types return a unit gradient scaled by amplitude") {
        for (const char* type : {"perlin", "simplex", "fbm", "worley"}) {
            n.noise_type = type;
            int nonzero = 0;
            for (int i = 0; i < 128; ++i) {
                const Vec3 g = evaluate_noise_vector(n, sample_point(i), 0.0f, 5);
                const float len = length(g);
                if (len > 1e-6f) {
                    ++nonzero;
                    INFO(type);
                    REQUIRE(len == Catch::Approx(n.amplitude).margin(1e-4));
                }
            }
            INFO(type);
            REQUIRE(nonzero > 100);
        }
    }
}
