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
