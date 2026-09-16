#include "aether/core/render_types.hpp"

namespace aether {
namespace {
nlohmann::json v3(Vec3 v) { return {v.x, v.y, v.z}; }
nlohmann::json c4(Color c) { return {c.r, c.g, c.b, c.a}; }
Vec3 get_v3(const nlohmann::json& j, const char* k, Vec3 d) {
    if (!j.contains(k) || !j[k].is_array() || j[k].size() < 3) return d;
    return {j[k][0].get<float>(), j[k][1].get<float>(), j[k][2].get<float>()};
}
Color get_c4(const nlohmann::json& j, const char* k, Color d) {
    if (!j.contains(k) || !j[k].is_array() || j[k].size() < 3) return d;
    Color c{j[k][0].get<float>(), j[k][1].get<float>(), j[k][2].get<float>(), 1.0f};
    if (j[k].size() > 3) c.a = j[k][3].get<float>();
    return c;
}
template <class T> T get_or(const nlohmann::json& j, const char* k, T d) {
    if (!j.contains(k) || j[k].is_null()) return d;
    return j[k].get<T>();
}
}  // namespace

nlohmann::json CameraDesc::to_json() const {
    return {{"position", v3(position)}, {"target", v3(target)}, {"up", v3(up)}, {"fov", fov_deg},
            {"near", near_plane}, {"far", far_plane}, {"exposure", exposure}};
}
CameraDesc CameraDesc::from_json(const nlohmann::json& j) {
    CameraDesc c;
    if (!j.is_object()) return c;
    c.position = get_v3(j, "position", c.position);
    c.target = get_v3(j, "target", c.target);
    c.up = get_v3(j, "up", c.up);
    c.fov_deg = get_or(j, "fov", c.fov_deg);
    c.near_plane = get_or(j, "near", c.near_plane);
    c.far_plane = get_or(j, "far", c.far_plane);
    c.exposure = get_or(j, "exposure", c.exposure);
    return c;
}

nlohmann::json RenderSettings::to_json() const {
    return {{"width", width}, {"height", height}, {"background", c4(background)}, {"ground_plane", ground_plane},
            {"grid", grid}, {"ground_albedo", ground_albedo}, {"bloom", bloom}, {"bloom_threshold", bloom_threshold},
            {"bloom_intensity", bloom_intensity}, {"bloom_radius", bloom_radius}, {"exposure", exposure},
            {"tonemap", tonemap}, {"soft_particles", soft_particles}, {"motion_blur", motion_blur},
            {"supersample", supersample}};
}
RenderSettings RenderSettings::from_json(const nlohmann::json& j) {
    RenderSettings s;
    if (!j.is_object()) return s;
    s.width = get_or(j, "width", s.width);
    s.height = get_or(j, "height", s.height);
    s.background = get_c4(j, "background", s.background);
    s.ground_plane = get_or(j, "ground_plane", s.ground_plane);
    s.grid = get_or(j, "grid", s.grid);
    s.ground_albedo = get_or(j, "ground_albedo", s.ground_albedo);
    s.bloom = get_or(j, "bloom", s.bloom);
    s.bloom_threshold = get_or(j, "bloom_threshold", s.bloom_threshold);
    s.bloom_intensity = get_or(j, "bloom_intensity", s.bloom_intensity);
    s.bloom_radius = get_or(j, "bloom_radius", s.bloom_radius);
    s.exposure = get_or(j, "exposure", s.exposure);
    s.tonemap = get_or(j, "tonemap", s.tonemap);
    s.soft_particles = get_or(j, "soft_particles", s.soft_particles);
    s.motion_blur = get_or(j, "motion_blur", s.motion_blur);
    s.supersample = get_or(j, "supersample", s.supersample);
    return s;
}

nlohmann::json RenderStatistics::to_json() const {
    return {{"particles_submitted", particles_submitted}, {"particles_drawn", particles_drawn},
            {"fragments_shaded", fragments_shaded}, {"overdraw", overdraw}, {"render_ms", render_ms}};
}

}  // namespace aether
