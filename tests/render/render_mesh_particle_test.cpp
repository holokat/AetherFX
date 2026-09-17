// Mesh particles: the per-particle orientation quaternion, the per-axis mesh
// scale and the seeded "<mesh_id>#k" variant lookup (docs/VOCABULARY.md,
// "Mesh particle orientation").
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <string>
#include <vector>

#include "aether/render/renderer.hpp"

using namespace aether;
using namespace aether::render;
using Catch::Approx;

namespace {

RenderSettings flat_settings(int size) {
    RenderSettings s;
    s.width = size;
    s.height = size;
    s.background = Color{0.0f, 0.0f, 0.0f, 0.0f};
    s.ground_plane = false;
    s.grid = false;
    s.bloom = false;
    s.soft_particles = false;
    s.exposure = 1.0f;
    s.supersample = 1;
    return s;
}

CameraDesc front_camera() {
    CameraDesc cam;
    cam.position = Vec3{0.0f, 0.0f, 5.0f};
    cam.target = Vec3{0.0f, 0.0f, 0.0f};
    cam.up = Vec3{0.0f, 1.0f, 0.0f};
    cam.fov_deg = 45.0f;
    return cam;
}

MaterialDesc unlit_additive(const std::string& id) {
    MaterialDesc m;
    m.id = id;
    m.blend = BlendMode::Additive;
    m.shading = Shading::Unlit;
    m.soft_particle = false;
    m.base_color = Color{1.0f, 1.0f, 1.0f, 1.0f};
    m.opacity = 1.0f;
    m.emissive_intensity = 0.0f;
    return m;
}

// A quad in the XY plane facing +Z, so it stays visible however it is spun
// about Z and no back-face question arises.
MeshData flat_quad(float half_width, float half_height) {
    MeshData m;
    m.positions = {{-half_width, -half_height, 0.0f},
                   {half_width, -half_height, 0.0f},
                   {half_width, half_height, 0.0f},
                   {-half_width, half_height, 0.0f}};
    m.normals = {{0, 0, 1}, {0, 0, 1}, {0, 0, 1}, {0, 0, 1}};
    m.uvs = {{0, 1}, {1, 1}, {1, 0}, {0, 0}};
    m.indices = {0, 1, 2, 0, 2, 3};
    return m;
}

void push_mesh_particle(ParticleBuffer& pb, Vec3 position, float size, Vec4 orientation, Vec3 scale3,
                        uint32_t variant) {
    pb.position.push_back(position);
    pb.previous_position.push_back(position);
    pb.velocity.push_back(Vec3{});
    pb.acceleration.push_back(Vec3{});
    pb.age.push_back(0.0f);
    pb.lifetime.push_back(1.0f);
    pb.size.push_back(size);
    pb.rotation.push_back(0.0f);
    pb.angular_velocity.push_back(0.0f);
    pb.color.push_back(Color::white());
    pb.opacity.push_back(1.0f);
    pb.emissive.push_back(0.0f);
    pb.mass.push_back(1.0f);
    pb.custom0.push_back(0.0f);
    pb.custom1.push_back(0.0f);
    pb.seed.push_back(static_cast<uint32_t>(pb.position.size()));
    pb.orientation.push_back(orientation);
    pb.scale3.push_back(scale3);
    pb.variant.push_back(variant);
}

Vec4 quat_axis_angle(Vec3 axis, float radians) {
    const Vec3 a = normalize(axis);
    const float s = std::sin(radians * 0.5f);
    return {a.x * s, a.y * s, a.z * s, std::cos(radians * 0.5f)};
}

constexpr Vec4 kIdentity{0.0f, 0.0f, 0.0f, 1.0f};

bool is_lit(const Image& img, int x, int y) {
    const size_t i = (static_cast<size_t>(y) * static_cast<size_t>(img.width) + static_cast<size_t>(x)) * 4;
    return Color{img.rgba[i], img.rgba[i + 1], img.rgba[i + 2], 1.0f}.luminance() > 0.01f;
}

int lit_pixels(const Image& img) {
    int n = 0;
    for (int y = 0; y < img.height; ++y)
        for (int x = 0; x < img.width; ++x)
            if (is_lit(img, x, y)) ++n;
    return n;
}

int lit_rows(const Image& img) {
    int n = 0;
    for (int y = 0; y < img.height; ++y)
        for (int x = 0; x < img.width; ++x)
            if (is_lit(img, x, y)) {
                ++n;
                break;
            }
    return n;
}

int lit_cols(const Image& img) {
    int n = 0;
    for (int x = 0; x < img.width; ++x)
        for (int y = 0; y < img.height; ++y)
            if (is_lit(img, x, y)) {
                ++n;
                break;
            }
    return n;
}

// One mesh particle at the origin, rendered on its own.
Image render_one(const ResourceSet& resources, const std::string& mesh_id, Vec4 orientation,
                 Vec3 scale3 = Vec3::one(), uint32_t variant = 0u, float size = 1.0f) {
    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "add";
    pb.blend = BlendMode::Additive;
    pb.render_mode = RenderMode::Mesh;
    pb.mesh_id = mesh_id;
    pb.soft_particle_distance = 0.0f;
    push_mesh_particle(pb, Vec3{}, size, orientation, scale3, variant);
    fs.particles.push_back(pb);
    return create_software_renderer()->render(fs, resources, front_camera(), flat_settings(128));
}

ResourceSet quad_resources() {
    ResourceSet res;
    res.meshes["bar"] = flat_quad(0.9f, 0.15f);  // wide and short
    res.materials["add"] = unlit_additive("add");
    return res;
}

}  // namespace

TEST_CASE("mesh particles render differently for different orientations", "[render][mesh][orientation]") {
    const ResourceSet res = quad_resources();

    const Image upright = render_one(res, "bar", kIdentity);
    const Image rolled = render_one(res, "bar", quat_axis_angle(Vec3{0, 0, 1}, kHalfPi));

    REQUIRE(lit_pixels(upright) > 0);
    CHECK(upright.hash() != rolled.hash());
    // A wide, short bar spun a quarter turn about the view axis becomes tall and narrow.
    CHECK(lit_cols(upright) > lit_rows(upright));
    CHECK(lit_rows(rolled) > lit_cols(rolled));
    CHECK(lit_cols(rolled) == Approx(static_cast<float>(lit_rows(upright))).margin(2.0));
    CHECK(lit_pixels(rolled) == Approx(static_cast<float>(lit_pixels(upright))).margin(24.0));

    SECTION("a rotation about the view axis is not the same as one about the up axis") {
        const Image tipped = render_one(res, "bar", quat_axis_angle(Vec3{1, 0, 0}, kHalfPi));
        CHECK(tipped.hash() != upright.hash());
        CHECK(lit_pixels(tipped) < lit_pixels(upright));  // seen edge-on
    }

    SECTION("the identity quaternion reproduces the unrotated transform") {
        // An empty orientation array (a FrameState from before mesh particles
        // could be oriented) must render exactly like an explicit identity.
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "add";
        pb.blend = BlendMode::Additive;
        pb.render_mode = RenderMode::Mesh;
        pb.mesh_id = "bar";
        pb.soft_particle_distance = 0.0f;
        push_mesh_particle(pb, Vec3{}, 1.0f, kIdentity, Vec3::one(), 0u);
        pb.orientation.clear();
        pb.scale3.clear();
        pb.variant.clear();
        fs.particles.push_back(pb);
        const Image legacy = create_software_renderer()->render(fs, res, front_camera(), flat_settings(128));
        CHECK(legacy.hash() == upright.hash());
    }

    SECTION("orientation is deterministic") {
        const Vec4 q = quat_axis_angle(Vec3{0.3f, 0.8f, -0.5f}, 1.1f);
        CHECK(render_one(res, "bar", q).hash() == render_one(res, "bar", q).hash());
    }
}

TEST_CASE("per-axis mesh scale stretches the instance", "[render][mesh][orientation]") {
    const ResourceSet res = quad_resources();
    const Image plain = render_one(res, "bar", kIdentity);
    const Image tall = render_one(res, "bar", kIdentity, Vec3{1.0f, 3.0f, 1.0f});
    const Image thin = render_one(res, "bar", kIdentity, Vec3{0.25f, 1.0f, 1.0f});

    CHECK(lit_rows(tall) > lit_rows(plain));
    CHECK(lit_cols(tall) == Approx(static_cast<float>(lit_cols(plain))).margin(1.0));
    CHECK(lit_cols(thin) < lit_cols(plain));
    CHECK(lit_rows(thin) == Approx(static_cast<float>(lit_rows(plain))).margin(1.0));
    CHECK(lit_pixels(tall) > lit_pixels(plain));
    CHECK(lit_pixels(thin) < lit_pixels(plain));

    SECTION("a large scale is not culled off-screen") {
        // The conservative on-screen test has to account for mesh_scale.
        const Image huge = render_one(res, "bar", kIdentity, Vec3{1.0f, 8.0f, 1.0f}, 0u, 0.4f);
        CHECK(lit_pixels(huge) > 0);
    }

    SECTION("scale is applied in mesh space, so the stretch turns with the mesh") {
        // transform = translate * rotate * scale: scaling local Y fattens the
        // bar across its short axis, and a quarter roll then lays that fattening
        // across the screen's X instead of its Y.
        const Vec4 roll = quat_axis_angle(Vec3{0, 0, 1}, kHalfPi);
        const Image rolled = render_one(res, "bar", roll);
        const Image rolled_tall = render_one(res, "bar", roll, Vec3{1.0f, 3.0f, 1.0f});
        CHECK(lit_cols(rolled_tall) > lit_cols(rolled) * 2);
        CHECK(lit_rows(rolled_tall) == Approx(static_cast<float>(lit_rows(rolled))).margin(1.0));
        // ...whereas in screen space it is still the taller-than-wide silhouette.
        CHECK(lit_rows(rolled_tall) > lit_cols(rolled_tall));
    }
}

TEST_CASE("mesh particles look up baked seeded variants", "[render][mesh][variants]") {
    ResourceSet res;
    res.materials["add"] = unlit_additive("add");
    res.meshes["vm"] = flat_quad(0.2f, 0.2f);           // variant 0: small
    res.meshes["vm#1"] = flat_quad(0.45f, 0.45f);       // variant 1: medium
    res.meshes["vm#2"] = flat_quad(0.8f, 0.8f);         // variant 2: large

    const Image small = render_one(res, "vm", kIdentity, Vec3::one(), 0u);
    const Image medium = render_one(res, "vm", kIdentity, Vec3::one(), 1u);
    const Image large = render_one(res, "vm", kIdentity, Vec3::one(), 2u);

    REQUIRE(lit_pixels(small) > 0);
    CHECK(lit_pixels(medium) > lit_pixels(small));
    CHECK(lit_pixels(large) > lit_pixels(medium));
    CHECK(small.hash() != medium.hash());
    CHECK(medium.hash() != large.hash());

    SECTION("a missing variant falls back to variant 0") {
        // "vm#3" was never baked; index 3 and index 42 both draw the base mesh.
        CHECK(render_one(res, "vm", kIdentity, Vec3::one(), 3u).hash() == small.hash());
        CHECK(render_one(res, "vm", kIdentity, Vec3::one(), 42u).hash() == small.hash());
    }

    SECTION("variants are only probed up to the first gap") {
        // "vm#4" exists but "vm#3" does not, so the run stops at 2 and index 4
        // falls back to variant 0 rather than picking up the stray mesh.
        ResourceSet gapped = res;
        gapped.meshes["vm#4"] = flat_quad(1.5f, 1.5f);
        CHECK(render_one(gapped, "vm", kIdentity, Vec3::one(), 4u).hash() == small.hash());
        CHECK(render_one(gapped, "vm", kIdentity, Vec3::one(), 2u).hash() == large.hash());
    }

    SECTION("one buffer can mix variants") {
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "add";
        pb.blend = BlendMode::Additive;
        pb.render_mode = RenderMode::Mesh;
        pb.mesh_id = "vm";
        pb.soft_particle_distance = 0.0f;
        push_mesh_particle(pb, Vec3{-1.2f, 0.0f, 0.0f}, 1.0f, kIdentity, Vec3::one(), 0u);
        push_mesh_particle(pb, Vec3{0.0f, 0.0f, 0.0f}, 1.0f, kIdentity, Vec3::one(), 1u);
        push_mesh_particle(pb, Vec3{1.2f, 0.0f, 0.0f}, 1.0f, kIdentity, Vec3::one(), 2u);
        fs.particles.push_back(pb);
        const Image mixed = create_software_renderer()->render(fs, res, front_camera(), flat_settings(128));

        // Three blobs of clearly different widths, left to right.
        int left = 0, centre = 0, right = 0;
        for (int y = 0; y < mixed.height; ++y)
            for (int x = 0; x < mixed.width; ++x) {
                if (!is_lit(mixed, x, y)) continue;
                if (x < mixed.width / 3) ++left;
                else if (x < 2 * mixed.width / 3) ++centre;
                else ++right;
            }
        CHECK(left > 0);
        CHECK(centre > left);
        CHECK(right > centre);
    }

    SECTION("an unknown base mesh is still skipped") {
        const Image nothing = render_one(res, "not_baked", kIdentity, Vec3::one(), 1u);
        CHECK(lit_pixels(nothing) == 0);
    }
}
