#ifndef AETHERFX_H
#define AETHERFX_H
/*
 * AetherFX C ABI -- the stable interface game engines link against.
 *
 * The library *simulates*; the engine *renders*. An engine compiles an effect
 * once, creates one runtime per playing instance, advances it with the game
 * clock and reads the per-frame buffers (particles, lights, beams, trails,
 * decals, mesh instances) plus the baked resources (textures, meshes,
 * materials) it needs to draw them with its own shaders.
 *
 * The simulation is the same deterministic fixed-step simulation the studio
 * uses: the same effect JSON, seed, fixed timestep and step count produce
 * bit-identical buffers on every machine and in every host.
 *
 * See docs/ENGINE_INTEGRATION.md for the integration walkthrough, the material
 * mapping table, the sprite-sheet layout and the unit conventions.
 *
 * --------------------------------------------------------------------------
 * Conventions
 * --------------------------------------------------------------------------
 * C99. No C++ types cross this boundary and no exception ever escapes it.
 *
 * Errors: functions that report status return int -- 0 is success, a negative
 * aetherfx_status is failure. Functions that return a handle return NULL on
 * failure. Functions that return a count return a negative status on failure.
 * aetherfx_last_error() describes the most recent failure on the calling
 * thread; it is only meaningful immediately after a failing call.
 *
 * Structs: every plain-data struct below is declared with a tag and no
 * typedef, because several of them share a name with the accessor that fills
 * them (as `struct stat` does with stat()). Write
 * `struct aetherfx_light_info info;` in C and in C++ alike. Handles are
 * typedef'd, so `aetherfx_effect*` works unqualified.
 *
 * Booleans are int (0 or 1). Enums are passed as int fields in structs so the
 * layout never depends on the compiler's enum sizing.
 *
 * Memory: every pointer returned by this library is owned by the library.
 *   - `const char*` strings inside info structs and from accessors such as
 *     aetherfx_effect_name() point into the owning object and stay valid until
 *     that object is freed or mutated. Strings that come out of a runtime
 *     frame stay valid until the next step of that runtime.
 *   - Array pointers into a runtime frame (positions, polylines, ribbons, ...)
 *     stay valid until the next aetherfx_runtime_step(),
 *     aetherfx_runtime_simulate_to() or aetherfx_runtime_reset() on that
 *     runtime. Copy them into engine buffers during the frame you read them.
 *   - Array pointers into a compiled effect (texture pixels, mesh vertices)
 *     stay valid until aetherfx_compiled_free().
 *   - The few functions that return `char*` return a freshly allocated,
 *     NUL-terminated JSON string that the caller must release with
 *     aetherfx_free_string().
 *
 * Threading: the error message is thread-local, and distinct handles are
 * independent, so one runtime per worker thread is fine. A single handle must
 * not be used from two threads at once. Reading a runtime's buffers while
 * another thread steps that same runtime is a data race.
 *
 * Units: meters, seconds, linear (not sRGB) colour, right-handed Y-up,
 * quaternions (x, y, z, w), matrices column-major. Angles are radians in the
 * buffers, degrees where a field says so.
 */

#include <stddef.h>
#include <stdint.h>

/* -------------------------------------------------------------------------
 * Export macro
 * -------------------------------------------------------------------------
 * Define AETHERFX_STATIC before including this header when you link the
 * static library (aetherfx_c_static). AETHERFX_BUILD_DLL is defined by the
 * library's own build; consumers never define it.
 */
#if defined(_WIN32)
#  if defined(AETHERFX_STATIC)
#    define AETHERFX_API
#  elif defined(AETHERFX_BUILD_DLL)
#    define AETHERFX_API __declspec(dllexport)
#  else
#    define AETHERFX_API __declspec(dllimport)
#  endif
#else
#  if defined(AETHERFX_STATIC)
#    define AETHERFX_API
#  else
#    define AETHERFX_API __attribute__((visibility("default")))
#  endif
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Versioning
 * -------------------------------------------------------------------------
 * AETHERFX_ABI_VERSION changes only when the binary interface changes in a way
 * that breaks existing callers (a struct layout, a signature, an enum value).
 * Purely additive releases keep it. A host that caches a compiled plugin
 * should compare aetherfx_abi_version() against AETHERFX_ABI_VERSION at load
 * time and refuse to run on a mismatch.
 */
#define AETHERFX_ABI_VERSION 1

#define AETHERFX_VERSION_MAJOR 0
#define AETHERFX_VERSION_MINOR 1
#define AETHERFX_VERSION_PATCH 0

/* Library version (of the build, not of this header). Any pointer may be NULL. */
AETHERFX_API void aetherfx_version(int* major, int* minor, int* patch);

/* ABI version of the build. Compare with AETHERFX_ABI_VERSION. */
AETHERFX_API int aetherfx_abi_version(void);

/* Version string of the build, e.g. "0.1.0". Valid forever; never NULL. */
AETHERFX_API const char* aetherfx_version_string(void);

/* -------------------------------------------------------------------------
 * Status codes
 * ------------------------------------------------------------------------- */
enum aetherfx_status {
    AETHERFX_OK = 0,
    AETHERFX_ERROR_INVALID_ARGUMENT = -1,  /* NULL handle, NULL output, bad value */
    AETHERFX_ERROR_OUT_OF_RANGE = -2,      /* index outside [0, count) */
    AETHERFX_ERROR_INVALID_JSON = -3,      /* the text is not JSON, or not an effect */
    AETHERFX_ERROR_VALIDATION = -4,        /* the effect has error diagnostics */
    AETHERFX_ERROR_COMPILE = -5,           /* compilation failed */
    AETHERFX_ERROR_RUNTIME = -6,           /* the runtime refused the request */
    AETHERFX_ERROR_IO = -7,                /* file could not be read */
    AETHERFX_ERROR_BUFFER_TOO_SMALL = -8,  /* caller buffer too small; see last_error */
    AETHERFX_ERROR_UNSUPPORTED = -9,       /* not available in this build */
    AETHERFX_ERROR_INTERNAL = -10          /* unexpected failure; see last_error */
};

/*
 * Message for the most recent failing call on this thread, or "" when the last
 * call succeeded. Never NULL. The pointer stays valid until the next failing
 * call on the same thread.
 */
AETHERFX_API const char* aetherfx_last_error(void);

/* Releases a string returned by an aetherfx_*_json() function. NULL is a no-op. */
AETHERFX_API void aetherfx_free_string(char* text);

/* -------------------------------------------------------------------------
 * Enumerations (mirrors of the engine vocabulary; values are part of the ABI)
 * ------------------------------------------------------------------------- */
enum aetherfx_blend_mode {
    AETHERFX_BLEND_ADDITIVE = 0,
    AETHERFX_BLEND_ALPHA = 1,
    AETHERFX_BLEND_PREMULTIPLIED = 2
};

enum aetherfx_render_mode {
    AETHERFX_RENDER_BILLBOARD = 0,
    AETHERFX_RENDER_STRETCHED_BILLBOARD = 1,
    AETHERFX_RENDER_MESH = 2,
    AETHERFX_RENDER_RIBBON = 3,
    AETHERFX_RENDER_NONE = 4
};

enum aetherfx_shading {
    AETHERFX_SHADING_UNLIT = 0,
    AETHERFX_SHADING_LIT = 1
};

enum aetherfx_light_type {
    AETHERFX_LIGHT_POINT = 0,
    AETHERFX_LIGHT_SPOT = 1,
    AETHERFX_LIGHT_AREA = 2
};

/* -------------------------------------------------------------------------
 * Opaque handles
 * -------------------------------------------------------------------------
 * aetherfx_effect   - an authored graph, editable through set_parameter.
 * aetherfx_compiled - a validated effect plus its baked resources and plan.
 * aetherfx_runtime  - one playing instance; owns its own copy of the plan, so
 *                     it keeps working after the compiled handle is freed.
 */
typedef struct aetherfx_effect aetherfx_effect;
typedef struct aetherfx_compiled aetherfx_compiled;
typedef struct aetherfx_runtime aetherfx_runtime;

/* -------------------------------------------------------------------------
 * Effects
 * ------------------------------------------------------------------------- */

/*
 * Parses an effect document. `json_utf8` need not be NUL-terminated; pass its
 * byte length in `len` (pass 0 with a NUL-terminated string to use strlen).
 * Documents written against an older schema_version are migrated forward.
 * Returns NULL on failure.
 */
AETHERFX_API aetherfx_effect* aetherfx_effect_load_json(const char* json_utf8, size_t len);

/* Same, reading the document from a file. Returns NULL on failure. */
AETHERFX_API aetherfx_effect* aetherfx_effect_load_file(const char* path);

/* Releases an effect. NULL is a no-op. Compiled effects and runtimes made from
 * it are unaffected: both hold their own copy. */
AETHERFX_API void aetherfx_effect_free(aetherfx_effect* effect);

/* The effect's name. Valid until the effect is freed. NULL on failure. */
AETHERFX_API const char* aetherfx_effect_name(const aetherfx_effect* effect);

/* Authored duration in seconds, or a negative status on failure. Effects may
 * keep producing particles past their duration; it is a presentation hint. */
AETHERFX_API double aetherfx_effect_duration(const aetherfx_effect* effect);

/*
 * Validates the effect and writes the diagnostics as JSON into `buf`:
 *   {"ok":bool,"errors":n,"warnings":n,"items":[{severity,code,message,node,param}]}
 *
 * Returns AETHERFX_OK when the effect has no error diagnostics,
 * AETHERFX_ERROR_VALIDATION when it does (the JSON is still written, and is
 * how you find out what is wrong), AETHERFX_ERROR_BUFFER_TOO_SMALL when the
 * JSON does not fit (the text is truncated, and aetherfx_last_error() reports
 * the capacity it needs), or AETHERFX_ERROR_INVALID_ARGUMENT.
 *
 * `buf` is always NUL-terminated when cap > 0. Pass buf = NULL and cap = 0 to
 * validate without writing anything.
 */
AETHERFX_API int aetherfx_effect_validate(const aetherfx_effect* effect, char* buf, size_t cap);

/*
 * Overrides one parameter of one node, for runtime tweaks (an intensity, a
 * colour, a duration) without editing the document. `json_value` is the value
 * in the vocabulary's JSON encoding: 2.5, true, "sphere", [1,0,0],
 * [[0,1],[1,0]] for a curve, "#ff8800" for a colour. Setting a constant clears
 * any keyframe track on that parameter.
 *
 * Returns AETHERFX_OK, or AETHERFX_ERROR_INVALID_ARGUMENT when the node or
 * parameter is unknown or the value has the wrong type (the message names the
 * expected type).
 *
 * The change applies to the next aetherfx_compile(); existing compiled effects
 * and runtimes keep running the plan they were made with.
 */
AETHERFX_API int aetherfx_effect_set_parameter(aetherfx_effect* effect, const char* node_id,
                                               const char* name, const char* json_value);

/*
 * Serialises the effect back to a document. `indent` is the number of spaces
 * per level, or a negative number for the most compact form. Returns a string
 * to release with aetherfx_free_string(), or NULL on failure.
 */
AETHERFX_API char* aetherfx_effect_to_json(const aetherfx_effect* effect, int indent);

/* -------------------------------------------------------------------------
 * Controls
 * -------------------------------------------------------------------------
 * An effect may ship named numeric knobs -- "Intensity", "Flame height",
 * "Hue" -- that scale, offset or hue-rotate the parameters they are bound to.
 * They are how a game spawns a weaker or a stronger instance of the same
 * effect without touching the graph: set the controls, then compile. Moving a
 * control is not an edit; the authored values stay as they are and the change
 * is folded in by aetherfx_compile(). See docs/CONTROLS.md.
 *
 * Controls are indexed [0, count) in document order.
 */

struct aetherfx_control_info {
    const char* id;            /* "flames_intensity" */
    const char* label;         /* "Flame height", for a UI */
    const char* group;         /* "Global", or the layer name */
    const char* unit;          /* "x", "deg", or "" */
    double min;
    double max;
    double default_value;      /* what a reset returns to */
    double value;              /* what the next compile will apply */
    double step;               /* increment a slider or an arrow key should use */
    int binding_count;         /* node parameters this control drives */
};

/* Number of controls on the effect, or a negative status. */
AETHERFX_API int aetherfx_effect_control_count(const aetherfx_effect* effect);

/* Fills `out` for control `index`. The strings point into the effect and stay
 * valid until it is freed or that control is changed. Returns AETHERFX_OK or a
 * negative status. */
AETHERFX_API int aetherfx_control_info(const aetherfx_effect* effect, int index,
                                       struct aetherfx_control_info* out);

/* Index of the control with this id, or AETHERFX_ERROR_OUT_OF_RANGE. */
AETHERFX_API int aetherfx_effect_control_index(const aetherfx_effect* effect, const char* id);

/*
 * Sets one control. `value` must lie inside the control's [min, max];
 * AETHERFX_ERROR_OUT_OF_RANGE says it does not, and
 * AETHERFX_ERROR_INVALID_ARGUMENT that there is no such control.
 *
 * Like aetherfx_effect_set_parameter, the change applies to the next
 * aetherfx_compile(); compiled effects and runtimes that already exist keep
 * running the plan they were made with.
 */
AETHERFX_API int aetherfx_effect_set_control(aetherfx_effect* effect, const char* id, double value);

/* -------------------------------------------------------------------------
 * Compilation
 * ------------------------------------------------------------------------- */

/*
 * Validates the effect, resolves its graph, bakes its textures and meshes and
 * produces the execution plan. Compile once per effect and share the result
 * between instances; it is the expensive step.
 *
 * `fixed_dt` is the simulation timestep in seconds; pass 0 for the default
 * 1/60. The timestep is part of the determinism contract: two hosts that want
 * identical results must use the same one.
 *
 * Returns NULL only when compilation could not run at all. An effect with
 * error diagnostics still returns a handle so you can read them, but
 * aetherfx_compiled_ok() is 0 and no runtime can be created from it.
 */
AETHERFX_API aetherfx_compiled* aetherfx_compile(const aetherfx_effect* effect, double fixed_dt);

/* Releases a compiled effect. NULL is a no-op. Runtimes already created from
 * it keep working. */
AETHERFX_API void aetherfx_compiled_free(aetherfx_compiled* compiled);

/* 1 when the compile produced no error diagnostics (a runtime can be created),
 * 0 when it did, negative on failure. */
AETHERFX_API int aetherfx_compiled_ok(const aetherfx_compiled* compiled);

/* The timestep this effect was compiled with, in seconds. Negative on failure. */
AETHERFX_API double aetherfx_compiled_fixed_dt(const aetherfx_compiled* compiled);

/* Compile diagnostics, same JSON shape as aetherfx_effect_validate(). Release
 * with aetherfx_free_string(). NULL on failure. */
AETHERFX_API char* aetherfx_compiled_diagnostics_json(const aetherfx_compiled* compiled);

/* The execution plan: {"nodes":[...],"tiers":{...},"resources":{...},
 * "diagnostics":{...}}. Useful for tooling and bug reports. Release with
 * aetherfx_free_string(). NULL on failure. */
AETHERFX_API char* aetherfx_compiled_plan_json(const aetherfx_compiled* compiled);

/* -------------------------------------------------------------------------
 * Baked resources
 * -------------------------------------------------------------------------
 * Resources are indexed [0, count) in a stable order (sorted by id), so an
 * engine can upload them once after compiling and then address them by index.
 * The ids in the frame-state info structs match these ids.
 */

/*
 * A baked texture. Pixels are linear RGBA float, row-major, origin top-left,
 * `width * height * 4` floats.
 *
 * An animated texture stores its frames side by side in one image: frame i
 * occupies columns [i * frame_width, (i + 1) * frame_width). `frames` is 1 for
 * a still texture.
 */
struct aetherfx_texture_info {
    const char* id;
    int width;        /* of the whole image, all frames included */
    int height;
    int frames;       /* >= 1 */
    int frame_width;  /* width / frames */
    int channels;     /* always 4 (RGBA) */
};

/* Number of baked textures, or a negative status. */
AETHERFX_API int aetherfx_compiled_texture_count(const aetherfx_compiled* compiled);

/* Fills `out` for texture `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_texture_info(const aetherfx_compiled* compiled, int index,
                                       struct aetherfx_texture_info* out);

/* Index of the texture with this id, or AETHERFX_ERROR_OUT_OF_RANGE when there
 * is none. Use it to resolve the sprite_id / texture_id / base_texture strings
 * that appear in frame state and materials. */
AETHERFX_API int aetherfx_texture_index(const aetherfx_compiled* compiled, const char* id);

/* Linear RGBA pixels, width * height * 4 floats. Valid until the compiled
 * effect is freed. NULL on failure. */
AETHERFX_API const float* aetherfx_texture_pixels(const aetherfx_compiled* compiled, int index);

/*
 * Converts the texture to 8 bits per channel and writes width * height * 4
 * bytes into `out`. `cap` is the size of `out` in bytes and must be at least
 * that. With `srgb_encode` non-zero the RGB channels are sRGB-encoded (what a
 * GPU sampler expects from an sRGB texture format) and alpha stays linear;
 * with 0 all four channels are the linear values clamped to [0,1].
 *
 * Returns AETHERFX_OK, AETHERFX_ERROR_BUFFER_TOO_SMALL (the required size is
 * in the error message) or another negative status.
 */
AETHERFX_API int aetherfx_texture_pixels_rgba8(const aetherfx_compiled* compiled, int index,
                                               uint8_t* out, size_t cap, int srgb_encode);

/*
 * A baked mesh. Positions are xyz triples; normals and uvs, when present, have
 * one entry per position (3 and 2 floats respectively). Indices are triangle
 * lists, `index_count` entries, counter-clockwise front faces.
 *
 * Seeded primitives bake several variants under "<id>", "<id>#1", "<id>#2"...
 * Each variant is its own entry here; a mesh particle system tells you how
 * many it has in aetherfx_particle_system_info::mesh_variants, and each
 * particle picks one with aetherfx_particle_variant().
 */
struct aetherfx_mesh_info {
    const char* id;
    size_t vertex_count;
    size_t index_count;
    int has_normals;
    int has_uvs;
};

/* Number of baked meshes (variants included), or a negative status. */
AETHERFX_API int aetherfx_compiled_mesh_count(const aetherfx_compiled* compiled);

/* Fills `out` for mesh `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_mesh_info(const aetherfx_compiled* compiled, int index,
                                    struct aetherfx_mesh_info* out);

/* Index of the mesh with this id ("rock_mesh", "rock_mesh#1", ...), or
 * AETHERFX_ERROR_OUT_OF_RANGE when there is none. */
AETHERFX_API int aetherfx_mesh_index(const aetherfx_compiled* compiled, const char* id);

/* vertex_count * 3 floats. Valid until the compiled effect is freed. */
AETHERFX_API const float* aetherfx_mesh_positions(const aetherfx_compiled* compiled, int index);

/* vertex_count * 3 floats, or NULL when the mesh has no normals. */
AETHERFX_API const float* aetherfx_mesh_normals(const aetherfx_compiled* compiled, int index);

/* vertex_count * 2 floats, or NULL when the mesh has no UVs. */
AETHERFX_API const float* aetherfx_mesh_uvs(const aetherfx_compiled* compiled, int index);

/* index_count 32-bit indices. */
AETHERFX_API const uint32_t* aetherfx_mesh_indices(const aetherfx_compiled* compiled, int index);

/*
 * A baked material: everything the engine needs to configure its own shader.
 * See the mapping table in docs/ENGINE_INTEGRATION.md.
 *
 * base_texture / noise_texture are texture ids, "" when unused. Colours are
 * linear RGBA.
 */
struct aetherfx_material {
    const char* id;
    int blend;                 /* enum aetherfx_blend_mode */
    int shading;               /* enum aetherfx_shading */
    float base_color[4];
    float opacity;             /* multiplies the particle opacity */
    float emissive_color[4];
    float emissive_intensity;  /* added to the per-particle emissive */
    float fresnel_power;       /* 0 = no rim term */
    float dissolve;            /* [0,1], noise-driven erase over lifetime */
    float erosion;             /* [0,1], noise-driven edge break-up */
    int soft_particle;         /* 1 = fade against scene depth */
    float depth_fade;          /* meters, the soft-particle fade distance */
    const char* base_texture;
    const char* noise_texture; /* mask source for dissolve / erosion */
};

/* Number of baked materials, or a negative status. */
AETHERFX_API int aetherfx_compiled_material_count(const aetherfx_compiled* compiled);

/* Fills `out` for material `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_material_info(const aetherfx_compiled* compiled, int index,
                                        struct aetherfx_material* out);

/* Index of the material with this id, or AETHERFX_ERROR_OUT_OF_RANGE. */
AETHERFX_API int aetherfx_material_index(const aetherfx_compiled* compiled, const char* id);

/* -------------------------------------------------------------------------
 * Runtime
 * -------------------------------------------------------------------------
 * One runtime is one playing instance. It copies the plan it is built from, so
 * several instances of the same effect are independent and can be stepped on
 * different threads.
 */

/* Creates a runtime. Fails when the compiled effect has error diagnostics.
 * Returns NULL on failure. */
AETHERFX_API aetherfx_runtime* aetherfx_runtime_create(const aetherfx_compiled* compiled);

/* Releases a runtime. NULL is a no-op. Every pointer previously handed out by
 * this runtime becomes dangling. */
AETHERFX_API void aetherfx_runtime_free(aetherfx_runtime* runtime);

/* Rewinds to t = 0, frame 0, no particles, with the analytic scene evaluated
 * at t = 0 so frame 0 is drawable without stepping. Invalidates frame
 * pointers. */
AETHERFX_API int aetherfx_runtime_reset(aetherfx_runtime* runtime);

/* Advances exactly one fixed timestep. Invalidates frame pointers. */
AETHERFX_API int aetherfx_runtime_step(aetherfx_runtime* runtime);

/*
 * Steps until the runtime's time reaches `time`. It never interpolates and
 * never steps backwards: if `time` is behind the current time nothing happens,
 * so call aetherfx_runtime_reset() to replay. This is the normal per-frame
 * entry point -- pass the instance's elapsed play time and the runtime takes
 * however many fixed steps that needs (one at 60 Hz, more after a hitch).
 * Invalidates frame pointers.
 */
AETHERFX_API int aetherfx_runtime_simulate_to(aetherfx_runtime* runtime, double time);

/* Current simulation time in seconds (always frame_index * fixed_dt), or a
 * negative status on failure. */
AETHERFX_API double aetherfx_runtime_time(const aetherfx_runtime* runtime);

/* Number of steps taken since the last reset. UINT64_MAX on failure. */
AETHERFX_API uint64_t aetherfx_runtime_frame_index(const aetherfx_runtime* runtime);

/* The fixed timestep in seconds, or a negative status on failure. */
AETHERFX_API double aetherfx_runtime_fixed_dt(const aetherfx_runtime* runtime);

/* Name of the simulation backend, e.g. "cpu". The pointer is valid until the
 * next aetherfx_runtime_backend() call on this thread. NULL on failure. */
AETHERFX_API const char* aetherfx_runtime_backend(const aetherfx_runtime* runtime);

/*
 * Simulation statistics as JSON: {"time","frame","systems":[{"system_id",
 * "alive","spawned_total","died_total","peak_alive","capacity","dropped",
 * "collisions"}],"total_alive","total_spawned","events_fired","last_step_ms",
 * "total_step_ms"}. Timings are wall-clock and are the only values here that
 * are not deterministic. Release with aetherfx_free_string(). NULL on failure.
 */
AETHERFX_API char* aetherfx_runtime_statistics_json(const aetherfx_runtime* runtime);

/* -------------------------------------------------------------------------
 * Frame state: particles
 * -------------------------------------------------------------------------
 * Particles are stored as parallel arrays (structure of arrays), one set per
 * particle system. All arrays of a system have `count` entries; multiply by
 * the component count for the float length (3 for position, 4 for colour, 1
 * for size...). The arrays are only valid until the next step of this runtime.
 */
struct aetherfx_particle_system_info {
    const char* id;                 /* particle_system node id */
    size_t count;                   /* live particles this frame */
    int render_mode;                /* enum aetherfx_render_mode */
    int blend;                      /* enum aetherfx_blend_mode; the material's
                                       blend wins when material_id is set */
    const char* material_id;        /* "" = none */
    const char* sprite_id;          /* texture id for billboards, "" = none */
    const char* mesh_id;            /* base mesh id for AETHERFX_RENDER_MESH */
    int mesh_variants;              /* variants baked as "<mesh_id>#k", >= 1 */
    int sprite_columns;             /* sprite sheet grid inside one frame */
    int sprite_rows;
    float sprite_fps;               /* 0 = one pass over the particle lifetime */
    float velocity_stretch;         /* length = size * (1 + stretch * speed) */
    int align_to_velocity;          /* 1 = rotate the quad to screen velocity */
    float soft_particle_distance;   /* meters; 0 = no depth fade */
    int sort;                       /* 1 = the engine should depth-sort this system */
};

/* Number of particle systems this frame, or a negative status. Systems are
 * stable for the lifetime of the runtime, so an engine can bind one draw call
 * per index. */
AETHERFX_API int aetherfx_runtime_particle_system_count(const aetherfx_runtime* runtime);

/* Fills `out` for system `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_particle_system_info(const aetherfx_runtime* runtime, int index,
                                               struct aetherfx_particle_system_info* out);

/*
 * Per-particle arrays for system `index`, `count` entries each.
 *
 * Each returns NULL when `index` is out of range (with an error set) or when
 * the system has no live particles (with no error set), so read `count` from
 * aetherfx_particle_system_info() first and skip empty systems.
 */
AETHERFX_API const float* aetherfx_particle_position(const aetherfx_runtime* runtime, int index);          /* xyz, meters */
AETHERFX_API const float* aetherfx_particle_previous_position(const aetherfx_runtime* runtime, int index); /* xyz, last step (motion vectors) */
AETHERFX_API const float* aetherfx_particle_velocity(const aetherfx_runtime* runtime, int index);          /* xyz, m/s */
AETHERFX_API const float* aetherfx_particle_age(const aetherfx_runtime* runtime, int index);               /* seconds */
AETHERFX_API const float* aetherfx_particle_lifetime(const aetherfx_runtime* runtime, int index);          /* seconds */
AETHERFX_API const float* aetherfx_particle_size(const aetherfx_runtime* runtime, int index);              /* world-space diameter, meters */
AETHERFX_API const float* aetherfx_particle_rotation(const aetherfx_runtime* runtime, int index);          /* radians, roll about the view axis */
AETHERFX_API const float* aetherfx_particle_color(const aetherfx_runtime* runtime, int index);             /* rgba linear; use opacity for alpha */
AETHERFX_API const float* aetherfx_particle_opacity(const aetherfx_runtime* runtime, int index);           /* [0,1] */
AETHERFX_API const float* aetherfx_particle_emissive(const aetherfx_runtime* runtime, int index);          /* HDR multiplier */
AETHERFX_API const float* aetherfx_particle_orientation(const aetherfx_runtime* runtime, int index);       /* quaternion xyzw; (0,0,0,1) when unused */
AETHERFX_API const float* aetherfx_particle_scale3(const aetherfx_runtime* runtime, int index);            /* xyz multipliers on size; (1,1,1) when unused */
AETHERFX_API const uint32_t* aetherfx_particle_variant(const aetherfx_runtime* runtime, int index);        /* mesh variant index, < mesh_variants */
AETHERFX_API const uint32_t* aetherfx_particle_seed(const aetherfx_runtime* runtime, int index);           /* stable per-particle seed for shader jitter */
AETHERFX_API const float* aetherfx_particle_custom0(const aetherfx_runtime* runtime, int index);           /* normalized age, age / lifetime in [0,1] */

/* -------------------------------------------------------------------------
 * Frame state: lights
 * ------------------------------------------------------------------------- */
struct aetherfx_light_info {
    const char* id;
    int type;              /* enum aetherfx_light_type */
    float position[3];
    float direction[3];    /* spot / area */
    float color[4];        /* linear rgba */
    float intensity;
    float radius;          /* meters, the influence radius */
    float cone_angle_deg;  /* spot half-angle */
};

/* Number of lights this frame, or a negative status. */
AETHERFX_API int aetherfx_runtime_light_count(const aetherfx_runtime* runtime);

/* Fills `out` for light `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_light_info(const aetherfx_runtime* runtime, int index,
                                     struct aetherfx_light_info* out);

/* -------------------------------------------------------------------------
 * Frame state: decals
 * ------------------------------------------------------------------------- */
struct aetherfx_decal_info {
    const char* id;
    float position[3];
    float normal[3];      /* projection axis */
    float size[2];        /* meters */
    float rotation_deg;   /* about the normal */
    float color[4];       /* linear rgba */
    float opacity;
    float emissive;
    int circle;           /* 1 = radial falloff, 0 = full rectangle */
    int blend;            /* enum aetherfx_blend_mode */
    const char* texture_id;
    const char* material_id;
};

/* Number of decals this frame, or a negative status. */
AETHERFX_API int aetherfx_runtime_decal_count(const aetherfx_runtime* runtime);

/* Fills `out` for decal `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_decal_info(const aetherfx_runtime* runtime, int index,
                                     struct aetherfx_decal_info* out);

/* -------------------------------------------------------------------------
 * Frame state: mesh instances
 * ------------------------------------------------------------------------- */
struct aetherfx_mesh_instance_info {
    const char* id;
    const char* mesh_id;
    const char* material_id;
    float transform[16];  /* column-major, meters */
    float color[4];       /* linear rgba tint */
    float emissive;
    int visible;
};

/* Number of mesh instances this frame, or a negative status. */
AETHERFX_API int aetherfx_runtime_mesh_instance_count(const aetherfx_runtime* runtime);

/* Fills `out` for mesh instance `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_mesh_instance_info(const aetherfx_runtime* runtime, int index,
                                             struct aetherfx_mesh_instance_info* out);

/* -------------------------------------------------------------------------
 * Frame state: volumes
 * -------------------------------------------------------------------------
 * A `volume` node. `mode` is "procedural" -- a raymarched closed-form density
 * field a host can draw on its own -- or "simulation", the V1 fluid stub that
 * fills only id/bounds/density/temperature (and compiles with warning W104).
 *
 * The density function every backend must agree on, the meaning of `shape` and
 * of `height` per shape, and the marching recipe are in docs/VOLUMES.md. The
 * field is exactly zero outside the local box, so march
 * `transform * [-extent, extent]` and nothing else.
 *
 * Added after ABI 1 shipped: purely additive (new struct, new functions), so
 * AETHERFX_ABI_VERSION is unchanged. A host built against the older header
 * simply never calls these.
 */
struct aetherfx_volume_info {
    const char* id;
    const char* mode;          /* "procedural" | "simulation" */
    const char* shape;         /* sphere | column | disc | ring | nebula | cone */
    const char* volume_type;   /* smoke | fire | fog | dust | magic | generic_density */
    const char* backend;       /* "procedural_volume" | "volume_stub" */
    float transform[16];       /* local -> world, column-major, meters */
    float bounds_min[3];       /* world-space AABB of the shape */
    float bounds_max[3];
    float radius;              /* meters */
    float height;              /* meters */
    float density;             /* extinction per meter */
    float emission;            /* HDR emission multiplier */
    float color[4];            /* linear rgba, the shell tint */
    float color_hot[4];        /* linear rgba, the core tint */
    float filament_scale;      /* noise frequency, 1/m */
    float strands;             /* 0 = soft clouds, 1 = ridged filaments */
    float carve;               /* density threshold */
    float softness;            /* shape edge falloff */
    int spiral_arms;
    float arm_sharpness;
    float twist;               /* rad per meter of height */
    float spin;                /* revolutions per second about local +Y */
    float climb;               /* m/s of upward noise advection */
    float scatter;             /* single-scatter weight from scene lights */
    int march_steps;           /* quality hint; a backend may clamp it */
    uint32_t seed;             /* noise seed */
    float time;                /* effect time the fields above were evaluated at */
    float temperature;         /* simulation mode */
};

/* Number of volumes this frame, or a negative status. */
AETHERFX_API int aetherfx_runtime_volume_count(const aetherfx_runtime* runtime);

/* Fills `out` for volume `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_volume_info(const aetherfx_runtime* runtime, int index,
                                      struct aetherfx_volume_info* out);

/* -------------------------------------------------------------------------
 * Frame state: beams
 * -------------------------------------------------------------------------
 * A beam is one or more polylines: polyline 0 is the main bolt, the rest are
 * branches (draw them thinner -- the reference renderer uses 0.6x).
 */
struct aetherfx_beam_info {
    const char* id;
    size_t polyline_count;
    float width;        /* meters */
    float color[4];     /* linear rgba */
    float emissive;
    int blend;          /* enum aetherfx_blend_mode */
    const char* material_id;
    float pulse_phase;  /* [0,1) position of a travelling pulse, < 0 = none */
};

/* Number of beams this frame, or a negative status. */
AETHERFX_API int aetherfx_runtime_beam_count(const aetherfx_runtime* runtime);

/* Fills `out` for beam `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_beam_info(const aetherfx_runtime* runtime, int index,
                                    struct aetherfx_beam_info* out);

/*
 * Points of one polyline of one beam. `*xyz` receives `*count` xyz triples
 * (3 * (*count) floats) in world space, valid until the next step. Both output
 * pointers are required. Returns AETHERFX_OK or a negative status.
 */
AETHERFX_API int aetherfx_beam_polyline(const aetherfx_runtime* runtime, int beam, int polyline,
                                        const float** xyz, size_t* count);

/* -------------------------------------------------------------------------
 * Frame state: trails
 * -------------------------------------------------------------------------
 * A trail is one or more ribbons (one per tracked source, or one per live
 * particle for particle trails). Vertices are oldest first; build a camera
 * facing strip by extruding each vertex by width * 0.5 along the cross product
 * of the segment tangent and the view direction.
 */
struct aetherfx_trail_vertex {
    float position[3];
    float width;           /* meters, full width */
    float age;             /* seconds since the vertex was laid */
    float normalized_age;  /* age / lifetime in [0,1] */
    float u;               /* distance-based U coordinate */
    float color[4];        /* linear rgba */
    float opacity;
    float emissive;
};

struct aetherfx_trail_info {
    const char* id;
    size_t ribbon_count;
    int blend;  /* enum aetherfx_blend_mode */
    const char* material_id;
};

/* Number of trails this frame, or a negative status. */
AETHERFX_API int aetherfx_runtime_trail_count(const aetherfx_runtime* runtime);

/* Fills `out` for trail `index`. Returns AETHERFX_OK or a negative status. */
AETHERFX_API int aetherfx_trail_info(const aetherfx_runtime* runtime, int index,
                                     struct aetherfx_trail_info* out);

/*
 * Vertices of one ribbon of one trail. `*verts` receives `*count` vertices,
 * valid until the next step. A ribbon with fewer than two vertices draws
 * nothing. Both output pointers are required. Returns AETHERFX_OK or a
 * negative status.
 */
AETHERFX_API int aetherfx_trail_ribbon(const aetherfx_runtime* runtime, int trail, int ribbon,
                                       const struct aetherfx_trail_vertex** verts, size_t* count);

/* -------------------------------------------------------------------------
 * Frame state: camera
 * -------------------------------------------------------------------------
 * The authored preview camera. Game engines normally ignore it and use their
 * own; it is here for tools and for effects authored around a fixed framing.
 */
struct aetherfx_camera {
    float position[3];
    float target[3];
    float up[3];
    float fov_deg;     /* vertical field of view */
    float near_plane;
    float far_plane;
    float exposure;
};

/*
 * Fills `out` with the effect's camera. Returns 1 when the effect has one, 0
 * when it has none (`out` is left untouched), or a negative status on failure.
 */
AETHERFX_API int aetherfx_runtime_camera(const aetherfx_runtime* runtime, struct aetherfx_camera* out);

#ifdef __cplusplus
}  /* extern "C" */
#endif

#endif /* AETHERFX_H */
