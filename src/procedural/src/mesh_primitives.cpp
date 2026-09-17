// Mesh primitives, OBJ loading, surface sampling and curve evaluation.
//
// Conventions (docs/ARCHITECTURE.md, docs/VOCABULARY.md):
//   * every primitive is centred on the origin, +Y up, in metres,
//   * triangles wind counter-clockwise when seen from outside,
//   * positions, normals and UVs are always filled in (same length),
//   * everything is a pure function of its inputs; sampling takes an explicit
//     Pcg32 stream so results are reproducible.
#include "aether/procedural/mesh_primitives.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <string_view>
#include <unordered_map>

#include "aether/core/error.hpp"
#include "aether/procedural/noise.hpp"

namespace aether::procedural {
namespace {

void add_vertex(MeshData& mesh, Vec3 position, Vec3 normal, Vec2 uv) {
    mesh.positions.push_back(position);
    mesh.normals.push_back(normal);
    mesh.uvs.push_back(uv);
}

void add_triangle(MeshData& mesh, uint32_t a, uint32_t b, uint32_t c) {
    mesh.indices.push_back(a);
    mesh.indices.push_back(b);
    mesh.indices.push_back(c);
}

// a, b, c, d are the quad corners in counter-clockwise order seen from outside.
void add_quad(MeshData& mesh, uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
    add_triangle(mesh, a, b, c);
    add_triangle(mesh, a, c, d);
}

int sane_segments(int segments, int minimum) { return std::max(minimum, segments); }

// --- helpers for the faceted seeded primitives ----------------------------
// These build "one set of vertices per triangle" meshes: positions are pushed
// first with placeholder normals/UVs, and finalize_faceted fills both in once
// the geometry is final.

void add_facet(MeshData& mesh, Vec3 a, Vec3 b, Vec3 c) {
    const uint32_t base = static_cast<uint32_t>(mesh.positions.size());
    add_vertex(mesh, a, Vec3::up(), Vec2{});
    add_vertex(mesh, b, Vec3::up(), Vec2{});
    add_vertex(mesh, c, Vec3::up(), Vec2{});
    add_triangle(mesh, base, base + 1, base + 2);
}

// Shrinks (never grows) the mesh about the origin so its bounding box fits in
// x,z in [-radius, radius] and y in [-height/2, height/2]. Jitter can overshoot
// either limit; this keeps the documented bounds contract exact.
void fit_in_cylinder(MeshData& mesh, float radius, float height) {
    float max_radial = 0.0f, max_y = 0.0f;
    for (const Vec3& p : mesh.positions) {
        max_radial = std::max(max_radial, std::sqrt(p.x * p.x + p.z * p.z));
        max_y = std::max(max_y, std::fabs(p.y));
    }
    const float half_h = height * 0.5f;
    const float sxz = (radius > 0.0f && max_radial > radius) ? radius / max_radial : 1.0f;
    const float sy = (half_h > 0.0f && max_y > half_h) ? half_h / max_y : 1.0f;
    if (sxz == 1.0f && sy == 1.0f) return;
    for (Vec3& p : mesh.positions) {
        p.x *= sxz;
        p.z *= sxz;
        p.y *= sy;
    }
}

// Per-face normals (the vertices are already unique per triangle) and a
// cylindrical UV map over the mesh height.
void finalize_faceted(MeshData& mesh, float height) {
    for (size_t i = 0; i < mesh.positions.size(); ++i) {
        const Vec3& p = mesh.positions[i];
        mesh.uvs[i] = Vec2{std::atan2(p.z, p.x) * (1.0f / kTwoPi) + 0.5f,
                           height > 1e-6f ? saturate(p.y / height + 0.5f) : 0.5f};
    }
    for (size_t t = 0; t + 2 < mesh.indices.size(); t += 3) {
        const Vec3 a = mesh.positions[mesh.indices[t]];
        const Vec3 b = mesh.positions[mesh.indices[t + 1]];
        const Vec3 c = mesh.positions[mesh.indices[t + 2]];
        const Vec3 face = cross(b - a, c - a);
        const Vec3 n = length(face) > 1e-12f ? normalize(face) : Vec3::up();
        mesh.normals[mesh.indices[t]] = n;
        mesh.normals[mesh.indices[t + 1]] = n;
        mesh.normals[mesh.indices[t + 2]] = n;
    }
}

// One crystal spike: an irregular n-gon base at y = -half_h fanned up to a
// single apex, plus the base cap. Winding matches make_cone.
void add_spike(MeshData& mesh, Pcg32& rng, int slices, float radius, float half_h, float apex_y, float irregularity,
               Vec2 centre) {
    std::vector<Vec3> ring(static_cast<size_t>(slices));
    const float gap = kTwoPi / static_cast<float>(slices);
    for (int j = 0; j < slices; ++j) {
        // angular jitter stays under a quarter of the gap, so the ring keeps its winding order
        const float angle = static_cast<float>(j) * gap + rng.signed_unit() * gap * 0.25f * irregularity;
        const float r = radius * (1.0f + rng.signed_unit() * 0.5f * irregularity);
        ring[static_cast<size_t>(j)] = Vec3{centre.x + r * std::cos(angle), -half_h, centre.y + r * std::sin(angle)};
    }
    const Vec3 apex{centre.x + rng.signed_unit() * 0.35f * irregularity * radius, apex_y,
                    centre.y + rng.signed_unit() * 0.35f * irregularity * radius};
    const Vec3 base_centre{centre.x, -half_h, centre.y};
    for (int j = 0; j < slices; ++j) {
        const Vec3 a = ring[static_cast<size_t>(j)];
        const Vec3 b = ring[static_cast<size_t>((j + 1) % slices)];
        add_facet(mesh, a, apex, b);        // side
        add_facet(mesh, base_centre, a, b); // base cap, facing -Y
    }
}

// Unit icosahedron: 12 vertices, 20 outward-wound faces.
void icosahedron(std::vector<Vec3>& positions, std::vector<uint32_t>& indices) {
    const float t = (1.0f + std::sqrt(5.0f)) * 0.5f;
    positions = {{-1, t, 0}, {1, t, 0},  {-1, -t, 0}, {1, -t, 0}, {0, -1, t},  {0, 1, t},
                 {0, -1, -t}, {0, 1, -t}, {t, 0, -1},  {t, 0, 1},  {-t, 0, -1}, {-t, 0, 1}};
    for (Vec3& p : positions) p = normalize(p);
    indices = {0, 11, 5, 0, 5,  1, 0, 1, 7, 0, 7,  10, 0,  10, 11, 1, 5, 9, 5, 11, 4, 11, 10, 2, 10, 7, 6, 7, 1, 8,
               3, 9,  4, 3, 4,  2, 3, 2, 6, 3, 6,  8,  3,  8,  9,  4, 9, 5, 2, 4,  11, 6, 2,  10, 8, 6, 7, 9, 8, 1};
}

// One Loop-style split of every triangle, new vertices pushed back onto the
// unit sphere. Shared edges reuse one midpoint so the surface stays closed.
void subdivide_unit(std::vector<Vec3>& positions, std::vector<uint32_t>& indices) {
    std::unordered_map<uint64_t, uint32_t> midpoints;
    std::vector<uint32_t> out;
    out.reserve(indices.size() * 4);
    auto midpoint = [&](uint32_t a, uint32_t b) {
        const uint64_t key = (static_cast<uint64_t>(std::min(a, b)) << 32) | std::max(a, b);
        const auto it = midpoints.find(key);
        if (it != midpoints.end()) return it->second;
        const uint32_t index = static_cast<uint32_t>(positions.size());
        positions.push_back(normalize((positions[a] + positions[b]) * 0.5f));
        midpoints.emplace(key, index);
        return index;
    };
    for (size_t t = 0; t + 2 < indices.size(); t += 3) {
        const uint32_t a = indices[t], b = indices[t + 1], c = indices[t + 2];
        const uint32_t ab = midpoint(a, b), bc = midpoint(b, c), ca = midpoint(c, a);
        for (uint32_t v : {a, ab, ca, b, bc, ab, c, ca, bc, ab, bc, ca}) out.push_back(v);
    }
    indices.swap(out);
}

// Parallel-transport frames: rotate the previous frame by the minimal rotation
// that maps the previous tangent onto the current one. Avoids the flipping that
// a fixed reference up-vector produces on curved paths.
void parallel_transport_frames(const std::vector<Vec3>& tangents, Vec3 seed_up, std::vector<Vec3>& out_normals,
                               std::vector<Vec3>& out_binormals) {
    const size_t n = tangents.size();
    out_normals.assign(n, Vec3::up());
    out_binormals.assign(n, Vec3::right());
    if (n == 0) return;

    Vec3 normal = cross(seed_up, tangents[0]);
    if (length(normal) < 1e-5f) normal = orthogonal(tangents[0]);
    normal = normalize(cross(tangents[0], normalize(normal)));
    if (length(normal) < 1e-5f) normal = orthogonal(tangents[0]);
    out_normals[0] = normal;
    out_binormals[0] = normalize(cross(tangents[0], normal));

    for (size_t i = 1; i < n; ++i) {
        const Vec3 prev_t = tangents[i - 1];
        const Vec3 cur_t = tangents[i];
        Vec3 transported = out_normals[i - 1];
        const Vec3 axis = cross(prev_t, cur_t);
        const float sin_a = length(axis);
        if (sin_a > 1e-6f) {
            const Vec3 unit_axis = axis / sin_a;
            const float angle = std::atan2(sin_a, clamp(dot(prev_t, cur_t), -1.0f, 1.0f));
            const float c = std::cos(angle), s = std::sin(angle);
            // Rodrigues' rotation of the previous normal about the rotation axis.
            transported = transported * c + cross(unit_axis, transported) * s +
                          unit_axis * (dot(unit_axis, transported) * (1.0f - c));
        }
        // Re-orthogonalize against the current tangent to stop drift.
        transported = transported - cur_t * dot(cur_t, transported);
        if (length(transported) < 1e-5f) transported = orthogonal(cur_t);
        out_normals[i] = normalize(transported);
        out_binormals[i] = normalize(cross(cur_t, out_normals[i]));
    }
}

std::vector<Vec3> path_tangents(const std::vector<Vec3>& path) {
    const size_t n = path.size();
    std::vector<Vec3> tangents(n, Vec3::forward());
    for (size_t i = 0; i < n; ++i) {
        Vec3 t;
        if (n == 1) t = Vec3::forward();
        else if (i == 0) t = path[1] - path[0];
        else if (i + 1 == n) t = path[n - 1] - path[n - 2];
        else t = path[i + 1] - path[i - 1];
        if (length(t) < 1e-6f) t = i > 0 ? tangents[i - 1] : Vec3::forward();
        tangents[i] = normalize(t);
    }
    return tangents;
}

// --- curve helpers ---------------------------------------------------------

int curve_segment_count(size_t point_count, CurveType type, bool closed) {
    if (point_count < 2) return 0;
    if (type == CurveType::Bezier) {
        if (closed && point_count % 3 == 0) return static_cast<int>(point_count / 3);
        if (!closed && point_count >= 4 && (point_count - 1) % 3 == 0) {
            return static_cast<int>((point_count - 1) / 3);
        }
        // Not a valid Bezier control polygon: fall back to catmull_rom.
        return static_cast<int>(closed ? point_count : point_count - 1);
    }
    return static_cast<int>(closed ? point_count : point_count - 1);
}

bool bezier_layout_ok(size_t point_count, bool closed) {
    if (closed) return point_count >= 3 && point_count % 3 == 0;
    return point_count >= 4 && (point_count - 1) % 3 == 0;
}

Vec3 point_at(const std::vector<Vec3>& pts, int index, bool closed) {
    const int n = static_cast<int>(pts.size());
    if (n == 0) return Vec3::zero();
    if (closed) return pts[static_cast<size_t>(((index % n) + n) % n)];
    return pts[static_cast<size_t>(clamp(index, 0, n - 1))];
}

Vec3 catmull_rom(Vec3 p0, Vec3 p1, Vec3 p2, Vec3 p3, float t) {
    const float t2 = t * t;
    const float t3 = t2 * t;
    return (p1 * 2.0f + (p2 - p0) * t + (p0 * 2.0f - p1 * 5.0f + p2 * 4.0f - p3) * t2 +
            (p1 * 3.0f - p0 - p2 * 3.0f + p3) * t3) *
           0.5f;
}

Vec3 cubic_bezier(Vec3 p0, Vec3 p1, Vec3 p2, Vec3 p3, float t) {
    const float u = 1.0f - t;
    return p0 * (u * u * u) + p1 * (3.0f * u * u * t) + p2 * (3.0f * u * t * t) + p3 * (t * t * t);
}

// --- OBJ parsing -----------------------------------------------------------

struct ObjKey {
    int v = 0, vt = 0, vn = 0;
    bool operator==(const ObjKey& o) const { return v == o.v && vt == o.vt && vn == o.vn; }
};
struct ObjKeyHash {
    size_t operator()(const ObjKey& k) const {
        uint64_t h = fnv1a_64("obj");
        h = hash_combine(h, static_cast<uint64_t>(static_cast<uint32_t>(k.v)));
        h = hash_combine(h, static_cast<uint64_t>(static_cast<uint32_t>(k.vt)));
        h = hash_combine(h, static_cast<uint64_t>(static_cast<uint32_t>(k.vn)));
        return static_cast<size_t>(h);
    }
};

// Resolves an OBJ index (1-based, negative counts from the end) to 0-based.
int resolve_obj_index(int raw, size_t count) {
    if (raw > 0) return raw - 1;
    if (raw < 0) return static_cast<int>(count) + raw;
    return -1;
}

ObjKey parse_face_vertex(std::string_view token) {
    ObjKey key;
    int field = 0;
    size_t start = 0;
    for (size_t i = 0; i <= token.size(); ++i) {
        if (i == token.size() || token[i] == '/') {
            const std::string_view part = token.substr(start, i - start);
            if (!part.empty()) {
                const int value = std::atoi(std::string(part).c_str());
                if (field == 0) key.v = value;
                else if (field == 1) key.vt = value;
                else if (field == 2) key.vn = value;
            }
            ++field;
            start = i + 1;
        }
    }
    return key;
}

}  // namespace

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

MeshData make_sphere(float radius, int segments) {
    const int stacks = sane_segments(segments, 2);
    const int slices = sane_segments(segments * 2, 3);
    MeshData mesh;
    mesh.positions.reserve(static_cast<size_t>(stacks + 1) * static_cast<size_t>(slices + 1));
    for (int i = 0; i <= stacks; ++i) {
        const float v = static_cast<float>(i) / static_cast<float>(stacks);
        const float theta = v * kPi;  // 0 at +Y, pi at -Y
        const float sin_t = std::sin(theta), cos_t = std::cos(theta);
        for (int j = 0; j <= slices; ++j) {
            const float u = static_cast<float>(j) / static_cast<float>(slices);
            const float phi = u * kTwoPi;
            const Vec3 n{sin_t * std::cos(phi), cos_t, sin_t * std::sin(phi)};
            add_vertex(mesh, n * radius, n, Vec2{u, v});
        }
    }
    const auto index_of = [slices](int i, int j) { return static_cast<uint32_t>(i * (slices + 1) + j); };
    for (int i = 0; i < stacks; ++i) {
        for (int j = 0; j < slices; ++j) {
            const uint32_t a = index_of(i, j);
            const uint32_t b = index_of(i, j + 1);
            const uint32_t c = index_of(i + 1, j + 1);
            const uint32_t d = index_of(i + 1, j);
            if (i == 0) {
                add_triangle(mesh, a, c, d);  // top cap: the a/b edge is degenerate
            } else if (i + 1 == stacks) {
                add_triangle(mesh, a, b, c);  // bottom cap: the c/d edge is degenerate
            } else {
                add_quad(mesh, a, b, c, d);
            }
        }
    }
    return mesh;
}

MeshData make_cube(Vec3 size) {
    const Vec3 h = size * 0.5f;
    MeshData mesh;
    struct Face {
        Vec3 normal, right, up;
    };
    const Face faces[6] = {
        {{0, 0, 1}, {1, 0, 0}, {0, 1, 0}},    // +Z
        {{0, 0, -1}, {-1, 0, 0}, {0, 1, 0}},  // -Z
        {{1, 0, 0}, {0, 0, -1}, {0, 1, 0}},   // +X
        {{-1, 0, 0}, {0, 0, 1}, {0, 1, 0}},   // -X
        {{0, 1, 0}, {1, 0, 0}, {0, 0, -1}},   // +Y
        {{0, -1, 0}, {1, 0, 0}, {0, 0, 1}},   // -Y
    };
    for (const Face& f : faces) {
        const uint32_t base = static_cast<uint32_t>(mesh.positions.size());
        const Vec3 center = f.normal * h;
        const Vec3 right = f.right * h;
        const Vec3 up = f.up * h;
        add_vertex(mesh, center - right - up, f.normal, Vec2{0, 1});
        add_vertex(mesh, center + right - up, f.normal, Vec2{1, 1});
        add_vertex(mesh, center + right + up, f.normal, Vec2{1, 0});
        add_vertex(mesh, center - right + up, f.normal, Vec2{0, 0});
        add_quad(mesh, base, base + 1, base + 2, base + 3);
    }
    return mesh;
}

MeshData make_plane(Vec2 size, int segments) {
    const int n = sane_segments(segments, 1);
    const Vec2 half = size * 0.5f;
    MeshData mesh;
    for (int i = 0; i <= n; ++i) {
        const float tx = static_cast<float>(i) / static_cast<float>(n);
        for (int j = 0; j <= n; ++j) {
            const float tz = static_cast<float>(j) / static_cast<float>(n);
            add_vertex(mesh, Vec3{lerp(-half.x, half.x, tx), 0.0f, lerp(-half.y, half.y, tz)}, Vec3::up(),
                       Vec2{tx, tz});
        }
    }
    const auto index_of = [n](int i, int j) { return static_cast<uint32_t>(i * (n + 1) + j); };
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j < n; ++j) {
            add_quad(mesh, index_of(i, j), index_of(i, j + 1), index_of(i + 1, j + 1), index_of(i + 1, j));
        }
    }
    return mesh;
}

MeshData make_disc(float radius, float inner_radius, int segments) {
    const int slices = sane_segments(segments, 3);
    const float inner = clamp(inner_radius, 0.0f, std::max(radius, 0.0f));
    MeshData mesh;
    const float inv_diameter = radius > 0.0f ? 0.5f / radius : 0.0f;
    const auto uv_of = [inv_diameter](Vec3 p) {
        return Vec2{0.5f + p.x * inv_diameter, 0.5f + p.z * inv_diameter};
    };
    if (inner <= 0.0f) {
        const uint32_t center = static_cast<uint32_t>(mesh.positions.size());
        add_vertex(mesh, Vec3::zero(), Vec3::up(), Vec2{0.5f, 0.5f});
        for (int j = 0; j <= slices; ++j) {
            const float a = static_cast<float>(j) / static_cast<float>(slices) * kTwoPi;
            const Vec3 p{radius * std::cos(a), 0.0f, radius * std::sin(a)};
            add_vertex(mesh, p, Vec3::up(), uv_of(p));
        }
        for (int j = 0; j < slices; ++j) {
            // Reversed rim order so the winding is CCW seen from +Y.
            add_triangle(mesh, center, center + 2 + static_cast<uint32_t>(j), center + 1 + static_cast<uint32_t>(j));
        }
        return mesh;
    }
    for (int j = 0; j <= slices; ++j) {
        const float a = static_cast<float>(j) / static_cast<float>(slices) * kTwoPi;
        const float cos_a = std::cos(a), sin_a = std::sin(a);
        const Vec3 inner_p{inner * cos_a, 0.0f, inner * sin_a};
        const Vec3 outer_p{radius * cos_a, 0.0f, radius * sin_a};
        add_vertex(mesh, inner_p, Vec3::up(), uv_of(inner_p));
        add_vertex(mesh, outer_p, Vec3::up(), uv_of(outer_p));
    }
    for (int j = 0; j < slices; ++j) {
        const uint32_t i0 = static_cast<uint32_t>(j * 2);
        const uint32_t o0 = i0 + 1;
        const uint32_t i1 = i0 + 2;
        const uint32_t o1 = i0 + 3;
        add_quad(mesh, i0, i1, o1, o0);
    }
    return mesh;
}

MeshData make_ring(float radius, float inner_radius, int segments) {
    const float inner = inner_radius > 0.0f ? inner_radius : radius * 0.5f;
    return make_disc(radius, inner, segments);
}

MeshData make_cone(float radius, float height, int segments) {
    const int slices = sane_segments(segments, 3);
    MeshData mesh;
    const float half_h = height * 0.5f;
    const float slope = std::sqrt(radius * radius + height * height);
    const float ny = slope > 1e-6f ? radius / slope : 1.0f;
    const float nr = slope > 1e-6f ? height / slope : 0.0f;

    // Side: one apex vertex per slice so the apex normal matches the face.
    for (int j = 0; j < slices; ++j) {
        const float a0 = static_cast<float>(j) / static_cast<float>(slices) * kTwoPi;
        const float a1 = static_cast<float>(j + 1) / static_cast<float>(slices) * kTwoPi;
        const float am = (a0 + a1) * 0.5f;
        const Vec3 p0{radius * std::cos(a0), -half_h, radius * std::sin(a0)};
        const Vec3 p1{radius * std::cos(a1), -half_h, radius * std::sin(a1)};
        const Vec3 apex{0.0f, half_h, 0.0f};
        const Vec3 n0{nr * std::cos(a0), ny, nr * std::sin(a0)};
        const Vec3 n1{nr * std::cos(a1), ny, nr * std::sin(a1)};
        const Vec3 nm{nr * std::cos(am), ny, nr * std::sin(am)};
        const uint32_t base = static_cast<uint32_t>(mesh.positions.size());
        add_vertex(mesh, p0, normalize(n0), Vec2{static_cast<float>(j) / static_cast<float>(slices), 1.0f});
        add_vertex(mesh, p1, normalize(n1), Vec2{static_cast<float>(j + 1) / static_cast<float>(slices), 1.0f});
        add_vertex(mesh, apex, normalize(nm), Vec2{(static_cast<float>(j) + 0.5f) / static_cast<float>(slices), 0.0f});
        add_triangle(mesh, base, base + 2, base + 1);
    }
    // Base cap, facing -Y.
    const uint32_t center = static_cast<uint32_t>(mesh.positions.size());
    add_vertex(mesh, Vec3{0.0f, -half_h, 0.0f}, Vec3{0, -1, 0}, Vec2{0.5f, 0.5f});
    for (int j = 0; j <= slices; ++j) {
        const float a = static_cast<float>(j) / static_cast<float>(slices) * kTwoPi;
        const float cos_a = std::cos(a), sin_a = std::sin(a);
        add_vertex(mesh, Vec3{radius * cos_a, -half_h, radius * sin_a}, Vec3{0, -1, 0},
                   Vec2{0.5f + 0.5f * cos_a, 0.5f + 0.5f * sin_a});
    }
    for (int j = 0; j < slices; ++j) {
        add_triangle(mesh, center, center + 1 + static_cast<uint32_t>(j), center + 2 + static_cast<uint32_t>(j));
    }
    return mesh;
}

MeshData make_cylinder(float radius, float height, int segments) {
    const int slices = sane_segments(segments, 3);
    const float half_h = height * 0.5f;
    MeshData mesh;
    // Side.
    const uint32_t side_base = static_cast<uint32_t>(mesh.positions.size());
    for (int j = 0; j <= slices; ++j) {
        const float u = static_cast<float>(j) / static_cast<float>(slices);
        const float a = u * kTwoPi;
        const Vec3 n{std::cos(a), 0.0f, std::sin(a)};
        add_vertex(mesh, Vec3{n.x * radius, half_h, n.z * radius}, n, Vec2{u, 0.0f});
        add_vertex(mesh, Vec3{n.x * radius, -half_h, n.z * radius}, n, Vec2{u, 1.0f});
    }
    for (int j = 0; j < slices; ++j) {
        const uint32_t t0 = side_base + static_cast<uint32_t>(j * 2);
        const uint32_t b0 = t0 + 1;
        const uint32_t t1 = t0 + 2;
        const uint32_t b1 = t0 + 3;
        add_quad(mesh, t0, t1, b1, b0);
    }
    // Top cap (+Y) and bottom cap (-Y).
    for (int side = 0; side < 2; ++side) {
        const float y = side == 0 ? half_h : -half_h;
        const Vec3 n = side == 0 ? Vec3{0, 1, 0} : Vec3{0, -1, 0};
        const uint32_t center = static_cast<uint32_t>(mesh.positions.size());
        add_vertex(mesh, Vec3{0.0f, y, 0.0f}, n, Vec2{0.5f, 0.5f});
        for (int j = 0; j <= slices; ++j) {
            const float a = static_cast<float>(j) / static_cast<float>(slices) * kTwoPi;
            const float cos_a = std::cos(a), sin_a = std::sin(a);
            add_vertex(mesh, Vec3{radius * cos_a, y, radius * sin_a}, n, Vec2{0.5f + 0.5f * cos_a, 0.5f + 0.5f * sin_a});
        }
        for (int j = 0; j < slices; ++j) {
            const uint32_t r0 = center + 1 + static_cast<uint32_t>(j);
            const uint32_t r1 = center + 2 + static_cast<uint32_t>(j);
            if (side == 0) add_triangle(mesh, center, r1, r0);
            else add_triangle(mesh, center, r0, r1);
        }
    }
    return mesh;
}

MeshData make_capsule(float radius, float height, int segments) {
    // `height` is the total height including the caps, so the mesh spans
    // [-height/2, +height/2] in Y and [-radius, radius] in X/Z.
    const int slices = sane_segments(segments * 2, 3);
    const int cap_stacks = std::max(2, sane_segments(segments, 2) / 2);
    const float r = std::max(radius, 0.0f);
    const float cylinder_h = std::max(height - 2.0f * r, 0.0f);
    const float half_c = cylinder_h * 0.5f;

    // Rings from the top pole down to the bottom pole.
    struct Ring {
        float y;      // centre offset of the ring's sphere cap
        float radial; // radius in XZ
        float ny;     // normal Y
        float nr;     // normal XZ scale
        float v;      // texture v
    };
    std::vector<Ring> rings;
    const float total_v = cylinder_h + 2.0f * r;
    const float cap_v = total_v > 0.0f ? r / total_v : 0.5f;
    for (int i = 0; i <= cap_stacks; ++i) {
        const float t = static_cast<float>(i) / static_cast<float>(cap_stacks);
        const float theta = t * kHalfPi;  // 0 at the pole
        const float s = std::sin(theta), c = std::cos(theta);
        rings.push_back(Ring{half_c + r * c, r * s, c, s, cap_v * t});
    }
    if (cylinder_h > 0.0f) {
        rings.push_back(Ring{-half_c, r, 0.0f, 1.0f, total_v > 0.0f ? (r + cylinder_h) / total_v : 0.5f});
    }
    for (int i = 1; i <= cap_stacks; ++i) {
        const float t = static_cast<float>(i) / static_cast<float>(cap_stacks);
        const float theta = kHalfPi + t * kHalfPi;
        const float s = std::sin(theta), c = std::cos(theta);
        rings.push_back(Ring{-half_c + r * c, r * s, c, s, 1.0f - cap_v * (1.0f - t)});
    }

    MeshData mesh;
    for (const Ring& ring : rings) {
        for (int j = 0; j <= slices; ++j) {
            const float u = static_cast<float>(j) / static_cast<float>(slices);
            const float a = u * kTwoPi;
            const float cos_a = std::cos(a), sin_a = std::sin(a);
            const Vec3 n = normalize(Vec3{ring.nr * cos_a, ring.ny, ring.nr * sin_a});
            add_vertex(mesh, Vec3{ring.radial * cos_a, ring.y, ring.radial * sin_a}, n, Vec2{u, ring.v});
        }
    }
    const int ring_count = static_cast<int>(rings.size());
    const auto index_of = [slices](int i, int j) { return static_cast<uint32_t>(i * (slices + 1) + j); };
    for (int i = 0; i + 1 < ring_count; ++i) {
        for (int j = 0; j < slices; ++j) {
            const uint32_t a = index_of(i, j);
            const uint32_t b = index_of(i, j + 1);
            const uint32_t c = index_of(i + 1, j + 1);
            const uint32_t d = index_of(i + 1, j);
            if (rings[static_cast<size_t>(i)].radial <= 0.0f) add_triangle(mesh, a, c, d);
            else if (rings[static_cast<size_t>(i) + 1].radial <= 0.0f) add_triangle(mesh, a, b, c);
            else add_quad(mesh, a, b, c, d);
        }
    }
    return mesh;
}

MeshData make_tube(const std::vector<Vec3>& path, float radius, int segments) {
    MeshData mesh;
    if (path.size() < 2) return mesh;
    const int slices = sane_segments(segments, 3);
    const std::vector<Vec3> tangents = path_tangents(path);
    std::vector<Vec3> normals, binormals;
    parallel_transport_frames(tangents, Vec3::up(), normals, binormals);

    std::vector<float> arc(path.size(), 0.0f);
    for (size_t i = 1; i < path.size(); ++i) arc[i] = arc[i - 1] + distance(path[i], path[i - 1]);
    const float total = arc.back() > 0.0f ? arc.back() : 1.0f;

    for (size_t i = 0; i < path.size(); ++i) {
        for (int j = 0; j <= slices; ++j) {
            const float u = static_cast<float>(j) / static_cast<float>(slices);
            const float a = u * kTwoPi;
            const Vec3 dir = normals[i] * std::cos(a) + binormals[i] * std::sin(a);
            add_vertex(mesh, path[i] + dir * radius, dir, Vec2{u, arc[i] / total});
        }
    }
    const auto index_of = [slices](size_t i, int j) {
        return static_cast<uint32_t>(i * static_cast<size_t>(slices + 1) + static_cast<size_t>(j));
    };
    for (size_t i = 0; i + 1 < path.size(); ++i) {
        for (int j = 0; j < slices; ++j) {
            add_quad(mesh, index_of(i, j), index_of(i, j + 1), index_of(i + 1, j + 1), index_of(i + 1, j));
        }
    }
    return mesh;
}

MeshData make_ribbon(const std::vector<Vec3>& path, float width, Vec3 up) {
    MeshData mesh;
    if (path.size() < 2) return mesh;
    const std::vector<Vec3> tangents = path_tangents(path);
    std::vector<Vec3> normals, binormals;
    const Vec3 seed_up = length(up) > 1e-6f ? normalize(up) : Vec3::up();
    parallel_transport_frames(tangents, seed_up, normals, binormals);

    std::vector<float> arc(path.size(), 0.0f);
    for (size_t i = 1; i < path.size(); ++i) arc[i] = arc[i - 1] + distance(path[i], path[i - 1]);
    const float total = arc.back() > 0.0f ? arc.back() : 1.0f;

    const float half_w = width * 0.5f;
    for (size_t i = 0; i < path.size(); ++i) {
        // `binormals` is the in-plane side direction; `normals` faces the sheet.
        const Vec3 side = binormals[i];
        const Vec3 face = normals[i];
        add_vertex(mesh, path[i] - side * half_w, face, Vec2{0.0f, arc[i] / total});
        add_vertex(mesh, path[i] + side * half_w, face, Vec2{1.0f, arc[i] / total});
    }
    for (size_t i = 0; i + 1 < path.size(); ++i) {
        const uint32_t a = static_cast<uint32_t>(i * 2);
        const uint32_t b = a + 1;
        const uint32_t c = a + 3;
        const uint32_t d = a + 2;
        add_quad(mesh, a, b, c, d);
    }
    return mesh;
}

MeshData load_obj(const std::filesystem::path& path) {
    std::ifstream file(path);
    if (!file) {
        throw Error("io", "cannot open OBJ file '" + path.string() + "'");
    }
    std::vector<Vec3> positions;
    std::vector<Vec3> normals;
    std::vector<Vec2> uvs;
    MeshData mesh;
    std::unordered_map<ObjKey, uint32_t, ObjKeyHash> vertex_cache;

    std::string line;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        std::istringstream stream(line);
        std::string tag;
        if (!(stream >> tag)) continue;
        if (tag == "v") {
            Vec3 p;
            stream >> p.x >> p.y >> p.z;
            positions.push_back(p);
        } else if (tag == "vn") {
            Vec3 n;
            stream >> n.x >> n.y >> n.z;
            normals.push_back(n);
        } else if (tag == "vt") {
            Vec2 t;
            stream >> t.x >> t.y;
            uvs.push_back(t);
        } else if (tag == "f") {
            std::vector<uint32_t> face;
            std::string token;
            while (stream >> token) {
                const ObjKey key = parse_face_vertex(token);
                auto cached = vertex_cache.find(key);
                if (cached != vertex_cache.end()) {
                    face.push_back(cached->second);
                    continue;
                }
                const int vi = resolve_obj_index(key.v, positions.size());
                if (vi < 0 || vi >= static_cast<int>(positions.size())) continue;
                const int ti = resolve_obj_index(key.vt, uvs.size());
                const int ni = resolve_obj_index(key.vn, normals.size());
                const Vec3 position = positions[static_cast<size_t>(vi)];
                const Vec3 normal = (ni >= 0 && ni < static_cast<int>(normals.size()))
                                        ? normals[static_cast<size_t>(ni)]
                                        : Vec3::zero();
                const Vec2 uv = (ti >= 0 && ti < static_cast<int>(uvs.size())) ? uvs[static_cast<size_t>(ti)] : Vec2{};
                const uint32_t index = static_cast<uint32_t>(mesh.positions.size());
                add_vertex(mesh, position, normal, uv);
                vertex_cache.emplace(key, index);
                face.push_back(index);
            }
            for (size_t i = 2; i < face.size(); ++i) {  // fan triangulation
                add_triangle(mesh, face[0], face[i - 1], face[i]);
            }
        }
        // mtllib / usemtl / o / g / s are intentionally ignored.
    }
    if (mesh.positions.empty()) {
        throw Error("io", "OBJ file '" + path.string() + "' contains no geometry");
    }
    // Fill in missing normals with area-weighted face normals.
    bool needs_normals = false;
    for (const Vec3& n : mesh.normals) {
        if (length_squared(n) < 1e-12f) { needs_normals = true; break; }
    }
    if (needs_normals) {
        std::vector<Vec3> accumulated(mesh.positions.size(), Vec3::zero());
        for (size_t t = 0; t + 2 < mesh.indices.size(); t += 3) {
            const uint32_t ia = mesh.indices[t], ib = mesh.indices[t + 1], ic = mesh.indices[t + 2];
            const Vec3 face_normal = cross(mesh.positions[ib] - mesh.positions[ia], mesh.positions[ic] - mesh.positions[ia]);
            accumulated[ia] += face_normal;
            accumulated[ib] += face_normal;
            accumulated[ic] += face_normal;
        }
        for (size_t i = 0; i < mesh.normals.size(); ++i) {
            if (length_squared(mesh.normals[i]) < 1e-12f) {
                const Vec3 n = accumulated[i];
                mesh.normals[i] = length(n) > 1e-9f ? normalize(n) : Vec3::up();
            }
        }
    }
    return mesh;
}

// ---------------------------------------------------------------------------
// Sampling
// ---------------------------------------------------------------------------

MeshData make_crystal(float radius, float height, int segments, float irregularity, uint32_t seed) {
    // 5..8 facets read as a crystal; more just looks like a cone, so the
    // vocabulary's `segments` is clamped into that range (3 is still allowed).
    const int slices = clamp(sane_segments(segments, 3), 3, 8);
    const float r = std::max(0.0f, radius);
    const float h = std::max(0.0f, height);
    const float irr = saturate(irregularity);
    const float half_h = h * 0.5f;
    Pcg32 rng(seed);

    MeshData mesh;
    // The apex is jittered downwards only, so a crystal never exceeds `height`.
    const float apex_y = half_h * (1.0f - 0.3f * irr * rng.next_float());
    add_spike(mesh, rng, slices, r, half_h, apex_y, irr, Vec2{0.0f, 0.0f});
    // With probability `irregularity`, a shorter twin spike fused at the base.
    if (rng.chance(irr)) {
        const float azimuth = rng.next_float() * kTwoPi;
        const float offset = r * (0.45f + 0.35f * rng.next_float());
        const Vec2 centre{offset * std::cos(azimuth), offset * std::sin(azimuth)};
        const float twin_apex = -half_h + (apex_y + half_h) * 0.6f;
        add_spike(mesh, rng, std::max(3, slices - 1), r * 0.5f, half_h, twin_apex, irr, centre);
    }
    fit_in_cylinder(mesh, r, h);
    finalize_faceted(mesh, h);
    return mesh;
}

MeshData make_rock(float radius, int segments, float irregularity, uint32_t seed) {
    const float r = std::max(0.0f, radius);
    const float irr = saturate(irregularity);
    Pcg32 rng(seed);

    std::vector<Vec3> unit;
    std::vector<uint32_t> indices;
    icosahedron(unit, indices);
    if (segments >= 10) subdivide_unit(unit, indices);  // 20 -> 80 facets

    // Displacement is sampled on the *unit* sphere, so vertices shared by
    // several facets always move by the same amount and the hull stays closed.
    const FbmParams fbm{2, 2.0f, 0.5f, NoiseBasis::Simplex};
    const Vec3 offset{rng.signed_unit() * 8.0f, rng.signed_unit() * 8.0f, rng.signed_unit() * 8.0f};
    std::vector<Vec3> positions(unit.size());
    for (size_t i = 0; i < unit.size(); ++i) {
        const float n = fbm3(unit[i] * 1.7f + offset, seed, fbm);
        positions[i] = unit[i] * std::max(r * 0.25f, r + n * irr * r * 0.6f);
    }
    const Vec3 squash{1.0f + rng.signed_unit() * 0.4f * irr, 1.0f + rng.signed_unit() * 0.4f * irr,
                      1.0f + rng.signed_unit() * 0.4f * irr};
    for (Vec3& p : positions) p *= squash;

    MeshData mesh;
    for (size_t t = 0; t + 2 < indices.size(); t += 3)
        add_facet(mesh, positions[indices[t]], positions[indices[t + 1]], positions[indices[t + 2]]);
    fit_in_cylinder(mesh, r, 2.0f * r);
    finalize_faceted(mesh, 2.0f * r);
    return mesh;
}

MeshData make_shard(float radius, float height, float irregularity, uint32_t seed) {
    const float r = std::max(0.0f, radius);
    const float h = std::max(0.0f, height);
    const float irr = saturate(irregularity);
    const float half_h = h * 0.5f;
    const float half_t = 0.06f * r;  // thickness = 0.12 * radius
    Pcg32 rng(seed);

    // Outline in XY, walking counter-clockwise from the sharp end at +Y.
    const int n = rng.range_int(4, 6);
    const float gap = kTwoPi / static_cast<float>(n);
    std::vector<Vec2> outline(static_cast<size_t>(n));
    for (int i = 0; i < n; ++i) {
        const float jitter = rng.signed_unit() * gap * 0.25f * irr;
        const float angle = kHalfPi + static_cast<float>(i) * gap + (i == 0 ? 0.0f : jitter);
        // the apex's two neighbours are pulled in, which is what makes the tip sharp
        float k = (i == 1 || i == n - 1) ? 0.55f : 1.0f;
        k *= 1.0f - 0.45f * irr * rng.next_float();
        outline[static_cast<size_t>(i)] = Vec2{r * std::cos(angle) * k, half_h * std::sin(angle) * k};
    }
    outline[0].x += rng.signed_unit() * 0.2f * irr * r;

    MeshData mesh;
    const Vec3 front_centre{0.0f, 0.0f, half_t};
    const Vec3 back_centre{0.0f, 0.0f, -half_t};
    for (int i = 0; i < n; ++i) {
        const Vec2 a2 = outline[static_cast<size_t>(i)];
        const Vec2 b2 = outline[static_cast<size_t>((i + 1) % n)];
        const Vec3 af{a2.x, a2.y, half_t}, bf{b2.x, b2.y, half_t};
        const Vec3 ab{a2.x, a2.y, -half_t}, bb{b2.x, b2.y, -half_t};
        add_facet(mesh, front_centre, af, bf);  // +Z face
        add_facet(mesh, back_centre, bb, ab);   // -Z face
        add_facet(mesh, af, ab, bb);            // rim
        add_facet(mesh, af, bb, bf);
    }
    fit_in_cylinder(mesh, r, h);
    finalize_faceted(mesh, h);
    return mesh;
}

Vec3 sample_surface(MeshData& mesh, Pcg32& rng, Vec3* normal) {
    if (mesh.indices.size() < 3 || mesh.positions.empty()) {
        if (normal) *normal = Vec3::up();
        return Vec3::zero();
    }
    if (mesh.cumulative_area.size() != mesh.triangle_count()) mesh.build_area_table();
    const float total = mesh.total_area();
    size_t tri = 0;
    if (total > 0.0f) {
        const float target = rng.next_float() * total;
        // First triangle whose cumulative area is >= target.
        const auto it = std::lower_bound(mesh.cumulative_area.begin(), mesh.cumulative_area.end(), target);
        tri = static_cast<size_t>(it - mesh.cumulative_area.begin());
        if (tri >= mesh.triangle_count()) tri = mesh.triangle_count() - 1;
    } else {
        tri = static_cast<size_t>(rng.range_int(0, static_cast<int>(mesh.triangle_count()) - 1));
    }
    const uint32_t ia = mesh.indices[tri * 3];
    const uint32_t ib = mesh.indices[tri * 3 + 1];
    const uint32_t ic = mesh.indices[tri * 3 + 2];
    // Uniform barycentric coordinates on a triangle.
    const float r1 = std::sqrt(rng.next_float());
    const float r2 = rng.next_float();
    const float wa = 1.0f - r1;
    const float wb = r1 * (1.0f - r2);
    const float wc = r1 * r2;
    const Vec3 pa = mesh.positions[ia], pb = mesh.positions[ib], pc = mesh.positions[ic];
    if (normal) {
        Vec3 n;
        if (mesh.normals.size() == mesh.positions.size()) {
            n = mesh.normals[ia] * wa + mesh.normals[ib] * wb + mesh.normals[ic] * wc;
        } else {
            n = cross(pb - pa, pc - pa);
        }
        *normal = length(n) > 1e-9f ? normalize(n) : Vec3::up();
    }
    return pa * wa + pb * wb + pc * wc;
}

Vec3 sample_bounds(const MeshData& mesh, Pcg32& rng) {
    const Bounds b = mesh.bounds();
    if (!b.valid()) return Vec3::zero();
    return {rng.range(b.min.x, b.max.x), rng.range(b.min.y, b.max.y), rng.range(b.min.z, b.max.z)};
}

// ---------------------------------------------------------------------------
// Curves
// ---------------------------------------------------------------------------

bool parse_curve_type(std::string_view s, CurveType& out) {
    if (s == "linear") { out = CurveType::Linear; return true; }
    if (s == "catmull_rom") { out = CurveType::CatmullRom; return true; }
    if (s == "bezier") { out = CurveType::Bezier; return true; }
    return false;
}

Vec3 evaluate_curve(const std::vector<Vec3>& points, CurveType type, bool closed, float t) {
    if (points.empty()) return Vec3::zero();
    if (points.size() == 1) return points[0];
    const int segments = curve_segment_count(points.size(), type, closed);
    if (segments <= 0) return points[0];
    const float clamped = clamp(t, 0.0f, 1.0f);
    const float s = clamped * static_cast<float>(segments);
    int index = static_cast<int>(std::floor(s));
    if (index >= segments) index = segments - 1;
    const float local = s - static_cast<float>(index);

    if (type == CurveType::Linear) {
        return lerp(point_at(points, index, closed), point_at(points, index + 1, closed), local);
    }
    if (type == CurveType::Bezier && bezier_layout_ok(points.size(), closed)) {
        const int base = index * 3;
        return cubic_bezier(point_at(points, base, closed), point_at(points, base + 1, closed),
                            point_at(points, base + 2, closed), point_at(points, base + 3, closed), local);
    }
    // catmull_rom (and the bezier fallback for a non-conforming point count)
    return catmull_rom(point_at(points, index - 1, closed), point_at(points, index, closed),
                       point_at(points, index + 1, closed), point_at(points, index + 2, closed), local);
}

Vec3 evaluate_curve_tangent(const std::vector<Vec3>& points, CurveType type, bool closed, float t) {
    if (points.size() < 2) return Vec3::forward();
    constexpr float kStep = 1e-3f;
    const float clamped = clamp(t, 0.0f, 1.0f);
    const float t0 = closed ? clamped - kStep : std::max(0.0f, clamped - kStep);
    const float t1 = closed ? clamped + kStep : std::min(1.0f, clamped + kStep);
    const float a = closed ? t0 - std::floor(t0) : t0;
    const float b = closed ? (t1 >= 1.0f ? t1 - 1.0f : t1) : t1;
    const Vec3 d = evaluate_curve(points, type, closed, b) - evaluate_curve(points, type, closed, a);
    if (length(d) < 1e-9f) {
        const Vec3 fallback = points.back() - points.front();
        return length(fallback) > 1e-9f ? normalize(fallback) : Vec3::forward();
    }
    return normalize(d);
}

std::vector<Vec3> tessellate_curve(const std::vector<Vec3>& points, CurveType type, bool closed, int segments) {
    std::vector<Vec3> out;
    const int n = std::max(1, segments);
    if (points.empty()) return out;
    if (points.size() == 1) { out.push_back(points[0]); return out; }
    const int count = closed ? n : n + 1;
    out.reserve(static_cast<size_t>(count));
    for (int i = 0; i < count; ++i) {
        out.push_back(evaluate_curve(points, type, closed, static_cast<float>(i) / static_cast<float>(n)));
    }
    return out;
}

float polyline_length(const std::vector<Vec3>& pts) {
    float total = 0.0f;
    for (size_t i = 1; i < pts.size(); ++i) total += distance(pts[i], pts[i - 1]);
    return total;
}

}  // namespace aether::procedural
