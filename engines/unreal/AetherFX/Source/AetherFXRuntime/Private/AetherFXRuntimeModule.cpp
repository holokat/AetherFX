// Copyright AetherFX.

#include "AetherFXLog.h"
#include "AetherFXHandles.h"
#include "AetherFXTypes.h"

#include "Modules/ModuleManager.h"

#include "aetherfx/aetherfx.h"

DEFINE_LOG_CATEGORY(LogAetherFX);

namespace AetherFX
{
	namespace MaterialParams
	{
		const FName BaseTexture(TEXT("BaseTexture"));
		const FName NoiseTexture(TEXT("NoiseTexture"));
		const FName BaseColor(TEXT("BaseColor"));
		const FName EmissiveColor(TEXT("EmissiveColor"));
		const FName EmissiveIntensity(TEXT("EmissiveIntensity"));
		const FName Opacity(TEXT("Opacity"));
		const FName FresnelPower(TEXT("FresnelPower"));
		const FName Additive(TEXT("Additive"));
	}
}

class FAetherFXRuntimeModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		int32 Major = 0, Minor = 0, Patch = 0;
		aetherfx_version(&Major, &Minor, &Patch);
		UE_LOG(LogAetherFX, Log, TEXT("AetherFX runtime online: libaetherfx %d.%d.%d (abi %d, header abi %d)"),
			Major, Minor, Patch, aetherfx_abi_version(), static_cast<int32>(AETHERFX_ABI_VERSION));

		AetherFX::CheckAbiVersion();
	}

	virtual void ShutdownModule() override
	{
		UE_LOG(LogAetherFX, Log, TEXT("AetherFX runtime offline."));
	}
};

IMPLEMENT_MODULE(FAetherFXRuntimeModule, AetherFXRuntime)
