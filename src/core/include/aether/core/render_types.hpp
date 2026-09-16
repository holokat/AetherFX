#pragma once
// Types shared by sim (which fills a FrameState) and render (which consumes it).
#include <string>

#include <nlohmann/json.hpp>

#include "aether/core/math.hpp"

namespace aether {

struct CameraDesc {
    Vec3 position{0, 1.5f, 5};
    Vec3 target{0, 1, 0};
    Vec3 up{0, 1, 0};
    float fov_deg = 45.0f;
    float near_plane = 0.05f;
    float far_plane = 200.0f;
    float exposure = 1.0f;
    Mat4 view() const { return Mat4::look_at(position, target, up); }
    Mat4 projection(float aspect) const { return Mat4::perspective(deg_to_rad(fov_deg), aspect, near_plane, far_plane); }
    nlohmann::json to_json() const;
    static CameraDesc from_json(const nlohmann::json& j);  // missing fields keep defaults
};

struct RenderSettings {
    int width = 512;
    int height = 512;
    Color background{0.02f, 0.02f, 0.025f, 1.0f};
    bool ground_plane = true;     // shaded plane at y=0 receiving light
    bool grid = true;             // 1 m grid lines on the ground plane
    float ground_albedo = 0.18f;
    bool bloom = true;
    float bloom_threshold = 1.0f;
    float bloom_intensity = 0.35f;
    float bloom_radius = 0.04f;   // fraction of width
    float exposure = 1.0f;
    bool tonemap = true;          // applied only when writing 8-bit output
    bool soft_particles = true;
    bool motion_blur = false;
    int supersample = 1;          // 1 or 2
    nlohmann::json to_json() const;
    static RenderSettings from_json(const nlohmann::json& j);
};

struct RenderStatistics {
    size_t particles_submitted = 0;
    size_t particles_drawn = 0;
    size_t fragments_shaded = 0;   // total pixels touched by all primitives
    double overdraw = 0.0;         // fragments_shaded / (width*height)
    double render_ms = 0.0;
    nlohmann::json to_json() const;
};

struct PostEffectState {
    std::string id;
    std::string post_type;  // bloom|distortion|heat_haze|chromatic_aberration|exposure_pulse
    float intensity = 1.0f;
    float threshold = 1.0f;
    float radius = 0.05f;
    float frequency = 8.0f;
    double time = 0.0;
};

}  // namespace aether
