// Copyright AetherFX.

#include "AetherFXEffectFactory.h"

#include "AetherFXEffect.h"
#include "AetherFXLog.h"
#include "AetherFXPackageImporter.h"

#include "Editor.h"
#include "Misc/Paths.h"
#include "Subsystems/ImportSubsystem.h"

UAetherFXEffectFactory::UAetherFXEffectFactory()
{
	SupportedClass = UAetherFXEffect::StaticClass();
	bCreateNew = false;
	bEditorImport = true;
	bText = true;

	Formats.Add(TEXT("aetherfx;AetherFX effect package"));
	Formats.Add(TEXT("json;AetherFX effect document or package manifest"));
}

bool UAetherFXEffectFactory::FactoryCanImport(const FString& Filename)
{
	const FString Extension = FPaths::GetExtension(Filename).ToLower();
	if (Extension == TEXT("aetherfx"))
	{
		return true;
	}
	if (Extension != TEXT("json"))
	{
		return false;
	}

	// A package file is always importable. A bare .json is only ours when it
	// smells like an effect document: other plugins own plenty of .json too.
	const AetherFX::FImportSource Source = AetherFX::FPackageImporter::Classify(Filename);
	if (Source.bIsPackage)
	{
		return true;
	}

	FString Head;
	if (FFileHelper::LoadFileToString(Head, *Filename))
	{
		return Head.Contains(TEXT("\"schema_version\"")) && Head.Contains(TEXT("\"nodes\""));
	}
	return false;
}

UObject* UAetherFXEffectFactory::FactoryCreateFile(
	UClass* InClass,
	UObject* InParent,
	FName InName,
	EObjectFlags Flags,
	const FString& Filename,
	const TCHAR* Parms,
	FFeedbackContext* Warn,
	bool& bOutOperationCanceled)
{
	bOutOperationCanceled = false;

	if (GEditor)
	{
		GEditor->GetEditorSubsystem<UImportSubsystem>()->BroadcastAssetPreImport(this, InClass, InParent, InName, TEXT("aetherfx"));
	}

	UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(InParent, InName, Flags);
	if (!Effect)
	{
		return nullptr;
	}

	const AetherFX::FImportSource Source = AetherFX::FPackageImporter::Classify(Filename);
	if (!AetherFX::FPackageImporter::Import(Effect, Source))
	{
		UE_LOG(LogAetherFX, Error, TEXT("Import of '%s' failed."), *Filename);
		return nullptr;
	}

	if (GEditor)
	{
		GEditor->GetEditorSubsystem<UImportSubsystem>()->BroadcastAssetPostImport(this, Effect);
	}

	return Effect;
}

bool UAetherFXEffectFactory::CanReimport(UObject* Obj, TArray<FString>& OutFilenames)
{
#if WITH_EDITORONLY_DATA
	if (const UAetherFXEffect* Effect = Cast<UAetherFXEffect>(Obj))
	{
		if (!Effect->SourceFilePath.IsEmpty())
		{
			OutFilenames.Add(Effect->SourceFilePath);
			return true;
		}
	}
#endif
	return false;
}

void UAetherFXEffectFactory::SetReimportPaths(UObject* Obj, const TArray<FString>& NewReimportPaths)
{
#if WITH_EDITORONLY_DATA
	if (UAetherFXEffect* Effect = Cast<UAetherFXEffect>(Obj))
	{
		if (NewReimportPaths.Num() > 0)
		{
			Effect->SourceFilePath = NewReimportPaths[0];
		}
	}
#endif
}

EReimportResult::Type UAetherFXEffectFactory::Reimport(UObject* Obj)
{
#if WITH_EDITORONLY_DATA
	UAetherFXEffect* Effect = Cast<UAetherFXEffect>(Obj);
	if (!Effect || Effect->SourceFilePath.IsEmpty())
	{
		return EReimportResult::Failed;
	}

	const AetherFX::FImportSource Source = AetherFX::FPackageImporter::Classify(Effect->SourceFilePath);
	if (!AetherFX::FPackageImporter::Import(Effect, Source))
	{
		return EReimportResult::Failed;
	}

	Effect->MarkPackageDirty();
	return EReimportResult::Succeeded;
#else
	return EReimportResult::Failed;
#endif
}

int32 UAetherFXEffectFactory::GetPriority() const
{
	return ImportPriority;
}
