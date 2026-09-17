# Engine Integration (libaetherfx, the C ABI)

`libaetherfx` is how a game engine plays AetherFX effects. The library
**simulates**; the engine **renders**. Every frame the library hands the engine
the same buffers the studio renderer draws from - particle arrays, lights,
beams, trails, decals, mesh instances - and the engine draws them with its own
materials, its own sorting and its own lighting.

The simulation is the studio simulation, not a re-implementation: the same
deterministic fixed-step CPU runtime described in `docs/RUNTIME.md`, compiled
into a shared library behind a C99 interface. An effect that looked right in the
studio behaves identically in the game.

The public header is `src/capi/include/aetherfx/aetherfx.h`; every function is
documented there. This document is the integration guide around it.

## 1. What the library does and does not do

Does: parse and validate effect documents, compile them (bake procedural
textures and meshes, resolve the graph, pick execution tiers), run the
deterministic simulation, expose per-frame buffers and the baked resources.

Does not: render, allocate GPU resources, read the clock, spawn threads, touch
files after load, or link any engine SDK. There is no global state beyond a
thread-local error message, so several effects, several instances and several
game worlds coexist without interfering.

## 2. Build and link

```bash
cmake --preset capi && cmake --build --preset capi && ctest --preset capi
```

Artifacts land in `build-capi/src/capi/`:

| file | what it is |
|---|---|
| `libaetherfx.dylib` / `.so` / `.dll` | the shared library - the normal choice |
| `libaetherfx_static.a` / `libaetherfx_static.lib` | the same code as a static library |
| `src/capi/include/aetherfx/aetherfx.h` | the only header you need |

The shared library exports `aetherfx_*` and nothing else: visibility is hidden
by default and a linker export list keeps the C++ modules that are linked into
it (core, compiler, sim, procedural, imageio) out of the dynamic symbol table.
Nothing in your build can collide with it, and no C++ ABI ever crosses the
boundary.

```bash
nm -gU build-capi/src/capi/libaetherfx.dylib | grep -c aetherfx_   # every exported symbol
```

### Unreal (C++)

Drop the header and the library into a third-party module and include the
header directly - it declares its own `extern "C"`.

```
Plugins/AetherFX/Source/ThirdParty/AetherFX/
    include/aetherfx/aetherfx.h
    lib/Mac/libaetherfx.dylib
    lib/Linux/libaetherfx.so
    lib/Win64/libaetherfx.dll, libaetherfx.lib
```

```csharp
// AetherFX.Build.cs
PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "ThirdParty/AetherFX/include"));
string Lib = Path.Combine(ModuleDirectory, "ThirdParty/AetherFX/lib");
if (Target.Platform == UnrealTargetPlatform.Win64)
{
    PublicAdditionalLibraries.Add(Path.Combine(Lib, "Win64/libaetherfx.lib"));
    PublicDelayLoadDLLs.Add("libaetherfx.dll");
    RuntimeDependencies.Add("$(BinaryOutputDir)/libaetherfx.dll", Path.Combine(Lib, "Win64/libaetherfx.dll"));
}
else if (Target.Platform == UnrealTargetPlatform.Mac)
{
    PublicAdditionalLibraries.Add(Path.Combine(Lib, "Mac/libaetherfx.dylib"));
    RuntimeDependencies.Add("$(BinaryOutputDir)/libaetherfx.dylib", Path.Combine(Lib, "Mac/libaetherfx.dylib"));
}
else if (Target.Platform == UnrealTargetPlatform.Linux)
{
    PublicAdditionalLibraries.Add(Path.Combine(Lib, "Linux/libaetherfx.so"));
    RuntimeDependencies.Add("$(BinaryOutputDir)/libaetherfx.so", Path.Combine(Lib, "Linux/libaetherfx.so"));
}
```

```cpp
#include "aetherfx/aetherfx.h"   // no extern "C" wrapper needed
```

To link statically instead, define `AETHERFX_STATIC` before including the
header and link the archives in dependency order, plus the C++ runtime:

```
libaetherfx_static.a libaether_sim.a libaether_compiler.a
libaether_procedural.a libaether_imageio.a libaether_core.a libtinyexr.a
-lc++            (clang)   /   -lstdc++ (gcc)
```

In Unreal that means `bUseAdaptiveUnityBuild`-neutral plain
`PublicAdditionalLibraries.AddRange(...)` of those files and
`PublicDefinitions.Add("AETHERFX_STATIC=1")`. The shared library is easier to
ship and hot-reload; static linking pays off for monolithic console builds.

### Unity (C#, P/Invoke)

Put `libaetherfx.dylib` / `libaetherfx.so` / `libaetherfx.dll` in
`Assets/Plugins/<platform>/` and declare the entry points with the library name
`aetherfx`. Mono probes both `aetherfx.<ext>` and `libaetherfx.<ext>`, so the
one name works on all three platforms (if your loader is stricter, rename the
Windows file to `aetherfx.dll`). Always pass `CallingConvention.Cdecl`.

```csharp
using System;
using System.Runtime.InteropServices;

internal static class AetherFX
{
    private const string Lib = "aetherfx";

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern int aetherfx_abi_version();

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr aetherfx_effect_load_file(
        [MarshalAs(UnmanagedType.LPUTF8Str)] string path);

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr aetherfx_compile(IntPtr effect, double fixedDt);

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr aetherfx_runtime_create(IntPtr compiled);

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern int aetherfx_runtime_simulate_to(IntPtr runtime, double time);

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern int aetherfx_particle_system_info(
        IntPtr runtime, int index, out ParticleSystemInfo info);

    [DllImport(Lib, CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr aetherfx_particle_position(IntPtr runtime, int index);

    [StructLayout(LayoutKind.Sequential)]
    public struct ParticleSystemInfo
    {
        public IntPtr id;              // Marshal.PtrToStringUTF8
        public UIntPtr count;
        public int renderMode, blend;
        public IntPtr materialId, spriteId, meshId;
        public int meshVariants, spriteColumns, spriteRows;
        public float spriteFps, velocityStretch;
        public int alignToVelocity;
        public float softParticleDistance;
        public int sort;
    }
}
```

Copy the arrays with `Marshal.Copy(ptr, managed, 0, n)` into a
`NativeArray<float>` or straight into a `ComputeBuffer`; do not hold the
`IntPtr` across a step. Match every struct field exactly - `int` for the enums
and the booleans, `UIntPtr` for `size_t`.

### Godot (GDExtension)

The header is plain C, so a GDExtension shared object links it the same way any
third-party C library does: add the include path, link `libaetherfx`, and ship
the library next to the extension in the `.gdextension` file's `[libraries]`
section per platform.

## 3. The lifecycle

Five calls carry the whole integration.

```c
#include <aetherfx/aetherfx.h>

/* 1. once per process: refuse a plugin built against a different ABI */
if (aetherfx_abi_version() != AETHERFX_ABI_VERSION) { /* bail out */ }

/* 2. once per effect asset, at load time (this is the expensive step) */
aetherfx_effect* effect = aetherfx_effect_load_file("effects/fireball.json");
aetherfx_compiled* compiled = aetherfx_compile(effect, 1.0 / 60.0);
if (!aetherfx_compiled_ok(compiled)) {
    char* why = aetherfx_compiled_diagnostics_json(compiled);   /* log and give up */
    aetherfx_free_string(why);
}
aetherfx_effect_free(effect);          /* the plan carries its own copy */
/* upload textures and meshes to the GPU here, once */

/* 3. once per playing instance */
aetherfx_runtime* runtime = aetherfx_runtime_create(compiled);

/* 4. every frame */
aetherfx_runtime_simulate_to(runtime, instance_play_time_seconds);
/* ...read the buffers and draw... */

/* 5. teardown */
aetherfx_runtime_free(runtime);
aetherfx_compiled_free(compiled);      /* only when no instance needs it */
```

`aetherfx_compiled` is shared, immutable and cheap to keep around; a runtime
copies the plan it needs, so runtimes outlive it safely. Keep one compiled
effect per asset and one runtime per instance.

`aetherfx_runtime_simulate_to(t)` takes as many fixed steps as it needs to reach
`t` and never interpolates: at 60 Hz with a 1/60 timestep it is one step, after
a hitch it is several, and on a frame that is too short it is none. It never
steps backwards - to replay an instance, call `aetherfx_runtime_reset()`. Pass
the instance's own elapsed play time, not the wall clock, so pausing and time
dilation work.

**Pointer lifetime.** Everything the frame accessors return points straight into
the runtime's buffers and is invalidated by the next `step`, `simulate_to` or
`reset` **on that runtime**. Read and copy inside the frame. Resource pointers
(texture pixels, mesh vertices) live as long as the compiled effect. Strings
returned by `*_json()` are yours to release with `aetherfx_free_string()`;
every other `const char*` is owned by the library.

## 4. Drawing a frame

```c
const int systems = aetherfx_runtime_particle_system_count(runtime);
for (int s = 0; s < systems; ++s) {
    struct aetherfx_particle_system_info info;
    if (aetherfx_particle_system_info(runtime, s, &info) != AETHERFX_OK) continue;
    if (info.count == 0) continue;

    const float* position = aetherfx_particle_position(runtime, s);   /* xyz  */
    const float* color    = aetherfx_particle_color(runtime, s);      /* rgba */
    const float* size     = aetherfx_particle_size(runtime, s);       /* meters, diameter */
    const float* opacity  = aetherfx_particle_opacity(runtime, s);
    const float* emissive = aetherfx_particle_emissive(runtime, s);
    const float* rotation = aetherfx_particle_rotation(runtime, s);   /* radians */
    const float* life01   = aetherfx_particle_custom0(runtime, s);    /* age / lifetime */
    /* fill one instance buffer and issue one draw call per system */
}
```

| buffer | draw as | key fields |
|---|---|---|
| particle system, `BILLBOARD` | camera-facing quad, `size` across, rolled by `rotation` | position, size, color, opacity, emissive, custom0, seed |
| particle system, `STRETCHED_BILLBOARD` | quad aligned to screen-space velocity, length `size * (1 + velocity_stretch * speed)` | + velocity, velocity_stretch |
| particle system, `MESH` | instanced mesh, transform = translate(position) * quat(orientation) * scale(size * scale3), mesh = variant `variant[i]` | + orientation, scale3, variant |
| particle system, `RIBBON` | the system's trail node draws it; skip the particles | - |
| particle system, `NONE` | nothing (simulation-only system, e.g. an event source) | - |
| light | the engine's own point/spot light | position, direction, color, intensity, radius, cone_angle_deg |
| beam | a camera-facing strip per polyline; polyline 0 is the bolt, the rest are branches (draw at ~0.6x width) | width, color, emissive, pulse_phase |
| trail | a camera-facing strip per ribbon, oldest vertex first | per-vertex position, width, u, color, opacity, emissive, normalized_age |
| decal | a projected decal, or a quad on the ground | position, normal, size, rotation_deg, circle, texture_id |
| mesh instance | a static mesh | mesh_id, transform (column-major), color, emissive, visible |

Notes that save an afternoon:

* `size` is a **world-space diameter** in meters, not a radius and not a scale.
* `color` is linear RGB with an unbounded (HDR) range; the alpha channel is
  unused - the alpha to draw with is `opacity` (times the material opacity and
  the sprite alpha).
* `custom0` is the normalized age. Use it to drive anything that fades, and to
  index a sprite sheet when `sprite_fps` is 0.
* `seed` is stable for the life of a particle. Hash it in the shader for
  per-particle jitter that does not swim.
* `previous_position` is the position at the previous step - motion vectors and
  motion blur come for free.
* `sort == 1` is the author asking for back-to-front sorting (what alpha-blended
  smoke needs); additive systems normally leave it 0 and can be drawn unsorted.
* Particles are packed in spawn order and stay that way; the index of a
  particle is not stable across steps (dead particles are compacted out).
* Beams and trails are rebuilt from scratch every step - do not cache their
  vertex counts.

## 5. Material mapping

Look a material up once per system (`aetherfx_material_index(compiled,
info.material_id)`, then `aetherfx_material_info`) and bind the engine shader
that matches it. The reference shading model, which your shader should
reproduce, is:

```
color    = particle.color.rgb * material.base_color.rgb
alpha    = sprite.a * particle.opacity * material.opacity [* erosion mask]
shaded   = unlit ? color : color * <engine lighting>
emission = color * material.emissive_color.rgb
                 * (particle.emissive + material.emissive_intensity)
src      = shaded + emission [+ fresnel rim]
```

| field | meaning | engine mapping |
|---|---|---|
| `blend = ADDITIVE` | `dst += src * a` | `One, One` with `src *= a`, depth test on, depth write off |
| `blend = ALPHA` | `dst = src*a + dst*(1-a)` | `SrcAlpha, OneMinusSrcAlpha` |
| `blend = PREMULTIPLIED` | `dst = src + dst*(1-a)` | `One, OneMinusSrcAlpha` |
| `shading = UNLIT` | emissive/self-lit sprite | unlit particle shader; no light sampling |
| `shading = LIT` | receives scene light | lit particle shader; the reference shades a billboard as a sphere-like puff (wrapped Lambert, 0.25 ambient, 0.35 translucency) |
| `emissive_color`, `emissive_intensity` | HDR emission, added **before** bloom | multiply into the emissive output so the engine's bloom picks it up |
| `fresnel_power` > 0 | rim term `pow(1 - saturate(dot(n, v)), power)`, added to the colour, tinted by `emissive_color * max(emissive_intensity, 0.35) * 2` | rim/fresnel term in the shader; it is what gives crystals and shields their silhouette |
| `soft_particle` | fade where the particle meets opaque geometry | depth-fade against the depth buffer over `soft_particle_distance` meters (from the **particle system info**); `depth_fade` on the material is the same distance for beams and trails |
| `dissolve`, `erosion` | noise-masked erase over the lifetime | `threshold = dissolve > 0 ? dissolve * (0.25 + 0.75*u) : 0.5 * erosion * u`, `edge = max(0.02, erosion)`, `alpha *= smoothstep(threshold - edge, threshold + edge, noise)` where `u = custom0` and `noise` is sampled from `noise_texture` at the particle UV offset by a hash of `seed` |
| `base_texture` | the material's texture when the system has no `sprite_id` | sample as the sprite |
| `noise_texture` | mask source for dissolve/erosion | bind alongside the sprite; when it is `""` use any tiling value noise |

A system's own `blend` is the authored default; when `material_id` is set, the
material's `blend` wins (that is what the reference renderer does).

## 6. Textures: frames and sprite sheets

Baked textures are linear RGBA float (`aetherfx_texture_pixels`) or 8-bit
(`aetherfx_texture_pixels_rgba8`, with optional sRGB encoding of RGB - alpha
stays linear). Two independent mechanisms animate them, and a system can use
both at once:

* **Frames** (`aetherfx_texture_info::frames`) live in the image itself, side by
  side: frame *i* covers columns `[i*frame_width, (i+1)*frame_width)`, full
  height. Select the frame rectangle first:
  `sprite_fps > 0 ? floor(age * sprite_fps) mod frames : floor(custom0 * frames)`.
* **Sprite sheet cells** (`sprite_columns` x `sprite_rows` on the particle
  system) subdivide **that frame rectangle** into a grid. The cell index is
  `sprite_fps > 0 ? floor(age * sprite_fps) mod cells : floor(custom0 * cells)`,
  laid out left to right, top to bottom.

Keep the UV half a texel inside the frame rectangle so bilinear taps never bleed
into the neighbouring frame. Upload a framed texture as one wide image (simplest)
or slice it into an array - the layout is in `frame_width`.

## 7. Meshes and variants

`aetherfx_mesh_positions/normals/uvs/indices` give tight float and `uint32`
arrays: positions and normals 3 floats, UVs 2 floats, indices a triangle list.
Normals and UVs can be absent (`has_normals`, `has_uvs`).

Seeded primitives (rock, crystal, shard) bake several variants so a debris
system does not repeat the same silhouette. They are stored under

```
"<mesh_id>"      variant 0
"<mesh_id>#1"    variant 1
"<mesh_id>#2"    ...
```

A mesh particle system reports how many exist in
`aetherfx_particle_system_info::mesh_variants`, and every particle carries its
own index in `aetherfx_particle_variant()`. Bucket the particles by variant and
issue one instanced draw per variant.

## 8. Units and coordinate systems

AetherFX is **meters, seconds, right-handed, Y-up, -Z forward** (the glTF
convention). Colours are linear, angles in the buffers are radians (fields named
`*_deg` are degrees), matrices are **column-major** with the translation in the
last column, quaternions are `(x, y, z, w)`.

| quantity | AetherFX | Unity (Y-up, left-handed, meters) | Unreal (Z-up, left-handed, centimeters) |
|---|---|---|---|
| position / direction `(x,y,z)` | as given | `(x, y, -z)` | `(-z, x, y) * 100` |
| distance, size, width, radius | meters | as given | `* 100` |
| quaternion `(x,y,z,w)` | as given | `(-x, -y, z, w)` | `(z, -x, -y, w)` |
| matrix | column-major | column-major (`Matrix4x4` is column-major) | `FMatrix` is row-major - transpose |
| colour | linear RGB (HDR) | linear (keep the project in linear space) | linear |
| angle | radians (`*_deg` fields: degrees) | radians / degrees as the API asks | degrees in blueprints |

The quaternion rule is the general one for a handedness flip: map the vector
part with the same axis rule as a position, then negate it (a rotation axis is a
pseudo-vector); `w` is unchanged. If you prefer not to trust a formula, build the
3x3 rotation matrix from the quaternion, map its columns with the position rule
and let the engine convert the matrix back to its own rotation type.

Scale is uniform unless the system uses mesh particles, where the final scale is
`size * scale3` per axis.

## 9. Threading

The simulation is CPU work with no I/O, so it belongs on a worker:

* One runtime per instance, stepped by one thread at a time. Handles are
  independent, so N runtimes on N workers is fine and needs no locking.
* Never read a runtime's buffers while another thread steps it. The usual shape
  is: kick the simulation jobs at the start of the frame, join before the render
  thread gathers the buffers, then copy into the engine's per-frame instance
  buffers.
* `aetherfx_compiled` is read-only once created and can be shared between
  threads and instances without a lock.
* `aetherfx_last_error()` is thread-local: check it on the thread that made the
  failing call.
* `aetherfx_effect_set_parameter()` mutates the effect and must not run while
  another thread compiles it. It only affects the **next** compile, never a
  running instance.

## 10. Determinism

Same document + same seed + same `fixed_dt` + same number of steps = bit
identical buffers, on every machine and in every host (`docs/RUNTIME.md`
section 10; the C API adds nothing to the path). That makes an effect safe for
lockstep and replay as long as every peer uses the same `fixed_dt` and drives
`simulate_to` from the same authoritative clock. Nothing in the simulation reads
wall-clock time; the only non-deterministic values in the API are the `*_ms`
timings in the statistics JSON.

Cosmetic-only integrations can float the timestep; a networked one should pin it
in the compile call and treat it as part of the asset.

## 11. Errors and diagnostics

Status-returning functions return `AETHERFX_OK` (0) or a negative
`aetherfx_status`; constructors return `NULL`. `aetherfx_last_error()` explains
the most recent failure on the calling thread. No exception can escape the
boundary - a failure is always a status, never a crash or a throw.

For content problems, ask for the diagnostics rather than the message:
`aetherfx_effect_validate()` before compiling, `aetherfx_compiled_diagnostics_json()`
after. Both return the standard document
`{"ok":bool,"errors":n,"warnings":n,"items":[{severity,code,message,node,param}]}`
with the codes from `docs/VOCABULARY.md`. A tools build should surface warnings
(a downgraded tier, a budget overrun) even when `ok` is true.
`aetherfx_compiled_plan_json()` dumps the whole execution plan and is the right
thing to attach to a bug report.

## 12. Versioning and ABI policy

`aetherfx_abi_version()` is the contract; `AETHERFX_ABI_VERSION` in the header
is what you compiled against. Compare them when you load the library and refuse
to run on a mismatch - it is one call and it turns a memory-corrupting struct
mismatch into a clear message.

* **ABI version** (starts at 1) changes only on a breaking change: a struct
  field removed, reordered or retyped, a signature changed, an enum value
  reassigned, a function removed. It is a new major release and a recompile.
* **Additive changes keep the ABI version**: new functions, new enum values
  appended, new structs. Existing callers keep working without a recompile.
  Fields are never appended to an existing struct - a new struct and a new
  accessor are added instead, because callers allocate these structs themselves.
* `aetherfx_version()` / `aetherfx_version_string()` report the build's
  semantic version, which moves independently of the ABI version.
* The **document** schema has its own version (`schema_version`); older
  documents are migrated forward on load, so an old effect asset keeps working
  with a new library.
* Enum values, status codes and struct layouts in the header are part of the
  ABI. Everything the header does not declare - including which C++ libraries
  are linked inside - is not, and can change at any time.

## 13. Current limitations

* One backend: the CPU runtime (`aetherfx_runtime_backend()` returns `"cpu"`).
  A GPU runtime will appear behind the same API and must match it.
* Volumes are a stub (Tier 3) and rigid-body physics falls back to the particle
  path (Tier 2); both report a compile warning and neither is exposed here.
* Post-effect state (bloom pushes, heat haze, chromatic aberration) and volume
  state are in the C++ `FrameState` but not yet in the C API; an engine drives
  its own post chain today.
* `aetherfx_effect_set_parameter()` sets constants only: it clears any keyframe
  track on the parameter it touches, and it needs a recompile to take effect.
  It is for instance tuning, not for animating a value per frame.
* Textures and meshes are baked at compile time and never change afterwards, so
  they can be uploaded once. There is no streaming or partial re-bake.
