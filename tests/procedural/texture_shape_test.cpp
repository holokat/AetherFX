// The `shape` op: signed-distance silhouettes for emblems, plates and glyph tiles.
// The checks are about the geometry contract rather than about pixels: the op is
// deterministic, a shield is mirror-symmetric and upright, the distance behind the
// modes is the exact one (so a bevel is a linear ramp off a straight side *and* off
// an arc), an outline is a thin band on the contour, and softness only ever widens
// the edge.
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>

#include "aether/procedural/texture_graph.hpp"

using namespace aether;
using namespace aether::procedural;
using Catch::Approx;
using nlohmann::json;

namespace {

constexpr int kSize = 256;

TextureResource bake_shape(json params, int size = kSize, uint32_t seed = 0) {
    const json graph = {{"nodes", json::array({json{{"id", "s"}, {"op", "shape"}, {"params", std::move(params)}}})},
                        {"output", "s"}};
    return bake_texture_graph(graph, TextureBakeOptions{size, size, 1, seed});
}

float lum(const Image& img, float u, float v) { return img.sample(u, v).r; }

float mean_of(const Image& img) {
    double sum = 0.0;
    for (size_t i = 0; i < img.pixel_count(); ++i) sum += img.rgba[i * 4];
    return static_cast<float>(sum / static_cast<double>(img.pixel_count()));
}

// Pixels that are neither empty nor full: the width of the soft edge, in pixels.
int partial_pixels(const Image& img) {
    int count = 0;
    for (size_t i = 0; i < img.pixel_count(); ++i) {
        const float v = img.rgba[i * 4];
        if (v > 0.02f && v < 0.98f) ++count;
    }
    return count;
}

// The shield every geometric check below uses: radius 0.4 at the default aspect, so it is
// 0.8 tall and 0.656 wide (half width 0.328), top edge at v = 0.1 and the point at v = 0.9.
// shoulder 0.3 puts the side/arc junction at v = 0.34; the arcs are centred 0.314 beyond the
// axis with radius 0.642.
json shield(json extra = json::object()) {
    json params = {{"shape", "shield"}, {"radius", 0.4}, {"shoulder", 0.3}, {"softness", 0.01}};
    params.update(extra);
    return params;
}

}  // namespace

TEST_CASE("shape is listed in the op catalogue", "[procedural][texture][shape]") {
    const std::vector<std::string> names = texture_op_names();
    CHECK(std::find(names.begin(), names.end(), "shape") != names.end());
    const json ops = texture_ops_json();
    const auto it = std::find_if(ops.begin(), ops.end(), [](const json& o) { return o["op"] == "shape"; });
    REQUIRE(it != ops.end());
    for (const char* p : {"shape", "mode", "radius", "aspect", "center", "rotation", "sides", "corner_radius",
                          "shoulder", "crest", "softness", "inset", "outline_width", "bevel"})
        CHECK((*it)["params"].contains(p));
    CHECK((*it)["inputs"].empty());
    // The op documents its own enums, the way `voronoi` and `math` do.
    const std::string shapes = (*it)["params"]["shape"]["description"].get<std::string>();
    for (const char* s : {"circle", "polygon", "hexagon", "diamond", "rounded_box", "shield"})
        CHECK(shapes.find(s) != std::string::npos);
    const std::string modes = (*it)["params"]["mode"]["description"].get<std::string>();
    for (const char* m : {"fill", "outline", "bevel"}) CHECK(modes.find(m) != std::string::npos);
}

TEST_CASE("shape is deterministic and writes a grayscale mask in [0,1]", "[procedural][texture][shape]") {
    for (const char* name : {"circle", "polygon", "hexagon", "diamond", "rounded_box", "shield"}) {
        for (const char* mode : {"fill", "outline", "bevel"}) {
            INFO(name << " / " << mode);
            const json params = {{"shape", name}, {"mode", mode}, {"corner_radius", 0.02}, {"crest", 0.05}};
            const TextureResource a = bake_shape(params, 96);
            const TextureResource b = bake_shape(params, 96);
            CHECK(a.image.hash() == b.image.hash());
            // No noise is involved, so the bake seed must not matter either.
            CHECK(a.image.hash() == bake_shape(params, 96, 1234).image.hash());
            float hi = 0.0f;
            for (size_t i = 0; i < a.image.pixel_count(); ++i) {
                const float v = a.image.rgba[i * 4];
                REQUIRE(std::isfinite(v));
                REQUIRE(v >= 0.0f);
                REQUIRE(v <= 1.0f);
                REQUIRE(a.image.rgba[i * 4 + 1] == v);
                REQUIRE(a.image.rgba[i * 4 + 3] == v);  // alpha carries the mask: a sprite can use it as is
                hi = std::max(hi, v);
            }
            CHECK(hi > 0.9f);                                // something was drawn
            CHECK(lum(a.image, 0.02f, 0.02f) == 0.0f);       // and the corner of the tile is empty
        }
    }
    CHECK(bake_shape({{"shape", "shield"}}).image.hash() != bake_shape({{"shape", "hexagon"}}).image.hash());
    CHECK(bake_shape({{"shape", "shield"}}).image.hash() !=
          bake_shape({{"shape", "shield"}, {"crest", 0.06}}).image.hash());
    // An unknown name degrades to the default shape, like every other enum-valued texture param.
    CHECK(bake_shape({{"shape", "blob"}}).image.hash() == bake_shape({{"shape", "circle"}}).image.hash());
}

TEST_CASE("shield is symmetric about its vertical axis in every mode", "[procedural][texture][shape]") {
    for (const char* mode : {"fill", "outline", "bevel"}) {
        for (const double crest : {0.0, 0.07}) {
            INFO(mode << " crest " << crest);
            const Image img =
                bake_shape(shield({{"mode", mode}, {"crest", crest}, {"corner_radius", 0.015}})).image;
            float worst = 0.0f;
            for (int y = 0; y < kSize; ++y)
                for (int x = 0; x < kSize / 2; ++x)
                    worst = std::max(worst, std::fabs(img.get(x, y).r - img.get(kSize - 1 - x, y).r));
            CHECK(worst < 1e-5f);
        }
    }
    // The symmetry is about the shape's own axis, so it survives an off-centre placement.
    const Image moved = bake_shape(shield({{"radius", 0.25}, {"center", json::array({0.5, 0.6})}})).image;
    float worst = 0.0f;
    for (int y = 0; y < kSize; ++y)
        for (int x = 0; x < kSize / 2; ++x)
            worst = std::max(worst, std::fabs(moved.get(x, y).r - moved.get(kSize - 1 - x, y).r));
    CHECK(worst < 1e-5f);
}

TEST_CASE("shield is an upright heater shield: flat top, straight shoulders, a point at the bottom",
          "[procedural][texture][shape]") {
    const Image img = bake_shape(shield()).image;
    CHECK(lum(img, 0.5f, 0.5f) == 1.0f);
    // Full width at the shoulders ...
    CHECK(lum(img, 0.5f + 0.30f, 0.2f) > 0.95f);
    CHECK(lum(img, 0.5f - 0.30f, 0.2f) > 0.95f);
    CHECK(lum(img, 0.5f + 0.30f, 0.12f) > 0.95f);   // right up into the top corner: the top is flat
    // ... narrowing to a point that sits on the axis at the BOTTOM of the image.
    CHECK(lum(img, 0.5f + 0.30f, 0.8f) < 0.02f);
    CHECK(lum(img, 0.5f, 0.88f) > 0.95f);
    CHECK(lum(img, 0.5f + 0.06f, 0.88f) < 0.02f);
    // Nothing outside the box `radius` and the natural 0.82 proportion promise.
    CHECK(lum(img, 0.5f, 0.07f) == 0.0f);
    CHECK(lum(img, 0.5f, 0.93f) == 0.0f);
    CHECK(lum(img, 0.5f + 0.36f, 0.2f) == 0.0f);
    // The silhouette only ever narrows on the way down.
    int previous = kSize;
    for (int y = kSize / 4; y < kSize; ++y) {
        int width = 0;
        for (int x = 0; x < kSize; ++x) width += img.get(x, y).r > 0.5f ? 1 : 0;
        CHECK(width <= previous);
        previous = width;
    }

    SECTION("aspect stretches the width and leaves the height alone") {
        const Image narrow = bake_shape(shield({{"aspect", 0.7}})).image;
        CHECK(lum(narrow, 0.5f + 0.30f, 0.2f) < 0.02f);   // 0.328 * 0.7 = 0.23 half width
        CHECK(lum(narrow, 0.5f + 0.20f, 0.2f) > 0.95f);
        CHECK(lum(narrow, 0.5f, 0.88f) > 0.95f);
    }

    SECTION("crest scallops the top edge between the corners and a centre peak") {
        const Image flat = bake_shape(shield()).image;
        const Image crested = bake_shape(shield({{"crest", 0.08}})).image;
        // Half way between the axis and the corner, just under the top edge: carved away.
        CHECK(lum(flat, 0.5f + 0.164f, 0.125f) > 0.95f);
        CHECK(lum(crested, 0.5f + 0.164f, 0.125f) < 0.02f);
        CHECK(lum(crested, 0.5f - 0.164f, 0.125f) < 0.02f);
        // The peak on the axis and the corners keep their height.
        CHECK(lum(crested, 0.5f, 0.115f) > 0.9f);
        CHECK(lum(crested, 0.5f + 0.315f, 0.12f) > 0.5f);
        // Below the scallops nothing changed.
        CHECK(lum(crested, 0.5f + 0.164f, 0.2f) > 0.95f);
        CHECK(mean_of(crested) < mean_of(flat));
    }
}

TEST_CASE("shape modes: an outline is a thin band on the contour of the fill", "[procedural][texture][shape]") {
    const Image fill = bake_shape(shield()).image;
    const Image outline = bake_shape(shield({{"mode", "outline"}, {"outline_width", 0.03}})).image;
    CHECK(lum(outline, 0.5f, 0.5f) == 0.0f);          // hollow
    CHECK(lum(fill, 0.5f, 0.5f) == 1.0f);
    CHECK(mean_of(outline) < 0.3f * mean_of(fill));   // far less coverage than the fill ...
    CHECK(mean_of(outline) > 0.02f);                  // ... but it is there

    // Wherever the fill crosses one half, the outline is at full strength: it sits ON the contour.
    int crossings = 0;
    for (int y = 40; y < 220; y += 12) {
        for (int x = 1; x < kSize; ++x) {
            const float a = fill.get(x - 1, y).r, b = fill.get(x, y).r;
            if ((a - 0.5f) * (b - 0.5f) > 0.0f) continue;
            ++crossings;
            CHECK(std::max(outline.get(x - 1, y).r, outline.get(x, y).r) > 0.9f);
        }
    }
    CHECK(crossings >= 20);

    SECTION("an outline inset by half its width stays inside the silhouette") {
        const Image hard_fill = bake_shape(shield({{"softness", 0.0}})).image;
        const Image inner =
            bake_shape(shield({{"mode", "outline"}, {"outline_width", 0.04}, {"inset", 0.02}, {"softness", 0.0}})).image;
        for (size_t i = 0; i < inner.pixel_count(); ++i) REQUIRE(inner.rgba[i * 4] <= hard_fill.rgba[i * 4]);
        CHECK(mean_of(inner) > 0.02f);
    }

    SECTION("the band has one width all the way round, because the distance is exact") {
        // Count band pixels along a row through the straight side and along the axis through the top
        // edge; then measure the band across the lower arc along its own normal.
        const Image band = bake_shape(shield({{"mode", "outline"}, {"outline_width", 0.04}, {"softness", 0.0}})).image;
        int across_side = 0, across_top = 0;
        for (int x = kSize / 2; x < kSize; ++x) across_side += band.get(x, 56).r > 0.5f ? 1 : 0;
        for (int y = 0; y < kSize / 2; ++y) across_top += band.get(kSize / 2 + 20, y).r > 0.5f ? 1 : 0;
        CHECK(std::abs(across_side - across_top) <= 1);
        CHECK(across_side == Approx(0.04 * kSize).margin(1.5));
        // Walk outwards along the arc's normal (30 degrees below the horizontal from the arc centre).
        const float cx = 0.5f - 0.314f, cy = 0.34f;
        int across_arc = 0;
        for (int i = 0; i < 400; ++i) {
            const float r = 0.55f + 0.2f * static_cast<float>(i) / 400.0f;
            across_arc += lum(band, cx + r * 0.8660254f, cy + r * 0.5f) > 0.5f ? 1 : 0;
        }
        CHECK(static_cast<float>(across_arc) * (0.2f / 400.0f) == Approx(0.04).margin(0.006));
    }
}

TEST_CASE("shape bevel is a linear ramp of the exact inside distance", "[procedural][texture][shape]") {
    const Image img = bake_shape(shield({{"mode", "bevel"}, {"bevel", 0.1}})).image;
    CHECK(lum(img, 0.5f, 0.5f) == 1.0f);        // deeper than the bevel: saturated
    CHECK(lum(img, 0.5f + 0.34f, 0.25f) == 0.0f);  // outside
    // Off the straight side: 0.025, 0.05 and 0.075 inside the edge at u = 0.828.
    CHECK(lum(img, 0.828f - 0.025f, 0.25f) == Approx(0.25).margin(0.02));
    CHECK(lum(img, 0.828f - 0.050f, 0.25f) == Approx(0.50).margin(0.02));
    CHECK(lum(img, 0.828f - 0.075f, 0.25f) == Approx(0.75).margin(0.02));
    // Off the lower arc (centre (0.186, 0.34) in UV, radius 0.642), along a normal 30 degrees down.
    const float cx = 0.5f - 0.314f, cy = 0.34f, rho = 0.642f;
    for (const float depth : {0.025f, 0.05f, 0.075f}) {
        const float r = rho - depth;
        CHECK(lum(img, cx + r * 0.8660254f, cy + r * 0.5f) == Approx(depth / 0.1f).margin(0.025));
    }
    // A negative inset of the bevel's size turns it into an outer glow: 1 on the old contour, 0 at the new one.
    const Image glow = bake_shape(shield({{"mode", "bevel"}, {"bevel", 0.06}, {"inset", -0.06}})).image;
    CHECK(lum(glow, 0.828f - 0.02f, 0.25f) == 1.0f);
    CHECK(lum(glow, 0.828f + 0.03f, 0.25f) == Approx(0.5).margin(0.03));
    CHECK(lum(glow, 0.828f + 0.07f, 0.25f) == 0.0f);
}

TEST_CASE("shape softness only widens the edge, monotonically", "[procedural][texture][shape]") {
    const std::vector<double> softness = {0.0, 0.005, 0.02, 0.05, 0.1};
    int previous_partial = -1;
    const float hard_mean = mean_of(bake_shape(shield({{"softness", 0.0}})).image);
    for (const double s : softness) {
        INFO("softness " << s);
        const Image img = bake_shape(shield({{"softness", s}})).image;
        const int partial = partial_pixels(img);
        CHECK(partial > previous_partial);
        previous_partial = partial;
        // The falloff is centred on the contour, so the coverage barely moves ...
        CHECK(mean_of(img) == Approx(hard_mean).margin(0.02));
        // ... and walking outwards from the axis the value never rises again.
        for (const int y : {40, 100, 160}) {
            float last = 2.0f;
            for (int x = kSize / 2; x < kSize; ++x) {
                const float v = img.get(x, y).r;
                REQUIRE(v <= last + 1e-6f);
                last = v;
            }
        }
    }
    // A harder edge is steeper at the same place: one pixel either side of the contour.
    const Image soft = bake_shape(shield({{"softness", 0.05}})).image;
    const Image crisp = bake_shape(shield({{"softness", 0.005}})).image;
    CHECK(lum(crisp, 0.828f - 0.02f, 0.25f) > lum(soft, 0.828f - 0.02f, 0.25f));
    CHECK(lum(crisp, 0.828f + 0.02f, 0.25f) < lum(soft, 0.828f + 0.02f, 0.25f));
    CHECK(lum(soft, 0.828f, 0.25f) == Approx(0.5).margin(0.06));
}

TEST_CASE("shape polygons: hexagon, diamond, sides and rotation", "[procedural][texture][shape]") {
    const json base = {{"radius", 0.4}, {"softness", 0.004}};
    SECTION("a hexagon has a vertex straight up and flat flanks at the apothem") {
        json p = base;
        p["shape"] = "hexagon";
        const Image img = bake_shape(p).image;
        CHECK(lum(img, 0.5f, 0.5f - 0.385f) > 0.9f);             // under the top vertex (circumradius 0.4)
        CHECK(lum(img, 0.5f, 0.5f + 0.385f) > 0.9f);
        CHECK(lum(img, 0.5f + 0.385f, 0.5f) < 0.02f);             // the flank is only 0.346 out
        CHECK(lum(img, 0.5f + 0.33f, 0.5f) > 0.9f);
        json q = p;
        q["rotation"] = 30.0;                                     // flat top
        const Image flat = bake_shape(q).image;
        CHECK(lum(flat, 0.5f, 0.5f - 0.385f) < 0.02f);
        CHECK(lum(flat, 0.5f + 0.385f, 0.5f) > 0.9f);
        json generic = p;
        generic["shape"] = "polygon";
        generic["sides"] = 6;
        CHECK(bake_shape(generic).image.hash() == img.hash());    // `hexagon` is polygon with six sides
    }
    SECTION("sides picks the polygon; a triangle points up") {
        json p = base;
        p["shape"] = "polygon";
        p["sides"] = 3;
        const Image tri = bake_shape(p).image;
        CHECK(lum(tri, 0.5f, 0.5f - 0.36f) > 0.9f);               // apex
        CHECK(lum(tri, 0.5f, 0.5f + 0.36f) < 0.02f);              // below the base (0.2 under the centre)
        CHECK(lum(tri, 0.5f + 0.3f, 0.5f + 0.17f) > 0.9f);        // base corner
        CHECK(lum(tri, 0.5f + 0.3f, 0.5f - 0.17f) < 0.02f);
        p["sides"] = 12;
        CHECK(mean_of(bake_shape(p).image) > mean_of(tri));       // more sides fill more of the circle
    }
    SECTION("a diamond is a rhombus, and rotation is counter-clockwise") {
        json p = base;
        p["shape"] = "diamond";
        p["aspect"] = 0.5;
        const Image tall = bake_shape(p).image;
        CHECK(lum(tall, 0.5f, 0.5f - 0.37f) > 0.9f);
        CHECK(lum(tall, 0.5f + 0.17f, 0.5f) > 0.9f);
        CHECK(lum(tall, 0.5f + 0.23f, 0.5f) < 0.02f);
        p["rotation"] = 90.0;
        const Image wide = bake_shape(p).image;
        CHECK(lum(wide, 0.5f + 0.37f, 0.5f) > 0.9f);
        CHECK(lum(wide, 0.5f, 0.5f - 0.23f) < 0.02f);
        // Direction: a shield turned a quarter turn counter-clockwise points its tip to the RIGHT,
        // so its broad shoulders are on the left: (0.3, 0.75) is inside, (0.7, 0.75) is past the arc.
        const Image turned = bake_shape(shield({{"rotation", 90.0}})).image;
        CHECK(lum(turned, 0.3f, 0.75f) > 0.9f);
        CHECK(lum(turned, 0.7f, 0.75f) < 0.02f);
        CHECK(lum(turned, 0.5f + 0.37f, 0.5f) > 0.9f);            // the tip
    }
}

TEST_CASE("shape corner_radius rounds corners without changing the size", "[procedural][texture][shape]") {
    SECTION("rounded_box") {
        const json sharp = {{"shape", "rounded_box"}, {"radius", 0.3}, {"aspect", 1.2}, {"softness", 0.004}};
        json round = sharp;
        round["corner_radius"] = 0.1;
        const Image a = bake_shape(sharp).image, b = bake_shape(round).image;
        CHECK(lum(a, 0.5f + 0.35f, 0.5f - 0.29f) > 0.9f);   // the very corner
        CHECK(lum(b, 0.5f + 0.35f, 0.5f - 0.29f) < 0.02f);
        CHECK(lum(b, 0.5f + 0.35f, 0.5f) > 0.9f);           // the edge midpoints did not move
        CHECK(lum(b, 0.5f + 0.37f, 0.5f) < 0.02f);
        CHECK(lum(b, 0.5f, 0.5f - 0.29f) > 0.9f);
        CHECK(lum(b, 0.5f, 0.5f - 0.31f) < 0.02f);
    }
    SECTION("polygon and shield") {
        json hexagon = {{"shape", "hexagon"}, {"radius", 0.4}, {"softness", 0.004}};
        const Image sharp_hex = bake_shape(hexagon).image;
        hexagon["corner_radius"] = 0.08;
        const Image round_hex = bake_shape(hexagon).image;
        // A 120 degree vertex rounded by r retreats by r / cos(30) - r: from 0.4 to 0.3876.
        CHECK(lum(sharp_hex, 0.5f, 0.5f - 0.397f) > 0.5f);
        CHECK(lum(round_hex, 0.5f, 0.5f - 0.397f) < 0.02f);  // the vertex is gone
        CHECK(lum(round_hex, 0.5f, 0.5f - 0.38f) > 0.9f);
        CHECK(lum(round_hex, 0.5f + 0.34f, 0.5f) > 0.9f);    // the flanks are where they were
        CHECK(lum(round_hex, 0.5f + 0.355f, 0.5f) < 0.02f);

        const Image sharp = bake_shape(shield({{"softness", 0.002}})).image;
        const Image round = bake_shape(shield({{"softness", 0.002}, {"corner_radius", 0.05}})).image;
        CHECK(lum(sharp, 0.5f + 0.32f, 0.108f) > 0.9f);     // the top corner ...
        CHECK(lum(round, 0.5f + 0.32f, 0.108f) < 0.02f);    // ... rounds off
        // The point is about 60 degrees wide, so rounding it by 0.05 pulls it up from v = 0.9 to 0.892.
        CHECK(lum(sharp, 0.5f, 0.897f) > 0.5f);             // (the tip is under a pixel wide here)
        CHECK(lum(round, 0.5f, 0.897f) < 0.02f);
        CHECK(lum(round, 0.5f, 0.885f) > 0.9f);
        CHECK(lum(round, 0.5f + 0.32f, 0.25f) > 0.9f);      // sides and top stay put
        CHECK(lum(round, 0.5f + 0.335f, 0.25f) < 0.02f);
        CHECK(lum(round, 0.5f, 0.108f) > 0.9f);
        CHECK(lum(round, 0.5f, 0.093f) < 0.02f);
    }
}

TEST_CASE("shape inset shrinks and grows the contour by an exact distance", "[procedural][texture][shape]") {
    const Image base = bake_shape(shield({{"softness", 0.0}})).image;
    const Image smaller = bake_shape(shield({{"softness", 0.0}, {"inset", 0.05}})).image;
    const Image larger = bake_shape(shield({{"softness", 0.0}, {"inset", -0.05}})).image;
    for (size_t i = 0; i < base.pixel_count(); ++i) {
        REQUIRE(smaller.rgba[i * 4] <= base.rgba[i * 4]);
        REQUIRE(base.rgba[i * 4] <= larger.rgba[i * 4]);
    }
    // The straight side moved by exactly the inset, in both directions.
    CHECK(lum(smaller, 0.828f - 0.04f, 0.25f) == 0.0f);
    CHECK(lum(smaller, 0.828f - 0.06f, 0.25f) == 1.0f);
    CHECK(lum(larger, 0.828f + 0.04f, 0.25f) == 1.0f);
    CHECK(lum(larger, 0.828f + 0.06f, 0.25f) == 0.0f);
    // Composes inside a graph: a fill minus its own inset is a rim, which is how a bevelled emblem is built.
    const json graph = {
        {"nodes", json::array({
            json{{"id", "outer"}, {"op", "shape"}, {"params", shield()}},
            json{{"id", "inner"}, {"op", "shape"}, {"params", shield({{"inset", 0.06}})}},
            json{{"id", "rim"}, {"op", "math"}, {"params", {{"mode", "subtract"}}}, {"inputs", {{"a", "outer"}, {"b", "inner"}}}},
        })},
        {"output", "rim"}};
    const Image rim = bake_texture_graph(graph, TextureBakeOptions{kSize, kSize, 1, 0}).image;
    CHECK(lum(rim, 0.5f, 0.5f) == 0.0f);
    CHECK(lum(rim, 0.828f - 0.03f, 0.25f) > 0.95f);
    CHECK(validate_texture_graph(graph).error_count() == 0);
}
