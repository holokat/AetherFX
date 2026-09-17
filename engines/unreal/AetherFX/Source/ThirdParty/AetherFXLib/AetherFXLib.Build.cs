// Copyright AetherFX. Third-party wrapper module for libaetherfx (the C ABI).
//
// This module carries no source of its own: it exposes the C header and the
// prebuilt archives of the AetherFX simulation library to every module that
// declares a dependency on "AetherFXLib".
//
// The binaries are NOT committed. Populate lib/<Platform>/ by running
//     engines/unreal/scripts/sync_libs.sh
// which configures/builds the CMake tree at the repository root and copies the
// archives plus include/aetherfx/aetherfx.h into this module.
//
// Link mode: STATIC. AETHERFX_STATIC=1 neutralises the dllimport/visibility
// attributes in aetherfx.h and we link the whole engine in dependency order, so
// a packaged game carries no extra dynamic library.

using System;
using System.IO;
using UnrealBuildTool;

public class AetherFXLib : ModuleRules
{
	public AetherFXLib(ReadOnlyTargetRules Target) : base(Target)
	{
		Type = ModuleType.External;

		PublicIncludePaths.Add(Path.Combine(ModuleDirectory, "include"));
		PublicDefinitions.Add("AETHERFX_STATIC=1");

		string LibRoot = Path.Combine(ModuleDirectory, "lib");

		// Dependency order matters for a static link: capi -> sim -> compiler ->
		// procedural -> imageio -> core -> tinyexr. Every archive below is
		// produced by the repository's CMake build.
		string[] Archives =
		{
			"aetherfx_static",
			"aether_sim",
			"aether_compiler",
			"aether_procedural",
			"aether_imageio",
			"aether_core",
			"tinyexr",
		};

		if (Target.Platform == UnrealTargetPlatform.Mac)
		{
			string MacDir = Path.Combine(LibRoot, "Mac");
			foreach (string Archive in Archives)
			{
				string Full = Path.Combine(MacDir, "lib" + Archive + ".a");
				if (!File.Exists(Full))
				{
					throw new BuildException(
						"AetherFXLib: missing '{0}'. Run engines/unreal/scripts/sync_libs.sh to " +
						"build libaetherfx and copy the archives into the plugin.", Full);
				}
				PublicAdditionalLibraries.Add(Full);
			}

			// libaether_* are C++; the C ABI hides the symbols but the runtime is
			// still needed at link time.
			PublicSystemLibraries.Add("c++");
		}
		else if (Target.Platform == UnrealTargetPlatform.Win64)
		{
			// TODO(win64): build libaetherfx with MSVC and drop the import
			// libraries into lib/Win64/ as:
			//     aetherfx_static.lib
			//     aether_sim.lib aether_compiler.lib aether_procedural.lib
			//     aether_imageio.lib aether_core.lib tinyexr.lib
			// then mirror the Mac branch (no "c++" system library on MSVC).
			// Until then the module links nothing and AetherFXRuntime will fail
			// to resolve aetherfx_* at link time, which is the intended loud
			// failure rather than a silent no-op plugin.
			throw new BuildException(
				"AetherFXLib: Win64 is not provisioned yet. Build libaetherfx with MSVC and place " +
				"aetherfx_static.lib, aether_sim.lib, aether_compiler.lib, aether_procedural.lib, " +
				"aether_imageio.lib, aether_core.lib and tinyexr.lib in " + Path.Combine(LibRoot, "Win64"));
		}
		else if (Target.Platform == UnrealTargetPlatform.Linux ||
				 Target.Platform == UnrealTargetPlatform.LinuxArm64)
		{
			// TODO(linux): build libaetherfx with the engine's bundled clang and
			// drop the archives into lib/Linux/ as:
			//     libaetherfx_static.a
			//     libaether_sim.a libaether_compiler.a libaether_procedural.a
			//     libaether_imageio.a libaether_core.a libtinyexr.a
			// then mirror the Mac branch (PublicSystemLibraries "c++" as well,
			// since the engine's toolchain uses libc++).
			throw new BuildException(
				"AetherFXLib: Linux is not provisioned yet. Build libaetherfx with the engine " +
				"toolchain and place the archives in " + Path.Combine(LibRoot, "Linux"));
		}
		else
		{
			throw new BuildException("AetherFXLib: unsupported platform " + Target.Platform);
		}
	}
}
