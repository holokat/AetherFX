// Copyright AetherFX.

#pragma once

#include "CoreMinimal.h"
#include "Components/SceneComponent.h"

#include "AetherFXHandles.h"
#include "AetherFXTypes.h"

#include "AetherFXComponent.generated.h"

class UAetherFXEffect;
class UDecalComponent;
class UInstancedStaticMeshComponent;
class ULightComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class UProceduralMeshComponent;
class UStaticMesh;
class UStaticMeshComponent;
class UTexture2D;

/**
 * Plays one AetherFX effect.
 *
 * libaetherfx simulates on the game thread every tick
 * (`aetherfx_runtime_simulate_to(elapsed)`); Unreal draws the result with its
 * own renderer:
 *
 *   particle system (billboard)  -> UInstancedStaticMeshComponent of unit quads,
 *                                   CPU-oriented towards the view, per-instance
 *                                   custom data for colour / alpha / emissive /
 *                                   sub-UV (see AetherFX::CustomData_*)
 *   particle system (mesh)       -> one UInstancedStaticMeshComponent per baked
 *                                   mesh variant
 *   light                        -> UPointLightComponent / USpotLightComponent
 *   decal                        -> UDecalComponent + dynamic material instance
 *   mesh instance                -> UStaticMeshComponent
 *   trail / beam                 -> UProceduralMeshComponent strips, rebuilt
 *                                   every frame as camera-facing quads
 *
 * Units and axes are converted in AetherFXCoordinates.h.
 */
UCLASS(ClassGroup = (AetherFX), meta = (BlueprintSpawnableComponent), HideCategories = (Object, LOD, Physics, Collision, Activation, Cooking))
class AETHERFXRUNTIME_API UAetherFXComponent : public USceneComponent
{
	GENERATED_BODY()

public:
	UAetherFXComponent();

	//~ Authoring ----------------------------------------------------------------

	/** The effect to play. Changing it while playing restarts playback. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	TObjectPtr<UAetherFXEffect> Effect;

	/** Restart from t = 0 when playback passes the effect's authored duration. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	bool bLoop = false;

	/** Simulation clock multiplier. 1 = real time. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX", meta = (ClampMin = "0.0", UIMin = "0.0", UIMax = "4.0"))
	float PlaybackRate = 1.0f;

	/** Start playing as soon as the component is registered. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	bool bAutoPlay = true;

	/** Destroy the owning actor once a non-looping effect has finished. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	bool bDestroyOwnerWhenFinished = false;

	/**
	 * Seconds of simulation to keep running past the authored duration before a
	 * non-looping effect is considered finished (tails, smoke, decals).
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX", meta = (ClampMin = "0.0"))
	float TailSeconds = 2.0f;

	//~ Lights --------------------------------------------------------------------

	/**
	 * candela = aetherfx_light_info::intensity * LightScale.
	 *
	 * AetherFX intensities are authored as a unitless "brightness" around 1..20;
	 * Unreal point lights want candelas. 100 puts a 1.0 AetherFX light at
	 * 100 cd, which reads like a small torch in a default-exposure scene.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX|Lights", meta = (ClampMin = "0.0"))
	float LightScale = 100.0f;

	/** Effect lights are cheap flickering fill light; shadows are off by default. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX|Lights")
	bool bLightsCastShadows = false;

	//~ Rendering -----------------------------------------------------------------

	/** Drawn instances, lights and decals cast no shadows and receive no decals. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX|Rendering")
	bool bCastShadows = false;

	/** Skip the light bridge entirely (useful on low settings). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX|Rendering")
	bool bEnableLights = true;

	/** Skip the decal bridge. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX|Rendering")
	bool bEnableDecals = true;

	/** Skip the trail/beam bridge (the per-frame procedural mesh rebuild). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX|Rendering")
	bool bEnableTrailsAndBeams = true;

	//~ Playback ------------------------------------------------------------------

	/** Compiles the effect if needed, creates a runtime and starts from t = 0. */
	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void Play();

	/** Stops playback and clears every drawn primitive. */
	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void Stop();

	/**
	 * Jumps to `TimeSeconds`. The simulation never runs backwards, so seeking
	 * backwards resets the runtime and re-simulates from 0 -- which is exact,
	 * but costs one step per frame of the interval.
	 */
	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void Seek(float TimeSeconds);

	UFUNCTION(BlueprintPure, Category = "AetherFX")
	bool IsPlaying() const { return bPlaying; }

	/** Elapsed play time in seconds (the value handed to simulate_to). */
	UFUNCTION(BlueprintPure, Category = "AetherFX")
	float GetPlaybackTime() const { return static_cast<float>(PlayTime); }

	/** Simulation time the library actually reached (a whole number of fixed steps). */
	UFUNCTION(BlueprintPure, Category = "AetherFX")
	float GetSimulationTime() const;

	//~ Introspection (used by the automation tests and by tools) ------------------

	/** Total live billboard + mesh particle instances drawn this frame. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetInstanceCount() const;

	/** Light components currently bridged. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetLightCount() const;

	/** Decal components currently bridged. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetDecalCount() const;

	/** Procedural mesh sections (trails + beams) built this frame. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetRibbonSectionCount() const { return RibbonSectionCount; }

	/** Static mesh components bridged from analytic mesh nodes. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetMeshInstanceCount() const;

	/**
	 * Live particles this frame in systems the bridge draws (billboard,
	 * stretched billboard, mesh). Ribbon systems are drawn by their trail node
	 * and NONE systems are simulation-only, so neither is counted.
	 *
	 * GetInstanceCount() must equal this every frame: that is the invariant that
	 * says no particle was silently dropped on the way to the renderer.
	 */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetDrawableParticleCount() const;

	/** Particle systems the compiled effect exposes (stable for the runtime's life). */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetSystemCount() const { return Systems.Num(); }

	/** The AetherFX node id of particle system `SystemIndex`. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	FName GetSystemId(int32 SystemIndex) const;

	/**
	 * The instanced-mesh component drawing particle system `SystemIndex`
	 * (`Bucket` selects the mesh variant). Tools and tests only.
	 */
	UInstancedStaticMeshComponent* GetSystemInstancedComponent(int32 SystemIndex, int32 Bucket = 0) const;

	/**
	 * Dynamic material instances the component is holding. Constant once every
	 * system and every trail/beam material has been seen -- a number that keeps
	 * climbing means something is re-creating a MID per frame.
	 */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	int32 GetDynamicMaterialCount() const { return SystemMaterials.Num(); }

	/** Simulation statistics as JSON, straight from the library. */
	UFUNCTION(BlueprintPure, Category = "AetherFX|Stats")
	FString GetStatisticsJson() const;

	/**
	 * Advances the simulation and rebuilds every primitive once, without waiting
	 * for a tick. Automation tests and editor previews drive playback this way.
	 */
	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void AdvanceAndRender(float DeltaSeconds);

	//~ UActorComponent -----------------------------------------------------------
	virtual void OnRegister() override;
	virtual void OnUnregister() override;
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;
	virtual void BeginDestroy() override;

private:
	/** Everything we draw for one particle_system node. */
	struct FSystemState
	{
		FName Id;
		/** One ISM per mesh variant; billboards have exactly one. */
		TArray<TObjectPtr<UInstancedStaticMeshComponent>> Instanced;
		TObjectPtr<UMaterialInstanceDynamic> Material;
		int32 RenderMode = 0;
		int32 Blend = 0;
		int32 SpriteColumns = 1;
		int32 SpriteRows = 1;
		int32 TextureFrames = 1;
		float SpriteFps = 0.0f;
		float VelocityStretch = 0.0f;
		bool bAlignToVelocity = false;
		FAetherFXMaterialParams MaterialParams;
		bool bHasMaterialParams = false;
	};

	bool EnsureRuntime();
	void DestroyRuntime();
	void BuildSystemStates();
	void ReleaseRenderComponents();

	void UpdateParticleSystems(const FVector& ViewLocation, const FVector& ViewUp);
	void UpdateLights();
	void UpdateDecals();
	void UpdateMeshInstances();
	void UpdateTrailsAndBeams(const FVector& ViewLocation);

	/** Camera position / up in this component's local space, with a safe fallback. */
	void GetLocalView(FVector& OutLocation, FVector& OutUp) const;

	UInstancedStaticMeshComponent* CreateInstancedComponent(UStaticMesh* MeshAsset, UMaterialInterface* Material);
	UTexture2D* ResolveTexture(const char* TextureId, int32* OutFrames);
	UStaticMesh* ResolveMesh(const char* MeshId, int32 Variant);
	UMaterialInstanceDynamic* MakeSystemMaterial(int32 Blend, int32 RenderMode, const char* MaterialId, const char* SpriteId, FSystemState& State);

	/**
	 * Cached dynamic material instance for one trail/beam material + blend.
	 * Trails and beams are rebuilt every frame; their materials must not be.
	 */
	UMaterialInstanceDynamic* GetRibbonMaterial(int32 Blend, const char* MaterialId);

	//~ State ---------------------------------------------------------------------

	AetherFX::FCompiledHandlePtr Compiled;
	AetherFX::FRuntimeHandle Runtime;

	TArray<FSystemState> Systems;

	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<ULightComponent>> Lights;

	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UDecalComponent>> Decals;

	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UStaticMeshComponent>> MeshInstances;

	UPROPERTY(Transient)
	TObjectPtr<UProceduralMeshComponent> RibbonMesh;

	/** Transient textures/meshes built from the compiled effect when the asset has none. */
	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UTexture2D>> FallbackTextures;

	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UStaticMesh>> FallbackMeshes;

	UPROPERTY(Transient)
	TArray<TObjectPtr<UInstancedStaticMeshComponent>> InstancedComponents;

	UPROPERTY(Transient)
	TArray<TObjectPtr<UMaterialInstanceDynamic>> SystemMaterials;

	/** Keyed "<material id>|<blend>"; see GetRibbonMaterial(). */
	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UMaterialInstanceDynamic>> RibbonMaterials;

	double PlayTime = 0.0;
	bool bPlaying = false;
	bool bFinished = false;
	int32 RibbonSectionCount = 0;

	/** Reusable scratch buffers so a tick allocates nothing steady-state. */
	TArray<FTransform> ScratchTransforms;
	TArray<float> ScratchCustomData;
};
