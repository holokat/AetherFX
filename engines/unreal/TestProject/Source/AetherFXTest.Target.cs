// Copyright AetherFX.

using UnrealBuildTool;
using System.Collections.Generic;

public class AetherFXTestTarget : TargetRules
{
	public AetherFXTestTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("AetherFXTest");
	}
}
