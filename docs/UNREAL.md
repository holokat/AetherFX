# Unreal Engine bridge (`engines/unreal/AetherFX`)

The AetherFX Unreal plugin plays AetherFX effects inside Unreal Engine 5.8.
`libaetherfx` **simulates** (the same deterministic fixed-step CPU simulation the
studio and the CLI run) and Unreal **renders** with its own primitives:
instanced static meshes, materials, lights, decals and procedural strips. No
AetherFX renderer, no Niagara.

Contents:

1. [Layout](#1-layout)
2. [Install](#2-install)
3. [Build](#3-build)
4. [Materials](#4-materials)
5. [Import an effect](#5-import-an-effect)
6. [Play an effect](#6-play-an-effect)
7. [What maps to what](#7-what-maps-to-what)
8. [Units and axes](#8-units-and-axes)
9. [Per-instance data and material parameters](#9-per-instance-data-and-material-parameters)
10. [Lights](#10-lights)
11. [Tests](#11-tests)
12. [Limitations](#12-limitations)

---

## 1. Layout

```
engines/unreal/
  AetherFX/                                  the plugin
    AetherFX.uplugin
    Content/Materials/*.uasset               the five generated materials
    Source/
      ThirdParty/AetherFXLib/                header + prebuilt static libs
        AetherFXLib.Build.cs
        PLATFORMS.md                         Win64 / Linux provisioning notes
        include/aetherfx/aetherfx.h          (committed, copied by sync_libs.sh)
        lib/Mac/*.a                          (NOT committed; sync_libs.sh writes it)
      AetherFXRuntime/                       runtime module, ships with the game
      AetherFXEditor/                        importer; editor only
  TestProject/                               minimal C++ project (AetherFXTest)
  scripts/
    sync_libs.sh                             build libaetherfx, copy into the plugin
    generate_materials.py                    build the five materials as .uasset
```

Module boundary: `AetherFXRuntime` depends only on Engine, RenderCore, RHI,
MeshDescription, ProceduralMeshComponent and `AetherFXLib`. It includes no
editor header, so it cooks into a packaged game. `AetherFXEditor` is where the
importer, the `UFactory` and the reimport handler live.

## 2. Install

**a. Put the plugin where the project can find it.** Either copy or symlink it
into `<YourProject>/Plugins/`:

```sh
ln -s "/abs/path/to/VFX Engine/engines/unreal/AetherFX" /abs/path/to/YourProject/Plugins/AetherFX
```

or, as `engines/unreal/TestProject` does, leave the plugin where it is and point
the `.uproject` at its parent directory:

```json
"AdditionalPluginDirectories": [ ".." ]
```

> A symlink inside `Plugins/` confuses Unreal Build Accelerator on macOS: it
> writes object files through the resolved path and then looks for them through
> the symlinked one, and the link step fails with
> `no such file or directory: .../AetherFXRuntime/<file>.cpp.o`. Prefer
> `AdditionalPluginDirectories`, or copy the plugin rather than linking it.

**b. Build `libaetherfx` and copy the archives in.** The binaries are not
committed:

```sh
engines/unreal/scripts/sync_libs.sh              # builds the CMake tree if needed
engines/unreal/scripts/sync_libs.sh --rebuild    # force a rebuild first
```

This writes `Source/ThirdParty/AetherFXLib/lib/Mac/*.a` and refreshes
`include/aetherfx/aetherfx.h` from `src/capi/include/aetherfx/aetherfx.h`.
`AetherFXLib.Build.cs` fails the build with a clear message if the archives are
missing, so a stale checkout never links half a plugin.

The link is **static** (`AETHERFX_STATIC=1`), in dependency order:

```
libaetherfx_static.a
libaether_sim.a  libaether_compiler.a  libaether_procedural.a
libaether_imageio.a  libaether_core.a  libtinyexr.a   + -lc++
```

so the packaged game ships no extra dynamic library.

**c. Enable the plugin** in the project's `.uproject` (or through
Edit > Plugins). `AetherFX` pulls in `ProceduralMeshComponent`.

```json
"Plugins": [
  { "Name": "AetherFX", "Enabled": true },
  { "Name": "ProceduralMeshComponent", "Enabled": true }
]
```

## 3. Build

```sh
"/Users/Shared/Epic Games/UE_5.8/Engine/Build/BatchFiles/Mac/Build.sh" \
    AetherFXTestEditor Mac Development \
    -Project="<abs>/engines/unreal/TestProject/AetherFXTest.uproject" \
    -WaitMutex
```

For your own project, swap `AetherFXTestEditor` for `<YourProject>Editor`. On
Windows use `Engine/Build/BatchFiles/Build.bat`, on Linux
`Engine/Build/BatchFiles/Linux/Build.sh` (see
[Limitations](#12-limitations) -- those platforms need their archives built
first).

**macOS note.** UE 5.8.1 accepts Xcode 15.2 to 26.9. If `xcode-select -p` points
at a newer Xcode, UBT refuses to build with
`SDK validation failed: found version 27.0, required version 15.2.0`. Select a
supported Xcode for the build without touching the machine's global setting:

```sh
export DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer
```

The linker may also warn that each AetherFX object file was
`built for newer 'macOS' version (27.0) than being linked (14.0)`. It is
harmless. To silence it, build the archives against Unreal's deployment target
in a tree of their own:

```sh
engines/unreal/scripts/sync_libs.sh --build-dir build-unreal \
    --osx-deployment-target 14.0 --rebuild
```

## 4. Materials

The plugin ships five materials in `AetherFX/Content/Materials`, generated by
`engines/unreal/scripts/generate_materials.py` through
`unreal.MaterialEditingLibrary` and committed as `.uasset`:

| asset | domain | blend | shading | used for |
|---|---|---|---|---|
| `M_AetherFX_Billboard_Additive` | Surface | Additive | Unlit | billboard/stretched particle systems whose material blend is `additive` |
| `M_AetherFX_Billboard_Translucent` | Surface | Translucent | Unlit | blend `alpha` and `premultiplied` |
| `M_AetherFX_Mesh` | Surface | Masked | Default Lit | mesh particles and analytic mesh instances; emissive + Fresnel rim |
| `M_AetherFX_Decal` | Deferred Decal | Translucent | - | decals |
| `M_AetherFX_Ribbon` | Surface | Alpha Composite | Unlit | trails and beams (per-vertex colour) |

Regenerate them (idempotent -- an existing material is emptied and rebuilt in
place, keeping its GUID so material instances survive):

```sh
export DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer   # macOS, see above
"/Users/Shared/Epic Games/UE_5.8/Engine/Binaries/Mac/UnrealEditor-Cmd" \
    "<abs>/engines/unreal/TestProject/AetherFXTest.uproject" \
    -run=pythonscript -script="<abs>/engines/unreal/scripts/generate_materials.py" \
    -unattended -nopause -nullrhi -NoSound -stdout
```

The script prints `[AetherFX materials] OK: 5 materials generated` and exits 0
on success; every failed node connection is reported individually and turns the
exit code non-zero. It needs the `PythonScriptPlugin` enabled (the test project
enables it).

**Billboards are oriented on the CPU, not in the shader.** `UAetherFXComponent`
builds each instance's camera-facing transform every tick
(`FRotationMatrix::MakeFromXZ(toView, up)` on a shared 1x1 unit quad), so the
materials only shade a quad. World-Position-Offset billboarding was rejected
because a small graph is what makes headless generation reliable, and the
per-instance sub-UV and colour data has to be read either way.

**If the materials are missing**, every accessor in `AetherFX::Materials` logs
one warning and falls back to `UMaterial::GetDefaultMaterial(MD_Surface)`. The
runtime never hard-depends on content that does not exist -- effects still
simulate and still produce instances, they just draw untextured. The automation
test `AetherFX.Materials.Generated` fails loudly in that case.

## 5. Import an effect

Produce a package from the repository root:

```sh
build/bin/aetherfx export examples/effects/fire_aoe.json \
    --format package --out out/packages/fire_aoe.aetherfx
```

Then either:

**Content browser.** Import and pick any file inside the package directory --
`manifest.json`, `runtime.json` or `effect.json`. The factory detects the
package from the manifest next to the chosen file (an `.aetherfx` *package* is a
directory, so the file dialog cannot select it directly). A bare
`<effect>.json` imports as a document.

**Script / build step.**

```python
import unreal
unreal.AetherFXImportLibrary.import_effect_package(
    "/abs/path/out/packages/fire_aoe.aetherfx",  # or a .json inside it
    "/Game/AetherFX",                            # destination package path
    "",                                          # asset name; "" derives FX_fire_aoe
    True)                                        # save straight away
```

The import creates a `UAetherFXEffect` holding:

* `EffectJson` -- the authoring document, which is what `libaetherfx` compiles;
* `Textures` -- `textures/*.png` as `UTexture2D` (sRGB, clamped, bilinear; frame
  strips get `TMGS_NoMipmaps` so mips cannot bleed across frames);
* `Meshes` -- `meshes/*.obj` through `FMeshDescription` +
  `UStaticMesh::BuildFromMeshDescriptions`, keyed `rock_mesh`, `rock_mesh_1`,
  ... (`#` is not legal in an FName, so `<id>#k` becomes `<id>_k`);
* `Materials` -- the baked `MaterialDesc` values as `FAetherFXMaterialParams`;
* `Duration`, `FixedTimeStep`, `SourceHash`, and the source path for reimport.

**Reimport** works from the content browser (Asset Actions > Reimport) and
rebuilds every sub-asset in place.

**No package, no importer.** An effect can also be played from raw JSON: set
`EffectJson` and call `Compile()`. With no imported resources the component
bakes transient `UTexture2D`s and `UStaticMesh`es out of the compiled effect
(`aetherfx_texture_pixels_rgba8`, `aetherfx_mesh_positions`), so a game that
ships effect documents instead of packages works with no editor code in the
loop. The `AetherFX.Bridge.DocumentOnly` test covers this path.

## 6. Play an effect

### C++

```cpp
#include "AetherFXActor.h"

// "the character casts the effect here"
AAetherFXActor::SpawnEffectAtLocation(this, MyEffect, HitLocation, FRotator::ZeroRotator, /*bAutoDestroy*/ true);
```

or on an existing actor:

```cpp
UAetherFXComponent* FX = CreateDefaultSubobject<UAetherFXComponent>(TEXT("FX"));
FX->Effect = MyEffect;
FX->bLoop  = false;
FX->PlaybackRate = 1.0f;
// ...
FX->Play();
```

### Blueprint

`AAetherFXActor` is `Blueprintable` with an `Effect` reference and an
**Auto Play** flag, plus:

| node | does |
|---|---|
| `Cast At (Location)` | moves the actor there and plays from t = 0 |
| `Cast At With Rotation` | same, with an orientation |
| `Play` / `Stop` | start / stop and release every drawn primitive |
| `Spawn Effect At Location` (static) | spawn + play in one call |

`UAetherFXComponent` is `BlueprintSpawnableComponent` and exposes `Play`,
`Stop`, `Seek`, `AdvanceAndRender`, `bLoop`, `PlaybackRate`, `LightScale` and a
`Stats` category (`GetInstanceCount`, `GetLightCount`, `GetDecalCount`,
`GetRibbonSectionCount`, `GetDrawableParticleCount`, `GetStatisticsJson`).

`Seek` never runs the simulation backwards -- seeking to an earlier time resets
the runtime and re-simulates from 0, which is exact but costs one fixed step per
frame of the interval.

## 7. What maps to what

| AetherFX frame buffer | Unreal |
|---|---|
| particle system, `billboard` / `stretched_billboard` | one `UInstancedStaticMeshComponent` of shared unit quads, CPU-oriented towards the view; per-instance custom data for colour, alpha, emissive and sub-UV |
| particle system, `mesh` | one `UInstancedStaticMeshComponent` per baked mesh variant, bucketed by `aetherfx_particle_variant()`; transform = position, orientation, `size * scale3` |
| particle system, `ribbon` / `none` | nothing (the trail node draws ribbons; `none` is simulation-only) |
| light | `UPointLightComponent` / `USpotLightComponent` |
| decal | `UDecalComponent` + a dynamic material instance |
| mesh instance | `UStaticMeshComponent` |
| trail | `UProceduralMeshComponent` section per trail, camera-facing strip per ribbon |
| beam | `UProceduralMeshComponent` section per beam; polyline 0 is the bolt, the rest are branches. The per-vertex width and the strike detail live behind `aetherfx_beam_style` / `aetherfx_beam_path`; the V1 bridge reads the plain polylines and draws branches at 0.6x width |
| camera | ignored -- Unreal uses its own |

Lights, decals and mesh instances are keyed by their AetherFX node id and reused
across frames; anything the simulation stops reporting is destroyed. Trails and
beams are rebuilt from scratch every frame because the library does not keep
their vertex counts stable.

Per-system toggles: `bEnableLights`, `bEnableDecals`, `bEnableTrailsAndBeams`,
`bCastShadows`, `bLightsCastShadows`.

## 8. Units and axes

AetherFX is metres, right-handed, **Y up**, -Z forward (glTF). Unreal is
centimetres, left-handed, **Z up**, +X forward. The full derivation lives in
`engines/unreal/AetherFX/Source/AetherFXRuntime/Public/AetherFXCoordinates.h`;
the short version:

| quantity | mapping |
|---|---|
| position (m -> cm) | `X = x*100`, `Y = z*100`, `Z = y*100` |
| direction | `(x, z, y)`, no unit factor |
| length / size / radius / width | `* 100` |
| quaternion `(x,y,z,w)` | `(x, z, y, -w)` |
| per-axis scale | `(sx, sz, sy)` |
| column-major matrix | rows = `S(c0)`, `S(c2)`, `S(c1)`, `S(c3)*100` where `S(v) = (v.x, v.z, v.y)` |
| colour | linear on both sides, copied straight through (alpha comes from `opacity`, not `color.a`) |

The axis map swaps Y and Z, which has determinant -1 -- that *is* the handedness
flip. The quaternion rule follows: a rotation `R` becomes `S*R*S`, which is a
rotation about the swapped axis by the negated angle, i.e. `(-S(v), w)`, and
negating the whole quaternion (same rotation) gives `(x, z, y, -w)`.

Mesh geometry is converted **once at import**: vertices are swapped and scaled to
centimetres and the triangle winding is reversed (the handedness flip would
otherwise turn every face inside out), so an instance transform carries only the
dimensionless `size * scale3`.

Verified by two automation tests:

* `AetherFX.Units.Mapping` -- the conversion helpers as pure arithmetic,
  including both quaternion cases and a matrix.
* `AetherFX.Units.AxisProbe` -- end to end. A two-system probe effect bursts
  four particles along AetherFX `+x` and four along `+y` at 1 m/s with no
  forces; after one simulated second the first group's instance transform must
  read `(100, 0, 0)` (Unreal **+X**) and the second `(0, 0, 100)` (Unreal
  **+Z**), each within 1 cm.

## 9. Per-instance data and material parameters

Every AetherFX instanced material reads ten floats of per-instance custom data
(`AetherFX::CustomData_*` in `AetherFXTypes.h`; the Python generator repeats the
table). All of it is computed on the CPU:

| index | meaning |
|---|---|
| 0, 1, 2 | linear RGB tint (the particle colour) |
| 3 | alpha (particle opacity) |
| 4 | per-particle emissive multiplier |
| 5, 6 | sub-UV offset `(u0, v0)` |
| 7, 8 | sub-UV scale `(du, dv)` -- the material does `UV * scale + offset` |
| 9 | normalised age, `age / lifetime` |

The sub-UV rectangle folds both animation mechanisms into one: a texture's
`frames` strips side by side, each subdivided by the system's
`sprite_columns` x `sprite_rows`, is a uniform grid of
`frames*sprite_columns` by `sprite_rows` cells. The cell is chosen with
`sprite_fps > 0 ? floor(age*fps) mod n : floor(custom0 * n)` and handed to the
shader as a plain offset/scale pair, so the material needs no flipbook maths.

Shader parameters a dynamic material instance is given per system:

| parameter | type | from |
|---|---|---|
| `BaseTexture` | texture | the system's `sprite_id`, else the material's `base_texture` |
| `NoiseTexture` | texture | the material's `noise_texture` |
| `BaseColor` | vector | `material.base_color` |
| `EmissiveColor` | vector | `material.emissive_color` |
| `EmissiveIntensity` | scalar | `material.emissive_intensity` |
| `Opacity` | scalar | `material.opacity` |
| `FresnelPower` | scalar | `material.fresnel_power` (mesh material only; 0 = no rim) |
| `Additive` | scalar | ribbon material only: 1 forces Opacity to 0, turning premultiplied into additive |

`AetherFX.Materials.Generated` asserts that each material exists, is not the
engine fallback, has the expected domain / blend / shading model, carries the
instanced-static-mesh usage flag where it needs it, and actually declares every
parameter listed above -- a missing parameter makes
`SetScalarParameterValue` a silent no-op, which is the kind of bug that only
shows up as "why is it grey".

## 10. Lights

```
candela = aetherfx_light_info::intensity * UAetherFXComponent::LightScale
```

`LightScale` defaults to **100** and is exposed on the component. AetherFX light
intensities are authored as a unitless brightness around 1..20; Unreal local
lights want candelas, so 100 puts a 1.0 AetherFX light at 100 cd, which reads
like a small torch at default exposure. Raise it for a brighter project, lower
it if your exposure is locked low.

The rest: `radius` (m) becomes the attenuation radius in cm, `color` is set
linear (`SetLightColor(..., bSRGB=false)`), a spot light's `cone_angle_deg` is
its outer cone with the inner at 0.75x, and shadows are off by default
(`bLightsCastShadows`) because effect lights flicker and shadow-casting point
lights are expensive.

## 11. Tests

Build, generate materials, then run headless:

```sh
export DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer   # macOS

"/Users/Shared/Epic Games/UE_5.8/Engine/Build/BatchFiles/Mac/Build.sh" \
    AetherFXTestEditor Mac Development \
    -Project="<abs>/engines/unreal/TestProject/AetherFXTest.uproject" -WaitMutex

"/Users/Shared/Epic Games/UE_5.8/Engine/Binaries/Mac/UnrealEditor-Cmd" \
    "<abs>/engines/unreal/TestProject/AetherFXTest.uproject" \
    -ExecCmds="Automation RunTests AetherFX; Quit" \
    -unattended -nopause -nullrhi -NoSound -log
```

| test | checks |
|---|---|
| `AetherFX.Bridge.Smoke` | imports `out/packages/fire_aoe.aetherfx` (falling back to `examples/effects/fire_aoe.json`), compiles it, spawns `AAetherFXActor`, `CastAt`s it, ticks 60 frames: instances > 0, lights > 0, decals > 0, every ISM registered with 10 custom-data floats, exactly 60 simulation steps, instance count == drawable particle count, statistics JSON available, clean `Stop()` and `Destroy()` |
| `AetherFX.Bridge.DocumentOnly` | the same effect as raw JSON with no imported resources: transient textures and meshes are baked from the compile and every system still instances |
| `AetherFX.Bridge.TrailsAndBeams` | `lightning_strike` (a beam) and, when it is present, the studio's `arcane_missile` (a trail and a `mesh` node): procedural mesh sections > 0, static mesh components > 0, and the dynamic material count stays flat across frames (the strips are rebuilt every frame; their materials must not be) |
| `AetherFX.Materials.Generated` | the five generated materials and their parameters (see 9) |
| `AetherFX.Units.AxisProbe` | +x -> +X and +y -> +Z end to end (see 8) |
| `AetherFX.Units.Mapping` | the conversion helpers |

## 12. Limitations

* **CPU simulation on the game thread.** `aetherfx_runtime_simulate_to()` runs
  inside `TickComponent`. The library is happy on a worker (one runtime per
  thread, see docs/ENGINE_INTEGRATION.md 9) but this bridge does not job it out
  yet. A 1700-particle effect is a few hundred microseconds; a screen full of
  them is not free.
* **Instances are rebuilt every frame.** Each ISM is cleared and refilled rather
  than diffed. Correct, and fine at these counts, but it is the first thing to
  optimise.
* **Trails and beams re-create their procedural mesh sections every frame**, for
  the same reason the library re-creates them: their vertex counts are not
  stable.
* **Billboards are CPU-oriented**, so they cost a rotation per particle per
  frame and they face the *first* player camera found (or, with no player
  camera -- editor viewport, commandlet -- a virtual camera 10 m along the
  component's -X). There is no per-view orientation, so split screen orients
  billboards for one view.
* **`premultiplied` blending is approximated** by the translucent billboard
  material. Trails and beams do get true premultiplied blending.
* **Not bridged:** volumes (a Tier 3 stub in the engine), post effects (bloom
  pushes, heat haze, chromatic aberration -- the C API does not expose them
  yet), soft-particle depth fade, `dissolve` / `erosion` noise masking,
  `uv_scroll` / `uv_rotate`, `temperature_gradient` and `distortion`. The
  material parameters are imported and stored on the asset; the generated
  materials just do not consume them yet.
* **Depth sorting** is left to Unreal's translucency sorting; the system's
  `sort` flag is not acted on.
* **Packaging the macOS `.app` was not verified.** The `AetherFXTest` (monolithic
  Game) target compiles the runtime module and links the executable -- which is
  what proves the module boundary holds outside the editor -- but UBT's final
  `.app` bundling step then fails on this machine with
  `Failed to finalize the .app with Xcode` /
  `Run custom shell script 'Touch UBT generated tiles'`, an Xcode-scheme problem
  with no AetherFX code in it. The Development Editor target builds and runs
  clean.
* **Win64 and Linux archives are TODO.** `AetherFXLib.Build.cs` throws a clear
  `BuildException` on those platforms with the file names it expects; see
  `engines/unreal/AetherFX/Source/ThirdParty/AetherFXLib/PLATFORMS.md`.
  `sync_libs.sh` already writes `lib/Linux/` when run on Linux.
* **Determinism.** `FixedTimeStep` is stored as a `double` on purpose -- 1/60
  rounded to `float` is larger than 1/60 and silently costs one simulation step
  per second, which is enough to make Unreal disagree with the studio. If you
  need lockstep-identical playback, drive `AdvanceAndRender` from the
  authoritative clock and keep `PlaybackRate` at 1.
