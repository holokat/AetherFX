// The `flame` op: a flame-shaped, animated heat slice that loops seamlessly.
#include <algorithm>
#include <cmath>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include <nlohmann/json.hpp>

#include "aether/procedural/texture_graph.hpp"

using namespace aether;
using namespace aether::procedural;
using nlohmann::json;

namespace {

constexpr int kWidth = 64;
constexpr int kHeight = 96;

TextureResource bake_flame(const json& params, int frames, uint32_t seed = 3) {
    const json graph = {{"nodes", json::array({json{{"id", "f"}, {"op", "flame"}, {"params", params}}})},
                        {"output", "f"}};
    return bake_texture_graph(graph, TextureBakeOptions{kWidth, kHeight, frames, seed});
}

// One frame out of the baked horizontal strip.
Image frame_of(const TextureResource& tex, int index) {
    Image out(kWidth, kHeight);
    for (int y = 0; y < kHeight; ++y)
        for (int x = 0; x < kWidth; ++x) out.set(x, y, tex.image.get(index * kWidth + x, y));
    return out;
}

float mean_of(const Image& img) {
    double sum = 0.0;
    for (size_t i = 0; i < img.pixel_count(); ++i) sum += img.rgba[i * 4];
    return static_cast<float>(sum / static_cast<double>(img.pixel_count()));
}

// Mean absolute difference between two frames, in [0,1].
float difference(const Image& a, const Image& b) {
    double sum = 0.0;
    for (size_t i = 0; i < a.pixel_count(); ++i)
        sum += std::fabs(static_cast<double>(a.rgba[i * 4]) - static_cast<double>(b.rgba[i * 4]));
    return static_cast<float>(sum / static_cast<double>(a.pixel_count()));
}

// Average heat over a rectangle given in UV.
float region(const Image& img, float u0, float v0, float u1, float v1) {
    double sum = 0.0;
    int count = 0;
    for (int y = static_cast<int>(v0 * kHeight); y < static_cast<int>(v1 * kHeight); ++y) {
        for (int x = static_cast<int>(u0 * kWidth); x < static_cast<int>(u1 * kWidth); ++x) {
            sum += img.get(x, y).r;
            ++count;
        }
    }
    return count > 0 ? static_cast<float>(sum / count) : 0.0f;
}

}  // namespace

TEST_CASE("flame is listed in the op catalogue", "[procedural][texture]") {
    const std::vector<std::string> names = texture_op_names();
    CHECK(std::find(names.begin(), names.end(), "flame") != names.end());
    const json ops = texture_ops_json();
    const auto it = std::find_if(ops.begin(), ops.end(), [](const json& o) { return o["op"] == "flame"; });
    REQUIRE(it != ops.end());
    for (const char* p : {"frequency", "speed", "warp", "width", "sharpness", "licks", "seed"})
        CHECK((*it)["params"].contains(p));
}

TEST_CASE("flame bakes a flipbook with a hot root and dark tip corners", "[procedural][texture]") {
    const TextureResource tex = bake_flame(json::object(), 16);
    REQUIRE(tex.frames == 16);
    REQUIRE(tex.image.width == kWidth * 16);
    REQUIRE(tex.image.height == kHeight);

    // The flame is stored root first: v = 0 is the root, v = 1 the far tip. (The renderer's
    // sprite V axis points up the screen, so this is what draws upright.)
    const Image frame = frame_of(tex, 0);
    const float root_centre = region(frame, 0.4f, 0.05f, 0.6f, 0.3f);
    const float tip_left = region(frame, 0.0f, 0.8f, 0.15f, 1.0f);
    const float tip_right = region(frame, 0.85f, 0.8f, 1.0f, 1.0f);
    CHECK(root_centre > 0.35f);
    CHECK(root_centre > tip_left * 8.0f);
    CHECK(root_centre > tip_right * 8.0f);
    CHECK(tip_left < 0.02f);
    CHECK(tip_right < 0.02f);
    // The root is also hotter than the centre of the tip, and the sides are cooler than the axis.
    CHECK(root_centre > region(frame, 0.4f, 0.8f, 0.6f, 1.0f));
    CHECK(root_centre > region(frame, 0.02f, 0.05f, 0.12f, 0.3f));
    // Grayscale ops write the value into alpha too, so the sheet can drive a sprite directly.
    CHECK(frame.get(kWidth / 2, 2).a == frame.get(kWidth / 2, 2).r);
}

TEST_CASE("flame loops seamlessly over the baked frames", "[procedural][texture]") {
    constexpr int kFrames = 24;
    const TextureResource tex = bake_flame(json::object(), kFrames);
    const Image first = frame_of(tex, 0);
    const Image last = frame_of(tex, kFrames - 1);
    const Image middle = frame_of(tex, kFrames / 2);

    // The wrap from the last frame back to the first must be no bigger a step than any other.
    const float wrap = difference(last, first);
    const float neighbour = difference(frame_of(tex, 0), frame_of(tex, 1));
    CHECK(wrap < neighbour * 1.4f);
    // ... while half a loop away the image really has changed.
    CHECK(difference(middle, first) > wrap * 2.0f);
    CHECK(mean_of(middle) > 0.01f);
}

TEST_CASE("flame is deterministic and answers to its parameters", "[procedural][texture]") {
    CHECK(bake_flame(json::object(), 4).image.hash() == bake_flame(json::object(), 4).image.hash());
    CHECK(bake_flame({{"seed", 1}}, 4).image.hash() != bake_flame({{"seed", 2}}, 4).image.hash());

    // A narrower flame covers less of the tile.
    const float wide = mean_of(frame_of(bake_flame({{"width", 0.9}}, 4), 0));
    const float narrow = mean_of(frame_of(bake_flame({{"width", 0.35}}, 4), 0));
    CHECK(narrow < wide);
    // Higher sharpness darkens everything that is not already at full heat.
    const float soft = mean_of(frame_of(bake_flame({{"sharpness", 1.0}}, 4), 0));
    const float hard = mean_of(frame_of(bake_flame({{"sharpness", 3.0}}, 4), 0));
    CHECK(hard < soft);
    // Breaking up the tips removes heat near the tip but leaves the root alone.
    const Image calm = frame_of(bake_flame({{"licks", 0.0}}, 4), 0);
    const Image torn = frame_of(bake_flame({{"licks", 0.9}}, 4), 0);
    CHECK(region(torn, 0.2f, 0.6f, 0.8f, 0.95f) < region(calm, 0.2f, 0.6f, 0.8f, 0.95f));
    CHECK(region(torn, 0.4f, 0.02f, 0.6f, 0.15f) == region(calm, 0.4f, 0.02f, 0.6f, 0.15f));
}
