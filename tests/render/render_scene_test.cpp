// Full-scene software renderer tests: a hand-built FrameState exercising every primitive kind,
// plus determinism and the PNG/EXR round trips.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

#include "aether/render/image_io.hpp"
#include "aether/render/renderer.hpp"

using namespace aether;
using namespace aether::render;
using Catch::Approx;

namespace {

std::filesystem::path output_dir() {
    std::filesystem::path dir(AETHER_TEST_OUTPUT_DIR);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

// Tiny deterministic LCG so the scene is identical on every platform and run.
struct Lcg {
    uint32_t s = 12345u;
    uint32_t next() {
        s = s * 1664525u + 1013904223u;
        return s;
    }
    float unit() { return static_cast<float>(next() >> 8) / 16777216.0f; }
    float range(float a, float b) { return a + (b - a) * unit(); }
};

MeshData make_unit_cube() {
    MeshData m;
    const Vec3 faces[6][4] = {
        {{-0.5f, -0.5f, 0.5f}, {0.5f, -0.5f, 0.5f}, {0.5f, 0.5f, 0.5f}, {-0.5f, 0.5f, 0.5f}},      // +Z
        {{0.5f, -0.5f, -0.5f}, {-0.5f, -0.5f, -0.5f}, {-0.5f, 0.5f, -0.5f}, {0.5f, 0.5f, -0.5f}},  // -Z
        {{0.5f, -0.5f, 0.5f}, {0.5f, -0.5f, -0.5f}, {0.5f, 0.5f, -0.5f}, {0.5f, 0.5f, 0.5f}},      // +X
        {{-0.5f, -0.5f, -0.5f}, {-0.5f, -0.5f, 0.5f}, {-0.5f, 0.5f, 0.5f}, {-0.5f, 0.5f, -0.5f}},  // -X
        {{-0.5f, 0.5f, 0.5f}, {0.5f, 0.5f, 0.5f}, {0.5f, 0.5f, -0.5f}, {-0.5f, 0.5f, -0.5f}},      // +Y
        {{-0.5f, -0.5f, -0.5f}, {0.5f, -0.5f, -0.5f}, {0.5f, -0.5f, 0.5f}, {-0.5f, -0.5f, 0.5f}},  // -Y
    };
    const Vec3 normals[6] = {{0, 0, 1}, {0, 0, -1}, {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}};
    const Vec2 uvs[4] = {{0, 1}, {1, 1}, {1, 0}, {0, 0}};
    for (int f = 0; f < 6; ++f) {
        const uint32_t base = static_cast<uint32_t>(m.positions.size());
        for (int v = 0; v < 4; ++v) {
            m.positions.push_back(faces[f][v]);
            m.normals.push_back(normals[f]);
            m.uvs.push_back(uvs[v]);
        }
        m.indices.insert(m.indices.end(), {base, base + 1, base + 2, base, base + 2, base + 3});
    }
    return m;
}

ResourceSet make_resources() {
    ResourceSet res;
    res.meshes["cube"] = make_unit_cube();

    MaterialDesc fire;
    fire.id = "mat_fire";
    fire.base_color = Color{1.0f, 1.0f, 1.0f, 1.0f};
    fire.blend = BlendMode::Additive;
    fire.shading = Shading::Unlit;
    fire.soft_particle = true;
    fire.temperature_gradient = Gradient{{0.0f, Color{1.0f, 0.25f, 0.06f, 1.0f}},
                                         {0.5f, Color{1.0f, 0.62f, 0.18f, 1.0f}},
                                         {1.0f, Color{1.0f, 0.95f, 0.75f, 1.0f}}};
    res.materials[fire.id] = fire;

    MaterialDesc smoke;
    smoke.id = "mat_smoke";
    smoke.base_color = Color{0.17f, 0.175f, 0.20f, 1.0f};
    smoke.blend = BlendMode::Alpha;
    smoke.shading = Shading::Lit;
    smoke.soft_particle = true;
    smoke.emissive_intensity = 0.0f;
    res.materials[smoke.id] = smoke;

    MaterialDesc metal;
    metal.id = "mat_metal";
    metal.base_color = Color{0.62f, 0.64f, 0.70f, 1.0f};
    metal.blend = BlendMode::Alpha;
    metal.shading = Shading::Lit;
    res.materials[metal.id] = metal;

    // 64x64 RGBA decal texture: a soft ring.
    TextureResource ring;
    ring.image = Image(64, 64, Color::transparent());
    for (int y = 0; y < 64; ++y) {
        for (int x = 0; x < 64; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / 64.0f * 2.0f - 1.0f;
            const float v = (static_cast<float>(y) + 0.5f) / 64.0f * 2.0f - 1.0f;
            const float r = std::sqrt(u * u + v * v);
            const float a = (1.0f - smoothstep(0.85f, 1.0f, r)) * smoothstep(0.45f, 0.72f, r);
            ring.image.set(x, y, Color{1.0f, 0.65f, 0.3f, a});
        }
    }
    res.textures["tex_ring"] = ring;
    return res;
}

FrameState make_scene() {
    FrameState fs;
    fs.time = 1.25;
    fs.frame_index = 75;

    // --- 200 additive fire billboards in a sphere around (0,1,0) ---------------------------
    ParticleBuffer fire;
    fire.system_id = "fire";
    fire.render_mode = RenderMode::Billboard;
    fire.blend = BlendMode::Additive;
    fire.material_id = "mat_fire";
    fire.soft_particle_distance = 0.25f;
    Lcg rng{7u};
    for (int i = 0; i < 200; ++i) {
        const float theta = rng.range(0.0f, kTwoPi);
        const float z = rng.range(-1.0f, 1.0f);
        const float rad = std::sqrt(std::max(0.0f, 1.0f - z * z)) * rng.range(0.14f, 0.52f);
        const Vec3 p{std::cos(theta) * rad, 0.95f + z * 0.5f, std::sin(theta) * rad};
        fire.position.push_back(p);
        fire.previous_position.push_back(p);
        fire.velocity.push_back(Vec3{0.0f, rng.range(0.4f, 1.6f), 0.0f});
        fire.acceleration.push_back(Vec3{});
        fire.age.push_back(rng.range(0.0f, 0.9f));
        fire.lifetime.push_back(1.0f);
        fire.size.push_back(rng.range(0.10f, 0.26f));
        fire.rotation.push_back(rng.range(0.0f, kTwoPi));
        fire.angular_velocity.push_back(0.0f);
        fire.color.push_back(Color{1.0f, rng.range(0.34f, 0.58f), rng.range(0.08f, 0.2f), 1.0f});
        fire.opacity.push_back(rng.range(0.10f, 0.34f));
        fire.emissive.push_back(1.5f);
        fire.mass.push_back(1.0f);
        fire.custom0.push_back(0.0f);
        fire.custom1.push_back(0.0f);
        fire.seed.push_back(static_cast<uint32_t>(i));
    }
    fs.particles.push_back(fire);

    // --- 100 alpha smoke billboards -------------------------------------------------------
    ParticleBuffer smoke;
    smoke.system_id = "smoke";
    smoke.render_mode = RenderMode::Billboard;
    smoke.blend = BlendMode::Alpha;
    smoke.material_id = "mat_smoke";
    smoke.sort = true;
    smoke.soft_particle_distance = 0.4f;
    for (int i = 0; i < 100; ++i) {
        const float theta = rng.range(0.0f, kTwoPi);
        const float rad = rng.range(0.1f, 0.85f);
        const Vec3 p{std::cos(theta) * rad, rng.range(1.25f, 2.7f), std::sin(theta) * rad};
        smoke.position.push_back(p);
        smoke.previous_position.push_back(p);
        smoke.velocity.push_back(Vec3{0.0f, 0.9f, 0.0f});
        smoke.acceleration.push_back(Vec3{});
        smoke.age.push_back(rng.range(0.1f, 0.95f));
        smoke.lifetime.push_back(1.0f);
        smoke.size.push_back(rng.range(0.45f, 1.0f));
        smoke.rotation.push_back(rng.range(0.0f, kTwoPi));
        smoke.angular_velocity.push_back(0.0f);
        smoke.color.push_back(Color{0.8f, 0.82f, 0.9f, 1.0f});
        smoke.opacity.push_back(rng.range(0.03f, 0.09f));
        smoke.emissive.push_back(0.0f);
        smoke.mass.push_back(1.0f);
        smoke.custom0.push_back(0.0f);
        smoke.custom1.push_back(0.0f);
        smoke.seed.push_back(static_cast<uint32_t>(i));
    }
    fs.particles.push_back(smoke);

    // --- a warm point light ---------------------------------------------------------------
    LightState light;
    light.id = "key";
    light.type = LightType::Point;
    light.position = Vec3{0.0f, 1.15f, 0.45f};
    light.color = Color{1.0f, 0.70f, 0.42f, 1.0f};
    light.intensity = 6.0f;
    light.radius = 8.0f;
    fs.lights.push_back(light);

    LightState fill;
    fill.id = "fill";
    fill.type = LightType::Spot;
    fill.position = Vec3{-2.2f, 3.0f, 2.4f};
    fill.direction = normalize(Vec3{0.6f, -1.0f, -0.7f});
    fill.color = Color{0.45f, 0.62f, 1.0f, 1.0f};
    fill.intensity = 9.0f;
    fill.radius = 12.0f;
    fill.cone_angle_deg = 42.0f;
    fs.lights.push_back(fill);

    // --- a beam from (0,3,0) to (0,0,0) with one branch ------------------------------------
    BeamState beam;
    beam.id = "bolt";
    beam.width = 0.05f;
    beam.color = Color{0.60f, 0.78f, 1.0f, 1.0f};
    beam.emissive = 2.2f;
    beam.blend = BlendMode::Additive;
    beam.pulse_phase = 0.35f;
    std::vector<Vec3> main;
    for (int i = 0; i <= 10; ++i) {
        const float t = static_cast<float>(i) / 10.0f;
        const float jitter = (i == 0 || i == 10) ? 0.0f : 0.09f;
        main.push_back(Vec3{rng.range(-jitter, jitter), lerp(3.0f, 0.0f, t), rng.range(-jitter, jitter)});
    }
    beam.polylines.push_back(main);
    std::vector<Vec3> branch;
    branch.push_back(main[4]);
    branch.push_back(main[4] + Vec3{0.35f, -0.28f, 0.12f});
    branch.push_back(main[4] + Vec3{0.62f, -0.62f, 0.05f});
    beam.polylines.push_back(branch);
    fs.beams.push_back(beam);

    // --- a trail ribbon --------------------------------------------------------------------
    TrailState trail;
    trail.id = "spark_trail";
    trail.blend = BlendMode::Additive;
    std::vector<TrailVertex> ribbon;
    for (int i = 0; i <= 24; ++i) {
        const float t = static_cast<float>(i) / 24.0f;
        TrailVertex tv;
        tv.position = Vec3{-1.9f + 3.5f * t, 0.22f + std::sin(t * kPi) * 0.85f, 1.35f - 1.1f * t};
        tv.width = lerp(0.015f, 0.085f, t);  // taper is applied upstream: oldest vertex is thinnest
        tv.age = (1.0f - t) * 0.5f;
        tv.normalized_age = 1.0f - t;
        tv.u = t * 3.0f;
        tv.color = Color{0.35f + 0.55f * t, 0.78f, 1.0f, 1.0f};
        tv.opacity = lerp(0.1f, 1.0f, t);
        tv.emissive = 1.1f * t;
        ribbon.push_back(tv);
    }
    trail.ribbons.push_back(ribbon);
    fs.trails.push_back(trail);

    // --- a mesh instance (unit cube) --------------------------------------------------------
    MeshInstanceState cube;
    cube.id = "anvil";
    cube.mesh_id = "cube";
    cube.material_id = "mat_metal";
    cube.transform = Mat4::trs(Vec3{1.25f, 0.4f, -0.4f}, Vec3{0.0f, 28.0f, 0.0f}, Vec3{0.8f});
    cube.color = Color{1.0f, 1.0f, 1.0f, 1.0f};
    cube.emissive = 0.0f;
    fs.meshes.push_back(cube);

    // --- a scorch decal on the ground --------------------------------------------------------
    DecalState decal;
    decal.id = "scorch";
    decal.position = Vec3{0.0f, 0.0f, 0.0f};
    decal.size = Vec2{3.2f, 3.2f};
    decal.rotation_deg = 17.0f;
    decal.color = Color{1.0f, 0.55f, 0.22f, 1.0f};
    decal.opacity = 0.8f;
    decal.emissive = 0.35f;
    decal.circle = true;
    decal.blend = BlendMode::Alpha;
    decal.texture_id = "tex_ring";
    fs.decals.push_back(decal);

    // --- a heat haze post effect --------------------------------------------------------------
    PostEffectState haze;
    haze.id = "haze";
    haze.post_type = "heat_haze";
    haze.intensity = 0.4f;
    haze.radius = 0.05f;
    haze.frequency = 8.0f;
    haze.time = fs.time;
    fs.post_effects.push_back(haze);

    return fs;
}

CameraDesc hero_camera() {
    CameraDesc cam;
    cam.position = Vec3{2.6f, 1.9f, 4.6f};
    cam.target = Vec3{0.0f, 1.05f, 0.0f};
    cam.fov_deg = 40.0f;
    return cam;
}

RenderSettings hero_settings(int size) {
    RenderSettings s;
    s.width = size;
    s.height = size;
    s.background = Color{0.015f, 0.017f, 0.024f, 1.0f};
    s.ground_plane = true;
    s.grid = true;
    s.ground_albedo = 0.24f;
    s.bloom = true;
    s.bloom_threshold = 1.15f;
    s.bloom_intensity = 0.24f;
    s.bloom_radius = 0.035f;
    s.soft_particles = true;
    return s;
}

float region_mean_luminance(const Image& img, int x0, int y0, int x1, int y1) {
    double sum = 0.0;
    int n = 0;
    for (int y = y0; y < y1; ++y)
        for (int x = x0; x < x1; ++x) {
            sum += static_cast<double>(img.get(x, y).luminance());
            ++n;
        }
    return n > 0 ? static_cast<float>(sum / n) : 0.0f;
}

}  // namespace

TEST_CASE("software renderer draws a full scene", "[render]") {
    const FrameState fs = make_scene();
    const ResourceSet res = make_resources();
    const CameraDesc cam = hero_camera();
    const RenderSettings settings = hero_settings(256);

    auto renderer = create_software_renderer();
    REQUIRE(renderer != nullptr);
    CHECK(renderer->name() == "software");
    const Image img = renderer->render(fs, res, cam, settings);

    REQUIRE(img.width == 256);
    REQUIRE(img.height == 256);

    const float center = region_mean_luminance(img, 96, 96, 160, 160);
    const float corners = 0.25f * (region_mean_luminance(img, 0, 0, 32, 32) +
                                   region_mean_luminance(img, 224, 0, 256, 32) +
                                   region_mean_luminance(img, 0, 224, 32, 256) +
                                   region_mean_luminance(img, 224, 224, 256, 256));
    INFO("center=" << center << " corners=" << corners);
    CHECK(center > corners);

    // Coverage over the background at the centre of the effect. The default background is
    // opaque, so coverage starts at 1; rendering with a transparent background yields a real
    // coverage matte instead.
    const float center_alpha = 0.25f * (img.get(127, 127).a + img.get(128, 127).a + img.get(127, 128).a +
                                        img.get(128, 128).a);
    CHECK(center_alpha > 0.5f);

    RenderSettings matte = settings;
    matte.background = Color{0.0f, 0.0f, 0.0f, 0.0f};
    const Image cutout = renderer->render(fs, res, cam, matte);
    CHECK(cutout.get(128, 128).a > 0.5f);
    CHECK(cutout.get(2, 2).a == Approx(0.0f).margin(1e-5));      // sky: nothing covers it
    CHECK(cutout.get(128, 250).a > 0.9f);                        // ground covers the lower frame

    const RenderStatistics stats = renderer->last_statistics();
    CHECK(stats.particles_submitted == 300);
    CHECK(stats.particles_drawn > 0);
    CHECK(stats.fragments_shaded > 0);
    CHECK(stats.overdraw > 0.0);
    CHECK(stats.render_ms >= 0.0);
}

TEST_CASE("software renderer is deterministic", "[render]") {
    const FrameState fs = make_scene();
    const ResourceSet res = make_resources();
    const CameraDesc cam = hero_camera();
    const RenderSettings settings = hero_settings(160);

    auto a = create_software_renderer();
    auto b = create_software_renderer();
    const Image first = a->render(fs, res, cam, settings);
    const Image second = b->render(fs, res, cam, settings);
    CHECK(first.hash() == second.hash());

    // The same renderer instance re-used must also give the identical result.
    const Image third = a->render(fs, res, cam, settings);
    CHECK(first.hash() == third.hash());
}

TEST_CASE("supersampling produces a smoother but still deterministic image", "[render]") {
    const FrameState fs = make_scene();
    const ResourceSet res = make_resources();
    const CameraDesc cam = hero_camera();
    RenderSettings settings = hero_settings(128);
    settings.supersample = 2;

    auto renderer = create_software_renderer();
    const Image a = renderer->render(fs, res, cam, settings);
    const Image b = renderer->render(fs, res, cam, settings);
    REQUIRE(a.width == 128);
    CHECK(a.hash() == b.hash());
}

TEST_CASE("rendered frames round-trip through PNG and EXR", "[render]") {
    const FrameState fs = make_scene();
    const ResourceSet res = make_resources();
    const CameraDesc cam = hero_camera();
    const RenderSettings settings = hero_settings(128);

    auto renderer = create_software_renderer();
    const Image hdr = renderer->render(fs, res, cam, settings);

    const std::filesystem::path dir = output_dir();
    const std::filesystem::path png_path = dir / "scene.png";
    const std::filesystem::path exr_path = dir / "scene.exr";

    TonemapSettings tm;
    tm.exposure = 1.0f;
    tm.filmic = true;
    tm.srgb = true;
    REQUIRE_NOTHROW(write_png(png_path, hdr, tm));
    REQUIRE_NOTHROW(write_exr(exr_path, hdr));
    REQUIRE(std::filesystem::exists(png_path));
    REQUIRE(std::filesystem::exists(exr_path));

    // EXR is linear half float: values must match the HDR image closely.
    const Image exr = read_image(exr_path);
    REQUIRE(exr.width == hdr.width);
    REQUIRE(exr.height == hdr.height);
    double worst_exr = 0.0;
    for (size_t i = 0; i < hdr.rgba.size(); ++i) {
        const float want = hdr.rgba[i];
        const float got = exr.rgba[i];
        worst_exr = std::max(worst_exr, static_cast<double>(std::fabs(want - got)) /
                                            std::max(1.0, static_cast<double>(std::fabs(want))));
    }
    INFO("worst relative EXR error " << worst_exr);
    CHECK(worst_exr <= 1e-2);

    // PNG is 8-bit sRGB: comparing in sRGB space isolates the quantisation error.
    const Image png = read_image(png_path);
    REQUIRE(png.width == hdr.width);
    const Image expected = tonemap_image(hdr, tm);
    double worst_png = 0.0;
    for (size_t p = 0; p < png.pixel_count(); ++p) {
        for (int c = 0; c < 3; ++c) {
            const float got = linear_to_srgb(png.rgba[p * 4 + static_cast<size_t>(c)]);
            const float want = expected.rgba[p * 4 + static_cast<size_t>(c)];
            worst_png = std::max(worst_png, static_cast<double>(std::fabs(got - want)));
        }
        worst_png = std::max(worst_png, static_cast<double>(std::fabs(png.rgba[p * 4 + 3] -
                                                                     expected.rgba[p * 4 + 3])));
    }
    INFO("worst PNG error " << worst_png);
    CHECK(worst_png <= 2.0 / 255.0);
}

TEST_CASE("hero frame", "[render][hero]") {
    const FrameState fs = make_scene();
    const ResourceSet res = make_resources();
    const CameraDesc cam = hero_camera();
    RenderSettings settings = hero_settings(512);
    settings.supersample = 2;

    auto renderer = create_software_renderer();
    const Image hdr = renderer->render(fs, res, cam, settings);
    const RenderStatistics stats = renderer->last_statistics();
    INFO("particles " << stats.particles_drawn << "/" << stats.particles_submitted << " overdraw "
                      << stats.overdraw << " in " << stats.render_ms << " ms");

    const std::filesystem::path path = output_dir() / "hero_frame.png";
    TonemapSettings tm;
    tm.exposure = 1.0f;
    tm.filmic = true;
    REQUIRE_NOTHROW(write_image(path, hdr, tm));
    REQUIRE(std::filesystem::exists(path));
    REQUIRE_NOTHROW(write_image(output_dir() / "hero_frame.exr", hdr, tm));
    CHECK(stats.particles_drawn == 300);
}
