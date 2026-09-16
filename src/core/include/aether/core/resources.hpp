#pragma once
// Baked resources shared by compiler (producer), sim and renderer (consumers).
#include <map>
#include <string>
#include <vector>

#include "aether/core/enums.hpp"
#include "aether/core/image.hpp"
#include "aether/core/math.hpp"
#include "aether/core/value.hpp"

namespace aether {

struct MeshData {
    std::vector<Vec3> positions;
    std::vector<Vec3> normals;   // same size as positions (may be empty for point clouds)
    std::vector<Vec2> uvs;       // same size as positions or empty
    std::vector<uint32_t> indices;  // triangles
    Bounds bounds() const;
    size_t triangle_count() const { return indices.size() / 3; }
    // Area-weighted random surface point sampling table (cumulative areas), built lazily by
    // consumers; kept here so it is computed once.
    std::vector<float> cumulative_area;
    void build_area_table();
    float total_area() const { return cumulative_area.empty() ? 0.0f : cumulative_area.back(); }
};

struct MaterialDesc {
    std::string id;
    Color base_color{1, 1, 1, 1};
    float opacity = 1.0f;
    Color emissive_color{1, 1, 1, 1};
    float emissive_intensity = 0.0f;
    BlendMode blend = BlendMode::Additive;
    Shading shading = Shading::Unlit;
    bool soft_particle = true;
    float depth_fade = 0.1f;
    float distortion = 0.0f;
    float fresnel_power = 0.0f;
    Vec2 uv_scroll{0, 0};
    float uv_rotate = 0.0f;      // deg/s
    float dissolve = 0.0f;
    float erosion = 0.0f;
    bool double_sided = true;
    Gradient temperature_gradient;  // empty = unused
    std::string base_texture;       // resource ids ("" = none)
    std::string noise_texture;
    std::string gradient_texture;
};

// Animated textures store frames side by side: frame i occupies columns
// [i*frame_width, (i+1)*frame_width).
struct TextureResource {
    Image image;
    int frames = 1;
    int frame_width() const { return frames > 0 ? image.width / frames : image.width; }
};

struct ResourceSet {
    std::map<std::string, TextureResource> textures;  // keyed by texture node id
    std::map<std::string, MeshData> meshes;           // keyed by mesh node id
    std::map<std::string, MaterialDesc> materials;    // keyed by material node id
    const TextureResource* texture(const std::string& id) const;
    const MeshData* mesh(const std::string& id) const;
    const MaterialDesc* material(const std::string& id) const;
};

}  // namespace aether
