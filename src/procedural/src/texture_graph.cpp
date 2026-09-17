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
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <string_view>
#include <utility>
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
        "shape",
        "Signed-distance silhouette: the primitive for emblems, icon plates, glyph tiles and hex cells. "
        "The outline is described exactly (straight segments and circular arcs) and the op evaluates the "
        "true distance to it, so an `outline` keeps one width all the way round, `inset` gives honest "
        "concentric copies and `bevel` is a linear ramp. Shapes are centred on `center`, upright (the top "
        "of the shape is the top of the image, v = 0) and `radius` is half their height; `shield` is a "
        "heater shield - a flat or scalloped (`crest`) top, straight shoulders, then two arcs sweeping "
        "in to the point. Modes: `fill` = 1 inside, 0 outside; `outline` = a band `outline_width` wide "
        "centred on the contour (set `inset` to half the width to keep it inside the silhouette); `bevel` "
        "= 0 at the contour climbing linearly to 1 at depth `bevel` inside, the building block for inner "
        "glows and embossed rims (`invert` it and multiply by a `fill`), and with a negative `inset` of "
        "the same size an outer glow. An emblem is a few of these folded with `math`: a bright outline, "
        "a dimmer inset fill, a thin inner line, and a tall thin `rounded_box` with a large `softness` "
        "multiplied in as the centre ridge. Never build a cross or a plus from two boxes (house style).",
        {{"shape", "string", "circle", "circle | polygon | hexagon | diamond | rounded_box | shield"},
         {"mode", "string", "fill", "fill | outline | bevel"},
         {"radius", "float", 0.35, "half the height of the shape in UV units (polygon: the circumradius)"},
         {"aspect", "float", 1.0,
          "horizontal stretch; 1 keeps the natural proportions (a shield is 0.82 as wide as it is tall)"},
         {"center", "vec2", json::array({0.5, 0.5}), "centre in UV space"},
         {"rotation", "float", 0.0, "rotation in degrees, counter-clockwise"},
         {"sides", "int", 6, "polygon: number of sides, 3..64, first vertex pointing up"},
         {"corner_radius", "float", 0.0, "rounds the corners without changing the overall size"},
         {"shoulder", "float", 0.3, "shield: fraction of the height with straight sides above the arcs"},
         {"crest", "float", 0.0,
          "shield: depth of the two scallops in the top edge as a fraction of the width (0 = flat, 0.06 = heraldic)"},
         {"softness", "float", 0.01, "edge falloff half-width in UV units; 0 = hard edge"},
         {"inset", "float", 0.0, "moves the contour inwards (negative: outwards) before the mode is applied"},
         {"outline_width", "float", 0.03, "outline: full width of the band"},
         {"bevel", "float", 0.1, "bevel: depth over which the inner ramp climbs from 0 to 1"}},
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
    ops.push_back(OpSpec{
        "fire_sim",
        "Baked 2D flame simulation: temperature in [0,1] per frame, the op to reach for when fire has "
        "to look like fire rather than like one painted tongue (`flame`). A grid the size of the "
        "texture carries a temperature field; per step the velocity is buoyancy * heat upwards "
        "(accelerating with height, so the column stretches), plus the curl of an animated noise "
        "potential (`turbulence`, `turbulence_scale`, `detail`), plus an inward entrainment flow "
        "integrated from dvx/dx = -dvy/dy along each row, which necks the plume the way a real one "
        "narrows and pinches puffs off. The field is advected semi-Lagrangian with bilinear sampling "
        "(cold air at the sides and above the tip, the fuel bed repeated below the base), cooled by a "
        "noise-modulated rate that grows with height plus a subtractive term that erases whatever is "
        "nearly cold - that is what leaves ragged eroded edges instead of a soft halo - lightly "
        "diffused, and re-lit from a fuel band across the bottom `fuel_width` of the tile whose "
        "moving patches make tongues that are born apart and split as they rise. `sharpness` is an "
        "S-curve about 0.5 that keeps the white-hot core and crushes the haze. Frames are captured "
        "every `substeps` steps after `warmup`; the root is at v = 0, so the sheet draws upright and "
        "a stretched_billboard points its tip along the particle's velocity, exactly like `flame`. "
        "With `loop` (default) every time axis is wrapped onto a circle so the forcing is exactly "
        "periodic and the warm-up runs a whole loop before frame 0, which lands the state on the "
        "forcing's limit cycle: frame 0 continues frame N-1 with no cross-fade and so no ghosting. "
        "Bake it at 96x144..160x256 with frames: 24-32 and colour it with a material "
        "temperature_gradient or a `colorize` with the fire ramp "
        "[[0,[0.05,0,0,1]], [0.25,[0.8,0.1,0,1]], [0.5,[1,0.45,0.05,1]], [0.75,[1,0.85,0.35,1]], "
        "[1,[1,1,0.85,1]]]. Deterministic: same seed, same bytes.",
        {{"seed", "int", 0, "per-op seed, combined with the bake seed"},
         {"fuel", "float", 1.25, "source intensity at the base, 0..2"},
         {"fuel_width", "float", 0.6, "fraction of the width the fuel band covers, centred"},
         {"buoyancy", "float", 1.7, "upward advection speed per unit heat"},
         {"turbulence", "float", 1.15, "curl-noise strength"},
         {"turbulence_scale", "float", 3.0, "curl-noise frequency (eddies per tile height)"},
         {"cooling", "float", 1.8, "cooling and edge erosion rate per unit sim time"},
         {"detail", "int", 4, "noise octaves, 1..5"},
         {"speed", "float", 0.06, "sim time per baked frame"},
         {"substeps", "int", 4, "simulation steps per frame, 1..8"},
         {"warmup", "int", 48, "steps run before frame 0; raised to one whole loop when `loop`"},
         {"loop", "bool", true, "periodic forcing plus a full-loop warm-up so the flipbook closes"},
         {"flicker", "float", 0.35, "temporal fuel modulation, 0..1"},
         {"sharpness", "float", 1.25, "output contrast about 0.5; 1 = linear"}},
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

// Baked frames of a `fire_sim` node, keyed by node id. The graph is evaluated
// once per frame but the simulation runs once per bake, so the whole flipbook is
// simulated on the first frame that asks for it and read back afterwards.
struct FireSimCache {
    std::map<std::string, std::vector<std::vector<float>>> by_node;
};

struct Ctx {
    int width = 0;
    int height = 0;
    int frames = 1;      // frames in the bake
    int frame = 0;       // index of the frame being evaluated
    float time = 0.0f;   // frame / frames, in [0,1)
    uint32_t seed = 0;
    std::shared_ptr<FireSimCache> fire;  // shared across the frames of one bake
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

// ---------------------------------------------------------------------------
// shape: signed-distance silhouettes (emblems, plates, glyph tiles, hex cells)
// ---------------------------------------------------------------------------
//
// Every silhouette is described as the closed outline it is - a short list of
// straight segments and circular arcs - and the op evaluates the true Euclidean
// distance to that outline, signed by an inside test. max()/min() of half-planes
// and discs would only *bound* the distance; working from the exact one is what
// keeps an `outline` the same width all the way round a pointed shield, lets
// `inset` produce honest concentric copies, and makes `bevel` a linear ramp the
// rest of the graph can shape with `levels`.
//
// `corner_radius` is the usual offset trick done properly: the outline is built
// for the shape shrunk inwards by r (every segment moved along its normal, convex
// arcs losing r of radius and concave ones gaining it, the corners re-intersected
// in closed form) and r is subtracted from the distance, so the corners round and
// the overall size stays what `radius` says.
//
// The shape frame is x right, y up, origin at `center`. Texture v runs down the
// image and image row 0 is drawn at the top of a sprite, so "up" is towards v = 0.
//
// No per-pixel trigonometry: every arc spans less than half a turn, so clipping a
// sample to an arc's wedge is two cross products. The only transcendental calls
// are the sin/cos that place a polygon's vertices and the rotation, once per bake.

struct OutlinePiece {
    bool arc = false;
    Vec2 a;                // start point
    Vec2 b;                // end point
    Vec2 centre;           // arc only
    float radius = 0.0f;   // arc only
};

inline float cross2(Vec2 a, Vec2 b) { return a.x * b.y - a.y * b.x; }

float distance_to_segment(Vec2 p, Vec2 a, Vec2 b) {
    const Vec2 ab = b - a;
    const float len2 = dot(ab, ab);
    const float t = len2 > 1e-12f ? saturate(dot(p - a, ab) / len2) : 0.0f;
    return length(p - (a + ab * t));
}

float distance_to_arc(Vec2 p, const OutlinePiece& arc) {
    const Vec2 q = p - arc.centre;
    const Vec2 e0 = arc.a - arc.centre;
    const Vec2 e1 = arc.b - arc.centre;
    const float turn = cross2(e0, e1) >= 0.0f ? 1.0f : -1.0f;
    if (turn * cross2(e0, q) >= 0.0f && turn * cross2(q, e1) >= 0.0f) {
        return std::fabs(length(q) - arc.radius);  // inside the wedge: radial distance
    }
    return std::min(length(p - arc.a), length(p - arc.b));
}

// A heater shield's natural proportions: 0.82 as wide as it is tall.
constexpr float kShieldWidthRatio = 0.82f;

class ShapeField {
public:
    enum class Kind { Ellipse, Box, Polygon, Shield };

    ShapeField(const std::string& shape, float radius, float aspect, int sides, float corner_radius,
               float shoulder, float crest) {
        const float r = std::max(radius, 1e-4f);
        const float stretch = std::max(aspect, 1e-3f);
        if (shape == "shield") {
            build_shield(r, stretch, std::max(corner_radius, 0.0f), shoulder, crest);
        } else if (shape == "rounded_box") {
            kind_ = Kind::Box;
            half_ = Vec2{r * stretch, r};
            rounding_ = clamp(corner_radius, 0.0f, std::min(half_.x, half_.y));
        } else if (shape == "polygon" || shape == "hexagon" || shape == "diamond") {
            const int n = shape == "hexagon" ? 6 : (shape == "diamond" ? 4 : clamp(sides, 3, 64));
            build_polygon(r, stretch, n, std::max(corner_radius, 0.0f));
        } else {  // circle (an ellipse when aspect != 1)
            kind_ = Kind::Ellipse;
            half_ = Vec2{r * stretch, r};
        }
    }

    // Signed distance in UV units: negative inside, positive outside.
    float distance(Vec2 p) const {
        switch (kind_) {
            case Kind::Ellipse: return ellipse_distance(p);
            case Kind::Box: {
                const Vec2 q{std::fabs(p.x) - (half_.x - rounding_), std::fabs(p.y) - (half_.y - rounding_)};
                const float outside = length(Vec2{std::max(q.x, 0.0f), std::max(q.y, 0.0f)});
                return outside + std::min(std::max(q.x, q.y), 0.0f) - rounding_;
            }
            case Kind::Polygon: return signed_outline_distance(p, inside_polygon(p));
            case Kind::Shield: {
                const Vec2 folded{std::fabs(p.x), p.y};
                return signed_outline_distance(folded, inside_shield(folded));
            }
        }
        return 0.0f;
    }

private:
    float ellipse_distance(Vec2 p) const {
        if (std::fabs(half_.x - half_.y) < 1e-6f) return length(p) - half_.y;
        // First-order distance to the ellipse: f / |grad f| with f = |p / ab| - 1. Exact on the
        // contour, which is where fills, outlines and bevels are evaluated.
        const float k0 = length(Vec2{p.x / half_.x, p.y / half_.y});
        const float k1 = length(Vec2{p.x / (half_.x * half_.x), p.y / (half_.y * half_.y)});
        if (k1 < 1e-9f) return -std::min(half_.x, half_.y);
        return k0 * (k0 - 1.0f) / k1;
    }

    float signed_outline_distance(Vec2 p, bool inside) const {
        float nearest = std::numeric_limits<float>::max();
        for (const OutlinePiece& piece : pieces_) {
            nearest = std::min(nearest, piece.arc ? distance_to_arc(p, piece) : distance_to_segment(p, piece.a, piece.b));
        }
        return (inside ? -nearest : nearest) - rounding_;
    }

    // Regular n-gon, first vertex straight up, walked clockwise, then stretched by `aspect`.
    void build_polygon(float radius, float stretch, int n, float corner_radius) {
        kind_ = Kind::Polygon;
        std::vector<Vec2> vertices(static_cast<size_t>(n));
        for (int k = 0; k < n; ++k) {
            const float angle = kTwoPi * static_cast<float>(k) / static_cast<float>(n);
            vertices[static_cast<size_t>(k)] = Vec2{radius * stretch * std::sin(angle), radius * std::cos(angle)};
        }
        // Inward normals (clockwise walk with y up: the inside is on the right of the travel direction).
        std::vector<Vec2> normals(static_cast<size_t>(n));
        float apothem = std::numeric_limits<float>::max();
        for (int k = 0; k < n; ++k) {
            const Vec2 a = vertices[static_cast<size_t>(k)];
            const Vec2 b = vertices[static_cast<size_t>((k + 1) % n)];
            const Vec2 e = normalize(b - a);
            normals[static_cast<size_t>(k)] = Vec2{e.y, -e.x};
            apothem = std::min(apothem, -dot(normals[static_cast<size_t>(k)], a));
        }
        rounding_ = clamp(corner_radius, 0.0f, 0.9f * std::max(apothem, 0.0f));
        if (rounding_ > 0.0f) {
            // Shrink by the rounding: each vertex moves to where its two offset edges meet.
            std::vector<Vec2> inset(static_cast<size_t>(n));
            for (int k = 0; k < n; ++k) {
                const Vec2 n0 = normals[static_cast<size_t>((k + n - 1) % n)];
                const Vec2 n1 = normals[static_cast<size_t>(k)];
                const float det = cross2(n0, n1);
                Vec2 shift{0.0f, 0.0f};
                if (std::fabs(det) > 1e-9f) {
                    shift = Vec2{rounding_ * (n1.y - n0.y) / det, rounding_ * (n0.x - n1.x) / det};
                }
                inset[static_cast<size_t>(k)] = vertices[static_cast<size_t>(k)] + shift;
            }
            vertices = std::move(inset);
        }
        pieces_.reserve(static_cast<size_t>(n));
        for (int k = 0; k < n; ++k) {
            OutlinePiece piece;
            piece.a = vertices[static_cast<size_t>(k)];
            piece.b = vertices[static_cast<size_t>((k + 1) % n)];
            pieces_.push_back(piece);
        }
    }

    bool inside_polygon(Vec2 p) const {
        // Convex and walked clockwise: inside means on the right of every edge.
        for (const OutlinePiece& piece : pieces_) {
            if (cross2(piece.b - piece.a, p - piece.a) > 0.0f) return false;
        }
        return true;
    }

    // Heater shield, x >= 0 half only (the sample is folded with |x|):
    //   top edge      flat, or one concave scallop from the centre peak to the corner (`crest`)
    //   shoulder      a straight vertical side, `shoulder` of the height long
    //   lower arc     centre (-c, yc), tangent to the side at (half_w, yc), reaching the point at (0, -radius)
    // c follows from those two conditions: (half_w + c)^2 = c^2 + depth^2.
    void build_shield(float radius, float stretch, float corner_radius, float shoulder, float crest) {
        kind_ = Kind::Shield;
        const float height = 2.0f * radius;
        const float half_w = std::min(0.5f * height * kShieldWidthRatio * stretch, height);
        const float width = 2.0f * half_w;
        const float top = radius;
        const float scallop = clamp(crest, 0.0f, 0.2f) * width;
        rounding_ = clamp(corner_radius, 0.0f, 0.45f * half_w);
        // The side has to outlast the rounding and the scallop, and the arcs need at least a
        // semicircle's depth to reach the axis.
        const float min_side = 0.02f * height + rounding_ + scallop;
        const float max_side = std::max(height - half_w, min_side);
        const float side = clamp(shoulder * height, min_side, max_side);
        const float depth = std::max(height - side, half_w);
        shield_yc_ = -radius + depth;
        shield_c_ = (depth * depth - half_w * half_w) / (2.0f * half_w);
        shield_rho_ = half_w + shield_c_ - rounding_;
        shield_half_w_ = half_w - rounding_;
        shield_top_ = top - rounding_;

        const Vec2 junction{shield_half_w_, shield_yc_};
        const Vec2 tip{0.0f, shield_yc_ - std::sqrt(std::max(0.0f, shield_rho_ * shield_rho_ - shield_c_ * shield_c_))};
        Vec2 peak{0.0f, shield_top_};
        Vec2 corner{shield_half_w_, shield_top_};

        OutlinePiece top_piece;
        scalloped_ = scallop > 1e-5f;
        if (scalloped_) {
            // A circle through the peak (0, top) and the corner (half_w, top) whose lowest point dips
            // `scallop` below the top edge; shrinking the shield grows this concave arc by the rounding.
            const float quarter = 0.5f * half_w;
            const float k = (quarter * quarter - scallop * scallop) / (2.0f * scallop);
            scallop_centre_ = Vec2{quarter, top + k};
            scallop_radius_ = k + scallop + rounding_;
            const float r2 = scallop_radius_ * scallop_radius_;
            const float to_side = quarter - rounding_;
            peak.y = scallop_centre_.y - std::sqrt(std::max(0.0f, r2 - quarter * quarter));
            corner.y = scallop_centre_.y - std::sqrt(std::max(0.0f, r2 - to_side * to_side));
            top_piece.arc = true;
            top_piece.centre = scallop_centre_;
            top_piece.radius = scallop_radius_;
        }
        top_piece.a = peak;
        top_piece.b = corner;
        pieces_.push_back(top_piece);

        OutlinePiece side_piece;
        side_piece.a = corner;
        side_piece.b = junction;
        pieces_.push_back(side_piece);

        OutlinePiece lower;
        lower.arc = true;
        lower.a = junction;
        lower.b = tip;
        lower.centre = Vec2{-shield_c_, shield_yc_};
        lower.radius = shield_rho_;
        pieces_.push_back(lower);
    }

    bool inside_shield(Vec2 p) const {  // p is already folded to x >= 0
        if (p.x > shield_half_w_ || p.y > shield_top_) return false;
        if (p.y < shield_yc_) {
            const Vec2 q{p.x + shield_c_, p.y - shield_yc_};
            return dot(q, q) <= shield_rho_ * shield_rho_;
        }
        if (scalloped_) {
            const Vec2 q = p - scallop_centre_;
            return dot(q, q) >= scallop_radius_ * scallop_radius_;
        }
        return true;
    }

    Kind kind_ = Kind::Ellipse;
    Vec2 half_{0.35f, 0.35f};
    float rounding_ = 0.0f;
    std::vector<OutlinePiece> pieces_;
    // shield, already shrunk by the rounding
    float shield_half_w_ = 0.0f, shield_top_ = 0.0f, shield_yc_ = 0.0f, shield_c_ = 0.0f, shield_rho_ = 0.0f;
    bool scalloped_ = false;
    Vec2 scallop_centre_;
    float scallop_radius_ = 0.0f;
};

Image eval_shape(const json& p, const Ctx& ctx) {
    const std::string mode = param_string(p, "mode", "fill");
    const Vec2 center = param_vec2(p, "center", Vec2{0.5f, 0.5f});
    const float rotation = deg_to_rad(param_float(p, "rotation", 0.0f));
    const float softness = std::max(param_float(p, "softness", 0.01f), 0.0f);
    const float inset = param_float(p, "inset", 0.0f);
    const float half_band = 0.5f * std::max(param_float(p, "outline_width", 0.03f), 0.0f);
    const float bevel = std::max(param_float(p, "bevel", 0.1f), 0.0f);
    const ShapeField field(param_string(p, "shape", "circle"), param_float(p, "radius", 0.35f),
                           param_float(p, "aspect", 1.0f), param_int(p, "sides", 6),
                           param_float(p, "corner_radius", 0.0f), param_float(p, "shoulder", 0.3f),
                           param_float(p, "crest", 0.0f));
    const float cr = std::cos(rotation), sr = std::sin(rotation);
    const bool outline = mode == "outline";
    const bool ramp = mode == "bevel";

    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        const float dy = center.y - uv_v(ctx, y);  // up is towards v = 0
        for (int x = 0; x < ctx.width; ++x) {
            const float dx = uv_u(ctx, x) - center.x;
            // Rotating the shape counter-clockwise is sampling it rotated clockwise.
            const Vec2 local{dx * cr + dy * sr, -dx * sr + dy * cr};
            const float d = field.distance(local) + inset;
            float value;
            if (outline) {
                value = soft_edge(half_band, softness, std::fabs(d));
            } else if (ramp) {
                value = d >= 0.0f ? 0.0f : (bevel > 1e-6f ? saturate(-d / bevel) : 1.0f);
            } else {  // fill
                value = soft_edge(0.0f, softness, d);
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

// ---------------------------------------------------------------------------
// fire_sim: a baked 2D flame simulation
// ---------------------------------------------------------------------------
//
// `flame` paints one smooth tongue from noise; `fire_sim` runs a small
// deterministic Eulerian simulation and bakes its temperature field into the
// flipbook, so the shapes are made by the flow instead of by a silhouette
// formula: tongues rise, neck, split off and cool into ragged wisps, and the
// motion is coherent from frame to frame.
//
// The grid is the bake resolution, row j = 0 at the root (v = 0) and up the
// flame with increasing j. World units put the height at 1 and the width at the
// aspect ratio, so noise is isotropic in pixels. One step:
//
//   1. velocity   v = (0, buoyancy * heat * acceleration(height))
//                     + turbulence * curl(psi)            (divergence free)
//                     + entrainment                       (see below)
//   2. advect     T(x) = T(x - v*dt) bilinear, cold air outside the sides and
//                 above the tip, the fuel bed repeated below the base
//   3. cool       T *= 1 - cooling*dt*(height, noise); then a subtractive term
//                 that erases whatever is nearly cold, which is what erodes the
//                 edges into rags instead of fading them into haze
//   4. diffuse    one 5-point pass, keeps filaments from aliasing
//   5. fuel       T = max(T, source) over the bottom band, broken into moving
//                 patches so tongues are born apart and split as they rise
//
// The entrainment term is a cheap stand-in for a pressure solve: incompressible
// flow has dvx/dx = -dvy/dy, so integrating the vertical gradient of the rise
// speed along each row gives the sideways flow that pulls the column inwards
// where it accelerates. That is what makes a plume narrow and pinch off puffs.
//
// Forcing fields (the curl potential and the cooling/erosion noise) are built on
// a coarse grid once per frame and lerped across the substeps; the fine per-cell
// work is then just advection and arithmetic.
//
// Looping (`loop: true`): every noise axis that carries time is wrapped onto a
// circle whose circumference is one loop, so the forcing is exactly periodic,
// and the sim is warmed up for at least one whole loop before frame 0 is kept.
// Cooling plus outflow erase the initial state within a loop, so the state lands
// on the forcing's limit cycle and frame 0 continues frame N-1 with no cross
// fade and therefore no ghosting.
//
// Determinism: plain float, one thread, fixed loop order, no wall clock.

struct FireSimSettings {
    int nx = 1;
    int ny = 1;
    int frames = 1;
    float fuel = 1.25f;
    float fuel_width = 0.6f;
    float buoyancy = 1.7f;
    float turbulence = 1.15f;
    float turbulence_scale = 3.0f;
    float cooling = 1.8f;
    int detail = 4;
    float speed = 0.06f;
    int substeps = 4;
    int warmup = 48;
    bool loop = true;
    float flicker = 0.35f;
    float sharpness = 1.25f;
    uint32_t seed = 0;
};

// Tuning that is not exposed: the shape of the plume rather than its amount.
constexpr float kAmbientRise = 0.45f;   // rise speed of cold air, as a fraction of `buoyancy`
constexpr float kRiseBase = 0.5f;       // rise speed at the root ...
constexpr float kRiseGain = 1.1f;       // ... plus this * sqrt(height): the plume accelerates
constexpr float kEddyStretch = 0.42f;    // vertical squeeze of the curl potential: tall, licking eddies
constexpr float kSway = 0.65f;          // sideways share of the curl velocity; fire wanders less than smoke
constexpr float kTurbBase = 0.32f;      // turbulence at the root; it ramps in with height
constexpr float kEntrain = 0.3f;       // strength of the continuity-driven inward flow
constexpr float kCoolBase = 0.45f;      // cooling at the root ...
constexpr float kCoolHeight = 1.6f;     // ... plus this * height
constexpr float kCoolNoise = 1.1f;      // how much the cooling noise modulates that
constexpr float kErode = 1.0f;          // subtractive cooling: erases the almost-cold
constexpr float kTipKill = 2.6f;        // extra cooling over the last quarter: the tips burn out
                                        // inside the tile instead of running off the top edge
constexpr float kDiffuse = 0.03f;       // 5-point diffusion per step
constexpr float kBandFrac = 0.05f;      // fuel bed height, as a fraction of the tile
constexpr float kPatchBase = 0.15f;     // patchiness of the source at the very bottom row
constexpr float kFieldSamples = 3.0f;   // forcing-grid samples per finest noise feature
constexpr float kRootFade = 0.1f;       // the bottom of the tile fades in, so a big sprite has no
                                        // hard bright edge to show its quad (`flame` does the same);
                                        // the noise field breaks the fade up so the cut is ragged
                                        // instead of a ruler-straight line across the sprite

class FireSim {
public:
    explicit FireSim(const FireSimSettings& settings) : s_(settings) { run(); }

    // One baked frame, row-major from the root up, values in [0,1].
    const std::vector<float>& frame(int index) const {
        const int i = clamp(index, 0, static_cast<int>(frames_.size()) - 1);
        return frames_[static_cast<size_t>(i)];
    }

private:
    // Coarse forcing grid: the curl potential's gradient plus the cooling noise.
    struct Field {
        std::vector<float> gx, gy, mix;
    };

    // fbm whose time axis is wrapped onto a circle: adding 1 to `phase` turns it
    // by exactly 2*pi, so the field is periodic over one loop however fast it
    // scrolls. `rate` is the distance travelled along y in one loop.
    float looping_fbm(float x, float y, float phase, float rate, uint32_t seed, const FbmParams& fp) const {
        if (!s_.loop) return fbm4(Vec4{x, y + phase * rate, phase * 0.85f, 0.0f}, seed, fp);
        const float r = std::max(1e-3f, rate) / kTwoPi;
        const float theta = (y + phase * rate) / r;
        return fbm4(Vec4{x, r * std::cos(theta), r * std::sin(theta), 0.0f}, seed, fp);
    }

    void build_field(float phase, Field& field) const {
        FbmParams potential;
        potential.octaves = s_.detail;
        potential.basis = NoiseBasis::Simplex;
        FbmParams mixer = potential;
        mixer.octaves = std::min(s_.detail + 1, 5);

        const float ts = s_.turbulence_scale;
        std::vector<float> psi(static_cast<size_t>(fnx_) * static_cast<size_t>(fny_), 0.0f);
        for (int ky = 0; ky < fny_; ++ky) {
            const float wy = static_cast<float>(ky) * hy_;
            for (int kx = 0; kx < fnx_; ++kx) {
                const float wx = static_cast<float>(kx) * hx_;
                const size_t k = static_cast<size_t>(ky) * static_cast<size_t>(fnx_) + static_cast<size_t>(kx);
                psi[k] = looping_fbm(wx * ts, wy * ts * kEddyStretch, phase, drift_ * ts * kEddyStretch,
                                     s_.seed + 17u, potential);
                field.mix[k] = 0.5f + 0.5f * looping_fbm(wx * ts * 1.9f + 37.5f, wy * ts * 1.9f, phase,
                                                         drift_ * ts * 1.9f, s_.seed + 911u, mixer);
            }
        }
        // Central differences (one sided at the border) give the curl potential's
        // gradient at the same nodes; the curl itself is (dpsi/dy, -dpsi/dx).
        for (int ky = 0; ky < fny_; ++ky) {
            for (int kx = 0; kx < fnx_; ++kx) {
                const size_t k = static_cast<size_t>(ky) * static_cast<size_t>(fnx_) + static_cast<size_t>(kx);
                const int x0 = std::max(0, kx - 1), x1 = std::min(fnx_ - 1, kx + 1);
                const int y0 = std::max(0, ky - 1), y1 = std::min(fny_ - 1, ky + 1);
                const size_t row = static_cast<size_t>(ky) * static_cast<size_t>(fnx_);
                field.gx[k] = (psi[row + static_cast<size_t>(x1)] - psi[row + static_cast<size_t>(x0)]) /
                              (static_cast<float>(x1 - x0) * hx_);
                field.gy[k] = (psi[static_cast<size_t>(y1) * static_cast<size_t>(fnx_) + static_cast<size_t>(kx)] -
                               psi[static_cast<size_t>(y0) * static_cast<size_t>(fnx_) + static_cast<size_t>(kx)]) /
                              (static_cast<float>(y1 - y0) * hy_);
            }
        }
    }

    // Bilinear lookup in a coarse field, blending the two keyframes that bracket
    // the current time so the forcing is continuous across the substeps.
    void sample_field(float wx, float wy, float blend, float& gx, float& gy, float& mix) const {
        const float fx = clamp(wx / hx_, 0.0f, static_cast<float>(fnx_ - 1));
        const float fy = clamp(wy / hy_, 0.0f, static_cast<float>(fny_ - 1));
        const int x0 = std::min(static_cast<int>(fx), fnx_ - 2 > 0 ? fnx_ - 2 : 0);
        const int y0 = std::min(static_cast<int>(fy), fny_ - 2 > 0 ? fny_ - 2 : 0);
        const int x1 = std::min(x0 + 1, fnx_ - 1);
        const int y1 = std::min(y0 + 1, fny_ - 1);
        const float tx = fx - static_cast<float>(x0);
        const float ty = fy - static_cast<float>(y0);
        const size_t r0 = static_cast<size_t>(y0) * static_cast<size_t>(fnx_);
        const size_t r1 = static_cast<size_t>(y1) * static_cast<size_t>(fnx_);
        const size_t i00 = r0 + static_cast<size_t>(x0), i10 = r0 + static_cast<size_t>(x1);
        const size_t i01 = r1 + static_cast<size_t>(x0), i11 = r1 + static_cast<size_t>(x1);
        const auto pick = [&](const std::vector<float>& a, const std::vector<float>& b, size_t i) {
            return lerp(a[i], b[i], blend);
        };
        const auto bilinear = [&](const std::vector<float>& a, const std::vector<float>& b) {
            return lerp(lerp(pick(a, b, i00), pick(a, b, i10), tx), lerp(pick(a, b, i01), pick(a, b, i11), tx), ty);
        };
        gx = bilinear(field_a_.gx, field_b_.gx);
        gy = bilinear(field_a_.gy, field_b_.gy);
        mix = bilinear(field_a_.mix, field_b_.mix);
    }

    // Bilinear temperature lookup in fine-cell coordinates (cell centres at i+0.5).
    float sample_temp(const std::vector<float>& t, float x, float y) const {
        const float fx = x - 0.5f;
        const float fy = y - 0.5f;
        const float flx = std::floor(fx);
        const float fly = std::floor(fy);
        const int x0 = static_cast<int>(flx);
        const int y0 = static_cast<int>(fly);
        const float tx = fx - flx;
        const float ty = fy - fly;
        const auto at = [&](int i, int j) -> float {
            if (i < 0 || i >= s_.nx) return 0.0f;   // cold air beside the flame
            if (j >= s_.ny) return 0.0f;            // cold air above the tip
            if (j < 0) j = 0;                       // the fuel bed continues below the base
            return t[static_cast<size_t>(j) * static_cast<size_t>(s_.nx) + static_cast<size_t>(i)];
        };
        return lerp(lerp(at(x0, y0), at(x0 + 1, y0), tx), lerp(at(x0, y0 + 1), at(x0 + 1, y0 + 1), tx), ty);
    }

    void step(float dt, float phase, float blend) {
        const int nx = s_.nx, ny = s_.ny;
        const float inv_ny = 1.0f / static_cast<float>(ny);

        // 1. velocity: buoyant rise + curl noise.
        for (int j = 0; j < ny; ++j) {
            const float wy = (static_cast<float>(j) + 0.5f) * cell_;
            const float h = (static_cast<float>(j) + 0.5f) * inv_ny;
            const float accel = kRiseBase + kRiseGain * std::sqrt(h);
            const float turb = s_.turbulence / s_.turbulence_scale *
                               (kTurbBase + (1.0f - kTurbBase) * smoothstep(0.0f, 0.35f, h));
            for (int i = 0; i < nx; ++i) {
                const size_t k = static_cast<size_t>(j) * static_cast<size_t>(nx) + static_cast<size_t>(i);
                const float wx = (static_cast<float>(i) + 0.5f) * cell_;
                float gx = 0.0f, gy = 0.0f, mix = 0.0f;
                sample_field(wx, wy, blend, gx, gy, mix);
                mix_[k] = mix;
                const float heat = temp_[k];
                const float rise = s_.buoyancy * (kAmbientRise + (1.0f - kAmbientRise) * heat) * accel;
                vx_[k] = kSway * turb * gy;
                vy_[k] = rise - turb * gx;
            }
        }

        // 2. entrainment: dvx/dx = -dvy/dy integrated along the row, then centred
        //    so the row has no net sideways drift.
        if (kEntrain > 0.0f) {
            std::vector<float> flow(static_cast<size_t>(nx), 0.0f);
            for (int j = 0; j < ny; ++j) {
                const size_t row = static_cast<size_t>(j) * static_cast<size_t>(nx);
                const int jm = std::max(0, j - 1);
                const int jp = std::min(ny - 1, j + 1);
                const float inv_dy = 1.0f / (static_cast<float>(jp - jm) * cell_);
                const size_t rm = static_cast<size_t>(jm) * static_cast<size_t>(nx);
                const size_t rp = static_cast<size_t>(jp) * static_cast<size_t>(nx);
                float prev = (vy_[rp] - vy_[rm]) * inv_dy;
                float acc = 0.0f;
                double sum = 0.0;
                flow[0] = 0.0f;
                for (int i = 1; i < nx; ++i) {
                    const float d = (vy_[rp + static_cast<size_t>(i)] - vy_[rm + static_cast<size_t>(i)]) * inv_dy;
                    acc -= 0.5f * (prev + d) * cell_;
                    prev = d;
                    flow[static_cast<size_t>(i)] = acc;
                    sum += static_cast<double>(acc);
                }
                const float mean = static_cast<float>(sum / static_cast<double>(nx));
                for (int i = 0; i < nx; ++i) {
                    vx_[row + static_cast<size_t>(i)] += kEntrain * (flow[static_cast<size_t>(i)] - mean);
                }
            }
        }

        // 3. advect and cool.
        const float to_cells = dt / cell_;
        for (int j = 0; j < ny; ++j) {
            const float h = (static_cast<float>(j) + 0.5f) * inv_ny;
            const float tip = kTipKill * smoothstep(0.62f, 1.0f, h);
            const float erode_ramp = smoothstep(0.02f, 0.28f, h);
            for (int i = 0; i < nx; ++i) {
                const size_t k = static_cast<size_t>(j) * static_cast<size_t>(nx) + static_cast<size_t>(i);
                const float sx = static_cast<float>(i) + 0.5f - vx_[k] * to_cells;
                const float sy = static_cast<float>(j) + 0.5f - vy_[k] * to_cells;
                float heat = sample_temp(temp_, sx, sy);
                const float mix = mix_[k];
                const float cool = s_.cooling * dt * (kCoolBase + kCoolHeight * h) *
                                   (1.0f + tip * (0.45f + 1.3f * (1.0f - mix))) *
                                   (1.0f + kCoolNoise * (mix - 0.5f));
                heat *= std::max(0.0f, 1.0f - cool);
                heat -= s_.cooling * dt * kErode * erode_ramp * (0.3f + 1.5f * (1.0f - mix));
                next_[k] = saturate(heat);
            }
        }

        // 4. diffuse (5-point), writing back into the live field.
        for (int j = 0; j < ny; ++j) {
            const size_t row = static_cast<size_t>(j) * static_cast<size_t>(nx);
            const size_t rm = static_cast<size_t>(std::max(0, j - 1)) * static_cast<size_t>(nx);
            const size_t rp = static_cast<size_t>(std::min(ny - 1, j + 1)) * static_cast<size_t>(nx);
            for (int i = 0; i < nx; ++i) {
                const size_t k = row + static_cast<size_t>(i);
                const size_t km = row + static_cast<size_t>(std::max(0, i - 1));
                const size_t kp = row + static_cast<size_t>(std::min(nx - 1, i + 1));
                const float around = 0.25f * (next_[km] + next_[kp] + next_[rm + static_cast<size_t>(i)] +
                                              next_[rp + static_cast<size_t>(i)]);
                temp_[k] = saturate(next_[k] + kDiffuse * (around - next_[k]));
            }
        }

        // 5. fuel bed: patches that move and break so tongues are born separate.
        FbmParams patch_fbm;
        patch_fbm.octaves = 3;
        patch_fbm.basis = NoiseBasis::Simplex;
        FbmParams slow_fbm;
        slow_fbm.octaves = 2;
        slow_fbm.basis = NoiseBasis::Simplex;
        const float flick =
            1.0f - s_.flicker * (0.5f - 0.5f * looping_fbm(3.7f, 0.0f, phase, 2.0f, s_.seed + 4211u, slow_fbm));
        const float half_width = std::max(0.02f, s_.fuel_width) * 0.5f;
        for (int i = 0; i < nx; ++i) {
            const float u = (static_cast<float>(i) + 0.5f) / static_cast<float>(nx);
            const float big = 0.5f + 0.5f * looping_fbm(u * 3.1f, 0.0f, phase, 2.2f, s_.seed + 131u, slow_fbm);
            const float fine = 0.5f + 0.5f * looping_fbm(u * 9.0f + 5.5f, 0.0f, phase, 3.6f, s_.seed + 577u, patch_fbm);
            // A soft floor keeps the bed alight; the gaps between the patches are
            // where two tongues part company.
            patch_[static_cast<size_t>(i)] =
                saturate((0.18f + 1.2f * smoothstep(0.38f, 0.74f, big)) * (0.35f + 0.85f * fine));
        }
        const int band = std::max(1, static_cast<int>(std::lround(static_cast<float>(ny) * kBandFrac)));
        for (int j = 0; j < band && j < ny; ++j) {
            const float jn = (static_cast<float>(j) + 0.5f) / static_cast<float>(band);
            const float vprof = 1.0f - smoothstep(0.3f, 1.05f, jn);
            const float patch_mix = kPatchBase + (1.0f - kPatchBase) * jn;
            const size_t row = static_cast<size_t>(j) * static_cast<size_t>(nx);
            for (int i = 0; i < nx; ++i) {
                const float u = (static_cast<float>(i) + 0.5f) / static_cast<float>(nx);
                const float d = std::fabs(u - 0.5f) / half_width;
                const float band_mask = 1.0f - smoothstep(0.62f, 1.02f, d);
                const float amp = s_.fuel * band_mask * vprof * flick *
                                  lerp(1.0f, patch_[static_cast<size_t>(i)], patch_mix);
                const size_t k = row + static_cast<size_t>(i);
                temp_[k] = std::max(temp_[k], saturate(amp));
            }
        }
    }

    void run() {
        const size_t cells = static_cast<size_t>(s_.nx) * static_cast<size_t>(s_.ny);
        cell_ = 1.0f / static_cast<float>(s_.ny);
        temp_.assign(cells, 0.0f);
        next_.assign(cells, 0.0f);
        vx_.assign(cells, 0.0f);
        vy_.assign(cells, 0.0f);
        mix_.assign(cells, 0.0f);
        patch_.assign(static_cast<size_t>(s_.nx), 0.0f);

        // Forcing grid: a few samples per finest noise feature, so the turbulence
        // looks the same whatever the bake resolution is.
        const float finest = s_.turbulence_scale * std::pow(2.0f, static_cast<float>(s_.detail - 1));
        fny_ = clamp(static_cast<int>(std::lround(finest * kFieldSamples)) + 1, 12, 160);
        const float aspect = static_cast<float>(s_.nx) / static_cast<float>(s_.ny);
        fnx_ = clamp(static_cast<int>(std::lround(static_cast<float>(fny_ - 1) * aspect)) + 1, 4, 240);
        hx_ = aspect / static_cast<float>(fnx_ - 1);
        hy_ = 1.0f / static_cast<float>(fny_ - 1);
        const size_t nodes = static_cast<size_t>(fnx_) * static_cast<size_t>(fny_);
        field_a_.gx.assign(nodes, 0.0f);
        field_a_.gy.assign(nodes, 0.0f);
        field_a_.mix.assign(nodes, 0.0f);
        field_b_ = field_a_;

        // The eddies drift up with the flame, at least one tile per loop so the
        // circle the time axis rides never shows its own vertical period.
        const float loop_time = static_cast<float>(s_.frames) * s_.speed;
        drift_ = clamp(s_.buoyancy * loop_time * 0.5f, 1.15f, 8.0f);

        const float dt = s_.speed / static_cast<float>(s_.substeps);
        const float phase_per_step = dt / std::max(1e-6f, loop_time);
        int warmup = s_.warmup;
        if (s_.loop) warmup = std::max(warmup, s_.frames * s_.substeps * 3 / 2);

        // Keyframes of the forcing sit one frame apart; `key_` is the index of
        // field_a_, field_b_ is the next one.
        key_ = std::numeric_limits<int>::min();
        const int total = warmup + s_.frames * s_.substeps;
        frames_.clear();
        frames_.reserve(static_cast<size_t>(s_.frames));
        for (int step_index = 0; step_index < total; ++step_index) {
            const float phase = static_cast<float>(step_index - warmup) * phase_per_step;
            const float key_pos = phase * static_cast<float>(s_.frames);
            const int key = static_cast<int>(std::floor(key_pos));
            set_key(key);
            step(dt, phase, key_pos - static_cast<float>(key));
            const int captured = step_index + 1 - warmup;
            if (captured > 0 && captured % s_.substeps == 0) emit();
        }
        while (frames_.size() < static_cast<size_t>(s_.frames)) emit();
    }

    void set_key(int key) {
        if (key == key_) return;
        const auto phase_of = [&](int k) { return static_cast<float>(k) / static_cast<float>(s_.frames); };
        if (key == key_ + 1) {
            std::swap(field_a_, field_b_);
        } else {
            build_field(phase_of(key), field_a_);
        }
        build_field(phase_of(key + 1), field_b_);
        key_ = key;
    }

    void emit() {
        std::vector<float> out(temp_.size(), 0.0f);
        for (int j = 0; j < s_.ny; ++j) {
            const float h = (static_cast<float>(j) + 0.5f) / static_cast<float>(s_.ny);
            const size_t row = static_cast<size_t>(j) * static_cast<size_t>(s_.nx);
            for (int i = 0; i < s_.nx; ++i) {
                const size_t k = row + static_cast<size_t>(i);
                const float root = smoothstep(0.0f, kRootFade * (0.35f + 1.3f * mix_[k]), h);
                out[k] = contrast(temp_[k] * root, s_.sharpness);
            }
        }
        frames_.push_back(std::move(out));
    }

    // S-curve about 0.5: keeps the white-hot core at 1 and crushes the haze.
    static float contrast(float t, float s) {
        const float v = saturate(t);
        if (std::fabs(s - 1.0f) < 1e-4f) return v;
        return v < 0.5f ? 0.5f * std::pow(2.0f * v, s) : 1.0f - 0.5f * std::pow(2.0f * (1.0f - v), s);
    }

    FireSimSettings s_;
    float cell_ = 1.0f;
    float drift_ = 1.5f;
    int fnx_ = 2, fny_ = 2;
    float hx_ = 1.0f, hy_ = 1.0f;
    int key_ = 0;
    Field field_a_, field_b_;
    std::vector<float> temp_, next_, vx_, vy_, mix_, patch_;
    std::vector<std::vector<float>> frames_;
};

FireSimSettings fire_sim_settings(const json& p, const Ctx& ctx, uint32_t seed) {
    FireSimSettings s;
    s.nx = ctx.width;
    s.ny = ctx.height;
    s.frames = std::max(1, ctx.frames);
    s.fuel = clamp(param_float(p, "fuel", s.fuel), 0.0f, 2.0f);
    s.fuel_width = clamp(param_float(p, "fuel_width", s.fuel_width), 0.02f, 1.0f);
    s.buoyancy = clamp(param_float(p, "buoyancy", s.buoyancy), 0.0f, 8.0f);
    s.turbulence = clamp(param_float(p, "turbulence", s.turbulence), 0.0f, 4.0f);
    s.turbulence_scale = clamp(param_float(p, "turbulence_scale", s.turbulence_scale), 0.25f, 16.0f);
    s.cooling = clamp(param_float(p, "cooling", s.cooling), 0.0f, 8.0f);
    s.detail = clamp(param_int(p, "detail", s.detail), 1, 5);
    s.speed = clamp(param_float(p, "speed", s.speed), 0.002f, 0.5f);
    s.substeps = clamp(param_int(p, "substeps", s.substeps), 1, 8);
    s.warmup = clamp(param_int(p, "warmup", s.warmup), 0, 512);
    s.loop = param_bool(p, "loop", s.loop);
    s.flicker = clamp(param_float(p, "flicker", s.flicker), 0.0f, 1.0f);
    s.sharpness = clamp(param_float(p, "sharpness", s.sharpness), 0.1f, 8.0f);
    s.seed = seed;
    return s;
}

// The whole flipbook is simulated once and cached on the bake, then read back a
// frame at a time: the graph is evaluated per frame, but the sim is not.
Image eval_fire_sim(const json& p, const Ctx& ctx, uint32_t seed, const std::string& id) {
    FireSimCache local;
    FireSimCache& cache = ctx.fire != nullptr ? *ctx.fire : local;
    auto it = cache.by_node.find(id);
    if (it == cache.by_node.end()) {
        FireSim sim(fire_sim_settings(p, ctx, seed));
        std::vector<std::vector<float>> baked;
        baked.reserve(static_cast<size_t>(std::max(1, ctx.frames)));
        for (int f = 0; f < std::max(1, ctx.frames); ++f) baked.push_back(sim.frame(f));
        it = cache.by_node.emplace(id, std::move(baked)).first;
    }
    const std::vector<std::vector<float>>& baked = it->second;
    const int index = clamp(ctx.frame, 0, static_cast<int>(baked.size()) - 1);
    const std::vector<float>& heat = baked[static_cast<size_t>(index)];
    Image out(ctx.width, ctx.height);
    for (int y = 0; y < ctx.height; ++y) {
        for (int x = 0; x < ctx.width; ++x) {
            set_gray(out, x, y, heat[static_cast<size_t>(y) * static_cast<size_t>(ctx.width) + static_cast<size_t>(x)]);
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
        if (op == "shape") return eval_shape(p, ctx_);
        if (op == "cracks") return eval_cracks(p, ctx_, seed);
        if (op == "voronoi") return eval_voronoi(p, ctx_, seed);
        if (op == "flame") return eval_flame(p, ctx_, seed);
        if (op == "fire_sim") return eval_fire_sim(p, ctx_, seed, node.id);
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

    const auto fire_cache = std::make_shared<FireSimCache>();
    for (int f = 0; f < frames; ++f) {
        Ctx ctx;
        ctx.width = options.width;
        ctx.height = options.height;
        ctx.frames = frames;
        ctx.frame = f;
        ctx.time = static_cast<float>(f) / static_cast<float>(frames);
        ctx.seed = options.seed;
        ctx.fire = fire_cache;
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
