// Copyright AetherFX.
//
// Thin RAII wrappers around the three opaque handles of the C ABI. Nothing in
// this file is a UObject and none of it is reflected: raw `aetherfx_*` pointers
// never reach Blueprints. UObjects hold these by value (or by TSharedPtr) and
// the destructor calls the matching `*_free`.

#pragma once

#include "CoreMinimal.h"
#include "Templates/SharedPointer.h"

struct aetherfx_effect;
struct aetherfx_compiled;
struct aetherfx_runtime;

namespace AetherFX
{
	/** Owns an `aetherfx_effect*` (a parsed authoring document). Move-only. */
	class AETHERFXRUNTIME_API FEffectHandle
	{
	public:
		FEffectHandle() = default;
		explicit FEffectHandle(aetherfx_effect* InHandle) : Handle(InHandle) {}
		~FEffectHandle() { Reset(); }

		FEffectHandle(const FEffectHandle&) = delete;
		FEffectHandle& operator=(const FEffectHandle&) = delete;

		FEffectHandle(FEffectHandle&& Other) noexcept : Handle(Other.Handle) { Other.Handle = nullptr; }
		FEffectHandle& operator=(FEffectHandle&& Other) noexcept
		{
			if (this != &Other)
			{
				Reset();
				Handle = Other.Handle;
				Other.Handle = nullptr;
			}
			return *this;
		}

		void Reset();
		bool IsValid() const { return Handle != nullptr; }
		aetherfx_effect* Get() const { return Handle; }

	private:
		aetherfx_effect* Handle = nullptr;
	};

	/**
	 * Owns an `aetherfx_compiled*` (a validated effect plus its baked resources
	 * and execution plan). Shared: one compile per asset, many runtimes.
	 */
	class AETHERFXRUNTIME_API FCompiledHandle
	{
	public:
		FCompiledHandle() = default;
		explicit FCompiledHandle(aetherfx_compiled* InHandle) : Handle(InHandle) {}
		~FCompiledHandle() { Reset(); }

		FCompiledHandle(const FCompiledHandle&) = delete;
		FCompiledHandle& operator=(const FCompiledHandle&) = delete;

		FCompiledHandle(FCompiledHandle&& Other) noexcept : Handle(Other.Handle) { Other.Handle = nullptr; }
		FCompiledHandle& operator=(FCompiledHandle&& Other) noexcept
		{
			if (this != &Other)
			{
				Reset();
				Handle = Other.Handle;
				Other.Handle = nullptr;
			}
			return *this;
		}

		void Reset();
		bool IsValid() const { return Handle != nullptr; }
		aetherfx_compiled* Get() const { return Handle; }

	private:
		aetherfx_compiled* Handle = nullptr;
	};

	/** Owns an `aetherfx_runtime*` -- one playing instance. Move-only. */
	class AETHERFXRUNTIME_API FRuntimeHandle
	{
	public:
		FRuntimeHandle() = default;
		explicit FRuntimeHandle(aetherfx_runtime* InHandle) : Handle(InHandle) {}
		~FRuntimeHandle() { Reset(); }

		FRuntimeHandle(const FRuntimeHandle&) = delete;
		FRuntimeHandle& operator=(const FRuntimeHandle&) = delete;

		FRuntimeHandle(FRuntimeHandle&& Other) noexcept : Handle(Other.Handle) { Other.Handle = nullptr; }
		FRuntimeHandle& operator=(FRuntimeHandle&& Other) noexcept
		{
			if (this != &Other)
			{
				Reset();
				Handle = Other.Handle;
				Other.Handle = nullptr;
			}
			return *this;
		}

		void Reset();
		bool IsValid() const { return Handle != nullptr; }
		aetherfx_runtime* Get() const { return Handle; }

	private:
		aetherfx_runtime* Handle = nullptr;
	};

	using FCompiledHandlePtr = TSharedPtr<FCompiledHandle, ESPMode::ThreadSafe>;
	using FCompiledHandleRef = TSharedRef<FCompiledHandle, ESPMode::ThreadSafe>;

	/** Library build version, for logging and ABI checks. */
	AETHERFXRUNTIME_API FString GetLibraryVersionString();

	/** True when the linked library's ABI matches the header we compiled against. */
	AETHERFXRUNTIME_API bool CheckAbiVersion();

	/** The last error reported on this thread by the C library ("" when none). */
	AETHERFXRUNTIME_API FString GetLastError();
}
