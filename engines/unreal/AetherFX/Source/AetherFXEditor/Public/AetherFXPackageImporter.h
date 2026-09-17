// Copyright AetherFX.
//
// Reads an `.aetherfx` interchange package (docs/PACKAGE_FORMAT.md) or a bare
// effect document and fills a UAetherFXEffect with the document plus the
// imported textures, meshes and material parameters.

#pragma once

#include "CoreMinimal.h"

class UAetherFXEffect;
class UObject;
class UStaticMesh;
class UTexture2D;

namespace AetherFX
{
	struct FMeshData;

	/** Where an import is reading from. */
	struct FImportSource
	{
		/** The file the user picked (runtime.json / manifest.json / effect.json / any .json). */
		FString PickedFile;
		/** The package directory when this is a package import; empty for a bare document. */
		FString PackageDirectory;
		/** True when PackageDirectory holds a valid `aetherfx-package` manifest. */
		bool bIsPackage = false;
	};

	class AETHERFXEDITOR_API FPackageImporter
	{
	public:
		/**
		 * Classifies `InFilename`: a file inside an `.aetherfx` package directory
		 * (or the directory itself) becomes a package import, anything else a
		 * bare-document import.
		 */
		static FImportSource Classify(const FString& InFilename);

		/**
		 * Imports into `Effect`, creating textures and meshes as sub-objects of
		 * `Effect`'s package. Existing sub-objects with the same name are
		 * replaced, so this doubles as the reimport path.
		 */
		static bool Import(UAetherFXEffect* Effect, const FImportSource& Source);

		/** Parses a minimal Wavefront OBJ into Unreal-space geometry. */
		static bool ReadObj(const FString& ObjPath, FMeshData& OutMesh);

		/** Decodes a PNG into a persistent UTexture2D owned by `Outer`. */
		static UTexture2D* ImportTexture(UObject* Outer, FName AssetName, const FString& PngPath, int32 Frames);
	};
}
