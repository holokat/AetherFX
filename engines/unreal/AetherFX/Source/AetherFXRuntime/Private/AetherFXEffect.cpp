// Copyright AetherFX.

#include "AetherFXEffect.h"

#include "AetherFXLog.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"

#include "aetherfx/aetherfx.h"

UAetherFXEffect::UAetherFXEffect() = default;

void UAetherFXEffect::BeginDestroy()
{
	InvalidateCompiled();
	Super::BeginDestroy();
}

#if WITH_EDITOR
void UAetherFXEffect::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	static const FName EffectJsonName = GET_MEMBER_NAME_CHECKED(UAetherFXEffect, EffectJson);
	static const FName FixedTimeStepName = GET_MEMBER_NAME_CHECKED(UAetherFXEffect, FixedTimeStep);

	const FName Changed = PropertyChangedEvent.GetPropertyName();
	if (Changed == EffectJsonName || Changed == FixedTimeStepName)
	{
		InvalidateCompiled();
	}
}
#endif

void UAetherFXEffect::InvalidateCompiled()
{
	Compiled.Reset();
	bCompileFailed = false;
}

bool UAetherFXEffect::IsCompiled() const
{
	return Compiled.IsValid() && Compiled->IsValid();
}

bool UAetherFXEffect::K2_Compile()
{
	return Compile();
}

bool UAetherFXEffect::Compile()
{
	if (IsCompiled())
	{
		return true;
	}
	if (bCompileFailed)
	{
		return false;
	}

	if (EffectJson.IsEmpty())
	{
		UE_LOG(LogAetherFX, Error, TEXT("UAetherFXEffect '%s' has no EffectJson to compile."), *GetName());
		bCompileFailed = true;
		return false;
	}

	if (!AetherFX::CheckAbiVersion())
	{
		bCompileFailed = true;
		return false;
	}

	const FTCHARToUTF8 Utf8(*EffectJson);

	AetherFX::FEffectHandle Effect(aetherfx_effect_load_json(Utf8.Get(), static_cast<size_t>(Utf8.Length())));
	if (!Effect.IsValid())
	{
		UE_LOG(LogAetherFX, Error, TEXT("UAetherFXEffect '%s': load failed: %s"),
			*GetName(), *AetherFX::GetLastError());
		bCompileFailed = true;
		return false;
	}

	if (EffectName.IsEmpty())
	{
		if (const char* Name = aetherfx_effect_name(Effect.Get()))
		{
			EffectName = UTF8_TO_TCHAR(Name);
		}
	}
	if (Duration <= 0.0f)
	{
		const double Authored = aetherfx_effect_duration(Effect.Get());
		if (Authored > 0.0)
		{
			Duration = static_cast<float>(Authored);
		}
	}

	AetherFX::FCompiledHandlePtr NewCompiled = MakeShared<AetherFX::FCompiledHandle, ESPMode::ThreadSafe>(
		aetherfx_compile(Effect.Get(), FixedTimeStep));

	if (!NewCompiled->IsValid())
	{
		UE_LOG(LogAetherFX, Error, TEXT("UAetherFXEffect '%s': compile failed: %s"),
			*GetName(), *AetherFX::GetLastError());
		bCompileFailed = true;
		return false;
	}

	if (char* Diagnostics = aetherfx_compiled_diagnostics_json(NewCompiled->Get()))
	{
		CompileDiagnostics = UTF8_TO_TCHAR(Diagnostics);
		aetherfx_free_string(Diagnostics);
	}

	if (aetherfx_compiled_ok(NewCompiled->Get()) != 1)
	{
		UE_LOG(LogAetherFX, Error, TEXT("UAetherFXEffect '%s': effect has error diagnostics: %s"),
			*GetName(), *CompileDiagnostics);
		bCompileFailed = true;
		return false;
	}

	Compiled = MoveTemp(NewCompiled);

	// The resolved speed: the authored `time_scale` with the document's controls
	// folded in, which is what a host must drive its clock with. Reading it off
	// the plan rather than the document is what makes a Speed control work.
	if (const double Resolved = aetherfx_compiled_time_scale(Compiled->Get()); Resolved > 0.0)
	{
		TimeScale = Resolved;
	}

	UE_LOG(LogAetherFX, Log,
		TEXT("UAetherFXEffect '%s' compiled: %d textures, %d meshes, %d materials, dt=%.6f, time_scale=%.3f"),
		*GetName(),
		aetherfx_compiled_texture_count(Compiled->Get()),
		aetherfx_compiled_mesh_count(Compiled->Get()),
		aetherfx_compiled_material_count(Compiled->Get()),
		aetherfx_compiled_fixed_dt(Compiled->Get()),
		TimeScale);

	return true;
}

AetherFX::FCompiledHandlePtr UAetherFXEffect::GetCompiled()
{
	if (!IsCompiled())
	{
		Compile();
	}
	return IsCompiled() ? Compiled : nullptr;
}

FString UAetherFXEffect::SanitizeResourceId(const FString& Id)
{
	FString Out = Id;
	// `#` separates a seeded mesh variant; FName and package names reject it.
	Out.ReplaceInline(TEXT("#"), TEXT("_"), ESearchCase::CaseSensitive);
	Out.ReplaceInline(TEXT(" "), TEXT("_"), ESearchCase::CaseSensitive);
	Out.ReplaceInline(TEXT("."), TEXT("_"), ESearchCase::CaseSensitive);
	Out.ReplaceInline(TEXT("/"), TEXT("_"), ESearchCase::CaseSensitive);
	return Out;
}

FName UAetherFXEffect::MakeMeshVariantKey(const FString& MeshId, int32 Variant)
{
	if (MeshId.IsEmpty())
	{
		return NAME_None;
	}
	if (Variant <= 0)
	{
		return FName(*SanitizeResourceId(MeshId));
	}
	return FName(*SanitizeResourceId(FString::Printf(TEXT("%s#%d"), *MeshId, Variant)));
}

UTexture2D* UAetherFXEffect::FindTexture(FName TextureId) const
{
	const TObjectPtr<UTexture2D>* Found = Textures.Find(TextureId);
	return Found ? Found->Get() : nullptr;
}

UStaticMesh* UAetherFXEffect::FindMesh(FName MeshId) const
{
	const TObjectPtr<UStaticMesh>* Found = Meshes.Find(MeshId);
	return Found ? Found->Get() : nullptr;
}

UStaticMesh* UAetherFXEffect::FindMeshVariant(const FString& MeshId, int32 Variant) const
{
	if (UStaticMesh* Mesh = FindMesh(MakeMeshVariantKey(MeshId, Variant)))
	{
		return Mesh;
	}
	return FindMesh(MakeMeshVariantKey(MeshId, 0));
}

const FAetherFXMaterialParams* UAetherFXEffect::FindMaterial(FName MaterialId) const
{
	return Materials.Find(MaterialId);
}
