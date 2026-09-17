// Copyright AetherFX.

using UnrealBuildTool;

public class AetherFXTest : ModuleRules
{
	public AetherFXTest(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"AetherFXRuntime",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"Projects",
		});

		if (Target.bBuildEditor)
		{
			PrivateDependencyModuleNames.Add("UnrealEd");
			PrivateDependencyModuleNames.Add("AetherFXEditor");
		}
	}
}
