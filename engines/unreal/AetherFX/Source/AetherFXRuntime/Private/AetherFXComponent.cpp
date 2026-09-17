// Copyright AetherFX.

#include "AetherFXComponent.h"

#include "AetherFXCoordinates.h"
#include "AetherFXEffect.h"
#include "AetherFXLog.h"
#include "AetherFXResources.h"

#include "Camera/PlayerCameraManager.h"
#include "Components/DecalComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "GameFramework/PlayerController.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "ProceduralMeshComponent.h"

#include "aetherfx/aetherfx.h"

namespace
{
	/** FName for a C string, NAME_None for null/empty. */
	FName ToName(const char* Utf8)
	{
		return (Utf8 && Utf8[0] != '\0') ? FName(UTF8_TO_TCHAR(Utf8)) : NAME_None;
	}

	bool IsEmptyId(const char* Utf8)
	{
		return Utf8 == nullptr || Utf8[0] == '\0';
	}
}

UAetherFXComponent::UAetherFXComponent()
{
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.TickGroup = TG_PrePhysics;
	bTickInEditor = false;
	bAutoActivate = true;
}

void UAetherFXComponent::OnRegister()
{
	Super::OnRegister();

	if (bAutoPlay && Effect && GetWorld() && GetWorld()->IsGameWorld())
	{
		Play();
	}
}

void UAetherFXComponent::OnUnregister()
{
	Stop();
	Super::OnUnregister();
}

void UAetherFXComponent::BeginDestroy()
{
	DestroyRuntime();
	Super::BeginDestroy();
}

//-----------------------------------------------------------------------------
// Playback
//-----------------------------------------------------------------------------

void UAetherFXComponent::Play()
{
	if (!EnsureRuntime())
	{
		return;
	}

	aetherfx_runtime_reset(Runtime.Get());
	PlayTime = 0.0;
	bPlaying = true;
	bFinished = false;

	// Frame 0 is drawable without stepping (the analytic scene is evaluated at
	// t = 0 by reset), so push it straight away.
	FVector ViewLocation, ViewUp;
	GetLocalView(ViewLocation, ViewUp);
	UpdateParticleSystems(ViewLocation, ViewUp);
	UpdateLights();
	UpdateDecals();
	UpdateMeshInstances();
	UpdateTrailsAndBeams(ViewLocation);
}

void UAetherFXComponent::Stop()
{
	bPlaying = false;
	PlayTime = 0.0;
	ReleaseRenderComponents();
}

void UAetherFXComponent::Seek(float TimeSeconds)
{
	if (!EnsureRuntime())
	{
		return;
	}

	const double Target = FMath::Max(0.0, static_cast<double>(TimeSeconds));
	if (Target < aetherfx_runtime_time(Runtime.Get()))
	{
		// The simulation never runs backwards; replay from the start.
		aetherfx_runtime_reset(Runtime.Get());
	}

	PlayTime = Target;
	bFinished = false;
	aetherfx_runtime_simulate_to(Runtime.Get(), PlayTime);

	FVector ViewLocation, ViewUp;
	GetLocalView(ViewLocation, ViewUp);
	UpdateParticleSystems(ViewLocation, ViewUp);
	UpdateLights();
	UpdateDecals();
	UpdateMeshInstances();
	UpdateTrailsAndBeams(ViewLocation);
}

float UAetherFXComponent::GetSimulationTime() const
{
	if (!Runtime.IsValid())
	{
		return 0.0f;
	}
	const double Time = aetherfx_runtime_time(Runtime.Get());
	return Time >= 0.0 ? static_cast<float>(Time) : 0.0f;
}

FString UAetherFXComponent::GetStatisticsJson() const
{
	if (!Runtime.IsValid())
	{
		return FString();
	}
	FString Out;
	if (char* Json = aetherfx_runtime_statistics_json(Runtime.Get()))
	{
		Out = UTF8_TO_TCHAR(Json);
		aetherfx_free_string(Json);
	}
	return Out;
}

void UAetherFXComponent::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	if (!bPlaying)
	{
		return;
	}
	AdvanceAndRender(DeltaTime);
}

void UAetherFXComponent::AdvanceAndRender(float DeltaSeconds)
{
	if (!EnsureRuntime())
	{
		return;
	}

	PlayTime += static_cast<double>(FMath::Max(0.0f, DeltaSeconds)) * static_cast<double>(FMath::Max(0.0f, PlaybackRate));

	const double AuthoredDuration = (Effect && Effect->Duration > 0.0f) ? static_cast<double>(Effect->Duration) : 0.0;
	if (bLoop && AuthoredDuration > 0.0 && PlayTime >= AuthoredDuration)
	{
		aetherfx_runtime_reset(Runtime.Get());
		PlayTime = FMath::Fmod(PlayTime, AuthoredDuration);
	}

	if (aetherfx_runtime_simulate_to(Runtime.Get(), PlayTime) != AETHERFX_OK)
	{
		UE_LOG(LogAetherFX, Warning, TEXT("%s: simulate_to(%.3f) failed: %s"),
			*GetName(), PlayTime, *AetherFX::GetLastError());
		bPlaying = false;
		return;
	}

	FVector ViewLocation, ViewUp;
	GetLocalView(ViewLocation, ViewUp);

	UpdateParticleSystems(ViewLocation, ViewUp);
	UpdateLights();
	UpdateDecals();
	UpdateMeshInstances();
	UpdateTrailsAndBeams(ViewLocation);

	if (!bLoop && AuthoredDuration > 0.0 && PlayTime > AuthoredDuration + static_cast<double>(TailSeconds))
	{
		if (GetInstanceCount() == 0 && GetLightCount() == 0 && RibbonSectionCount == 0)
		{
			bFinished = true;
			bPlaying = false;
			if (bDestroyOwnerWhenFinished && GetOwner())
			{
				GetOwner()->Destroy();
			}
		}
	}
}

//-----------------------------------------------------------------------------
// Runtime lifetime
//-----------------------------------------------------------------------------

bool UAetherFXComponent::EnsureRuntime()
{
	if (Runtime.IsValid())
	{
		return true;
	}

	if (!Effect)
	{
		return false;
	}

	Compiled = Effect->GetCompiled();
	if (!Compiled.IsValid() || !Compiled->IsValid())
	{
		return false;
	}

	Runtime = AetherFX::FRuntimeHandle(aetherfx_runtime_create(Compiled->Get()));
	if (!Runtime.IsValid())
	{
		UE_LOG(LogAetherFX, Error, TEXT("%s: runtime_create failed: %s"), *GetName(), *AetherFX::GetLastError());
		Compiled.Reset();
		return false;
	}

	BuildSystemStates();
	return true;
}

void UAetherFXComponent::DestroyRuntime()
{
	Runtime.Reset();
	Compiled.Reset();
	Systems.Reset();
}

void UAetherFXComponent::ReleaseRenderComponents()
{
	for (TObjectPtr<UInstancedStaticMeshComponent>& Instanced : InstancedComponents)
	{
		if (Instanced)
		{
			Instanced->ClearInstances();
			Instanced->DestroyComponent();
		}
	}
	InstancedComponents.Reset();
	SystemMaterials.Reset();
	RibbonMaterials.Reset();

	for (TPair<FName, TObjectPtr<ULightComponent>>& Pair : Lights)
	{
		if (Pair.Value)
		{
			Pair.Value->DestroyComponent();
		}
	}
	Lights.Reset();

	for (TPair<FName, TObjectPtr<UDecalComponent>>& Pair : Decals)
	{
		if (Pair.Value)
		{
			Pair.Value->DestroyComponent();
		}
	}
	Decals.Reset();

	for (TPair<FName, TObjectPtr<UStaticMeshComponent>>& Pair : MeshInstances)
	{
		if (Pair.Value)
		{
			Pair.Value->DestroyComponent();
		}
	}
	MeshInstances.Reset();

	if (RibbonMesh)
	{
		RibbonMesh->ClearAllMeshSections();
		RibbonMesh->DestroyComponent();
		RibbonMesh = nullptr;
	}
	RibbonSectionCount = 0;

	Systems.Reset();
	DestroyRuntime();
}

//-----------------------------------------------------------------------------
// Per-system setup
//-----------------------------------------------------------------------------

UTexture2D* UAetherFXComponent::ResolveTexture(const char* TextureId, int32* OutFrames)
{
	if (OutFrames)
	{
		*OutFrames = 1;
	}
	if (IsEmptyId(TextureId))
	{
		return nullptr;
	}

	const FName Key = ToName(TextureId);

	if (Compiled.IsValid() && Compiled->IsValid() && OutFrames)
	{
		const int32 Index = aetherfx_texture_index(Compiled->Get(), TextureId);
		if (Index >= 0)
		{
			struct aetherfx_texture_info Info;
			if (aetherfx_texture_info(Compiled->Get(), Index, &Info) == AETHERFX_OK)
			{
				*OutFrames = FMath::Max(1, Info.frames);
			}
		}
	}

	if (Effect)
	{
		if (UTexture2D* Imported = Effect->FindTexture(Key))
		{
			return Imported;
		}
	}

	if (TObjectPtr<UTexture2D>* Cached = FallbackTextures.Find(Key))
	{
		return Cached->Get();
	}

	// No imported asset: bake one from the compiled effect. This is what makes
	// an effect playable from raw JSON, with no editor importer in the loop.
	if (Compiled.IsValid() && Compiled->IsValid())
	{
		const int32 Index = aetherfx_texture_index(Compiled->Get(), TextureId);
		if (Index >= 0)
		{
			if (UTexture2D* Built = AetherFX::CreateTransientTextureFromCompiled(Compiled->Get(), Index, this))
			{
				FallbackTextures.Add(Key, Built);
				return Built;
			}
		}
	}

	return nullptr;
}

UStaticMesh* UAetherFXComponent::ResolveMesh(const char* MeshId, int32 Variant)
{
	if (IsEmptyId(MeshId))
	{
		return nullptr;
	}

	const FString Id(UTF8_TO_TCHAR(MeshId));

	if (Effect)
	{
		if (UStaticMesh* Imported = Effect->FindMeshVariant(Id, Variant))
		{
			return Imported;
		}
	}

	const FName Key = UAetherFXEffect::MakeMeshVariantKey(Id, Variant);
	if (TObjectPtr<UStaticMesh>* Cached = FallbackMeshes.Find(Key))
	{
		return Cached->Get();
	}

	if (Compiled.IsValid() && Compiled->IsValid())
	{
		const FString ResourceId = (Variant <= 0) ? Id : FString::Printf(TEXT("%s#%d"), *Id, Variant);
		const FTCHARToUTF8 Utf8(*ResourceId);
		int32 Index = aetherfx_mesh_index(Compiled->Get(), Utf8.Get());
		if (Index < 0 && Variant > 0)
		{
			const FTCHARToUTF8 BaseUtf8(*Id);
			Index = aetherfx_mesh_index(Compiled->Get(), BaseUtf8.Get());
		}
		if (Index >= 0)
		{
			AetherFX::FMeshData Data;
			if (AetherFX::ReadCompiledMesh(Compiled->Get(), Index, Data))
			{
				UStaticMesh* Built = AetherFX::BuildStaticMesh(
					this,
					MakeUniqueObjectName(this, UStaticMesh::StaticClass(), *FString::Printf(TEXT("SM_AetherFX_%s"), *Key.ToString())),
					RF_Transient,
					Data,
					AetherFX::Materials::Mesh());
				if (Built)
				{
					FallbackMeshes.Add(Key, Built);
					return Built;
				}
			}
		}
	}

	return nullptr;
}

UInstancedStaticMeshComponent* UAetherFXComponent::CreateInstancedComponent(UStaticMesh* MeshAsset, UMaterialInterface* Material)
{
	AActor* Owner = GetOwner();
	if (!Owner || !MeshAsset)
	{
		return nullptr;
	}

	UInstancedStaticMeshComponent* Instanced = NewObject<UInstancedStaticMeshComponent>(Owner, NAME_None, RF_Transient);
	Instanced->SetStaticMesh(MeshAsset);
	Instanced->SetNumCustomDataFloats(AetherFX::CustomData_Count);
	Instanced->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Instanced->SetCastShadow(bCastShadows);
	Instanced->bReceivesDecals = false;
	Instanced->SetMobility(EComponentMobility::Movable);
	Instanced->bDisableCollision = true;
	Instanced->SetGenerateOverlapEvents(false);
	Instanced->SetupAttachment(this);
	Instanced->RegisterComponent();

	if (Material)
	{
		Instanced->SetMaterial(0, Material);
	}

	InstancedComponents.Add(Instanced);
	return Instanced;
}

UMaterialInstanceDynamic* UAetherFXComponent::MakeSystemMaterial(
	int32 Blend, int32 RenderMode, const char* MaterialId, const char* SpriteId, FSystemState& State)
{
	// The material's blend wins over the system's authored blend, as in the
	// reference renderer (docs/ENGINE_INTEGRATION.md 5).
	const FAetherFXMaterialParams* Params = nullptr;
	if (Effect && !IsEmptyId(MaterialId))
	{
		Params = Effect->FindMaterial(ToName(MaterialId));
	}

	if (!Params && Compiled.IsValid() && Compiled->IsValid() && !IsEmptyId(MaterialId))
	{
		const int32 Index = aetherfx_material_index(Compiled->Get(), MaterialId);
		struct aetherfx_material Baked;
		if (Index >= 0 && aetherfx_material_info(Compiled->Get(), Index, &Baked) == AETHERFX_OK)
		{
			FAetherFXMaterialParams Built;
			Built.Id = ToName(Baked.id);
			Built.Blend = static_cast<EAetherFXBlend>(FMath::Clamp(Baked.blend, 0, 2));
			Built.Shading = static_cast<EAetherFXShading>(FMath::Clamp(Baked.shading, 0, 1));
			Built.BaseColor = FLinearColor(Baked.base_color[0], Baked.base_color[1], Baked.base_color[2], Baked.base_color[3]);
			Built.Opacity = Baked.opacity;
			Built.EmissiveColor = FLinearColor(Baked.emissive_color[0], Baked.emissive_color[1], Baked.emissive_color[2], Baked.emissive_color[3]);
			Built.EmissiveIntensity = Baked.emissive_intensity;
			Built.FresnelPower = Baked.fresnel_power;
			Built.Dissolve = Baked.dissolve;
			Built.Erosion = Baked.erosion;
			Built.bSoftParticle = Baked.soft_particle != 0;
			Built.DepthFade = Baked.depth_fade;
			Built.BaseTexture = ToName(Baked.base_texture);
			Built.NoiseTexture = ToName(Baked.noise_texture);
			State.MaterialParams = Built;
			State.bHasMaterialParams = true;
			Params = &State.MaterialParams;
		}
	}
	else if (Params)
	{
		State.MaterialParams = *Params;
		State.bHasMaterialParams = true;
		Params = &State.MaterialParams;
	}

	const int32 EffectiveBlend = Params ? static_cast<int32>(Params->Blend) : Blend;
	State.Blend = EffectiveBlend;

	UMaterialInterface* Base = nullptr;
	if (RenderMode == AETHERFX_RENDER_RIBBON)
	{
		// Trails and beams are procedural strips with per-vertex colour, so they
		// use the ribbon material rather than the per-instance billboard one.
		Base = AetherFX::Materials::Ribbon();
	}
	else if (RenderMode == AETHERFX_RENDER_MESH)
	{
		Base = AetherFX::Materials::Mesh();
	}
	else if (EffectiveBlend == AETHERFX_BLEND_ADDITIVE)
	{
		Base = AetherFX::Materials::BillboardAdditive();
	}
	else
	{
		// ALPHA and PREMULTIPLIED both land on the translucent billboard; the
		// premultiplied case is approximated (see docs/UNREAL.md, Limitations).
		Base = AetherFX::Materials::BillboardTranslucent();
	}

	if (!Base)
	{
		return nullptr;
	}

	UMaterialInstanceDynamic* Dynamic = UMaterialInstanceDynamic::Create(Base, this);
	if (!Dynamic)
	{
		return nullptr;
	}

	int32 Frames = 1;
	const char* TextureId = !IsEmptyId(SpriteId)
		? SpriteId
		: ((Params && !Params->BaseTexture.IsNone()) ? nullptr : nullptr);

	UTexture2D* Sprite = ResolveTexture(TextureId, &Frames);
	if (!Sprite && Params && !Params->BaseTexture.IsNone())
	{
		const FTCHARToUTF8 BaseTextureUtf8(*Params->BaseTexture.ToString());
		Sprite = ResolveTexture(BaseTextureUtf8.Get(), &Frames);
	}
	State.TextureFrames = FMath::Max(1, Frames);

	if (Sprite)
	{
		Dynamic->SetTextureParameterValue(AetherFX::MaterialParams::BaseTexture, Sprite);
	}

	// The ribbon material blends premultiplied; forcing opacity to 0 makes it
	// additive, which is what an additive trail or beam wants.
	Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::Additive,
		(EffectiveBlend == AETHERFX_BLEND_ADDITIVE) ? 1.0f : 0.0f);

	if (Params)
	{
		Dynamic->SetVectorParameterValue(AetherFX::MaterialParams::BaseColor, Params->BaseColor);
		Dynamic->SetVectorParameterValue(AetherFX::MaterialParams::EmissiveColor, Params->EmissiveColor);
		Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::EmissiveIntensity, Params->EmissiveIntensity);
		Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::Opacity, Params->Opacity);
		Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::FresnelPower, Params->FresnelPower);

		if (!Params->NoiseTexture.IsNone())
		{
			int32 NoiseFrames = 1;
			const FTCHARToUTF8 NoiseUtf8(*Params->NoiseTexture.ToString());
			if (UTexture2D* Noise = ResolveTexture(NoiseUtf8.Get(), &NoiseFrames))
			{
				Dynamic->SetTextureParameterValue(AetherFX::MaterialParams::NoiseTexture, Noise);
			}
		}
	}
	else
	{
		Dynamic->SetVectorParameterValue(AetherFX::MaterialParams::BaseColor, FLinearColor::White);
		Dynamic->SetVectorParameterValue(AetherFX::MaterialParams::EmissiveColor, FLinearColor::White);
		Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::EmissiveIntensity, 0.0f);
		Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::Opacity, 1.0f);
	}

	SystemMaterials.Add(Dynamic);
	return Dynamic;
}

UMaterialInstanceDynamic* UAetherFXComponent::GetRibbonMaterial(int32 Blend, const char* MaterialId)
{
	const FName Key(*FString::Printf(TEXT("%s|%d"),
		IsEmptyId(MaterialId) ? TEXT("") : UTF8_TO_TCHAR(MaterialId), Blend));

	if (TObjectPtr<UMaterialInstanceDynamic>* Cached = RibbonMaterials.Find(Key))
	{
		if (*Cached)
		{
			return Cached->Get();
		}
	}

	FSystemState Scratch;
	UMaterialInstanceDynamic* Dynamic = MakeSystemMaterial(Blend, AETHERFX_RENDER_RIBBON, MaterialId, nullptr, Scratch);
	RibbonMaterials.Add(Key, Dynamic);
	return Dynamic;
}

void UAetherFXComponent::BuildSystemStates()
{
	Systems.Reset();

	if (!Runtime.IsValid())
	{
		return;
	}

	const int32 SystemCount = aetherfx_runtime_particle_system_count(Runtime.Get());
	if (SystemCount <= 0)
	{
		return;
	}

	Systems.SetNum(SystemCount);

	for (int32 Index = 0; Index < SystemCount; ++Index)
	{
		struct aetherfx_particle_system_info Info;
		if (aetherfx_particle_system_info(Runtime.Get(), Index, &Info) != AETHERFX_OK)
		{
			continue;
		}

		FSystemState& State = Systems[Index];
		State.Id = ToName(Info.id);
		State.RenderMode = Info.render_mode;
		State.Blend = Info.blend;
		State.SpriteColumns = FMath::Max(1, Info.sprite_columns);
		State.SpriteRows = FMath::Max(1, Info.sprite_rows);
		State.SpriteFps = Info.sprite_fps;
		State.VelocityStretch = Info.velocity_stretch;
		State.bAlignToVelocity = Info.align_to_velocity != 0;

		if (Info.render_mode == AETHERFX_RENDER_RIBBON || Info.render_mode == AETHERFX_RENDER_NONE)
		{
			// Ribbon systems are drawn by their trail node; NONE draws nothing.
			continue;
		}

		State.Material = MakeSystemMaterial(Info.blend, Info.render_mode, Info.material_id, Info.sprite_id, State);

		if (Info.render_mode == AETHERFX_RENDER_MESH)
		{
			const int32 Variants = FMath::Max(1, Info.mesh_variants);
			State.Instanced.Reserve(Variants);
			for (int32 Variant = 0; Variant < Variants; ++Variant)
			{
				UStaticMesh* MeshAsset = ResolveMesh(Info.mesh_id, Variant);
				State.Instanced.Add(CreateInstancedComponent(MeshAsset, State.Material));
			}
		}
		else
		{
			State.Instanced.Add(CreateInstancedComponent(AetherFX::GetUnitQuadMesh(), State.Material));
		}
	}

	UE_LOG(LogAetherFX, Verbose, TEXT("%s: %d particle systems bridged."), *GetName(), SystemCount);
}

//-----------------------------------------------------------------------------
// View
//-----------------------------------------------------------------------------

void UAetherFXComponent::GetLocalView(FVector& OutLocation, FVector& OutUp) const
{
	// Fallback: a virtual camera 10 m behind the component, so billboards face
	// the component's -X. Used in the editor (no PlayerCameraManager), in
	// commandlets and with -nullrhi.
	FVector WorldLocation = GetComponentLocation() - GetForwardVector() * 1000.0;
	FVector WorldUp = FVector::UpVector;

	if (const UWorld* World = GetWorld())
	{
		for (FConstPlayerControllerIterator It = World->GetPlayerControllerIterator(); It; ++It)
		{
			const APlayerController* Controller = It->Get();
			if (Controller && Controller->PlayerCameraManager)
			{
				WorldLocation = Controller->PlayerCameraManager->GetCameraLocation();
				WorldUp = Controller->PlayerCameraManager->GetCameraRotation().Quaternion().GetUpVector();
				break;
			}
		}
	}

	const FTransform& ToWorld = GetComponentTransform();
	OutLocation = ToWorld.InverseTransformPosition(WorldLocation);
	OutUp = ToWorld.InverseTransformVectorNoScale(WorldUp).GetSafeNormal(UE_SMALL_NUMBER, FVector::UpVector);
}

//-----------------------------------------------------------------------------
// Particles
//-----------------------------------------------------------------------------

void UAetherFXComponent::UpdateParticleSystems(const FVector& ViewLocation, const FVector& ViewUp)
{
	if (!Runtime.IsValid())
	{
		return;
	}

	for (int32 SystemIndex = 0; SystemIndex < Systems.Num(); ++SystemIndex)
	{
		FSystemState& State = Systems[SystemIndex];
		if (State.Instanced.Num() == 0)
		{
			continue;
		}

		struct aetherfx_particle_system_info Info;
		if (aetherfx_particle_system_info(Runtime.Get(), SystemIndex, &Info) != AETHERFX_OK)
		{
			continue;
		}

		const int32 Count = static_cast<int32>(Info.count);
		const bool bMeshMode = (State.RenderMode == AETHERFX_RENDER_MESH);
		const int32 BucketCount = State.Instanced.Num();

		if (Count == 0)
		{
			for (TObjectPtr<UInstancedStaticMeshComponent>& Bucket : State.Instanced)
			{
				if (Bucket && Bucket->GetInstanceCount() > 0)
				{
					Bucket->ClearInstances();
				}
			}
			continue;
		}

		const float* Position = aetherfx_particle_position(Runtime.Get(), SystemIndex);
		const float* Color = aetherfx_particle_color(Runtime.Get(), SystemIndex);
		const float* Size = aetherfx_particle_size(Runtime.Get(), SystemIndex);
		const float* Opacity = aetherfx_particle_opacity(Runtime.Get(), SystemIndex);
		const float* Emissive = aetherfx_particle_emissive(Runtime.Get(), SystemIndex);
		const float* Rotation = aetherfx_particle_rotation(Runtime.Get(), SystemIndex);
		const float* Age01 = aetherfx_particle_custom0(Runtime.Get(), SystemIndex);
		const float* Age = aetherfx_particle_age(Runtime.Get(), SystemIndex);
		const float* Velocity = aetherfx_particle_velocity(Runtime.Get(), SystemIndex);
		const float* Orientation = bMeshMode ? aetherfx_particle_orientation(Runtime.Get(), SystemIndex) : nullptr;
		const float* Scale3 = bMeshMode ? aetherfx_particle_scale3(Runtime.Get(), SystemIndex) : nullptr;
		const uint32* Variant = (bMeshMode && BucketCount > 1) ? aetherfx_particle_variant(Runtime.Get(), SystemIndex) : nullptr;

		if (!Position || !Size)
		{
			continue;
		}

		// The material carries its own opacity and emissive_intensity as shader
		// parameters, so the per-instance data holds only the particle's values.
		const float MaterialOpacity = 1.0f;

		// Sub-UV grid: `frames` strips side by side, each split into
		// sprite_columns x sprite_rows cells.
		const int32 Frames = FMath::Max(1, State.TextureFrames);
		const int32 Columns = FMath::Max(1, State.SpriteColumns);
		const int32 Rows = FMath::Max(1, State.SpriteRows);
		const int32 Cells = Columns * Rows;
		const int32 TotalColumns = Frames * Columns;
		const float ScaleU = 1.0f / static_cast<float>(TotalColumns);
		const float ScaleV = 1.0f / static_cast<float>(Rows);

		// Bucket by mesh variant so each ISM gets a contiguous run.
		for (int32 Bucket = 0; Bucket < BucketCount; ++Bucket)
		{
			UInstancedStaticMeshComponent* Instanced = State.Instanced[Bucket];
			if (!Instanced)
			{
				continue;
			}

			ScratchTransforms.Reset(Count);
			ScratchCustomData.Reset(Count * AetherFX::CustomData_Count);

			for (int32 Particle = 0; Particle < Count; ++Particle)
			{
				if (Variant && static_cast<int32>(Variant[Particle] % static_cast<uint32>(BucketCount)) != Bucket)
				{
					continue;
				}
				if (!Variant && BucketCount > 1 && Bucket != 0)
				{
					continue;
				}

				const FVector Location = AetherFX::PositionToUnreal(&Position[Particle * 3]);
				const float SizeMeters = Size[Particle];

				FQuat InstanceRotation = FQuat::Identity;
				FVector InstanceScale = FVector::OneVector;

				if (bMeshMode)
				{
					InstanceRotation = Orientation ? AetherFX::OrientationToUnreal(&Orientation[Particle * 4]) : FQuat::Identity;
					const FVector AxisScale = Scale3 ? AetherFX::Scale3ToUnreal(&Scale3[Particle * 3]) : FVector::OneVector;
					// The baked mesh is already in centimetres, so the instance
					// scale is the dimensionless `size * scale3`.
					InstanceScale = AxisScale * static_cast<double>(SizeMeters);
				}
				else
				{
					FVector ToView = ViewLocation - Location;
					if (!ToView.Normalize())
					{
						ToView = FVector::ForwardVector;
					}

					FVector Up = ViewUp;
					double Width = AetherFX::LengthToUnreal(SizeMeters);
					double Height = Width;

					if (State.RenderMode == AETHERFX_RENDER_STRETCHED_BILLBOARD && Velocity)
					{
						const FVector VelocityUE = AetherFX::DirectionToUnreal(&Velocity[Particle * 3]);
						const double Speed = VelocityUE.Size();
						if (Speed > UE_KINDA_SMALL_NUMBER)
						{
							// Length along the quad's local Z (its "up"), so the
							// quad stretches along the screen-space velocity.
							Up = VelocityUE / Speed;
							Height = AetherFX::LengthToUnreal(SizeMeters * (1.0f + State.VelocityStretch * static_cast<float>(Speed)));
						}
					}
					else if (State.bAlignToVelocity && Velocity)
					{
						const FVector VelocityUE = AetherFX::DirectionToUnreal(&Velocity[Particle * 3]);
						if (VelocityUE.SizeSquared() > UE_KINDA_SMALL_NUMBER)
						{
							Up = VelocityUE.GetSafeNormal();
						}
					}

					// Quad plane is local YZ with normal +X: point +X at the view
					// and +Z along `Up`.
					InstanceRotation = FRotationMatrix::MakeFromXZ(ToView, Up).ToQuat();
					if (Rotation && State.RenderMode == AETHERFX_RENDER_BILLBOARD)
					{
						// Roll about the view axis, in the quad's local space.
						InstanceRotation = InstanceRotation * FQuat(FVector::XAxisVector, -static_cast<double>(Rotation[Particle]));
					}
					InstanceScale = FVector(1.0, Width, Height);
				}

				ScratchTransforms.Emplace(InstanceRotation, Location, InstanceScale);

				const float ParticleAlpha = (Opacity ? Opacity[Particle] : 1.0f) * MaterialOpacity;
				const float ParticleEmissive = Emissive ? Emissive[Particle] : 0.0f;
				const float Normalized = Age01 ? Age01[Particle] : 0.0f;

				int32 FrameIndex = 0;
				int32 CellIndex = 0;
				if (State.SpriteFps > 0.0f && Age)
				{
					const int32 Ticks = FMath::FloorToInt(Age[Particle] * State.SpriteFps);
					FrameIndex = (Frames > 1) ? (Ticks % Frames) : 0;
					CellIndex = (Cells > 1) ? (Ticks % Cells) : 0;
				}
				else
				{
					const float Clamped = FMath::Clamp(Normalized, 0.0f, 0.9999f);
					FrameIndex = (Frames > 1) ? FMath::Clamp(FMath::FloorToInt(Clamped * Frames), 0, Frames - 1) : 0;
					CellIndex = (Cells > 1) ? FMath::Clamp(FMath::FloorToInt(Clamped * Cells), 0, Cells - 1) : 0;
				}

				const int32 GridColumn = FrameIndex * Columns + (CellIndex % Columns);
				const int32 GridRow = CellIndex / Columns;

				const int32 Base = ScratchCustomData.AddUninitialized(AetherFX::CustomData_Count);
				ScratchCustomData[Base + AetherFX::CustomData_ColorR] = Color ? Color[Particle * 4 + 0] : 1.0f;
				ScratchCustomData[Base + AetherFX::CustomData_ColorG] = Color ? Color[Particle * 4 + 1] : 1.0f;
				ScratchCustomData[Base + AetherFX::CustomData_ColorB] = Color ? Color[Particle * 4 + 2] : 1.0f;
				ScratchCustomData[Base + AetherFX::CustomData_Alpha] = ParticleAlpha;
				ScratchCustomData[Base + AetherFX::CustomData_Emissive] = ParticleEmissive;
				ScratchCustomData[Base + AetherFX::CustomData_UVOffsetU] = GridColumn * ScaleU;
				ScratchCustomData[Base + AetherFX::CustomData_UVOffsetV] = GridRow * ScaleV;
				ScratchCustomData[Base + AetherFX::CustomData_UVScaleU] = ScaleU;
				ScratchCustomData[Base + AetherFX::CustomData_UVScaleV] = ScaleV;
				ScratchCustomData[Base + AetherFX::CustomData_Age01] = Normalized;
			}

			Instanced->ClearInstances();
			if (ScratchTransforms.Num() > 0)
			{
				Instanced->AddInstances(ScratchTransforms, /*bShouldReturnIndices*/ false, /*bWorldSpace*/ false, /*bUpdateNavigation*/ false);
				for (int32 Instance = 0; Instance < ScratchTransforms.Num(); ++Instance)
				{
					Instanced->SetCustomData(
						Instance,
						TArrayView<const float>(&ScratchCustomData[Instance * AetherFX::CustomData_Count], AetherFX::CustomData_Count),
						/*bMarkRenderStateDirty*/ false);
				}
				Instanced->MarkRenderStateDirty();
			}
		}
	}
}

//-----------------------------------------------------------------------------
// Lights
//-----------------------------------------------------------------------------

void UAetherFXComponent::UpdateLights()
{
	if (!Runtime.IsValid())
	{
		return;
	}

	if (!bEnableLights)
	{
		for (TPair<FName, TObjectPtr<ULightComponent>>& Pair : Lights)
		{
			if (Pair.Value)
			{
				Pair.Value->DestroyComponent();
			}
		}
		Lights.Reset();
		return;
	}

	AActor* Owner = GetOwner();
	const int32 Count = aetherfx_runtime_light_count(Runtime.Get());

	TSet<FName> Seen;
	Seen.Reserve(FMath::Max(0, Count));

	for (int32 Index = 0; Index < Count; ++Index)
	{
		struct aetherfx_light_info Info;
		if (aetherfx_light_info(Runtime.Get(), Index, &Info) != AETHERFX_OK)
		{
			continue;
		}

		const FName Key = ToName(Info.id).IsNone() ? FName(*FString::Printf(TEXT("light_%d"), Index)) : ToName(Info.id);
		Seen.Add(Key);

		const bool bSpot = (Info.type == AETHERFX_LIGHT_SPOT);

		TObjectPtr<ULightComponent>* Existing = Lights.Find(Key);
		ULightComponent* Light = Existing ? Existing->Get() : nullptr;

		// A light that changes type between frames is rebuilt.
		const bool bTypeMismatch = Light && (bSpot != Light->IsA<USpotLightComponent>());
		if (bTypeMismatch)
		{
			Light->DestroyComponent();
			Light = nullptr;
			Lights.Remove(Key);
		}

		if (!Light && Owner)
		{
			ULightComponent* Created = bSpot
				? static_cast<ULightComponent*>(NewObject<USpotLightComponent>(Owner, NAME_None, RF_Transient))
				: static_cast<ULightComponent*>(NewObject<UPointLightComponent>(Owner, NAME_None, RF_Transient));
			Created->SetMobility(EComponentMobility::Movable);
			Created->SetCastShadows(bLightsCastShadows);
			Created->bAffectsWorld = true;
			Created->SetupAttachment(this);
			Created->RegisterComponent();
			Lights.Add(Key, Created);
			Light = Created;
		}

		if (!Light)
		{
			continue;
		}

		const FVector Location = AetherFX::PositionToUnreal(Info.position);
		FQuat Orientation = FQuat::Identity;
		if (bSpot)
		{
			FVector Direction = AetherFX::DirectionToUnreal(Info.direction);
			if (!Direction.Normalize())
			{
				Direction = -FVector::UpVector;
			}
			// Unreal spot lights shine down their local +X.
			Orientation = FRotationMatrix::MakeFromX(Direction).ToQuat();
		}
		Light->SetRelativeLocationAndRotation(Location, Orientation);

		if (ULocalLightComponent* Local = Cast<ULocalLightComponent>(Light))
		{
			Local->SetIntensityUnits(ELightUnits::Candelas);
			Local->SetAttenuationRadius(FMath::Max(1.0f, static_cast<float>(AetherFX::LengthToUnreal(Info.radius))));
		}
		Light->SetIntensity(FMath::Max(0.0f, Info.intensity * LightScale));
		Light->SetLightColor(FLinearColor(Info.color[0], Info.color[1], Info.color[2], 1.0f), /*bSRGB*/ false);

		if (USpotLightComponent* Spot = Cast<USpotLightComponent>(Light))
		{
			const float Outer = FMath::Clamp(Info.cone_angle_deg, 1.0f, 80.0f);
			Spot->SetOuterConeAngle(Outer);
			Spot->SetInnerConeAngle(Outer * 0.75f);
		}

		Light->SetVisibility(Info.intensity > 0.0f);
	}

	// Drop lights the simulation no longer reports.
	for (auto It = Lights.CreateIterator(); It; ++It)
	{
		if (!Seen.Contains(It->Key))
		{
			if (It->Value)
			{
				It->Value->DestroyComponent();
			}
			It.RemoveCurrent();
		}
	}
}

//-----------------------------------------------------------------------------
// Decals
//-----------------------------------------------------------------------------

void UAetherFXComponent::UpdateDecals()
{
	if (!Runtime.IsValid())
	{
		return;
	}

	if (!bEnableDecals)
	{
		for (TPair<FName, TObjectPtr<UDecalComponent>>& Pair : Decals)
		{
			if (Pair.Value)
			{
				Pair.Value->DestroyComponent();
			}
		}
		Decals.Reset();
		return;
	}

	AActor* Owner = GetOwner();
	const int32 Count = aetherfx_runtime_decal_count(Runtime.Get());

	TSet<FName> Seen;
	Seen.Reserve(FMath::Max(0, Count));

	for (int32 Index = 0; Index < Count; ++Index)
	{
		struct aetherfx_decal_info Info;
		if (aetherfx_decal_info(Runtime.Get(), Index, &Info) != AETHERFX_OK)
		{
			continue;
		}

		const FName Key = ToName(Info.id).IsNone() ? FName(*FString::Printf(TEXT("decal_%d"), Index)) : ToName(Info.id);
		Seen.Add(Key);

		TObjectPtr<UDecalComponent>* Existing = Decals.Find(Key);
		UDecalComponent* Decal = Existing ? Existing->Get() : nullptr;

		if (!Decal && Owner)
		{
			Decal = NewObject<UDecalComponent>(Owner, NAME_None, RF_Transient);
			Decal->SetMobility(EComponentMobility::Movable);
			Decal->SetupAttachment(this);
			Decal->RegisterComponent();

			if (UMaterialInterface* Base = AetherFX::Materials::Decal())
			{
				Decal->SetDecalMaterial(Base);
				Decal->CreateDynamicMaterialInstance();
			}
			Decals.Add(Key, Decal);
		}

		if (!Decal)
		{
			continue;
		}

		const FVector Location = AetherFX::PositionToUnreal(Info.position);
		FVector Normal = AetherFX::DirectionToUnreal(Info.normal);
		if (!Normal.Normalize())
		{
			Normal = FVector::UpVector;
		}

		// Unreal decals project along their local -X... which is to say the
		// component's +X points into the surface. Our normal points out of it.
		FQuat Orientation = FRotationMatrix::MakeFromX(-Normal).ToQuat();
		Orientation = Orientation * FQuat(FVector::XAxisVector, FMath::DegreesToRadians(Info.rotation_deg));
		Decal->SetRelativeLocationAndRotation(Location, Orientation);

		const double HalfWidth = FMath::Max(1.0, AetherFX::LengthToUnreal(Info.size[0]) * 0.5);
		const double HalfHeight = FMath::Max(1.0, AetherFX::LengthToUnreal(Info.size[1]) * 0.5);
		Decal->DecalSize = FVector(FMath::Max(16.0, FMath::Max(HalfWidth, HalfHeight)), HalfWidth, HalfHeight);
		Decal->MarkRenderStateDirty();

		UMaterialInstanceDynamic* Dynamic = Cast<UMaterialInstanceDynamic>(Decal->GetDecalMaterial());
		if (!Dynamic)
		{
			Dynamic = Decal->CreateDynamicMaterialInstance();
		}
		if (Dynamic)
		{
			int32 Frames = 1;
			if (UTexture2D* Texture = ResolveTexture(Info.texture_id, &Frames))
			{
				Dynamic->SetTextureParameterValue(AetherFX::MaterialParams::BaseTexture, Texture);
			}
			Dynamic->SetVectorParameterValue(AetherFX::MaterialParams::BaseColor,
				FLinearColor(Info.color[0], Info.color[1], Info.color[2], 1.0f));
			Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::Opacity, Info.opacity);
			Dynamic->SetVectorParameterValue(AetherFX::MaterialParams::EmissiveColor,
				FLinearColor(Info.color[0], Info.color[1], Info.color[2], 1.0f));
			Dynamic->SetScalarParameterValue(AetherFX::MaterialParams::EmissiveIntensity, Info.emissive);
		}

		Decal->SetVisibility(Info.opacity > 0.0f);
	}

	for (auto It = Decals.CreateIterator(); It; ++It)
	{
		if (!Seen.Contains(It->Key))
		{
			if (It->Value)
			{
				It->Value->DestroyComponent();
			}
			It.RemoveCurrent();
		}
	}
}

//-----------------------------------------------------------------------------
// Analytic mesh instances
//-----------------------------------------------------------------------------

void UAetherFXComponent::UpdateMeshInstances()
{
	if (!Runtime.IsValid())
	{
		return;
	}

	AActor* Owner = GetOwner();
	const int32 Count = aetherfx_runtime_mesh_instance_count(Runtime.Get());

	TSet<FName> Seen;
	Seen.Reserve(FMath::Max(0, Count));

	for (int32 Index = 0; Index < Count; ++Index)
	{
		struct aetherfx_mesh_instance_info Info;
		if (aetherfx_mesh_instance_info(Runtime.Get(), Index, &Info) != AETHERFX_OK)
		{
			continue;
		}

		const FName Key = ToName(Info.id).IsNone() ? FName(*FString::Printf(TEXT("mesh_%d"), Index)) : ToName(Info.id);
		Seen.Add(Key);

		TObjectPtr<UStaticMeshComponent>* Existing = MeshInstances.Find(Key);
		UStaticMeshComponent* MeshComponent = Existing ? Existing->Get() : nullptr;

		if (!MeshComponent && Owner)
		{
			MeshComponent = NewObject<UStaticMeshComponent>(Owner, NAME_None, RF_Transient);
			MeshComponent->SetMobility(EComponentMobility::Movable);
			MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			MeshComponent->SetCastShadow(bCastShadows);
			MeshComponent->bReceivesDecals = false;
			MeshComponent->SetupAttachment(this);
			MeshComponent->RegisterComponent();
			MeshInstances.Add(Key, MeshComponent);
		}

		if (!MeshComponent)
		{
			continue;
		}

		if (!MeshComponent->GetStaticMesh())
		{
			if (UStaticMesh* MeshAsset = ResolveMesh(Info.mesh_id, 0))
			{
				MeshComponent->SetStaticMesh(MeshAsset);
				FSystemState Scratch;
				if (UMaterialInstanceDynamic* Dynamic = MakeSystemMaterial(
						AETHERFX_BLEND_ALPHA, AETHERFX_RENDER_MESH, Info.material_id, nullptr, Scratch))
				{
					MeshComponent->SetMaterial(0, Dynamic);
				}
			}
		}

		MeshComponent->SetRelativeTransform(FTransform(AetherFX::TransformToUnreal(Info.transform)));
		MeshComponent->SetVisibility(Info.visible != 0);
	}

	for (auto It = MeshInstances.CreateIterator(); It; ++It)
	{
		if (!Seen.Contains(It->Key))
		{
			if (It->Value)
			{
				It->Value->DestroyComponent();
			}
			It.RemoveCurrent();
		}
	}
}

//-----------------------------------------------------------------------------
// Trails and beams
//-----------------------------------------------------------------------------

namespace
{
	/**
	 * Extrudes a polyline into a camera-facing strip.
	 *
	 * `Points` are in the component's local space (already converted). Each
	 * vertex is offset by +-HalfWidth along cross(tangent, toView), which is the
	 * construction docs/ENGINE_INTEGRATION.md 4 prescribes for trails and beams.
	 */
	void BuildStrip(
		const TArray<FVector>& Points,
		const TArray<float>& HalfWidths,
		const TArray<FLinearColor>& Colors,
		const TArray<float>& Us,
		const FVector& ViewLocation,
		TArray<FVector>& OutVertices,
		TArray<int32>& OutTriangles,
		TArray<FVector>& OutNormals,
		TArray<FVector2D>& OutUVs,
		TArray<FLinearColor>& OutColors)
	{
		const int32 Count = Points.Num();
		if (Count < 2)
		{
			return;
		}

		const int32 BaseVertex = OutVertices.Num();

		for (int32 Index = 0; Index < Count; ++Index)
		{
			const FVector Previous = Points[FMath::Max(0, Index - 1)];
			const FVector Next = Points[FMath::Min(Count - 1, Index + 1)];
			FVector Tangent = Next - Previous;
			if (!Tangent.Normalize())
			{
				Tangent = FVector::ForwardVector;
			}

			FVector ToView = ViewLocation - Points[Index];
			if (!ToView.Normalize())
			{
				ToView = FVector::ForwardVector;
			}

			FVector Side = FVector::CrossProduct(Tangent, ToView);
			if (!Side.Normalize())
			{
				Side = FVector::CrossProduct(Tangent, FVector::UpVector).GetSafeNormal(UE_SMALL_NUMBER, FVector::RightVector);
			}

			const double HalfWidth = HalfWidths.IsValidIndex(Index) ? HalfWidths[Index] : 1.0;
			const FLinearColor Color = Colors.IsValidIndex(Index) ? Colors[Index] : FLinearColor::White;
			const float U = Us.IsValidIndex(Index) ? Us[Index] : (static_cast<float>(Index) / static_cast<float>(Count - 1));

			OutVertices.Add(Points[Index] + Side * HalfWidth);
			OutVertices.Add(Points[Index] - Side * HalfWidth);
			OutNormals.Add(ToView);
			OutNormals.Add(ToView);
			OutUVs.Emplace(U, 0.0);
			OutUVs.Emplace(U, 1.0);
			OutColors.Add(Color);
			OutColors.Add(Color);
		}

		for (int32 Segment = 0; Segment < Count - 1; ++Segment)
		{
			const int32 V0 = BaseVertex + Segment * 2;
			const int32 V1 = V0 + 1;
			const int32 V2 = V0 + 2;
			const int32 V3 = V0 + 3;

			OutTriangles.Add(V0); OutTriangles.Add(V2); OutTriangles.Add(V1);
			OutTriangles.Add(V1); OutTriangles.Add(V2); OutTriangles.Add(V3);
		}
	}
}

void UAetherFXComponent::UpdateTrailsAndBeams(const FVector& ViewLocation)
{
	RibbonSectionCount = 0;

	if (!Runtime.IsValid() || !bEnableTrailsAndBeams)
	{
		if (RibbonMesh)
		{
			RibbonMesh->ClearAllMeshSections();
		}
		return;
	}

	const int32 TrailCount = FMath::Max(0, aetherfx_runtime_trail_count(Runtime.Get()));
	const int32 BeamCount = FMath::Max(0, aetherfx_runtime_beam_count(Runtime.Get()));

	if (TrailCount == 0 && BeamCount == 0)
	{
		if (RibbonMesh)
		{
			RibbonMesh->ClearAllMeshSections();
		}
		return;
	}

	AActor* Owner = GetOwner();
	if (!RibbonMesh && Owner)
	{
		RibbonMesh = NewObject<UProceduralMeshComponent>(Owner, NAME_None, RF_Transient);
		RibbonMesh->bUseAsyncCooking = false;
		RibbonMesh->SetMobility(EComponentMobility::Movable);
		RibbonMesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		RibbonMesh->SetCastShadow(false);
		RibbonMesh->bReceivesDecals = false;
		RibbonMesh->SetupAttachment(this);
		RibbonMesh->RegisterComponent();
	}
	if (!RibbonMesh)
	{
		return;
	}

	// Trails and beams are rebuilt from scratch every step (the library does not
	// keep their vertex counts stable), so the sections are rebuilt too.
	RibbonMesh->ClearAllMeshSections();

	TArray<FVector> Vertices;
	TArray<int32> Triangles;
	TArray<FVector> Normals;
	TArray<FVector2D> UVs;
	TArray<FLinearColor> Colors;
	TArray<FProcMeshTangent> Tangents;

	TArray<FVector> Points;
	TArray<float> HalfWidths;
	TArray<FLinearColor> PointColors;
	TArray<float> Us;

	int32 SectionIndex = 0;

	for (int32 Trail = 0; Trail < TrailCount; ++Trail)
	{
		struct aetherfx_trail_info Info;
		if (aetherfx_trail_info(Runtime.Get(), Trail, &Info) != AETHERFX_OK)
		{
			continue;
		}

		Vertices.Reset(); Triangles.Reset(); Normals.Reset(); UVs.Reset(); Colors.Reset();

		for (int32 Ribbon = 0; Ribbon < static_cast<int32>(Info.ribbon_count); ++Ribbon)
		{
			const struct aetherfx_trail_vertex* Verts = nullptr;
			size_t VertexCount = 0;
			if (aetherfx_trail_ribbon(Runtime.Get(), Trail, Ribbon, &Verts, &VertexCount) != AETHERFX_OK || !Verts || VertexCount < 2)
			{
				continue;
			}

			Points.Reset(); HalfWidths.Reset(); PointColors.Reset(); Us.Reset();
			for (size_t Vertex = 0; Vertex < VertexCount; ++Vertex)
			{
				const struct aetherfx_trail_vertex& V = Verts[Vertex];
				Points.Add(AetherFX::PositionToUnreal(V.position));
				HalfWidths.Add(static_cast<float>(AetherFX::LengthToUnreal(V.width) * 0.5));
				PointColors.Add(FLinearColor(
					V.color[0] * FMath::Max(1.0f, 1.0f + V.emissive),
					V.color[1] * FMath::Max(1.0f, 1.0f + V.emissive),
					V.color[2] * FMath::Max(1.0f, 1.0f + V.emissive),
					V.opacity));
				Us.Add(V.u);
			}

			BuildStrip(Points, HalfWidths, PointColors, Us, ViewLocation, Vertices, Triangles, Normals, UVs, Colors);
		}

		if (Vertices.Num() >= 3 && Triangles.Num() >= 3)
		{
			RibbonMesh->CreateMeshSection_LinearColor(SectionIndex, Vertices, Triangles, Normals, UVs, Colors, Tangents, /*bCreateCollision*/ false);
			if (UMaterialInstanceDynamic* Dynamic = GetRibbonMaterial(Info.blend, Info.material_id))
			{
				RibbonMesh->SetMaterial(SectionIndex, Dynamic);
			}
			else if (UMaterialInterface* Base = AetherFX::Materials::Ribbon())
			{
				RibbonMesh->SetMaterial(SectionIndex, Base);
			}
			++SectionIndex;
		}
	}

	for (int32 Beam = 0; Beam < BeamCount; ++Beam)
	{
		struct aetherfx_beam_info Info;
		if (aetherfx_beam_info(Runtime.Get(), Beam, &Info) != AETHERFX_OK)
		{
			continue;
		}

		Vertices.Reset(); Triangles.Reset(); Normals.Reset(); UVs.Reset(); Colors.Reset();

		for (int32 Polyline = 0; Polyline < static_cast<int32>(Info.polyline_count); ++Polyline)
		{
			const float* Xyz = nullptr;
			size_t PointCount = 0;
			if (aetherfx_beam_polyline(Runtime.Get(), Beam, Polyline, &Xyz, &PointCount) != AETHERFX_OK || !Xyz || PointCount < 2)
			{
				continue;
			}

			// Polyline 0 is the bolt; the rest are branches, drawn thinner.
			const float WidthScale = (Polyline == 0) ? 1.0f : 0.6f;
			const float HalfWidth = static_cast<float>(AetherFX::LengthToUnreal(Info.width) * 0.5 * WidthScale);
			const FLinearColor Color(
				Info.color[0] * FMath::Max(1.0f, 1.0f + Info.emissive),
				Info.color[1] * FMath::Max(1.0f, 1.0f + Info.emissive),
				Info.color[2] * FMath::Max(1.0f, 1.0f + Info.emissive),
				Info.color[3]);

			Points.Reset(); HalfWidths.Reset(); PointColors.Reset(); Us.Reset();
			for (size_t Point = 0; Point < PointCount; ++Point)
			{
				Points.Add(AetherFX::PositionToUnreal(&Xyz[Point * 3]));
				HalfWidths.Add(HalfWidth);
				PointColors.Add(Color);
				Us.Add(static_cast<float>(Point) / static_cast<float>(PointCount - 1));
			}

			BuildStrip(Points, HalfWidths, PointColors, Us, ViewLocation, Vertices, Triangles, Normals, UVs, Colors);
		}

		if (Vertices.Num() >= 3 && Triangles.Num() >= 3)
		{
			RibbonMesh->CreateMeshSection_LinearColor(SectionIndex, Vertices, Triangles, Normals, UVs, Colors, Tangents, /*bCreateCollision*/ false);
			if (UMaterialInstanceDynamic* Dynamic = GetRibbonMaterial(Info.blend, Info.material_id))
			{
				RibbonMesh->SetMaterial(SectionIndex, Dynamic);
			}
			else if (UMaterialInterface* Base = AetherFX::Materials::Ribbon())
			{
				RibbonMesh->SetMaterial(SectionIndex, Base);
			}
			++SectionIndex;
		}
	}

	RibbonSectionCount = SectionIndex;
}

//-----------------------------------------------------------------------------
// Stats
//-----------------------------------------------------------------------------

int32 UAetherFXComponent::GetInstanceCount() const
{
	int32 Total = 0;
	for (const TObjectPtr<UInstancedStaticMeshComponent>& Instanced : InstancedComponents)
	{
		if (Instanced)
		{
			Total += Instanced->GetInstanceCount();
		}
	}
	return Total;
}

int32 UAetherFXComponent::GetLightCount() const
{
	return Lights.Num();
}

int32 UAetherFXComponent::GetDecalCount() const
{
	return Decals.Num();
}

int32 UAetherFXComponent::GetMeshInstanceCount() const
{
	return MeshInstances.Num();
}

int32 UAetherFXComponent::GetDrawableParticleCount() const
{
	if (!Runtime.IsValid())
	{
		return 0;
	}

	int32 Total = 0;
	for (int32 SystemIndex = 0; SystemIndex < Systems.Num(); ++SystemIndex)
	{
		const int32 RenderMode = Systems[SystemIndex].RenderMode;
		if (RenderMode == AETHERFX_RENDER_RIBBON || RenderMode == AETHERFX_RENDER_NONE)
		{
			continue;
		}

		struct aetherfx_particle_system_info Info;
		if (aetherfx_particle_system_info(Runtime.Get(), SystemIndex, &Info) == AETHERFX_OK)
		{
			Total += static_cast<int32>(Info.count);
		}
	}
	return Total;
}

FName UAetherFXComponent::GetSystemId(int32 SystemIndex) const
{
	return Systems.IsValidIndex(SystemIndex) ? Systems[SystemIndex].Id : NAME_None;
}

UInstancedStaticMeshComponent* UAetherFXComponent::GetSystemInstancedComponent(int32 SystemIndex, int32 Bucket) const
{
	if (!Systems.IsValidIndex(SystemIndex))
	{
		return nullptr;
	}
	const FSystemState& State = Systems[SystemIndex];
	return State.Instanced.IsValidIndex(Bucket) ? State.Instanced[Bucket].Get() : nullptr;
}
