// Procedural texture graph evaluation (docs/VOCABULARY.md, "Procedural
// texture graph").
//
// The graph is a DAG of ops; every op produces an RGBA float Image at the bake
// resolution. Evaluation is memoized per frame and fully deterministic: the
// only source of randomness is the integer-hashed noise in noise.cpp, seeded by
// `bake seed + op seed * 7919 + hash(op id)`.
//
// UV convention: u in [0,1] across the width, v in [0,1] down the height,
// sampled at pixel centres ((x + 0.5) / width).
//
// Grayscale ops write the value into r, g, b *and* alpha, so a mask can be fed
// straight to a sprite slot without a channel_pack.
#include "aether/procedural/texture_graph.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/core/value.hpp"
#include "aether/procedural/noise.hpp"

namespace aether::procedural {
namespace {

using nlohmann::json;

// ---------------------------------------------------------------------------
// Op specs (single source of truth for texture_ops_json + validation)
// ---------------------------------------------------------------------------

struct ParamSpec {
    const char* name;
    const char* type;  // float|int|bool|string|vec2|color|gradient
    json default_value;
    const char* description;
};

struct OpSpec {
    const char* name;
    const char* description;
    std::vector<ParamSpec> params;
    std::vector<const char*> inputs;
};

std::vector<ParamSpec> noise_params() {
    return {
        {"frequency", "float", 4.0, "features per unit UV"},
        {"octaves", "int", 3, "fbm/curl octave count"},
        {"lacunarity", "float", 2.0, "frequency multiplier per octave"},
        {"gain", "float", 0.5, "amplitude multiplier per octave"},
        {"seed", "int", 0, "per-op seed, combined with the bake seed"},
        {"animate", "float", 0.0, "time-axis speed; 0 = static"},
        {"tile", "bool", false, "sample a periodic domain so the result tiles seamlessly"},
    };
}

std::vector<OpSpec> build_op_specs() {
    std::vector<OpSpec> ops;
    const auto noise_op = [&](const char* name, const char* desc) {
        ops.push_back(OpSpec{name, desc, noise_params(), {}});
    };
    noise_op("perlin", "Improved Perlin noise remapped to [0,1].");
    noise_op("simplex", "Simplex noise remapped to [0,1].");
    noise_op("worley", "Worley F1 distance, clamped to [0,1].");
    noise_op("fbm", "Fractal sum of simplex octaves remapped to [0,1].");
    noise_op("curl", "Magnitude of the divergence-free curl field, compressed to [0,1) by v/(v+2).");
    noise_op("cellular", "Random constant value per Voronoi cell in [0,1).");
    noise_op("blue_noise", "Hashed value per grid cell (blue-noise-like, not true blue noise).");

    ops.push_back(OpSpec{"gradient_linear", "Linear ramp across the UV square.",
                         {{"angle", "float", 0.0, "ramp direction in degrees (0 = +u)"},
                          {"start", "float", 0.0, "value at the start of the ramp"},
                          {"end", "float", 1.0, "value at the end of the ramp"}},
                         {}});
    ops.push_back(OpSpec{"gradient_radial", "Radial falloff: 1 at the centre, 0 at `radius`.",
                         {{"center", "vec2", json::array({0.5, 0.5}), "centre in UV space"},
                          {"radius", "float", 0.5, "distance at which the value reaches 0"},
                          {"inner_radius", "float", 0.0, "distance held at 1 before the falloff starts"},
                          {"falloff", "string", "smooth", "linear | smooth | quadratic"}},
                         {}});
    ops.push_back(OpSpec{"ring", "Soft annulus centred on the UV square.",
                         {{"radius", "float", 0.4, "ring centreline radius"},
                          {"thickness", "float", 0.05, "full ring width"},
                          {"softness", "float", 0.01, "edge softness"}},
                         {}});
    ops.push_back(OpSpec{"spokes", "Radial lines from the centre (compass / magic-circle spokes).",
                         {{"count", "int", 8, "number of spokes"},
                          {"width", "float", 0.008, "line width in UV units"},
                          {"softness", "float", 0.004, "edge softness"},
                          {"inner_radius", "float", 0.03, "spokes start here"},
                          {"outer_radius", "float", 0.5, "spokes end here"},
                          {"rotation", "float", 0.0, "rotation of the whole set in degrees"}},
                         {}});
    ops.push_back(OpSpec{"star", "Light-flare star: thin rays that taper away from a bright core.",
                         {{"points", "int", 4, "number of rays"},
                          {"width", "float", 0.02, "ray width at the core in UV units"},
                          {"outer_radius", "float", 0.25, "ray length"},
                          {"core_radius", "float", 0.04, "radius of the bright core"},
                          {"softness", "float", 0.006, "edge softness"},
                          {"rotation", "float", 0.0, "rotation in degrees"}},
                         {}});
    ops.push_back(OpSpec{
        "flame",
        "Animated flame slice: heat in [0,1] for one licking tongue of fire, meant to be baked with "
        "frames: 16-32 and coloured by a material temperature_gradient or a `colorize` op (feed it "
        "through `levels` first to pick the silhouette). Recipe, with v = 0 at the top so h = 1-v is the "
        "height above the base: a vertical profile base = smoothstep(0, 0.15, h) * "
        "(1 - smoothstep(0.55, 1, h))^1.5 and a horizontal mask 1 - ((2u-1)/half_width)^2 whose "
        "half_width starts at `width` and narrows to a point towards the top; a vertically stretched, "
        "domain-warped fbm n = fbm(u*frequency + wu, v*frequency*0.6 + wv) supplies the tongues, where "
        "(wu, wv) is a coarser second fbm scaled by `warp`. The same (wu, wv) and n also displace h and "
        "u before the profile and the mask are evaluated, so the silhouette itself leans and frays "
        "instead of staying a smooth cone. heat = base * mask * (0.18 + 0.82*n), with the lower centre "
        "held near full heat so it reads as a white-hot core, the tips broken into separate licks by "
        "1 - smoothstep(threshold) on a third, finer noise (`licks`), and the result raised to "
        "`sharpness`. Every noise axis that carries time is wrapped onto a circle whose circumference is "
        "the distance scrolled in one loop, so the flipbook loops seamlessly: frame 0 continues the last "
        "frame. Fire ramp for `colorize` (black -> deep red -> orange -> yellow -> white): "
        "[[0,[0.05,0,0,1]], [0.25,[0.8,0.1,0,1]], [0.5,[1,0.45,0.05,1]], [0.75,[1,0.85,0.35,1]], "
        "[1,[1,1,0.85,1]]].",
        {{"frequency", "float", 3.0, "tongue detail across the tile"},
         {"speed", "float", 1.0, "upward scroll over one loop; the flipbook always closes"},
         {"warp", "float", 0.35, "domain warp strength; 0 = straight, unleaning tongues"},
         {"width", "float", 0.9, "flame half width at the base, in half-tiles"},
         {"sharpness", "float", 1.6, "contrast exponent; higher = thinner, hotter cores"},
         {"licks", "float", 0.4, "how much the tips break up into separate tongues"},
         {"seed", "int", 0, "per-op seed"}},
        {}});
    ops.push_back(OpSpec{"cracks", "Thin bright lines along Worley cell boundaries on black.",
                         {{"density", "float", 5.0, "cells per unit UV"},
                          {"width", "float", 0.01, "crack width in UV units"},
                          {"seed", "int", 0, "per-op seed"}},
                         {}});
    ops.push_back(OpSpec{"voronoi", "Voronoi cells.",
                         {{"cells", "float", 8.0, "cells per unit UV"},
                          {"mode", "string", "distance", "distance | id | edges"},
                          {"seed", "int", 0, "per-op seed"}},
                         {}});
    ops.push_back(OpSpec{"erosion", "Edge erosion of `a` driven by a noise input (or an internal fbm).",
                         {{"amount", "float", 0.5, "erosion threshold in [0,1]"},
                          {"frequency", "float", 6.0, "internal fbm frequency when no noise input is given"},
                          {"seed", "int", 0, "per-op seed for the internal fbm"}},
                         {"a", "noise"}});
    ops.push_back(OpSpec{"distort", "Offsets `a` by the r/g channels of `by`, centred at 0.5.",
                         {{"amount", "float", 0.1, "maximum offset in UV units"}},
                         {"a", "by"}});
    ops.push_back(OpSpec{"flow_map", "Per-pixel central-difference gradient of `a`, encoded as rg = 0.5 + 0.5*grad.",
                         {}, {"a"}});
    ops.push_back(OpSpec{"normal_from_height", "Tangent-space normal from `a` as a height field, encoded 0.5 + 0.5*n.",
                         {{"strength", "float", 1.0, "slope multiplier (per-pixel differences)"}},
                         {"a"}});
    ops.push_back(OpSpec{"channel_pack", "Packs the selected channel of each input into r/g/b/a.",
                         {{"channel", "string", "luminance", "luminance | r | g | b | a"}},
                         {"r", "g", "b", "a"}});
    ops.push_back(OpSpec{"levels", "Input/output range remap with gamma, applied to rgb and alpha.",
                         {{"in_low", "float", 0.0, "input black point"},
                          {"in_high", "float", 1.0, "input white point"},
                          {"out_low", "float", 0.0, "output black point"},
                          {"out_high", "float", 1.0, "output white point"},
                          {"gamma", "float", 1.0, "midtone gamma; output = t^(1/gamma)"}},
                         {"a"}});
    ops.push_back(OpSpec{"math", "Per-channel binary math between `a` and `b`.",
                         {{"mode", "string", "multiply", "add | multiply | subtract | max | min | screen | lerp"},
                          {"factor", "float", 0.5, "lerp t (only used by mode = lerp)"},
                          {"value", "float", 1.0, "constant used for `b` when no b input is connected"}},
                         {"a", "b"}});
    ops.push_back(OpSpec{"invert", "1 - value on rgb and alpha.", {}, {"a"}});
    ops.push_back(OpSpec{"dissolve_mask", "smoothstep(threshold - softness, threshold + softness, a).",
                         {{"threshold", "float", 0.5, "dissolve threshold"},
                          {"softness", "float", 0.05, "edge softness"}},
                         {"a"}});
    ops.push_back(OpSpec{"colorize", "Maps the luminance of `a` through a gradient; keeps a's alpha.",
                         {{"gradient", "gradient", json::array({json::array({0.0, json::array({0.0, 0.0, 0.0, 1.0})}),
                                                                json::array({1.0, json::array({1.0, 1.0, 1.0, 1.0})})}),
                           "[[t,[r,g,b,a]], ...]"}},
                         {"a"}});
    ops.push_back(OpSpec{"constant", "Solid colour.",
                         {{"color", "color", json::array({1.0, 1.0, 1.0, 1.0}), "linear rgba"}},
                         {}});
    ops.push_back(OpSpec{"time", "Animation phase: fract(time * speed) as a flat grayscale value.",
                         {{"speed", "float", 1.0, "cycles over the baked frame range"}},
                         {}});
    ops.push_back(OpSpec{"blur", "Separable Gaussian blur of `a` (sigma = radius/2, clamped edges).",
                         {{"radius", "float", 2.0, "blur radius in pixels"}},
                         {"a"}});
    return ops;
}

const OpSpec* find_op_spec(const std::vector<OpSpec>& ops, std::string_view name) {
    for (const OpSpec& o : ops) {
        if (name == o.name) return &o;
    }
    return nullptr;
}

// ---------------------------------------------------------------------------
// Parameter access
// ---------------------------------------------------------------------------

const json& params_of(const json& node) {
    static const json kEmpty = json::object();
    auto it = node.find("params");
    if (it == node.end() || !it->is_object()) return kEmpty;
    return *it;
}

float param_float(const json& p, const char* key, float fallback) {
    auto it = p.find(key);
    if (it == p.end() || !it->is_number()) return fallback;
    return it->get<float>();
}
int param_int(const json& p, const char* key, int fallback) {
    auto it = p.find(key);
    if (it == p.end() || !it->is_number()) return fallback;
    return static_cast<int>(std::lround(it->get<double>()));
}
bool param_bool(const json& p, const char* key, bool fallback) {
    auto it = p.find(key);
    if (it == p.end()) return fallback;
    if (it->is_boolean()) return it->get<bool>();
    if (it->is_number()) return it->get<double>() != 0.0;
    return fallback;
}
std::string param_string(const json& p, const char* key, const char* fallback) {
    auto it = p.find(key);
    if (it == p.end() || !it->is_string()) return std::string(fallback);
    return it->get<std::string>();
}
Vec2 param_vec2(const json& p, const char* key, Vec2 fallback) {
    auto it = p.find(key);
    if (it == p.end() || !it->is_array() || it->size() < 2) return fallback;
    const json& a = *it;
    if (!a[0].is_number() || !a[1].is_number()) return fallback;
    return {a[0].get<float>(), a[1].get<float>()};
}
Color param_color(const json& p, const char* key, Color fallback) {
    auto it = p.find(key);
    if (it == p.end() || !it->is_array() || it->size() < 3) return fallback;
    const json& a = *it;
    for (size_t i = 0; i < a.size() && i < 4; ++i) {
        if (!a[i].is_number()) return fallback;
    }
    return {a[0].get<float>(), a[1].get<float>(), a[2].get<float>(),
            a.size() > 3 ? a[3].get<float>() : 1.0f};
}

Color color_from_json(const json& a) {
    Color c{0, 0, 0, 1};
    for (size_t i = 0; i < a.size() && i < 4; ++i) {
        if (a[i].is_number()) c[static_cast<int>(i)] = a[i].get<float>();
    }
    return c;
}

// Accepts [[t,[r,g,b,a]], ...] and [{"t": t, "color": [r,g,b,a]}, ...].
Gradient param_gradient(const json& p, const char* key) {
    Gradient g;
    auto it = p.find(key);
    if (it != p.end() && it->is_array()) {
        for (const json& k : *it) {
            if (k.is_array() && k.size() >= 2 && k[0].is_number() && k[1].is_array()) {
                g.keys.push_back(GradientKey{k[0].get<float>(), color_from_json(k[1])});
            } else if (k.is_object()) {
                const auto t_it = k.find("t");
                const auto c_it = k.find("color");
                if (t_it != k.end() && t_it->is_number() && c_it != k.end() && c_it->is_array()) {
                    g.keys.push_back(GradientKey{t_it->get<float>(), color_from_json(*c_it)});
                }
            }
        }
    }
    if (g.keys.empty()) {
        g.keys.push_back(GradientKey{0.0f, Color{0, 0, 0, 1}});
        g.keys.push_back(GradientKey{1.0f, Color{1, 1, 1, 1}});
    }
    std::stable_sort(g.keys.begin(), g.keys.end(),
                     [](const GradientKey& a, const GradientKey& b) { return a.t < b.t; });
    return g;
}

// ---------------------------------------------------------------------------
// Small image helpers
// ---------------------------------------------------------------------------

inline float luminance_of(Color c) { return c.luminance(); }

// Edge-safe smoothstep with an explicit half-width.
inline float soft_edge(float edge, float softness, float x) {
    const float s = std::fabs(softness);
    if (s < 1e-6f) return x < edge ? 1.0f : 0.0f;
    return 1.0f - smoothstep(edge - s, edge + s, x);
}

inline void set_gray(Image& img, int x, int y, float v) { img.set(x, y, Color{v, v, v, v}); }

struct Ctx {
    int width = 0;
    int height = 0;
    float time = 0.0f;
    uint32_t seed = 0;
};

uint32_t op_seed_of(const Ctx& ctx, const json& p, const std::string& id) {
    const uint32_t local = static_cast<uint32_t>(param_int(p, "seed", 0));
    return ctx.seed + local * 7919u + static_cast<uint32_t>(fnv1a_64(id));
}

// UV of a pixel centre.
inline float uv_u(const Ctx& ctx, int x) { return (static_cast<float>(x) + 0.5f) / static_cast<float>(ctx.width); }
inline float uv_v(const Ctx& ctx, int y) { return (static_cast<float>(y) + 0.5f) / static_cast<float>(ctx.height); }

// Periodic embedding of the unit UV square onto a 4D torus: any lattice noise
// sampled here is exactly periodic in u and v. Adding a constant (the time
// offset) to every component keeps that periodicity.
inline Vec4 torus_point(float u, float v, float freq, float t_offset) {
    const float r = freq / kTwoPi;
    const float au = u * kTwoPi;
    const float av = v * kTwoPi;
    return {r * std::cos(au) + t_offset, r * std::sin(au) + t_offset,
            r * std::cos(av) + t_offset, r * std::sin(av) + t_offset};
}

// ---------------------------------------------------------------------------
// Noise ops
// ---------------------------------------------------------------------------

enum class NoiseOp { Perlin, Simplex, Worley, Fbm, Curl, Cellular, BlueNoise };

Image eval_noise_op(NoiseOp op, const json& p, const Ctx& ctx, uint32_t seed) {
    const float frequency = param_float(p, "frequency", 4.0f);
    const bool tile = param_bool(p, "tile", false);
    const float animate = param_float(p, "animate", 0.0f);
    const float t_axis = animate * ctx.time;
    FbmParams fbm;
    fbm.octaves = param_int(p, "octaves", 3);
    fbm.lacunarity = param_float(p, "lacunarity", 2.0f);
    fbm.gain = param_float(p, "gain", 0.5f);
    fbm.basis = NoiseBasis::Simplex;

    // blue_noise quantizes to a grid; tiling wraps that grid modulo the cell count.
    const int tile_cells = std::max(1, static_cast<int>(std::lround(frequency)));

    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const Vec3 p3{u * frequency, v * frequency, t_axis};
            const Vec4 p4 = torus_point(u, v, frequency, t_axis);
            float value = 0.0f;
            switch (op) {
                case NoiseOp::Perlin:
                    value = 0.5f + 0.5f * (tile ? perlin4(p4, seed) : perlin3(p3, seed));
                    break;
                case NoiseOp::Simplex:
                    value = 0.5f + 0.5f * (tile ? simplex4(p4, seed) : simplex3(p3, seed));
                    break;
                case NoiseOp::Fbm:
                    value = 0.5f + 0.5f * (tile ? fbm4(p4, seed, fbm) : fbm3(p3, seed, fbm));
                    break;
                case NoiseOp::Worley:
                    value = saturate(tile ? worley4(p4, seed) : worley3(p3, seed));
                    break;
                case NoiseOp::Cellular:
                    value = tile ? cellular4(p4, seed) : cellular3(p3, seed);
                    break;
                case NoiseOp::Curl: {
                    // The curl is taken in the sampling domain, so the magnitude is
                    // independent of `frequency`; v/(v+2) compresses it to [0,1).
                    const Vec3 c = tile ? curl4(p4.xyz(), p4.w, seed, fbm) : curl3(p3, seed, fbm);
                    const float m = length(c);
                    value = m / (m + 2.0f);
                    break;
                }
                case NoiseOp::BlueNoise: {
                    if (tile) {
                        const int cx = ((static_cast<int>(std::floor(u * static_cast<float>(tile_cells))) % tile_cells) + tile_cells) % tile_cells;
                        const int cy = ((static_cast<int>(std::floor(v * static_cast<float>(tile_cells))) % tile_cells) + tile_cells) % tile_cells;
                        value = hash_noise3(Vec3{static_cast<float>(cx), static_cast<float>(cy), std::floor(t_axis)}, seed);
                    } else {
                        value = hash_noise3(Vec3{std::floor(p3.x), std::floor(p3.y), std::floor(p3.z)}, seed);
                    }
                    break;
                }
            }
            set_gray(out, x, y, saturate(value));
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// Pattern ops
// ---------------------------------------------------------------------------

Image eval_gradient_linear(const json& p, const Ctx& ctx) {
    const float angle = deg_to_rad(param_float(p, "angle", 0.0f));
    const float start = param_float(p, "start", 0.0f);
    const float end = param_float(p, "end", 1.0f);
    const float dx = std::cos(angle), dy = std::sin(angle);
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const float t = saturate(0.5f + (u - 0.5f) * dx + (v - 0.5f) * dy);
            set_gray(out, x, y, lerp(start, end, t));
        }
    }
    return out;
}

Image eval_gradient_radial(const json& p, const Ctx& ctx) {
    const Vec2 center = param_vec2(p, "center", Vec2{0.5f, 0.5f});
    const float radius = param_float(p, "radius", 0.5f);
    const float inner = param_float(p, "inner_radius", 0.0f);
    const std::string falloff = param_string(p, "falloff", "smooth");
    const float span = radius - inner;
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const float d = length(Vec2{u, v} - center);
            float t = span > 1e-6f ? saturate((d - inner) / span) : (d <= inner ? 0.0f : 1.0f);
            float value;
            if (falloff == "linear") {
                value = 1.0f - t;
            } else if (falloff == "quadratic") {
                value = (1.0f - t) * (1.0f - t);
            } else {  // smooth
                value = 1.0f - t * t * (3.0f - 2.0f * t);
            }
            set_gray(out, x, y, saturate(value));
        }
    }
    return out;
}

Image eval_ring(const json& p, const Ctx& ctx) {
    const float radius = param_float(p, "radius", 0.4f);
    const float thickness = param_float(p, "thickness", 0.05f);
    const float softness = param_float(p, "softness", 0.01f);
    const float half = std::fabs(thickness) * 0.5f;
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const float d = length(Vec2{u - 0.5f, v - 0.5f});
            set_gray(out, x, y, saturate(soft_edge(half, softness, std::fabs(d - radius))));
        }
    }
    return out;
}

// Distance from (dx, dy) to the nearest of `count` rays leaving the origin (evenly spaced).
float nearest_spoke_distance(float dx, float dy, int count, float rotation_rad) {
    const float r = std::sqrt(dx * dx + dy * dy);
    if (r < 1e-6f || count <= 0) return 0.0f;
    const float step = kTwoPi / static_cast<float>(count);
    float a = std::atan2(dy, dx) - rotation_rad;
    a = a - std::floor(a / step) * step;                    // [0, step)
    const float off = std::min(a, step - a);                 // angular distance to the nearest ray
    return off >= kHalfPi ? r : r * std::sin(off);           // behind the ray's origin: full distance
}

Image eval_spokes(const json& p, const Ctx& ctx) {
    const int count = std::max(1, param_int(p, "count", 8));
    const float width = std::max(param_float(p, "width", 0.008f), 0.0f);
    const float softness = std::max(param_float(p, "softness", 0.004f), 0.0f);
    const float inner = std::max(param_float(p, "inner_radius", 0.03f), 0.0f);
    const float outer = std::max(param_float(p, "outer_radius", 0.5f), inner);
    const float rot = deg_to_rad(param_float(p, "rotation", 0.0f));
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const float dx = u - 0.5f, dy = v - 0.5f;
            const float r = std::sqrt(dx * dx + dy * dy);
            float value = 0.0f;
            if (r >= inner && r <= outer) {
                const float d = nearest_spoke_distance(dx, dy, count, rot);
                value = soft_edge(width * 0.5f, softness, d);
                // soft ends
                value *= smoothstep(inner, inner + softness * 2.0f + 0.004f, r) * (1.0f - smoothstep(outer - softness * 2.0f - 0.004f, outer, r));
            }
            set_gray(out, x, y, saturate(value));
        }
    }
    return out;
}

Image eval_star(const json& p, const Ctx& ctx) {
    const int points = std::max(1, param_int(p, "points", 4));
    const float width = std::max(param_float(p, "width", 0.02f), 0.0f);
    const float outer = std::max(param_float(p, "outer_radius", 0.25f), 0.001f);
    const float core = std::max(param_float(p, "core_radius", 0.04f), 0.0f);
    const float softness = std::max(param_float(p, "softness", 0.006f), 0.0f);
    const float rot = deg_to_rad(param_float(p, "rotation", 0.0f));
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const float dx = u - 0.5f, dy = v - 0.5f;
            const float r = std::sqrt(dx * dx + dy * dy);
            float value = 0.0f;
            if (r <= outer) {
                const float taper = 1.0f - r / outer;              // rays thin and dim towards the tips
                const float half = width * 0.5f * (0.25f + 0.75f * taper);
                const float d = nearest_spoke_distance(dx, dy, points, rot);
                value = soft_edge(half, softness, d) * std::pow(taper, 0.7f);
            }
            if (core > 0.0f) {
                const float glow = 1.0f - smoothstep(0.0f, core, r);
                value = std::max(value, glow * glow);
            }
            set_gray(out, x, y, saturate(value));
        }
    }
    return out;
}

Image eval_cracks(const json& p, const Ctx& ctx, uint32_t seed) {
    const float density = std::max(param_float(p, "density", 5.0f), 0.0001f);
    const float width = std::max(param_float(p, "width", 0.01f), 0.0f);
    // The Worley domain is `density` cells per unit UV, so a UV width becomes
    // width * density in cell units.
    const float w_cells = std::max(width * density, 1e-4f);
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            float f2 = 0.0f;
            const float f1 = worley2(Vec2{u * density, v * density}, seed, &f2);
            const float edge = f2 - f1;
            set_gray(out, x, y, saturate(1.0f - smoothstep(w_cells * 0.5f, w_cells * 1.5f, edge)));
        }
    }
    return out;
}

Image eval_voronoi(const json& p, const Ctx& ctx, uint32_t seed) {
    const float cells = std::max(param_float(p, "cells", 8.0f), 0.0001f);
    const std::string mode = param_string(p, "mode", "distance");
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            const Vec2 q{u * cells, v * cells};
            float value;
            if (mode == "id") {
                value = cellular2(q, seed);
            } else if (mode == "edges") {
                float f2 = 0.0f;
                const float f1 = worley2(q, seed, &f2);
                value = 1.0f - smoothstep(0.0f, 0.15f, f2 - f1);
            } else {  // distance
                value = saturate(worley2(q, seed));
            }
            set_gray(out, x, y, saturate(value));
        }
    }
    return out;
}

// Animated flame slice in [0,1] "heat" values. See the op description for the recipe.
//
// Looping: every noise layer that moves with time takes its vertical coordinate
// `y - t * rate` and wraps it onto a circle of circumference `rate`, so one whole
// loop rotates that circle by exactly 2*pi and the field returns to itself. The
// texture therefore tiles in time without a cross-fade.
Image eval_flame(const json& p, const Ctx& ctx, uint32_t seed) {
    const float frequency = std::max(0.01f, param_float(p, "frequency", 3.0f));
    const float speed = std::max(0.01f, param_float(p, "speed", 1.0f));
    const float warp = param_float(p, "warp", 0.35f);
    const float width = std::max(0.05f, param_float(p, "width", 0.9f));
    const float sharpness = std::max(0.01f, param_float(p, "sharpness", 1.6f));
    const float licks = saturate(param_float(p, "licks", 0.4f));

    constexpr float kProfileExponent = 1.5f;   // how hard the flame fades out towards its tip
    constexpr float kTipNarrowing = 0.92f;     // fraction of the width lost between base and tip
    constexpr float kRise = 0.35f;             // how far the noise displaces the silhouette vertically
    constexpr float kStretch = 0.6f;           // vertical noise scale; < 1 elongates the tongues upward
    constexpr float kLean = 0.35f;             // how much the warp layer lifts/drops whole tongues
    constexpr float kCore = 0.55f;             // how strongly the lower centre is held at full heat

    FbmParams body;
    body.octaves = 4;
    FbmParams coarse;
    coarse.octaves = 2;
    FbmParams tips;
    tips.octaves = 3;

    const float t = ctx.time;
    // One looping noise layer: `y` is the static vertical coordinate, `rate` both the
    // scroll speed and the circumference that closes the loop.
    const auto layer = [t](float x, float y, float rate, uint32_t s, const FbmParams& fp) {
        const float r = std::max(1e-4f, rate) / kTwoPi;
        const float theta = (y + t * rate) / r;
        return fbm4(Vec4{x, r * std::cos(theta), r * std::sin(theta), 0.0f}, s, fp);
    };

    const float rate = speed * 2.0f;
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        // The flame root sits at v = 0. The renderer's sprite V axis points up the screen
        // (v = 1 is drawn at the top), so a flame stored root-first renders upright, and on a
        // stretched_billboard its tip points along the particle's velocity.
        const float height = uv_v(ctx, y);  // 0 at the root of the flame, 1 at the far tip
        const float vy = 1.0f - height;     // noise coordinate, so the field scrolls root -> tip
        // The base of the flame stays solid; the displacement ramps in with height so only the
        // tongues wander.
        const float loose = smoothstep(0.04f, 0.45f, height);
        const float tip = smoothstep(0.35f, 0.95f, height);
        const float lick_cut = 1.2f - (licks + 0.2f) * tip;
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            // Domain warp: a coarser, slower layer bends the tongues sideways.
            const float wx = u * frequency * 0.45f;
            const float wy = vy * frequency * 0.3f;
            const float wu = warp * layer(wx + 11.3f, wy, rate * 0.6f, seed + 101u, coarse);
            const float wv = warp * layer(wx - 7.1f, wy, rate * 0.6f, seed + 257u, coarse);
            const float n01 =
                saturate(0.5f + 0.5f * layer(u * frequency + wu, vy * frequency * kStretch + wv, rate, seed, body));

            // The same fields displace the silhouette, so the profile and the mask carve ragged,
            // leaning tongues instead of a smooth cone.
            const float uu = u + loose * wu / frequency;
            const float hh = height + loose * (kRise * (0.5f - n01) + kLean * wv / frequency);
            const float base = smoothstep(0.0f, 0.15f, hh) *
                               std::pow(std::max(0.0f, 1.0f - smoothstep(0.55f, 1.0f, hh)), kProfileExponent);
            const float half_width = std::max(0.06f, width * (1.0f - kTipNarrowing * std::pow(saturate(hh), 1.3f)));
            const float xr = (2.0f * uu - 1.0f) / half_width;
            const float mask = saturate(1.0f - xr * xr);
            if (base <= 0.0f || mask <= 0.0f) {
                set_gray(out, x, y, 0.0f);
                continue;
            }
            // Third noise, faster and finer: breaks the tips into separate licks.
            const float l = 0.5f + 0.5f * layer(u * frequency * 1.6f, vy * frequency * 1.0f,
                                                rate * 1.35f, seed + 613u, tips);
            const float lick = 1.0f - smoothstep(lick_cut - 0.2f, lick_cut + 0.2f, 1.0f - l);
            // The lower centre is the core: hold it near full heat so it reads white-hot once
            // a fire ramp is applied, and let the noise own the edges and the tips.
            const float core = mask * mask * mask * (1.0f - smoothstep(0.02f, 0.5f, height));
            const float hot = lerp(n01, 1.0f, kCore * core);
            const float heat = base * mask * (0.18f + 0.82f * hot) * lick;
            set_gray(out, x, y, std::pow(saturate(heat), sharpness));
        }
    }
    return out;
}

Image eval_constant(const json& p, const Ctx& ctx) {
    Image out(ctx.width, ctx.height);
    out.fill(param_color(p, "color", Color{1, 1, 1, 1}));
    return out;
}

Image eval_time(const json& p, const Ctx& ctx) {
    const float speed = param_float(p, "speed", 1.0f);
    const float value = saturate(fract(ctx.time * speed));
    Image out(ctx.width, ctx.height);
    out.fill(Color{value, value, value, value});
    return out;
}

// ---------------------------------------------------------------------------
// Filter ops
// ---------------------------------------------------------------------------

Image white_image(const Ctx& ctx) {
    Image out(ctx.width, ctx.height);
    out.fill(Color{1, 1, 1, 1});
    return out;
}

using InputMap = std::map<std::string, const Image*>;

const Image* find_input(const InputMap& inputs, const char* port) {
    auto it = inputs.find(port);
    return it == inputs.end() ? nullptr : it->second;
}

Image eval_levels(const json& p, const Ctx& ctx, const Image& a) {
    const float in_low = param_float(p, "in_low", 0.0f);
    const float in_high = param_float(p, "in_high", 1.0f);
    const float out_low = param_float(p, "out_low", 0.0f);
    const float out_high = param_float(p, "out_high", 1.0f);
    const float gamma = param_float(p, "gamma", 1.0f);
    const float span = in_high - in_low;
    const float inv_gamma = gamma > 1e-6f ? 1.0f / gamma : 1.0f;
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            Color c = a.get(x, y);
            for (int i = 0; i < 4; ++i) {
                float t = std::fabs(span) > 1e-6f ? saturate((c[i] - in_low) / span) : (c[i] >= in_high ? 1.0f : 0.0f);
                if (inv_gamma != 1.0f) t = std::pow(t, inv_gamma);
                c[i] = lerp(out_low, out_high, t);
            }
            out.set(x, y, c);
        }
    }
    return out;
}

Image eval_math(const json& p, const Ctx& ctx, const Image& a, const Image* b) {
    const std::string mode = param_string(p, "mode", "multiply");
    const float factor = param_float(p, "factor", 0.5f);
    const float constant_value = param_float(p, "value", 1.0f);
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            const Color ca = a.get(x, y);
            const Color cb = b ? b->get(x, y) : Color{constant_value, constant_value, constant_value, constant_value};
            Color r;
            for (int i = 0; i < 4; ++i) {
                const float va = ca[i], vb = cb[i];
                float value;
                if (mode == "add") value = va + vb;
                else if (mode == "subtract") value = va - vb;
                else if (mode == "max") value = std::max(va, vb);
                else if (mode == "min") value = std::min(va, vb);
                else if (mode == "screen") value = 1.0f - (1.0f - va) * (1.0f - vb);
                else if (mode == "lerp") value = lerp(va, vb, factor);
                else value = va * vb;  // multiply
                r[i] = value;
            }
            out.set(x, y, r);
        }
    }
    return out;
}

Image eval_invert(const Ctx& ctx, const Image& a) {
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            const Color c = a.get(x, y);
            out.set(x, y, Color{1.0f - c.r, 1.0f - c.g, 1.0f - c.b, 1.0f - c.a});
        }
    }
    return out;
}

Image eval_dissolve_mask(const json& p, const Ctx& ctx, const Image& a) {
    const float threshold = param_float(p, "threshold", 0.5f);
    const float softness = param_float(p, "softness", 0.05f);
    const float lo = threshold - softness;
    const float hi = threshold + softness;
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            Color c = a.get(x, y);
            for (int i = 0; i < 4; ++i) {
                c[i] = std::fabs(hi - lo) < 1e-6f ? (c[i] >= threshold ? 1.0f : 0.0f) : smoothstep(lo, hi, c[i]);
            }
            out.set(x, y, c);
        }
    }
    return out;
}

Image eval_colorize(const json& p, const Ctx& ctx, const Image& a) {
    const Gradient gradient = param_gradient(p, "gradient");
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            const Color c = a.get(x, y);
            const Color mapped = gradient.eval(saturate(luminance_of(c)));
            out.set(x, y, Color{mapped.r, mapped.g, mapped.b, c.a});
        }
    }
    return out;
}

Image eval_erosion(const json& p, const Ctx& ctx, const Image& a, const Image* noise_input, uint32_t seed) {
    const float amount = param_float(p, "amount", 0.5f);
    Image internal_noise;
    if (noise_input == nullptr) {
        json internal = json::object();
        internal["frequency"] = param_float(p, "frequency", 6.0f);
        internal["octaves"] = 3;
        internal_noise = eval_noise_op(NoiseOp::Fbm, internal, ctx, seed);
    }
    const Image& n = noise_input ? *noise_input : internal_noise;
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            Color c = a.get(x, y);
            const float nv = luminance_of(n.get(x, y));
            for (int i = 0; i < 4; ++i) {
                c[i] = smoothstep(amount - 0.1f, amount + 0.1f, c[i] - nv * amount);
            }
            out.set(x, y, c);
        }
    }
    return out;
}

Image eval_distort(const json& p, const Ctx& ctx, const Image& a, const Image* by) {
    const float amount = param_float(p, "amount", 0.1f);
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float v = uv_v(ctx, y);
        for (int x = 0; x < ctx.width; ++x) {
            const float u = uv_u(ctx, x);
            float ox = 0.0f, oy = 0.0f;
            if (by) {
                const Color d = by->get(x, y);
                ox = (d.r - 0.5f) * amount;
                oy = (d.g - 0.5f) * amount;
            }
            out.set(x, y, a.sample(u + ox, v + oy, WrapMode::Clamp));
        }
    }
    return out;
}

Image eval_flow_map(const Ctx& ctx, const Image& a) {
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            const float gx = (luminance_of(a.fetch(x + 1, y)) - luminance_of(a.fetch(x - 1, y))) * 0.5f;
            const float gy = (luminance_of(a.fetch(x, y + 1)) - luminance_of(a.fetch(x, y - 1))) * 0.5f;
            out.set(x, y, Color{saturate(0.5f + 0.5f * gx), saturate(0.5f + 0.5f * gy), 0.0f, 1.0f});
        }
    }
    return out;
}

Image eval_normal_from_height(const json& p, const Ctx& ctx, const Image& a) {
    const float strength = param_float(p, "strength", 1.0f);
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            const float dhdx = (luminance_of(a.fetch(x + 1, y)) - luminance_of(a.fetch(x - 1, y))) * 0.5f * strength;
            const float dhdy = (luminance_of(a.fetch(x, y + 1)) - luminance_of(a.fetch(x, y - 1))) * 0.5f * strength;
            const Vec3 n = normalize(Vec3{-dhdx, -dhdy, 1.0f});
            out.set(x, y, Color{0.5f + 0.5f * n.x, 0.5f + 0.5f * n.y, 0.5f + 0.5f * n.z, 1.0f});
        }
    }
    return out;
}

float select_channel(Color c, const std::string& channel) {
    if (channel == "r") return c.r;
    if (channel == "g") return c.g;
    if (channel == "b") return c.b;
    if (channel == "a") return c.a;
    return luminance_of(c);
}

Image eval_channel_pack(const json& p, const Ctx& ctx, const InputMap& inputs) {
    const std::string channel = param_string(p, "channel", "luminance");
    const Image* src[4] = {find_input(inputs, "r"), find_input(inputs, "g"), find_input(inputs, "b"),
                           find_input(inputs, "a")};
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            Color c{0, 0, 0, 1};
            for (int i = 0; i < 4; ++i) {
                if (src[i]) c[i] = select_channel(src[i]->get(x, y), channel);
                else c[i] = (i == 3) ? 1.0f : 0.0f;
            }
            out.set(x, y, c);
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// Graph structure
// ---------------------------------------------------------------------------

struct GraphNode {
    std::string id;
    std::string op;
    const json* node = nullptr;
    std::vector<std::pair<std::string, std::string>> inputs;  // port -> node id
};

struct ParsedGraph {
    std::vector<GraphNode> nodes;  // declaration order
    std::map<std::string, size_t> by_id;
    std::string output;
};

void collect_inputs(const json& node, std::vector<std::pair<std::string, std::string>>& out) {
    auto it = node.find("inputs");
    if (it == node.end() || !it->is_object()) return;
    for (auto entry = it->begin(); entry != it->end(); ++entry) {
        if (entry.value().is_string()) {
            out.emplace_back(entry.key(), entry.value().get<std::string>());
        } else if (entry.value().is_array() && !entry.value().empty() && entry.value()[0].is_string()) {
            // Tolerate the effect-graph "list of refs" form; the texture graph
            // only ever consumes the first reference.
            out.emplace_back(entry.key(), entry.value()[0].get<std::string>());
        }
    }
}

// Structural parse. `strict` throws; otherwise problems are skipped so the
// validator can report all of them at once.
ParsedGraph parse_graph(const json& graph, bool strict) {
    ParsedGraph g;
    if (!graph.is_object()) {
        if (strict) throw Error("texture_graph", "texture graph must be a JSON object");
        return g;
    }
    auto nodes_it = graph.find("nodes");
    if (nodes_it == graph.end() || !nodes_it->is_array() || nodes_it->empty()) {
        if (strict) throw Error("texture_graph", "texture graph has no \"nodes\" array");
        return g;
    }
    for (const json& n : *nodes_it) {
        if (!n.is_object()) {
            if (strict) throw Error("texture_graph", "texture graph node is not an object");
            continue;
        }
        const auto id_it = n.find("id");
        const auto op_it = n.find("op");
        if (id_it == n.end() || !id_it->is_string()) {
            if (strict) throw Error("texture_graph", "texture graph node is missing a string \"id\"");
            continue;
        }
        GraphNode gn;
        gn.id = id_it->get<std::string>();
        gn.op = (op_it != n.end() && op_it->is_string()) ? op_it->get<std::string>() : std::string();
        gn.node = &n;
        collect_inputs(n, gn.inputs);
        if (g.by_id.count(gn.id) != 0) {
            if (strict) throw Error("texture_graph", "duplicate texture graph node id '" + gn.id + "'");
            continue;
        }
        g.by_id.emplace(gn.id, g.nodes.size());
        g.nodes.push_back(std::move(gn));
    }
    auto out_it = graph.find("output");
    if (out_it != graph.end() && out_it->is_string()) g.output = out_it->get<std::string>();
    return g;
}

// ---------------------------------------------------------------------------
// Evaluation
// ---------------------------------------------------------------------------

class Evaluator {
public:
    static constexpr size_t kMaxDepth = 1024;

    Evaluator(const ParsedGraph& graph, const Ctx& ctx) : graph_(graph), ctx_(ctx) {}

    const Image& evaluate(const std::string& id) {
        auto cached = cache_.find(id);
        if (cached != cache_.end()) return cached->second;
        if (!active_.insert(id).second) {
            throw Error("texture_graph", "cycle in texture graph at node '" + id + "'");
        }
        if (active_.size() > kMaxDepth) {
            throw Error("texture_graph", "texture graph nesting is too deep at node '" + id + "'");
        }
        auto found = graph_.by_id.find(id);
        if (found == graph_.by_id.end()) {
            throw Error("texture_graph", "unknown texture graph node '" + id + "'");
        }
        const GraphNode& node = graph_.nodes[found->second];
        InputMap inputs;
        for (const auto& [port, ref] : node.inputs) {
            if (graph_.by_id.find(ref) == graph_.by_id.end()) {
                throw Error("texture_graph",
                            "texture graph node '" + id + "' input '" + port + "' references unknown node '" + ref + "'");
            }
            inputs[port] = &evaluate(ref);
        }
        Image result = run_op(node, inputs);
        active_.erase(id);
        auto inserted = cache_.emplace(id, std::move(result));
        return inserted.first->second;
    }

private:
    Image run_op(const GraphNode& node, const InputMap& inputs) {
        const json& p = params_of(*node.node);
        const uint32_t seed = op_seed_of(ctx_, p, node.id);
        const std::string& op = node.op;

        if (op == "perlin") return eval_noise_op(NoiseOp::Perlin, p, ctx_, seed);
        if (op == "simplex") return eval_noise_op(NoiseOp::Simplex, p, ctx_, seed);
        if (op == "worley") return eval_noise_op(NoiseOp::Worley, p, ctx_, seed);
        if (op == "fbm") return eval_noise_op(NoiseOp::Fbm, p, ctx_, seed);
        if (op == "curl") return eval_noise_op(NoiseOp::Curl, p, ctx_, seed);
        if (op == "cellular") return eval_noise_op(NoiseOp::Cellular, p, ctx_, seed);
        if (op == "blue_noise") return eval_noise_op(NoiseOp::BlueNoise, p, ctx_, seed);
        if (op == "gradient_linear") return eval_gradient_linear(p, ctx_);
        if (op == "gradient_radial") return eval_gradient_radial(p, ctx_);
        if (op == "ring") return eval_ring(p, ctx_);
        if (op == "spokes") return eval_spokes(p, ctx_);
        if (op == "star") return eval_star(p, ctx_);
        if (op == "cracks") return eval_cracks(p, ctx_, seed);
        if (op == "voronoi") return eval_voronoi(p, ctx_, seed);
        if (op == "flame") return eval_flame(p, ctx_, seed);
        if (op == "constant") return eval_constant(p, ctx_);
        if (op == "time") return eval_time(p, ctx_);
        if (op == "channel_pack") return eval_channel_pack(p, ctx_, inputs);

        // Ops with a primary `a` input; a missing input degrades to white so a
        // partially built graph still bakes.
        const Image* a = find_input(inputs, "a");
        Image fallback;
        if (a == nullptr) {
            fallback = white_image(ctx_);
            a = &fallback;
        }
        if (op == "levels") return eval_levels(p, ctx_, *a);
        if (op == "math") return eval_math(p, ctx_, *a, find_input(inputs, "b"));
        if (op == "invert") return eval_invert(ctx_, *a);
        if (op == "dissolve_mask") return eval_dissolve_mask(p, ctx_, *a);
        if (op == "colorize") return eval_colorize(p, ctx_, *a);
        if (op == "erosion") return eval_erosion(p, ctx_, *a, find_input(inputs, "noise"), seed);
        if (op == "distort") return eval_distort(p, ctx_, *a, find_input(inputs, "by"));
        if (op == "flow_map") return eval_flow_map(ctx_, *a);
        if (op == "normal_from_height") return eval_normal_from_height(p, ctx_, *a);
        if (op == "blur") return gaussian_blur(*a, param_float(p, "radius", 2.0f));

        throw Error("texture_graph", "unknown texture graph op '" + op + "' on node '" + node.id + "'");
    }

    const ParsedGraph& graph_;
    Ctx ctx_;
    std::map<std::string, Image> cache_;
    std::set<std::string> active_;
};

}  // namespace

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

std::vector<std::string> texture_op_names() {
    std::vector<std::string> names;
    for (const OpSpec& o : build_op_specs()) names.emplace_back(o.name);
    return names;
}

nlohmann::json texture_ops_json() {
    json out = json::array();
    for (const OpSpec& o : build_op_specs()) {
        json entry;
        entry["op"] = o.name;
        entry["description"] = o.description;
        json params = json::object();
        for (const ParamSpec& p : o.params) {
            json spec;
            spec["type"] = p.type;
            spec["default"] = p.default_value;
            spec["description"] = p.description;
            params[p.name] = spec;
        }
        entry["params"] = params;
        json inputs = json::array();
        for (const char* in : o.inputs) inputs.push_back(in);
        entry["inputs"] = inputs;
        out.push_back(entry);
    }
    return out;
}

TextureResource bake_texture_graph(const nlohmann::json& graph, const TextureBakeOptions& options) {
    if (options.width <= 0 || options.height <= 0) {
        throw Error("texture_graph", "texture bake resolution must be positive");
    }
    const int frames = std::max(1, options.frames);
    const ParsedGraph parsed = parse_graph(graph, /*strict=*/true);
    if (parsed.output.empty()) {
        throw Error("texture_graph", "texture graph has no \"output\" node id");
    }
    if (parsed.by_id.find(parsed.output) == parsed.by_id.end()) {
        throw Error("texture_graph", "texture graph output '" + parsed.output + "' is not a node in the graph");
    }

    TextureResource resource;
    resource.frames = frames;
    resource.image.resize(options.width * frames, options.height, Color::transparent());

    for (int f = 0; f < frames; ++f) {
        Ctx ctx;
        ctx.width = options.width;
        ctx.height = options.height;
        ctx.time = static_cast<float>(f) / static_cast<float>(frames);
        ctx.seed = options.seed;
        Evaluator evaluator(parsed, ctx);
        const Image& frame = evaluator.evaluate(parsed.output);
        const int x0 = f * options.width;
        for (int y = 0; y < options.height; ++y) {
            for (int x = 0; x < options.width; ++x) {
                resource.image.set(x0 + x, y, frame.get(x, y));
            }
        }
    }
    return resource;
}

Diagnostics validate_texture_graph(const nlohmann::json& graph) {
    Diagnostics diag;
    const std::vector<OpSpec> specs = build_op_specs();

    if (!graph.is_object()) {
        diag.error("T001", "texture graph must be a JSON object with \"nodes\" and \"output\"");
        return diag;
    }
    auto nodes_it = graph.find("nodes");
    if (nodes_it == graph.end() || !nodes_it->is_array()) {
        diag.error("T001", "texture graph has no \"nodes\" array");
        return diag;
    }
    if (nodes_it->empty()) {
        diag.error("T001", "texture graph \"nodes\" is empty");
    }

    // Structural pass: ids, ops, params.
    std::map<std::string, size_t> by_id;
    std::vector<GraphNode> nodes;
    size_t index = 0;
    for (const json& n : *nodes_it) {
        const std::string where = "nodes[" + std::to_string(index) + "]";
        ++index;
        if (!n.is_object()) {
            diag.error("T001", where + " is not an object");
            continue;
        }
        const auto id_it = n.find("id");
        if (id_it == n.end() || !id_it->is_string() || id_it->get<std::string>().empty()) {
            diag.error("T001", where + " is missing a non-empty string \"id\"");
            continue;
        }
        GraphNode gn;
        gn.id = id_it->get<std::string>();
        gn.node = &n;
        const auto op_it = n.find("op");
        if (op_it == n.end() || !op_it->is_string()) {
            diag.error("T002", "node has no string \"op\"", gn.id);
        } else {
            gn.op = op_it->get<std::string>();
        }
        collect_inputs(n, gn.inputs);
        if (by_id.count(gn.id) != 0) {
            diag.error("T001", "duplicate node id '" + gn.id + "'", gn.id);
            continue;
        }
        by_id.emplace(gn.id, nodes.size());
        nodes.push_back(std::move(gn));
    }

    for (const GraphNode& n : nodes) {
        if (n.op.empty()) continue;
        const OpSpec* spec = find_op_spec(specs, n.op);
        if (spec == nullptr) {
            diag.error("T002", "unknown texture op '" + n.op + "'", n.id, "op");
            continue;
        }
        const json& p = params_of(*n.node);
        for (const ParamSpec& ps : spec->params) {
            auto it = p.find(ps.name);
            if (it == p.end()) continue;
            const std::string type = ps.type;
            bool ok = true;
            if (type == "float" || type == "int") ok = it->is_number();
            else if (type == "bool") ok = it->is_boolean() || it->is_number();
            else if (type == "string") ok = it->is_string();
            else if (type == "vec2") ok = it->is_array() && it->size() >= 2;
            else if (type == "color") ok = it->is_array() && it->size() >= 3;
            else if (type == "gradient") ok = it->is_array();
            if (!ok) {
                diag.error("T006", "parameter '" + std::string(ps.name) + "' of op '" + n.op + "' must be " + type,
                           n.id, ps.name);
            }
        }
        // Unresolved inputs.
        for (const auto& [port, ref] : n.inputs) {
            if (by_id.find(ref) == by_id.end()) {
                diag.error("T003", "input '" + port + "' references unknown node '" + ref + "'", n.id, port);
            }
        }
    }

    // Cycle detection (iterative DFS with colours; 0 = unseen, 1 = on stack, 2 = done).
    {
        std::vector<int> color(nodes.size(), 0);
        std::vector<size_t> next_input(nodes.size(), 0);
        std::vector<size_t> stack;
        bool reported = false;
        for (size_t root = 0; root < nodes.size() && !reported; ++root) {
            if (color[root] != 0) continue;
            stack.clear();
            stack.push_back(root);
            while (!stack.empty() && !reported) {
                const size_t cur = stack.back();
                if (color[cur] == 0) color[cur] = 1;
                bool descended = false;
                while (next_input[cur] < nodes[cur].inputs.size()) {
                    const std::string& ref = nodes[cur].inputs[next_input[cur]].second;
                    ++next_input[cur];
                    auto target = by_id.find(ref);
                    if (target == by_id.end()) continue;
                    const size_t ti = target->second;
                    if (color[ti] == 1) {
                        diag.error("T004", "cycle in texture graph involving node '" + nodes[ti].id + "'",
                                   nodes[ti].id);
                        reported = true;
                        break;
                    }
                    if (color[ti] == 0) {
                        stack.push_back(ti);
                        descended = true;
                        break;
                    }
                }
                if (reported) break;
                if (!descended && next_input[cur] >= nodes[cur].inputs.size()) {
                    color[cur] = 2;
                    stack.pop_back();
                }
            }
        }
    }

    // Output reference.
    auto out_it = graph.find("output");
    if (out_it == graph.end() || !out_it->is_string() || out_it->get<std::string>().empty()) {
        diag.error("T005", "texture graph has no \"output\" node id");
    } else if (by_id.find(out_it->get<std::string>()) == by_id.end()) {
        diag.error("T005", "texture graph output '" + out_it->get<std::string>() + "' is not a node in the graph");
    }
    return diag;
}

// ---------------------------------------------------------------------------
// Built-in sprites and image utilities
// ---------------------------------------------------------------------------

Image default_soft_particle(int size) {
    const int n = std::max(1, size);
    Image img(n, n);
    for (int y = 0; y < n; ++y) {
        const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(n);
        for (int x = 0; x < n; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(n);
            const float d = length(Vec2{u - 0.5f, v - 0.5f}) * 2.0f;  // 0 at centre, 1 at edge
            const float t = saturate(d);
            const float falloff = 1.0f - t * t * (3.0f - 2.0f * t);
            img.set(x, y, Color{1.0f, 1.0f, 1.0f, falloff});
        }
    }
    return img;
}

Image default_spark(int size) {
    const int n = std::max(1, size);
    Image img(n, n);
    for (int y = 0; y < n; ++y) {
        const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(n);
        for (int x = 0; x < n; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(n);
            const float d = saturate(length(Vec2{u - 0.5f, v - 0.5f}) * 2.0f);
            const float core = (1.0f - d) * (1.0f - d);
            const float alpha = core * core;  // tight, bright centre
            img.set(x, y, Color{1.0f, 1.0f, 1.0f, alpha});
        }
    }
    return img;
}

Image default_smoke_puff(int size, uint32_t seed) {
    const int n = std::max(1, size);
    FbmParams fbm;
    fbm.octaves = 4;
    fbm.lacunarity = 2.0f;
    fbm.gain = 0.5f;
    fbm.basis = NoiseBasis::Simplex;
    Image img(n, n);
    for (int y = 0; y < n; ++y) {
        const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(n);
        for (int x = 0; x < n; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(n);
            const float d = saturate(length(Vec2{u - 0.5f, v - 0.5f}) * 2.0f);
            const float radial = 1.0f - d * d * (3.0f - 2.0f * d);
            const float noise = 0.5f + 0.5f * fbm3(Vec3{u * 5.0f, v * 5.0f, 0.0f}, seed, fbm);
            const float alpha = saturate(smoothstep(0.08f, 0.55f, radial * noise));
            img.set(x, y, Color{1.0f, 1.0f, 1.0f, alpha});
        }
    }
    return img;
}

Image resample(const Image& src, int width, int height) {
    if (width <= 0 || height <= 0) return Image{};
    Image out(width, height);
    if (src.empty()) return out;
    for (int y = 0; y < height; ++y) {
        const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(height);
        for (int x = 0; x < width; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(width);
            out.set(x, y, src.sample(u, v, WrapMode::Clamp));
        }
    }
    return out;
}

Image gaussian_blur(const Image& src, float radius_px) {
    if (src.empty() || !(radius_px > 0.0f)) return src;
    const int radius = std::max(1, static_cast<int>(std::ceil(radius_px)));
    const float sigma = std::max(radius_px * 0.5f, 1e-4f);
    const float inv_two_sigma_sq = 1.0f / (2.0f * sigma * sigma);
    std::vector<float> kernel(static_cast<size_t>(radius) * 2 + 1);
    float total = 0.0f;
    for (int i = -radius; i <= radius; ++i) {
        const float w = std::exp(-static_cast<float>(i * i) * inv_two_sigma_sq);
        kernel[static_cast<size_t>(i + radius)] = w;
        total += w;
    }
    for (float& w : kernel) w /= total;

    Image horizontal(src.width, src.height);
    for (int y = 0; y < src.height; ++y) {
        for (int x = 0; x < src.width; ++x) {
            Color acc{0, 0, 0, 0};
            for (int i = -radius; i <= radius; ++i) {
                acc += src.fetch(x + i, y, WrapMode::Clamp) * kernel[static_cast<size_t>(i + radius)];
            }
            horizontal.set(x, y, acc);
        }
    }
    Image out(src.width, src.height);
    for (int y = 0; y < src.height; ++y) {
        for (int x = 0; x < src.width; ++x) {
            Color acc{0, 0, 0, 0};
            for (int i = -radius; i <= radius; ++i) {
                acc += horizontal.fetch(x, y + i, WrapMode::Clamp) * kernel[static_cast<size_t>(i + radius)];
            }
            out.set(x, y, acc);
        }
    }
    return out;
}

void premultiply(Image& img) {
    for (size_t i = 0; i < img.pixel_count(); ++i) {
        const float a = img.rgba[i * 4 + 3];
        img.rgba[i * 4 + 0] *= a;
        img.rgba[i * 4 + 1] *= a;
        img.rgba[i * 4 + 2] *= a;
    }
}

}  // namespace aether::procedural
