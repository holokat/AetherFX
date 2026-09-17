// Copyright AetherFX.

#include "AetherFXEditorModule.h"

#include "AetherFXLog.h"
#include "Modules/ModuleManager.h"

#define LOCTEXT_NAMESPACE "AetherFXEditor"

void FAetherFXEditorModule::StartupModule()
{
	UE_LOG(LogAetherFX, Log, TEXT("AetherFX editor online."));
}

void FAetherFXEditorModule::ShutdownModule()
{
	UE_LOG(LogAetherFX, Log, TEXT("AetherFX editor offline."));
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FAetherFXEditorModule, AetherFXEditor)
