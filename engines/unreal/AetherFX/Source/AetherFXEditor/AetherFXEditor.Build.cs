// Copyright AetherFX.

using UnrealBuildTool;

public class AetherFXEditor : ModuleRules
{
	public AetherFXEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"UnrealEd",
			"AetherFXRuntime",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AssetTools",
			"AssetRegistry",
			"EditorFramework",
			"EditorSubsystem",
			"ImageCore",
			"ImageWrapper",
			"Json",
			"JsonUtilities",
			"MaterialEditor",
			"MeshDescription",
			"Projects",
			"RenderCore",
			"RHI",
			"Slate",
			"SlateCore",
			"StaticMeshDescription",
			"ToolMenus",
		});
	}
}
