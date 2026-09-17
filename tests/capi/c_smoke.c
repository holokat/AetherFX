/*
 * Pure C99 smoke test: proves that aetherfx/aetherfx.h is valid C (no C++ leaks
 * into the public header) and that a C program can drive the whole pipeline --
 * load, compile, create a runtime, step it and read the frame buffers -- with
 * nothing but the shared library.
 *
 * Compiled as C, not C++, on purpose. If this file stops compiling, the header
 * has grown something only a C++ compiler accepts.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <aetherfx/aetherfx.h>

#define CHECK(cond, what)                                                   \
    do {                                                                    \
        if (!(cond)) {                                                      \
            printf("FAIL %s: %s (%s)\n", (what), #cond, aetherfx_last_error()); \
            return 1;                                                       \
        }                                                                   \
    } while (0)

int main(void) {
    char path[1024];
    int major = -1, minor = -1, patch = -1;
    aetherfx_effect* effect = NULL;
    aetherfx_compiled* compiled = NULL;
    aetherfx_runtime* runtime = NULL;
    char diagnostics[8192];
    char* statistics = NULL;
    struct aetherfx_camera camera;
    int system_count = 0;
    int texture_count = 0;
    int i = 0;
    size_t total = 0;

    aetherfx_version(&major, &minor, &patch);
    CHECK(major >= 0 && minor >= 0 && patch >= 0, "version");
    CHECK(aetherfx_abi_version() == AETHERFX_ABI_VERSION, "abi version");
    printf("aetherfx %s (abi %d)\n", aetherfx_version_string(), aetherfx_abi_version());

    snprintf(path, sizeof(path), "%s/tests/fixtures/effects/fireball.json", AETHER_SOURCE_DIR);
    effect = aetherfx_effect_load_file(path);
    CHECK(effect != NULL, "load fireball.json");
    CHECK(strcmp(aetherfx_effect_name(effect), "Fireball") == 0, "effect name");
    CHECK(aetherfx_effect_duration(effect) > 0.0, "effect duration");
    CHECK(aetherfx_effect_validate(effect, diagnostics, sizeof(diagnostics)) == AETHERFX_OK, "validate");
    CHECK(diagnostics[0] == '{', "diagnostics json");

    compiled = aetherfx_compile(effect, 0.0);
    CHECK(compiled != NULL, "compile");
    CHECK(aetherfx_compiled_ok(compiled) == 1, "compiled ok");

    texture_count = aetherfx_compiled_texture_count(compiled);
    CHECK(texture_count > 0, "texture count");
    for (i = 0; i < texture_count; ++i) {
        struct aetherfx_texture_info texture;
        CHECK(aetherfx_texture_info(compiled, i, &texture) == AETHERFX_OK, "texture info");
        CHECK(texture.channels == 4 && texture.frames >= 1, "texture layout");
        CHECK(aetherfx_texture_pixels(compiled, i) != NULL, "texture pixels");
    }

    runtime = aetherfx_runtime_create(compiled);
    CHECK(runtime != NULL, "runtime create");
    CHECK(aetherfx_runtime_simulate_to(runtime, 1.0) == AETHERFX_OK, "simulate_to");
    CHECK(aetherfx_runtime_frame_index(runtime) == 60u, "frame index");

    system_count = aetherfx_runtime_particle_system_count(runtime);
    CHECK(system_count > 0, "particle systems");
    for (i = 0; i < system_count; ++i) {
        struct aetherfx_particle_system_info system_info;
        CHECK(aetherfx_particle_system_info(runtime, i, &system_info) == AETHERFX_OK, "system info");
        total += system_info.count;
        if (system_info.count > 0) {
            const float* position = aetherfx_particle_position(runtime, i);
            const float* opacity = aetherfx_particle_opacity(runtime, i);
            CHECK(position != NULL && opacity != NULL, "particle arrays");
            CHECK(isfinite(position[0]) && isfinite(position[system_info.count * 3 - 1]), "finite positions");
        }
    }
    CHECK(total > 0, "live particles");

    CHECK(aetherfx_runtime_light_count(runtime) >= 1, "lights");
    CHECK(aetherfx_runtime_camera(runtime, &camera) == 1, "camera");
    CHECK(camera.fov_deg > 0.0f, "camera fov");

    statistics = aetherfx_runtime_statistics_json(runtime);
    CHECK(statistics != NULL, "statistics");
    CHECK(strstr(statistics, "total_alive") != NULL, "statistics content");
    aetherfx_free_string(statistics);

    /* Controls: a plain C struct and four calls, on an effect that has none. */
    {
        struct aetherfx_control_info control;
        CHECK(aetherfx_effect_control_count(effect) == 0, "control count");
        CHECK(aetherfx_control_info(effect, 0, &control) == AETHERFX_ERROR_OUT_OF_RANGE, "control info range");
        CHECK(aetherfx_effect_control_index(effect, "nope") == AETHERFX_ERROR_OUT_OF_RANGE, "control index");
        CHECK(aetherfx_effect_set_control(effect, "nope", 1.0) == AETHERFX_ERROR_INVALID_ARGUMENT, "set control");
    }

    /* Errors are reported, not thrown, in C too. */
    CHECK(aetherfx_effect_load_json("{ nope", 0) == NULL, "bad json rejected");
    CHECK(aetherfx_last_error()[0] != '\0', "error message");

    aetherfx_runtime_free(runtime);
    aetherfx_compiled_free(compiled);
    aetherfx_effect_free(effect);

    printf("ok: %d systems, %lu particles at t=1s\n", system_count, (unsigned long)total);
    return 0;
}
