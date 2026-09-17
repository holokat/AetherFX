#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "aether/core/enums.hpp"
#include "aether/core/frame_state.hpp"
#include "aether/core/image.hpp"
#include "aether/core/math.hpp"
#include "aether/core/rng.hpp"
#include "aether/core/value.hpp"

using namespace aether;

TEST_CASE("math: matrix inverse and look_at", "[core][math]") {
    Mat4 m = Mat4::trs({1, 2, 3}, {10, 20, 30}, {2, 2, 2});
    Mat4 inv = m.inverse();
    Vec3 p{0.3f, -0.7f, 1.1f};
    Vec3 back = inv.transform_point(m.transform_point(p));
    REQUIRE_THAT(back.x, Catch::Matchers::WithinAbs(p.x, 1e-4));
    REQUIRE_THAT(back.y, Catch::Matchers::WithinAbs(p.y, 1e-4));
    REQUIRE_THAT(back.z, Catch::Matchers::WithinAbs(p.z, 1e-4));
    Mat4 v = Mat4::look_at({0, 0, 5}, {0, 0, 0}, {0, 1, 0});
    Vec3 origin_in_view = v.transform_point({0, 0, 0});
    REQUIRE_THAT(origin_in_view.z, Catch::Matchers::WithinAbs(-5.0, 1e-5));
}

TEST_CASE("rng: pcg32 is deterministic and derive_seed is stable", "[core][rng]") {
    Pcg32 a(42, 1), b(42, 1);
    for (int i = 0; i < 100; ++i) REQUIRE(a.next_u32() == b.next_u32());
    REQUIRE(derive_seed(7u, "flames", std::nullopt) == derive_seed(7u, "flames", std::nullopt));
    REQUIRE(derive_seed(7u, "flames", std::nullopt) != derive_seed(7u, "smoke", std::nullopt));
    REQUIRE(derive_seed(7u, "flames", 3u) != derive_seed(7u, "flames", std::nullopt));
    Pcg32 c(1);
    for (int i = 0; i < 1000; ++i) { float f = c.next_float(); REQUIRE(f >= 0.0f); REQUIRE(f < 1.0f); }
}

TEST_CASE("enums: round trip", "[core][enums]") {
    for (int i = 0; i < kNodeTypeCount; ++i) {
        NodeType t = static_cast<NodeType>(i), back{};
        REQUIRE(parse_node_type(to_string(t), back));
        REQUIRE(back == t);
    }
    BlendMode b{};
    REQUIRE_FALSE(parse_blend_mode("nope", b));
}

TEST_CASE("curve and gradient evaluation", "[core][value]") {
    Curve c{{0.0f, 0.0f, Interp::Linear}, {0.5f, 1.0f, Interp::Step}, {1.0f, 0.0f, Interp::Linear}};
    REQUIRE_THAT(c.eval(0.25f), Catch::Matchers::WithinAbs(0.5, 1e-6));
    REQUIRE_THAT(c.eval(0.75f), Catch::Matchers::WithinAbs(1.0, 1e-6));  // step holds
    REQUIRE_THAT(c.eval(-1.0f), Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE_THAT(c.eval(2.0f), Catch::Matchers::WithinAbs(0.0, 1e-6));
    REQUIRE(Curve{}.eval(0.3f) == 1.0f);
    Gradient g{{0.0f, Color::black()}, {1.0f, Color::white()}};
    REQUIRE_THAT(g.eval(0.5f).r, Catch::Matchers::WithinAbs(0.5, 1e-6));
}

TEST_CASE("image: sample and hash", "[core][image]") {
    Image img(4, 4, Color::black());
    img.set(0, 0, Color::white());
    REQUIRE(img.get(0, 0) == Color::white());
    Color c = img.sample(0.125f, 0.125f);  // pixel center of (0,0)
    REQUIRE_THAT(c.r, Catch::Matchers::WithinAbs(1.0, 1e-5));
    Image other = img;
    REQUIRE(other.hash() == img.hash());
    other.set(1, 1, Color{0.5f, 0, 0, 1});
    REQUIRE(other.hash() != img.hash());
}

TEST_CASE("frame state: particle buffer and hash", "[core][frame_state]") {
    ParticleBuffer pb;
    pb.resize(3);
    pb.position[2] = {1, 2, 3};
    pb.swap_remove(0);
    REQUIRE(pb.count() == 2);
    REQUIRE(pb.position[0] == Vec3{1, 2, 3});
    FrameState a, b;
    a.particles.push_back(pb); b.particles.push_back(pb);
    REQUIRE(a.hash() == b.hash());
    b.particles[0].position[0].x += 1.0f;
    REQUIRE(a.hash() != b.hash());
}

TEST_CASE("frame state: mesh particle orientation, scale and variant", "[core][frame_state]") {
    ParticleBuffer pb;
    pb.resize(3);
    // Defaults reproduce the pre-orientation behaviour: identity rotation, unit
    // scale, variant 0.
    for (size_t i = 0; i < pb.count(); ++i) {
        REQUIRE(pb.orientation[i] == Vec4{0, 0, 0, 1});
        REQUIRE(pb.scale3[i] == Vec3{1, 1, 1});
        REQUIRE(pb.variant[i] == 0u);
    }

    SECTION("the hash covers the new arrays") {
        FrameState a, b;
        a.particles.push_back(pb);
        b.particles.push_back(pb);
        REQUIRE(a.hash() == b.hash());

        b.particles[0].orientation[1] = Vec4{0.0f, 0.7071068f, 0.0f, 0.7071068f};
        REQUIRE(a.hash() != b.hash());

        FrameState c = a;
        c.particles[0].scale3[2] = Vec3{1.0f, 3.0f, 1.0f};
        REQUIRE(c.hash() != a.hash());

        FrameState d = a;
        d.particles[0].variant[0] = 2u;
        REQUIRE(d.hash() != a.hash());
    }

    SECTION("compaction keeps every array aligned") {
        for (size_t i = 0; i < pb.count(); ++i) {
            pb.position[i] = Vec3{static_cast<float>(i), 0.0f, 0.0f};
            pb.orientation[i] = Vec4{static_cast<float>(i), 0.0f, 0.0f, 1.0f};
            pb.scale3[i] = Vec3{static_cast<float>(i), 1.0f, 1.0f};
            pb.variant[i] = static_cast<uint32_t>(i);
        }
        pb.swap_remove(0);  // particle 2 is moved into slot 0
        REQUIRE(pb.count() == 2);
        REQUIRE(pb.orientation.size() == 2);
        REQUIRE(pb.scale3.size() == 2);
        REQUIRE(pb.variant.size() == 2);
        for (size_t i = 0; i < pb.count(); ++i) {
            const float k = pb.position[i].x;
            REQUIRE(pb.orientation[i].x == k);
            REQUIRE(pb.scale3[i].x == k);
            REQUIRE(pb.variant[i] == static_cast<uint32_t>(k));
        }

        pb.clear();
        REQUIRE(pb.orientation.empty());
        REQUIRE(pb.scale3.empty());
        REQUIRE(pb.variant.empty());
        pb.push_default();
        REQUIRE(pb.orientation.size() == 1);
        REQUIRE(pb.orientation[0] == Vec4{0, 0, 0, 1});
        REQUIRE(pb.scale3[0] == Vec3{1, 1, 1});
    }
}

TEST_CASE("FrameState hash covers the procedural volume fields", "[core][frame_state][volume]") {
    VolumeState v;
    v.id = "nebula";
    v.mode = "procedural";
    v.shape = "nebula";
    v.backend = "procedural_volume";
    v.radius = 2.0f;
    v.height = 1.5f;
    v.density = 1.1f;
    v.emission = 1.5f;
    v.transform = Mat4::trs({0, 2, 0}, {0, 30, 0}, {1, 1, 1});
    v.bounds_min = Vec3{-2, 1, -2};
    v.bounds_max = Vec3{2, 3, 2};
    v.seed = 7u;
    v.time = 1.25f;

    FrameState a;
    a.volumes.push_back(v);
    FrameState b = a;
    REQUIRE(a.hash() == b.hash());

    // Every field a renderer reads has to move the hash, or a determinism test
    // would pass over a volume that changed.
    auto differs = [&](auto mutate) {
        FrameState other = a;
        mutate(other.volumes[0]);
        return other.hash() != a.hash();
    };
    CHECK(differs([](VolumeState& s) { s.mode = "simulation"; }));
    CHECK(differs([](VolumeState& s) { s.shape = "column"; }));
    CHECK(differs([](VolumeState& s) { s.backend = "volume_stub"; }));
    CHECK(differs([](VolumeState& s) { s.radius += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.height += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.density += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.emission += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.color = Color{1, 0, 0, 1}; }));
    CHECK(differs([](VolumeState& s) { s.color_hot = Color{0, 1, 0, 1}; }));
    CHECK(differs([](VolumeState& s) { s.filament_scale += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.strands += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.carve += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.softness += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.spiral_arms = 4; }));
    CHECK(differs([](VolumeState& s) { s.arm_sharpness += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.twist += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.spin += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.climb += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.scatter += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.march_steps = 64; }));
    CHECK(differs([](VolumeState& s) { s.transform.at(0, 3) = 1.0f; }));
    CHECK(differs([](VolumeState& s) { s.bounds_max.y += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.seed = 8u; }));
    CHECK(differs([](VolumeState& s) { s.time += 0.01f; }));
    CHECK(differs([](VolumeState& s) { s.temperature += 0.01f; }));
}
