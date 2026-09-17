// Copyright AetherFX.

#include "AetherFXImportLibrary.h"

#include "AetherFXEffect.h"
#include "AetherFXLog.h"
#include "AetherFXPackageImporter.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "FileHelpers.h"
#include "HAL/FileManager.h"
#include "Misc/Paths.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace
{
	FString DeriveAssetName(const FString& SourcePath)
	{
		FString Base = SourcePath;
		Base.RemoveFromEnd(TEXT("/"));

		// A package is a directory named `<effect>.aetherfx`; a file inside one
		// is `manifest.json`, which would be a terrible asset name.
		const FString Extension = FPaths::GetExtension(Base).ToLower();
		if (Extension == TEXT("json"))
		{
			const FString Directory = FPaths::GetPath(Base);
			if (FPaths::GetExtension(Directory).ToLower() == TEXT("aetherfx"))
			{
				Base = Directory;
			}
		}

		FString Name = FPaths::GetBaseFilename(Base);
		if (Name.IsEmpty())
		{
			Name = TEXT("AetherFXEffect");
		}
		return FString::Printf(TEXT("FX_%s"), *UAetherFXEffect::SanitizeResourceId(Name));
	}
}

UAetherFXEffect* UAetherFXImportLibrary::ImportEffectPackage(
	const FString& SourcePath,
	const FString& DestinationPackagePath,
	const FString& AssetName,
	bool bSave)
{
	const FString Absolute = FPaths::ConvertRelativePathToFull(SourcePath);
	if (!FPaths::FileExists(Absolute) && !IFileManager::Get().DirectoryExists(*Absolute))
	{
		UE_LOG(LogAetherFX, Error, TEXT("ImportEffectPackage: '%s' does not exist."), *Absolute);
		return nullptr;
	}

	const FString FinalName = AssetName.IsEmpty() ? DeriveAssetName(Absolute) : AssetName;
	const FString Destination = DestinationPackagePath.IsEmpty() ? TEXT("/Game/AetherFX") : DestinationPackagePath;
	const FString PackageName = FString::Printf(TEXT("%s/%s"), *Destination, *FinalName);

	UPackage* Package = CreatePackage(*PackageName);
	if (!Package)
	{
		UE_LOG(LogAetherFX, Error, TEXT("ImportEffectPackage: cannot create package '%s'."), *PackageName);
		return nullptr;
	}
	Package->FullyLoad();

	UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(Package, FName(*FinalName), RF_Public | RF_Standalone);
	const AetherFX::FImportSource Source = AetherFX::FPackageImporter::Classify(Absolute);
	if (!AetherFX::FPackageImporter::Import(Effect, Source))
	{
		return nullptr;
	}

	FAssetRegistryModule::AssetCreated(Effect);
	Package->MarkPackageDirty();

	if (bSave)
	{
		const FString Filename = FPackageName::LongPackageNameToFilename(PackageName, FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, Effect, *Filename, SaveArgs))
		{
			UE_LOG(LogAetherFX, Warning, TEXT("ImportEffectPackage: could not save '%s'."), *Filename);
		}
		else
		{
			UE_LOG(LogAetherFX, Log, TEXT("ImportEffectPackage: saved '%s'."), *Filename);
		}
	}

	return Effect;
}

UAetherFXEffect* UAetherFXImportLibrary::ImportEffectTransient(const FString& SourcePath)
{
	const FString Absolute = FPaths::ConvertRelativePathToFull(SourcePath);
	if (!FPaths::FileExists(Absolute) && !IFileManager::Get().DirectoryExists(*Absolute))
	{
		UE_LOG(LogAetherFX, Error, TEXT("ImportEffectTransient: '%s' does not exist."), *Absolute);
		return nullptr;
	}

	const FName Name = MakeUniqueObjectName(GetTransientPackage(), UAetherFXEffect::StaticClass(), FName(*DeriveAssetName(Absolute)));
	UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(GetTransientPackage(), Name, RF_Transient);

	const AetherFX::FImportSource Source = AetherFX::FPackageImporter::Classify(Absolute);
	if (!AetherFX::FPackageImporter::Import(Effect, Source))
	{
		return nullptr;
	}
	return Effect;
}
