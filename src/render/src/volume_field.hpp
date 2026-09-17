#pragma once
// The procedural volume density field - docs/VOLUMES.md, "Density function".
//
// One closed-form function of (position, time) plus the twenty-odd parameters of a
// `volume` node in `mode: procedural`. Every backend evaluates the same maths:
// this header is the reference, `static/viewer/volumes.js` is the GLSL translation
// and an engine bridge writes the same nodes into its own material graph.
//
//   p_local  = inverse(transform) * p
//   sdf      = signed distance of `shape` (approximate: it drives a mask, not a surface)
//   mask     = 1 - smoothstep(-softness * radius, 0, sdf)
//   q        = p_local rotated about Y by (spin * 2pi * time + twist * p.y),
//              then shifted down by climb * time
//   n_soft   = 0.5 + 0.5 * fbm3(q * filament_scale, seed)
//   n_ridged = 1 - |fbm3(q * filament_scale * 1.7, seed + 1)|
//   noise    = mix(n_soft, n_ridged, strands)
//   arms     = spiral_arms > 0
//                ? pow(0.5 + 0.5*cos(spiral_arms*atan2(q.z,q.x) - arm_sharpness*|q.xz|), arm_sharpness)
//                : 1
//   d        = mask * arms * smoothstep(carve, carve + 0.25, noise) * density
//
// `d` is an extinction coefficient in 1/m: a marcher turns a step of `dt` metres
// into `alpha = 1 - exp(-d * dt)`, which makes the result independent of the step
// count and keeps a 32-step CPU render in the same family as a 48-step GPU one.
//
// The whole field is exactly zero outside volume_shape_extent(), which is what lets
// a backend march only the box and skip everything else.
#include <algorithm>
#include <cmath>

#include "aether/core/frame_state.hpp"
#include "aether/core/math.hpp"
#include "aether/procedural/noise.hpp"

namespace aether::render {

// fbm shape. The GLSL backend uses hash-based value noise rather than simplex, so
// the fields are not bit-identical; the octave count, lacunarity and gain are, which
// is what keeps the two looks the same.
inline constexpr int kVolumeFbmOctaves = 4;
inline constexpr float kVolumeFbmLacunarity = 2.0f;
inline constexpr float kVolumeFbmGain = 0.5f;
inline constexpr float kVolumeRidgedScale = 1.7f;  // frequency of the ridged field
inline constexpr float kVolumeCarveWidth = 0.25f;  // width of the carve smoothstep

// Approximate signed distance to `shape`, in the volume's local space.
inline float volume_shape_sdf(std::string_view shape, Vec3 p, float radius, float height) {
    const float r = std::max(0.0f, radius);
    const float h = std::max(1e-4f, height);
    const float xz = std::sqrt(p.x * p.x + p.z * p.z);
    if (shape == "column") return std::max(xz - r, std::fabs(p.y) - h * 0.5f);
    if (shape == "disc") return std::max(xz - r, std::fabs(p.y) - h * kVolumeDiscThickness * 0.5f);
    if (shape == "ring") {
        const float minor = h * kVolumeRingThickness;
        const float a = xz - r;
        return std::sqrt(a * a + p.y * p.y) - minor;
    }
    if (shape == "nebula") {  // flattened ellipsoid, radii (r, h/2, r)
        const float ry = std::max(1e-4f, h * 0.5f);
        const float rx = std::max(1e-4f, r);
        const Vec3 n{p.x / rx, p.y / ry, p.z / rx};
        return (length(n) - 1.0f) * std::min(rx, ry);
    }
    if (shape == "cone") {  // base radius r at y = -h/2, apex at y = +h/2
        const float taper = saturate(0.5f - p.y / h);
        return std::max(xz - r * taper, std::fabs(p.y) - h * 0.5f);
    }
    return length(p) - r;  // sphere
}

// One volume, resolved for one instant: the inverse transform, the marching box and
// the trigonometry that does not vary per sample are computed once.
class VolumeField {
public:
    VolumeField(const VolumeState& state, float at_time)
        : state_(state),
          time_(at_time),
          extent_(volume_shape_extent(state.shape, state.radius, state.height)),
          world_to_local_(state.transform.inverse()),
          spin_angle_(kTwoPi * state.spin * at_time),
          edge_(std::max(0.0f, state.softness) * std::max(0.0f, state.radius)),
          climb_(state.climb * at_time) {}

    const VolumeState& state() const { return state_; }
    float time() const { return time_; }
    Vec3 extent() const { return extent_; }                    // local half-extents of the march box
    const Mat4& world_to_local() const { return world_to_local_; }
    // A field that can never produce a visible sample: the caller skips it entirely.
    bool empty() const {
        return !(state_.density > 0.0f) || extent_.x <= 0.0f || extent_.y <= 0.0f || extent_.z <= 0.0f;
    }

    float density_local(Vec3 p) const {
        if (empty()) return 0.0f;
        const float sdf = volume_shape_sdf(state_.shape, p, state_.radius, state_.height);
        const float mask = edge_ > 1e-6f ? 1.0f - smoothstep(-edge_, 0.0f, sdf) : (sdf < 0.0f ? 1.0f : 0.0f);
        if (mask <= 0.0f) return 0.0f;  // the noise is the expensive half: never evaluate it outside the shape

        const float angle = spin_angle_ + state_.twist * p.y;
        const float c = std::cos(angle), s = std::sin(angle);
        const Vec3 q{p.x * c - p.z * s, p.y - climb_, p.x * s + p.z * c};

        const procedural::FbmParams fbm{kVolumeFbmOctaves, kVolumeFbmLacunarity, kVolumeFbmGain,
                                        procedural::NoiseBasis::Simplex};
        const float scale = std::max(1e-3f, state_.filament_scale);
        const float soft = 0.5f + 0.5f * procedural::fbm3(q * scale, state_.seed, fbm);
        const float ridged = 1.0f - std::fabs(procedural::fbm3(q * (scale * kVolumeRidgedScale), state_.seed + 1u, fbm));
        const float noise = lerp(soft, ridged, saturate(state_.strands));

        float arms = 1.0f;
        if (state_.spiral_arms > 0) {
            const float sharpness = std::max(0.1f, state_.arm_sharpness);
            const float radial = std::sqrt(q.x * q.x + q.z * q.z);
            const float wave = 0.5f + 0.5f * std::cos(static_cast<float>(state_.spiral_arms) * std::atan2(q.z, q.x) -
                                                      sharpness * radial);
            arms = std::pow(std::max(0.0f, wave), sharpness);
        }

        const float carve = saturate(state_.carve);
        return mask * arms * smoothstep(carve, carve + kVolumeCarveWidth, noise) * state_.density;
    }

    float density(Vec3 world_p) const { return density_local(world_to_local_.transform_point(world_p)); }

    // Base tint of a sample: cool shell, hot core (docs/VOLUMES.md).
    Vec3 tint(float d) const { return lerp(state_.color.rgb(), state_.color_hot.rgb(), saturate(d * 2.0f)); }

private:
    const VolumeState& state_;
    float time_;
    Vec3 extent_;
    Mat4 world_to_local_;
    float spin_angle_;
    float edge_;
    float climb_;
};

// The reference entry point: the density of `v` at a world-space point, at `time`.
inline float volume_density(const VolumeState& v, Vec3 world_p, float time) {
    return VolumeField(v, time).density(world_p);
}

// The colour a sample of density `d` emits and scatters with.
inline Vec3 volume_tint(const VolumeState& v, float d) {
    return lerp(v.color.rgb(), v.color_hot.rgb(), saturate(d * 2.0f));
}

}  // namespace aether::render
