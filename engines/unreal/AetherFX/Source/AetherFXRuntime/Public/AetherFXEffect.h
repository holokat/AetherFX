// Copyright AetherFX.

#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"

#include "AetherFXHandles.h"
#include "AetherFXTypes.h"

#include "AetherFXEffect.generated.h"

class UStaticMesh;
class UTexture2D;

/**
 * One imported AetherFX effect.
 *
 * Holds the authoring document (`EffectJson`, the same text the studio and the
 * CLI read) plus the resources the Unreal importer built from an `.aetherfx`
 * package. `Compile()` hands the JSON to libaetherfx and caches the resulting
 * `aetherfx_compiled*` behind a shared RAII holder; every component that plays
 * this effect creates its own `aetherfx_runtime*` from that one compile.
 *
 * The raw C handles never appear in a UPROPERTY and are never exposed to
 * Blueprints -- Blueprint sees the asset, the component and nothing else.
 */
UCLASS(BlueprintType, hidecategories = (Object))
class AETHERFXRUNTIME_API UAetherFXEffect : public UDataAsset
{
	GENERATED_BODY()

public:
	UAetherFXEffect();

	//~ The authoring document ---------------------------------------------------

	/** The effect document (effect.json). This is what libaetherfx compiles. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX", meta = (MultiLine = true))
	FString EffectJson;

	/** Display name from the package manifest. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX")
	FString EffectName;

	/** Authored duration in seconds. A presentation hint, not a hard stop. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX")
	float Duration = 0.0f;

	/**
	 * Simulation timestep the package was exported with; 0 means "library
	 * default" (1/60).
	 *
	 * Double, not float, on purpose: the timestep is part of the determinism
	 * contract (docs/ENGINE_INTEGRATION.md 10), and 1/60 rounded to float is
	 * *larger* than 1/60, which silently costs a step per second of playback and
	 * makes Unreal disagree with the studio.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "AetherFX")
	double FixedTimeStep = 0.0;

	/** 16 hex digits identifying the source document; used to skip needless re-imports. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX")
	FString SourceHash;

	//~ Imported resources --------------------------------------------------------

	/** Baked textures, keyed by AetherFX texture id (`tex_flame`, ...). */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX|Resources")
	TMap<FName, TObjectPtr<UTexture2D>> Textures;

	/**
	 * Baked meshes. Variant 0 is keyed by the bare mesh id; variant k is keyed
	 * by the sanitised form of `<id>#k`, i.e. `<id>_k` (FName cannot hold `#`).
	 * Use MakeMeshVariantKey() rather than building the key by hand.
	 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX|Resources")
	TMap<FName, TObjectPtr<UStaticMesh>> Meshes;

	/** Baked material descriptions, keyed by material id. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX|Resources")
	TMap<FName, FAetherFXMaterialParams> Materials;

#if WITH_EDITORONLY_DATA
	/** Absolute or project-relative path of the imported `.aetherfx` package / `.json` file. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX|Import")
	FString SourceFilePath;
#endif

	//~ Compilation ---------------------------------------------------------------

	/**
	 * Compiles `EffectJson` through libaetherfx and caches the result. Cheap to
	 * call repeatedly: the second call returns the cached handle. Returns false
	 * and logs the diagnostics when the effect does not compile.
	 */
	bool Compile();

	/** Drops the cached compile. Live runtimes keep working (they own their plan). */
	void InvalidateCompiled();

	/** The cached compile, compiling on demand. Null when the effect is invalid. */
	AetherFX::FCompiledHandlePtr GetCompiled();

	/** True when a compiled plan is cached and usable. */
	UFUNCTION(BlueprintPure, Category = "AetherFX")
	bool IsCompiled() const;

	/** Compiles and reports whether the effect is playable. Safe on a cooked build. */
	UFUNCTION(BlueprintCallable, Category = "AetherFX", meta = (DisplayName = "Compile"))
	bool K2_Compile();

	/** Compile diagnostics as JSON, or an empty string when the effect never compiled. */
	UFUNCTION(BlueprintPure, Category = "AetherFX")
	FString GetCompileDiagnostics() const { return CompileDiagnostics; }

	//~ Resource lookup -----------------------------------------------------------

	/** `rock_mesh` + variant 2 -> `rock_mesh_2`; variant 0 -> `rock_mesh`. */
	static FName MakeMeshVariantKey(const FString& MeshId, int32 Variant);

	/** Turns an AetherFX resource id into something FName and the asset registry accept. */
	static FString SanitizeResourceId(const FString& Id);

	UFUNCTION(BlueprintPure, Category = "AetherFX")
	UTexture2D* FindTexture(FName TextureId) const;

	UFUNCTION(BlueprintPure, Category = "AetherFX")
	UStaticMesh* FindMesh(FName MeshId) const;

	/** Variant-aware mesh lookup; falls back to variant 0 when the variant is missing. */
	UStaticMesh* FindMeshVariant(const FString& MeshId, int32 Variant) const;

	const FAetherFXMaterialParams* FindMaterial(FName MaterialId) const;

	//~ UObject -------------------------------------------------------------------
	virtual void BeginDestroy() override;
#if WITH_EDITOR
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif

private:
	/** Cached compile. Not a UPROPERTY: it owns a raw C handle. */
	AetherFX::FCompiledHandlePtr Compiled;

	UPROPERTY(Transient)
	FString CompileDiagnostics;

	/** Set when a compile attempt failed, so we do not retry every frame. */
	bool bCompileFailed = false;
};
