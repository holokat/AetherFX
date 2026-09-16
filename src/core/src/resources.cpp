#include "aether/core/resources.hpp"

namespace aether {

Bounds MeshData::bounds() const {
    Bounds b;
    for (const auto& p : positions) b.expand(p);
    return b;
}

void MeshData::build_area_table() {
    cumulative_area.clear();
    cumulative_area.reserve(triangle_count());
    float acc = 0.0f;
    for (size_t t = 0; t + 2 < indices.size(); t += 3) {
        const Vec3& a = positions[indices[t]];
        const Vec3& b = positions[indices[t + 1]];
        const Vec3& c = positions[indices[t + 2]];
        acc += 0.5f * length(cross(b - a, c - a));
        cumulative_area.push_back(acc);
    }
}

const TextureResource* ResourceSet::texture(const std::string& id) const {
    auto it = textures.find(id);
    return it == textures.end() ? nullptr : &it->second;
}
const MeshData* ResourceSet::mesh(const std::string& id) const {
    auto it = meshes.find(id);
    return it == meshes.end() ? nullptr : &it->second;
}
const MaterialDesc* ResourceSet::material(const std::string& id) const {
    auto it = materials.find(id);
    return it == materials.end() ? nullptr : &it->second;
}

}  // namespace aether
