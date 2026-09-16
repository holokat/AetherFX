#pragma once
#include <filesystem>
#include <vector>

#include "aether/core/math.hpp"
#include "aether/core/resources.hpp"
#include "aether/core/rng.hpp"

namespace aether::procedural {

// All primitives are centered at the origin, +Y up, with normals and UVs.
MeshData make_sphere(float radius, int segments);
MeshData make_cube(Vec3 size);
MeshData make_plane(Vec2 size, int segments = 1);        // XZ plane, normal +Y
MeshData make_disc(float radius, float inner_radius, int segments);   // XZ, normal +Y
MeshData make_ring(float radius, float inner_radius, int segments);   // alias of disc with inner>0
MeshData make_cone(float radius, float height, int segments);         // apex at +height/2
MeshData make_cylinder(float radius, float height, int segments);
MeshData make_capsule(float radius, float height, int segments);
MeshData make_tube(const std::vector<Vec3>& path, float radius, int segments);
MeshData make_ribbon(const std::vector<Vec3>& path, float width, Vec3 up = Vec3::up());
MeshData load_obj(const std::filesystem::path& path);  // positions/normals/uvs/triangles; throws Error

// Uniform area-weighted surface sample. Builds the area table on first use.
Vec3 sample_surface(MeshData& mesh, Pcg32& rng, Vec3* normal = nullptr);
// Rejection-sampled point inside a closed mesh's bounds (V1: bounds only, documented limitation).
Vec3 sample_bounds(const MeshData& mesh, Pcg32& rng);

enum class CurveType { Linear, CatmullRom, Bezier };
bool parse_curve_type(std::string_view s, CurveType& out);
// Evaluates a control polygon at t in [0,1] (arc-length is NOT normalized; V1 uses parameter space).
Vec3 evaluate_curve(const std::vector<Vec3>& points, CurveType type, bool closed, float t);
// Tangent (normalized) at t.
Vec3 evaluate_curve_tangent(const std::vector<Vec3>& points, CurveType type, bool closed, float t);
// Polyline with `segments` segments (segments+1 points, or `segments` when closed).
std::vector<Vec3> tessellate_curve(const std::vector<Vec3>& points, CurveType type, bool closed, int segments);
float polyline_length(const std::vector<Vec3>& pts);

}  // namespace aether::procedural
