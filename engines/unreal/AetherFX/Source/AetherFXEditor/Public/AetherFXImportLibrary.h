// Copyright AetherFX.

#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"

#include "AetherFXImportLibrary.generated.h"

class UAetherFXEffect;

/**
 * Scripted import, for Python commandlets, build steps and automation tests.
 *
 * `ImportEffectPackage("/abs/path/fire_aoe.aetherfx", "/Game/AetherFX")` does
 * exactly what the content-browser importer does, without a UI.
 */
UCLASS()
class AETHERFXEDITOR_API UAetherFXImportLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Imports an `.aetherfx` package directory (or a bare effect .json) and
	 * saves a UAetherFXEffect asset under `DestinationPackagePath`.
	 *
	 * @param SourcePath            absolute path to the package directory, or to
	 *                              a .json file inside it, or to a bare document
	 * @param DestinationPackagePath e.g. "/Game/AetherFX"
	 * @param AssetName             defaults to the source's base name
	 * @param bSave                 write the .uasset to disk straight away
	 */
	UFUNCTION(BlueprintCallable, Category = "AetherFX|Import")
	static UAetherFXEffect* ImportEffectPackage(
		const FString& SourcePath,
		const FString& DestinationPackagePath,
		const FString& AssetName,
		bool bSave = true);

	/**
	 * Imports without creating a persistent package: the effect lives in the
	 * transient package, resources included. This is what an automation test
	 * wants -- nothing to clean up.
	 */
	UFUNCTION(BlueprintCallable, Category = "AetherFX|Import")
	static UAetherFXEffect* ImportEffectTransient(const FString& SourcePath);
};
