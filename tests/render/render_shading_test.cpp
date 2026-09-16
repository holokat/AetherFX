// Focused software renderer tests: blending, soft particles, sprite sheets and statistics.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <vector>

#include "aether/render/renderer.hpp"

using namespace aether;
using namespace aether::render;
using Catch::Approx;

namespace {

// A plain renderer configuration: nothing but the particles on a black background.
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

void push_particle(ParticleBuffer& pb, Vec3 position, float size, Color color, float opacity, float emissive,
                   float age = 0.0f, float lifetime = 1.0f) {
    pb.position.push_back(position);
    pb.previous_position.push_back(position);
    pb.velocity.push_back(Vec3{});
    pb.acceleration.push_back(Vec3{});
    pb.age.push_back(age);
    pb.lifetime.push_back(lifetime);
    pb.size.push_back(size);
    pb.rotation.push_back(0.0f);
    pb.angular_velocity.push_back(0.0f);
    pb.color.push_back(color);
    pb.opacity.push_back(opacity);
    pb.emissive.push_back(emissive);
    pb.mass.push_back(1.0f);
    pb.custom0.push_back(0.0f);
    pb.custom1.push_back(0.0f);
    pb.seed.push_back(static_cast<uint32_t>(pb.position.size()));
}

// A 1x1 fully opaque white sprite: makes the fragment alpha exactly the particle opacity, so the
// blend arithmetic can be asserted exactly.
TextureResource flat_white_sprite() {
    TextureResource t;
    t.image = Image(1, 1, Color{1.0f, 1.0f, 1.0f, 1.0f});
    t.frames = 1;
    return t;
}

MaterialDesc simple_material(const std::string& id, BlendMode blend) {
    MaterialDesc m;
    m.id = id;
    m.blend = blend;
    m.shading = Shading::Unlit;
    m.soft_particle = false;
    m.base_color = Color{1.0f, 1.0f, 1.0f, 1.0f};
    m.opacity = 1.0f;
    m.emissive_intensity = 0.0f;
    return m;
}

float mean_luminance(const Image& img) {
    double sum = 0.0;
    for (size_t i = 0; i < img.pixel_count(); ++i)
        sum += static_cast<double>(Color{img.rgba[i * 4], img.rgba[i * 4 + 1], img.rgba[i * 4 + 2], 1.0f}
                                       .luminance());
    return img.pixel_count() > 0 ? static_cast<float>(sum / static_cast<double>(img.pixel_count())) : 0.0f;
}

}  // namespace

TEST_CASE("additive blending accumulates", "[render]") {
    ResourceSet res;
    res.textures["flat"] = flat_white_sprite();
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    FrameState fs;
    ParticleBuffer pb;
    pb.system_id = "two";
    pb.blend = BlendMode::Additive;
    pb.material_id = "add";
    pb.sprite_id = "flat";
    pb.soft_particle_distance = 0.0f;
    push_particle(pb, Vec3{0.0f, 0.0f, -1.0f}, 2.0f, Color::white(), 0.5f, 0.0f);
    push_particle(pb, Vec3{0.0f, 0.0f, 0.0f}, 2.0f, Color::white(), 0.5f, 0.0f);
    fs.particles.push_back(pb);

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(64));
    const Color c = img.get(32, 32);
    // dst = 0 + 1*0.5 + 1*0.5
    CHECK(c.r == Approx(1.0f).margin(1e-4));
    CHECK(c.g == Approx(1.0f).margin(1e-4));
    CHECK(c.b == Approx(1.0f).margin(1e-4));
    CHECK(c.a == Approx(0.75f).margin(1e-4));  // coverage = 1-(1-a)(1-b)
}

TEST_CASE("alpha blending composites back to front", "[render]") {
    ResourceSet res;
    res.textures["flat"] = flat_white_sprite();
    res.materials["over"] = simple_material("over", BlendMode::Alpha);

    FrameState fs;
    ParticleBuffer pb;
    pb.system_id = "two";
    pb.blend = BlendMode::Alpha;
    pb.material_id = "over";
    pb.sprite_id = "flat";
    pb.soft_particle_distance = 0.0f;
    // Submitted near-first on purpose: the renderer must still draw the far one first.
    push_particle(pb, Vec3{0.0f, 0.0f, 0.0f}, 2.0f, Color{1.0f, 0.0f, 0.0f, 1.0f}, 0.5f, 0.0f);
    push_particle(pb, Vec3{0.0f, 0.0f, -1.0f}, 2.0f, Color{0.0f, 0.0f, 1.0f, 1.0f}, 0.5f, 0.0f);
    fs.particles.push_back(pb);

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(64));
    const Color c = img.get(32, 32);
    // blue over black -> (0,0,0.5); red over that -> (0.5, 0, 0.25)
    CHECK(c.r == Approx(0.5f).margin(1e-4));
    CHECK(c.g == Approx(0.0f).margin(1e-4));
    CHECK(c.b == Approx(0.25f).margin(1e-4));
    CHECK(c.a == Approx(0.75f).margin(1e-4));
}

TEST_CASE("premultiplied blending ignores the source alpha on the colour term", "[render]") {
    ResourceSet res;
    res.textures["flat"] = flat_white_sprite();
    res.materials["pm"] = simple_material("pm", BlendMode::Premultiplied);

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "pm";
    pb.sprite_id = "flat";
    pb.blend = BlendMode::Premultiplied;
    pb.soft_particle_distance = 0.0f;
    push_particle(pb, Vec3{0.0f, 0.0f, 0.0f}, 2.0f, Color{0.4f, 0.4f, 0.4f, 1.0f}, 0.5f, 0.0f);
    fs.particles.push_back(pb);

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(64));
    const Color c = img.get(32, 32);
    CHECK(c.r == Approx(0.4f).margin(1e-4));  // dst = src + dst*(1-a), dst starts at 0
    CHECK(c.a == Approx(0.5f).margin(1e-4));
}

TEST_CASE("soft particles fade against the ground plane", "[render]") {
    ResourceSet res;
    MaterialDesc m = simple_material("soft", BlendMode::Additive);
    m.soft_particle = true;
    res.materials["soft"] = m;

    auto build = [&](float y) {
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "soft";
        pb.blend = BlendMode::Additive;
        pb.soft_particle_distance = 0.6f;
        push_particle(pb, Vec3{0.0f, y, 0.0f}, 1.5f, Color::white(), 1.0f, 0.0f);
        fs.particles.push_back(pb);
        return fs;
    };

    RenderSettings settings = plain_settings(128);
    settings.ground_plane = true;  // writes depth; albedo 0 keeps it black so only the particle shows
    settings.ground_albedo = 0.0f;
    settings.soft_particles = true;

    CameraDesc cam;
    cam.position = Vec3{0.0f, 1.2f, 4.5f};
    cam.target = Vec3{0.0f, 0.8f, 0.0f};
    cam.fov_deg = 45.0f;

    auto renderer = create_software_renderer();
    const Image intersecting = renderer->render(build(0.02f), res, cam, settings);
    const Image raised = renderer->render(build(1.0f), res, cam, settings);

    const float lum_intersecting = mean_luminance(intersecting);
    const float lum_raised = mean_luminance(raised);
    INFO("intersecting=" << lum_intersecting << " raised=" << lum_raised);
    CHECK(lum_intersecting < lum_raised);
    CHECK(lum_intersecting > 0.0f);

    // With soft particles disabled the intersecting billboard is only depth-clipped, so it is
    // brighter than the soft-faded version.
    RenderSettings hard = settings;
    hard.soft_particles = false;
    const Image hard_img = renderer->render(build(0.02f), res, cam, hard);
    CHECK(mean_luminance(hard_img) > lum_intersecting);
}

TEST_CASE("statistics count every particle in view", "[render]") {
    ResourceSet res;
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "add";
    pb.blend = BlendMode::Additive;
    pb.soft_particle_distance = 0.0f;
    for (int i = 0; i < 50; ++i) {
        const float a = static_cast<float>(i) * 0.37f;
        push_particle(pb, Vec3{std::cos(a) * 0.4f, std::sin(a) * 0.4f, std::sin(a * 1.7f) * 0.3f}, 0.3f,
                      Color::white(), 0.8f, 1.0f);
    }
    fs.particles.push_back(pb);

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(128));
    const RenderStatistics stats = renderer->last_statistics();
    CHECK(stats.particles_submitted == 50);
    CHECK(stats.particles_drawn == 50);
    CHECK(stats.fragments_shaded > 0);
    CHECK(stats.overdraw > 0.0);
    CHECK(mean_luminance(img) > 0.0f);

    // render_mode none / ribbon are submitted but never drawn as billboards.
    FrameState skipped = fs;
    skipped.particles[0].render_mode = RenderMode::Ribbon;
    renderer->render(skipped, res, front_camera(), plain_settings(64));
    CHECK(renderer->last_statistics().particles_submitted == 50);
    CHECK(renderer->last_statistics().particles_drawn == 0);
}

TEST_CASE("sprite sheets select cells from age", "[render]") {
    // 2x2 sheet: red, green (top row) / blue, yellow (bottom row).
    TextureResource sheet;
    sheet.image = Image(64, 64, Color::transparent());
    sheet.frames = 1;
    for (int y = 0; y < 64; ++y) {
        for (int x = 0; x < 64; ++x) {
            const bool right = x >= 32;
            const bool bottom = y >= 32;
            Color c = Color{1.0f, 0.0f, 0.0f, 1.0f};
            if (right && !bottom) c = Color{0.0f, 1.0f, 0.0f, 1.0f};
            if (!right && bottom) c = Color{0.0f, 0.0f, 1.0f, 1.0f};
            if (right && bottom) c = Color{1.0f, 1.0f, 0.0f, 1.0f};
            sheet.image.set(x, y, c);
        }
    }
    ResourceSet res;
    res.textures["sheet"] = sheet;
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    auto render_one = [&](float age, float fps) {
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "add";
        pb.sprite_id = "sheet";
        pb.blend = BlendMode::Additive;
        pb.sprite_columns = 2;
        pb.sprite_rows = 2;
        pb.sprite_fps = fps;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 2.0f, Color::white(), 1.0f, 0.0f, age, 1.0f);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        const Image img = renderer->render(fs, res, front_camera(), plain_settings(64));
        return img.get(32, 32);
    };

    const Color f0 = render_one(0.10f, 0.0f);  // cell 0 -> red
    const Color f1 = render_one(0.35f, 0.0f);  // cell 1 -> green
    const Color f2 = render_one(0.60f, 0.0f);  // cell 2 -> blue
    const Color f3 = render_one(0.85f, 0.0f);  // cell 3 -> yellow

    CHECK(f0.r > 0.5f); CHECK(f0.g < 0.1f); CHECK(f0.b < 0.1f);
    CHECK(f1.g > 0.5f); CHECK(f1.r < 0.1f); CHECK(f1.b < 0.1f);
    CHECK(f2.b > 0.5f); CHECK(f2.r < 0.1f); CHECK(f2.g < 0.1f);
    CHECK(f3.r > 0.5f); CHECK(f3.g > 0.5f); CHECK(f3.b < 0.1f);

    // sprite_fps > 0 plays the sheet at a fixed rate instead of over the lifetime.
    const Color fps2 = render_one(0.5f, 4.0f);  // floor(0.5*4) = 2 -> blue
    CHECK(fps2.b > 0.5f);
    CHECK(fps2.r < 0.1f);
}

TEST_CASE("stretched billboards elongate along the screen velocity", "[render]") {
    ResourceSet res;
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    auto render_mode = [&](RenderMode mode, float stretch) {
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "add";
        pb.blend = BlendMode::Additive;
        pb.render_mode = mode;
        pb.velocity_stretch = stretch;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 0.5f, Color::white(), 1.0f, 0.0f);
        pb.velocity[0] = Vec3{0.0f, 3.0f, 0.0f};
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(128));
    };

    auto lit_rows = [](const Image& img) {
        int rows = 0;
        for (int y = 0; y < img.height; ++y) {
            for (int x = 0; x < img.width; ++x) {
                if (img.get(x, y).luminance() > 1e-4f) { ++rows; break; }
            }
        }
        return rows;
    };
    auto lit_cols = [](const Image& img) {
        int cols = 0;
        for (int x = 0; x < img.width; ++x) {
            for (int y = 0; y < img.height; ++y) {
                if (img.get(x, y).luminance() > 1e-4f) { ++cols; break; }
            }
        }
        return cols;
    };

    const Image plain = render_mode(RenderMode::Billboard, 0.0f);
    const Image stretched = render_mode(RenderMode::StretchedBillboard, 1.0f);
    CHECK(lit_rows(plain) > 0);
    CHECK(lit_rows(stretched) > lit_rows(plain));       // taller along the velocity
    CHECK(lit_cols(stretched) <= lit_cols(plain) + 1);  // no wider across it
}

TEST_CASE("mesh particles and unknown mesh ids are handled", "[render]") {
    MeshData quad;
    quad.positions = {{-0.5f, -0.5f, 0.0f}, {0.5f, -0.5f, 0.0f}, {0.5f, 0.5f, 0.0f}, {-0.5f, 0.5f, 0.0f}};
    quad.normals = {{0, 0, 1}, {0, 0, 1}, {0, 0, 1}, {0, 0, 1}};
    quad.indices = {0, 1, 2, 0, 2, 3};

    ResourceSet res;
    res.meshes["quad"] = quad;
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "add";
    pb.blend = BlendMode::Additive;
    pb.render_mode = RenderMode::Mesh;
    pb.mesh_id = "quad";
    pb.soft_particle_distance = 0.0f;
    push_particle(pb, Vec3{}, 1.5f, Color{0.2f, 0.9f, 0.4f, 1.0f}, 1.0f, 0.0f);
    fs.particles.push_back(pb);

    // A mesh instance with an unknown id must be skipped, not crash.
    MeshInstanceState missing;
    missing.id = "missing";
    missing.mesh_id = "does_not_exist";
    fs.meshes.push_back(missing);

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), plain_settings(64));
    const Color c = img.get(32, 32);
    CHECK(c.g > 0.5f);
    CHECK(c.r < 0.5f);
}

TEST_CASE("post effects modify the image", "[render]") {
    ResourceSet res;
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "add";
    pb.blend = BlendMode::Additive;
    pb.soft_particle_distance = 0.0f;
    push_particle(pb, Vec3{}, 1.0f, Color::white(), 1.0f, 3.0f);
    fs.particles.push_back(pb);

    auto renderer = create_software_renderer();
    const RenderSettings base = plain_settings(96);
    const Image plain = renderer->render(fs, res, front_camera(), base);

    SECTION("bloom spreads energy outwards") {
        RenderSettings bloomed = base;
        bloomed.bloom = true;
        bloomed.bloom_threshold = 0.5f;
        bloomed.bloom_intensity = 1.0f;
        bloomed.bloom_radius = 0.25f;  // sigma = radius * width pixels
        const Image img = renderer->render(fs, res, front_camera(), bloomed);
        CHECK(mean_luminance(img) > mean_luminance(plain));
        // Well outside the billboard's own footprint, but within the bloom kernel.
        CHECK(plain.get(48, 16).luminance() == Approx(0.0f).margin(1e-6));
        CHECK(img.get(48, 16).luminance() > 1e-4f);
    }
    SECTION("a bloom post effect node enables and overrides bloom") {
        FrameState with_bloom = fs;
        PostEffectState pe;
        pe.post_type = "bloom";
        pe.intensity = 1.5f;
        pe.threshold = 0.2f;
        pe.radius = 0.1f;
        with_bloom.post_effects.push_back(pe);
        const Image img = renderer->render(with_bloom, res, front_camera(), base);
        CHECK(mean_luminance(img) > mean_luminance(plain));
    }
    SECTION("exposure pulse scales the whole frame") {
        FrameState pulsed = fs;
        PostEffectState pe;
        pe.post_type = "exposure_pulse";
        pe.intensity = 1.0f;
        pe.frequency = 1.0f;
        pe.time = 0.25;  // sin(2*pi*0.25) = 1 -> factor 1 + 1*0.5*1 = 1.5
        pulsed.post_effects.push_back(pe);
        const Image img = renderer->render(pulsed, res, front_camera(), base);
        CHECK(img.get(48, 48).r == Approx(plain.get(48, 48).r * 1.5f).epsilon(1e-3));
    }
    SECTION("chromatic aberration separates the channels") {
        FrameState ca = fs;
        PostEffectState pe;
        pe.post_type = "chromatic_aberration";
        pe.intensity = 3.0f;
        ca.post_effects.push_back(pe);
        const Image img = renderer->render(ca, res, front_camera(), base);
        bool separated = false;
        for (int y = 0; y < img.height && !separated; ++y)
            for (int x = 0; x < img.width && !separated; ++x) {
                const Color c = img.get(x, y);
                if (std::fabs(c.r - c.b) > 0.05f) separated = true;
            }
        CHECK(separated);
    }
    SECTION("heat haze warps the image but keeps it deterministic") {
        FrameState hazed = fs;
        PostEffectState pe;
        pe.post_type = "heat_haze";
        pe.intensity = 1.0f;
        pe.radius = 0.06f;
        pe.time = 0.75;
        hazed.post_effects.push_back(pe);
        const Image a = renderer->render(hazed, res, front_camera(), base);
        const Image b = renderer->render(hazed, res, front_camera(), base);
        CHECK(a.hash() == b.hash());
        CHECK(a.hash() != plain.hash());
    }
}

TEST_CASE("the ground plane and grid respond to lights", "[render]") {
    ResourceSet res;
    FrameState fs;
    LightState l;
    l.type = LightType::Point;
    l.position = Vec3{0.0f, 1.0f, 0.0f};
    l.color = Color{1.0f, 1.0f, 1.0f, 1.0f};
    l.intensity = 40.0f;
    l.radius = 6.0f;
    fs.lights.push_back(l);

    RenderSettings settings = plain_settings(128);
    settings.ground_plane = true;
    settings.ground_albedo = 0.5f;
    CameraDesc cam;
    cam.position = Vec3{0.0f, 2.5f, 5.0f};
    cam.target = Vec3{0.0f, 0.0f, 0.0f};

    auto renderer = create_software_renderer();
    const Image unlit = renderer->render(FrameState{}, res, cam, settings);
    const Image lit = renderer->render(fs, res, cam, settings);
    CHECK(mean_luminance(lit) > mean_luminance(unlit));

    RenderSettings gridded = settings;
    gridded.grid = true;
    const Image with_grid = renderer->render(fs, res, cam, gridded);
    CHECK(with_grid.hash() != lit.hash());

    // The ground covers the lower part of the frame.
    CHECK(lit.get(64, 120).a > 0.5f);
}

TEST_CASE("decals project onto the ground", "[render]") {
    ResourceSet res;
    FrameState fs;
    DecalState d;
    d.position = Vec3{0.0f, 0.0f, 0.0f};
    d.size = Vec2{2.0f, 2.0f};
    d.color = Color{1.0f, 0.2f, 0.1f, 1.0f};
    d.opacity = 1.0f;
    d.emissive = 1.0f;
    d.circle = true;
    d.blend = BlendMode::Alpha;
    fs.decals.push_back(d);

    RenderSettings settings = plain_settings(128);
    settings.ground_plane = true;
    settings.ground_albedo = 0.1f;
    CameraDesc cam;
    cam.position = Vec3{0.0f, 3.0f, 3.5f};
    cam.target = Vec3{0.0f, 0.0f, 0.0f};

    auto renderer = create_software_renderer();
    const Image without = renderer->render(FrameState{}, res, cam, settings);
    const Image with = renderer->render(fs, res, cam, settings);
    CHECK(mean_luminance(with) > mean_luminance(without));

    // The decal is red: the centre of the frame must be red-dominant.
    const Color c = with.get(64, 70);
    CHECK(c.r > c.g);
    CHECK(c.r > c.b);
}

TEST_CASE("beams and trails are drawn", "[render]") {
    ResourceSet res;
    RenderSettings settings = plain_settings(128);
    CameraDesc cam = front_camera();
    cam.position = Vec3{0.0f, 1.5f, 6.0f};
    cam.target = Vec3{0.0f, 1.5f, 0.0f};
    auto renderer = create_software_renderer();

    FrameState empty;
    const Image blank = renderer->render(empty, res, cam, settings);
    CHECK(mean_luminance(blank) == Approx(0.0f).margin(1e-6));

    FrameState with_beam;
    BeamState beam;
    beam.polylines.push_back({Vec3{0.0f, 3.0f, 0.0f}, Vec3{0.0f, 0.0f, 0.0f}});
    beam.polylines.push_back({Vec3{0.0f, 1.5f, 0.0f}, Vec3{0.8f, 0.9f, 0.0f}});
    beam.width = 0.1f;
    beam.color = Color{0.5f, 0.7f, 1.0f, 1.0f};
    beam.emissive = 3.0f;
    beam.pulse_phase = 0.5f;
    with_beam.beams.push_back(beam);
    const Image beam_img = renderer->render(with_beam, res, cam, settings);
    CHECK(mean_luminance(beam_img) > 0.0f);
    CHECK(beam_img.get(64, 64).b > beam_img.get(64, 64).r);  // blue beam

    FrameState with_trail;
    TrailState trail;
    std::vector<TrailVertex> ribbon;
    for (int i = 0; i <= 12; ++i) {
        const float t = static_cast<float>(i) / 12.0f;
        TrailVertex tv;
        tv.position = Vec3{-1.5f + 3.0f * t, 1.5f, 0.0f};
        tv.width = lerp(0.02f, 0.25f, t);
        tv.color = Color{1.0f, 0.4f, 0.1f, 1.0f};
        tv.opacity = 1.0f;
        tv.emissive = 1.0f;
        tv.u = t;
        ribbon.push_back(tv);
    }
    trail.ribbons.push_back(ribbon);
    with_trail.trails.push_back(trail);
    const Image trail_img = renderer->render(with_trail, res, cam, settings);
    CHECK(mean_luminance(trail_img) > 0.0f);
    // The ribbon tapers: the wide (right) end covers more rows than the thin (left) end.
    auto column_coverage = [&](int x) {
        int n = 0;
        for (int y = 0; y < trail_img.height; ++y)
            if (trail_img.get(x, y).luminance() > 1e-4f) ++n;
        return n;
    };
    CHECK(column_coverage(100) > column_coverage(30));
}

TEST_CASE("20k billboards render within the performance budget", "[render][perf]") {
    ResourceSet res;
    res.materials["add"] = simple_material("add", BlendMode::Additive);

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "add";
    pb.blend = BlendMode::Additive;
    pb.soft_particle_distance = 0.0f;
    pb.reserve(20000);
    uint32_t seed = 1u;
    auto next = [&seed] {
        seed = seed * 1664525u + 1013904223u;
        return static_cast<float>(seed >> 8) / 16777216.0f;
    };
    for (int i = 0; i < 20000; ++i) {
        const Vec3 p{next() * 3.0f - 1.5f, next() * 3.0f - 1.5f, next() * 3.0f - 1.5f};
        push_particle(pb, p, 0.05f + next() * 0.08f, Color{1.0f, 0.6f, 0.3f, 1.0f}, 0.4f, 1.0f);
    }
    fs.particles.push_back(pb);

    RenderSettings settings = plain_settings(512);
    settings.ground_plane = true;
    settings.grid = true;
    settings.bloom = true;

    auto renderer = create_software_renderer();
    const Image img = renderer->render(fs, res, front_camera(), settings);
    const RenderStatistics stats = renderer->last_statistics();
    WARN("20k billboards @512: " << stats.render_ms << " ms, fragments " << stats.fragments_shaded
                                 << ", overdraw " << stats.overdraw);
    CHECK(stats.particles_submitted == 20000);
    CHECK(stats.particles_drawn == 20000);
    CHECK(mean_luminance(img) > 0.0f);
    CHECK(stats.render_ms < 5000.0);  // generous: the target is ~1 s on an M2
}
