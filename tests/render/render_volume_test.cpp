// The procedural volume pass (docs/VOLUMES.md): what it draws, where it draws
// nothing, that it is deterministic and that it respects the opaque depth buffer.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <string>
#include <vector>

#include "aether/render/renderer.hpp"
// White-box: volume_field.hpp is render-private, so the test reaches into the
// module's own sources rather than through its public headers.
#include "../../src/render/src/volume_field.hpp"

using namespace aether;
using namespace aether::render;
using Catch::Approx;

namespace {

RenderSettings plain_settings(int size) {
    RenderSettings s;
    s.width = size;
    s.height = size;
    s.background = Color{0.0f, 0.0f, 0.0f, 0.0f};
    s.ground_plane = false;
    s.grid = false;
    s.bloom = false;
    s.soft_particles = false;
    s.exposure = 1.0f;
    s.tonemap = false;
    s.supersample = 1;
    return s;
}

CameraDesc front_camera() {
    CameraDesc cam;
    cam.position = Vec3{0.0f, 0.0f, 6.0f};
    cam.target = Vec3{0.0f, 0.0f, 0.0f};
    cam.up = Vec3{0.0f, 1.0f, 0.0f};
    cam.fov_deg = 45.0f;
    return cam;
}

// A dense, unmistakable sphere at the origin: no carve, no arms, so the whole
// bounding shape is filled and the maths under test is the marcher, not the noise.
VolumeState solid_sphere() {
    VolumeState v;
    v.id = "cloud";
    v.mode = "procedural";
    v.shape = "sphere";
    v.backend = "procedural_volume";
    v.radius = 1.0f;
    v.height = 2.0f;
    v.density = 4.0f;
    v.emission = 1.0f;
    v.color = Color{1.0f, 0.0f, 0.0f, 1.0f};
    v.color_hot = Color{1.0f, 0.0f, 0.0f, 1.0f};
    v.filament_scale = 1.0f;
    v.strands = 0.0f;
    v.carve = 0.0f;     // keep every sample
    v.softness = 0.2f;
    v.spiral_arms = 0;
    v.scatter = 0.0f;   // emission only: no light rig needed
    v.march_steps = 32;
    v.transform = Mat4::identity();
    v.seed = 5u;
    v.time = 0.0f;
    v.bounds_min = Vec3{-1, -1, -1};
    v.bounds_max = Vec3{1, 1, 1};
    return v;
}

float luminance_of(const Color& c) { return c.r * 0.2126f + c.g * 0.7152f + c.b * 0.0722f; }

}  // namespace

TEST_CASE("a procedural volume covers its bounds and nothing else", "[render][volume]") {
    FrameState fs;
    fs.volumes.push_back(solid_sphere());

    ResourceSet res;
    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(128));

    // The sphere has radius 1 at 6 m through a 45 degree fov: about 26 px of a
    // 128 px frame, so the centre is inside it and the corners are far outside.
    CHECK(luminance_of(img.get(64, 64)) > 0.05f);
    for (const std::pair<int, int>& corner : {std::pair<int, int>{1, 1}, {126, 1}, {1, 126}, {126, 126}}) {
        CAPTURE(corner.first, corner.second);
        const Color c = img.get(corner.first, corner.second);
        CHECK(c.r == Approx(0.0f).margin(1e-6));
        CHECK(c.g == Approx(0.0f).margin(1e-6));
        CHECK(c.b == Approx(0.0f).margin(1e-6));
        CHECK(c.a == Approx(0.0f).margin(1e-6));
    }
    // The tint is honoured: a red volume never writes green or blue.
    const Color centre = img.get(64, 64);
    CHECK(centre.r > 0.05f);
    CHECK(centre.g == Approx(0.0f).margin(1e-6));
    CHECK(centre.a > 0.05f);
}

TEST_CASE("volume rendering is deterministic", "[render][volume]") {
    FrameState fs;
    fs.volumes.push_back(solid_sphere());
    fs.volumes.front().spiral_arms = 3;   // exercise every branch of the field
    fs.volumes.front().carve = 0.35f;
    fs.volumes.front().strands = 0.7f;
    fs.volumes.front().spin = 0.5f;
    fs.volumes.front().twist = 0.4f;
    fs.volumes.front().climb = 0.3f;
    fs.volumes.front().time = 0.75f;

    ResourceSet res;
    auto a = create_software_renderer();
    auto b = create_software_renderer();
    const Image first = a->render(fs, res, front_camera(), plain_settings(96));
    const Image second = b->render(fs, res, front_camera(), plain_settings(96));
    CHECK(first.hash() == second.hash());
    // ... and it actually drew something, or the comparison proves nothing.
    double sum = 0.0;
    for (int y = 0; y < first.height; ++y)
        for (int x = 0; x < first.width; ++x) sum += first.get(x, y).a;
    CHECK(sum > 1.0);
}

TEST_CASE("carve = 1 removes the whole field", "[render][volume]") {
    FrameState fs;
    VolumeState v = solid_sphere();
    v.carve = 1.0f;  // smoothstep(1, 1.25, noise) is 0 for any noise in [0, 1]
    fs.volumes.push_back(v);

    ResourceSet res;
    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(96));
    for (int y = 0; y < img.height; ++y) {
        for (int x = 0; x < img.width; ++x) {
            const Color c = img.get(x, y);
            REQUIRE(c.r == Approx(0.0f).margin(1e-6));
            REQUIRE(c.a == Approx(0.0f).margin(1e-6));
        }
    }
}

TEST_CASE("a simulation-mode volume still draws nothing", "[render][volume]") {
    FrameState fs;
    VolumeState v = solid_sphere();
    v.mode = "simulation";
    v.backend = "volume_stub";
    fs.volumes.push_back(v);

    ResourceSet res;
    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(64));
    for (int y = 0; y < img.height; ++y)
        for (int x = 0; x < img.width; ++x) REQUIRE(img.get(x, y).a == Approx(0.0f).margin(1e-6));
}

TEST_CASE("an opaque mesh in front of a volume cuts the march", "[render][volume]") {
    // A unit quad at z = 3 (between the camera at z = 6 and the volume at the
    // origin) must hide the volume behind it.
    MeshData quad;
    quad.positions = {{-3.0f, -3.0f, 3.0f}, {3.0f, -3.0f, 3.0f}, {3.0f, 3.0f, 3.0f}, {-3.0f, 3.0f, 3.0f}};
    quad.normals = {{0, 0, 1}, {0, 0, 1}, {0, 0, 1}, {0, 0, 1}};
    quad.uvs = {{0, 0}, {1, 0}, {1, 1}, {0, 1}};
    quad.indices = {0, 1, 2, 0, 2, 3};

    ResourceSet res;
    res.meshes["wall"] = quad;
    MaterialDesc opaque;
    opaque.id = "wall_mat";
    opaque.blend = BlendMode::Alpha;
    opaque.shading = Shading::Unlit;
    opaque.base_color = Color{0.0f, 0.0f, 1.0f, 1.0f};
    opaque.opacity = 1.0f;
    res.materials["wall_mat"] = opaque;

    FrameState fs;
    fs.volumes.push_back(solid_sphere());
    MeshInstanceState wall;
    wall.id = "wall";
    wall.mesh_id = "wall";
    wall.material_id = "wall_mat";
    wall.transform = Mat4::identity();
    fs.meshes.push_back(wall);

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(96));
    const Color centre = img.get(48, 48);
    // Pure wall: the red emission of the volume never reaches the frame.
    CHECK(centre.b > 0.1f);
    CHECK(centre.r == Approx(0.0f).margin(1e-5));
}

TEST_CASE("the volume transform places and scales the field", "[render][volume]") {
    // Two frames of the same volume, one shifted left by 1.8 m: the centre pixel
    // lights up in the first and is empty in the second, and the sphere shows up
    // where the projection says it should (px 13 of 96 at this framing).
    ResourceSet res;
    auto renderer = create_software_renderer();

    FrameState centred;
    centred.volumes.push_back(solid_sphere());
    const Image a = renderer->render(centred, res, front_camera(), plain_settings(96));

    FrameState shifted;
    VolumeState moved = solid_sphere();
    moved.transform = Mat4::translation(Vec3{-1.8f, 0.0f, 0.0f});
    shifted.volumes.push_back(moved);
    const Image b = renderer->render(shifted, res, front_camera(), plain_settings(96));

    CHECK(a.get(48, 48).a > 0.05f);
    CHECK(b.get(48, 48).a == Approx(0.0f).margin(1e-6));
    CHECK(b.get(13, 48).a > 0.05f);   // it moved, it did not disappear
}

TEST_CASE("volume_density is the documented field", "[render][volume][field]") {
    VolumeState v = solid_sphere();
    v.softness = 0.0f;   // a hard edge, so "inside" and "outside" are exact

    SECTION("the field is zero outside the shape and positive inside") {
        CHECK(volume_density(v, Vec3{0.0f, 0.0f, 0.0f}, 0.0f) > 0.0f);
        CHECK(volume_density(v, Vec3{5.0f, 0.0f, 0.0f}, 0.0f) == Approx(0.0f).margin(1e-8));
        CHECK(volume_density(v, Vec3{0.0f, 1.01f, 0.0f}, 0.0f) == Approx(0.0f).margin(1e-8));
    }

    SECTION("it follows the transform") {
        VolumeState moved = v;
        moved.transform = Mat4::translation(Vec3{10.0f, 0.0f, 0.0f});
        CHECK(volume_density(moved, Vec3{10.0f, 0.0f, 0.0f}, 0.0f) ==
              Approx(volume_density(v, Vec3{0.0f, 0.0f, 0.0f}, 0.0f)));
        CHECK(volume_density(moved, Vec3{0.0f, 0.0f, 0.0f}, 0.0f) == Approx(0.0f).margin(1e-8));
    }

    SECTION("density scales the field and carve = 1 empties it") {
        const float base = volume_density(v, Vec3{0.2f, 0.1f, -0.3f}, 0.0f);
        REQUIRE(base > 0.0f);
        VolumeState twice = v;
        twice.density *= 2.0f;
        CHECK(volume_density(twice, Vec3{0.2f, 0.1f, -0.3f}, 0.0f) == Approx(base * 2.0f).margin(1e-4));
        VolumeState carved = v;
        carved.carve = 1.0f;
        CHECK(volume_density(carved, Vec3{0.2f, 0.1f, -0.3f}, 0.0f) == Approx(0.0f).margin(1e-8));
    }

    SECTION("spin and climb move the field over time") {
        VolumeState moving = v;
        moving.spin = 0.25f;
        moving.climb = 0.5f;
        moving.carve = 0.5f;   // sit on the threshold, where the noise is visible at all
        const Vec3 probes[]{{0.35f, 0.1f, -0.2f}, {-0.4f, 0.3f, 0.15f}, {0.1f, -0.25f, 0.45f}};
        bool moved = false;
        for (const Vec3& p : probes)
            if (volume_density(moving, p, 0.0f) != Approx(volume_density(moving, p, 0.9f))) moved = true;
        CHECK(moved);
        // A static field ignores time completely.
        VolumeState still = moving;
        still.spin = 0.0f;
        still.climb = 0.0f;
        for (const Vec3& p : probes)
            CHECK(volume_density(still, p, 0.0f) == Approx(volume_density(still, p, 9.0f)));
    }

    SECTION("the tint runs from color to color_hot") {
        VolumeState tinted = v;
        tinted.color = Color{1.0f, 0.0f, 0.0f, 1.0f};
        tinted.color_hot = Color{0.0f, 0.0f, 1.0f, 1.0f};
        CHECK(volume_tint(tinted, 0.0f).x == Approx(1.0f));
        CHECK(volume_tint(tinted, 0.25f).x == Approx(0.5f));   // saturate(d * 2) = 0.5
        CHECK(volume_tint(tinted, 1.0f).z == Approx(1.0f));
    }

    SECTION("every shape is bounded by volume_shape_extent") {
        for (const std::string shape : {"sphere", "column", "disc", "ring", "nebula", "cone"}) {
            CAPTURE(shape);
            VolumeState s = v;
            s.shape = shape;
            s.radius = 1.0f;
            s.height = 2.0f;
            const Vec3 extent = volume_shape_extent(shape, s.radius, s.height);
            REQUIRE(extent.x > 0.0f);
            // Just outside the box on each axis, the field must be exactly zero.
            const Vec3 outside[]{{extent.x * 1.01f + 0.01f, 0.0f, 0.0f},
                                 {0.0f, extent.y * 1.01f + 0.01f, 0.0f},
                                 {0.0f, 0.0f, extent.z * 1.01f + 0.01f}};
            for (const Vec3& p : outside) CHECK(volume_density(s, p, 0.0f) == Approx(0.0f).margin(1e-8));
        }
    }
}
