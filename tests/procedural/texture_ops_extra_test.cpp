#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>

#include "aether/procedural/texture_graph.hpp"

using namespace aether;
using namespace aether::procedural;
using nlohmann::json;

namespace {
TextureResource bake(const json& node, int size = 128) {
    json graph = {{"nodes", json::array({node})}, {"output", node["id"]}};
    return bake_texture_graph(graph, TextureBakeOptions{size, size, 1, 0});
}
float lum(const Image& img, float u, float v) { return img.sample(u, v).r; }
}  // namespace

TEST_CASE("spokes op draws radial lines only along the spoke directions", "[procedural][texture]") {
    auto tex = bake({{"id", "s"}, {"op", "spokes"}, {"params", {{"count", 4}, {"width", 0.02}, {"softness", 0.004},
                                                                 {"inner_radius", 0.05}, {"outer_radius", 0.5}}}});
    const Image& img = tex.image;
    REQUIRE(img.width == 128);
    CHECK(lum(img, 0.5f, 0.25f) > 0.9f);   // on the vertical spoke
    CHECK(lum(img, 0.75f, 0.5f) > 0.9f);   // on the horizontal spoke
    CHECK(lum(img, 0.68f, 0.68f) < 0.05f); // between spokes (45 degrees) is dark with count 4
    CHECK(lum(img, 0.5f, 0.505f) < 0.05f); // inside inner radius
    auto eight = bake({{"id", "s"}, {"op", "spokes"}, {"params", {{"count", 8}, {"width", 0.02}}}});
    CHECK(lum(eight.image, 0.68f, 0.68f) > 0.9f);  // count 8 adds the diagonals
    CHECK(tex.image.hash() == bake({{"id", "s"}, {"op", "spokes"}, {"params", {{"count", 4}, {"width", 0.02}, {"softness", 0.004},
                                                                                  {"inner_radius", 0.05}, {"outer_radius", 0.5}}}}).image.hash());
}

TEST_CASE("star op has a bright core and rays that taper", "[procedural][texture]") {
    auto tex = bake({{"id", "st"}, {"op", "star"}, {"params", {{"points", 4}, {"width", 0.03}, {"outer_radius", 0.3}, {"core_radius", 0.05}}}});
    const Image& img = tex.image;
    CHECK(lum(img, 0.5f, 0.5f) > 0.95f);                 // core
    const float near = lum(img, 0.5f, 0.42f);            // on a ray near the core
    const float far = lum(img, 0.5f, 0.24f);             // further out along the ray
    CHECK(near > far);
    CHECK(far > 0.05f);
    CHECK(lum(img, 0.5f, 0.15f) < 0.02f);                // beyond outer radius
    CHECK(lum(img, 0.65f, 0.65f) < 0.05f);               // off the rays
}

TEST_CASE("spokes and star are listed in the op catalogue", "[procedural][texture]") {
    auto names = texture_op_names();
    CHECK(std::find(names.begin(), names.end(), "spokes") != names.end());
    CHECK(std::find(names.begin(), names.end(), "star") != names.end());
}
