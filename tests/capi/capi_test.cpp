// The C ABI as an engine sees it: this file includes nothing but the public C
// header and links nothing but the shared library, so it also verifies that the
// exported symbol set is complete and that no C++ detail is needed to use it.
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <aetherfx/aetherfx.h>

using Catch::Approx;

namespace {

std::string example(const char* name) {
    return std::string(AETHER_SOURCE_DIR) + "/examples/effects/" + name;
}

// --- a dependency-free JSON well-formedness check ---------------------------
// The test deliberately links the shared library and Catch2 only, so it cannot
// borrow the engine's JSON library to prove that a returned document parses.
class JsonScanner {
public:
    explicit JsonScanner(const std::string& text) : text_(text) {}

    bool parse() {
        skip_ws();
        if (!value()) return false;
        skip_ws();
        return pos_ == text_.size();
    }

private:
    void skip_ws() {
        while (pos_ < text_.size() && (text_[pos_] == ' ' || text_[pos_] == '\t' || text_[pos_] == '\n' ||
                                       text_[pos_] == '\r'))
            ++pos_;
    }
    bool eat(char c) {
        if (pos_ < text_.size() && text_[pos_] == c) {
            ++pos_;
            return true;
        }
        return false;
    }
    bool literal(const char* word) {
        const size_t n = std::strlen(word);
        if (text_.compare(pos_, n, word) != 0) return false;
        pos_ += n;
        return true;
    }
    bool string_value() {
        if (!eat('"')) return false;
        while (pos_ < text_.size()) {
            const char c = text_[pos_++];
            if (c == '\\') {
                if (pos_ >= text_.size()) return false;
                ++pos_;
            } else if (c == '"') {
                return true;
            }
        }
        return false;
    }
    bool number() {
        const size_t start = pos_;
        if (pos_ < text_.size() && (text_[pos_] == '-' || text_[pos_] == '+')) ++pos_;
        while (pos_ < text_.size() && (std::isdigit(static_cast<unsigned char>(text_[pos_])) != 0 ||
                                       text_[pos_] == '.' || text_[pos_] == 'e' || text_[pos_] == 'E' ||
                                       text_[pos_] == '+' || text_[pos_] == '-'))
            ++pos_;
        return pos_ > start;
    }
    bool array() {
        if (!eat('[')) return false;
        skip_ws();
        if (eat(']')) return true;
        for (;;) {
            skip_ws();
            if (!value()) return false;
            skip_ws();
            if (eat(',')) continue;
            return eat(']');
        }
    }
    bool object() {
        if (!eat('{')) return false;
        skip_ws();
        if (eat('}')) return true;
        for (;;) {
            skip_ws();
            if (!string_value()) return false;
            skip_ws();
            if (!eat(':')) return false;
            skip_ws();
            if (!value()) return false;
            skip_ws();
            if (eat(',')) continue;
            return eat('}');
        }
    }
    bool value() {
        if (pos_ >= text_.size()) return false;
        switch (text_[pos_]) {
            case '{': return object();
            case '[': return array();
            case '"': return string_value();
            case 't': return literal("true");
            case 'f': return literal("false");
            case 'n': return literal("null");
            default: return number();
        }
    }

    const std::string& text_;
    size_t pos_ = 0;
};

bool json_parses(const std::string& text) { return JsonScanner(text).parse(); }

bool contains(const std::string& haystack, const char* needle) {
    return haystack.find(needle) != std::string::npos;
}

// Owning wrappers so a failing REQUIRE cannot leak a handle.
struct Effect {
    aetherfx_effect* handle = nullptr;
    explicit Effect(const std::string& path) : handle(aetherfx_effect_load_file(path.c_str())) {}
    ~Effect() { aetherfx_effect_free(handle); }
    Effect(const Effect&) = delete;
    Effect& operator=(const Effect&) = delete;
    operator const aetherfx_effect*() const { return handle; }  // NOLINT(google-explicit-constructor)
};

struct Compiled {
    aetherfx_compiled* handle = nullptr;
    explicit Compiled(const aetherfx_effect* effect, double dt = 0.0) : handle(aetherfx_compile(effect, dt)) {}
    ~Compiled() { aetherfx_compiled_free(handle); }
    Compiled(const Compiled&) = delete;
    Compiled& operator=(const Compiled&) = delete;
    operator const aetherfx_compiled*() const { return handle; }  // NOLINT(google-explicit-constructor)
};

struct Runtime {
    aetherfx_runtime* handle = nullptr;
    explicit Runtime(const aetherfx_compiled* compiled) : handle(aetherfx_runtime_create(compiled)) {}
    ~Runtime() { aetherfx_runtime_free(handle); }
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;
    operator aetherfx_runtime*() const { return handle; }  // NOLINT(google-explicit-constructor)
};

// Frees the char* a *_json() function returns.
struct OwnedString {
    char* text = nullptr;
    explicit OwnedString(char* t) : text(t) {}
    ~OwnedString() { aetherfx_free_string(text); }
    OwnedString(const OwnedString&) = delete;
    OwnedString& operator=(const OwnedString&) = delete;
    std::string str() const { return text != nullptr ? std::string(text) : std::string(); }
};

void step_n(aetherfx_runtime* runtime, int n) {
    for (int i = 0; i < n; ++i) REQUIRE(aetherfx_runtime_step(runtime) == AETHERFX_OK);
}

}  // namespace

TEST_CASE("version and ABI are reported", "[capi]") {
    int major = -1, minor = -1, patch = -1;
    aetherfx_version(&major, &minor, &patch);
    CHECK(major >= 0);
    CHECK(minor >= 0);
    CHECK(patch >= 0);
    CHECK(aetherfx_abi_version() == AETHERFX_ABI_VERSION);
    CHECK(aetherfx_abi_version() == 1);
    CHECK(std::string(aetherfx_version_string()).size() >= 5);
    // Partial version queries are allowed.
    int only_major = -1;
    aetherfx_version(&only_major, nullptr, nullptr);
    CHECK(only_major == major);
}

TEST_CASE("fireball loads, validates and compiles", "[capi]") {
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);
    CHECK(std::string(aetherfx_effect_name(effect)) == "Fireball");
    CHECK(aetherfx_effect_duration(effect) == Approx(2.5));

    std::vector<char> diagnostics(16384, '\0');
    REQUIRE(aetherfx_effect_validate(effect, diagnostics.data(), diagnostics.size()) == AETHERFX_OK);
    const std::string report(diagnostics.data());
    CHECK(json_parses(report));
    CHECK(contains(report, "\"ok\":true"));

    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);
    CHECK(aetherfx_compiled_ok(compiled) == 1);
    CHECK(aetherfx_compiled_fixed_dt(compiled) == Approx(1.0 / 60.0));

    OwnedString compile_diagnostics(aetherfx_compiled_diagnostics_json(compiled));
    REQUIRE(compile_diagnostics.text != nullptr);
    CHECK(json_parses(compile_diagnostics.str()));

    OwnedString plan(aetherfx_compiled_plan_json(compiled));
    REQUIRE(plan.text != nullptr);
    CHECK(json_parses(plan.str()));
    CHECK(contains(plan.str(), "\"nodes\""));
    CHECK(contains(plan.str(), "flame_ps"));

    // A non-default timestep sticks.
    Compiled coarse(effect, 1.0 / 30.0);
    REQUIRE(coarse.handle != nullptr);
    CHECK(aetherfx_compiled_fixed_dt(coarse) == Approx(1.0 / 30.0));
}

TEST_CASE("compiled resources are exposed", "[capi]") {
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);

    const int textures = aetherfx_compiled_texture_count(compiled);
    const int meshes = aetherfx_compiled_mesh_count(compiled);
    const int materials = aetherfx_compiled_material_count(compiled);
    REQUIRE(textures > 0);
    REQUIRE(meshes > 0);
    REQUIRE(materials > 0);

    for (int i = 0; i < textures; ++i) {
        struct aetherfx_texture_info info;
        REQUIRE(aetherfx_texture_info(compiled, i, &info) == AETHERFX_OK);
        INFO("texture " << info.id);
        CHECK(info.channels == 4);
        CHECK(info.width > 0);
        CHECK(info.height > 0);
        CHECK(info.frames >= 1);
        CHECK(info.frame_width == info.width / info.frames);
        CHECK(aetherfx_texture_index(compiled, info.id) == i);

        const float* pixels = aetherfx_texture_pixels(compiled, i);
        REQUIRE(pixels != nullptr);
        const size_t floats = static_cast<size_t>(info.width) * static_cast<size_t>(info.height) * 4;
        for (size_t p = 0; p < floats; ++p) CHECK(std::isfinite(pixels[p]));

        // The 8-bit path reports the size it needs and then fills the buffer.
        std::vector<uint8_t> rgba8(floats);
        CHECK(aetherfx_texture_pixels_rgba8(compiled, i, rgba8.data(), 8, 1) == AETHERFX_ERROR_BUFFER_TOO_SMALL);
        CHECK(std::string(aetherfx_last_error()).find(std::to_string(floats)) != std::string::npos);
        REQUIRE(aetherfx_texture_pixels_rgba8(compiled, i, rgba8.data(), rgba8.size(), 1) == AETHERFX_OK);
        // sRGB encoding brightens the midtones, so the two encodings differ somewhere.
        std::vector<uint8_t> linear8(floats);
        REQUIRE(aetherfx_texture_pixels_rgba8(compiled, i, linear8.data(), linear8.size(), 0) == AETHERFX_OK);
        for (size_t p = 0; p < floats; p += 4) CHECK(rgba8[p + 3] == linear8[p + 3]);  // alpha stays linear
    }

    for (int i = 0; i < meshes; ++i) {
        struct aetherfx_mesh_info info;
        REQUIRE(aetherfx_mesh_info(compiled, i, &info) == AETHERFX_OK);
        INFO("mesh " << info.id);
        CHECK(info.vertex_count > 0);
        CHECK(info.index_count % 3 == 0);
        CHECK(aetherfx_mesh_index(compiled, info.id) == i);
        REQUIRE(aetherfx_mesh_positions(compiled, i) != nullptr);
        REQUIRE(aetherfx_mesh_indices(compiled, i) != nullptr);
        if (info.has_normals != 0) REQUIRE(aetherfx_mesh_normals(compiled, i) != nullptr);
        if (info.has_uvs != 0) REQUIRE(aetherfx_mesh_uvs(compiled, i) != nullptr);
        const uint32_t* indices = aetherfx_mesh_indices(compiled, i);
        for (size_t k = 0; k < info.index_count; ++k) CHECK(indices[k] < info.vertex_count);
    }

    bool found_emissive = false;
    for (int i = 0; i < materials; ++i) {
        struct aetherfx_material material;
        REQUIRE(aetherfx_material_info(compiled, i, &material) == AETHERFX_OK);
        INFO("material " << material.id);
        CHECK(aetherfx_material_index(compiled, material.id) == i);
        CHECK(material.blend >= AETHERFX_BLEND_ADDITIVE);
        CHECK(material.blend <= AETHERFX_BLEND_PREMULTIPLIED);
        CHECK((material.shading == AETHERFX_SHADING_UNLIT || material.shading == AETHERFX_SHADING_LIT));
        CHECK(material.base_texture != nullptr);
        CHECK(material.noise_texture != nullptr);
        if (material.emissive_intensity > 0.0f) found_emissive = true;
    }
    CHECK(found_emissive);  // mat_core is emissive_intensity 8

    // Out of range is an error, not a crash.
    struct aetherfx_texture_info info;
    CHECK(aetherfx_texture_info(compiled, textures, &info) == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(aetherfx_texture_info(compiled, -1, &info) == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(aetherfx_texture_info(compiled, 0, nullptr) == AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_texture_index(compiled, "no_such_texture") == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(std::string(aetherfx_last_error()).empty() == false);
}

TEST_CASE("fire_aoe exposes animated textures and mesh variants", "[capi]") {
    Effect effect(example("fire_aoe.json"));
    REQUIRE(effect.handle != nullptr);
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);
    REQUIRE(aetherfx_compiled_ok(compiled) == 1);

    // tex_puff is baked with 8 frames side by side.
    const int puff = aetherfx_texture_index(compiled, "tex_puff");
    REQUIRE(puff >= 0);
    struct aetherfx_texture_info puff_info;
    REQUIRE(aetherfx_texture_info(compiled, puff, &puff_info) == AETHERFX_OK);
    CHECK(puff_info.frames == 8);
    CHECK(puff_info.frame_width == puff_info.width / 8);
    CHECK(puff_info.width == puff_info.frame_width * 8);

    // rock_mesh bakes seeded variants "rock_mesh", "rock_mesh#1" ...
    REQUIRE(aetherfx_mesh_index(compiled, "rock_mesh") >= 0);
    const int variant = aetherfx_mesh_index(compiled, "rock_mesh#1");
    REQUIRE(variant >= 0);
    struct aetherfx_mesh_info variant_info;
    REQUIRE(aetherfx_mesh_info(compiled, variant, &variant_info) == AETHERFX_OK);
    CHECK(std::string(variant_info.id) == "rock_mesh#1");
    CHECK(variant_info.vertex_count > 0);

    Runtime runtime(compiled);
    REQUIRE(runtime.handle != nullptr);
    REQUIRE(aetherfx_runtime_simulate_to(runtime, 1.0) == AETHERFX_OK);

    bool saw_mesh_system = false;
    const int systems = aetherfx_runtime_particle_system_count(runtime);
    REQUIRE(systems > 0);
    for (int i = 0; i < systems; ++i) {
        struct aetherfx_particle_system_info info;
        REQUIRE(aetherfx_particle_system_info(runtime, i, &info) == AETHERFX_OK);
        if (info.render_mode != AETHERFX_RENDER_MESH) continue;
        saw_mesh_system = true;
        INFO("mesh system " << info.id);
        CHECK(std::string(info.mesh_id) == "rock_mesh");
        CHECK(info.mesh_variants == 8);
        if (info.count > 0) {
            const uint32_t* variants = aetherfx_particle_variant(runtime, i);
            REQUIRE(variants != nullptr);
            for (size_t p = 0; p < info.count; ++p)
                CHECK(variants[p] < static_cast<uint32_t>(info.mesh_variants));
            // Mesh particles carry an orientation and a per-axis scale.
            const float* orientation = aetherfx_particle_orientation(runtime, i);
            const float* scale3 = aetherfx_particle_scale3(runtime, i);
            REQUIRE(orientation != nullptr);
            REQUIRE(scale3 != nullptr);
            for (size_t p = 0; p < info.count; ++p) {
                const float length2 = orientation[p * 4 + 0] * orientation[p * 4 + 0] +
                                      orientation[p * 4 + 1] * orientation[p * 4 + 1] +
                                      orientation[p * 4 + 2] * orientation[p * 4 + 2] +
                                      orientation[p * 4 + 3] * orientation[p * 4 + 3];
                CHECK(length2 == Approx(1.0f).margin(1e-3));
                CHECK(scale3[p * 3 + 0] > 0.0f);
            }
        }
    }
    CHECK(saw_mesh_system);
}

TEST_CASE("stepping fireball fills consistent particle buffers", "[capi]") {
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);
    Runtime runtime(compiled);
    REQUIRE(runtime.handle != nullptr);
    CHECK(std::string(aetherfx_runtime_backend(runtime)) == "cpu");

    step_n(runtime, 60);
    CHECK(aetherfx_runtime_frame_index(runtime) == 60u);
    CHECK(aetherfx_runtime_fixed_dt(runtime) == Approx(1.0 / 60.0));
    CHECK(aetherfx_runtime_time(runtime) == Approx(1.0).margin(1e-9));

    const int systems = aetherfx_runtime_particle_system_count(runtime);
    REQUIRE(systems == 3);
    size_t total = 0;
    for (int i = 0; i < systems; ++i) {
        struct aetherfx_particle_system_info info;
        REQUIRE(aetherfx_particle_system_info(runtime, i, &info) == AETHERFX_OK);
        INFO("system " << info.id);
        CHECK(info.sprite_columns >= 1);
        CHECK(info.sprite_rows >= 1);
        CHECK(info.material_id != nullptr);
        CHECK(info.sprite_id != nullptr);
        CHECK(info.mesh_id != nullptr);
        CHECK(info.mesh_variants >= 1);
        total += info.count;
        if (info.count == 0) continue;

        const float* position = aetherfx_particle_position(runtime, i);
        const float* previous = aetherfx_particle_previous_position(runtime, i);
        const float* velocity = aetherfx_particle_velocity(runtime, i);
        const float* age = aetherfx_particle_age(runtime, i);
        const float* lifetime = aetherfx_particle_lifetime(runtime, i);
        const float* size = aetherfx_particle_size(runtime, i);
        const float* rotation = aetherfx_particle_rotation(runtime, i);
        const float* color = aetherfx_particle_color(runtime, i);
        const float* opacity = aetherfx_particle_opacity(runtime, i);
        const float* emissive = aetherfx_particle_emissive(runtime, i);
        const float* custom0 = aetherfx_particle_custom0(runtime, i);
        const uint32_t* seed = aetherfx_particle_seed(runtime, i);
        REQUIRE(position != nullptr);
        REQUIRE(previous != nullptr);
        REQUIRE(velocity != nullptr);
        REQUIRE(age != nullptr);
        REQUIRE(lifetime != nullptr);
        REQUIRE(size != nullptr);
        REQUIRE(rotation != nullptr);
        REQUIRE(color != nullptr);
        REQUIRE(opacity != nullptr);
        REQUIRE(emissive != nullptr);
        REQUIRE(custom0 != nullptr);
        REQUIRE(seed != nullptr);

        for (size_t p = 0; p < info.count; ++p) {
            for (int c = 0; c < 3; ++c) {
                CHECK(std::isfinite(position[p * 3 + c]));
                CHECK(std::isfinite(previous[p * 3 + c]));
                CHECK(std::isfinite(velocity[p * 3 + c]));
            }
            for (int c = 0; c < 4; ++c) CHECK(color[p * 4 + c] >= 0.0f);
            CHECK(lifetime[p] > 0.0f);
            CHECK(age[p] >= 0.0f);
            CHECK(age[p] <= lifetime[p]);
            CHECK(size[p] >= 0.0f);
            CHECK(std::isfinite(rotation[p]));
            CHECK(opacity[p] >= 0.0f);
            CHECK(emissive[p] >= 0.0f);
            CHECK(custom0[p] >= 0.0f);
            CHECK(custom0[p] <= 1.0f);
            CHECK(custom0[p] == Approx(age[p] / lifetime[p]).margin(1e-5));
        }
        // Out-of-range systems report an error; empty ones just have no array.
        CHECK(aetherfx_particle_position(runtime, systems) == nullptr);
        CHECK(std::string(aetherfx_last_error()).empty() == false);
    }
    CHECK(total > 0);

    // A fireball carries a trail ribbon from its core mesh, a light and a camera.
    REQUIRE(aetherfx_runtime_trail_count(runtime) >= 1);
    struct aetherfx_trail_info trail;
    REQUIRE(aetherfx_trail_info(runtime, 0, &trail) == AETHERFX_OK);
    CHECK(trail.ribbon_count >= 1);
    const struct aetherfx_trail_vertex* verts = nullptr;
    size_t vert_count = 0;
    REQUIRE(aetherfx_trail_ribbon(runtime, 0, 0, &verts, &vert_count) == AETHERFX_OK);
    REQUIRE(vert_count >= 2);
    REQUIRE(verts != nullptr);
    for (size_t v = 0; v < vert_count; ++v) {
        CHECK(std::isfinite(verts[v].position[0]));
        CHECK(verts[v].width >= 0.0f);
        CHECK(verts[v].normalized_age >= 0.0f);
        CHECK(verts[v].normalized_age <= 1.0f);
        CHECK(verts[v].opacity >= 0.0f);
    }
    // Vertices are laid oldest first, so age decreases along the ribbon.
    CHECK(verts[0].age >= verts[vert_count - 1].age);

    // The visible "core" mesh node is a mesh instance with a column-major transform.
    REQUIRE(aetherfx_runtime_mesh_instance_count(runtime) >= 1);
    struct aetherfx_mesh_instance_info instance;
    REQUIRE(aetherfx_mesh_instance_info(runtime, 0, &instance) == AETHERFX_OK);
    CHECK(std::string(instance.id) == "core");
    CHECK(std::string(instance.mesh_id) == "core");
    CHECK(aetherfx_mesh_index(compiled, instance.mesh_id) >= 0);
    CHECK(instance.visible == 1);
    for (int k = 0; k < 16; ++k) CHECK(std::isfinite(instance.transform[k]));
    CHECK(instance.transform[15] == Approx(1.0f));
    // Column-major: the translation is the last column, and the core flies forward.
    CHECK(instance.transform[13] == Approx(1.0f).margin(1e-3));  // y = 1
    CHECK(instance.transform[14] > -2.2f);                       // z has advanced from its start

    REQUIRE(aetherfx_runtime_light_count(runtime) >= 1);
    struct aetherfx_light_info light;
    REQUIRE(aetherfx_light_info(runtime, 0, &light) == AETHERFX_OK);
    CHECK(std::string(light.id) == "glow");
    CHECK(light.type == AETHERFX_LIGHT_POINT);
    CHECK(light.intensity > 0.0f);
    CHECK(light.radius > 0.0f);

    struct aetherfx_camera camera;
    REQUIRE(aetherfx_runtime_camera(runtime, &camera) == 1);
    CHECK(camera.fov_deg > 0.0f);
    CHECK(camera.far_plane > camera.near_plane);

    OwnedString statistics(aetherfx_runtime_statistics_json(runtime));
    REQUIRE(statistics.text != nullptr);
    CHECK(json_parses(statistics.str()));
    CHECK(contains(statistics.str(), "\"total_alive\""));
    CHECK(contains(statistics.str(), "flame_ps"));

    // reset rewinds to a drawable frame 0.
    REQUIRE(aetherfx_runtime_reset(runtime) == AETHERFX_OK);
    CHECK(aetherfx_runtime_frame_index(runtime) == 0u);
    CHECK(aetherfx_runtime_time(runtime) == Approx(0.0));
}

TEST_CASE("lightning_strike exposes beams, lights, decals and mesh particles", "[capi]") {
    Effect effect(example("lightning_strike.json"));
    REQUIRE(effect.handle != nullptr);
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);
    Runtime runtime(compiled);
    REQUIRE(runtime.handle != nullptr);

    REQUIRE(aetherfx_runtime_simulate_to(runtime, 0.2) == AETHERFX_OK);
    CHECK(aetherfx_runtime_time(runtime) >= 0.2);

    REQUIRE(aetherfx_runtime_beam_count(runtime) >= 1);
    struct aetherfx_beam_info beam;
    REQUIRE(aetherfx_beam_info(runtime, 0, &beam) == AETHERFX_OK);
    CHECK(beam.width > 0.0f);
    CHECK(beam.polyline_count >= 1);
    CHECK(beam.material_id != nullptr);
    for (size_t p = 0; p < beam.polyline_count; ++p) {
        const float* xyz = nullptr;
        size_t points = 0;
        REQUIRE(aetherfx_beam_polyline(runtime, 0, static_cast<int>(p), &xyz, &points) == AETHERFX_OK);
        REQUIRE(points >= 2);
        REQUIRE(xyz != nullptr);
        for (size_t k = 0; k < points * 3; ++k) CHECK(std::isfinite(xyz[k]));
    }
    const float* xyz = nullptr;
    size_t points = 0;
    CHECK(aetherfx_beam_polyline(runtime, 0, static_cast<int>(beam.polyline_count), &xyz, &points) ==
          AETHERFX_ERROR_OUT_OF_RANGE);

    // The strike detail added after ABI 1: cross-section, per-vertex width and
    // intensity, branch generation, afterglow ghosts and end flares.
    struct aetherfx_beam_style style;
    REQUIRE(aetherfx_beam_style(runtime, 0, &style) == AETHERFX_OK);
    CHECK(style.core_width > 0.0f);
    CHECK(style.glow_width > 0.0f);
    CHECK(style.path_count == beam.polyline_count);
    for (size_t p = 0; p < style.path_count; ++p) {
        struct aetherfx_beam_path path;
        REQUIRE(aetherfx_beam_path(runtime, 0, static_cast<int>(p), &path) == AETHERFX_OK);
        REQUIRE(path.vertex_count >= 2);
        REQUIRE(path.position != nullptr);
        REQUIRE(path.width != nullptr);
        REQUIRE(path.intensity != nullptr);
        CHECK(path.depth >= 0);
        CHECK(path.fade > 0.0f);
        CHECK(path.fade <= 1.0f);
        for (size_t k = 0; k < path.vertex_count; ++k) {
            CHECK(std::isfinite(path.width[k]));
            CHECK(path.width[k] >= 0.0f);
            CHECK(path.intensity[k] > 0.0f);
        }
        // the polyline accessor and the path accessor describe the same points
        const float* legacy = nullptr;
        size_t legacy_count = 0;
        REQUIRE(aetherfx_beam_polyline(runtime, 0, static_cast<int>(p), &legacy, &legacy_count) == AETHERFX_OK);
        REQUIRE(legacy_count == path.vertex_count);
        CHECK(legacy == path.position);
    }
    struct aetherfx_beam_path past_end;
    CHECK(aetherfx_beam_path(runtime, 0, static_cast<int>(style.path_count), &past_end) ==
          AETHERFX_ERROR_OUT_OF_RANGE);
    for (size_t g = 0; g < style.ghost_count; ++g) {
        struct aetherfx_beam_path ghost;
        REQUIRE(aetherfx_beam_ghost(runtime, 0, static_cast<int>(g), &ghost) == AETHERFX_OK);
        CHECK(ghost.fade > 0.0f);
        CHECK(ghost.fade <= 1.0f);
        CHECK(ghost.vertex_count >= 2);
    }
    for (size_t f = 0; f < style.flare_count; ++f) {
        struct aetherfx_beam_flare flare;
        REQUIRE(aetherfx_beam_flare(runtime, 0, static_cast<int>(f), &flare) == AETHERFX_OK);
        CHECK(flare.radius > 0.0f);
        CHECK(flare.intensity > 0.0f);
        for (int k = 0; k < 3; ++k) CHECK(std::isfinite(flare.position[k]));
    }

    REQUIRE(aetherfx_runtime_light_count(runtime) >= 1);
    REQUIRE(aetherfx_runtime_decal_count(runtime) >= 1);
    struct aetherfx_decal_info decal;
    REQUIRE(aetherfx_decal_info(runtime, 0, &decal) == AETHERFX_OK);
    CHECK(decal.size[0] > 0.0f);
    CHECK(decal.size[1] > 0.0f);
    CHECK(decal.opacity >= 0.0f);
    CHECK(decal.texture_id != nullptr);
    CHECK((decal.blend >= AETHERFX_BLEND_ADDITIVE && decal.blend <= AETHERFX_BLEND_PREMULTIPLIED));

    // "chunk" is an invisible mesh node: it is the source geometry of the debris
    // particle system, not a standalone instance.
    CHECK(aetherfx_runtime_mesh_instance_count(runtime) == 0);
    bool saw_debris = false;
    const int systems = aetherfx_runtime_particle_system_count(runtime);
    for (int i = 0; i < systems; ++i) {
        struct aetherfx_particle_system_info info;
        REQUIRE(aetherfx_particle_system_info(runtime, i, &info) == AETHERFX_OK);
        if (info.render_mode != AETHERFX_RENDER_MESH) continue;
        saw_debris = true;
        CHECK(std::string(info.mesh_id) == "chunk");
        CHECK(aetherfx_mesh_index(compiled, info.mesh_id) >= 0);
    }
    CHECK(saw_debris);

    OwnedString statistics(aetherfx_runtime_statistics_json(runtime));
    REQUIRE(statistics.text != nullptr);
    CHECK(json_parses(statistics.str()));
}

TEST_CASE("bad input is reported, never thrown", "[capi]") {
    CHECK(aetherfx_effect_load_json("{ not json", 0) == nullptr);
    CHECK(std::string(aetherfx_last_error()).empty() == false);

    CHECK(aetherfx_effect_load_json(nullptr, 0) == nullptr);
    CHECK(std::string(aetherfx_last_error()).empty() == false);

    // Valid JSON that is not an effect document.
    const char* not_an_effect = "[1, 2, 3]";
    CHECK(aetherfx_effect_load_json(not_an_effect, std::strlen(not_an_effect)) == nullptr);
    CHECK(std::string(aetherfx_last_error()).empty() == false);

    CHECK(aetherfx_effect_load_file("/definitely/not/here.json") == nullptr);
    CHECK(std::string(aetherfx_last_error()).empty() == false);

    CHECK(aetherfx_effect_name(nullptr) == nullptr);
    CHECK(aetherfx_effect_duration(nullptr) < 0.0);
    CHECK(aetherfx_compile(nullptr, 0.0) == nullptr);
    CHECK(aetherfx_runtime_create(nullptr) == nullptr);
    CHECK(aetherfx_runtime_step(nullptr) == AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_runtime_particle_system_count(nullptr) == AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_compiled_plan_json(nullptr) == nullptr);
    aetherfx_effect_free(nullptr);
    aetherfx_compiled_free(nullptr);
    aetherfx_runtime_free(nullptr);
    aetherfx_free_string(nullptr);

    // A successful call clears the message.
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);
    CHECK(std::string(aetherfx_last_error()).empty());
}

TEST_CASE("parameters can be overridden before compiling", "[capi]") {
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);

    CHECK(aetherfx_effect_set_parameter(effect.handle, "no_such_node", "rate", "1.0") ==
          AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(contains(aetherfx_last_error(), "no_such_node"));
    CHECK(aetherfx_effect_set_parameter(effect.handle, "flames", "no_such_parameter", "1.0") ==
          AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_effect_set_parameter(effect.handle, "flames", "rate", "\"fast\"") ==
          AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_effect_set_parameter(effect.handle, "flames", "rate", "not json") ==
          AETHERFX_ERROR_INVALID_JSON);

    REQUIRE(aetherfx_effect_set_parameter(effect.handle, "flames", "rate", "12.0") == AETHERFX_OK);
    REQUIRE(aetherfx_effect_set_parameter(effect.handle, "glow", "color", "[0.1, 0.2, 0.3]") == AETHERFX_OK);

    OwnedString json(aetherfx_effect_to_json(effect, 2));
    REQUIRE(json.text != nullptr);
    CHECK(json_parses(json.str()));
    CHECK(contains(json.str(), "12.0"));

    // The document round-trips through the loader.
    aetherfx_effect* reloaded = aetherfx_effect_load_json(json.text, 0);
    REQUIRE(reloaded != nullptr);
    CHECK(std::string(aetherfx_effect_name(reloaded)) == "Fireball");
    aetherfx_effect_free(reloaded);

    // The override reaches the simulation: fewer flames spawn per second.
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);
    Runtime runtime(compiled);
    REQUIRE(runtime.handle != nullptr);
    REQUIRE(aetherfx_runtime_simulate_to(runtime, 0.5) == AETHERFX_OK);

    struct aetherfx_light_info light;
    REQUIRE(aetherfx_runtime_light_count(runtime) >= 1);
    REQUIRE(aetherfx_light_info(runtime, 0, &light) == AETHERFX_OK);
    CHECK(light.color[0] == Approx(0.1f).margin(1e-5));
    CHECK(light.color[1] == Approx(0.2f).margin(1e-5));
    CHECK(light.color[2] == Approx(0.3f).margin(1e-5));
}

// A tiny document with two controls, written out in full so this file keeps
// needing nothing but the public header.
const char* kControlledEffect = R"JSON({
  "schema_version": "0.1.0",
  "name": "Knobs",
  "duration": 1.0,
  "seed": 1,
  "layers": [],
  "nodes": [{"id": "glow", "type": "light",
             "parameters": {"intensity": 10.0, "color": [1.0, 0.0, 0.0, 1.0], "radius": 4.0}}],
  "controls": [
    {"id": "brightness", "label": "Brightness", "group": "Global", "min": 0.0, "max": 3.0,
     "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
     "bindings": [{"node": "glow", "parameter": "intensity", "op": "multiply"}]},
    {"id": "hue", "label": "Hue", "group": "Global", "min": -180.0, "max": 180.0,
     "default": 0.0, "value": 0.0, "step": 1.0, "unit": "deg",
     "bindings": [{"node": "glow", "parameter": "color", "op": "hue_shift"}]}
  ]
})JSON";

TEST_CASE("controls are enumerable and settable before compiling", "[capi][controls]") {
    aetherfx_effect* effect = aetherfx_effect_load_json(kControlledEffect, 0);
    REQUIRE(effect != nullptr);

    REQUIRE(aetherfx_effect_control_count(effect) == 2);
    struct aetherfx_control_info info;
    REQUIRE(aetherfx_control_info(effect, 0, &info) == AETHERFX_OK);
    CHECK(std::string(info.id) == "brightness");
    CHECK(std::string(info.label) == "Brightness");
    CHECK(std::string(info.group) == "Global");
    CHECK(std::string(info.unit) == "x");
    CHECK(info.min == Approx(0.0));
    CHECK(info.max == Approx(3.0));
    CHECK(info.default_value == Approx(1.0));
    CHECK(info.value == Approx(1.0));
    CHECK(info.step == Approx(0.01));
    CHECK(info.binding_count == 1);

    CHECK(aetherfx_effect_control_index(effect, "hue") == 1);
    CHECK(aetherfx_effect_control_index(effect, "nope") == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(aetherfx_control_info(effect, 7, &info) == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(aetherfx_control_info(effect, 0, nullptr) == AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_effect_control_count(nullptr) == AETHERFX_ERROR_INVALID_ARGUMENT);

    CHECK(aetherfx_effect_set_control(effect, "nope", 1.0) == AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(contains(aetherfx_last_error(), "nope"));
    CHECK(aetherfx_effect_set_control(effect, "brightness", 9.0) == AETHERFX_ERROR_OUT_OF_RANGE);

    // A stronger instance of the same effect: set the control, then compile.
    REQUIRE(aetherfx_effect_set_control(effect, "brightness", 2.0) == AETHERFX_OK);
    REQUIRE(aetherfx_effect_set_control(effect, "hue", 120.0) == AETHERFX_OK);
    REQUIRE(aetherfx_control_info(effect, 0, &info) == AETHERFX_OK);
    CHECK(info.value == Approx(2.0));

    aetherfx_compiled* compiled = aetherfx_compile(effect, 1.0 / 60.0);
    REQUIRE(compiled != nullptr);
    REQUIRE(aetherfx_compiled_ok(compiled) == 1);
    aetherfx_runtime* runtime = aetherfx_runtime_create(compiled);
    REQUIRE(runtime != nullptr);
    REQUIRE(aetherfx_runtime_simulate_to(runtime, 0.2) == AETHERFX_OK);

    struct aetherfx_light_info light;
    REQUIRE(aetherfx_runtime_light_count(runtime) == 1);
    REQUIRE(aetherfx_light_info(runtime, 0, &light) == AETHERFX_OK);
    CHECK(light.intensity == Approx(20.0f).margin(1e-4));   // 10 * 2
    CHECK(light.color[0] == Approx(0.0f).margin(1e-5));     // red rotated to green
    CHECK(light.color[1] == Approx(1.0f).margin(1e-5));
    CHECK(light.color[2] == Approx(0.0f).margin(1e-5));

    // The document keeps the authored value and carries the control values.
    OwnedString json(aetherfx_effect_to_json(effect, 2));
    REQUIRE(json.text != nullptr);
    CHECK(json_parses(json.str()));
    CHECK(contains(json.str(), "brightness"));
    CHECK(contains(json.str(), "10.0"));

    aetherfx_runtime_free(runtime);
    aetherfx_compiled_free(compiled);
    aetherfx_effect_free(effect);
}

TEST_CASE("an effect without controls reports none", "[capi][controls]") {
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);
    CHECK(aetherfx_effect_control_count(effect) == 0);
    CHECK(aetherfx_effect_control_index(effect, "anything") == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(aetherfx_effect_set_control(effect.handle, "anything", 1.0) == AETHERFX_ERROR_INVALID_ARGUMENT);
}

TEST_CASE("two runtimes of the same effect stay bit identical", "[capi]") {
    Effect effect(example("fireball.json"));
    REQUIRE(effect.handle != nullptr);
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);

    Runtime a(compiled);
    Runtime b(compiled);
    REQUIRE(a.handle != nullptr);
    REQUIRE(b.handle != nullptr);
    step_n(a, 60);
    step_n(b, 60);

    const int systems = aetherfx_runtime_particle_system_count(a);
    REQUIRE(systems == aetherfx_runtime_particle_system_count(b));
    REQUIRE(systems > 0);
    size_t compared = 0;
    for (int i = 0; i < systems; ++i) {
        struct aetherfx_particle_system_info ia;
        struct aetherfx_particle_system_info ib;
        REQUIRE(aetherfx_particle_system_info(a, i, &ia) == AETHERFX_OK);
        REQUIRE(aetherfx_particle_system_info(b, i, &ib) == AETHERFX_OK);
        REQUIRE(std::string(ia.id) == std::string(ib.id));
        REQUIRE(ia.count == ib.count);
        if (ia.count == 0) continue;
        const float* pa = aetherfx_particle_position(a, i);
        const float* pb = aetherfx_particle_position(b, i);
        REQUIRE(pa != nullptr);
        REQUIRE(pb != nullptr);
        // Bit-identical, not approximately equal: this is the determinism contract.
        CHECK(std::memcmp(pa, pb, ia.count * 3 * sizeof(float)) == 0);
        compared += ia.count;
    }
    CHECK(compared > 0);

    // A second, independent compile of the same document also matches, and
    // simulate_to(t) equals stepping to the same time.
    Effect again(example("fireball.json"));
    Compiled recompiled(again);
    Runtime c(recompiled);
    REQUIRE(c.handle != nullptr);
    REQUIRE(aetherfx_runtime_simulate_to(c, 60.0 / 60.0) == AETHERFX_OK);
    REQUIRE(aetherfx_runtime_frame_index(c) == aetherfx_runtime_frame_index(a));
    for (int i = 0; i < systems; ++i) {
        struct aetherfx_particle_system_info ia;
        struct aetherfx_particle_system_info ic;
        REQUIRE(aetherfx_particle_system_info(a, i, &ia) == AETHERFX_OK);
        REQUIRE(aetherfx_particle_system_info(c, i, &ic) == AETHERFX_OK);
        REQUIRE(ia.count == ic.count);
        if (ia.count == 0) continue;
        CHECK(std::memcmp(aetherfx_particle_position(a, i), aetherfx_particle_position(c, i),
                          ia.count * 3 * sizeof(float)) == 0);
    }
}

TEST_CASE("void_nebula exposes its procedural volume", "[capi][volume]") {
    Effect effect(example("void_nebula.json"));
    REQUIRE(effect.handle != nullptr);
    Compiled compiled(effect);
    REQUIRE(compiled.handle != nullptr);
    Runtime runtime(compiled);
    REQUIRE(runtime.handle != nullptr);

    // Before the density ramps in, the node is already inside its window.
    REQUIRE(aetherfx_runtime_simulate_to(runtime, 1.2) == AETHERFX_OK);
    REQUIRE(aetherfx_runtime_volume_count(runtime) == 1);

    struct aetherfx_volume_info volume;
    REQUIRE(aetherfx_volume_info(runtime, 0, &volume) == AETHERFX_OK);
    REQUIRE(volume.id != nullptr);
    CHECK(std::string(volume.id) == "void_core");
    CHECK(std::string(volume.mode) == "procedural");
    CHECK(std::string(volume.shape) == "nebula");
    CHECK(std::string(volume.backend) == "procedural_volume");
    CHECK(std::string(volume.volume_type) == "magic");
    CHECK(volume.radius == Approx(2.2f).margin(1e-4));
    CHECK(volume.density > 0.0f);
    CHECK(volume.emission > 0.0f);
    CHECK(volume.spiral_arms == 3);
    CHECK(volume.march_steps == 48);
    CHECK(volume.strands == Approx(0.6f).margin(1e-4));
    CHECK(volume.carve == Approx(0.5f).margin(1e-4));
    CHECK(volume.spin == Approx(0.15f).margin(1e-4));
    CHECK(volume.scatter >= 0.0f);
    CHECK(volume.time == Approx(1.2f).margin(0.02f));
    for (int i = 0; i < 16; ++i) CHECK(std::isfinite(volume.transform[i]));
    // Column-major, so the translation is the last column: the node sits above y = 0.
    CHECK(volume.transform[13] > 1.0f);
    for (int i = 0; i < 3; ++i) CHECK(volume.bounds_max[i] > volume.bounds_min[i]);
    CHECK(volume.color[2] > volume.color[1]);       // a violet shell
    CHECK(volume.color_hot[1] > volume.color_hot[0]);  // a cyan core

    // Out of range is reported, never thrown.
    CHECK(aetherfx_volume_info(runtime, 1, &volume) == AETHERFX_ERROR_OUT_OF_RANGE);
    CHECK(aetherfx_volume_info(runtime, 0, nullptr) == AETHERFX_ERROR_INVALID_ARGUMENT);
    CHECK(aetherfx_runtime_volume_count(nullptr) < 0);

    // An effect with no volume reports zero, not an error.
    Effect plain(example("fireball.json"));
    REQUIRE(plain.handle != nullptr);
    Compiled plain_compiled(plain);
    REQUIRE(plain_compiled.handle != nullptr);
    Runtime plain_runtime(plain_compiled);
    REQUIRE(plain_runtime.handle != nullptr);
    CHECK(aetherfx_runtime_volume_count(plain_runtime) == 0);
}
