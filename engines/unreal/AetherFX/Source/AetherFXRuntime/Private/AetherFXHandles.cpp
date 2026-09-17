// Copyright AetherFX.

#include "AetherFXHandles.h"

#include "AetherFXLog.h"

#include "aetherfx/aetherfx.h"

namespace AetherFX
{
	void FEffectHandle::Reset()
	{
		if (Handle)
		{
			aetherfx_effect_free(Handle);
			Handle = nullptr;
		}
	}

	void FCompiledHandle::Reset()
	{
		if (Handle)
		{
			aetherfx_compiled_free(Handle);
			Handle = nullptr;
		}
	}

	void FRuntimeHandle::Reset()
	{
		if (Handle)
		{
			aetherfx_runtime_free(Handle);
			Handle = nullptr;
		}
	}

	FString GetLibraryVersionString()
	{
		const char* Version = aetherfx_version_string();
		return Version ? FString(UTF8_TO_TCHAR(Version)) : FString(TEXT("<unknown>"));
	}

	bool CheckAbiVersion()
	{
		const int32 Linked = aetherfx_abi_version();
		if (Linked != AETHERFX_ABI_VERSION)
		{
			UE_LOG(LogAetherFX, Error,
				TEXT("libaetherfx ABI mismatch: the plugin was compiled against %d but the library reports %d. ")
				TEXT("Re-run engines/unreal/scripts/sync_libs.sh and rebuild."),
				static_cast<int32>(AETHERFX_ABI_VERSION), Linked);
			return false;
		}
		return true;
	}

	FString GetLastError()
	{
		const char* Message = aetherfx_last_error();
		return (Message && Message[0] != '\0') ? FString(UTF8_TO_TCHAR(Message)) : FString();
	}
}
