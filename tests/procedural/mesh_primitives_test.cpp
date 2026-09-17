// Mesh primitives, OBJ loading, surface sampling and curve evaluation.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/procedural/mesh_primitives.hpp"

using namespace aether;
using namespace aether::procedural;

namespace {

std::filesystem::path test_output_dir() {
    const std::filesystem::path dir{AETHER_TEST_OUTPUT_DIR};
    std::filesystem::create_directories(dir);
    return dir;
}

struct MeshCheck {
    bool indices_in_range = true;
    bool normals_unit = true;
    bool uvs_sized = true;
    bool normals_sized = true;
    bool triangle_multiple = true;
    bool winding_outward = true;  // face normal agrees with the interpolated vertex normals
    float worst_normal_error = 0.0f;
};

MeshCheck check_mesh(const MeshData& mesh) {
    MeshCheck result;
    result.normals_sized = mesh.normals.size() == mesh.positions.size();
    result.uvs_sized = mesh.uvs.size() == mesh.positions.size();
    result.triangle_multiple = mesh.indices.size() % 3 == 0;
    for (uint32_t index : mesh.indices) {
        if (index >= mesh.positions.size()) result.indices_in_range = false;
    }
    for (const Vec3& n : mesh.normals) {
        const float error = std::fabs(length(n) - 1.0f);
        result.worst_normal_error = std::max(result.worst_normal_error, error);
        if (error > 1e-4f) result.normals_unit = false;
    }
    if (result.indices_in_range && result.normals_sized) {
        for (size_t t = 0; t + 2 < mesh.indices.size(); t += 3) {
            const Vec3 a = mesh.positions[mesh.indices[t]];
            const Vec3 b = mesh.positions[mesh.indices[t + 1]];
            const Vec3 c = mesh.positions[mesh.indices[t + 2]];
            const Vec3 face = cross(b - a, c - a);
            if (length(face) < 1e-12f) continue;  // degenerate, ignore
            const Vec3 vertex_normal = mesh.normals[mesh.indices[t]] + mesh.normals[mesh.indices[t + 1]] +
                                       mesh.normals[mesh.indices[t + 2]];
            if (length(vertex_normal) < 1e-9f) continue;
            if (dot(normalize(face), normalize(vertex_normal)) <= 0.0f) result.winding_outward = false;
        }
    }
    return result;
}

void require_valid(const MeshData& mesh) {
    const MeshCheck check = check_mesh(mesh);
    INFO("worst normal error = " << check.worst_normal_error);
    REQUIRE(check.triangle_multiple);
    REQUIRE(check.indices_in_range);
    REQUIRE(check.normals_sized);
    REQUIRE(check.uvs_sized);
    REQUIRE(check.normals_unit);
    REQUIRE(check.winding_outward);
    REQUIRE(mesh.triangle_count() > 0);
}

std::vector<Vec3> demo_path() {
    return {{0, 0, 0}, {0, 1, 0}, {0.5f, 2.0f, 0.0f}, {1.5f, 2.5f, 0.5f}};
}

}  // namespace

TEST_CASE("sphere", "[procedural][mesh]") {
    constexpr int kSegments = 16;
    const MeshData sphere = make_sphere(1.0f, kSegments);
    require_valid(sphere);

    // stacks = segments, slices = 2*segments; the pole rows are triangles.
    const size_t slices = static_cast<size_t>(kSegments) * 2;
    REQUIRE(sphere.triangle_count() == 2 * slices * static_cast<size_t>(kSegments - 1));
    REQUIRE(sphere.positions.size() == static_cast<size_t>(kSegments + 1) * (slices + 1));

    const Bounds bounds = sphere.bounds();
    REQUIRE(bounds.min.x == Catch::Approx(-1.0f).margin(1e-5));
    REQUIRE(bounds.min.y == Catch::Approx(-1.0f).margin(1e-5));
    REQUIRE(bounds.min.z == Catch::Approx(-1.0f).margin(1e-5));
    REQUIRE(bounds.max.x == Catch::Approx(1.0f).margin(1e-5));
    REQUIRE(bounds.max.y == Catch::Approx(1.0f).margin(1e-5));
    REQUIRE(bounds.max.z == Catch::Approx(1.0f).margin(1e-5));

    // Every vertex is on the sphere and its normal points outward.
    for (size_t i = 0; i < sphere.positions.size(); ++i) {
        REQUIRE(length(sphere.positions[i]) == Catch::Approx(1.0f).margin(1e-5));
        REQUIRE(dot(sphere.normals[i], normalize(sphere.positions[i])) == Catch::Approx(1.0f).margin(1e-4));
        REQUIRE(sphere.uvs[i].x >= 0.0f);
        REQUIRE(sphere.uvs[i].x <= 1.0f);
        REQUIRE(sphere.uvs[i].y >= 0.0f);
        REQUIRE(sphere.uvs[i].y <= 1.0f);
    }

    MeshData scaled = make_sphere(2.5f, 32);
    scaled.build_area_table();
    REQUIRE(scaled.total_area() == Catch::Approx(4.0f * kPi * 2.5f * 2.5f).epsilon(0.02));
}

TEST_CASE("cube", "[procedural][mesh]") {
    const MeshData cube = make_cube(Vec3{1.0f, 2.0f, 3.0f});
    require_valid(cube);
    REQUIRE(cube.triangle_count() == 12);
    REQUIRE(cube.positions.size() == 24);  // hard edges: one vertex per face corner

    const Bounds bounds = cube.bounds();
    REQUIRE(bounds.min == Vec3{-0.5f, -1.0f, -1.5f});
    REQUIRE(bounds.max == Vec3{0.5f, 1.0f, 1.5f});

    MeshData copy = cube;
    copy.build_area_table();
    REQUIRE(copy.total_area() == Catch::Approx(2.0f * (1 * 2 + 1 * 3 + 2 * 3)));
}

TEST_CASE("plane, disc and ring face +Y", "[procedural][mesh]") {
    const MeshData plane = make_plane(Vec2{2.0f, 4.0f}, 3);
    require_valid(plane);
    REQUIRE(plane.triangle_count() == 3 * 3 * 2);
    for (const Vec3& n : plane.normals) REQUIRE(n == Vec3::up());
    const Bounds pb = plane.bounds();
    REQUIRE(pb.min == Vec3{-1.0f, 0.0f, -2.0f});
    REQUIRE(pb.max == Vec3{1.0f, 0.0f, 2.0f});

    const MeshData disc = make_disc(1.0f, 0.0f, 24);
    require_valid(disc);
    REQUIRE(disc.triangle_count() == 24);
    for (const Vec3& n : disc.normals) REQUIRE(n == Vec3::up());

    const MeshData ring = make_ring(1.0f, 0.5f, 24);
    require_valid(ring);
    REQUIRE(ring.triangle_count() == 48);
    float inner_radius = 1e9f;
    for (const Vec3& p : ring.positions) inner_radius = std::min(inner_radius, length(p));
    REQUIRE(inner_radius == Catch::Approx(0.5f).margin(1e-5));

    // make_ring with inner_radius <= 0 still produces an annulus.
    const MeshData default_ring = make_ring(1.0f, 0.0f, 12);
    require_valid(default_ring);
    float default_inner = 1e9f;
    for (const Vec3& p : default_ring.positions) default_inner = std::min(default_inner, length(p));
    REQUIRE(default_inner > 0.0f);

    // All three primitives generate CCW triangles seen from +Y.
    for (const MeshData* mesh : {&plane, &disc, &ring}) {
        const Vec3 a = mesh->positions[mesh->indices[0]];
        const Vec3 b = mesh->positions[mesh->indices[1]];
        const Vec3 c = mesh->positions[mesh->indices[2]];
        REQUIRE(normalize(cross(b - a, c - a)).y > 0.9f);
    }
}

TEST_CASE("cone, cylinder and capsule", "[procedural][mesh]") {
    const MeshData cone = make_cone(0.5f, 1.0f, 24);
    require_valid(cone);
    const Bounds cone_bounds = cone.bounds();
    REQUIRE(cone_bounds.max.y == Catch::Approx(0.5f).margin(1e-5));
    REQUIRE(cone_bounds.min.y == Catch::Approx(-0.5f).margin(1e-5));
    REQUIRE(cone_bounds.max.x == Catch::Approx(0.5f).margin(1e-5));

    const MeshData cylinder = make_cylinder(0.5f, 2.0f, 24);
    require_valid(cylinder);
    const Bounds cyl_bounds = cylinder.bounds();
    REQUIRE(cyl_bounds.min.y == Catch::Approx(-1.0f).margin(1e-5));
    REQUIRE(cyl_bounds.max.y == Catch::Approx(1.0f).margin(1e-5));
    MeshData cyl_copy = cylinder;
    cyl_copy.build_area_table();
    // side (2*pi*r*h) + two caps (2*pi*r^2), minus the polygonal approximation.
    REQUIRE(cyl_copy.total_area() == Catch::Approx(kTwoPi * 0.5f * 2.0f + kTwoPi * 0.25f).epsilon(0.03));

    const MeshData capsule = make_capsule(0.5f, 2.0f, 16);
    require_valid(capsule);
    const Bounds cap_bounds = capsule.bounds();
    REQUIRE(cap_bounds.min.y == Catch::Approx(-1.0f).margin(1e-4));
    REQUIRE(cap_bounds.max.y == Catch::Approx(1.0f).margin(1e-4));
    REQUIRE(cap_bounds.max.x == Catch::Approx(0.5f).margin(1e-4));
    // A capsule shorter than its diameter degenerates gracefully into a sphere.
    require_valid(make_capsule(0.5f, 0.5f, 12));
}

TEST_CASE("tube and ribbon follow a path", "[procedural][mesh]") {
    const std::vector<Vec3> path = demo_path();

    const MeshData tube = make_tube(path, 0.2f, 12);
    require_valid(tube);
    REQUIRE(tube.positions.size() == path.size() * 13);
    REQUIRE(tube.triangle_count() == (path.size() - 1) * 12 * 2);
    // Every ring vertex sits `radius` away from its path point.
    for (size_t i = 0; i < path.size(); ++i) {
        for (int j = 0; j <= 12; ++j) {
            const Vec3 p = tube.positions[i * 13 + static_cast<size_t>(j)];
            REQUIRE(distance(p, path[i]) == Catch::Approx(0.2f).margin(1e-4));
        }
    }

    const MeshData ribbon = make_ribbon(path, 0.3f);
    require_valid(ribbon);
    REQUIRE(ribbon.positions.size() == path.size() * 2);
    REQUIRE(ribbon.triangle_count() == (path.size() - 1) * 2);
    for (size_t i = 0; i < path.size(); ++i) {
        REQUIRE(distance(ribbon.positions[i * 2], ribbon.positions[i * 2 + 1]) == Catch::Approx(0.3f).margin(1e-4));
    }

    // Degenerate paths produce empty meshes instead of throwing.
    REQUIRE(make_tube({}, 1.0f, 8).positions.empty());
    REQUIRE(make_tube({Vec3{0, 0, 0}}, 1.0f, 8).positions.empty());
    REQUIRE(make_ribbon({Vec3{0, 0, 0}}, 1.0f).positions.empty());
}

TEST_CASE("surface sampling is area weighted and lands on the surface", "[procedural][mesh]") {
    MeshData sphere = make_sphere(1.5f, 24);
    Pcg32 rng(4242);
    Vec3 normal{};
    for (int i = 0; i < 5000; ++i) {
        const Vec3 p = sample_surface(sphere, rng, &normal);
        REQUIRE(length(p) == Catch::Approx(1.5f).epsilon(0.01));
        REQUIRE(length(normal) == Catch::Approx(1.0f).margin(1e-4));
        REQUIRE(dot(normal, normalize(p)) > 0.9f);
    }
    REQUIRE(sphere.cumulative_area.size() == sphere.triangle_count());
    REQUIRE(sphere.total_area() == Catch::Approx(4.0f * kPi * 1.5f * 1.5f).epsilon(0.02));

    SECTION("sampling is reproducible from the same stream") {
        MeshData a = make_sphere(1.0f, 12);
        MeshData b = make_sphere(1.0f, 12);
        Pcg32 rng_a(7), rng_b(7);
        for (int i = 0; i < 200; ++i) {
            REQUIRE(sample_surface(a, rng_a) == sample_surface(b, rng_b));
        }
    }

    SECTION("area weighting favours the large face") {
        // A long thin quad plus a tiny one: almost every sample lands on the big face.
        MeshData mesh;
        mesh.positions = {{0, 0, 0}, {10, 0, 0}, {10, 0, 10}, {0, 0, 10}, {-1, 0, 0}, {-0.9f, 0, 0}, {-0.9f, 0, 0.1f}};
        mesh.normals.assign(mesh.positions.size(), Vec3::up());
        mesh.uvs.assign(mesh.positions.size(), Vec2{});
        mesh.indices = {0, 1, 2, 0, 2, 3, 4, 5, 6};
        Pcg32 stream(1);
        int on_big = 0;
        for (int i = 0; i < 2000; ++i) {
            const Vec3 p = sample_surface(mesh, stream);
            if (p.x >= 0.0f) ++on_big;
        }
        REQUIRE(on_big > 1990);
    }

    SECTION("sample_bounds stays inside the bounding box") {
        const MeshData cube = make_cube(Vec3{2, 4, 6});
        Pcg32 stream(3);
        for (int i = 0; i < 1000; ++i) {
            const Vec3 p = sample_bounds(cube, stream);
            REQUIRE(p.x >= -1.0f);
            REQUIRE(p.x <= 1.0f);
            REQUIRE(p.y >= -2.0f);
            REQUIRE(p.y <= 2.0f);
            REQUIRE(p.z >= -3.0f);
            REQUIRE(p.z <= 3.0f);
        }
    }

    SECTION("an empty mesh does not crash") {
        MeshData empty;
        Pcg32 stream(0);
        REQUIRE(sample_surface(empty, stream) == Vec3::zero());
        REQUIRE(sample_bounds(empty, stream) == Vec3::zero());
    }
}

TEST_CASE("OBJ round trip", "[procedural][mesh]") {
    const std::filesystem::path path = test_output_dir() / "quad.obj";
    {
        std::ofstream out(path);
        out << "# aetherfx test quad\n"
            << "mtllib ignored.mtl\n"
            << "o quad\n"
            << "v -1.0 0.0 -1.0\n"
            << "v  1.0 0.0 -1.0\n"
            << "v  1.0 0.0  1.0\n"
            << "v -1.0 0.0  1.0\n"
            << "vt 0.0 0.0\n"
            << "vt 1.0 0.0\n"
            << "vt 1.0 1.0\n"
            << "vt 0.0 1.0\n"
            << "vn 0.0 1.0 0.0\n"
            << "usemtl none\n"
            << "s off\n"
            << "f 1/1/1 2/2/1 3/3/1 4/4/1\n";  // a quad, must be fan-triangulated
    }
    const MeshData mesh = load_obj(path);
    REQUIRE(mesh.positions.size() == 4);
    REQUIRE(mesh.normals.size() == 4);
    REQUIRE(mesh.uvs.size() == 4);
    REQUIRE(mesh.triangle_count() == 2);
    for (const Vec3& n : mesh.normals) REQUIRE(n == Vec3::up());
    REQUIRE(mesh.uvs[2].x == Catch::Approx(1.0f));
    REQUIRE(mesh.uvs[2].y == Catch::Approx(1.0f));
    const Bounds bounds = mesh.bounds();
    REQUIRE(bounds.min == Vec3{-1, 0, -1});
    REQUIRE(bounds.max == Vec3{1, 0, 1});
    MeshData copy = mesh;
    copy.build_area_table();
    REQUIRE(copy.total_area() == Catch::Approx(4.0f));

    SECTION("positions-only faces get generated normals") {
        const std::filesystem::path bare = test_output_dir() / "tri.obj";
        {
            std::ofstream out(bare);
            out << "v 0 0 0\nv 1 0 0\nv 0 0 1\nf 1 2 3\n";
        }
        const MeshData tri = load_obj(bare);
        REQUIRE(tri.triangle_count() == 1);
        REQUIRE(tri.normals.size() == 3);
        for (const Vec3& n : tri.normals) REQUIRE(length(n) == Catch::Approx(1.0f).margin(1e-5));
    }

    SECTION("negative indices count from the end") {
        const std::filesystem::path neg = test_output_dir() / "negative.obj";
        {
            std::ofstream out(neg);
            out << "v 0 0 0\nv 1 0 0\nv 0 0 1\nf -3 -2 -1\n";
        }
        const MeshData tri = load_obj(neg);
        REQUIRE(tri.triangle_count() == 1);
        REQUIRE(tri.positions.size() == 3);
    }

    SECTION("a missing file throws Error(\"io\")") {
        const std::filesystem::path missing = test_output_dir() / "does_not_exist.obj";
        std::filesystem::remove(missing);
        REQUIRE_THROWS_AS(load_obj(missing), Error);
        try {
            load_obj(missing);
            FAIL("expected a throw");
        } catch (const Error& e) {
            REQUIRE(e.code() == "io");
        }
    }
}

TEST_CASE("parse_curve_type", "[procedural][curve]") {
    CurveType type = CurveType::Bezier;
    REQUIRE(parse_curve_type("linear", type));
    REQUIRE(type == CurveType::Linear);
    REQUIRE(parse_curve_type("catmull_rom", type));
    REQUIRE(type == CurveType::CatmullRom);
    REQUIRE(parse_curve_type("bezier", type));
    REQUIRE(type == CurveType::Bezier);
    REQUIRE_FALSE(parse_curve_type("spline", type));
    REQUIRE(type == CurveType::Bezier);  // unchanged on failure
}

TEST_CASE("curve endpoints and closure", "[procedural][curve]") {
    const std::vector<Vec3> points{{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}};

    for (CurveType type : {CurveType::Linear, CurveType::CatmullRom, CurveType::Bezier}) {
        INFO("curve type = " << static_cast<int>(type));
        REQUIRE(evaluate_curve(points, type, false, 0.0f) == points.front());
        REQUIRE(evaluate_curve(points, type, false, 1.0f) == points.back());
        // Clamped outside [0,1].
        REQUIRE(evaluate_curve(points, type, false, -1.0f) == points.front());
        REQUIRE(evaluate_curve(points, type, false, 2.0f) == points.back());
        // Closed curves come back to the start.
        REQUIRE(evaluate_curve(points, type, true, 0.0f) == evaluate_curve(points, type, true, 1.0f));
        REQUIRE(evaluate_curve(points, type, true, 0.0f) == points.front());
    }

    SECTION("degenerate inputs") {
        REQUIRE(evaluate_curve({}, CurveType::Linear, false, 0.5f) == Vec3::zero());
        const std::vector<Vec3> single{{3, 4, 5}};
        REQUIRE(evaluate_curve(single, CurveType::CatmullRom, false, 0.5f) == single[0]);
    }
}

TEST_CASE("curve interpolation", "[procedural][curve]") {
    SECTION("linear is piecewise") {
        const std::vector<Vec3> points{{0, 0, 0}, {2, 0, 0}, {2, 2, 0}};
        REQUIRE(evaluate_curve(points, CurveType::Linear, false, 0.25f) == Vec3{1, 0, 0});
        REQUIRE(evaluate_curve(points, CurveType::Linear, false, 0.5f) == Vec3{2, 0, 0});
        REQUIRE(evaluate_curve(points, CurveType::Linear, false, 0.75f) == Vec3{2, 1, 0});
    }

    SECTION("catmull_rom passes through its control points") {
        const std::vector<Vec3> points{{0, 0, 0}, {1, 1, 0}, {2, 0, 0}, {3, 1, 0}};
        for (int i = 0; i < 4; ++i) {
            const float t = static_cast<float>(i) / 3.0f;
            const Vec3 p = evaluate_curve(points, CurveType::CatmullRom, false, t);
            REQUIRE(p.x == Catch::Approx(points[static_cast<size_t>(i)].x).margin(1e-4));
            REQUIRE(p.y == Catch::Approx(points[static_cast<size_t>(i)].y).margin(1e-4));
        }
    }

    SECTION("bezier uses groups of four sharing endpoints") {
        // Two cubic segments: P0..P3 and P3..P6.
        const std::vector<Vec3> points{{0, 0, 0}, {0, 1, 0}, {1, 1, 0}, {1, 0, 0},
                                       {1, -1, 0}, {2, -1, 0}, {2, 0, 0}};
        REQUIRE(evaluate_curve(points, CurveType::Bezier, false, 0.0f) == Vec3{0, 0, 0});
        REQUIRE(evaluate_curve(points, CurveType::Bezier, false, 1.0f) == Vec3{2, 0, 0});
        const Vec3 joint = evaluate_curve(points, CurveType::Bezier, false, 0.5f);
        REQUIRE(joint.x == Catch::Approx(1.0f).margin(1e-5));
        REQUIRE(joint.y == Catch::Approx(0.0f).margin(1e-5));
        // Interior points bulge away from the chord.
        REQUIRE(evaluate_curve(points, CurveType::Bezier, false, 0.25f).y > 0.3f);

        SECTION("a non-conforming control count falls back to catmull_rom") {
            const std::vector<Vec3> five{{0, 0, 0}, {1, 0, 0}, {2, 0, 0}, {3, 0, 0}, {4, 0, 0}};
            REQUIRE(evaluate_curve(five, CurveType::Bezier, false, 0.5f) ==
                    evaluate_curve(five, CurveType::CatmullRom, false, 0.5f));
        }
    }
}

TEST_CASE("curve tangents, tessellation and length", "[procedural][curve]") {
    const std::vector<Vec3> line{{0, 0, 0}, {3, 0, 0}};

    SECTION("tangent of a straight line points along it") {
        for (float t : {0.0f, 0.25f, 0.5f, 1.0f}) {
            const Vec3 tangent = evaluate_curve_tangent(line, CurveType::Linear, false, t);
            REQUIRE(tangent.x == Catch::Approx(1.0f).margin(1e-4));
            REQUIRE(length(tangent) == Catch::Approx(1.0f).margin(1e-4));
        }
    }

    SECTION("tangent is always unit length on a curved path") {
        const std::vector<Vec3> points{{0, 0, 0}, {1, 2, 0}, {3, 1, 1}, {4, 3, -1}};
        for (int i = 0; i <= 20; ++i) {
            const float t = static_cast<float>(i) / 20.0f;
            for (CurveType type : {CurveType::Linear, CurveType::CatmullRom, CurveType::Bezier}) {
                REQUIRE(length(evaluate_curve_tangent(points, type, false, t)) == Catch::Approx(1.0f).margin(1e-4));
            }
        }
    }

    SECTION("tessellation counts") {
        REQUIRE(tessellate_curve(line, CurveType::Linear, false, 10).size() == 11);
        const std::vector<Vec3> square{{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}};
        REQUIRE(tessellate_curve(square, CurveType::Linear, true, 8).size() == 8);
        REQUIRE(tessellate_curve(square, CurveType::Linear, false, 1).size() == 2);
        REQUIRE(tessellate_curve({}, CurveType::Linear, false, 4).empty());
        REQUIRE(tessellate_curve({Vec3{1, 2, 3}}, CurveType::Linear, false, 4).size() == 1);
    }

    SECTION("polyline length") {
        REQUIRE(polyline_length(tessellate_curve(line, CurveType::Linear, false, 10)) ==
                Catch::Approx(3.0f).margin(1e-4));
        REQUIRE(polyline_length({}) == 0.0f);
        REQUIRE(polyline_length({Vec3{1, 1, 1}}) == 0.0f);
        // A closed unit square tessellated on its corners is 3 sides long as a
        // polyline (the closing segment is implicit).
        const std::vector<Vec3> square{{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}};
        REQUIRE(polyline_length(tessellate_curve(square, CurveType::Linear, true, 4)) ==
                Catch::Approx(3.0f).margin(1e-4));
    }
}

// ---------------------------------------------------------------------------
// seeded organic primitives (crystal / rock / shard)
// ---------------------------------------------------------------------------

namespace {

// A faceted mesh duplicates its vertices per triangle, so every triangle owns
// three consecutive indices and all three normals are the face normal.
void require_flat_shaded(const MeshData& mesh) {
    REQUIRE(mesh.positions.size() == mesh.indices.size());
    for (size_t t = 0; t + 2 < mesh.indices.size(); t += 3) {
        const Vec3 a = mesh.positions[mesh.indices[t]];
        const Vec3 b = mesh.positions[mesh.indices[t + 1]];
        const Vec3 c = mesh.positions[mesh.indices[t + 2]];
        const Vec3 face = cross(b - a, c - a);
        if (length(face) < 1e-9f) continue;
        const Vec3 n = normalize(face);
        for (int k = 0; k < 3; ++k)
            REQUIRE(dot(mesh.normals[mesh.indices[t + static_cast<size_t>(k)]], n) == Catch::Approx(1.0f).margin(1e-4));
    }
}

bool same_geometry(const MeshData& a, const MeshData& b) {
    return a.positions == b.positions && a.normals == b.normals && a.indices == b.indices;
}

// Bounds contract: x,z inside [-radius, radius], y inside [-height/2, height/2].
void require_within(const MeshData& mesh, float radius, float height) {
    const Bounds bounds = mesh.bounds();
    REQUIRE(bounds.valid());
    REQUIRE(std::max(std::fabs(bounds.min.x), std::fabs(bounds.max.x)) <= radius + 1e-4f);
    REQUIRE(std::max(std::fabs(bounds.min.z), std::fabs(bounds.max.z)) <= radius + 1e-4f);
    REQUIRE(std::max(std::fabs(bounds.min.y), std::fabs(bounds.max.y)) <= height * 0.5f + 1e-4f);
}

}  // namespace

TEST_CASE("crystal is a valid faceted spike", "[procedural][mesh][crystal]") {
    const MeshData crystal = make_crystal(0.3f, 1.2f, 6, 0.5f, 11u);
    require_valid(crystal);
    require_flat_shaded(crystal);
    require_within(crystal, 0.3f, 1.2f);

    // The apex is the single highest vertex and the base sits at -height/2.
    const Bounds bounds = crystal.bounds();
    REQUIRE(bounds.min.y == Catch::Approx(-0.6f).margin(1e-4));
    REQUIRE(bounds.max.y > 0.0f);

    SECTION("a regular crystal has exactly one spike") {
        // irregularity 0 never grows the twin spike: slices sides + slices cap triangles.
        const MeshData regular = make_crystal(0.5f, 1.0f, 6, 0.0f, 7u);
        require_valid(regular);
        REQUIRE(regular.triangle_count() == 12);
        REQUIRE(regular.bounds().max.y == Catch::Approx(0.5f).margin(1e-5));
        // and the base ring is a regular hexagon of exactly `radius`
        for (const Vec3& p : regular.positions) {
            if (p.y > -0.4f) continue;
            const float r = std::sqrt(p.x * p.x + p.z * p.z);
            REQUIRE((r < 1e-5f || r == Catch::Approx(0.5f).margin(1e-5)));
        }
    }

    SECTION("irregularity 1 always fuses a twin spike") {
        const MeshData twinned = make_crystal(0.5f, 1.0f, 6, 1.0f, 7u);
        require_valid(twinned);
        REQUIRE(twinned.triangle_count() > 12);
    }
}

TEST_CASE("rock is a valid faceted boulder", "[procedural][mesh][rock]") {
    const MeshData low = make_rock(0.4f, 6, 0.6f, 5u);
    require_valid(low);
    require_flat_shaded(low);
    require_within(low, 0.4f, 0.8f);
    REQUIRE(low.triangle_count() == 20);  // bare icosahedron

    const MeshData high = make_rock(0.4f, 24, 0.6f, 5u);
    require_valid(high);
    REQUIRE(high.triangle_count() == 80);  // one subdivision

    SECTION("irregularity 0 is a plain icosphere") {
        const MeshData ideal = make_rock(1.0f, 24, 0.0f, 3u);
        require_valid(ideal);
        for (const Vec3& p : ideal.positions) REQUIRE(length(p) == Catch::Approx(1.0f).margin(1e-4));
    }

    SECTION("irregularity moves the surface off the sphere") {
        const MeshData bumpy = make_rock(1.0f, 24, 1.0f, 3u);
        float worst = 0.0f;
        for (const Vec3& p : bumpy.positions) worst = std::max(worst, std::fabs(length(p) - 1.0f));
        REQUIRE(worst > 0.05f);
    }
}

TEST_CASE("shard is a valid thin flake", "[procedural][mesh][shard]") {
    const MeshData shard = make_shard(0.25f, 0.9f, 0.4f, 21u);
    require_valid(shard);
    require_flat_shaded(shard);
    require_within(shard, 0.25f, 0.9f);

    // Thin: the whole flake lives inside +/- 0.06 * radius on Z.
    const Bounds bounds = shard.bounds();
    REQUIRE(std::max(std::fabs(bounds.min.z), std::fabs(bounds.max.z)) <= 0.06f * 0.25f + 1e-4f);
    // Sharp end: the topmost vertex is far above the widest part of the outline.
    REQUIRE(bounds.max.y > 0.25f * 0.9f);
}

TEST_CASE("the seeded primitives are pure functions of (parameters, seed)", "[procedural][mesh][crystal][rock][shard]") {
    for (uint32_t seed : {0u, 1u, 17u, 4242u}) {
        REQUIRE(same_geometry(make_crystal(0.3f, 1.0f, 6, 0.5f, seed), make_crystal(0.3f, 1.0f, 6, 0.5f, seed)));
        REQUIRE(same_geometry(make_rock(0.3f, 12, 0.5f, seed), make_rock(0.3f, 12, 0.5f, seed)));
        REQUIRE(same_geometry(make_shard(0.3f, 1.0f, 0.5f, seed), make_shard(0.3f, 1.0f, 0.5f, seed)));
    }

    // Different seeds must give visibly different meshes, not just reordered ones.
    for (uint32_t seed = 1; seed <= 8; ++seed) {
        REQUIRE(!same_geometry(make_crystal(0.3f, 1.0f, 6, 0.5f, seed), make_crystal(0.3f, 1.0f, 6, 0.5f, seed + 1)));
        REQUIRE(!same_geometry(make_rock(0.3f, 12, 0.5f, seed), make_rock(0.3f, 12, 0.5f, seed + 1)));
        REQUIRE(!same_geometry(make_shard(0.3f, 1.0f, 0.5f, seed), make_shard(0.3f, 1.0f, 0.5f, seed + 1)));
    }

    // "Visibly different" means the vertices actually move, not that a float
    // wobbled in the last bit.
    const MeshData a = make_rock(1.0f, 12, 0.7f, 1u);
    const MeshData b = make_rock(1.0f, 12, 0.7f, 2u);
    REQUIRE(a.positions.size() == b.positions.size());
    float worst = 0.0f;
    for (size_t i = 0; i < a.positions.size(); ++i) worst = std::max(worst, distance(a.positions[i], b.positions[i]));
    REQUIRE(worst > 0.05f);
}

TEST_CASE("the seeded primitives survive degenerate parameters", "[procedural][mesh]") {
    for (int segments : {0, 3, 5, 8, 64}) {
        require_valid(make_crystal(0.5f, 1.0f, segments, 0.35f, 2u));
        require_valid(make_rock(0.5f, segments, 0.35f, 2u));
    }
    // Clamped irregularity and a zero height must not produce NaNs or garbage.
    for (float irregularity : {-1.0f, 0.0f, 1.0f, 3.0f}) {
        const MeshData crystal = make_crystal(0.5f, 1.0f, 6, irregularity, 9u);
        require_valid(crystal);
        require_within(crystal, 0.5f, 1.0f);
        const MeshData shard = make_shard(0.5f, 1.0f, irregularity, 9u);
        require_valid(shard);
        require_within(shard, 0.5f, 1.0f);
    }
}
