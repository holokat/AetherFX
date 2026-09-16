// AetherFX software reference renderer (docs/ARCHITECTURE.md section 7).
//
// Pipeline, entirely in linear HDR float RGB:
//
//   background -> ground plane (+ grid, + decals) -> opaque mesh instances
//     -> depth-sorted transparent pass (billboards, mesh particles, beams, trails)
//     -> box downsample from the supersampled buffer
//     -> post effects (bloom, warp, chromatic aberration, exposure pulse) -> exposure
//
// The result is a linear HDR RGBA Image. RGB is the beauty pass *including* the
// background colour; A is the coverage of scene content over that background, seeded from
// `settings.background.a`. The default background is opaque, so previews composite correctly;
// pass a background with alpha 0 to get a pure coverage matte for compositing the effect over
// something else. Tonemapping happens only in image_io.
//
// Determinism: no threads inside a render() call, no wall-clock reads except
// RenderStatistics::render_ms, fixed summation order everywhere, and a total order on the
// transparent sort key (view depth descending, submission index ascending). Rendering a whole
// *sequence* on several workers (frame_render_pool.cpp) gives each worker its own renderer, so
// that is deterministic too.
//
// Known V1 gaps (all deliberate; the primitive is still counted and, where it makes sense, still
// drawn without the effect):
//   * material.distortion    - refracting primitives are drawn normally, no screen-space warp.
//   * VolumeState            - the sim backend is a stub with no field data, so nothing is drawn.
//   * RenderSettings::motion_blur - ignored.
//   * material uv_scroll / uv_rotate / gradient_texture - ignored; base_color, opacity,
//     emissive, blend, shading, soft_particle, depth_fade, fresnel_power, dissolve, erosion,
//     noise_texture and temperature_gradient are honoured.
//   * DecalState::normal     - decals project onto the y = 0 plane only.
//   * TrailState::twist_deg  - ignored (ribbons stay camera facing).
//   * Meshes are not clipped against the near plane: a triangle with a vertex behind it is
//     dropped rather than split.
//   * material dissolve / erosion apply to billboards only; mesh particles are not eroded.

#include "aether/render/renderer.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <vector>

#include "aether/core/image.hpp"

namespace aether::render {
namespace {

// ---------------------------------------------------------------------------------------------
// Look constants. Deliberately fixed rather than exposed through RenderSettings so that a given
// FrameState always produces the same image.
// ---------------------------------------------------------------------------------------------
constexpr float kBackgroundDepth = 1.0e30f;  // finite sentinel; keeps soft-particle math NaN-free
constexpr float kGroundAmbient = 0.15f;
constexpr float kMeshAmbient = 0.15f;
// Shaded ("lit") billboard model, see shade_volumetric().
constexpr float kBillboardAmbient = 0.25f;
constexpr float kBillboardWrap = 0.35f;          // wrapped-Lambert softness of the terminator
constexpr float kBillboardTranslucency = 0.35f;  // back-lit bleed through the puff
// Fresnel rim, see fresnel_rim(): a low emissive_intensity still gives a visible edge.
constexpr float kFresnelMinEmissive = 0.35f;
constexpr float kFresnelScale = 2.0f;
constexpr int kNoiseSize = 64;  // built-in dissolve/erosion noise, generated once
constexpr float kBranchWidthScale = 0.6f;  // beam branch polylines are thinner than the main beam
constexpr float kGridSpacing = 1.0f;       // metres
constexpr int kMaxBlurRadius = 96;

// ---------------------------------------------------------------------------------------------
// Framebuffer
// ---------------------------------------------------------------------------------------------
struct Framebuffer {
    int w = 0;
    int h = 0;
    std::vector<Vec3> color;
    std::vector<float> depth;     // positive view-space distance along -Z; kBackgroundDepth = empty
    std::vector<float> coverage;  // 0..1

    Framebuffer(int width, int height, Vec3 background, float background_alpha)
        : w(width), h(height),
          color(static_cast<size_t>(width) * height, background),
          depth(static_cast<size_t>(width) * height, kBackgroundDepth),
          coverage(static_cast<size_t>(width) * height, background_alpha) {}

    size_t index(int x, int y) const { return static_cast<size_t>(y) * w + x; }
};

// src is *unpremultiplied* HDR radiance; `a` is coverage.
inline void blend_pixel(Framebuffer& fb, size_t i, Vec3 src, float a, BlendMode mode) {
    switch (mode) {
        case BlendMode::Additive: fb.color[i] += src * a; break;
        case BlendMode::Alpha: fb.color[i] = src * a + fb.color[i] * (1.0f - a); break;
        case BlendMode::Premultiplied: fb.color[i] = src + fb.color[i] * (1.0f - a); break;
    }
    fb.coverage[i] = 1.0f - (1.0f - fb.coverage[i]) * (1.0f - a);
}

// ---------------------------------------------------------------------------------------------
// Projection
// ---------------------------------------------------------------------------------------------
struct Projector {
    Mat4 view;
    Mat4 view_inv;
    Mat4 proj;
    float width = 1.0f;
    float height = 1.0f;
    float p00 = 1.0f;
    float p11 = 1.0f;
    float near_plane = 0.05f;
    float far_plane = 200.0f;
    Vec3 eye;
    // World-space camera basis (the rows of the orthonormal view matrix): +X right, +Y up,
    // +Z back (the camera looks down -Z), used to rotate view-space normals into world space.
    Vec3 cam_right{1, 0, 0};
    Vec3 cam_up{0, 1, 0};
    Vec3 cam_back{0, 0, 1};

    Vec3 to_view(Vec3 world) const { return view.transform_point(world); }
    Vec3 view_dir_to_world(Vec3 v) const { return cam_right * v.x + cam_up * v.y + cam_back * v.z; }
    Vec3 dir_to_view(Vec3 world_dir) const { return view.transform_vector(world_dir); }

    // Projects a view-space point. Returns false at/behind the near plane.
    bool project_view(Vec3 v, Vec2& px, float& depth) const {
        depth = -v.z;
        if (!(depth > near_plane)) return false;
        px.x = (v.x * p00 / depth * 0.5f + 0.5f) * width;
        px.y = (0.5f - v.y * p11 / depth * 0.5f) * height;
        return true;
    }
    bool project(Vec3 world, Vec2& px, float& depth) const { return project_view(to_view(world), px, depth); }

    // Pixels per world unit for a length perpendicular to the view axis at `depth`.
    // (p00 * 0.5 * width == p11 * 0.5 * height, so the scale is isotropic.)
    float pixel_scale(float depth) const { return p11 * 0.5f * height / depth; }
    float world_per_pixel(float depth) const { return depth / (p11 * 0.5f * height); }

    // Unnormalised world-space ray direction through a pixel centre; its view-space z is -1, so a
    // hit parameter t along it *is* the view depth.
    Vec3 ray_dir(int x, int y) const {
        float ndc_x = 2.0f * (static_cast<float>(x) + 0.5f) / width - 1.0f;
        float ndc_y = 1.0f - 2.0f * (static_cast<float>(y) + 0.5f) / height;
        return view_inv.transform_vector(Vec3{ndc_x / p00, ndc_y / p11, -1.0f});
    }
};

// ---------------------------------------------------------------------------------------------
// Rasterisers
// ---------------------------------------------------------------------------------------------

// Parallelogram centred at `c` with half-axes `ax`/`ay` (pixel space). Calls
// frag(x, y, u, v) with u,v in [0,1] for each covered pixel centre. Camera-facing quads project
// to exact parallelograms (they are planar and at constant view depth), so this is both faster
// and seam-free compared with splitting into triangles.
template <class F>
void raster_parallelogram(int w, int h, Vec2 c, Vec2 ax, Vec2 ay, F&& frag) {
    const float det = ax.x * ay.y - ay.x * ax.y;
    if (std::fabs(det) < 1e-12f) return;
    const float inv = 1.0f / det;

    const float ex = std::fabs(ax.x) + std::fabs(ay.x);
    const float ey = std::fabs(ax.y) + std::fabs(ay.y);
    int x0 = static_cast<int>(std::floor(c.x - ex));
    int x1 = static_cast<int>(std::ceil(c.x + ex));
    int y0 = static_cast<int>(std::floor(c.y - ey));
    int y1 = static_cast<int>(std::ceil(c.y + ey));
    x0 = std::max(x0, 0); y0 = std::max(y0, 0);
    x1 = std::min(x1, w - 1); y1 = std::min(y1, h - 1);
    if (x0 > x1 || y0 > y1) return;

    // s = ( ay.y*dx - ay.x*dy) / det, t = (-ax.y*dx + ax.x*dy) / det   (inverse of [ax ay]).
    const float ds_dx = ay.y * inv, ds_dy = -ay.x * inv;
    const float dt_dx = -ax.y * inv, dt_dy = ax.x * inv;
    for (int y = y0; y <= y1; ++y) {
        const float dy = (static_cast<float>(y) + 0.5f) - c.y;
        const float dx0 = (static_cast<float>(x0) + 0.5f) - c.x;
        float s = ds_dx * dx0 + ds_dy * dy;
        float t = dt_dx * dx0 + dt_dy * dy;
        for (int x = x0; x <= x1; ++x, s += ds_dx, t += dt_dx) {
            if (s < -1.0f || s > 1.0f || t < -1.0f || t > 1.0f) continue;
            frag(x, y, s * 0.5f + 0.5f, t * 0.5f + 0.5f);
        }
    }
}

inline float edge_fn(Vec2 a, Vec2 b, Vec2 p) {
    return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
}
// Top-left rule: for a shared edge, exactly one of (a->b) / (b->a) fills the boundary, so two
// adjacent triangles never blend the same pixel twice.
inline bool edge_is_fill(Vec2 a, Vec2 b) {
    const float dy = b.y - a.y;
    return dy == 0.0f ? (b.x - a.x) > 0.0f : dy < 0.0f;
}

// Triangle with barycentric output: frag(x, y, b0, b1, b2).
template <class F>
void raster_triangle(int w, int h, Vec2 p0, Vec2 p1, Vec2 p2, F&& frag) {
    const float area = edge_fn(p0, p1, p2);
    if (std::fabs(area) < 1e-12f) return;
    const float sgn = area < 0.0f ? -1.0f : 1.0f;
    const float inv_area = 1.0f / (area * sgn);

    int x0 = static_cast<int>(std::floor(std::min(p0.x, std::min(p1.x, p2.x))));
    int x1 = static_cast<int>(std::ceil(std::max(p0.x, std::max(p1.x, p2.x))));
    int y0 = static_cast<int>(std::floor(std::min(p0.y, std::min(p1.y, p2.y))));
    int y1 = static_cast<int>(std::ceil(std::max(p0.y, std::max(p1.y, p2.y))));
    x0 = std::max(x0, 0); y0 = std::max(y0, 0);
    x1 = std::min(x1, w - 1); y1 = std::min(y1, h - 1);
    if (x0 > x1 || y0 > y1) return;

    const bool f0 = sgn > 0.0f ? edge_is_fill(p1, p2) : edge_is_fill(p2, p1);
    const bool f1 = sgn > 0.0f ? edge_is_fill(p2, p0) : edge_is_fill(p0, p2);
    const bool f2 = sgn > 0.0f ? edge_is_fill(p0, p1) : edge_is_fill(p1, p0);

    for (int y = y0; y <= y1; ++y) {
        for (int x = x0; x <= x1; ++x) {
            const Vec2 q{static_cast<float>(x) + 0.5f, static_cast<float>(y) + 0.5f};
            const float w0 = edge_fn(p1, p2, q) * sgn;
            const float w1 = edge_fn(p2, p0, q) * sgn;
            const float w2 = edge_fn(p0, p1, q) * sgn;
            if (w0 < 0.0f || (w0 == 0.0f && !f0)) continue;
            if (w1 < 0.0f || (w1 == 0.0f && !f1)) continue;
            if (w2 < 0.0f || (w2 == 0.0f && !f2)) continue;
            frag(x, y, w0 * inv_area, w1 * inv_area, w2 * inv_area);
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Lighting
// ---------------------------------------------------------------------------------------------

// Inverse-square with a smooth window that reaches exactly zero at `radius`, so lights have
// bounded influence: 1/(d^2+1) * clamp(1-(d/r)^4)^2.
inline float light_falloff(const LightState& l, float d) {
    float f = 1.0f / (d * d + 1.0f);
    if (l.radius > 0.0f) {
        const float x = saturate(1.0f - std::pow(d / l.radius, 4.0f));
        f *= x * x;
    }
    return f;
}

inline float light_cone(const LightState& l, Vec3 light_to_point) {
    if (l.type != LightType::Spot) return 1.0f;
    const Vec3 axis = normalize(l.direction);
    if (length(axis) < kEpsilon) return 1.0f;
    const float c = dot(light_to_point, axis);
    const float ca = std::cos(deg_to_rad(clamp(l.cone_angle_deg, 0.0f, 90.0f)));
    return smoothstep(ca, lerp(ca, 1.0f, 0.35f), c);
}

// Surface lighting for the ground plane and for meshes; area lights are treated as point lights
// in V1. Billboards use shade_volumetric() instead, which has its own wrapped Lambert term.
inline Vec3 light_shade(const LightState& l, Vec3 p, Vec3 n) {
    const Vec3 to_light = l.position - p;
    const float d = length(to_light);
    const Vec3 dir = d > kEpsilon ? to_light / d : Vec3{0.0f, 1.0f, 0.0f};
    const float lambert = std::max(0.0f, dot(n, dir));
    if (lambert <= 0.0f) return {};
    const float f = light_falloff(l, d) * light_cone(l, -dir);
    if (f <= 0.0f) return {};
    return l.color.rgb() * (l.intensity * lambert * f);
}

inline Vec3 gather_lights(const std::vector<LightState>& lights, Vec3 p, Vec3 n, float ambient) {
    Vec3 acc{ambient, ambient, ambient};
    for (const LightState& l : lights) acc += light_shade(l, p, n);
    return acc;
}

// One light reduced to what a shaded billboard needs per fragment. Everything that only depends
// on the particle centre (direction, distance falloff, spot cone) is computed once per particle;
// only the dot product with the per-fragment normal is left.
struct BillboardLight {
    Vec3 dir;       // unit direction from the particle towards the light
    Vec3 radiance;  // light colour * intensity * falloff * cone
};

inline void resolve_billboard_lights(const std::vector<LightState>& lights, Vec3 p,
                                     std::vector<BillboardLight>& out) {
    out.clear();
    for (const LightState& l : lights) {
        const Vec3 to_light = l.position - p;
        const float d = length(to_light);
        const Vec3 dir = d > kEpsilon ? to_light / d : Vec3{0.0f, 1.0f, 0.0f};
        const float f = light_falloff(l, d) * light_cone(l, -dir);
        if (f <= 0.0f) continue;
        out.push_back(BillboardLight{dir, l.color.rgb() * (l.intensity * f)});
    }
}

// Shaded ("lit") billboard model - upgrade 1. A camera-facing quad is treated as the sphere it
// stands in for, so a smoke puff gets a lit side and a dark side instead of reading as a decal:
//
//   n_view = (dx, dy, sqrt(max(0, 1 - dx*dx - dy*dy)))    (dx, dy) = fragment position in [-1,1]
//   n      = camera basis (right, up, back) * n_view      -> world space
//   wrap   = saturate((dot(n, L) + 0.35) / 1.35)          soft terminator: a puff has no hard edge
//   trans  = 0.35 * max(0, dot(-n, L))                    light behind the puff bleeds through it
//   shade  = 0.25 + sum over lights of radiance * (wrap + trans)
//
// The caller multiplies `shade` by the particle/material colour and adds emission on top, so an
// unlit particle (Shading::Unlit) is untouched by any of this.
inline Vec3 shade_volumetric(const std::vector<BillboardLight>& lights, Vec3 n) {
    Vec3 acc{kBillboardAmbient, kBillboardAmbient, kBillboardAmbient};
    for (const BillboardLight& l : lights) {
        const float nd = dot(n, l.dir);
        const float wrap = saturate((nd + kBillboardWrap) / (1.0f + kBillboardWrap));
        const float trans = kBillboardTranslucency * std::max(0.0f, -nd);
        const float term = wrap + trans;
        if (term > 0.0f) acc += l.radiance * term;
    }
    return acc;
}

// Fresnel rim - upgrade 2. `v` is the unit direction from the surface to the camera; the caller
// scales this by material.emissive_color * max(emissive_intensity, 0.35) * 2, so a crystal with a
// low emissive intensity still gets a glowing silhouette. Added to the shaded colour before the
// post chain, hence before bloom.
inline float fresnel_rim(Vec3 n, Vec3 v, float power) {
    return std::pow(1.0f - saturate(dot(n, v)), power);
}

// The per-material rim colour: material.emissive_color * max(emissive_intensity, 0.35) * 2.
inline Vec3 fresnel_color_of(const MaterialDesc& m) {
    return m.emissive_color.rgb() * (std::max(m.emissive_intensity, kFresnelMinEmissive) * kFresnelScale);
}

// ---------------------------------------------------------------------------------------------
// Built-in dissolve / erosion noise - upgrade 3.
//
// A self-contained hash-based value noise (3 octaves, lacunarity 2, gain 0.5) baked once into a
// 64x64 image. The lattice wraps at each octave's period, so the image tiles seamlessly and a
// per-particle UV offset never shows a seam. Deliberately independent of aether::procedural: the
// renderer must not depend on the texture compiler to shade a fragment.
// ---------------------------------------------------------------------------------------------
inline uint32_t noise_hash(uint32_t x, uint32_t y, uint32_t seed) {
    uint32_t h = x * 374761393u + y * 668265263u + seed * 2246822519u;
    h = (h ^ (h >> 13)) * 1274126177u;
    return h ^ (h >> 16);
}

inline float hash_unit(uint32_t x, uint32_t y, uint32_t seed) {
    return static_cast<float>(noise_hash(x, y, seed) >> 8) / 16777216.0f;  // [0,1)
}

inline float lattice(int x, int y, int period, uint32_t seed) {
    const uint32_t ux = static_cast<uint32_t>(((x % period) + period) % period);
    const uint32_t uy = static_cast<uint32_t>(((y % period) + period) % period);
    return hash_unit(ux, uy, seed);
}

inline float value_noise(float x, float y, int period, uint32_t seed) {
    const int x0 = static_cast<int>(std::floor(x));
    const int y0 = static_cast<int>(std::floor(y));
    const float tx = smootherstep(x - static_cast<float>(x0));
    const float ty = smootherstep(y - static_cast<float>(y0));
    const float a = lattice(x0, y0, period, seed);
    const float b = lattice(x0 + 1, y0, period, seed);
    const float c = lattice(x0, y0 + 1, period, seed);
    const float d = lattice(x0 + 1, y0 + 1, period, seed);
    return lerp(lerp(a, b, tx), lerp(c, d, tx), ty);
}

const Image& built_in_noise() {
    static const Image image = [] {
        Image n(kNoiseSize, kNoiseSize, Color::black());
        for (int y = 0; y < kNoiseSize; ++y) {
            for (int x = 0; x < kNoiseSize; ++x) {
                const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(kNoiseSize);
                const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(kNoiseSize);
                float sum = 0.0f;
                float norm = 0.0f;
                float amp = 1.0f;
                int period = 4;
                for (int octave = 0; octave < 3; ++octave) {
                    sum += amp * value_noise(u * static_cast<float>(period), v * static_cast<float>(period), period,
                                             1013u + static_cast<uint32_t>(octave) * 7919u);
                    norm += amp;
                    amp *= 0.5f;
                    period *= 2;
                }
                const float value = saturate(norm > 0.0f ? sum / norm : 0.0f);
                n.set(x, y, Color{value, value, value, 1.0f});
            }
        }
        return n;
    }();
    return image;
}

// ---------------------------------------------------------------------------------------------
// Default (procedural) particle sprite: a soft radial falloff (1 - r^2)^2 over the quad.
// ---------------------------------------------------------------------------------------------
inline float default_sprite_alpha(float u, float v) {
    const float sx = u * 2.0f - 1.0f;
    const float sy = v * 2.0f - 1.0f;
    const float a = std::max(0.0f, 1.0f - (sx * sx + sy * sy));
    return a * a;
}

// ---------------------------------------------------------------------------------------------
// Resolved, per-primitive-source shading data (computed once, used per fragment).
// ---------------------------------------------------------------------------------------------
struct ResolvedParticles {
    const ParticleBuffer* buf = nullptr;
    const TextureResource* sprite = nullptr;
    const Image* noise = nullptr;  // dissolve/erosion mask source (material or built-in)
    const MeshData* mesh = nullptr;
    const Gradient* temperature = nullptr;
    BlendMode blend = BlendMode::Additive;
    bool lit = false;
    bool soft = false;
    bool distortion = false;  // V1: counted, the refraction itself is not applied (see notes)
    float soft_distance = 0.0f;
    Vec3 base_color{1.0f, 1.0f, 1.0f};
    Vec3 emissive_color{1.0f, 1.0f, 1.0f};
    float material_opacity = 1.0f;
    float emissive_intensity = 0.0f;
    float fresnel_power = 0.0f;
    Vec3 fresnel_color{};
    float dissolve = 0.0f;
    float erosion = 0.0f;
    int sprite_columns = 1;
    int sprite_rows = 1;
    int sprite_cells = 1;
    int texture_frames = 1;
    bool eroded() const { return dissolve > 0.0f || erosion > 0.0f; }
};

struct ResolvedSimple {  // beams / trails
    Vec3 base_color{1.0f, 1.0f, 1.0f};
    float emissive_scale = 1.0f;
    float opacity = 1.0f;
    BlendMode blend = BlendMode::Additive;
    bool soft = false;
    float soft_distance = 0.0f;
};

// ---------------------------------------------------------------------------------------------
// Transparent draw list
// ---------------------------------------------------------------------------------------------
enum class DrawKind : uint8_t { ParticleQuad, ParticleMesh, BeamSegment, TrailSegment };

struct DrawItem {
    float depth = 0.0f;   // view depth of the primitive centre
    uint32_t order = 0;   // submission index; breaks depth ties deterministically
    DrawKind kind = DrawKind::ParticleQuad;
    uint32_t owner = 0;   // buffer index (particles)
    uint32_t item = 0;    // particle index, or index into beam_segs / trail_segs
};

struct BeamSeg {
    uint32_t beam = 0;
    Vec3 a, b;
    float half_width = 0.0f;
    float t0 = 0.0f, t1 = 0.0f;  // normalised position along the polyline
};

struct TrailSeg {
    uint32_t trail = 0;
    const TrailVertex* a = nullptr;
    const TrailVertex* b = nullptr;
};

// ---------------------------------------------------------------------------------------------
// Post processing helpers (operate on the downsampled output image).
// ---------------------------------------------------------------------------------------------
void gaussian_blur_rgb(std::vector<Vec3>& buf, int w, int h, float sigma) {
    if (w <= 0 || h <= 0 || sigma <= 0.0f) return;
    int radius = static_cast<int>(std::ceil(sigma * 3.0f));
    radius = clamp(radius, 1, kMaxBlurRadius);
    std::vector<float> kernel(static_cast<size_t>(radius) * 2 + 1);
    float sum = 0.0f;
    const float inv2s2 = 1.0f / (2.0f * sigma * sigma);
    for (int i = -radius; i <= radius; ++i) {
        const float v = std::exp(-static_cast<float>(i) * static_cast<float>(i) * inv2s2);
        kernel[static_cast<size_t>(i + radius)] = v;
        sum += v;
    }
    for (float& k : kernel) k /= sum;

    std::vector<Vec3> tmp(buf.size());
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            Vec3 acc{};
            for (int i = -radius; i <= radius; ++i) {
                const int sx = clamp(x + i, 0, w - 1);
                acc += buf[static_cast<size_t>(y) * w + sx] * kernel[static_cast<size_t>(i + radius)];
            }
            tmp[static_cast<size_t>(y) * w + x] = acc;
        }
    }
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            Vec3 acc{};
            for (int i = -radius; i <= radius; ++i) {
                const int sy = clamp(y + i, 0, h - 1);
                acc += tmp[static_cast<size_t>(sy) * w + x] * kernel[static_cast<size_t>(i + radius)];
            }
            buf[static_cast<size_t>(y) * w + x] = acc;
        }
    }
}

// Bright pass at half resolution, separable gaussian, bilinear upsample, additive combine.
void apply_bloom(Image& img, float threshold, float intensity, float radius_fraction) {
    if (img.empty() || intensity <= 0.0f) return;
    const int hw = std::max(1, img.width / 2);
    const int hh = std::max(1, img.height / 2);
    std::vector<Vec3> bright(static_cast<size_t>(hw) * hh);
    for (int y = 0; y < hh; ++y) {
        for (int x = 0; x < hw; ++x) {
            Vec3 acc{};
            int n = 0;
            for (int dy = 0; dy < 2; ++dy) {
                for (int dx = 0; dx < 2; ++dx) {
                    const int sx = std::min(x * 2 + dx, img.width - 1);
                    const int sy = std::min(y * 2 + dy, img.height - 1);
                    const Color c = img.get(sx, sy);
                    acc += Vec3{std::max(0.0f, c.r - threshold), std::max(0.0f, c.g - threshold),
                                std::max(0.0f, c.b - threshold)};
                    ++n;
                }
            }
            bright[static_cast<size_t>(y) * hw + x] = acc / static_cast<float>(n);
        }
    }
    const float sigma = std::max(0.5f, radius_fraction * static_cast<float>(img.width) * 0.5f);
    gaussian_blur_rgb(bright, hw, hh, sigma);

    for (int y = 0; y < img.height; ++y) {
        const float fy = clamp((static_cast<float>(y) + 0.5f) * 0.5f - 0.5f, 0.0f, static_cast<float>(hh - 1));
        const int y0 = static_cast<int>(fy);
        const int y1 = std::min(y0 + 1, hh - 1);
        const float ty = fy - static_cast<float>(y0);
        for (int x = 0; x < img.width; ++x) {
            const float fx = clamp((static_cast<float>(x) + 0.5f) * 0.5f - 0.5f, 0.0f, static_cast<float>(hw - 1));
            const int x0 = static_cast<int>(fx);
            const int x1 = std::min(x0 + 1, hw - 1);
            const float tx = fx - static_cast<float>(x0);
            const Vec3 b = lerp(lerp(bright[static_cast<size_t>(y0) * hw + x0], bright[static_cast<size_t>(y0) * hw + x1], tx),
                                lerp(bright[static_cast<size_t>(y1) * hw + x0], bright[static_cast<size_t>(y1) * hw + x1], tx), ty);
            float* p = img.at(x, y);
            p[0] += b.x * intensity;
            p[1] += b.y * intensity;
            p[2] += b.z * intensity;
        }
    }
}

// Screen-space warp shared by `heat_haze` and `distortion`: a deterministic sinusoidal field of
// the normalised pixel coordinate and the post effect's time. RGBA are warped together so
// coverage follows the image.
void apply_warp(Image& img, float intensity, float radius, double time) {
    if (img.empty() || intensity <= 0.0f) return;
    const Image src = img;
    const float t = static_cast<float>(time);
    const float amp = clamp(intensity * radius * static_cast<float>(img.width) * 0.5f, 0.0f, 32.0f);
    if (amp <= 0.0f) return;
    const float fw = static_cast<float>(img.width);
    const float fh = static_cast<float>(img.height);
    for (int y = 0; y < img.height; ++y) {
        const float ny = (static_cast<float>(y) + 0.5f) / fh;
        for (int x = 0; x < img.width; ++x) {
            const float nx = (static_cast<float>(x) + 0.5f) / fw;
            const float ox = std::sin(ny * 37.0f + t * 2.1f) * 0.6f +
                             std::sin(nx * 23.0f + ny * 17.0f + t * 1.3f) * 0.4f;
            const float oy = std::cos(nx * 31.0f - t * 1.7f) * 0.5f +
                             std::sin(nx * 13.0f - ny * 29.0f - t * 2.3f) * 0.5f;
            const Color c = src.sample((static_cast<float>(x) + 0.5f + ox * amp) / fw,
                                       (static_cast<float>(y) + 0.5f + oy * amp) / fh, WrapMode::Clamp);
            img.set(x, y, c);
        }
    }
}

// Radial R/B separation of intensity*2 pixels.
void apply_chromatic_aberration(Image& img, float intensity) {
    if (img.empty() || intensity <= 0.0f) return;
    const Image src = img;
    const float fw = static_cast<float>(img.width);
    const float fh = static_cast<float>(img.height);
    const float cx = fw * 0.5f, cy = fh * 0.5f;
    const float shift = intensity * 2.0f;
    for (int y = 0; y < img.height; ++y) {
        for (int x = 0; x < img.width; ++x) {
            const float px = static_cast<float>(x) + 0.5f;
            const float py = static_cast<float>(y) + 0.5f;
            const Vec2 d = normalize(Vec2{px - cx, py - cy});
            const Color r = src.sample((px + d.x * shift) / fw, (py + d.y * shift) / fh, WrapMode::Clamp);
            const Color b = src.sample((px - d.x * shift) / fw, (py - d.y * shift) / fh, WrapMode::Clamp);
            const Color g = src.get(x, y);
            img.set(x, y, Color{r.r, g.g, b.b, g.a});
        }
    }
}

// ---------------------------------------------------------------------------------------------
// The renderer
// ---------------------------------------------------------------------------------------------
class SoftwareRenderer final : public IRenderer {
public:
    std::string name() const override { return "software"; }
    RenderStatistics last_statistics() const override { return stats_; }

    Image render(const FrameState& state, const ResourceSet& resources, const CameraDesc& camera,
                 const RenderSettings& settings) override {
        const auto clock_start = std::chrono::steady_clock::now();
        stats_ = RenderStatistics{};
        fragments_ = 0;
        distortion_prims_ = 0;
        ribbon_particles_ = 0;
        unknown_meshes_ = 0;

        const int out_w = std::max(1, settings.width);
        const int out_h = std::max(1, settings.height);
        const int ss = clamp(settings.supersample, 1, 4);
        const int w = out_w * ss;
        const int h = out_h * ss;

        Projector proj;
        proj.view = camera.view();
        proj.view_inv = proj.view.inverse();
        proj.width = static_cast<float>(w);
        proj.height = static_cast<float>(h);
        const float aspect = static_cast<float>(out_w) / static_cast<float>(out_h);
        proj.proj = camera.projection(aspect);
        proj.p00 = proj.proj.at(0, 0);
        proj.p11 = proj.proj.at(1, 1);
        proj.near_plane = std::max(1e-4f, camera.near_plane);
        proj.far_plane = std::max(proj.near_plane * 2.0f, camera.far_plane);
        proj.eye = camera.position;
        proj.cam_right = Vec3{proj.view.at(0, 0), proj.view.at(0, 1), proj.view.at(0, 2)};
        proj.cam_up = Vec3{proj.view.at(1, 0), proj.view.at(1, 1), proj.view.at(1, 2)};
        proj.cam_back = Vec3{proj.view.at(2, 0), proj.view.at(2, 1), proj.view.at(2, 2)};

        Framebuffer fb(w, h, settings.background.rgb(), saturate(settings.background.a));

        draw_ground(fb, proj, state, resources, settings);
        draw_opaque_meshes(fb, proj, state, resources, settings);
        draw_transparent(fb, proj, state, resources, settings);
        // Volumes are a V1 stub: VolumeState carries no field data, so nothing is drawn.
        volumes_ = state.volumes.size();

        Image img = downsample(fb, out_w, out_h, ss);
        apply_post_effects(img, state, camera, settings);

        // Fragments and overdraw are both measured at the supersampled resolution, so overdraw
        // stays comparable across supersample settings.
        stats_.fragments_shaded = fragments_;
        const double pixels = static_cast<double>(w) * static_cast<double>(h);
        stats_.overdraw = pixels > 0.0 ? static_cast<double>(fragments_) / pixels : 0.0;
        stats_.render_ms =
            std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - clock_start).count();
        return img;
    }

private:
    RenderStatistics stats_;
    size_t fragments_ = 0;
    size_t distortion_prims_ = 0;
    size_t ribbon_particles_ = 0;
    size_t unknown_meshes_ = 0;
    size_t volumes_ = 0;

    // -----------------------------------------------------------------------------------------
    // Ground plane, grid and decals (a per-pixel ray cast against y = 0).
    // -----------------------------------------------------------------------------------------
    void draw_ground(Framebuffer& fb, const Projector& proj, const FrameState& state,
                     const ResourceSet& resources, const RenderSettings& settings) {
        if (!settings.ground_plane) return;
        if (proj.eye.y <= 0.0f) return;  // camera below the plane: nothing to see in V1

        // Pre-resolve decal textures/materials once.
        struct ResolvedDecal {
            const DecalState* d = nullptr;
            const TextureResource* tex = nullptr;
            Vec3 color{1.0f, 1.0f, 1.0f};
            float cos_r = 1.0f, sin_r = 0.0f;
            float inv_sx = 1.0f, inv_sy = 1.0f;
            // Animated texture frame (upgrade 4), selected by the effect time.
            float frame = 0.0f;
            float inv_frames = 1.0f;
            float frame_u0 = 0.0f, frame_u1 = 1.0f;
            bool clamp_frame = false;
        };
        std::vector<ResolvedDecal> decals;
        decals.reserve(state.decals.size());
        for (const DecalState& d : state.decals) {
            ResolvedDecal rd;
            rd.d = &d;
            rd.tex = d.texture_id.empty() ? nullptr : resources.texture(d.texture_id);
            rd.color = d.color.rgb();
            if (const MaterialDesc* m = d.material_id.empty() ? nullptr : resources.material(d.material_id))
                rd.color = rd.color * m->base_color.rgb();
            const float r = deg_to_rad(d.rotation_deg);
            rd.cos_r = std::cos(r);
            rd.sin_r = std::sin(r);
            rd.inv_sx = std::fabs(d.size.x) > kEpsilon ? 1.0f / d.size.x : 0.0f;
            rd.inv_sy = std::fabs(d.size.y) > kEpsilon ? 1.0f / d.size.y : 0.0f;
            const int frames = rd.tex ? std::max(1, rd.tex->frames) : 1;
            rd.inv_frames = 1.0f / static_cast<float>(frames);
            if (frames > 1) {
                // A baked animated decal texture plays at a fixed 8 fps off the effect time:
                // frame = floor(time * 8) mod frames. Deliberately simple; decals are backdrops.
                const double ff = std::floor(state.time * 8.0);
                const double wrapped = ff - std::floor(ff / frames) * frames;
                rd.frame = static_cast<float>(clamp(static_cast<int>(wrapped), 0, frames - 1));
                rd.clamp_frame = true;
                const float half_texel =
                    rd.tex->image.width > 0 ? 0.5f / static_cast<float>(rd.tex->image.width) : 0.0f;
                rd.frame_u0 = rd.frame * rd.inv_frames + half_texel;
                rd.frame_u1 = (rd.frame + 1.0f) * rd.inv_frames - half_texel;
                if (rd.frame_u1 < rd.frame_u0) rd.frame_u0 = rd.frame_u1 = (rd.frame_u0 + rd.frame_u1) * 0.5f;
            }
            decals.push_back(rd);
        }

        const float fade_start = proj.far_plane * 0.6f;
        const Vec3 background = fb.color.empty() ? Vec3{} : fb.color[0];

        for (int y = 0; y < fb.h; ++y) {
            for (int x = 0; x < fb.w; ++x) {
                const Vec3 dir = proj.ray_dir(x, y);
                if (dir.y >= -1e-6f) continue;
                const float t = -proj.eye.y / dir.y;  // == view depth (ray has view-space z = -1)
                if (t <= proj.near_plane || t >= proj.far_plane) continue;
                const Vec3 hit = proj.eye + dir * t;

                Vec3 lit = gather_lights(state.lights, hit, Vec3::up(), kGroundAmbient);
                Vec3 col = lit * settings.ground_albedo;

                if (settings.grid) col = apply_grid(col, hit, t, proj);
                for (const ResolvedDecal& rd : decals) col = apply_decal(col, rd, hit);

                // Soft horizon: blend to the background over the last 40% of the far plane so the
                // ground does not end in a hard line.
                const float fade = 1.0f - smoothstep(fade_start, proj.far_plane, t);
                const size_t i = fb.index(x, y);
                fb.color[i] = lerp(background, col, fade);
                fb.depth[i] = t;
                fb.coverage[i] = 1.0f - (1.0f - fb.coverage[i]) * (1.0f - fade);
                ++fragments_;
            }
        }
    }

    // Thin darker lines every metre with a small constant lift so they stay readable on an
    // unlit ground, fading out with distance; axes are stronger.
    static Vec3 apply_grid(Vec3 col, Vec3 hit, float depth, const Projector& proj) {
        const float line_w = proj.world_per_pixel(depth) * 1.2f;
        if (line_w <= 0.0f) return col;
        const float dx = std::fabs(fract(hit.x / kGridSpacing + 0.5f) - 0.5f) * kGridSpacing;
        const float dz = std::fabs(fract(hit.z / kGridSpacing + 0.5f) - 0.5f) * kGridSpacing;
        float g = std::max(1.0f - smoothstep(0.0f, line_w, dx), 1.0f - smoothstep(0.0f, line_w, dz));
        const float ax = std::max(1.0f - smoothstep(0.0f, line_w * 1.6f, std::fabs(hit.x)),
                                  1.0f - smoothstep(0.0f, line_w * 1.6f, std::fabs(hit.z)));
        g = std::max(g * 0.75f, ax);
        g *= 1.0f / (1.0f + depth * 0.06f);
        if (g <= 0.0f) return col;
        const Vec3 line = col * 0.45f + Vec3{0.012f, 0.013f, 0.016f};
        return lerp(col, line, saturate(g));
    }

    template <class RD>
    static Vec3 apply_decal(Vec3 col, const RD& rd, Vec3 hit) {
        const DecalState& d = *rd.d;
        const float rx = hit.x - d.position.x;
        const float rz = hit.z - d.position.z;
        const float lx = rx * rd.cos_r + rz * rd.sin_r;
        const float lz = -rx * rd.sin_r + rz * rd.cos_r;
        const float u = lx * rd.inv_sx + 0.5f;
        const float v = lz * rd.inv_sy + 0.5f;
        if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) return col;

        Vec3 src = rd.color;
        float a = d.opacity;
        if (rd.tex && !rd.tex->image.empty()) {
            float fu = (rd.frame + u) * rd.inv_frames;
            if (rd.clamp_frame) fu = clamp(fu, rd.frame_u0, rd.frame_u1);
            const Color c = rd.tex->image.sample(fu, v, WrapMode::Clamp);
            src = src * c.rgb();
            a *= c.a;
        } else if (d.circle) {
            const float r = std::sqrt((u * 2.0f - 1.0f) * (u * 2.0f - 1.0f) + (v * 2.0f - 1.0f) * (v * 2.0f - 1.0f));
            a *= 1.0f - smoothstep(0.85f, 1.0f, r);
        }
        if (a <= 0.0f) return col;
        src += rd.color * d.emissive;
        switch (d.blend) {
            case BlendMode::Additive: return col + src * a;
            case BlendMode::Premultiplied: return src + col * (1.0f - a);
            case BlendMode::Alpha: default: return src * a + col * (1.0f - a);
        }
    }

    // -----------------------------------------------------------------------------------------
    // Opaque mesh instances
    // -----------------------------------------------------------------------------------------
    void draw_opaque_meshes(Framebuffer& fb, const Projector& proj, const FrameState& state,
                            const ResourceSet& resources, const RenderSettings& settings) {
        for (const MeshInstanceState& mi : state.meshes) {
            if (!mi.visible) continue;
            const MeshData* mesh = resources.mesh(mi.mesh_id);
            if (!mesh || mesh->indices.empty()) {
                ++unknown_meshes_;
                continue;
            }
            const MaterialDesc* mat = mi.material_id.empty() ? nullptr : resources.material(mi.material_id);
            MeshShading sh;
            sh.albedo = mi.color.rgb() * (mat ? mat->base_color.rgb() : Vec3::one());
            sh.lit = mat && mat->shading == Shading::Lit;
            // emissive = colour * instance emissive * material emissive_color * (1 + intensity)
            sh.emissive = sh.albedo * mi.emissive *
                          (mat ? mat->emissive_color.rgb() * (1.0f + mat->emissive_intensity) : Vec3::one());
            sh.fresnel_power = mat ? std::max(0.0f, mat->fresnel_power) : 0.0f;
            if (sh.fresnel_power > 0.0f) sh.fresnel_color = fresnel_color_of(*mat);
            sh.alpha = 1.0f;
            sh.blend = BlendMode::Alpha;
            sh.opaque = true;
            sh.depth_write = true;
            draw_mesh(fb, proj, state, settings, *mesh, mi.transform, sh);
        }
    }

    struct MeshShading {
        Vec3 albedo{1.0f, 1.0f, 1.0f};
        Vec3 emissive{};
        Vec3 fresnel_color{};
        float fresnel_power = 0.0f;
        float alpha = 1.0f;
        bool lit = false;
        bool opaque = true;
        bool depth_write = true;
        BlendMode blend = BlendMode::Alpha;
    };

    void draw_mesh(Framebuffer& fb, const Projector& proj, const FrameState& state,
                   const RenderSettings& settings, const MeshData& mesh, const Mat4& transform,
                   const MeshShading& sh) {
        (void)settings;
        const size_t vcount = mesh.positions.size();
        if (vcount == 0) return;
        const Mat4 normal_mat = transform.inverse().transposed();
        const bool have_normals = mesh.normals.size() == vcount;
        const bool need_normal = sh.lit || sh.fresnel_power > 0.0f;

        // Transform + project once per vertex.
        scratch_pos_.assign(vcount, Vec3{});
        scratch_nrm_.assign(vcount, Vec3{0.0f, 1.0f, 0.0f});
        scratch_px_.assign(vcount, Vec2{});
        scratch_depth_.assign(vcount, 0.0f);
        scratch_ok_.assign(vcount, 0);
        for (size_t i = 0; i < vcount; ++i) {
            scratch_pos_[i] = transform.transform_point(mesh.positions[i]);
            if (have_normals) scratch_nrm_[i] = normalize(normal_mat.transform_vector(mesh.normals[i]));
            Vec2 px;
            float d = 0.0f;
            scratch_ok_[i] = proj.project(scratch_pos_[i], px, d) ? 1 : 0;
            scratch_px_[i] = px;
            scratch_depth_[i] = d;
        }

        for (size_t t = 0; t + 2 < mesh.indices.size(); t += 3) {
            const uint32_t i0 = mesh.indices[t], i1 = mesh.indices[t + 1], i2 = mesh.indices[t + 2];
            if (i0 >= vcount || i1 >= vcount || i2 >= vcount) continue;
            if (!scratch_ok_[i0] || !scratch_ok_[i1] || !scratch_ok_[i2]) continue;  // V1: no near clipping
            const float iw0 = 1.0f / scratch_depth_[i0];
            const float iw1 = 1.0f / scratch_depth_[i1];
            const float iw2 = 1.0f / scratch_depth_[i2];
            const Vec3 p0 = scratch_pos_[i0], p1 = scratch_pos_[i1], p2 = scratch_pos_[i2];
            const Vec3 n0 = scratch_nrm_[i0], n1 = scratch_nrm_[i1], n2 = scratch_nrm_[i2];
            const Vec3 flat_n = have_normals ? Vec3{} : normalize(cross(p1 - p0, p2 - p0));

            raster_triangle(fb.w, fb.h, scratch_px_[i0], scratch_px_[i1], scratch_px_[i2],
                            [&](int x, int y, float b0, float b1, float b2) {
                                const float iw = b0 * iw0 + b1 * iw1 + b2 * iw2;
                                if (iw <= 0.0f) return;
                                const float depth = 1.0f / iw;
                                const size_t idx = fb.index(x, y);
                                if (depth >= fb.depth[idx]) return;
                                const float w0 = b0 * iw0 * depth, w1 = b1 * iw1 * depth, w2 = b2 * iw2 * depth;
                                Vec3 rgb = sh.albedo;
                                if (need_normal) {
                                    const Vec3 wp = p0 * w0 + p1 * w1 + p2 * w2;
                                    Vec3 nn = have_normals ? normalize(n0 * w0 + n1 * w1 + n2 * w2) : flat_n;
                                    const Vec3 to_eye = proj.eye - wp;
                                    // double_sided: always shade the side facing the camera
                                    if (dot(nn, to_eye) < 0.0f) nn = -nn;
                                    if (sh.lit) rgb = rgb * gather_lights(state.lights, wp, nn, kMeshAmbient);
                                    if (sh.fresnel_power > 0.0f)
                                        rgb += sh.fresnel_color * fresnel_rim(nn, normalize(to_eye), sh.fresnel_power);
                                }
                                rgb += sh.emissive;
                                if (sh.opaque) {
                                    fb.color[idx] = rgb;
                                    fb.depth[idx] = depth;
                                    fb.coverage[idx] = 1.0f;  // opaque geometry fully covers
                                } else {
                                    blend_pixel(fb, idx, rgb, sh.alpha, sh.blend);
                                    if (sh.depth_write) fb.depth[idx] = depth;
                                }
                                ++fragments_;
                            });
        }
    }

    // -----------------------------------------------------------------------------------------
    // Transparent pass
    // -----------------------------------------------------------------------------------------
    void draw_transparent(Framebuffer& fb, const Projector& proj, const FrameState& state,
                          const ResourceSet& resources, const RenderSettings& settings) {
        std::vector<ResolvedParticles> buffers;
        buffers.reserve(state.particles.size());
        for (const ParticleBuffer& pb : state.particles) buffers.push_back(resolve_particles(pb, resources));

        std::vector<BeamSeg> beam_segs;
        std::vector<TrailSeg> trail_segs;
        std::vector<DrawItem> items;
        uint32_t order = 0;

        // --- particles -------------------------------------------------------------------
        for (size_t bi = 0; bi < buffers.size(); ++bi) {
            const ResolvedParticles& rp = buffers[bi];
            const ParticleBuffer& pb = *rp.buf;
            const size_t n = pb.count();
            stats_.particles_submitted += n;
            if (rp.distortion) distortion_prims_ += n;
            if (pb.render_mode == RenderMode::None) continue;
            if (pb.render_mode == RenderMode::Ribbon) {  // ribbons are drawn by the trail primitive
                ribbon_particles_ += n;
                continue;
            }
            const bool mesh_mode = pb.render_mode == RenderMode::Mesh;
            if (mesh_mode && !rp.mesh) continue;

            for (size_t pi = 0; pi < n; ++pi) {
                const Vec3 view_pos = proj.to_view(pb.position[pi]);
                const float depth = -view_pos.z;
                if (!(depth > proj.near_plane) || depth >= proj.far_plane) continue;
                const float size = pi < pb.size.size() ? pb.size[pi] : 0.0f;
                if (!(size > 0.0f)) continue;

                // Conservative on-screen test using the largest possible half extent.
                float half = size * 0.5f;
                if (pb.render_mode == RenderMode::StretchedBillboard && pi < pb.velocity.size()) {
                    const float speed = length(pb.velocity[pi]);
                    half = std::max(half, size * (1.0f + pb.velocity_stretch * speed) * 0.5f);
                }
                const float r = half * proj.pixel_scale(depth) * 1.5f;
                Vec2 px;
                float unused_depth = 0.0f;
                if (!proj.project_view(view_pos, px, unused_depth)) continue;
                if (px.x + r < 0.0f || px.x - r > proj.width || px.y + r < 0.0f || px.y - r > proj.height) continue;

                ++stats_.particles_drawn;
                DrawItem it;
                it.depth = depth;
                it.order = order++;
                it.kind = mesh_mode ? DrawKind::ParticleMesh : DrawKind::ParticleQuad;
                it.owner = static_cast<uint32_t>(bi);
                it.item = static_cast<uint32_t>(pi);
                items.push_back(it);
            }
        }

        // --- beams -----------------------------------------------------------------------
        for (size_t bi = 0; bi < state.beams.size(); ++bi) {
            const BeamState& beam = state.beams[bi];
            for (size_t li = 0; li < beam.polylines.size(); ++li) {
                const std::vector<Vec3>& pts = beam.polylines[li];
                if (pts.size() < 2) continue;
                const float width = beam.width * (li == 0 ? 1.0f : kBranchWidthScale);
                const float inv_span = 1.0f / static_cast<float>(pts.size() - 1);
                for (size_t si = 0; si + 1 < pts.size(); ++si) {
                    BeamSeg seg;
                    seg.beam = static_cast<uint32_t>(bi);
                    seg.a = pts[si];
                    seg.b = pts[si + 1];
                    seg.half_width = width * 0.5f;
                    seg.t0 = static_cast<float>(si) * inv_span;
                    seg.t1 = static_cast<float>(si + 1) * inv_span;
                    const float depth = -proj.to_view((seg.a + seg.b) * 0.5f).z;
                    if (!(depth > proj.near_plane) || depth >= proj.far_plane) continue;
                    DrawItem it;
                    it.depth = depth;
                    it.order = order++;
                    it.kind = DrawKind::BeamSegment;
                    it.item = static_cast<uint32_t>(beam_segs.size());
                    beam_segs.push_back(seg);
                    items.push_back(it);
                }
            }
        }

        // --- trails ----------------------------------------------------------------------
        for (size_t ti = 0; ti < state.trails.size(); ++ti) {
            const TrailState& tr = state.trails[ti];
            for (const std::vector<TrailVertex>& ribbon : tr.ribbons) {
                for (size_t vi = 0; vi + 1 < ribbon.size(); ++vi) {
                    TrailSeg seg;
                    seg.trail = static_cast<uint32_t>(ti);
                    seg.a = &ribbon[vi];
                    seg.b = &ribbon[vi + 1];
                    const float depth = -proj.to_view((seg.a->position + seg.b->position) * 0.5f).z;
                    if (!(depth > proj.near_plane) || depth >= proj.far_plane) continue;
                    DrawItem it;
                    it.depth = depth;
                    it.order = order++;
                    it.kind = DrawKind::TrailSegment;
                    it.item = static_cast<uint32_t>(trail_segs.size());
                    trail_segs.push_back(seg);
                    items.push_back(it);
                }
            }
        }

        // Back to front, ties broken by submission index: a total order, hence deterministic.
        std::sort(items.begin(), items.end(), [](const DrawItem& a, const DrawItem& b) {
            if (a.depth != b.depth) return a.depth > b.depth;
            return a.order < b.order;
        });

        std::vector<ResolvedSimple> beam_mats;
        beam_mats.reserve(state.beams.size());
        for (const BeamState& beam : state.beams) {
            ResolvedSimple rs;
            rs.blend = beam.blend;
            rs.base_color = beam.color.rgb();
            rs.emissive_scale = beam.emissive;
            rs.opacity = beam.color.a;
            if (const MaterialDesc* m = beam.material_id.empty() ? nullptr : resources.material(beam.material_id)) {
                rs.base_color = rs.base_color * m->base_color.rgb();
                rs.emissive_scale *= 1.0f + m->emissive_intensity;
                rs.opacity *= m->opacity;
                rs.blend = m->blend;
                rs.soft = m->soft_particle;
                rs.soft_distance = m->depth_fade;
            }
            beam_mats.push_back(rs);
        }
        std::vector<ResolvedSimple> trail_mats;
        trail_mats.reserve(state.trails.size());
        for (const TrailState& tr : state.trails) {
            ResolvedSimple rs;
            rs.blend = tr.blend;
            rs.soft_distance = 0.1f;
            rs.soft = true;
            if (const MaterialDesc* m = tr.material_id.empty() ? nullptr : resources.material(tr.material_id)) {
                rs.base_color = m->base_color.rgb();
                rs.emissive_scale = 1.0f + m->emissive_intensity;
                rs.opacity = m->opacity;
                rs.blend = m->blend;
                rs.soft = m->soft_particle;
                rs.soft_distance = m->depth_fade;
            }
            trail_mats.push_back(rs);
        }

        for (const DrawItem& it : items) {
            switch (it.kind) {
                case DrawKind::ParticleQuad:
                    draw_particle_quad(fb, proj, state, settings, buffers[it.owner], it.item);
                    break;
                case DrawKind::ParticleMesh:
                    draw_particle_mesh(fb, proj, state, settings, buffers[it.owner], it.item);
                    break;
                case DrawKind::BeamSegment:
                    draw_beam_segment(fb, proj, settings, state.beams[beam_segs[it.item].beam],
                                      beam_mats[beam_segs[it.item].beam], beam_segs[it.item]);
                    break;
                case DrawKind::TrailSegment:
                    draw_trail_segment(fb, proj, settings, trail_mats[trail_segs[it.item].trail],
                                       trail_segs[it.item]);
                    break;
            }
        }
    }

    static ResolvedParticles resolve_particles(const ParticleBuffer& pb, const ResourceSet& resources) {
        ResolvedParticles rp;
        rp.buf = &pb;
        rp.blend = pb.blend;
        rp.soft_distance = pb.soft_particle_distance;
        rp.sprite = pb.sprite_id.empty() ? nullptr : resources.texture(pb.sprite_id);
        rp.mesh = pb.mesh_id.empty() ? nullptr : resources.mesh(pb.mesh_id);
        rp.sprite_columns = std::max(1, pb.sprite_columns);
        rp.sprite_rows = std::max(1, pb.sprite_rows);
        rp.sprite_cells = rp.sprite_columns * rp.sprite_rows;
        rp.texture_frames = rp.sprite ? std::max(1, rp.sprite->frames) : 1;
        if (const MaterialDesc* m = pb.material_id.empty() ? nullptr : resources.material(pb.material_id)) {
            rp.base_color = m->base_color.rgb();
            rp.emissive_color = m->emissive_color.rgb();
            rp.material_opacity = m->opacity;
            rp.emissive_intensity = m->emissive_intensity;
            rp.blend = m->blend;
            rp.lit = m->shading == Shading::Lit;
            rp.soft = m->soft_particle;
            rp.distortion = m->distortion > 0.0f;
            rp.fresnel_power = std::max(0.0f, m->fresnel_power);
            if (rp.fresnel_power > 0.0f) rp.fresnel_color = fresnel_color_of(*m);
            rp.dissolve = saturate(m->dissolve);
            rp.erosion = saturate(m->erosion);
            if (rp.eroded()) {
                const TextureResource* noise = m->noise_texture.empty() ? nullptr : resources.texture(m->noise_texture);
                rp.noise = (noise && !noise->image.empty()) ? &noise->image : &built_in_noise();
            }
            if (!m->temperature_gradient.empty()) rp.temperature = &m->temperature_gradient;
            if (rp.sprite == nullptr && !m->base_texture.empty()) rp.sprite = resources.texture(m->base_texture);
        } else {
            rp.soft = true;  // no material: default MaterialDesc behaviour
        }
        return rp;
    }

    // Colour / alpha / emission shared by billboard and mesh particles.
    //
    //   colour   = particle.color * material.base_color [* temperature_gradient(1 - age/lifetime)]
    //   shaded   = unlit ? colour
    //                    : colour * (billboards: shade_volumetric(), meshes: gather_lights())
    //   emission = colour * material.emissive_color * (particle.emissive + material.emissive_intensity)
    //   src      = shaded + emission [+ fresnel rim],  alpha = sprite.a * particle.opacity *
    //              material.opacity [* dissolve/erosion mask]
    //
    // So emissive == 0 and emissive_intensity == 0 gives a plain (lit or unlit) particle.
    struct ParticleShade {
        Vec3 color{};
        Vec3 emission{};
        float alpha = 0.0f;
        float life01 = 0.0f;
    };

    static ParticleShade shade_particle(const ResolvedParticles& rp, size_t i) {
        const ParticleBuffer& pb = *rp.buf;
        ParticleShade s;
        const float life = i < pb.lifetime.size() ? pb.lifetime[i] : 0.0f;
        const float age = i < pb.age.size() ? pb.age[i] : 0.0f;
        s.life01 = life > 0.0f ? saturate(age / life) : 0.0f;
        Vec3 c = (i < pb.color.size() ? pb.color[i].rgb() : Vec3::one()) * rp.base_color;
        if (rp.temperature) c = c * rp.temperature->eval(1.0f - s.life01).rgb();
        s.color = c;
        const float emissive = i < pb.emissive.size() ? pb.emissive[i] : 0.0f;
        s.emission = c * rp.emissive_color * (emissive + rp.emissive_intensity);
        s.alpha = (i < pb.opacity.size() ? pb.opacity[i] : 1.0f) * rp.material_opacity;
        return s;
    }

    // Selects the sprite-sheet cell for a particle: sprite_fps > 0 plays at a fixed rate,
    // otherwise the cells are mapped across the particle's lifetime.
    static void sprite_cell(const ResolvedParticles& rp, const ParticleBuffer& pb, size_t i, float life01,
                            int& col, int& row) {
        col = 0;
        row = 0;
        if (rp.sprite_cells <= 1) return;
        int frame;
        if (pb.sprite_fps > 0.0f) {
            const float age = i < pb.age.size() ? pb.age[i] : 0.0f;
            frame = static_cast<int>(std::floor(age * pb.sprite_fps));
        } else {
            frame = static_cast<int>(std::floor(life01 * static_cast<float>(rp.sprite_cells)));
        }
        frame = ((frame % rp.sprite_cells) + rp.sprite_cells) % rp.sprite_cells;
        col = frame % rp.sprite_columns;
        row = frame / rp.sprite_columns;
    }

    // Normalised age. The runtime writes it into custom0 (docs/RUNTIME.md section 6); buffers
    // built by hand without it fall back to age / lifetime.
    static float particle_age_norm(const ResolvedParticles& rp, size_t i, float life01) {
        const ParticleBuffer& pb = *rp.buf;
        return i < pb.custom0.size() ? saturate(pb.custom0[i]) : life01;
    }

    // Animated texture frame - upgrade 4. TextureResource::frames bakes frames side by side, so
    // this selects the frame *rectangle* that the sprite_columns/rows sheet is then indexed
    // inside:
    //   sprite_fps > 0 : frame = floor(age * sprite_fps) mod frames  (a fixed playback rate)
    //   otherwise      : frame = floor(age_norm * frames) clamped    (one pass over the life)
    static int sprite_frame(const ResolvedParticles& rp, const ParticleBuffer& pb, size_t i, float age_norm) {
        if (rp.texture_frames <= 1) return 0;
        const float frames = static_cast<float>(rp.texture_frames);
        float frame;
        if (pb.sprite_fps > 0.0f) {
            const float age = i < pb.age.size() ? pb.age[i] : 0.0f;
            frame = std::floor(age * pb.sprite_fps);
            frame -= std::floor(frame / frames) * frames;  // positive modulo, overflow-free
        } else {
            frame = std::floor(age_norm * frames);
        }
        return clamp(static_cast<int>(frame), 0, rp.texture_frames - 1);
    }

    void draw_particle_quad(Framebuffer& fb, const Projector& proj, const FrameState& state,
                            const RenderSettings& settings, const ResolvedParticles& rp, uint32_t index) {
        const ParticleBuffer& pb = *rp.buf;
        const size_t i = index;
        const Vec3 view_pos = proj.to_view(pb.position[i]);
        const float depth = -view_pos.z;
        Vec2 center;
        float ignored = 0.0f;
        if (!proj.project_view(view_pos, center, ignored)) return;

        const float size = pb.size[i];
        const float rot = i < pb.rotation.size() ? pb.rotation[i] : 0.0f;
        // View space is camera space, so the camera-facing quad axes are simply X and Y there.
        Vec2 axis_a{std::cos(rot), std::sin(rot)};
        Vec2 axis_b{-axis_a.y, axis_a.x};
        float half_a = size * 0.5f;
        float half_b = size * 0.5f;

        const bool stretched = pb.render_mode == RenderMode::StretchedBillboard;
        if (stretched || pb.align_to_velocity) {
            const Vec3 vel_view = i < pb.velocity.size() ? proj.dir_to_view(pb.velocity[i]) : Vec3{};
            Vec2 screen_dir{vel_view.x, vel_view.y};
            if (length(screen_dir) > 1e-5f) {
                screen_dir = normalize(screen_dir);
                // Orient the quad along the screen-space velocity ...
                axis_b = screen_dir;
                axis_a = Vec2{screen_dir.y, -screen_dir.x};
                if (stretched) {
                    // ... and elongate it: length = size * (1 + velocity_stretch * speed).
                    const float speed = length(pb.velocity[i]);
                    half_b = size * (1.0f + pb.velocity_stretch * speed) * 0.5f;
                }
            }
        }

        // Project the view-space half-axes into pixel space (exact: the quad is planar and at
        // constant view depth, so the mapping is affine).
        const float sx = proj.p00 * 0.5f * proj.width / depth;
        const float sy = -proj.p11 * 0.5f * proj.height / depth;
        const Vec2 ax{axis_a.x * half_a * sx, axis_a.y * half_a * sy};
        const Vec2 ay{axis_b.x * half_b * sx, axis_b.y * half_b * sy};

        const ParticleShade sh = shade_particle(rp, i);
        if (sh.alpha <= 0.0f) return;
        int cell_col = 0, cell_row = 0;
        sprite_cell(rp, pb, i, sh.life01, cell_col, cell_row);
        const float age_norm = particle_age_norm(rp, i, sh.life01);

        // --- shading ---------------------------------------------------------------------
        // Unlit billboards keep a single colour for the whole quad. Lit ones are shaded per
        // fragment against a volumetric normal (upgrade 1) and pick up the fresnel rim when the
        // material asks for one (upgrade 2); both are added before post, hence before bloom.
        const bool volumetric = rp.lit;
        const bool rim = volumetric && rp.fresnel_power > 0.0f;
        Vec3 src_base{};
        Vec3 to_eye{};
        float long_scale = 1.0f;
        if (volumetric) {
            resolve_billboard_lights(state.lights, pb.position[i], scratch_lights_);
            if (rim) to_eye = normalize(proj.eye - pb.position[i]);
            // axis_a / axis_b are orthonormal in the view plane, so a round billboard's unit disc
            // maps straight onto the sphere it stands in for. A stretched billboard is a capsule
            // instead: its long (velocity) axis is compressed to the short axis' scale, so the
            // normal bends across the width and stays nearly flat along the streak.
            if (half_b > half_a && half_b > kEpsilon) long_scale = half_a / half_b;
        } else {
            src_base = sh.color + sh.emission;
        }

        // --- erosion / dissolve (upgrade 3) ----------------------------------------------
        // threshold = dissolve > 0 ? dissolve * (0.25 + 0.75 * age_norm)
        //                          : 0.5 * erosion * age_norm      (pure erosion eats the edges)
        // edge      = max(0.02, erosion)
        // alpha    *= smoothstep(threshold - edge, threshold + edge, noise)
        // The noise UV is offset by a hash of the particle seed, so no two puffs of a system
        // break up the same way.
        float threshold = 0.0f;
        float edge = 0.0f;
        float noise_u = 0.0f;
        float noise_v = 0.0f;
        if (rp.noise) {
            threshold = rp.dissolve > 0.0f ? rp.dissolve * (0.25f + 0.75f * age_norm)
                                           : 0.5f * rp.erosion * age_norm;
            edge = std::max(0.02f, rp.erosion);
            const uint32_t particle_seed = i < pb.seed.size() ? pb.seed[i] : 0u;
            noise_u = hash_unit(particle_seed, 0x9e37u, 17u);
            noise_v = hash_unit(particle_seed, 0x85ebu, 29u);
        }

        // --- animated frame rectangle (upgrade 4) ----------------------------------------
        // The sheet cell is indexed inside the frame, and the UV is kept half a texel away from
        // the frame border so a bilinear tap never bleeds in from the neighbouring frame.
        const float inv_frames = 1.0f / static_cast<float>(rp.texture_frames);
        const float frame = static_cast<float>(sprite_frame(rp, pb, i, age_norm));
        float frame_u0 = 0.0f;
        float frame_u1 = 1.0f;
        if (rp.texture_frames > 1 && rp.sprite && rp.sprite->image.width > 0) {
            const float half_texel = 0.5f / static_cast<float>(rp.sprite->image.width);
            frame_u0 = frame * inv_frames + half_texel;
            frame_u1 = (frame + 1.0f) * inv_frames - half_texel;
            if (frame_u1 < frame_u0) frame_u0 = frame_u1 = (frame_u0 + frame_u1) * 0.5f;
        }
        const bool clamp_frame = rp.texture_frames > 1;

        const bool soft = settings.soft_particles && rp.soft && rp.soft_distance > 0.0f;
        const float inv_soft = soft ? 1.0f / rp.soft_distance : 0.0f;
        const float inv_cols = 1.0f / static_cast<float>(rp.sprite_columns);
        const float inv_rows = 1.0f / static_cast<float>(rp.sprite_rows);
        const BlendMode blend = rp.blend;

        raster_parallelogram(fb.w, fb.h, center, ax, ay, [&](int x, int y, float u, float v) {
            const size_t idx = fb.index(x, y);
            if (depth > fb.depth[idx]) return;  // behind opaque geometry
            float a = sh.alpha;
            Vec3 src = src_base;
            if (volumetric) {
                // Sprite-local position in [-1,1], rotated out of the quad's frame back into the
                // view plane so the normal follows the screen position, not the sprite's roll.
                const float ds = u * 2.0f - 1.0f;
                const float dt = (v * 2.0f - 1.0f) * long_scale;
                const float dx = axis_a.x * ds + axis_b.x * dt;
                const float dy = axis_a.y * ds + axis_b.y * dt;
                const Vec3 n = proj.view_dir_to_world(
                    Vec3{dx, dy, std::sqrt(std::max(0.0f, 1.0f - dx * dx - dy * dy))});
                src = sh.color * shade_volumetric(scratch_lights_, n) + sh.emission;
                if (rim) src += rp.fresnel_color * fresnel_rim(n, to_eye, rp.fresnel_power);
            }
            if (rp.noise) {
                const float mask = rp.noise->sample(u + noise_u, v + noise_v, WrapMode::Repeat).r;
                a *= smoothstep(threshold - edge, threshold + edge, mask);
                if (a <= 0.0f) return;
            }
            if (rp.sprite && !rp.sprite->image.empty()) {
                float su = (frame + (static_cast<float>(cell_col) + u) * inv_cols) * inv_frames;
                if (clamp_frame) su = clamp(su, frame_u0, frame_u1);
                const float sv = (static_cast<float>(cell_row) + v) * inv_rows;
                const Color c = rp.sprite->image.sample(su, sv, WrapMode::Clamp);
                a *= c.a;
                src = src * c.rgb();
            } else {
                a *= default_sprite_alpha(u, v);
            }
            if (soft) a *= saturate((fb.depth[idx] - depth) * inv_soft);
            if (a <= 0.0f) return;
            blend_pixel(fb, idx, src, saturate(a), blend);
            ++fragments_;
        });
    }

    void draw_particle_mesh(Framebuffer& fb, const Projector& proj, const FrameState& state,
                            const RenderSettings& settings, const ResolvedParticles& rp, uint32_t index) {
        const ParticleBuffer& pb = *rp.buf;
        const size_t i = index;
        if (!rp.mesh) return;
        const ParticleShade sh = shade_particle(rp, i);
        if (sh.alpha <= 0.0f) return;
        const float size = pb.size[i];
        const float rot = i < pb.rotation.size() ? pb.rotation[i] : 0.0f;
        const Mat4 xf = Mat4::translation(pb.position[i]) * Mat4::rotation_y(rot) * Mat4::scaling(Vec3{size});

        MeshShading ms;
        ms.albedo = sh.color;
        ms.emissive = sh.emission;
        ms.fresnel_color = rp.fresnel_color;
        ms.fresnel_power = rp.fresnel_power;
        ms.alpha = saturate(sh.alpha);
        ms.lit = rp.lit;
        ms.opaque = false;
        ms.blend = rp.blend;
        ms.depth_write = rp.blend == BlendMode::Alpha;
        draw_mesh(fb, proj, state, settings, *rp.mesh, xf, ms);
    }

    // Camera-facing quad per polyline segment with a bright core and soft edges:
    //   profile(t) = (1-|t|)^2 + 2 * clamp(1 - 4|t|)^2   with t = 2v - 1 across the width.
    void draw_beam_segment(Framebuffer& fb, const Projector& proj, const RenderSettings& settings,
                           const BeamState& beam, const ResolvedSimple& rs, const BeamSeg& seg) {
        Vec2 pa, pb_px;
        float da = 0.0f, db = 0.0f;
        if (!proj.project(seg.a, pa, da) || !proj.project(seg.b, pb_px, db)) return;
        Vec2 along = pb_px - pa;
        if (length(along) < 1e-4f) return;
        const Vec2 dir = normalize(along);
        const Vec2 perp{-dir.y, dir.x};
        const float half_px =
            seg.half_width * (proj.pixel_scale(da) + proj.pixel_scale(db)) * 0.5f;
        if (half_px <= 0.0f) return;

        const Vec2 center = (pa + pb_px) * 0.5f;
        const Vec2 ax = along * 0.5f;
        const Vec2 ay = perp * std::max(half_px, 0.6f);  // never thinner than a pixel
        const float depth_mid = (da + db) * 0.5f;
        const float pulse = beam.pulse_phase;
        const bool soft = settings.soft_particles && rs.soft && rs.soft_distance > 0.0f;
        const float inv_soft = soft ? 1.0f / rs.soft_distance : 0.0f;

        raster_parallelogram(fb.w, fb.h, center, ax, ay, [&](int x, int y, float u, float v) {
            const size_t idx = fb.index(x, y);
            const float depth = lerp(da, db, u);
            if (depth > fb.depth[idx]) return;
            const float t = std::fabs(v * 2.0f - 1.0f);
            const float soft_edge = (1.0f - t) * (1.0f - t);
            const float core_t = saturate(1.0f - t * 4.0f);
            float profile = soft_edge + core_t * core_t * 2.0f;
            if (pulse >= 0.0f) {
                float d = std::fabs(lerp(seg.t0, seg.t1, u) - pulse);
                d = std::min(d, 1.0f - d);  // the pulse wraps along the beam
                profile *= 1.0f + 3.0f * std::exp(-(d * d) / (0.06f * 0.06f));
            }
            if (profile <= 0.0f) return;
            float a = saturate(profile) * rs.opacity;
            if (soft) a *= saturate((fb.depth[idx] - depth_mid) * inv_soft);
            if (a <= 0.0f) return;
            blend_pixel(fb, idx, rs.base_color * (rs.emissive_scale * profile), a, rs.blend);
            ++fragments_;
        });
    }

    // Ribbon segment: a camera-facing trapezoid (the two vertices may have different widths),
    // drawn as two triangles with a smooth cross-section. `twist_deg` is ignored in V1.
    void draw_trail_segment(Framebuffer& fb, const Projector& proj, const RenderSettings& settings,
                            const ResolvedSimple& rs, const TrailSeg& seg) {
        Vec2 pa, pbx;
        float da = 0.0f, db = 0.0f;
        if (!proj.project(seg.a->position, pa, da) || !proj.project(seg.b->position, pbx, db)) return;
        Vec2 along = pbx - pa;
        if (length(along) < 1e-4f) return;
        const Vec2 dir = normalize(along);
        const Vec2 perp{-dir.y, dir.x};
        const float wa = std::max(seg.a->width * 0.5f * proj.pixel_scale(da), 0.5f);
        const float wb = std::max(seg.b->width * 0.5f * proj.pixel_scale(db), 0.5f);

        const Vec2 q0 = pa + perp * wa;   // (s=0, v=0)
        const Vec2 q1 = pbx + perp * wb;  // (s=1, v=0)
        const Vec2 q2 = pbx - perp * wb;  // (s=1, v=1)
        const Vec2 q3 = pa - perp * wa;   // (s=0, v=1)

        const Vec3 ca = seg.a->color.rgb() * rs.base_color;
        const Vec3 cb = seg.b->color.rgb() * rs.base_color;
        const float aa = seg.a->opacity * seg.a->color.a * rs.opacity;
        const float ab = seg.b->opacity * seg.b->color.a * rs.opacity;
        const float ea = seg.a->emissive * rs.emissive_scale;
        const float eb = seg.b->emissive * rs.emissive_scale;
        const bool soft = settings.soft_particles && rs.soft && rs.soft_distance > 0.0f;
        const float inv_soft = soft ? 1.0f / rs.soft_distance : 0.0f;

        auto shade = [&](int x, int y, float s, float v) {
            const size_t idx = fb.index(x, y);
            const float depth = lerp(da, db, s);
            if (depth > fb.depth[idx]) return;
            const float e = 1.0f - std::fabs(v * 2.0f - 1.0f);
            const float across = e * e * (3.0f - 2.0f * e);  // smooth ribbon edges
            float a = lerp(aa, ab, s) * across;
            if (soft) a *= saturate((fb.depth[idx] - depth) * inv_soft);
            if (a <= 0.0f) return;
            const Vec3 base = lerp(ca, cb, s);
            const Vec3 src = base * (1.0f + lerp(ea, eb, s));
            blend_pixel(fb, idx, src, saturate(a), rs.blend);
            ++fragments_;
        };

        // (q0, q1, q2) then (q0, q2, q3); the shared edge is handled by the top-left rule so no
        // pixel is blended twice.
        raster_triangle(fb.w, fb.h, q0, q1, q2, [&](int x, int y, float, float b1, float b2) {
            shade(x, y, b1 + b2, b2);
        });
        raster_triangle(fb.w, fb.h, q0, q2, q3, [&](int x, int y, float, float b1, float b2) {
            shade(x, y, b1, b1 + b2);
        });
    }

    // -----------------------------------------------------------------------------------------
    // Resolve + post
    // -----------------------------------------------------------------------------------------
    static Image downsample(const Framebuffer& fb, int out_w, int out_h, int ss) {
        Image img(out_w, out_h);
        const float inv = 1.0f / static_cast<float>(ss * ss);
        for (int y = 0; y < out_h; ++y) {
            for (int x = 0; x < out_w; ++x) {
                Vec3 acc{};
                float cov = 0.0f;
                for (int sy = 0; sy < ss; ++sy) {
                    for (int sx = 0; sx < ss; ++sx) {
                        const size_t i = fb.index(x * ss + sx, y * ss + sy);
                        acc += fb.color[i];
                        cov += fb.coverage[i];
                    }
                }
                img.set(x, y, Color{acc.x * inv, acc.y * inv, acc.z * inv, cov * inv});
            }
        }
        return img;
    }

    void apply_post_effects(Image& img, const FrameState& state, const CameraDesc& camera,
                            const RenderSettings& settings) const {
        float bloom_threshold = settings.bloom_threshold;
        float bloom_intensity = settings.bloom_intensity;
        float bloom_radius = settings.bloom_radius;
        bool bloom_enabled = settings.bloom;
        for (const PostEffectState& pe : state.post_effects) {
            if (pe.post_type != "bloom") continue;
            bloom_enabled = true;  // an explicit bloom node turns bloom on and overrides the settings
            bloom_threshold = pe.threshold;
            bloom_intensity = pe.intensity;
            bloom_radius = pe.radius;
            break;
        }
        if (bloom_enabled) apply_bloom(img, bloom_threshold, bloom_intensity, bloom_radius);

        for (const PostEffectState& pe : state.post_effects) {
            if (pe.post_type == "heat_haze" || pe.post_type == "distortion")
                apply_warp(img, pe.intensity, pe.radius, pe.time);
        }
        for (const PostEffectState& pe : state.post_effects) {
            if (pe.post_type == "chromatic_aberration") apply_chromatic_aberration(img, pe.intensity);
        }
        float pulse = 1.0f;
        for (const PostEffectState& pe : state.post_effects) {
            if (pe.post_type != "exposure_pulse") continue;
            const float phase = kTwoPi * pe.frequency * static_cast<float>(pe.time);
            pulse *= 1.0f + pe.intensity * 0.5f * (0.5f + 0.5f * std::sin(phase));
        }

        // Linear HDR out: exposure only, never a tonemap (that belongs to image_io).
        const float exposure = settings.exposure * camera.exposure * pulse;
        if (exposure != 1.0f) {
            for (size_t i = 0; i < img.pixel_count(); ++i) {
                img.rgba[i * 4 + 0] *= exposure;
                img.rgba[i * 4 + 1] *= exposure;
                img.rgba[i * 4 + 2] *= exposure;
            }
        }
    }

    // Reused per-primitive scratch so rasterisation never allocates per triangle or per fragment.
    std::vector<BillboardLight> scratch_lights_;
    std::vector<Vec3> scratch_pos_;
    std::vector<Vec3> scratch_nrm_;
    std::vector<Vec2> scratch_px_;
    std::vector<float> scratch_depth_;
    std::vector<uint8_t> scratch_ok_;
};

}  // namespace

std::unique_ptr<IRenderer> create_software_renderer() { return std::make_unique<SoftwareRenderer>(); }

}  // namespace aether::render
