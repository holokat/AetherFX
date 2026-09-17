// Copyright AetherFX.

#pragma once

#include "CoreMinimal.h"
#include "EditorReimportHandler.h"
#include "Factories/Factory.h"

#include "AetherFXEffectFactory.generated.h"

/**
 * Imports an `.aetherfx` interchange package or a bare effect document into a
 * UAetherFXEffect.
 *
 * A package is a *directory*, so the content browser import dialog is pointed at
 * a file inside it (`manifest.json`, `runtime.json` or `effect.json`); the
 * factory detects the package from the manifest next to the chosen file. A
 * stand-alone `<effect>.json` imports as a document with no baked resources --
 * the runtime then bakes its textures and meshes from the compile.
 */
UCLASS()
class AETHERFXEDITOR_API UAetherFXEffectFactory : public UFactory, public FReimportHandler
{
	GENERATED_BODY()

public:
	UAetherFXEffectFactory();

	//~ UFactory
	virtual bool FactoryCanImport(const FString& Filename) override;
	virtual UObject* FactoryCreateFile(
		UClass* InClass,
		UObject* InParent,
		FName InName,
		EObjectFlags Flags,
		const FString& Filename,
		const TCHAR* Parms,
		FFeedbackContext* Warn,
		bool& bOutOperationCanceled) override;

	//~ FReimportHandler
	virtual bool CanReimport(UObject* Obj, TArray<FString>& OutFilenames) override;
	virtual void SetReimportPaths(UObject* Obj, const TArray<FString>& NewReimportPaths) override;
	virtual EReimportResult::Type Reimport(UObject* Obj) override;
	virtual int32 GetPriority() const override;
};
