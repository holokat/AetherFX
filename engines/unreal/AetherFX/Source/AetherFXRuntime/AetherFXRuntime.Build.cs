// Copyright AetherFX.

using UnrealBuildTool;

public class AetherFXRuntime : ModuleRules
{
	public AetherFXRuntime(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"ProceduralMeshComponent",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"RenderCore",
			"RHI",
			"MeshDescription",
			"StaticMeshDescription",
			"AetherFXLib",
		});
	}
}
