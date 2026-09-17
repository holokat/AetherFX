// The `fire_sim` op: a baked 2D flame simulation. Unlike `flame` (one painted
// tongue) the shapes come out of the flow, so the checks here are about the
// simulation's behaviour: it stays in range, it is deterministic, it is hot at
// the root and cold at the tip, consecutive frames are related to each other,
// and the flipbook closes on itself.
#include <algorithm>
#include <cmath>
#include <string>
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

TextureResource bake_fire(const json& params, int frames, uint32_t seed = 5) {
    const json graph = {{"nodes", json::array({json{{"id", "f"}, {"op", "fire_sim"}, {"params", params}}})},
                        {"output", "f"}};
    return bake_texture_graph(graph, TextureBakeOptions{kWidth, kHeight, frames, seed});
}

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

// Average heat over a rectangle given in UV (v = 0 is the root).
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

TEST_CASE("fire_sim is listed in the op catalogue", "[procedural][texture][fire]") {
    const std::vector<std::string> names = texture_op_names();
    CHECK(std::find(names.begin(), names.end(), "fire_sim") != names.end());
    const json ops = texture_ops_json();
    const auto it = std::find_if(ops.begin(), ops.end(), [](const json& o) { return o["op"] == "fire_sim"; });
    REQUIRE(it != ops.end());
    for (const char* p : {"seed", "fuel", "fuel_width", "buoyancy", "turbulence", "turbulence_scale", "cooling",
                          "detail", "speed", "substeps", "warmup", "loop", "flicker", "sharpness"})
        CHECK((*it)["params"].contains(p));
    // `flame` keeps its own spec: fire_sim is an addition, not a replacement.
    CHECK(std::find(names.begin(), names.end(), "flame") != names.end());
}

TEST_CASE("fire_sim bakes a strip of heat values in [0,1]", "[procedural][texture][fire]") {
    constexpr int kFrames = 8;
    const TextureResource tex = bake_fire(json::object(), kFrames);
    REQUIRE(tex.frames == kFrames);
    REQUIRE(tex.image.width == kWidth * kFrames);
    REQUIRE(tex.image.height == kHeight);

    float lo = 1.0f, hi = 0.0f;
    for (size_t i = 0; i < tex.image.pixel_count(); ++i) {
        const float v = tex.image.rgba[i * 4];
        CHECK(std::isfinite(v));
        lo = std::min(lo, v);
        hi = std::max(hi, v);
        // Grayscale ops write the value into rgb and alpha, so the sheet can drive a sprite directly.
        CHECK(tex.image.rgba[i * 4 + 3] == v);
    }
    CHECK(lo >= 0.0f);
    CHECK(hi <= 1.0f);
    CHECK(hi > 0.9f);   // there is a white-hot core somewhere
    CHECK(lo < 0.01f);  // and cold air somewhere
}

TEST_CASE("fire_sim burns hot at the root and cools towards the tip", "[procedural][texture][fire]") {
    const TextureResource tex = bake_fire(json::object(), 8);
    for (int f = 0; f < 8; ++f) {
        const Image frame = frame_of(tex, f);
        // Measured above the root fade-in (the bottom of the tile ramps up so a big sprite has no
        // hard edge across its base) and below the tip.
        const float root = region(frame, 0.0f, 0.1f, 1.0f, 0.3f);
        const float top = region(frame, 0.0f, 0.8f, 1.0f, 1.0f);
        INFO("frame " << f << " root " << root << " top " << top);
        CHECK(root > 0.25f);
        CHECK(root > top * 3.0f);
        // The fuel band is centred, so the flame lives around the axis, not in the corners.
        CHECK(region(frame, 0.4f, 0.1f, 0.6f, 0.25f) > region(frame, 0.0f, 0.1f, 0.08f, 0.25f));
        // The tile's own border is cold: nothing is cut off at the edges of the sprite.
        for (int x = 0; x < kWidth; ++x) {
            CHECK(frame.get(x, 0).r < 0.02f);
            CHECK(frame.get(x, kHeight - 1).r < 0.02f);
        }
        for (int y = 0; y < kHeight; ++y) {
            CHECK(frame.get(0, y).r < 0.02f);
            CHECK(frame.get(kWidth - 1, y).r < 0.02f);
        }
    }
}

TEST_CASE("fire_sim frames move but stay coherent", "[procedural][texture][fire]") {
    constexpr int kFrames = 16;
    const TextureResource tex = bake_fire(json::object(), kFrames);
    std::vector<Image> frames;
    frames.reserve(kFrames);
    for (int f = 0; f < kFrames; ++f) frames.push_back(frame_of(tex, f));

    float worst_neighbour = 0.0f;
    for (int f = 0; f + 1 < kFrames; ++f) {
        const float d = difference(frames[static_cast<size_t>(f)], frames[static_cast<size_t>(f + 1)]);
        INFO("frames " << f << " and " << f + 1);
        CHECK(d > 0.0005f);  // the fire is alive ...
        CHECK(d < 0.12f);    // ... but one frame is not a new fire
        worst_neighbour = std::max(worst_neighbour, d);
    }
    // Half a loop apart the picture really has changed: more than any single step.
    const float far = difference(frames[0], frames[kFrames / 2]);
    CHECK(far > worst_neighbour);
}

TEST_CASE("fire_sim closes the loop", "[procedural][texture][fire]") {
    constexpr int kFrames = 16;
    const TextureResource tex = bake_fire(json::object(), kFrames);
    const Image first = frame_of(tex, 0);
    const Image last = frame_of(tex, kFrames - 1);

    double sum = 0.0;
    float worst = 0.0f;
    for (int f = 0; f + 1 < kFrames; ++f) {
        const float d = difference(frame_of(tex, f), frame_of(tex, f + 1));
        sum += static_cast<double>(d);
        worst = std::max(worst, d);
    }
    const float mean_step = static_cast<float>(sum / static_cast<double>(kFrames - 1));
    const float wrap = difference(last, first);
    INFO("wrap " << wrap << " mean step " << mean_step << " worst step " << worst);
    // The wrap from the last frame back to the first is just another step.
    CHECK(wrap < worst * 1.25f);
    CHECK(wrap < mean_step * 1.6f);

    // Without `loop` the sim is not asked to close, and the seam is free to be a jump.
    const TextureResource open = bake_fire({{"loop", false}}, kFrames);
    CHECK(mean_of(frame_of(open, kFrames - 1)) > 0.01f);
}

TEST_CASE("fire_sim is deterministic", "[procedural][texture][fire]") {
    const TextureResource a = bake_fire(json::object(), 6);
    const TextureResource b = bake_fire(json::object(), 6);
    REQUIRE(a.image.rgba.size() == b.image.rgba.size());
    for (size_t i = 0; i < a.image.rgba.size(); ++i) REQUIRE(a.image.rgba[i] == b.image.rgba[i]);
    CHECK(a.image.hash() == b.image.hash());

    // A different per-op seed is a different fire; so is a different bake seed.
    CHECK(bake_fire({{"seed", 1}}, 6).image.hash() != bake_fire({{"seed", 2}}, 6).image.hash());
    CHECK(bake_fire(json::object(), 6, 11).image.hash() != bake_fire(json::object(), 6, 12).image.hash());
}

TEST_CASE("fire_sim answers to its parameters", "[procedural][texture][fire]") {
    // A narrow fuel band makes a narrow fire.
    const Image wide = frame_of(bake_fire({{"fuel_width", 0.8}}, 4), 3);
    const Image narrow = frame_of(bake_fire({{"fuel_width", 0.2}}, 4), 3);
    CHECK(region(narrow, 0.0f, 0.0f, 0.2f, 0.2f) < region(wide, 0.0f, 0.0f, 0.2f, 0.2f));

    // More cooling burns out lower.
    const Image cool = frame_of(bake_fire({{"cooling", 3.5}}, 4), 3);
    const Image hot = frame_of(bake_fire({{"cooling", 0.8}}, 4), 3);
    CHECK(region(cool, 0.0f, 0.5f, 1.0f, 1.0f) < region(hot, 0.0f, 0.5f, 1.0f, 1.0f));

    // More buoyancy carries the fire higher.
    const Image slow = frame_of(bake_fire({{"buoyancy", 0.8}}, 4), 3);
    const Image fast = frame_of(bake_fire({{"buoyancy", 3.0}}, 4), 3);
    CHECK(region(fast, 0.0f, 0.45f, 1.0f, 0.9f) > region(slow, 0.0f, 0.45f, 1.0f, 0.9f));

    // Turbulence is what bends the column off the axis.
    const Image calm = frame_of(bake_fire({{"turbulence", 0.0}}, 4), 3);
    const Image wild = frame_of(bake_fire({{"turbulence", 2.0}}, 4), 3);
    const auto off_axis = [](const Image& img) {
        return region(img, 0.0f, 0.3f, 0.22f, 0.9f) + region(img, 0.78f, 0.3f, 1.0f, 0.9f);
    };
    CHECK(off_axis(wild) > off_axis(calm));

    // No fuel, no fire.
    CHECK(mean_of(frame_of(bake_fire({{"fuel", 0.0}}, 4), 3)) < 0.001f);
}
