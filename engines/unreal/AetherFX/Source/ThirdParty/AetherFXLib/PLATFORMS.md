# AetherFXLib -- per-platform binaries

`lib/` is **not committed** (see the `.gitignore` entry at the repository root).
Populate it with:

```
engines/unreal/scripts/sync_libs.sh          # builds the CMake tree if needed
engines/unreal/scripts/sync_libs.sh --rebuild
```

The script also refreshes `include/aetherfx/aetherfx.h` from
`src/capi/include/aetherfx/aetherfx.h`, which **is** committed so the plugin
parses without a build.

## Mac (done)

`lib/Mac/` -- arm64 archives, linked statically in dependency order:

```
libaetherfx_static.a
libaether_sim.a  libaether_compiler.a  libaether_procedural.a
libaether_imageio.a  libaether_core.a  libtinyexr.a
```

plus the `c++` system library.

## Win64 (TODO)

Build `libaetherfx` with MSVC (Release) and copy into `lib/Win64/`:

```
aetherfx_static.lib
aether_sim.lib  aether_compiler.lib  aether_procedural.lib
aether_imageio.lib  aether_core.lib  tinyexr.lib
```

Then, in `AetherFXLib.Build.cs`, replace the `throw new BuildException` in the
`UnrealTargetPlatform.Win64` branch with the same `PublicAdditionalLibraries.Add`
loop the Mac branch uses (no `lib` prefix, `.lib` suffix) and drop the
`PublicSystemLibraries.Add("c++")` -- MSVC links its own CRT.

## Linux (TODO)

Build with the engine's bundled clang toolchain so the libc++ ABI matches the
editor, then copy into `lib/Linux/`:

```
libaetherfx_static.a
libaether_sim.a  libaether_compiler.a  libaether_procedural.a
libaether_imageio.a  libaether_core.a  libtinyexr.a
```

`sync_libs.sh` already writes to `lib/Linux/` when it runs on Linux. Mirror the
Mac branch in `AetherFXLib.Build.cs`, keeping `PublicSystemLibraries.Add("c++")`.
