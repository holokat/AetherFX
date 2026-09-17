// Copyright AetherFX.

#pragma once

#include "CoreMinimal.h"
#include "UObject/ObjectMacros.h"

#include "AetherFXTypes.generated.h"

/** Mirrors `enum aetherfx_blend_mode`. Values are part of the C ABI. */
UENUM(BlueprintType)
enum class EAetherFXBlend : uint8
{
	Additive		UMETA(DisplayName = "Additive"),
	Alpha			UMETA(DisplayName = "Alpha"),
	Premultiplied	UMETA(DisplayName = "Premultiplied"),
};

/** Mirrors `enum aetherfx_render_mode`. Values are part of the C ABI. */
UENUM(BlueprintType)
enum class EAetherFXRenderMode : uint8
{
	Billboard			UMETA(DisplayName = "Billboard"),
	StretchedBillboard	UMETA(DisplayName = "Stretched Billboard"),
	Mesh				UMETA(DisplayName = "Mesh"),
	Ribbon				UMETA(DisplayName = "Ribbon"),
	None				UMETA(DisplayName = "None"),
};

/** Mirrors `enum aetherfx_shading`. */
UENUM(BlueprintType)
enum class EAetherFXShading : uint8
{
	Unlit	UMETA(DisplayName = "Unlit"),
	Lit		UMETA(DisplayName = "Lit"),
};

/**
 * One baked AetherFX material, stored on the effect asset by the importer.
 * These are the resolved `MaterialDesc` values (see docs/PACKAGE_FORMAT.md 5.8),
 * not the authored parameters, so an Unreal material instance can be configured
 * straight from them.
 */
USTRUCT(BlueprintType)
struct AETHERFXRUNTIME_API FAetherFXMaterialParams
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	FName Id;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	EAetherFXBlend Blend = EAetherFXBlend::Additive;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	EAetherFXShading Shading = EAetherFXShading::Unlit;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	FLinearColor BaseColor = FLinearColor::White;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	float Opacity = 1.0f;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	FLinearColor EmissiveColor = FLinearColor::White;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	float EmissiveIntensity = 0.0f;

	/** Rim term exponent; 0 = off. */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	float FresnelPower = 0.0f;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	float Dissolve = 0.0f;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	float Erosion = 0.0f;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	bool bSoftParticle = false;

	/** Soft-particle fade distance, metres (converted to cm when applied). */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	float DepthFade = 0.0f;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	FName BaseTexture;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX|Material")
	FName NoiseTexture;
};

namespace AetherFX
{
	/**
	 * Per-instance custom data layout shared by every AetherFX instanced-mesh
	 * material. Ten floats, all computed on the CPU so the material graph stays
	 * a handful of nodes (and therefore survives headless generation):
	 *
	 *   0,1,2  linear RGB tint
	 *   3      alpha (particle opacity * material opacity)
	 *   4      per-particle emissive multiplier (the material adds its own
	 *          EmissiveIntensity parameter on top)
	 *   5,6    sub-UV offset  (u0, v0)
	 *   7,8    sub-UV scale   (du, dv)     UV = InUV * (du, dv) + (u0, v0)
	 *   9      normalised age, age / lifetime in [0, 1]
	 */
	enum : int32
	{
		CustomData_ColorR = 0,
		CustomData_ColorG = 1,
		CustomData_ColorB = 2,
		CustomData_Alpha = 3,
		CustomData_Emissive = 4,
		CustomData_UVOffsetU = 5,
		CustomData_UVOffsetV = 6,
		CustomData_UVScaleU = 7,
		CustomData_UVScaleV = 8,
		CustomData_Age01 = 9,
		CustomData_Count = 10,
	};

	/** Parameter names the generated materials expose. */
	namespace MaterialParams
	{
		extern AETHERFXRUNTIME_API const FName BaseTexture;
		extern AETHERFXRUNTIME_API const FName NoiseTexture;
		extern AETHERFXRUNTIME_API const FName BaseColor;
		extern AETHERFXRUNTIME_API const FName EmissiveColor;
		extern AETHERFXRUNTIME_API const FName EmissiveIntensity;
		extern AETHERFXRUNTIME_API const FName Opacity;
		extern AETHERFXRUNTIME_API const FName FresnelPower;
		/** Ribbon material: 1 = additive (opacity forced to 0), 0 = premultiplied alpha. */
		extern AETHERFXRUNTIME_API const FName Additive;
	}
}
