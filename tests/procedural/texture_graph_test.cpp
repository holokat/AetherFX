// Procedural texture graph: baking, op coverage, tiling, validation.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/error.hpp"
#include "aether/procedural/texture_graph.hpp"

using namespace aether;
using namespace aether::procedural;
using nlohmann::json;

namespace {

// tests/fixtures/effects/fireball.json, node "tex_puff".
const char* kTexPuff = R"JSON({
  "nodes": [
    {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
    {"id": "n", "op": "fbm", "params": {"frequency": 4.0, "octaves": 4, "seed": 3}},
    {"id": "m", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r", "b": "n"}},
    {"id": "l", "op": "levels", "params": {"in_low": 0.05, "in_high": 0.6}, "inputs": {"a": "m"}}
  ],
  "output": "l"
})JSON";

// examples/effects/fire_aoe.json, node "tex_rune".
const char* kTexRune = R"JSON({
  "nodes": [
    {"id": "outer", "op": "ring", "params": {"radius": 0.46, "thickness": 0.03, "softness": 0.01}},
    {"id": "inner", "op": "ring", "params": {"radius": 0.3, "thickness": 0.015, "softness": 0.01}},
    {"id": "cracks", "op": "cracks", "params": {"density": 6.0, "width": 0.012, "seed": 11}},
    {"id": "mask", "op": "gradient_radial", "params": {"radius": 0.44, "falloff": "linear"}},
    {"id": "cm", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "cracks", "b": "mask"}},
    {"id": "sum", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "outer", "b": "inner"}},
    {"id": "all", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "sum", "b": "cm"}}
  ],
  "output": "all"
})JSON";

// examples/effects/fire_aoe.json, node "tex_scorch".
const char* kTexScorch = R"JSON({
  "nodes": [
    {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "inner_radius": 0.2, "falloff": "smooth"}},
    {"id": "n", "op": "fbm", "params": {"frequency": 6.0, "octaves": 3, "seed": 5}},
    {"id": "m", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r", "b": "n"}},
    {"id": "l", "op": "levels", "params": {"in_low": 0.1, "in_high": 0.5}, "inputs": {"a": "m"}}
  ],
  "output": "l"
})JSON";

// tests/fixtures/effects/fireball.json, node "tex_spark".
const char* kTexSpark = R"JSON({
  "nodes": [{"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "quadratic"}}],
  "output": "r"
})JSON";

float mean_region(const Image& img, int x0, int y0, int size) {
    float total = 0.0f;
    for (int y = y0; y < y0 + size; ++y) {
        for (int x = x0; x < x0 + size; ++x) total += img.get(x, y).r;
    }
    return total / static_cast<float>(size * size);
}

bool has_code(const Diagnostics& diag, const std::string& code) {
    return std::any_of(diag.items.begin(), diag.items.end(),
                       [&](const Diagnostic& d) { return d.code == code; });
}

}  // namespace

TEST_CASE("bakes the fireball tex_puff graph", "[procedural][texture]") {
    TextureBakeOptions options;
    options.width = 128;
    options.height = 128;
    const TextureResource baked = bake_texture_graph(json::parse(kTexPuff), options);

    REQUIRE(baked.image.width == 128);
    REQUIRE(baked.image.height == 128);
    REQUIRE(baked.frames == 1);
    REQUIRE(baked.frame_width() == 128);

    const Color center = baked.image.get(64, 64);
    const Color corner = baked.image.get(0, 0);
    REQUIRE(corner.r == Catch::Approx(0.0f).margin(1e-6));
    REQUIRE(corner.a == Catch::Approx(0.0f).margin(1e-6));
    REQUIRE(center.r > corner.r);
    REQUIRE(center.r > 0.05f);
    // Grayscale ops mirror the value into alpha so the mask doubles as a sprite.
    REQUIRE(center.a == Catch::Approx(center.r));
    // The disc really is a disc: the middle is much brighter than the corners.
    REQUIRE(mean_region(baked.image, 48, 48, 32) > 10.0f * mean_region(baked.image, 0, 0, 16));

    // Baking twice is bit-identical.
    const TextureResource again = bake_texture_graph(json::parse(kTexPuff), options);
    REQUIRE(again.image.hash() == baked.image.hash());

    // A different bake seed gives a different image.
    TextureBakeOptions other = options;
    other.seed = 99;
    REQUIRE(bake_texture_graph(json::parse(kTexPuff), other).image.hash() != baked.image.hash());
}

TEST_CASE("bakes every texture graph shipped in examples/effects", "[procedural][texture]") {
    struct Case {
        const char* name;
        const char* graph;
        int size;
    };
    const Case cases[] = {{"tex_puff", kTexPuff, 128},
                          {"tex_spark", kTexSpark, 32},
                          {"tex_rune", kTexRune, 256},
                          {"tex_scorch", kTexScorch, 256}};
    for (const Case& c : cases) {
        INFO(c.name);
        TextureBakeOptions options;
        options.width = c.size;
        options.height = c.size;
        TextureResource baked;
        REQUIRE_NOTHROW(baked = bake_texture_graph(json::parse(c.graph), options));
        REQUIRE(baked.image.width == c.size);
        REQUIRE(baked.image.height == c.size);
        float lo = 1e9f, hi = -1e9f;
        for (int y = 0; y < c.size; ++y) {
            for (int x = 0; x < c.size; ++x) {
                const Color px = baked.image.get(x, y);
                lo = std::min(lo, px.r);
                hi = std::max(hi, px.r);
            }
        }
        REQUIRE(lo >= 0.0f);
        REQUIRE(hi <= 1.0f);
        REQUIRE(hi - lo > 0.25f);  // it is not a flat image
    }
}

TEST_CASE("every texture op executes on a tiny canvas", "[procedural][texture]") {
    for (const std::string& op : texture_op_names()) {
        INFO("op = " << op);
        json graph;
        graph["nodes"] = json::array();
        graph["nodes"].push_back(json{{"id", "src_a"}, {"op", "fbm"}, {"params", {{"frequency", 3}}}});
        graph["nodes"].push_back(json{{"id", "src_b"}, {"op", "gradient_radial"}, {"params", json::object()}});
        json node;
        node["id"] = "target";
        node["op"] = op;
        node["params"] = json::object();
        // Wire every port this op could possibly read.
        node["inputs"] = json{{"a", "src_a"}, {"b", "src_b"}, {"by", "src_b"},
                              {"noise", "src_a"}, {"r", "src_a"}, {"g", "src_b"}};
        graph["nodes"].push_back(node);
        graph["output"] = "target";

        TextureBakeOptions options;
        options.width = 16;
        options.height = 16;
        TextureResource baked;
        REQUIRE_NOTHROW(baked = bake_texture_graph(graph, options));
        REQUIRE(baked.image.width == 16);
        REQUIRE(baked.image.height == 16);
        for (int y = 0; y < 16; ++y) {
            for (int x = 0; x < 16; ++x) {
                const Color px = baked.image.get(x, y);
                for (int ch = 0; ch < 4; ++ch) REQUIRE(std::isfinite(px[ch]));
            }
        }
    }
}

TEST_CASE("animated bakes lay frames out side by side", "[procedural][texture]") {
    const json graph = json::parse(R"({"nodes": [{"id": "t", "op": "time", "params": {"speed": 1}}], "output": "t"})");
    TextureBakeOptions options;
    options.width = 16;
    options.height = 16;
    options.frames = 4;
    const TextureResource baked = bake_texture_graph(graph, options);

    REQUIRE(baked.frames == 4);
    REQUIRE(baked.image.width == 64);
    REQUIRE(baked.image.height == 16);
    REQUIRE(baked.frame_width() == 16);

    std::vector<float> frame_values;
    for (int f = 0; f < 4; ++f) frame_values.push_back(baked.image.get(f * 16 + 8, 8).r);
    REQUIRE(frame_values[0] == Catch::Approx(0.0f).margin(1e-6));
    REQUIRE(frame_values[1] == Catch::Approx(0.25f).margin(1e-6));
    REQUIRE(frame_values[2] == Catch::Approx(0.5f).margin(1e-6));
    REQUIRE(frame_values[3] == Catch::Approx(0.75f).margin(1e-6));
    for (size_t i = 1; i < frame_values.size(); ++i) REQUIRE(frame_values[i] != frame_values[i - 1]);

    // An animated noise also differs frame to frame.
    const json animated = json::parse(
        R"({"nodes": [{"id": "n", "op": "fbm", "params": {"frequency": 3, "animate": 2.0}}], "output": "n"})");
    const TextureResource strip = bake_texture_graph(animated, options);
    REQUIRE(strip.image.get(8, 8).r != strip.image.get(16 + 8, 8).r);
}

TEST_CASE("tiling noise matches across the seam", "[procedural][texture]") {
    constexpr int kSize = 128;
    for (const char* op : {"fbm", "simplex", "perlin", "worley"}) {
        INFO("op = " << op);
        json graph;
        graph["nodes"] = json::array({json{{"id", "n"},
                                           {"op", op},
                                           {"params", {{"frequency", 4}, {"octaves", 3}, {"seed", 1}, {"tile", true}}}}});
        graph["output"] = "n";
        TextureBakeOptions options;
        options.width = kSize;
        options.height = kSize;
        const TextureResource tiled = bake_texture_graph(graph, options);

        double seam_h = 0.0, interior_h = 0.0, seam_v = 0.0, interior_v = 0.0;
        for (int i = 0; i < kSize; ++i) {
            seam_h += std::fabs(tiled.image.get(0, i).r - tiled.image.get(kSize - 1, i).r);
            interior_h += std::fabs(tiled.image.get(0, i).r - tiled.image.get(1, i).r);
            seam_v += std::fabs(tiled.image.get(i, 0).r - tiled.image.get(i, kSize - 1).r);
            interior_v += std::fabs(tiled.image.get(i, 0).r - tiled.image.get(i, 1).r);
        }
        seam_h /= kSize;
        interior_h /= kSize;
        seam_v /= kSize;
        interior_v /= kSize;
        // The wrap-around step must be no worse than an ordinary neighbour step.
        REQUIRE(seam_h < std::max(3.0 * interior_h, 0.05));
        REQUIRE(seam_v < std::max(3.0 * interior_v, 0.05));

        // ...and the untiled version of the same graph does have a visible seam.
        graph["nodes"][0]["params"]["tile"] = false;
        const TextureResource untiled = bake_texture_graph(graph, options);
        double untiled_seam = 0.0;
        for (int i = 0; i < kSize; ++i) {
            untiled_seam += std::fabs(untiled.image.get(0, i).r - untiled.image.get(kSize - 1, i).r);
        }
        untiled_seam /= kSize;
        REQUIRE(untiled_seam > 2.0 * seam_h);
    }
}

TEST_CASE("bake_texture_graph throws on structural problems", "[procedural][texture]") {
    TextureBakeOptions options;
    options.width = 8;
    options.height = 8;

    REQUIRE_THROWS_AS(bake_texture_graph(json::parse(R"({"output": "a"})"), options), Error);
    REQUIRE_THROWS_AS(
        bake_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "constant"}]})"), options), Error);
    REQUIRE_THROWS_AS(
        bake_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "constant"}], "output": "zz"})"), options),
        Error);
    REQUIRE_THROWS_AS(
        bake_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "nope"}], "output": "a"})"), options), Error);
    REQUIRE_THROWS_AS(
        bake_texture_graph(
            json::parse(R"({"nodes": [{"id": "a", "op": "invert", "inputs": {"a": "missing"}}], "output": "a"})"),
            options),
        Error);
    REQUIRE_THROWS_AS(
        bake_texture_graph(
            json::parse(R"({"nodes": [{"id": "a", "op": "invert", "inputs": {"a": "b"}},
                                       {"id": "b", "op": "invert", "inputs": {"a": "a"}}], "output": "a"})"),
            options),
        Error);

    try {
        bake_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "nope"}], "output": "a"})"), options);
        FAIL("expected a throw");
    } catch (const Error& e) {
        REQUIRE(e.code() == "texture_graph");
    }
}

TEST_CASE("validate_texture_graph reports diagnostics instead of throwing", "[procedural][texture]") {
    SECTION("a valid graph is clean") {
        const Diagnostics diag = validate_texture_graph(json::parse(kTexRune));
        INFO(diag.summary());
        REQUIRE(diag.ok());
        REQUIRE(diag.items.empty());
    }

    SECTION("T001 missing nodes") {
        REQUIRE(has_code(validate_texture_graph(json::parse(R"({"output": "a"})")), "T001"));
        REQUIRE(has_code(validate_texture_graph(json::parse(R"({"nodes": [], "output": "a"})")), "T001"));
        REQUIRE(has_code(validate_texture_graph(json::parse(R"({"nodes": [{"op": "constant"}], "output": "a"})")),
                         "T001"));
    }

    SECTION("T002 unknown op") {
        const Diagnostics diag =
            validate_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "wat"}], "output": "a"})"));
        REQUIRE(has_code(diag, "T002"));
        REQUIRE_FALSE(diag.ok());
    }

    SECTION("T003 unresolved input") {
        const Diagnostics diag = validate_texture_graph(json::parse(R"({
            "nodes": [{"id": "a", "op": "constant"},
                      {"id": "b", "op": "invert", "inputs": {"a": "nope"}}],
            "output": "b"})"));
        REQUIRE(has_code(diag, "T003"));
        REQUIRE_FALSE(has_code(diag, "T004"));
        const auto it = std::find_if(diag.items.begin(), diag.items.end(),
                                     [](const Diagnostic& d) { return d.code == "T003"; });
        REQUIRE(it != diag.items.end());
        REQUIRE(it->node.has_value());
        REQUIRE(*it->node == "b");
    }

    SECTION("T004 cycle") {
        const Diagnostics diag = validate_texture_graph(json::parse(R"({
            "nodes": [{"id": "a", "op": "invert", "inputs": {"a": "b"}},
                      {"id": "b", "op": "invert", "inputs": {"a": "c"}},
                      {"id": "c", "op": "invert", "inputs": {"a": "a"}}],
            "output": "a"})"));
        REQUIRE(has_code(diag, "T004"));
    }

    SECTION("T005 missing output") {
        REQUIRE(has_code(validate_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "constant"}]})")),
                         "T005"));
        REQUIRE(has_code(
            validate_texture_graph(json::parse(R"({"nodes": [{"id": "a", "op": "constant"}], "output": "zz"})")),
            "T005"));
    }

    SECTION("T006 bad param type") {
        const Diagnostics diag = validate_texture_graph(json::parse(R"({
            "nodes": [{"id": "a", "op": "fbm", "params": {"frequency": "four"}}], "output": "a"})"));
        REQUIRE(has_code(diag, "T006"));
        const Diagnostics diag2 = validate_texture_graph(json::parse(R"({
            "nodes": [{"id": "a", "op": "gradient_radial", "params": {"falloff": 3}}], "output": "a"})"));
        REQUIRE(has_code(diag2, "T006"));
    }

    SECTION("validation never throws on garbage") {
        REQUIRE_NOTHROW(validate_texture_graph(json::parse("[]")));
        REQUIRE_NOTHROW(validate_texture_graph(json::parse("3")));
        REQUIRE_NOTHROW(validate_texture_graph(json::object()));
    }
}

TEST_CASE("texture_ops_json describes every op name", "[procedural][texture]") {
    const std::vector<std::string> names = texture_op_names();
    const json ops = texture_ops_json();
    REQUIRE(ops.is_array());
    REQUIRE(ops.size() == names.size());
    REQUIRE(names.size() >= 24);

    for (const std::string& name : names) {
        INFO("op = " << name);
        const auto it = std::find_if(ops.begin(), ops.end(),
                                     [&](const json& entry) { return entry.at("op").get<std::string>() == name; });
        REQUIRE(it != ops.end());
        const json& entry = *it;
        REQUIRE(entry.contains("description"));
        REQUIRE(entry.at("description").is_string());
        REQUIRE_FALSE(entry.at("description").get<std::string>().empty());
        REQUIRE(entry.at("params").is_object());
        REQUIRE(entry.at("inputs").is_array());
        for (auto param = entry.at("params").begin(); param != entry.at("params").end(); ++param) {
            REQUIRE(param.value().contains("type"));
            REQUIRE(param.value().contains("default"));
            REQUIRE(param.value().contains("description"));
        }
    }
    // Every op named in docs/VOCABULARY.md is present.
    for (const char* documented :
         {"perlin", "simplex", "worley", "fbm", "curl", "cellular", "blue_noise", "gradient_linear",
          "gradient_radial", "ring", "cracks", "voronoi", "erosion", "distort", "flow_map", "normal_from_height",
          "channel_pack", "levels", "math", "invert", "dissolve_mask", "colorize", "constant", "time"}) {
        INFO(documented);
        REQUIRE(std::find(names.begin(), names.end(), std::string(documented)) != names.end());
    }
}

TEST_CASE("texture op semantics", "[procedural][texture]") {
    TextureBakeOptions options;
    options.width = 32;
    options.height = 32;
    const auto bake = [&](const char* text) { return bake_texture_graph(json::parse(text), options).image; };

    SECTION("constant fills the requested colour") {
        const Image img = bake(R"({"nodes": [{"id": "c", "op": "constant",
                                              "params": {"color": [0.25, 0.5, 0.75, 0.5]}}], "output": "c"})");
        const Color px = img.get(3, 7);
        REQUIRE(px.r == Catch::Approx(0.25f));
        REQUIRE(px.g == Catch::Approx(0.5f));
        REQUIRE(px.b == Catch::Approx(0.75f));
        REQUIRE(px.a == Catch::Approx(0.5f));
    }

    SECTION("gradient_linear ramps along +u at angle 0") {
        const Image img = bake(R"({"nodes": [{"id": "g", "op": "gradient_linear", "params": {}}], "output": "g"})");
        REQUIRE(img.get(0, 16).r < img.get(16, 16).r);
        REQUIRE(img.get(16, 16).r < img.get(31, 16).r);
        REQUIRE(img.get(0, 0).r == Catch::Approx(img.get(0, 31).r));  // constant along v
    }

    SECTION("gradient_radial is 1 inside inner_radius and 0 at radius") {
        const Image img = bake(R"({"nodes": [{"id": "g", "op": "gradient_radial",
                                              "params": {"radius": 0.5, "inner_radius": 0.2}}], "output": "g"})");
        REQUIRE(img.get(16, 16).r == Catch::Approx(1.0f).margin(1e-5));
        REQUIRE(img.get(0, 0).r == Catch::Approx(0.0f).margin(1e-5));
    }

    SECTION("invert flips rgb and alpha") {
        const Image img = bake(R"({"nodes": [{"id": "c", "op": "constant", "params": {"color": [0.25, 0.25, 0.25, 0.25]}},
                                             {"id": "i", "op": "invert", "inputs": {"a": "c"}}], "output": "i"})");
        const Color px = img.get(5, 5);
        REQUIRE(px.r == Catch::Approx(0.75f));
        REQUIRE(px.a == Catch::Approx(0.75f));
    }

    SECTION("math modes") {
        const auto run = [&](const char* mode) {
            std::string text = R"({"nodes": [
                {"id": "x", "op": "constant", "params": {"color": [0.8, 0.8, 0.8, 0.8]}},
                {"id": "y", "op": "constant", "params": {"color": [0.5, 0.5, 0.5, 0.5]}},
                {"id": "m", "op": "math", "params": {"mode": ")";
            text += mode;
            text += R"(", "factor": 0.25}, "inputs": {"a": "x", "b": "y"}}], "output": "m"})";
            return bake_texture_graph(json::parse(text), options).image.get(1, 1).r;
        };
        REQUIRE(run("add") == Catch::Approx(1.3f));
        REQUIRE(run("multiply") == Catch::Approx(0.4f));
        REQUIRE(run("subtract") == Catch::Approx(0.3f));
        REQUIRE(run("max") == Catch::Approx(0.8f));
        REQUIRE(run("min") == Catch::Approx(0.5f));
        REQUIRE(run("screen") == Catch::Approx(0.9f));
        REQUIRE(run("lerp") == Catch::Approx(0.725f));
    }

    SECTION("math with no b input uses the value parameter") {
        const Image img = bake(R"({"nodes": [
            {"id": "x", "op": "constant", "params": {"color": [0.4, 0.4, 0.4, 0.4]}},
            {"id": "m", "op": "math", "params": {"mode": "multiply", "value": 0.5}, "inputs": {"a": "x"}}],
            "output": "m"})");
        REQUIRE(img.get(1, 1).r == Catch::Approx(0.2f));
    }

    SECTION("levels remaps the input range") {
        const Image img = bake(R"({"nodes": [
            {"id": "x", "op": "constant", "params": {"color": [0.5, 0.5, 0.5, 0.5]}},
            {"id": "l", "op": "levels", "params": {"in_low": 0.25, "in_high": 0.75}, "inputs": {"a": "x"}}],
            "output": "l"})");
        REQUIRE(img.get(1, 1).r == Catch::Approx(0.5f));
        REQUIRE(img.get(1, 1).a == Catch::Approx(0.5f));
    }

    SECTION("dissolve_mask thresholds") {
        const Image img = bake(R"({"nodes": [
            {"id": "g", "op": "gradient_linear", "params": {}},
            {"id": "d", "op": "dissolve_mask", "params": {"threshold": 0.5, "softness": 0.02},
             "inputs": {"a": "g"}}], "output": "d"})");
        REQUIRE(img.get(2, 16).r == Catch::Approx(0.0f).margin(1e-5));
        REQUIRE(img.get(29, 16).r == Catch::Approx(1.0f).margin(1e-5));
    }

    SECTION("colorize maps luminance through the gradient and keeps alpha") {
        const Image img = bake(R"({"nodes": [
            {"id": "x", "op": "constant", "params": {"color": [1, 1, 1, 0.3]}},
            {"id": "c", "op": "colorize",
             "params": {"gradient": [[0, [0, 0, 0, 1]], [1, [1, 0, 0, 1]]]}, "inputs": {"a": "x"}}],
            "output": "c"})");
        const Color px = img.get(1, 1);
        REQUIRE(px.r == Catch::Approx(1.0f));
        REQUIRE(px.g == Catch::Approx(0.0f).margin(1e-6));
        REQUIRE(px.a == Catch::Approx(0.3f));
    }

    SECTION("channel_pack packs missing channels as 0 and missing alpha as 1") {
        const Image img = bake(R"({"nodes": [
            {"id": "x", "op": "constant", "params": {"color": [0.5, 0.5, 0.5, 1]}},
            {"id": "p", "op": "channel_pack", "inputs": {"r": "x"}}], "output": "p"})");
        const Color px = img.get(1, 1);
        REQUIRE(px.r == Catch::Approx(0.5f).margin(1e-5));
        REQUIRE(px.g == Catch::Approx(0.0f));
        REQUIRE(px.b == Catch::Approx(0.0f));
        REQUIRE(px.a == Catch::Approx(1.0f));
    }

    SECTION("ring is bright on its radius and dark elsewhere") {
        const Image img = bake(R"({"nodes": [{"id": "r", "op": "ring",
                                              "params": {"radius": 0.25, "thickness": 0.06, "softness": 0.01}}],
                                   "output": "r"})");
        REQUIRE(img.get(16, 16).r == Catch::Approx(0.0f).margin(1e-5));  // hole in the middle
        REQUIRE(img.get(16 + 8, 16).r > 0.9f);                           // on the radius (0.25 * 32 = 8 px)
        REQUIRE(img.get(31, 16).r == Catch::Approx(0.0f).margin(1e-5));  // outside
    }

    SECTION("cracks are thin bright lines on black") {
        const Image big = bake_texture_graph(
            json::parse(R"({"nodes": [{"id": "c", "op": "cracks",
                                       "params": {"density": 6, "width": 0.012, "seed": 11}}], "output": "c"})"),
            TextureBakeOptions{256, 256, 1, 0}).image;
        double bright = 0.0, mean = 0.0;
        for (int y = 0; y < 256; ++y) {
            for (int x = 0; x < 256; ++x) {
                const float v = big.get(x, y).r;
                mean += v;
                if (v > 0.5f) bright += 1.0;
            }
        }
        mean /= 256.0 * 256.0;
        const double bright_fraction = bright / (256.0 * 256.0);
        INFO("mean = " << mean << " bright fraction = " << bright_fraction);
        REQUIRE(mean < 0.25);                // mostly black
        REQUIRE(bright_fraction > 0.0005);   // but there really are cracks
        REQUIRE(bright_fraction < 0.35);
    }

    SECTION("voronoi modes all produce values in [0,1]") {
        for (const char* mode : {"distance", "id", "edges"}) {
            std::string text = R"({"nodes": [{"id": "v", "op": "voronoi", "params": {"cells": 6, "mode": ")";
            text += mode;
            text += R"("}}], "output": "v"})";
            const Image img = bake_texture_graph(json::parse(text), options).image;
            float lo = 1e9f, hi = -1e9f;
            for (int y = 0; y < 32; ++y) {
                for (int x = 0; x < 32; ++x) {
                    lo = std::min(lo, img.get(x, y).r);
                    hi = std::max(hi, img.get(x, y).r);
                }
            }
            INFO(mode);
            REQUIRE(lo >= 0.0f);
            REQUIRE(hi <= 1.0f);
            REQUIRE(hi - lo > 0.3f);
        }
    }

    SECTION("normal_from_height encodes a mostly +Z normal for a flat input") {
        const Image img = bake(R"({"nodes": [
            {"id": "x", "op": "constant", "params": {"color": [0.5, 0.5, 0.5, 1]}},
            {"id": "n", "op": "normal_from_height", "inputs": {"a": "x"}}], "output": "n"})");
        const Color px = img.get(16, 16);
        REQUIRE(px.r == Catch::Approx(0.5f).margin(1e-5));
        REQUIRE(px.g == Catch::Approx(0.5f).margin(1e-5));
        REQUIRE(px.b == Catch::Approx(1.0f).margin(1e-5));
        REQUIRE(px.a == Catch::Approx(1.0f));
    }

    SECTION("blur smooths a hard edge") {
        const Image sharp = bake(R"({"nodes": [{"id": "r", "op": "ring",
                                                "params": {"radius": 0.3, "thickness": 0.05, "softness": 0.0}}],
                                     "output": "r"})");
        const Image soft = bake(R"({"nodes": [
            {"id": "r", "op": "ring", "params": {"radius": 0.3, "thickness": 0.05, "softness": 0.0}},
            {"id": "b", "op": "blur", "params": {"radius": 3}, "inputs": {"a": "r"}}], "output": "b"})");
        const auto variance = [](const Image& img) {
            double mean = 0.0, sq = 0.0;
            const double n = static_cast<double>(img.pixel_count());
            for (int y = 0; y < img.height; ++y) {
                for (int x = 0; x < img.width; ++x) mean += img.get(x, y).r;
            }
            mean /= n;
            for (int y = 0; y < img.height; ++y) {
                for (int x = 0; x < img.width; ++x) {
                    const double d = img.get(x, y).r - mean;
                    sq += d * d;
                }
            }
            return sq / n;
        };
        REQUIRE(variance(soft) < variance(sharp));
    }
}

TEST_CASE("built-in sprites", "[procedural][texture]") {
    const Image soft = default_soft_particle(64);
    REQUIRE(soft.width == 64);
    REQUIRE(soft.height == 64);
    REQUIRE(soft.get(32, 32).a > 0.9f);
    REQUIRE(soft.get(0, 0).a == Catch::Approx(0.0f).margin(1e-5));
    REQUIRE(soft.get(32, 32).r == Catch::Approx(1.0f));

    const Image spark = default_spark(32);
    REQUIRE(spark.width == 32);
    REQUIRE(spark.get(16, 16).a > 0.8f);
    REQUIRE(spark.get(0, 0).a == Catch::Approx(0.0f).margin(1e-5));
    // The spark core is tighter than the soft particle's.
    REQUIRE(spark.get(16 + 8, 16).a < default_soft_particle(32).get(16 + 8, 16).a);

    const Image puff = default_smoke_puff(64, 7);
    REQUIRE(puff.width == 64);
    REQUIRE(puff.get(0, 0).a == Catch::Approx(0.0f).margin(1e-5));
    REQUIRE(default_smoke_puff(64, 7).hash() == puff.hash());
    REQUIRE(default_smoke_puff(64, 8).hash() != puff.hash());
    double alpha_sum = 0.0;
    for (int y = 0; y < 64; ++y) {
        for (int x = 0; x < 64; ++x) alpha_sum += puff.get(x, y).a;
    }
    REQUIRE(alpha_sum > 100.0);  // it is not empty
}

TEST_CASE("image utilities", "[procedural][texture]") {
    SECTION("resample changes size and keeps flat images flat") {
        Image src(8, 8, Color{0.25f, 0.5f, 0.75f, 1.0f});
        const Image out = resample(src, 17, 5);
        REQUIRE(out.width == 17);
        REQUIRE(out.height == 5);
        REQUIRE(out.get(8, 2).r == Catch::Approx(0.25f));
        REQUIRE(out.get(0, 0).b == Catch::Approx(0.75f));
    }

    SECTION("gaussian_blur preserves a constant image and is a no-op at radius 0") {
        Image src(16, 16, Color{0.4f, 0.4f, 0.4f, 1.0f});
        const Image blurred = gaussian_blur(src, 3.0f);
        REQUIRE(blurred.width == 16);
        for (int y = 0; y < 16; ++y) {
            for (int x = 0; x < 16; ++x) REQUIRE(blurred.get(x, y).r == Catch::Approx(0.4f).margin(1e-5));
        }
        REQUIRE(gaussian_blur(src, 0.0f).hash() == src.hash());
    }

    SECTION("gaussian_blur spreads a single bright pixel") {
        Image src(16, 16, Color{0, 0, 0, 0});
        src.set(8, 8, Color{1, 1, 1, 1});
        const Image blurred = gaussian_blur(src, 2.0f);
        REQUIRE(blurred.get(8, 8).r < 1.0f);
        REQUIRE(blurred.get(9, 8).r > 0.0f);
        double before = 0.0, after = 0.0;
        for (int y = 0; y < 16; ++y) {
            for (int x = 0; x < 16; ++x) {
                before += src.get(x, y).r;
                after += blurred.get(x, y).r;
            }
        }
        REQUIRE(after == Catch::Approx(before).margin(1e-3));  // energy preserving
    }

    SECTION("premultiply scales rgb by alpha") {
        Image img(2, 2, Color{1.0f, 0.5f, 0.25f, 0.5f});
        premultiply(img);
        const Color px = img.get(1, 1);
        REQUIRE(px.r == Catch::Approx(0.5f));
        REQUIRE(px.g == Catch::Approx(0.25f));
        REQUIRE(px.b == Catch::Approx(0.125f));
        REQUIRE(px.a == Catch::Approx(0.5f));
    }
}
