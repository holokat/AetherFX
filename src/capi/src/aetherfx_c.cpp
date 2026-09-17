// Implementation of the AetherFX C ABI (src/capi/include/aetherfx/aetherfx.h).
//
// Every exported function is a thin, noexcept wrapper around the C++ objects:
// aether::Effect, aether::compiler::CompiledEffect and aether::sim::IRuntime.
// Nothing here simulates anything; the engines that link this library get the
// exact same runtime the studio and the CLI drive.
//
// Boundary rules enforced in this file:
//   * no exception escapes: every body runs inside guard_status/guard_value,
//     which records a thread-local message and maps the exception to a status.
//   * pointers handed out point into objects the library owns (frame state
//     vectors, baked resources, std::string storage), never into temporaries.
//   * the structure-of-arrays frame state is exposed by casting the engine's
//     Vec3/Vec4/Color/TrailVertex arrays to float arrays; the static_asserts
//     below are what make that legal.

#include "aetherfx/aetherfx.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/controls.hpp"
#include "aether/core/effect.hpp"
#include "aether/core/error.hpp"
#include "aether/core/frame_state.hpp"
#include "aether/core/resources.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"
#include "aether/sim/runtime.hpp"

// Version text ("0.1.0") built from the macros in the public header, so the
// two can never drift apart.
#define AETHERFX_STRINGIFY_(x) #x
#define AETHERFX_STRINGIFY(x) AETHERFX_STRINGIFY_(x)
#define AETHERFX_VERSION_STRING                                                       \
    AETHERFX_STRINGIFY(AETHERFX_VERSION_MAJOR) "." AETHERFX_STRINGIFY(AETHERFX_VERSION_MINOR) \
    "." AETHERFX_STRINGIFY(AETHERFX_VERSION_PATCH)

// --- layout contracts -------------------------------------------------------
// The C API hands out the engine's own arrays. These asserts fail the build if
// a math type ever grows padding or a member, instead of silently shipping a
// wrong stride to every engine.
static_assert(sizeof(aether::Vec2) == 2 * sizeof(float), "Vec2 must be two tight floats");
static_assert(sizeof(aether::Vec3) == 3 * sizeof(float), "Vec3 must be three tight floats");
static_assert(sizeof(aether::Vec4) == 4 * sizeof(float), "Vec4 must be four tight floats");
static_assert(sizeof(aether::Color) == 4 * sizeof(float), "Color must be four tight floats");
static_assert(sizeof(aether::Mat4) == 16 * sizeof(float), "Mat4 must be sixteen tight floats");
static_assert(std::is_standard_layout<aether::TrailVertex>::value, "TrailVertex must be standard layout");
static_assert(std::is_standard_layout<struct aetherfx_trail_vertex>::value, "aetherfx_trail_vertex must be POD-ish");
static_assert(sizeof(aether::TrailVertex) == sizeof(struct aetherfx_trail_vertex), "trail vertex size mismatch");
static_assert(alignof(aether::TrailVertex) == alignof(struct aetherfx_trail_vertex), "trail vertex alignment mismatch");
static_assert(offsetof(aether::TrailVertex, position) == offsetof(struct aetherfx_trail_vertex, position), "position");
static_assert(offsetof(aether::TrailVertex, width) == offsetof(struct aetherfx_trail_vertex, width), "width");
static_assert(offsetof(aether::TrailVertex, age) == offsetof(struct aetherfx_trail_vertex, age), "age");
static_assert(offsetof(aether::TrailVertex, normalized_age) == offsetof(struct aetherfx_trail_vertex, normalized_age),
              "normalized_age");
static_assert(offsetof(aether::TrailVertex, u) == offsetof(struct aetherfx_trail_vertex, u), "u");
static_assert(offsetof(aether::TrailVertex, color) == offsetof(struct aetherfx_trail_vertex, color), "color");
static_assert(offsetof(aether::TrailVertex, opacity) == offsetof(struct aetherfx_trail_vertex, opacity), "opacity");
static_assert(offsetof(aether::TrailVertex, emissive) == offsetof(struct aetherfx_trail_vertex, emissive), "emissive");

// --- handles ----------------------------------------------------------------

struct aetherfx_effect {
    aether::Effect effect;
};

struct aetherfx_compiled {
    aether::compiler::CompiledEffect compiled;
    // Index -> entry of the ResourceSet maps. std::map keys and values are
    // node-stable, so these pointers (and the ids behind them) stay valid for
    // the lifetime of the handle. Order is the map order: sorted by id.
    std::vector<const std::pair<const std::string, aether::TextureResource>*> textures;
    std::vector<const std::pair<const std::string, aether::MeshData>*> meshes;
    std::vector<const std::pair<const std::string, aether::MaterialDesc>*> materials;

    void index_resources() {
        textures.clear();
        meshes.clear();
        materials.clear();
        textures.reserve(compiled.resources.textures.size());
        meshes.reserve(compiled.resources.meshes.size());
        materials.reserve(compiled.resources.materials.size());
        for (const auto& entry : compiled.resources.textures) textures.push_back(&entry);
        for (const auto& entry : compiled.resources.meshes) meshes.push_back(&entry);
        for (const auto& entry : compiled.resources.materials) materials.push_back(&entry);
    }
};

struct aetherfx_runtime {
    std::unique_ptr<aether::sim::IRuntime> runtime;
};

namespace {

using aether::Color;
using aether::Vec2;
using aether::Vec3;

// --- errors -----------------------------------------------------------------

// A failure raised by this layer itself (bad handle, bad index, small buffer),
// carrying the status the caller should see.
struct CapiError : std::runtime_error {
    CapiError(int status_code, const std::string& message) : std::runtime_error(message), status(status_code) {}
    int status;
};

thread_local std::string g_last_error;

void set_error(const std::string& message) { g_last_error = message; }

// Maps an aether::Error code (docs/VOCABULARY.md) onto the C status set.
int status_for_code(const std::string& code) {
    if (code == "E100") return AETHERFX_ERROR_RUNTIME;
    if (code == "E020" || code == "bad_json") return AETHERFX_ERROR_INVALID_JSON;
    if (code == "io_error") return AETHERFX_ERROR_IO;
    // Everything else Error is used for is bad input at an API boundary.
    return AETHERFX_ERROR_INVALID_ARGUMENT;
}

// Called from inside a catch block: classifies the in-flight exception, records
// its message and returns the status for it.
int record_error() noexcept {
    try {
        throw;
    } catch (const CapiError& e) {
        set_error(e.what());
        return e.status;
    } catch (const aether::Error& e) {
        set_error(e.code() + ": " + e.what());
        return status_for_code(e.code());
    } catch (const nlohmann::json::exception& e) {
        set_error(std::string("invalid JSON: ") + e.what());
        return AETHERFX_ERROR_INVALID_JSON;
    } catch (const std::bad_alloc&) {
        set_error("out of memory");
        return AETHERFX_ERROR_INTERNAL;
    } catch (const std::exception& e) {
        set_error(e.what());
        return AETHERFX_ERROR_INTERNAL;
    } catch (...) {
        set_error("unknown error");
        return AETHERFX_ERROR_INTERNAL;
    }
}

// Body wrapper for functions that return a status (or a count).
template <class Fn>
int guard_status(Fn&& body) noexcept {
    try {
        g_last_error.clear();
        return body();
    } catch (...) {
        return record_error();
    }
}

// Body wrapper for functions that return a pointer or a number.
template <class T, class Fn>
T guard_value(Fn&& body, T on_error) noexcept {
    try {
        g_last_error.clear();
        return body();
    } catch (...) {
        record_error();
        return on_error;
    }
}

// --- argument checking ------------------------------------------------------

template <class T>
const T* require(const T* handle, const char* what) {
    if (handle == nullptr) throw CapiError(AETHERFX_ERROR_INVALID_ARGUMENT, std::string(what) + " must not be NULL");
    return handle;
}

template <class T>
T* require_mutable(T* handle, const char* what) {
    if (handle == nullptr) throw CapiError(AETHERFX_ERROR_INVALID_ARGUMENT, std::string(what) + " must not be NULL");
    return handle;
}

const char* require_text(const char* text, const char* what) {
    if (text == nullptr) throw CapiError(AETHERFX_ERROR_INVALID_ARGUMENT, std::string(what) + " must not be NULL");
    return text;
}

size_t require_index(int index, size_t size, const char* what) {
    if (index < 0 || static_cast<size_t>(index) >= size)
        throw CapiError(AETHERFX_ERROR_OUT_OF_RANGE, std::string(what) + " index " + std::to_string(index) +
                                                         " is outside [0, " + std::to_string(size) + ")");
    return static_cast<size_t>(index);
}

int to_count(size_t n) {
    if (n > static_cast<size_t>(std::numeric_limits<int>::max()))
        throw CapiError(AETHERFX_ERROR_INTERNAL, "count does not fit in an int");
    return static_cast<int>(n);
}

// --- small conversions ------------------------------------------------------

int blend_to_c(aether::BlendMode blend) {
    switch (blend) {
        case aether::BlendMode::Additive: return AETHERFX_BLEND_ADDITIVE;
        case aether::BlendMode::Alpha: return AETHERFX_BLEND_ALPHA;
        case aether::BlendMode::Premultiplied: return AETHERFX_BLEND_PREMULTIPLIED;
    }
    return AETHERFX_BLEND_ADDITIVE;
}

int render_mode_to_c(aether::RenderMode mode) {
    switch (mode) {
        case aether::RenderMode::Billboard: return AETHERFX_RENDER_BILLBOARD;
        case aether::RenderMode::StretchedBillboard: return AETHERFX_RENDER_STRETCHED_BILLBOARD;
        case aether::RenderMode::Mesh: return AETHERFX_RENDER_MESH;
        case aether::RenderMode::Ribbon: return AETHERFX_RENDER_RIBBON;
        case aether::RenderMode::None: return AETHERFX_RENDER_NONE;
    }
    return AETHERFX_RENDER_BILLBOARD;
}

int shading_to_c(aether::Shading shading) {
    return shading == aether::Shading::Lit ? AETHERFX_SHADING_LIT : AETHERFX_SHADING_UNLIT;
}

int light_type_to_c(aether::LightType type) {
    switch (type) {
        case aether::LightType::Point: return AETHERFX_LIGHT_POINT;
        case aether::LightType::Spot: return AETHERFX_LIGHT_SPOT;
        case aether::LightType::Area: return AETHERFX_LIGHT_AREA;
    }
    return AETHERFX_LIGHT_POINT;
}

void copy_vec3(float (&out)[3], Vec3 v) {
    out[0] = v.x;
    out[1] = v.y;
    out[2] = v.z;
}

void copy_vec2(float (&out)[2], Vec2 v) {
    out[0] = v.x;
    out[1] = v.y;
}

void copy_color(float (&out)[4], Color c) {
    out[0] = c.r;
    out[1] = c.g;
    out[2] = c.b;
    out[3] = c.a;
}

// C strings that point into the owning object's std::string storage.
const char* c_str(const std::string& s) { return s.c_str(); }

// Copies a JSON document into a malloc'd buffer for aetherfx_free_string().
char* alloc_string(const std::string& text) {
    char* out = static_cast<char*>(std::malloc(text.size() + 1));
    if (out == nullptr) throw std::bad_alloc();
    std::memcpy(out, text.c_str(), text.size() + 1);
    return out;
}

// --- accessors --------------------------------------------------------------

const aether::FrameState& state_of(const aetherfx_runtime* runtime) {
    return require(runtime, "runtime")->runtime->state();
}

const aether::ParticleBuffer& particles_of(const aetherfx_runtime* runtime, int index) {
    const aether::FrameState& state = state_of(runtime);
    return state.particles[require_index(index, state.particles.size(), "particle system")];
}

// A particle array getter: NULL for an empty system (no error), the array
// otherwise. `expected` guards against a buffer whose arrays got out of step.
template <class T>
const T* particle_array(const std::vector<T>& values, size_t expected) {
    if (values.empty()) return nullptr;
    if (values.size() != expected)
        throw CapiError(AETHERFX_ERROR_INTERNAL, "particle array has " + std::to_string(values.size()) +
                                                     " entries but the system holds " + std::to_string(expected));
    return values.data();
}

// Baked variants of a mesh particle system's mesh: "<mesh_id>", "<mesh_id>#1"...
int mesh_variants_of(const aether::sim::IRuntime& runtime, const std::string& mesh_id) {
    if (mesh_id.empty()) return 1;
    if (const aether::compiler::CompiledNode* node = runtime.compiled().find(mesh_id))
        return std::max(1, node->mesh_variants);
    return 1;
}

uint8_t to_unorm8(float linear, bool srgb_encode) {
    const float v = srgb_encode ? aether::linear_to_srgb(linear) : aether::saturate(linear);
    return static_cast<uint8_t>(aether::clamp(v * 255.0f + 0.5f, 0.0f, 255.0f));
}

}  // namespace

// ---------------------------------------------------------------------------
// Versioning and errors
// ---------------------------------------------------------------------------

void aetherfx_version(int* major, int* minor, int* patch) {
    if (major != nullptr) *major = AETHERFX_VERSION_MAJOR;
    if (minor != nullptr) *minor = AETHERFX_VERSION_MINOR;
    if (patch != nullptr) *patch = AETHERFX_VERSION_PATCH;
}

int aetherfx_abi_version(void) { return AETHERFX_ABI_VERSION; }

const char* aetherfx_version_string(void) { return AETHERFX_VERSION_STRING; }

const char* aetherfx_last_error(void) { return g_last_error.c_str(); }

void aetherfx_free_string(char* text) { std::free(text); }

// ---------------------------------------------------------------------------
// Effects
// ---------------------------------------------------------------------------

aetherfx_effect* aetherfx_effect_load_json(const char* json_utf8, size_t len) {
    return guard_value<aetherfx_effect*>(
        [&]() {
            require_text(json_utf8, "json_utf8");
            const size_t size = len > 0 ? len : std::strlen(json_utf8);
            nlohmann::json document = nlohmann::json::parse(json_utf8, json_utf8 + size);
            aether::migrate_effect_json(document);
            auto handle = std::make_unique<aetherfx_effect>();
            handle->effect = aether::effect_from_json(document);
            return handle.release();
        },
        nullptr);
}

aetherfx_effect* aetherfx_effect_load_file(const char* path) {
    return guard_value<aetherfx_effect*>(
        [&]() {
            require_text(path, "path");
            auto handle = std::make_unique<aetherfx_effect>();
            handle->effect = aether::load_effect_file(path);
            return handle.release();
        },
        nullptr);
}

void aetherfx_effect_free(aetherfx_effect* effect) { delete effect; }

const char* aetherfx_effect_name(const aetherfx_effect* effect) {
    return guard_value<const char*>([&]() { return c_str(require(effect, "effect")->effect.name); }, nullptr);
}

double aetherfx_effect_duration(const aetherfx_effect* effect) {
    return guard_value<double>([&]() { return require(effect, "effect")->effect.duration; },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

double aetherfx_effect_time_scale(const aetherfx_effect* effect) {
    return guard_value<double>([&]() { return require(effect, "effect")->effect.time_scale; },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

int aetherfx_effect_validate(const aetherfx_effect* effect, char* buf, size_t cap) {
    return guard_status([&]() -> int {
        const aether::Diagnostics diagnostics = aether::validate(require(effect, "effect")->effect);
        const int status = diagnostics.ok() ? AETHERFX_OK : AETHERFX_ERROR_VALIDATION;
        if (buf == nullptr || cap == 0) return status;

        const std::string text = diagnostics.to_json().dump();
        if (text.size() + 1 > cap) {
            std::memcpy(buf, text.c_str(), cap - 1);
            buf[cap - 1] = '\0';
            set_error("diagnostics JSON needs " + std::to_string(text.size() + 1) + " bytes, got " +
                      std::to_string(cap));
            return AETHERFX_ERROR_BUFFER_TOO_SMALL;
        }
        std::memcpy(buf, text.c_str(), text.size() + 1);
        return status;
    });
}

int aetherfx_effect_set_parameter(aetherfx_effect* effect, const char* node_id, const char* name,
                                  const char* json_value) {
    return guard_status([&]() -> int {
        aetherfx_effect* handle = require_mutable(effect, "effect");
        require_text(node_id, "node_id");
        require_text(name, "name");
        require_text(json_value, "json_value");

        aether::Node* node = handle->effect.find_node(node_id);
        if (node == nullptr)
            throw aether::Error("E006", "unknown node \"" + std::string(node_id) + "\"");
        const aether::ParamSpec* spec = aether::SpecRegistry::instance().get(node->type).find_param(name);
        if (spec == nullptr)
            throw aether::Error("E004", "node \"" + std::string(node_id) + "\" of type \"" +
                                            std::string(aether::to_string(node->type)) + "\" has no parameter \"" +
                                            std::string(name) + "\"");

        const nlohmann::json value = nlohmann::json::parse(json_value);
        // Throws E005 with the expected type when the value does not fit.
        node->parameters[name] = aether::Parameter{aether::value_from_json(value, spec->type)};
        return AETHERFX_OK;
    });
}

char* aetherfx_effect_to_json(const aetherfx_effect* effect, int indent) {
    return guard_value<char*>(
        [&]() { return alloc_string(aether::effect_to_json(require(effect, "effect")->effect).dump(indent)); },
        nullptr);
}

// ---------------------------------------------------------------------------
// controls
// ---------------------------------------------------------------------------

int aetherfx_effect_control_count(const aetherfx_effect* effect) {
    return guard_status([&]() -> int { return to_count(require(effect, "effect")->effect.controls.size()); });
}

int aetherfx_control_info(const aetherfx_effect* effect, int index, struct aetherfx_control_info* out) {
    return guard_status([&]() -> int {
        const aetherfx_effect* handle = require(effect, "effect");
        require_mutable(out, "out");
        const std::vector<aether::Control>& controls = handle->effect.controls;
        const aether::Control& control = controls[require_index(index, controls.size(), "control")];
        out->id = c_str(control.id);
        out->label = c_str(control.label);
        out->group = c_str(control.group);
        out->unit = c_str(control.unit);
        out->min = control.min;
        out->max = control.max;
        out->default_value = control.default_value;
        out->value = control.value;
        out->step = control.step;
        out->binding_count = to_count(control.bindings.size());
        return AETHERFX_OK;
    });
}

int aetherfx_effect_control_index(const aetherfx_effect* effect, const char* id) {
    return guard_status([&]() -> int {
        const aetherfx_effect* handle = require(effect, "effect");
        require_text(id, "id");
        const std::vector<aether::Control>& controls = handle->effect.controls;
        for (size_t i = 0; i < controls.size(); ++i)
            if (controls[i].id == id) return static_cast<int>(i);
        throw CapiError(AETHERFX_ERROR_OUT_OF_RANGE, "no control with id \"" + std::string(id) + "\"");
    });
}

int aetherfx_effect_set_control(aetherfx_effect* effect, const char* id, double value) {
    return guard_status([&]() -> int {
        aetherfx_effect* handle = require_mutable(effect, "effect");
        require_text(id, "id");
        aether::Control* control = handle->effect.find_control(id);
        if (control == nullptr)
            throw aether::Error("E021", "unknown control \"" + std::string(id) + "\"");
        if (!(value >= control->min && value <= control->max))
            throw CapiError(AETHERFX_ERROR_OUT_OF_RANGE,
                            "control \"" + std::string(id) + "\" takes a value in [" + std::to_string(control->min) +
                                ", " + std::to_string(control->max) + "], got " + std::to_string(value));
        control->value = value;
        return AETHERFX_OK;
    });
}

// ---------------------------------------------------------------------------
// Compilation
// ---------------------------------------------------------------------------

aetherfx_compiled* aetherfx_compile(const aetherfx_effect* effect, double fixed_dt) {
    return guard_value<aetherfx_compiled*>(
        [&]() {
            const aetherfx_effect* handle = require(effect, "effect");
            aether::compiler::CompileOptions options;
            if (fixed_dt > 0.0) options.fixed_dt = fixed_dt;
            auto compiled = std::make_unique<aetherfx_compiled>();
            compiled->compiled = aether::compiler::compile(handle->effect, options);
            compiled->index_resources();
            return compiled.release();
        },
        nullptr);
}

void aetherfx_compiled_free(aetherfx_compiled* compiled) { delete compiled; }

int aetherfx_compiled_ok(const aetherfx_compiled* compiled) {
    return guard_status([&]() -> int { return require(compiled, "compiled")->compiled.ok() ? 1 : 0; });
}

double aetherfx_compiled_fixed_dt(const aetherfx_compiled* compiled) {
    return guard_value<double>([&]() { return require(compiled, "compiled")->compiled.fixed_dt; },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

double aetherfx_compiled_time_scale(const aetherfx_compiled* compiled) {
    return guard_value<double>([&]() { return require(compiled, "compiled")->compiled.time_scale(); },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

double aetherfx_compiled_wall_duration(const aetherfx_compiled* compiled) {
    return guard_value<double>([&]() { return require(compiled, "compiled")->compiled.wall_duration(); },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

char* aetherfx_compiled_diagnostics_json(const aetherfx_compiled* compiled) {
    return guard_value<char*>(
        [&]() { return alloc_string(require(compiled, "compiled")->compiled.diagnostics.to_json().dump()); }, nullptr);
}

char* aetherfx_compiled_plan_json(const aetherfx_compiled* compiled) {
    return guard_value<char*>([&]() { return alloc_string(require(compiled, "compiled")->compiled.plan_json().dump()); },
                              nullptr);
}

// ---------------------------------------------------------------------------
// Resources: textures
// ---------------------------------------------------------------------------

int aetherfx_compiled_texture_count(const aetherfx_compiled* compiled) {
    return guard_status([&]() -> int { return to_count(require(compiled, "compiled")->textures.size()); });
}

int aetherfx_texture_info(const aetherfx_compiled* compiled, int index, struct aetherfx_texture_info* out) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_mutable(out, "out");
        const auto& entry = *handle->textures[require_index(index, handle->textures.size(), "texture")];
        const aether::TextureResource& texture = entry.second;
        out->id = c_str(entry.first);
        out->width = texture.image.width;
        out->height = texture.image.height;
        out->frames = std::max(1, texture.frames);
        out->frame_width = texture.frame_width();
        out->channels = 4;
        return AETHERFX_OK;
    });
}

int aetherfx_texture_index(const aetherfx_compiled* compiled, const char* id) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_text(id, "id");
        for (size_t i = 0; i < handle->textures.size(); ++i)
            if (handle->textures[i]->first == id) return static_cast<int>(i);
        throw CapiError(AETHERFX_ERROR_OUT_OF_RANGE, "no texture with id \"" + std::string(id) + "\"");
    });
}

const float* aetherfx_texture_pixels(const aetherfx_compiled* compiled, int index) {
    return guard_value<const float*>(
        [&]() -> const float* {
            const aetherfx_compiled* handle = require(compiled, "compiled");
            const aether::Image& image = handle->textures[require_index(index, handle->textures.size(), "texture")]
                                            ->second.image;
            return image.rgba.empty() ? nullptr : image.rgba.data();
        },
        nullptr);
}

int aetherfx_texture_pixels_rgba8(const aetherfx_compiled* compiled, int index, uint8_t* out, size_t cap,
                                  int srgb_encode) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_mutable(out, "out");
        const aether::Image& image =
            handle->textures[require_index(index, handle->textures.size(), "texture")]->second.image;
        const size_t needed = image.pixel_count() * 4;
        if (cap < needed) {
            set_error("texture needs " + std::to_string(needed) + " bytes, got " + std::to_string(cap));
            return AETHERFX_ERROR_BUFFER_TOO_SMALL;
        }
        const bool encode = srgb_encode != 0;
        for (size_t p = 0; p < image.pixel_count(); ++p) {
            out[p * 4 + 0] = to_unorm8(image.rgba[p * 4 + 0], encode);
            out[p * 4 + 1] = to_unorm8(image.rgba[p * 4 + 1], encode);
            out[p * 4 + 2] = to_unorm8(image.rgba[p * 4 + 2], encode);
            out[p * 4 + 3] = to_unorm8(image.rgba[p * 4 + 3], false);  // alpha is always linear
        }
        return AETHERFX_OK;
    });
}

// ---------------------------------------------------------------------------
// Resources: meshes
// ---------------------------------------------------------------------------

int aetherfx_compiled_mesh_count(const aetherfx_compiled* compiled) {
    return guard_status([&]() -> int { return to_count(require(compiled, "compiled")->meshes.size()); });
}

int aetherfx_mesh_info(const aetherfx_compiled* compiled, int index, struct aetherfx_mesh_info* out) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_mutable(out, "out");
        const auto& entry = *handle->meshes[require_index(index, handle->meshes.size(), "mesh")];
        const aether::MeshData& mesh = entry.second;
        out->id = c_str(entry.first);
        out->vertex_count = mesh.positions.size();
        out->index_count = mesh.indices.size();
        out->has_normals = mesh.normals.size() == mesh.positions.size() && !mesh.normals.empty() ? 1 : 0;
        out->has_uvs = mesh.uvs.size() == mesh.positions.size() && !mesh.uvs.empty() ? 1 : 0;
        return AETHERFX_OK;
    });
}

int aetherfx_mesh_index(const aetherfx_compiled* compiled, const char* id) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_text(id, "id");
        for (size_t i = 0; i < handle->meshes.size(); ++i)
            if (handle->meshes[i]->first == id) return static_cast<int>(i);
        throw CapiError(AETHERFX_ERROR_OUT_OF_RANGE, "no mesh with id \"" + std::string(id) + "\"");
    });
}

const float* aetherfx_mesh_positions(const aetherfx_compiled* compiled, int index) {
    return guard_value<const float*>(
        [&]() -> const float* {
            const aetherfx_compiled* handle = require(compiled, "compiled");
            const aether::MeshData& mesh = handle->meshes[require_index(index, handle->meshes.size(), "mesh")]->second;
            return mesh.positions.empty() ? nullptr : &mesh.positions.front().x;
        },
        nullptr);
}

const float* aetherfx_mesh_normals(const aetherfx_compiled* compiled, int index) {
    return guard_value<const float*>(
        [&]() -> const float* {
            const aetherfx_compiled* handle = require(compiled, "compiled");
            const aether::MeshData& mesh = handle->meshes[require_index(index, handle->meshes.size(), "mesh")]->second;
            return mesh.normals.empty() ? nullptr : &mesh.normals.front().x;
        },
        nullptr);
}

const float* aetherfx_mesh_uvs(const aetherfx_compiled* compiled, int index) {
    return guard_value<const float*>(
        [&]() -> const float* {
            const aetherfx_compiled* handle = require(compiled, "compiled");
            const aether::MeshData& mesh = handle->meshes[require_index(index, handle->meshes.size(), "mesh")]->second;
            return mesh.uvs.empty() ? nullptr : &mesh.uvs.front().x;
        },
        nullptr);
}

const uint32_t* aetherfx_mesh_indices(const aetherfx_compiled* compiled, int index) {
    return guard_value<const uint32_t*>(
        [&]() -> const uint32_t* {
            const aetherfx_compiled* handle = require(compiled, "compiled");
            const aether::MeshData& mesh = handle->meshes[require_index(index, handle->meshes.size(), "mesh")]->second;
            return mesh.indices.empty() ? nullptr : mesh.indices.data();
        },
        nullptr);
}

// ---------------------------------------------------------------------------
// Resources: materials
// ---------------------------------------------------------------------------

int aetherfx_compiled_material_count(const aetherfx_compiled* compiled) {
    return guard_status([&]() -> int { return to_count(require(compiled, "compiled")->materials.size()); });
}

int aetherfx_material_info(const aetherfx_compiled* compiled, int index, struct aetherfx_material* out) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_mutable(out, "out");
        const auto& entry = *handle->materials[require_index(index, handle->materials.size(), "material")];
        const aether::MaterialDesc& material = entry.second;
        out->id = c_str(entry.first);
        out->blend = blend_to_c(material.blend);
        out->shading = shading_to_c(material.shading);
        copy_color(out->base_color, material.base_color);
        out->opacity = material.opacity;
        copy_color(out->emissive_color, material.emissive_color);
        out->emissive_intensity = material.emissive_intensity;
        out->fresnel_power = material.fresnel_power;
        out->dissolve = material.dissolve;
        out->erosion = material.erosion;
        out->soft_particle = material.soft_particle ? 1 : 0;
        out->depth_fade = material.depth_fade;
        out->base_texture = c_str(material.base_texture);
        out->noise_texture = c_str(material.noise_texture);
        return AETHERFX_OK;
    });
}

int aetherfx_material_index(const aetherfx_compiled* compiled, const char* id) {
    return guard_status([&]() -> int {
        const aetherfx_compiled* handle = require(compiled, "compiled");
        require_text(id, "id");
        for (size_t i = 0; i < handle->materials.size(); ++i)
            if (handle->materials[i]->first == id) return static_cast<int>(i);
        throw CapiError(AETHERFX_ERROR_OUT_OF_RANGE, "no material with id \"" + std::string(id) + "\"");
    });
}

// ---------------------------------------------------------------------------
// Runtime
// ---------------------------------------------------------------------------

aetherfx_runtime* aetherfx_runtime_create(const aetherfx_compiled* compiled) {
    return guard_value<aetherfx_runtime*>(
        [&]() {
            const aetherfx_compiled* handle = require(compiled, "compiled");
            auto runtime = std::make_unique<aetherfx_runtime>();
            runtime->runtime = aether::sim::create_cpu_runtime(handle->compiled);
            return runtime.release();
        },
        nullptr);
}

void aetherfx_runtime_free(aetherfx_runtime* runtime) { delete runtime; }

int aetherfx_runtime_reset(aetherfx_runtime* runtime) {
    return guard_status([&]() -> int {
        require_mutable(runtime, "runtime")->runtime->reset();
        return AETHERFX_OK;
    });
}

int aetherfx_runtime_step(aetherfx_runtime* runtime) {
    return guard_status([&]() -> int {
        require_mutable(runtime, "runtime")->runtime->step();
        return AETHERFX_OK;
    });
}

int aetherfx_runtime_simulate_to(aetherfx_runtime* runtime, double time) {
    return guard_status([&]() -> int {
        require_mutable(runtime, "runtime")->runtime->simulate_to(time);
        return AETHERFX_OK;
    });
}

double aetherfx_runtime_time(const aetherfx_runtime* runtime) {
    return guard_value<double>([&]() { return require(runtime, "runtime")->runtime->time(); },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

uint64_t aetherfx_runtime_frame_index(const aetherfx_runtime* runtime) {
    return guard_value<uint64_t>([&]() { return require(runtime, "runtime")->runtime->frame_index(); },
                                std::numeric_limits<uint64_t>::max());
}

double aetherfx_runtime_fixed_dt(const aetherfx_runtime* runtime) {
    return guard_value<double>([&]() { return require(runtime, "runtime")->runtime->fixed_dt(); },
                              static_cast<double>(AETHERFX_ERROR_INVALID_ARGUMENT));
}

const char* aetherfx_runtime_backend(const aetherfx_runtime* runtime) {
    // backend_name() returns by value, so the string is cached per runtime.
    static thread_local std::string backend;
    return guard_value<const char*>(
        [&]() {
            backend = require(runtime, "runtime")->runtime->backend_name();
            return backend.c_str();
        },
        nullptr);
}

char* aetherfx_runtime_statistics_json(const aetherfx_runtime* runtime) {
    return guard_value<char*>(
        [&]() { return alloc_string(require(runtime, "runtime")->runtime->statistics().to_json().dump()); }, nullptr);
}

// ---------------------------------------------------------------------------
// Frame state: particles
// ---------------------------------------------------------------------------

int aetherfx_runtime_particle_system_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).particles.size()); });
}

int aetherfx_particle_system_info(const aetherfx_runtime* runtime, int index,
                                  struct aetherfx_particle_system_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::ParticleBuffer& buffer = particles_of(runtime, index);
        out->id = c_str(buffer.system_id);
        out->count = buffer.count();
        out->render_mode = render_mode_to_c(buffer.render_mode);
        out->blend = blend_to_c(buffer.blend);
        out->material_id = c_str(buffer.material_id);
        out->sprite_id = c_str(buffer.sprite_id);
        out->mesh_id = c_str(buffer.mesh_id);
        out->mesh_variants = mesh_variants_of(*runtime->runtime, buffer.mesh_id);
        out->sprite_columns = std::max(1, buffer.sprite_columns);
        out->sprite_rows = std::max(1, buffer.sprite_rows);
        out->sprite_fps = buffer.sprite_fps;
        out->velocity_stretch = buffer.velocity_stretch;
        out->align_to_velocity = buffer.align_to_velocity ? 1 : 0;
        out->soft_particle_distance = buffer.soft_particle_distance;
        out->sort = buffer.sort ? 1 : 0;
        return AETHERFX_OK;
    });
}

// One accessor per array. They all follow the same shape: resolve the system,
// check the array length against the particle count, hand out the pointer.
#define AETHERFX_PARTICLE_FLOAT_ARRAY(fn, member, components)                                             \
    const float* fn(const aetherfx_runtime* runtime, int index) {                                         \
        return guard_value<const float*>(                                                                 \
            [&]() -> const float* {                                                                       \
                const aether::ParticleBuffer& buffer = particles_of(runtime, index);                      \
                const auto* first = particle_array(buffer.member, buffer.count());                        \
                static_assert(sizeof(*first) == (components) * sizeof(float), "component count mismatch"); \
                return first == nullptr ? nullptr : reinterpret_cast<const float*>(first);                \
            },                                                                                            \
            nullptr);                                                                                     \
    }

AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_position, position, 3)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_previous_position, previous_position, 3)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_velocity, velocity, 3)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_age, age, 1)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_lifetime, lifetime, 1)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_size, size, 1)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_rotation, rotation, 1)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_color, color, 4)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_opacity, opacity, 1)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_emissive, emissive, 1)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_orientation, orientation, 4)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_scale3, scale3, 3)
AETHERFX_PARTICLE_FLOAT_ARRAY(aetherfx_particle_custom0, custom0, 1)

#undef AETHERFX_PARTICLE_FLOAT_ARRAY

const uint32_t* aetherfx_particle_variant(const aetherfx_runtime* runtime, int index) {
    return guard_value<const uint32_t*>(
        [&]() -> const uint32_t* {
            const aether::ParticleBuffer& buffer = particles_of(runtime, index);
            return particle_array(buffer.variant, buffer.count());
        },
        nullptr);
}

const uint32_t* aetherfx_particle_seed(const aetherfx_runtime* runtime, int index) {
    return guard_value<const uint32_t*>(
        [&]() -> const uint32_t* {
            const aether::ParticleBuffer& buffer = particles_of(runtime, index);
            return particle_array(buffer.seed, buffer.count());
        },
        nullptr);
}

// ---------------------------------------------------------------------------
// Frame state: lights, decals, mesh instances
// ---------------------------------------------------------------------------

int aetherfx_runtime_light_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).lights.size()); });
}

int aetherfx_light_info(const aetherfx_runtime* runtime, int index, struct aetherfx_light_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::LightState& light = state.lights[require_index(index, state.lights.size(), "light")];
        out->id = c_str(light.id);
        out->type = light_type_to_c(light.type);
        copy_vec3(out->position, light.position);
        copy_vec3(out->direction, light.direction);
        copy_color(out->color, light.color);
        out->intensity = light.intensity;
        out->radius = light.radius;
        out->cone_angle_deg = light.cone_angle_deg;
        return AETHERFX_OK;
    });
}

int aetherfx_runtime_decal_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).decals.size()); });
}

int aetherfx_decal_info(const aetherfx_runtime* runtime, int index, struct aetherfx_decal_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::DecalState& decal = state.decals[require_index(index, state.decals.size(), "decal")];
        out->id = c_str(decal.id);
        copy_vec3(out->position, decal.position);
        copy_vec3(out->normal, decal.normal);
        copy_vec2(out->size, decal.size);
        out->rotation_deg = decal.rotation_deg;
        copy_color(out->color, decal.color);
        out->opacity = decal.opacity;
        out->emissive = decal.emissive;
        out->circle = decal.circle ? 1 : 0;
        out->blend = blend_to_c(decal.blend);
        out->texture_id = c_str(decal.texture_id);
        out->material_id = c_str(decal.material_id);
        return AETHERFX_OK;
    });
}

int aetherfx_runtime_mesh_instance_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).meshes.size()); });
}

int aetherfx_mesh_instance_info(const aetherfx_runtime* runtime, int index,
                                struct aetherfx_mesh_instance_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::MeshInstanceState& instance =
            state.meshes[require_index(index, state.meshes.size(), "mesh instance")];
        out->id = c_str(instance.id);
        out->mesh_id = c_str(instance.mesh_id);
        out->material_id = c_str(instance.material_id);
        std::memcpy(out->transform, instance.transform.m.data(), sizeof(out->transform));
        copy_color(out->color, instance.color);
        out->emissive = instance.emissive;
        out->visible = instance.visible ? 1 : 0;
        return AETHERFX_OK;
    });
}

// ---------------------------------------------------------------------------
// Frame state: volumes
// ---------------------------------------------------------------------------

int aetherfx_runtime_volume_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).volumes.size()); });
}

int aetherfx_volume_info(const aetherfx_runtime* runtime, int index, struct aetherfx_volume_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::VolumeState& volume = state.volumes[require_index(index, state.volumes.size(), "volume")];
        out->id = c_str(volume.id);
        out->mode = c_str(volume.mode);
        out->shape = c_str(volume.shape);
        out->volume_type = c_str(volume.volume_type);
        out->backend = c_str(volume.backend);
        std::memcpy(out->transform, volume.transform.m.data(), sizeof(out->transform));
        copy_vec3(out->bounds_min, volume.bounds_min);
        copy_vec3(out->bounds_max, volume.bounds_max);
        out->radius = volume.radius;
        out->height = volume.height;
        out->density = volume.density;
        out->emission = volume.emission;
        copy_color(out->color, volume.color);
        copy_color(out->color_hot, volume.color_hot);
        out->filament_scale = volume.filament_scale;
        out->strands = volume.strands;
        out->carve = volume.carve;
        out->softness = volume.softness;
        out->spiral_arms = volume.spiral_arms;
        out->arm_sharpness = volume.arm_sharpness;
        out->twist = volume.twist;
        out->spin = volume.spin;
        out->climb = volume.climb;
        out->scatter = volume.scatter;
        out->march_steps = volume.march_steps;
        out->seed = volume.seed;
        out->time = volume.time;
        out->temperature = volume.temperature;
        return AETHERFX_OK;
    });
}

// ---------------------------------------------------------------------------
// Frame state: beams and trails
// ---------------------------------------------------------------------------

int aetherfx_runtime_beam_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).beams.size()); });
}

int aetherfx_beam_info(const aetherfx_runtime* runtime, int index, struct aetherfx_beam_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::BeamState& beam = state.beams[require_index(index, state.beams.size(), "beam")];
        out->id = c_str(beam.id);
        out->polyline_count = beam.paths.size();
        out->width = beam.width;
        copy_color(out->color, beam.color);
        out->emissive = beam.emissive;
        out->blend = blend_to_c(beam.blend);
        out->material_id = c_str(beam.material_id);
        out->pulse_phase = beam.pulse_phase;
        return AETHERFX_OK;
    });
}

int aetherfx_beam_polyline(const aetherfx_runtime* runtime, int beam, int polyline, const float** xyz,
                           size_t* count) {
    return guard_status([&]() -> int {
        require_mutable(xyz, "xyz");
        require_mutable(count, "count");
        const aether::FrameState& state = state_of(runtime);
        const aether::BeamState& b = state.beams[require_index(beam, state.beams.size(), "beam")];
        const std::vector<Vec3>& points = b.paths[require_index(polyline, b.paths.size(), "polyline")].points;
        *xyz = points.empty() ? nullptr : &points.front().x;
        *count = points.size();
        return AETHERFX_OK;
    });
}

namespace {
void fill_beam_path(const aether::BeamPath& path, struct aetherfx_beam_path* out) {
    out->vertex_count = path.points.size();
    out->depth = path.depth;
    out->fade = path.fade;
    out->position = path.points.empty() ? nullptr : &path.points.front().x;
    out->width = path.width.empty() ? nullptr : path.width.data();
    out->intensity = path.intensity.empty() ? nullptr : path.intensity.data();
}
}  // namespace

int aetherfx_beam_style(const aetherfx_runtime* runtime, int beam, struct aetherfx_beam_style* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::BeamState& b = state.beams[require_index(beam, state.beams.size(), "beam")];
        out->core_width = b.core_width;
        out->glow_width = b.glow_width;
        out->path_count = b.paths.size();
        out->ghost_count = b.ghosts.size();
        out->flare_count = b.flares.size();
        return AETHERFX_OK;
    });
}

int aetherfx_beam_path(const aetherfx_runtime* runtime, int beam, int path,
                       struct aetherfx_beam_path* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::BeamState& b = state.beams[require_index(beam, state.beams.size(), "beam")];
        fill_beam_path(b.paths[require_index(path, b.paths.size(), "path")], out);
        return AETHERFX_OK;
    });
}

int aetherfx_beam_ghost(const aetherfx_runtime* runtime, int beam, int ghost,
                        struct aetherfx_beam_path* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::BeamState& b = state.beams[require_index(beam, state.beams.size(), "beam")];
        fill_beam_path(b.ghosts[require_index(ghost, b.ghosts.size(), "ghost")], out);
        return AETHERFX_OK;
    });
}

int aetherfx_beam_flare(const aetherfx_runtime* runtime, int beam, int index,
                        struct aetherfx_beam_flare* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::BeamState& b = state.beams[require_index(beam, state.beams.size(), "beam")];
        const aether::BeamFlare& flare = b.flares[require_index(index, b.flares.size(), "flare")];
        copy_vec3(out->position, flare.position);
        out->radius = flare.radius;
        out->intensity = flare.intensity;
        return AETHERFX_OK;
    });
}

int aetherfx_runtime_trail_count(const aetherfx_runtime* runtime) {
    return guard_status([&]() -> int { return to_count(state_of(runtime).trails.size()); });
}

int aetherfx_trail_info(const aetherfx_runtime* runtime, int index, struct aetherfx_trail_info* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        const aether::TrailState& trail = state.trails[require_index(index, state.trails.size(), "trail")];
        out->id = c_str(trail.id);
        out->ribbon_count = trail.ribbons.size();
        out->blend = blend_to_c(trail.blend);
        out->material_id = c_str(trail.material_id);
        return AETHERFX_OK;
    });
}

int aetherfx_trail_ribbon(const aetherfx_runtime* runtime, int trail, int ribbon,
                          const struct aetherfx_trail_vertex** verts, size_t* count) {
    return guard_status([&]() -> int {
        require_mutable(verts, "verts");
        require_mutable(count, "count");
        const aether::FrameState& state = state_of(runtime);
        const aether::TrailState& t = state.trails[require_index(trail, state.trails.size(), "trail")];
        const std::vector<aether::TrailVertex>& vertices =
            t.ribbons[require_index(ribbon, t.ribbons.size(), "ribbon")];
        // Layout-compatible, see the static_asserts at the top of this file.
        *verts = vertices.empty() ? nullptr
                                  : reinterpret_cast<const struct aetherfx_trail_vertex*>(vertices.data());
        *count = vertices.size();
        return AETHERFX_OK;
    });
}

// ---------------------------------------------------------------------------
// Frame state: camera
// ---------------------------------------------------------------------------

int aetherfx_runtime_camera(const aetherfx_runtime* runtime, struct aetherfx_camera* out) {
    return guard_status([&]() -> int {
        require_mutable(out, "out");
        const aether::FrameState& state = state_of(runtime);
        if (!state.camera.has_value()) return 0;
        const aether::CameraDesc& camera = *state.camera;
        copy_vec3(out->position, camera.position);
        copy_vec3(out->target, camera.target);
        copy_vec3(out->up, camera.up);
        out->fov_deg = camera.fov_deg;
        out->near_plane = camera.near_plane;
        out->far_plane = camera.far_plane;
        out->exposure = camera.exposure;
        return 1;
    });
}
