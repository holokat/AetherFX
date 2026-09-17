// Copyright AetherFX.

#include "AetherFXPackageImporter.h"

#include "AetherFXCoordinates.h"
#include "AetherFXEffect.h"
#include "AetherFXLog.h"
#include "AetherFXResources.h"
#include "AetherFXTypes.h"

#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "HAL/FileManager.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"

namespace AetherFX
{
	namespace
	{
		TSharedPtr<FJsonObject> LoadJsonObject(const FString& Path)
		{
			FString Text;
			if (!FFileHelper::LoadFileToString(Text, *Path))
			{
				return nullptr;
			}
			TSharedPtr<FJsonObject> Object;
			const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
			if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid())
			{
				return nullptr;
			}
			return Object;
		}

		FLinearColor ReadColor(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, const FLinearColor& Fallback)
		{
			const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
			if (!Object.IsValid() || !Object->TryGetArrayField(Field, Values) || !Values || Values->Num() < 3)
			{
				return Fallback;
			}
			return FLinearColor(
				static_cast<float>((*Values)[0]->AsNumber()),
				static_cast<float>((*Values)[1]->AsNumber()),
				static_cast<float>((*Values)[2]->AsNumber()),
				Values->Num() >= 4 ? static_cast<float>((*Values)[3]->AsNumber()) : 1.0f);
		}

		float ReadNumber(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, float Fallback)
		{
			double Value = 0.0;
			return (Object.IsValid() && Object->TryGetNumberField(Field, Value)) ? static_cast<float>(Value) : Fallback;
		}

		/** fixed_dt must keep full precision: see UAetherFXEffect::FixedTimeStep. */
		double ReadDouble(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, double Fallback)
		{
			double Value = 0.0;
			return (Object.IsValid() && Object->TryGetNumberField(Field, Value)) ? Value : Fallback;
		}

		FName ReadIdField(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field)
		{
			FString Value;
			if (Object.IsValid() && Object->TryGetStringField(Field, Value) && !Value.IsEmpty())
			{
				return FName(*Value);
			}
			return NAME_None;
		}

		EAetherFXBlend ParseBlend(const FString& Text)
		{
			if (Text.Equals(TEXT("alpha"), ESearchCase::IgnoreCase)) { return EAetherFXBlend::Alpha; }
			if (Text.Equals(TEXT("premultiplied"), ESearchCase::IgnoreCase)) { return EAetherFXBlend::Premultiplied; }
			return EAetherFXBlend::Additive;
		}

		/** Deletes any existing sub-object with this name so a reimport can reuse it. */
		void ClearStaleSubObject(UObject* Outer, FName Name)
		{
			if (UObject* Existing = StaticFindObjectFast(UObject::StaticClass(), Outer, Name))
			{
				Existing->ClearFlags(RF_Public | RF_Standalone);
				Existing->Rename(nullptr, GetTransientPackage(), REN_DontCreateRedirectors | REN_NonTransactional | REN_DoNotDirty);
				Existing->MarkAsGarbage();
			}
		}
	}

	FImportSource FPackageImporter::Classify(const FString& InFilename)
	{
		FImportSource Source;
		Source.PickedFile = FPaths::ConvertRelativePathToFull(InFilename);

		// The user may point at the package directory itself, or at any file in it.
		FString Directory = Source.PickedFile;
		if (!IFileManager::Get().DirectoryExists(*Directory))
		{
			Directory = FPaths::GetPath(Source.PickedFile);
		}

		const FString ManifestPath = FPaths::Combine(Directory, TEXT("manifest.json"));
		if (FPaths::FileExists(ManifestPath))
		{
			const TSharedPtr<FJsonObject> Manifest = LoadJsonObject(ManifestPath);
			FString Format;
			if (Manifest.IsValid() && Manifest->TryGetStringField(TEXT("format"), Format) && Format == TEXT("aetherfx-package"))
			{
				Source.bIsPackage = true;
				Source.PackageDirectory = Directory;
				if (IFileManager::Get().DirectoryExists(*Source.PickedFile))
				{
					// Remember a concrete file so reimport has something to stat.
					Source.PickedFile = ManifestPath;
				}
			}
		}

		return Source;
	}

	UTexture2D* FPackageImporter::ImportTexture(UObject* Outer, FName AssetName, const FString& PngPath, int32 Frames)
	{
		TArray<uint8> Compressed;
		if (!FFileHelper::LoadFileToArray(Compressed, *PngPath))
		{
			UE_LOG(LogAetherFX, Warning, TEXT("Import: cannot read texture '%s'."), *PngPath);
			return nullptr;
		}

		IImageWrapperModule& ImageWrapperModule = FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
		const TSharedPtr<IImageWrapper> Wrapper = ImageWrapperModule.CreateImageWrapper(EImageFormat::PNG);
		if (!Wrapper.IsValid() || !Wrapper->SetCompressed(Compressed.GetData(), Compressed.Num()))
		{
			UE_LOG(LogAetherFX, Warning, TEXT("Import: '%s' is not a readable PNG."), *PngPath);
			return nullptr;
		}

		TArray64<uint8> Raw;
		if (!Wrapper->GetRaw(ERGBFormat::BGRA, 8, Raw))
		{
			UE_LOG(LogAetherFX, Warning, TEXT("Import: cannot decode '%s' to BGRA8."), *PngPath);
			return nullptr;
		}

		const int32 Width = Wrapper->GetWidth();
		const int32 Height = Wrapper->GetHeight();
		if (Width <= 0 || Height <= 0)
		{
			return nullptr;
		}

		ClearStaleSubObject(Outer, AssetName);

		UTexture2D* Texture = NewObject<UTexture2D>(Outer, AssetName, RF_Public | RF_Standalone);
		Texture->Source.Init(Width, Height, 1, 1, TSF_BGRA8, Raw.GetData());
		Texture->SRGB = true;                        // the PNG stores sRGB-encoded linear data
		Texture->CompressionSettings = TC_Default;
		Texture->LODGroup = TEXTUREGROUP_Effects;
		Texture->AddressX = TA_Clamp;                // never wrap: frames sit side by side
		Texture->AddressY = TA_Clamp;
		Texture->Filter = TF_Bilinear;
		// A frame strip must not blur across frame boundaries in the lower mips.
		Texture->MipGenSettings = (Frames > 1) ? TMGS_NoMipmaps : TMGS_SimpleAverage;
		Texture->UpdateResource();
		Texture->PostEditChange();
		return Texture;
	}

	bool FPackageImporter::ReadObj(const FString& ObjPath, FMeshData& OutMesh)
	{
		OutMesh = FMeshData();

		TArray<FString> Lines;
		if (!FFileHelper::LoadFileToStringArray(Lines, *ObjPath))
		{
			return false;
		}

		TArray<FVector3f> Positions;
		TArray<FVector2f> UVs;
		TArray<FVector3f> Normals;

		// OBJ corners are (position, uv, normal) triples; a triple gets one
		// Unreal vertex, so flat-shaded meshes keep their hard edges.
		TMap<FString, int32> CornerToVertex;

		auto ParseCorner = [&](const FString& Token) -> int32
		{
			if (int32* Existing = CornerToVertex.Find(Token))
			{
				return *Existing;
			}

			TArray<FString> Parts;
			Token.ParseIntoArray(Parts, TEXT("/"), false);

			auto Resolve = [](const FString& Text, int32 Count) -> int32
			{
				if (Text.IsEmpty()) { return INDEX_NONE; }
				const int32 Value = FCString::Atoi(*Text);
				if (Value > 0) { return Value - 1; }            // 1-based
				if (Value < 0) { return Count + Value; }        // relative
				return INDEX_NONE;
			};

			const int32 PositionIndex = Parts.Num() > 0 ? Resolve(Parts[0], Positions.Num()) : INDEX_NONE;
			if (!Positions.IsValidIndex(PositionIndex))
			{
				return INDEX_NONE;
			}
			const int32 UVIndex = Parts.Num() > 1 ? Resolve(Parts[1], UVs.Num()) : INDEX_NONE;
			const int32 NormalIndex = Parts.Num() > 2 ? Resolve(Parts[2], Normals.Num()) : INDEX_NONE;

			const int32 NewIndex = OutMesh.Positions.Num();
			OutMesh.Positions.Add(Positions[PositionIndex]);
			OutMesh.UVs.Add(UVs.IsValidIndex(UVIndex) ? UVs[UVIndex] : FVector2f::ZeroVector);
			OutMesh.Normals.Add(Normals.IsValidIndex(NormalIndex) ? Normals[NormalIndex] : FVector3f::ZeroVector);
			CornerToVertex.Add(Token, NewIndex);
			return NewIndex;
		};

		bool bAnyNormals = false;
		bool bAnyUVs = false;

		for (const FString& RawLine : Lines)
		{
			const FString Line = RawLine.TrimStartAndEnd();
			if (Line.IsEmpty() || Line.StartsWith(TEXT("#")))
			{
				continue;
			}

			TArray<FString> Tokens;
			Line.ParseIntoArrayWS(Tokens);
			if (Tokens.Num() == 0)
			{
				continue;
			}

			if (Tokens[0] == TEXT("v") && Tokens.Num() >= 4)
			{
				// metres, (x, y, z) right-handed Y up -> centimetres, (x, z, y).
				const float X = FCString::Atof(*Tokens[1]);
				const float Y = FCString::Atof(*Tokens[2]);
				const float Z = FCString::Atof(*Tokens[3]);
				Positions.Emplace(
					X * static_cast<float>(MetersToUnreal),
					Z * static_cast<float>(MetersToUnreal),
					Y * static_cast<float>(MetersToUnreal));
			}
			else if (Tokens[0] == TEXT("vt") && Tokens.Num() >= 3)
			{
				// AetherFX writes v = 0 at the top of the image, which is what
				// Unreal samples, so the UVs copy across unchanged.
				UVs.Emplace(FCString::Atof(*Tokens[1]), FCString::Atof(*Tokens[2]));
				bAnyUVs = true;
			}
			else if (Tokens[0] == TEXT("vn") && Tokens.Num() >= 4)
			{
				const float X = FCString::Atof(*Tokens[1]);
				const float Y = FCString::Atof(*Tokens[2]);
				const float Z = FCString::Atof(*Tokens[3]);
				Normals.Emplace(X, Z, Y);
				bAnyNormals = true;
			}
			else if (Tokens[0] == TEXT("f") && Tokens.Num() >= 4)
			{
				TArray<int32, TInlineAllocator<8>> Corners;
				for (int32 Token = 1; Token < Tokens.Num(); ++Token)
				{
					const int32 Vertex = ParseCorner(Tokens[Token]);
					if (Vertex != INDEX_NONE)
					{
						Corners.Add(Vertex);
					}
				}
				// Fan-triangulate anything with more than three corners.
				for (int32 Corner = 2; Corner < Corners.Num(); ++Corner)
				{
					OutMesh.Indices.Add(static_cast<uint32>(Corners[0]));
					OutMesh.Indices.Add(static_cast<uint32>(Corners[Corner - 1]));
					OutMesh.Indices.Add(static_cast<uint32>(Corners[Corner]));
				}
			}
		}

		if (!bAnyNormals)
		{
			OutMesh.Normals.Reset();
		}
		if (!bAnyUVs)
		{
			OutMesh.UVs.Reset();
		}

		return OutMesh.IsValid();
	}

	bool FPackageImporter::Import(UAetherFXEffect* Effect, const FImportSource& Source)
	{
		if (!Effect)
		{
			return false;
		}

		UObject* Outer = Effect->GetOutermost();

		Effect->Textures.Reset();
		Effect->Meshes.Reset();
		Effect->Materials.Reset();
		Effect->InvalidateCompiled();

		//---------------------------------------------------------------------
		// Bare document: the JSON is the whole import.
		//---------------------------------------------------------------------
		if (!Source.bIsPackage)
		{
			FString Document;
			if (!FFileHelper::LoadFileToString(Document, *Source.PickedFile))
			{
				UE_LOG(LogAetherFX, Error, TEXT("Import: cannot read '%s'."), *Source.PickedFile);
				return false;
			}
			Effect->EffectJson = Document;
			Effect->FixedTimeStep = 0.0;
#if WITH_EDITORONLY_DATA
			Effect->SourceFilePath = Source.PickedFile;
#endif
			// Name/duration are filled in by the compile.
			const bool bCompiled = Effect->Compile();
			UE_LOG(LogAetherFX, Log, TEXT("Import: document '%s' (%s)."),
				*Source.PickedFile, bCompiled ? TEXT("compiles") : TEXT("does NOT compile"));
			return true;
		}

		//---------------------------------------------------------------------
		// Package import.
		//---------------------------------------------------------------------
		const FString ManifestPath = FPaths::Combine(Source.PackageDirectory, TEXT("manifest.json"));
		const TSharedPtr<FJsonObject> Manifest = LoadJsonObject(ManifestPath);
		if (!Manifest.IsValid())
		{
			UE_LOG(LogAetherFX, Error, TEXT("Import: '%s' is not readable JSON."), *ManifestPath);
			return false;
		}

		int32 Version = 0;
		Manifest->TryGetNumberField(TEXT("version"), Version);
		if (Version > 1)
		{
			UE_LOG(LogAetherFX, Error, TEXT("Import: package version %d is newer than this plugin understands (1)."), Version);
			return false;
		}

		const FString RuntimePath = FPaths::Combine(Source.PackageDirectory, TEXT("runtime.json"));
		const TSharedPtr<FJsonObject> Runtime = LoadJsonObject(RuntimePath);

		// The authoring document is what libaetherfx compiles.
		const FString EffectPath = FPaths::Combine(Source.PackageDirectory, TEXT("effect.json"));
		FString Document;
		if (!FFileHelper::LoadFileToString(Document, *EffectPath))
		{
			UE_LOG(LogAetherFX, Error, TEXT("Import: package has no readable effect.json ('%s')."), *EffectPath);
			return false;
		}
		Effect->EffectJson = Document;

		Manifest->TryGetStringField(TEXT("effect"), Effect->EffectName);
		Manifest->TryGetStringField(TEXT("source_hash"), Effect->SourceHash);
		Effect->Duration = ReadNumber(Manifest, TEXT("duration"), 0.0f);
		Effect->FixedTimeStep = ReadDouble(Manifest, TEXT("fixed_dt"), 0.0);
		// The package already carries the resolved speed, so the asset is right
		// even for a document that does not compile; Compile() confirms it.
		Effect->TimeScale = ReadDouble(Manifest, TEXT("time_scale"), 1.0);
#if WITH_EDITORONLY_DATA
		Effect->SourceFilePath = Source.PickedFile;
#endif

		//---- textures -------------------------------------------------------
		if (Runtime.IsValid())
		{
			const TArray<TSharedPtr<FJsonValue>>* Textures = nullptr;
			if (Runtime->TryGetArrayField(TEXT("textures"), Textures) && Textures)
			{
				for (const TSharedPtr<FJsonValue>& Value : *Textures)
				{
					const TSharedPtr<FJsonObject> Entry = Value->AsObject();
					if (!Entry.IsValid()) { continue; }

					FString Id, File;
					if (!Entry->TryGetStringField(TEXT("id"), Id) || !Entry->TryGetStringField(TEXT("file"), File))
					{
						continue;
					}

					int32 Frames = 1;
					Entry->TryGetNumberField(TEXT("frames"), Frames);

					const FString Absolute = FPaths::Combine(Source.PackageDirectory, File);
					const FName AssetName(*FString::Printf(TEXT("T_%s"), *UAetherFXEffect::SanitizeResourceId(Id)));
					if (UTexture2D* Texture = ImportTexture(Outer, AssetName, Absolute, Frames))
					{
						Effect->Textures.Add(FName(*Id), Texture);
					}
				}
			}
		}

		//---- meshes ---------------------------------------------------------
		if (Runtime.IsValid())
		{
			const TArray<TSharedPtr<FJsonValue>>* Meshes = nullptr;
			if (Runtime->TryGetArrayField(TEXT("meshes"), Meshes) && Meshes)
			{
				for (const TSharedPtr<FJsonValue>& Value : *Meshes)
				{
					const TSharedPtr<FJsonObject> Entry = Value->AsObject();
					if (!Entry.IsValid()) { continue; }

					FString Id;
					if (!Entry->TryGetStringField(TEXT("id"), Id))
					{
						continue;
					}

					TArray<FString> Files;
					const TArray<TSharedPtr<FJsonValue>>* FileList = nullptr;
					if (Entry->TryGetArrayField(TEXT("files"), FileList) && FileList)
					{
						for (const TSharedPtr<FJsonValue>& FileValue : *FileList)
						{
							Files.Add(FileValue->AsString());
						}
					}
					if (Files.Num() == 0)
					{
						FString Single;
						if (Entry->TryGetStringField(TEXT("file"), Single) && !Single.IsEmpty())
						{
							Files.Add(Single);
						}
					}

					for (int32 Variant = 0; Variant < Files.Num(); ++Variant)
					{
						const FString Absolute = FPaths::Combine(Source.PackageDirectory, Files[Variant]);

						FMeshData Data;
						if (!ReadObj(Absolute, Data))
						{
							UE_LOG(LogAetherFX, Warning, TEXT("Import: '%s' is not a readable OBJ."), *Absolute);
							continue;
						}

						const FName Key = UAetherFXEffect::MakeMeshVariantKey(Id, Variant);
						const FName AssetName(*FString::Printf(TEXT("SM_%s"), *Key.ToString()));
						ClearStaleSubObject(Outer, AssetName);

						if (UStaticMesh* Mesh = BuildStaticMesh(Outer, AssetName, RF_Public | RF_Standalone, Data, Materials::Mesh()))
						{
							Effect->Meshes.Add(Key, Mesh);
						}
					}
				}
			}
		}

		//---- materials ------------------------------------------------------
		if (Runtime.IsValid())
		{
			const TArray<TSharedPtr<FJsonValue>>* MaterialList = nullptr;
			if (Runtime->TryGetArrayField(TEXT("materials"), MaterialList) && MaterialList)
			{
				for (const TSharedPtr<FJsonValue>& Value : *MaterialList)
				{
					const TSharedPtr<FJsonObject> Entry = Value->AsObject();
					if (!Entry.IsValid()) { continue; }

					FString Id;
					if (!Entry->TryGetStringField(TEXT("id"), Id))
					{
						continue;
					}

					FAetherFXMaterialParams Params;
					Params.Id = FName(*Id);

					FString BlendText;
					Entry->TryGetStringField(TEXT("blend"), BlendText);
					Params.Blend = ParseBlend(BlendText);

					FString ShadingText;
					Entry->TryGetStringField(TEXT("shading"), ShadingText);
					Params.Shading = ShadingText.Equals(TEXT("lit"), ESearchCase::IgnoreCase)
						? EAetherFXShading::Lit
						: EAetherFXShading::Unlit;

					Params.BaseColor = ReadColor(Entry, TEXT("base_color"), FLinearColor::White);
					Params.EmissiveColor = ReadColor(Entry, TEXT("emissive_color"), FLinearColor::White);
					Params.Opacity = ReadNumber(Entry, TEXT("opacity"), 1.0f);
					Params.EmissiveIntensity = ReadNumber(Entry, TEXT("emissive_intensity"), 0.0f);
					Params.FresnelPower = ReadNumber(Entry, TEXT("fresnel_power"), 0.0f);
					Params.Dissolve = ReadNumber(Entry, TEXT("dissolve"), 0.0f);
					Params.Erosion = ReadNumber(Entry, TEXT("erosion"), 0.0f);
					Params.DepthFade = ReadNumber(Entry, TEXT("depth_fade"), 0.0f);
					Entry->TryGetBoolField(TEXT("soft_particle"), Params.bSoftParticle);
					Params.BaseTexture = ReadIdField(Entry, TEXT("base_texture"));
					Params.NoiseTexture = ReadIdField(Entry, TEXT("noise_texture"));

					Effect->Materials.Add(Params.Id, Params);
				}
			}
		}

		const bool bCompiled = Effect->Compile();

		UE_LOG(LogAetherFX, Log,
			TEXT("Imported '%s': %d textures, %d meshes, %d materials, duration %.2fs (plays in %.2fs at %.2fx), "
				 "dt %.6f (%s)."),
			*Effect->EffectName,
			Effect->Textures.Num(), Effect->Meshes.Num(), Effect->Materials.Num(),
			Effect->Duration, Effect->GetWallDuration(), Effect->TimeScale, Effect->FixedTimeStep,
			bCompiled ? TEXT("compiles") : TEXT("does NOT compile"));

		return true;
	}
}
