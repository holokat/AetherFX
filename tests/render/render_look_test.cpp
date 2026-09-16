// Look upgrades of the software renderer: shaded (lit) billboards, the fresnel rim, the
// dissolve/erosion mask and animated texture frames.
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

CameraDesc front_camera(float distance = 5.0f) {
    CameraDesc cam;
    cam.position = Vec3{0.0f, 0.0f, distance};
    cam.target = Vec3{0.0f, 0.0f, 0.0f};
    cam.up = Vec3{0.0f, 1.0f, 0.0f};
    cam.fov_deg = 45.0f;
    return cam;
}

void push_particle(ParticleBuffer& pb, Vec3 position, float size, Color color, float opacity, float emissive,
                   float age = 0.0f, float lifetime = 1.0f, float age_norm = 0.0f, uint32_t seed = 1u) {
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
    pb.custom0.push_back(age_norm);
    pb.custom1.push_back(0.0f);
    pb.seed.push_back(seed);
}

MaterialDesc lit_material(const std::string& id) {
    MaterialDesc m;
    m.id = id;
    m.blend = BlendMode::Additive;
    m.shading = Shading::Lit;
    m.soft_particle = false;
    m.base_color = Color::white();
    m.opacity = 1.0f;
    m.emissive_intensity = 0.0f;
    return m;
}

LightState point_light(Vec3 position, float intensity = 30.0f) {
    LightState l;
    l.id = "key";
    l.type = LightType::Point;
    l.position = position;
    l.color = Color::white();
    l.intensity = intensity;
    l.radius = 30.0f;
    return l;
}

float region_luminance(const Image& img, int x0, int y0, int x1, int y1) {
    double sum = 0.0;
    int n = 0;
    for (int y = y0; y < y1; ++y)
        for (int x = x0; x < x1; ++x) {
            sum += static_cast<double>(img.get(x, y).luminance());
            ++n;
        }
    return n > 0 ? static_cast<float>(sum / n) : 0.0f;
}

float mean_luminance(const Image& img) { return region_luminance(img, 0, 0, img.width, img.height); }

// Total coverage of the frame: the alpha channel is the renderer's coverage matte, so this is
// the natural "how much of the puff survived" measure.
double total_alpha(const Image& img) {
    double sum = 0.0;
    for (size_t i = 0; i < img.pixel_count(); ++i) sum += static_cast<double>(img.rgba[i * 4 + 3]);
    return sum;
}

// A UV sphere with smooth normals.
MeshData make_sphere(int segments, int rings) {
    MeshData m;
    for (int r = 0; r <= rings; ++r) {
        const float v = static_cast<float>(r) / static_cast<float>(rings);
        const float phi = v * kPi;
        for (int sgm = 0; sgm <= segments; ++sgm) {
            const float u = static_cast<float>(sgm) / static_cast<float>(segments);
            const float theta = u * kTwoPi;
            const Vec3 n{std::sin(phi) * std::cos(theta), std::cos(phi), std::sin(phi) * std::sin(theta)};
            m.positions.push_back(n);
            m.normals.push_back(n);
            m.uvs.push_back(Vec2{u, v});
        }
    }
    const int stride = segments + 1;
    for (int r = 0; r < rings; ++r) {
        for (int sgm = 0; sgm < segments; ++sgm) {
            const uint32_t a = static_cast<uint32_t>(r * stride + sgm);
            const uint32_t b = static_cast<uint32_t>(a + static_cast<uint32_t>(stride));
            m.indices.insert(m.indices.end(), {a, b, a + 1, a + 1, b, b + 1});
        }
    }
    return m;
}

}  // namespace

// -------------------------------------------------------------------------------------------
// 1. Shaded (lit) billboards
// -------------------------------------------------------------------------------------------
TEST_CASE("lit billboards are shaded from the light direction", "[render][lit]") {
    ResourceSet res;
    res.materials["smoke"] = lit_material("smoke");

    auto render_with_light = [&](Vec3 light_position) {
        FrameState fs;
        fs.lights.push_back(point_light(light_position));
        ParticleBuffer pb;
        pb.system_id = "puff";
        pb.material_id = "smoke";
        pb.blend = BlendMode::Additive;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 3.0f, Color::white(), 1.0f, 0.0f);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(128));
    };

    const Image from_left = render_with_light(Vec3{-4.0f, 0.0f, 1.0f});
    const Image from_right = render_with_light(Vec3{4.0f, 0.0f, 1.0f});

    // A big smoke sprite reads as a sphere: the half facing the light is brighter, and moving the
    // light to the other side flips which half that is.
    const float left_lit_left = region_luminance(from_left, 24, 44, 60, 84);
    const float left_lit_right = region_luminance(from_left, 68, 44, 104, 84);
    const float right_lit_left = region_luminance(from_right, 24, 44, 60, 84);
    const float right_lit_right = region_luminance(from_right, 68, 44, 104, 84);
    INFO("light left: L=" << left_lit_left << " R=" << left_lit_right << " | light right: L="
                          << right_lit_left << " R=" << right_lit_right);
    CHECK(left_lit_left > left_lit_right);
    CHECK(right_lit_right > right_lit_left);
    // The two images are mirror images of each other, so the overall energy matches.
    CHECK(mean_luminance(from_left) == Approx(mean_luminance(from_right)).epsilon(1e-3));
}

TEST_CASE("a lit billboard is translucent to a light behind it", "[render][lit]") {
    ResourceSet res;
    res.materials["smoke"] = lit_material("smoke");

    auto render_lights = [&](const std::vector<LightState>& lights) {
        FrameState fs;
        fs.lights = lights;
        ParticleBuffer pb;
        pb.material_id = "smoke";
        pb.blend = BlendMode::Additive;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 3.0f, Color::white(), 1.0f, 0.0f);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(96));
    };

    const Image ambient_only = render_lights({});
    // Behind the puff, on the far side from the camera: wrapped Lambert is zero there, so
    // anything the puff picks up comes from the 0.35 translucency term.
    const Image back_lit = render_lights({point_light(Vec3{0.0f, 0.0f, -4.0f})});
    CHECK(mean_luminance(ambient_only) > 0.0f);
    CHECK(mean_luminance(back_lit) > mean_luminance(ambient_only));
}

TEST_CASE("unlit billboards ignore the lights", "[render][lit]") {
    ResourceSet res;
    MaterialDesc m = lit_material("unlit");
    m.shading = Shading::Unlit;
    res.materials["unlit"] = m;

    auto render_lights = [&](const std::vector<LightState>& lights) {
        FrameState fs;
        fs.lights = lights;
        ParticleBuffer pb;
        pb.material_id = "unlit";
        pb.blend = BlendMode::Additive;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 2.0f, Color::white(), 1.0f, 0.0f);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(64));
    };

    CHECK(render_lights({}).hash() == render_lights({point_light(Vec3{-4.0f, 0.0f, 1.0f})}).hash());
}

// -------------------------------------------------------------------------------------------
// 2. Fresnel rim
// -------------------------------------------------------------------------------------------
TEST_CASE("fresnel_power adds a rim to a lit mesh", "[render][fresnel]") {
    ResourceSet res;
    res.meshes["sphere"] = make_sphere(48, 24);

    auto render_fresnel = [&](float power) {
        MaterialDesc m;
        m.id = "crystal";
        m.blend = BlendMode::Alpha;
        m.shading = Shading::Lit;
        m.base_color = Color{0.25f, 0.35f, 0.55f, 1.0f};
        m.emissive_color = Color{0.3f, 0.7f, 1.0f, 1.0f};
        m.emissive_intensity = 0.0f;  // the rim floor (0.35) still gives a visible edge
        m.fresnel_power = power;
        ResourceSet local = res;
        local.materials["crystal"] = m;

        FrameState fs;
        fs.lights.push_back(point_light(Vec3{2.0f, 2.0f, 3.0f}, 25.0f));
        MeshInstanceState mi;
        mi.id = "crystal";
        mi.mesh_id = "sphere";
        mi.material_id = "crystal";
        mi.transform = Mat4::identity();
        fs.meshes.push_back(mi);
        auto renderer = create_software_renderer();
        return renderer->render(fs, local, res.materials.empty() ? front_camera(4.0f) : front_camera(4.0f),
                                plain_settings(128));
    };

    // Mean luminance of the sphere's silhouette ring and of its centre.
    auto ring_and_centre = [](const Image& img, float& ring, float& centre) {
        const float cx = static_cast<float>(img.width) * 0.5f;
        const float cy = static_cast<float>(img.height) * 0.5f;
        double ring_sum = 0.0, centre_sum = 0.0;
        int ring_n = 0, centre_n = 0;
        for (int y = 0; y < img.height; ++y) {
            for (int x = 0; x < img.width; ++x) {
                const Color c = img.get(x, y);
                if (c.a < 0.5f) continue;  // background
                const float dx = static_cast<float>(x) + 0.5f - cx;
                const float dy = static_cast<float>(y) + 0.5f - cy;
                const float r = std::sqrt(dx * dx + dy * dy);
                if (r < 10.0f) { centre_sum += static_cast<double>(c.luminance()); ++centre_n; }
                else if (r > 30.0f) { ring_sum += static_cast<double>(c.luminance()); ++ring_n; }
            }
        }
        ring = ring_n > 0 ? static_cast<float>(ring_sum / ring_n) : 0.0f;
        centre = centre_n > 0 ? static_cast<float>(centre_sum / centre_n) : 0.0f;
        REQUIRE(ring_n > 0);
        REQUIRE(centre_n > 0);
    };

    const Image flat = render_fresnel(0.0f);
    const Image rimmed = render_fresnel(3.0f);

    float flat_ring = 0.0f, flat_centre = 0.0f, rim_ring = 0.0f, rim_centre = 0.0f;
    ring_and_centre(flat, flat_ring, flat_centre);
    ring_and_centre(rimmed, rim_ring, rim_centre);
    INFO("flat ring/centre=" << flat_ring / flat_centre << " rimmed=" << rim_ring / rim_centre);

    CHECK(rim_ring > flat_ring);                                 // the silhouette gained energy
    CHECK(rim_centre == Approx(flat_centre).epsilon(0.05));      // the centre barely moved
    CHECK(rim_ring / rim_centre > flat_ring / flat_centre);      // ... so the edge stands out
}

TEST_CASE("fresnel_power adds a rim to lit billboards", "[render][fresnel]") {
    auto render_fresnel = [](float power) {
        MaterialDesc m;
        m.id = "puff";
        m.blend = BlendMode::Additive;
        m.shading = Shading::Lit;
        m.soft_particle = false;
        m.base_color = Color{0.2f, 0.2f, 0.25f, 1.0f};
        m.emissive_color = Color{1.0f, 0.4f, 0.1f, 1.0f};
        m.emissive_intensity = 0.0f;
        m.fresnel_power = power;
        ResourceSet res;
        res.materials["puff"] = m;

        FrameState fs;
        fs.lights.push_back(point_light(Vec3{0.0f, 3.0f, 2.0f}, 20.0f));
        ParticleBuffer pb;
        pb.material_id = "puff";
        pb.blend = BlendMode::Additive;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 3.0f, Color::white(), 1.0f, 0.0f);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(128));
    };

    const Image flat = render_fresnel(0.0f);
    const Image rimmed = render_fresnel(3.0f);
    CHECK(mean_luminance(rimmed) > mean_luminance(flat));
    // The rim is warm (emissive_color), so it shifts the edge of the puff towards red.
    const Color flat_edge = flat.get(26, 64);
    const Color rim_edge = rimmed.get(26, 64);
    CHECK(rim_edge.r > flat_edge.r);
    CHECK(rim_edge.r - rim_edge.b > flat_edge.r - flat_edge.b);
    // ... and the centre, which faces the camera, is barely touched.
    CHECK(rimmed.get(64, 64).r == Approx(flat.get(64, 64).r).epsilon(0.02));
}

// -------------------------------------------------------------------------------------------
// 3. Dissolve / erosion
// -------------------------------------------------------------------------------------------
namespace {

// One eroded billboard on its own; returns the rendered frame so coverage can be measured.
Image render_eroded(float dissolve, float erosion, float age_norm, const std::string& noise_texture = "",
                    const ResourceSet* extra = nullptr) {
    MaterialDesc m;
    m.id = "wisp";
    m.blend = BlendMode::Alpha;
    m.shading = Shading::Unlit;
    m.soft_particle = false;
    m.base_color = Color::white();
    m.dissolve = dissolve;
    m.erosion = erosion;
    m.noise_texture = noise_texture;
    ResourceSet res = extra ? *extra : ResourceSet{};
    res.materials["wisp"] = m;

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "wisp";
    pb.blend = BlendMode::Alpha;
    pb.soft_particle_distance = 0.0f;
    push_particle(pb, Vec3{}, 3.0f, Color::white(), 1.0f, 0.0f, 0.5f, 1.0f, age_norm, 4242u);
    fs.particles.push_back(pb);
    auto renderer = create_software_renderer();
    return renderer->render(fs, res, front_camera(), plain_settings(128));
}

}  // namespace

TEST_CASE("dissolve removes coverage monotonically", "[render][dissolve]") {
    const double none = total_alpha(render_eroded(0.0f, 0.0f, 0.5f));
    const double half = total_alpha(render_eroded(0.5f, 0.0f, 0.5f));
    const double most = total_alpha(render_eroded(0.9f, 0.0f, 0.5f));
    INFO("coverage none=" << none << " 0.5=" << half << " 0.9=" << most);
    CHECK(none > half);
    CHECK(half > most);
    CHECK(most >= 0.0);
    // Zero dissolve and zero erosion must cost nothing at all: the mask is skipped entirely.
    CHECK(render_eroded(0.0f, 0.0f, 0.5f).hash() == render_eroded(0.0f, 0.0f, 0.9f).hash());
}

TEST_CASE("erosion alone eats the particle over its life", "[render][dissolve]") {
    const double young = total_alpha(render_eroded(0.0f, 0.6f, 0.05f));
    const double middle = total_alpha(render_eroded(0.0f, 0.6f, 0.5f));
    const double old = total_alpha(render_eroded(0.0f, 0.6f, 0.95f));
    INFO("coverage young=" << young << " middle=" << middle << " old=" << old);
    CHECK(young > middle);
    CHECK(middle > old);
}

TEST_CASE("the dissolve noise comes from material.noise_texture when there is one", "[render][dissolve]") {
    auto constant_noise = [](float value) {
        TextureResource t;
        t.image = Image(64, 64, Color{value, value, value, 1.0f});
        ResourceSet res;
        res.textures["noise"] = t;
        return res;
    };
    // threshold = 0.8 * (0.25 + 0.75 * 0.5) = 0.5, edge = 0.02: a noise value of 1 keeps every
    // fragment and a noise value of 0 removes every one, so the texture is unambiguously the
    // mask source.
    const ResourceSet white = constant_noise(1.0f);
    const ResourceSet black = constant_noise(0.0f);
    const ResourceSet grey = constant_noise(0.5f);

    const double undissolved = total_alpha(render_eroded(0.0f, 0.0f, 0.5f));
    CHECK(total_alpha(render_eroded(0.8f, 0.0f, 0.5f, "noise", &white)) == Approx(undissolved).epsilon(1e-5));
    CHECK(total_alpha(render_eroded(0.8f, 0.0f, 0.5f, "noise", &black)) == Approx(0.0).margin(1e-6));
    // Exactly on the threshold: smoothstep gives 0.5, so half the coverage survives.
    CHECK(total_alpha(render_eroded(0.8f, 0.0f, 0.5f, "noise", &grey)) ==
          Approx(undissolved * 0.5).epsilon(1e-4));

    // A named texture that is missing from the ResourceSet falls back to the built-in noise.
    CHECK(render_eroded(0.8f, 0.0f, 0.5f, "not_here", nullptr).hash() ==
          render_eroded(0.8f, 0.0f, 0.5f, "", nullptr).hash());
    CHECK(render_eroded(0.8f, 0.0f, 0.5f, "noise", &grey).hash() !=
          render_eroded(0.8f, 0.0f, 0.5f, "", nullptr).hash());
}

TEST_CASE("different particle seeds break up differently", "[render][dissolve]") {
    auto render_seed = [](uint32_t seed) {
        MaterialDesc m;
        m.id = "wisp";
        m.blend = BlendMode::Alpha;
        m.soft_particle = false;
        m.dissolve = 0.7f;
        ResourceSet res;
        res.materials["wisp"] = m;
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "wisp";
        pb.blend = BlendMode::Alpha;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 3.0f, Color::white(), 1.0f, 0.0f, 0.5f, 1.0f, 0.5f, seed);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(96));
    };
    CHECK(render_seed(1u).hash() != render_seed(2u).hash());
    CHECK(render_seed(1u).hash() == render_seed(1u).hash());  // still deterministic
}

// -------------------------------------------------------------------------------------------
// 4. Animated texture frames
// -------------------------------------------------------------------------------------------
namespace {

// A 4-frame animated texture baked side by side: red, green, blue, yellow.
TextureResource four_frames() {
    TextureResource t;
    t.frames = 4;
    t.image = Image(64, 16, Color::transparent());
    const Color colors[4] = {{1.0f, 0.0f, 0.0f, 1.0f},
                             {0.0f, 1.0f, 0.0f, 1.0f},
                             {0.0f, 0.0f, 1.0f, 1.0f},
                             {1.0f, 1.0f, 0.0f, 1.0f}};
    for (int y = 0; y < 16; ++y)
        for (int x = 0; x < 64; ++x) t.image.set(x, y, colors[x / 16]);
    return t;
}

Image render_animated(float age_norm, float age, float sprite_fps) {
    ResourceSet res;
    res.textures["anim"] = four_frames();
    MaterialDesc m;
    m.id = "add";
    m.blend = BlendMode::Additive;
    m.soft_particle = false;
    res.materials["add"] = m;

    FrameState fs;
    ParticleBuffer pb;
    pb.material_id = "add";
    pb.sprite_id = "anim";
    pb.blend = BlendMode::Additive;
    pb.sprite_fps = sprite_fps;
    pb.soft_particle_distance = 0.0f;
    push_particle(pb, Vec3{}, 2.0f, Color::white(), 1.0f, 0.0f, age, 1.0f, age_norm);
    fs.particles.push_back(pb);
    auto renderer = create_software_renderer();
    return renderer->render(fs, res, front_camera(), plain_settings(64));
}

}  // namespace

TEST_CASE("animated texture frames advance with the particle age", "[render][frames]") {
    const Color f0 = render_animated(0.10f, 0.0f, 0.0f).get(32, 32);
    const Color f1 = render_animated(0.35f, 0.0f, 0.0f).get(32, 32);
    const Color f2 = render_animated(0.60f, 0.0f, 0.0f).get(32, 32);
    const Color f3 = render_animated(0.99f, 0.0f, 0.0f).get(32, 32);

    CHECK(f0.r > 0.5f); CHECK(f0.g < 0.05f); CHECK(f0.b < 0.05f);  // red
    CHECK(f1.g > 0.5f); CHECK(f1.r < 0.05f); CHECK(f1.b < 0.05f);  // green
    CHECK(f2.b > 0.5f); CHECK(f2.r < 0.05f); CHECK(f2.g < 0.05f);  // blue
    CHECK(f3.r > 0.5f); CHECK(f3.g > 0.5f); CHECK(f3.b < 0.05f);   // yellow
}

TEST_CASE("sprite_fps drives the animated frame at a fixed rate", "[render][frames]") {
    // floor(0.5 * 4) = 2 -> blue, whatever the normalized age says.
    const Color c = render_animated(0.0f, 0.5f, 4.0f).get(32, 32);
    CHECK(c.b > 0.5f);
    CHECK(c.r < 0.05f);
    CHECK(c.g < 0.05f);
    // ... and it wraps: floor(1.25 * 4) = 5, 5 mod 4 = 1 -> green.
    const Color wrapped = render_animated(0.0f, 1.25f, 4.0f).get(32, 32);
    CHECK(wrapped.g > 0.5f);
    CHECK(wrapped.r < 0.05f);
}

TEST_CASE("frame sampling never bleeds into the neighbouring frame", "[render][frames]") {
    // Frame 1 is pure green and its neighbours are pure red and pure blue: with the UV clamped
    // inside the frame rectangle no pixel of the quad may pick up any red or blue at all.
    const Image img = render_animated(0.35f, 0.0f, 0.0f);
    float max_red = 0.0f;
    float max_blue = 0.0f;
    int covered = 0;
    for (int y = 0; y < img.height; ++y) {
        for (int x = 0; x < img.width; ++x) {
            const Color c = img.get(x, y);
            if (c.a <= 0.0f) continue;
            ++covered;
            max_red = std::max(max_red, c.r);
            max_blue = std::max(max_blue, c.b);
        }
    }
    REQUIRE(covered > 0);
    INFO("max red=" << max_red << " max blue=" << max_blue);
    CHECK(max_red == Approx(0.0f).margin(1e-6));
    CHECK(max_blue == Approx(0.0f).margin(1e-6));
}

TEST_CASE("animated frames combine with a sprite sheet inside the frame", "[render][frames]") {
    // Two frames, each holding a 2x1 sheet: frame 0 = [red | green], frame 1 = [blue | yellow].
    TextureResource t;
    t.frames = 2;
    t.image = Image(64, 16, Color::transparent());
    const Color cells[4] = {{1.0f, 0.0f, 0.0f, 1.0f},
                            {0.0f, 1.0f, 0.0f, 1.0f},
                            {0.0f, 0.0f, 1.0f, 1.0f},
                            {1.0f, 1.0f, 0.0f, 1.0f}};
    for (int y = 0; y < 16; ++y)
        for (int x = 0; x < 64; ++x) t.image.set(x, y, cells[x / 16]);

    ResourceSet res;
    res.textures["anim"] = t;
    MaterialDesc m;
    m.id = "add";
    m.blend = BlendMode::Additive;
    m.soft_particle = false;
    res.materials["add"] = m;

    auto render_cell = [&](float age_norm, float age, float fps) {
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "add";
        pb.sprite_id = "anim";
        pb.blend = BlendMode::Additive;
        pb.sprite_columns = 2;
        pb.sprite_rows = 1;
        pb.sprite_fps = fps;
        pb.soft_particle_distance = 0.0f;
        push_particle(pb, Vec3{}, 2.0f, Color::white(), 1.0f, 0.0f, age, 1.0f, age_norm);
        fs.particles.push_back(pb);
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, front_camera(), plain_settings(64)).get(32, 32);
    };

    // sprite_fps = 2: at age 0.1 both frame and cell are 0 (red); at age 0.6 both are 1 (yellow).
    const Color first = render_cell(0.0f, 0.1f, 2.0f);
    CHECK(first.r > 0.5f);
    CHECK(first.g < 0.05f);
    const Color second = render_cell(0.0f, 0.6f, 2.0f);
    CHECK(second.r > 0.5f);
    CHECK(second.g > 0.5f);
    CHECK(second.b < 0.05f);
}

TEST_CASE("animated decal textures advance with the effect time", "[render][frames]") {
    TextureResource t;
    t.frames = 2;
    t.image = Image(32, 16, Color::white());
    for (int y = 0; y < 16; ++y) {
        for (int x = 0; x < 16; ++x) t.image.set(x, y, Color{1.0f, 0.0f, 0.0f, 1.0f});
        for (int x = 16; x < 32; ++x) t.image.set(x, y, Color{0.0f, 0.0f, 1.0f, 1.0f});
    }
    ResourceSet res;
    res.textures["anim"] = t;

    auto render_at = [&](double time) {
        FrameState fs;
        fs.time = time;
        DecalState d;
        d.id = "scorch";
        d.position = Vec3{};
        d.size = Vec2{3.0f, 3.0f};
        d.color = Color::white();
        d.opacity = 1.0f;
        d.emissive = 1.0f;
        d.blend = BlendMode::Alpha;
        d.texture_id = "anim";
        fs.decals.push_back(d);

        RenderSettings settings = plain_settings(96);
        settings.ground_plane = true;
        settings.ground_albedo = 0.0f;
        CameraDesc cam;
        cam.position = Vec3{0.0f, 3.0f, 3.0f};
        cam.target = Vec3{0.0f, 0.0f, 0.0f};
        auto renderer = create_software_renderer();
        return renderer->render(fs, res, cam, settings).get(48, 56);
    };

    const Color frame0 = render_at(0.0);     // floor(0*8) = 0 -> red
    const Color frame1 = render_at(0.125);   // floor(0.125*8) = 1 -> blue
    const Color wrapped = render_at(0.25);   // floor(0.25*8) = 2, 2 mod 2 = 0 -> red again
    CHECK(frame0.r > frame0.b);
    CHECK(frame1.b > frame1.r);
    CHECK(wrapped.r > wrapped.b);
}

TEST_CASE("the look upgrades stay deterministic", "[render][lit][dissolve]") {
    ResourceSet res;
    MaterialDesc m = lit_material("smoke");
    m.fresnel_power = 2.5f;
    m.emissive_color = Color{0.4f, 0.6f, 1.0f, 1.0f};
    m.dissolve = 0.4f;
    m.erosion = 0.3f;
    res.materials["smoke"] = m;

    FrameState fs;
    fs.lights.push_back(point_light(Vec3{-3.0f, 2.0f, 2.0f}));
    fs.lights.push_back(point_light(Vec3{3.0f, -1.0f, -2.0f}, 12.0f));
    ParticleBuffer pb;
    pb.material_id = "smoke";
    pb.blend = BlendMode::Alpha;
    pb.soft_particle_distance = 0.0f;
    for (int i = 0; i < 24; ++i) {
        const float a = static_cast<float>(i) * 0.53f;
        push_particle(pb, Vec3{std::cos(a) * 0.8f, std::sin(a) * 0.6f, std::sin(a * 1.3f) * 0.5f}, 1.2f,
                      Color{0.7f, 0.72f, 0.8f, 1.0f}, 0.6f, 0.0f, 0.4f, 1.0f,
                      static_cast<float>(i) / 24.0f, static_cast<uint32_t>(i * 7 + 1));
    }
    fs.particles.push_back(pb);

    auto a = create_software_renderer();
    auto b = create_software_renderer();
    const Image first = a->render(fs, res, front_camera(), plain_settings(128));
    const Image second = b->render(fs, res, front_camera(), plain_settings(128));
    const Image third = a->render(fs, res, front_camera(), plain_settings(128));
    CHECK(first.hash() == second.hash());
    CHECK(first.hash() == third.hash());
}
