// Copyright AetherFX.
//
// Resource construction shared by the runtime and the editor importer.
//
// Geometry that reaches these helpers is already in Unreal space: positions in
// centimetres with the (x, y, z) -> (x, z, y) axis swap applied. See
// AetherFXCoordinates.h for the mapping and why.

#pragma once

#include "CoreMinimal.h"
#include "UObject/ObjectMacros.h"

class UMaterialInterface;
class UStaticMesh;
class UTexture2D;
struct aetherfx_compiled;

namespace AetherFX
{
	/** Triangle soup in Unreal space, ready for UStaticMesh::BuildFromMeshDescriptions. */
	struct AETHERFXRUNTIME_API FMeshData
	{
		TArray<FVector3f> Positions;   // centimetres, Unreal axes
		TArray<FVector3f> Normals;     // optional; empty = flat normals from the winding
		TArray<FVector2f> UVs;         // optional; empty = zero
		TArray<uint32> Indices;        // triangle list

		bool IsValid() const
		{
			return Positions.Num() >= 3 && Indices.Num() >= 3 && (Indices.Num() % 3) == 0;
		}
	};

	/**
	 * Builds a UStaticMesh from a triangle soup. Works in cooked builds
	 * (bFastBuild). Returns nullptr when the data is unusable.
	 */
	AETHERFXRUNTIME_API UStaticMesh* BuildStaticMesh(
		UObject* Outer, FName Name, EObjectFlags Flags, const FMeshData& Data, UMaterialInterface* Material);

	/**
	 * Reads mesh `Index` out of a compiled effect and converts it to Unreal
	 * space (axis swap + metres to centimetres).
	 */
	AETHERFXRUNTIME_API bool ReadCompiledMesh(const aetherfx_compiled* Compiled, int32 Index, FMeshData& Out);

	/**
	 * The shared 1x1 unit quad every billboard system instances.
	 *
	 * It lies in the local YZ plane with its normal along +X, so a billboard is
	 * oriented with FRotationMatrix::MakeFromXZ(ToCamera, CameraUp) and scaled
	 * by (1, Width, Height). UV (0,0) is the top-left corner (+Y, +Z).
	 *
	 * Created once, added to the root set, and reused by every component.
	 */
	AETHERFXRUNTIME_API UStaticMesh* GetUnitQuadMesh();

	/**
	 * Builds a transient UTexture2D from texture `Index` of a compiled effect.
	 * Used when an effect is played without going through the editor importer
	 * (the automation test, or a JSON loaded at runtime).
	 */
	AETHERFXRUNTIME_API UTexture2D* CreateTransientTextureFromCompiled(
		const aetherfx_compiled* Compiled, int32 Index, UObject* Outer);

	/** How many sub-images a texture's frame strip has, given the system's sprite grid. */
	struct AETHERFXRUNTIME_API FSubUVLayout
	{
		int32 Columns = 1;   // frames * sprite_columns
		int32 Rows = 1;      // sprite_rows

		int32 CellCount() const { return FMath::Max(1, Columns) * FMath::Max(1, Rows); }
	};

	/**
	 * Material assets generated into the plugin's content folder. Every getter
	 * falls back to the engine default material (and warns once) when the asset
	 * is missing, so the runtime never depends on content that does not exist.
	 */
	namespace Materials
	{
		AETHERFXRUNTIME_API UMaterialInterface* BillboardAdditive();
		AETHERFXRUNTIME_API UMaterialInterface* BillboardTranslucent();
		AETHERFXRUNTIME_API UMaterialInterface* Mesh();
		AETHERFXRUNTIME_API UMaterialInterface* Decal();
		AETHERFXRUNTIME_API UMaterialInterface* Ribbon();

		/** Package paths of the five generated materials, for tooling and docs. */
		AETHERFXRUNTIME_API TArray<FString> AllAssetPaths();
	}
}
