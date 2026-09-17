// Copyright AetherFX.

using UnrealBuildTool;
using System.Collections.Generic;

public class AetherFXTestEditorTarget : TargetRules
{
	public AetherFXTestEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("AetherFXTest");
	}
}
