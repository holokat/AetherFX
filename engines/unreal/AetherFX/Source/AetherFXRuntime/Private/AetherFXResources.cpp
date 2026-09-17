// Copyright AetherFX.

#include "AetherFXResources.h"

#include "AetherFXCoordinates.h"
#include "AetherFXHandles.h"
#include "AetherFXLog.h"

#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "Materials/Material.h"
#include "Materials/MaterialInterface.h"
#include "MeshDescription.h"
#include "StaticMeshAttributes.h"
#include "StaticMeshOperations.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

#include "aetherfx/aetherfx.h"

namespace AetherFX
{
	UStaticMesh* BuildStaticMesh(UObject* Outer, FName Name, EObjectFlags Flags, const FMeshData& Data, UMaterialInterface* Material)
	{
		if (!Data.IsValid())
		{
			UE_LOG(LogAetherFX, Warning, TEXT("BuildStaticMesh('%s'): %d positions / %d indices is not a mesh."),
				*Name.ToString(), Data.Positions.Num(), Data.Indices.Num());
			return nullptr;
		}

		FMeshDescription MeshDescription;
		FStaticMeshAttributes Attributes(MeshDescription);
		Attributes.Register();
		MeshDescription.SetNumUVChannels(1);

		TVertexAttributesRef<FVector3f> VertexPositions = Attributes.GetVertexPositions();
		TVertexInstanceAttributesRef<FVector3f> InstanceNormals = Attributes.GetVertexInstanceNormals();
		TVertexInstanceAttributesRef<FVector2f> InstanceUVs = Attributes.GetVertexInstanceUVs();
		TVertexInstanceAttributesRef<FVector4f> InstanceColors = Attributes.GetVertexInstanceColors();
		TPolygonGroupAttributesRef<FName> SlotNames = Attributes.GetPolygonGroupMaterialSlotNames();

		const int32 NumVertices = Data.Positions.Num();
		const int32 NumTriangles = Data.Indices.Num() / 3;

		MeshDescription.ReserveNewVertices(NumVertices);
		MeshDescription.ReserveNewVertexInstances(NumTriangles * 3);
		MeshDescription.ReserveNewTriangles(NumTriangles);
		MeshDescription.ReserveNewPolygons(NumTriangles);
		MeshDescription.ReserveNewEdges(NumTriangles * 3);

		TArray<FVertexID> VertexIDs;
		VertexIDs.Reserve(NumVertices);
		for (int32 VertexIndex = 0; VertexIndex < NumVertices; ++VertexIndex)
		{
			const FVertexID VertexID = MeshDescription.CreateVertex();
			VertexPositions[VertexID] = Data.Positions[VertexIndex];
			VertexIDs.Add(VertexID);
		}

		const FPolygonGroupID PolygonGroup = MeshDescription.CreatePolygonGroup();
		SlotNames[PolygonGroup] = FName(TEXT("AetherFX"));

		const bool bHasNormals = Data.Normals.Num() == NumVertices;
		const bool bHasUVs = Data.UVs.Num() == NumVertices;

		for (int32 Triangle = 0; Triangle < NumTriangles; ++Triangle)
		{
			const uint32 I0 = Data.Indices[Triangle * 3 + 0];
			const uint32 I1 = Data.Indices[Triangle * 3 + 1];
			const uint32 I2 = Data.Indices[Triangle * 3 + 2];
			if (!VertexIDs.IsValidIndex(static_cast<int32>(I0)) ||
				!VertexIDs.IsValidIndex(static_cast<int32>(I1)) ||
				!VertexIDs.IsValidIndex(static_cast<int32>(I2)))
			{
				continue;
			}

			// AetherFX winds counter-clockwise for a right-handed front face; the
			// axis swap to Unreal's left-handed space flips that, so the winding
			// is reversed here to keep front faces facing forward.
			const uint32 Corner[3] = { I0, I2, I1 };

			TArray<FVertexInstanceID, TInlineAllocator<3>> Instances;
			for (int32 Slot = 0; Slot < 3; ++Slot)
			{
				const uint32 Source = Corner[Slot];
				const FVertexInstanceID InstanceID = MeshDescription.CreateVertexInstance(VertexIDs[static_cast<int32>(Source)]);
				InstanceNormals[InstanceID] = bHasNormals
					? Data.Normals[static_cast<int32>(Source)]
					: FVector3f::ZeroVector;
				// AetherFX UVs put v = 0 at the top of the image, which is also
				// what Unreal samples, so they copy straight through.
				InstanceUVs.Set(InstanceID, 0, bHasUVs ? Data.UVs[static_cast<int32>(Source)] : FVector2f::ZeroVector);
				InstanceColors[InstanceID] = FVector4f(1.0f, 1.0f, 1.0f, 1.0f);
				Instances.Add(InstanceID);
			}

			MeshDescription.CreatePolygon(PolygonGroup, Instances);
		}

		if (!bHasNormals)
		{
			FStaticMeshOperations::ComputeTriangleTangentsAndNormals(MeshDescription);
			FStaticMeshOperations::ComputeTangentsAndNormals(MeshDescription, EComputeNTBsFlags::Normals | EComputeNTBsFlags::Tangents);
		}

		UStaticMesh* StaticMesh = NewObject<UStaticMesh>(Outer ? Outer : GetTransientPackage(), Name, Flags);
		StaticMesh->InitResources();
		StaticMesh->SetLightingGuid();

		// The three-argument FStaticMaterial takes an imported slot name, which is
		// editor-only data; this two-argument form compiles in a cooked build too.
		StaticMesh->GetStaticMaterials().Add(FStaticMaterial(
			Material ? Material : UMaterial::GetDefaultMaterial(MD_Surface),
			FName(TEXT("AetherFX"))));

		UStaticMesh::FBuildMeshDescriptionsParams Params;
		Params.bBuildSimpleCollision = false;
		Params.bMarkPackageDirty = false;
		Params.bCommitMeshDescription = true;
		Params.bFastBuild = true;
		Params.bAllowCpuAccess = false;

		TArray<const FMeshDescription*> Descriptions;
		Descriptions.Add(&MeshDescription);

		if (!StaticMesh->BuildFromMeshDescriptions(Descriptions, Params))
		{
			UE_LOG(LogAetherFX, Warning, TEXT("BuildStaticMesh('%s'): BuildFromMeshDescriptions failed."), *Name.ToString());
			return nullptr;
		}

		StaticMesh->CalculateExtendedBounds();
		return StaticMesh;
	}

	bool ReadCompiledMesh(const aetherfx_compiled* Compiled, int32 Index, FMeshData& Out)
	{
		Out = FMeshData();
		if (!Compiled)
		{
			return false;
		}

		struct aetherfx_mesh_info Info;
		if (aetherfx_mesh_info(Compiled, Index, &Info) != AETHERFX_OK)
		{
			return false;
		}

		const float* Positions = aetherfx_mesh_positions(Compiled, Index);
		const uint32* Indices = aetherfx_mesh_indices(Compiled, Index);
		if (!Positions || !Indices || Info.vertex_count == 0 || Info.index_count < 3)
		{
			return false;
		}

		const float* Normals = Info.has_normals ? aetherfx_mesh_normals(Compiled, Index) : nullptr;
		const float* UVs = Info.has_uvs ? aetherfx_mesh_uvs(Compiled, Index) : nullptr;

		const int32 VertexCount = static_cast<int32>(Info.vertex_count);
		Out.Positions.Reserve(VertexCount);
		if (Normals) { Out.Normals.Reserve(VertexCount); }
		if (UVs) { Out.UVs.Reserve(VertexCount); }

		for (int32 Vertex = 0; Vertex < VertexCount; ++Vertex)
		{
			// (x, y, z) metres -> (x, z, y) centimetres.
			Out.Positions.Emplace(
				Positions[Vertex * 3 + 0] * static_cast<float>(MetersToUnreal),
				Positions[Vertex * 3 + 2] * static_cast<float>(MetersToUnreal),
				Positions[Vertex * 3 + 1] * static_cast<float>(MetersToUnreal));

			if (Normals)
			{
				Out.Normals.Emplace(
					Normals[Vertex * 3 + 0],
					Normals[Vertex * 3 + 2],
					Normals[Vertex * 3 + 1]);
			}
			if (UVs)
			{
				Out.UVs.Emplace(UVs[Vertex * 2 + 0], UVs[Vertex * 2 + 1]);
			}
		}

		Out.Indices.Append(Indices, static_cast<int32>(Info.index_count));
		return Out.IsValid();
	}

	UStaticMesh* GetUnitQuadMesh()
	{
		static TWeakObjectPtr<UStaticMesh> Cached;
		if (Cached.IsValid())
		{
			return Cached.Get();
		}

		// A 1x1 quad in the local YZ plane, normal +X, centred on the origin.
		FMeshData Data;
		Data.Positions = {
			FVector3f(0.0f,  0.5f,  0.5f),   // 0: top-left     (u=0, v=0)
			FVector3f(0.0f, -0.5f,  0.5f),   // 1: top-right    (u=1, v=0)
			FVector3f(0.0f, -0.5f, -0.5f),   // 2: bottom-right (u=1, v=1)
			FVector3f(0.0f,  0.5f, -0.5f),   // 3: bottom-left  (u=0, v=1)
		};
		Data.Normals = {
			FVector3f(1.0f, 0.0f, 0.0f),
			FVector3f(1.0f, 0.0f, 0.0f),
			FVector3f(1.0f, 0.0f, 0.0f),
			FVector3f(1.0f, 0.0f, 0.0f),
		};
		Data.UVs = {
			FVector2f(0.0f, 0.0f),
			FVector2f(1.0f, 0.0f),
			FVector2f(1.0f, 1.0f),
			FVector2f(0.0f, 1.0f),
		};
		// BuildStaticMesh reverses the winding (it assumes AetherFX's
		// right-handed CCW), so these are listed clockwise to come out facing +X.
		Data.Indices = { 0, 1, 2, 0, 2, 3 };

		UStaticMesh* Quad = BuildStaticMesh(
			GetTransientPackage(),
			FName(TEXT("SM_AetherFX_UnitQuad")),
			RF_Transient | RF_Public,
			Data,
			Materials::BillboardAdditive());

		if (Quad)
		{
			Quad->AddToRoot();
			Cached = Quad;
		}
		return Quad;
	}

	UTexture2D* CreateTransientTextureFromCompiled(const aetherfx_compiled* Compiled, int32 Index, UObject* Outer)
	{
		if (!Compiled)
		{
			return nullptr;
		}

		struct aetherfx_texture_info Info;
		if (aetherfx_texture_info(Compiled, Index, &Info) != AETHERFX_OK)
		{
			return nullptr;
		}
		if (Info.width <= 0 || Info.height <= 0)
		{
			return nullptr;
		}

		const int64 ByteCount = static_cast<int64>(Info.width) * Info.height * 4;
		TArray<uint8> Pixels;
		Pixels.SetNumUninitialized(static_cast<int32>(ByteCount));

		// srgb_encode = 1: the GPU sampler decodes it back to linear because the
		// texture below is created sRGB.
		if (aetherfx_texture_pixels_rgba8(Compiled, Index, Pixels.GetData(), static_cast<size_t>(ByteCount), 1) != AETHERFX_OK)
		{
			UE_LOG(LogAetherFX, Warning, TEXT("Texture '%s': rgba8 conversion failed (%s)"),
				UTF8_TO_TCHAR(Info.id ? Info.id : ""), *AetherFX::GetLastError());
			return nullptr;
		}

		const FName TextureName = MakeUniqueObjectName(
			Outer ? Outer : GetTransientPackage(),
			UTexture2D::StaticClass(),
			FName(*FString::Printf(TEXT("T_AetherFX_%s"), UTF8_TO_TCHAR(Info.id ? Info.id : "tex"))));

		UTexture2D* Texture = UTexture2D::CreateTransient(Info.width, Info.height, PF_B8G8R8A8, TextureName);
		if (!Texture)
		{
			return nullptr;
		}

		Texture->SRGB = true;
		Texture->CompressionSettings = TC_Default;
		Texture->Filter = TF_Bilinear;
		Texture->AddressX = TA_Clamp;
		Texture->AddressY = TA_Clamp;
		Texture->NeverStream = true;

		if (FTexturePlatformData* PlatformData = Texture->GetPlatformData())
		{
			if (PlatformData->Mips.Num() > 0)
			{
				FTexture2DMipMap& Mip = PlatformData->Mips[0];
				if (void* Destination = Mip.BulkData.Lock(LOCK_READ_WRITE))
				{
					// RGBA -> BGRA.
					uint8* Out = static_cast<uint8*>(Destination);
					const uint8* In = Pixels.GetData();
					const int64 PixelCount = static_cast<int64>(Info.width) * Info.height;
					for (int64 Pixel = 0; Pixel < PixelCount; ++Pixel)
					{
						Out[Pixel * 4 + 0] = In[Pixel * 4 + 2];
						Out[Pixel * 4 + 1] = In[Pixel * 4 + 1];
						Out[Pixel * 4 + 2] = In[Pixel * 4 + 0];
						Out[Pixel * 4 + 3] = In[Pixel * 4 + 3];
					}
				}
				Mip.BulkData.Unlock();
			}
		}

		Texture->UpdateResource();
		return Texture;
	}

	namespace Materials
	{
		namespace
		{
			UMaterialInterface* LoadGenerated(const TCHAR* AssetPath, const TCHAR* Label, bool& bWarned)
			{
				if (UMaterialInterface* Loaded = LoadObject<UMaterialInterface>(nullptr, AssetPath))
				{
					return Loaded;
				}
				if (!bWarned)
				{
					bWarned = true;
					UE_LOG(LogAetherFX, Warning,
						TEXT("%s material '%s' is missing; falling back to the engine default material. ")
						TEXT("Generate the AetherFX materials (see docs/UNREAL.md, 'Materials')."),
						Label, AssetPath);
				}
				return UMaterial::GetDefaultMaterial(MD_Surface);
			}
		}

		static const TCHAR* PathBillboardAdditive    = TEXT("/AetherFX/Materials/M_AetherFX_Billboard_Additive.M_AetherFX_Billboard_Additive");
		static const TCHAR* PathBillboardTranslucent = TEXT("/AetherFX/Materials/M_AetherFX_Billboard_Translucent.M_AetherFX_Billboard_Translucent");
		static const TCHAR* PathMesh                 = TEXT("/AetherFX/Materials/M_AetherFX_Mesh.M_AetherFX_Mesh");
		static const TCHAR* PathDecal                = TEXT("/AetherFX/Materials/M_AetherFX_Decal.M_AetherFX_Decal");
		static const TCHAR* PathRibbon               = TEXT("/AetherFX/Materials/M_AetherFX_Ribbon.M_AetherFX_Ribbon");

		UMaterialInterface* BillboardAdditive()
		{
			static bool bWarned = false;
			return LoadGenerated(PathBillboardAdditive, TEXT("Billboard (additive)"), bWarned);
		}

		UMaterialInterface* BillboardTranslucent()
		{
			static bool bWarned = false;
			return LoadGenerated(PathBillboardTranslucent, TEXT("Billboard (translucent)"), bWarned);
		}

		UMaterialInterface* Mesh()
		{
			static bool bWarned = false;
			return LoadGenerated(PathMesh, TEXT("Mesh"), bWarned);
		}

		UMaterialInterface* Decal()
		{
			static bool bWarned = false;
			return LoadGenerated(PathDecal, TEXT("Decal"), bWarned);
		}

		UMaterialInterface* Ribbon()
		{
			static bool bWarned = false;
			return LoadGenerated(PathRibbon, TEXT("Ribbon"), bWarned);
		}

		TArray<FString> AllAssetPaths()
		{
			return {
				PathBillboardAdditive,
				PathBillboardTranslucent,
				PathMesh,
				PathDecal,
				PathRibbon,
			};
		}
	}
}
