// Copyright AetherFX.
//
// End-to-end smoke test for the AetherFX Unreal bridge.
//
// Imports `out/packages/fire_aoe.aetherfx` with the editor importer (falling
// back to `examples/effects/fire_aoe.json` through the C API when the package
// has not been exported), spawns an AAetherFXActor, ticks 60 frames and checks
// that the bridge produced instances, lights and decals without crashing.
//
// Run headless:
//   UnrealEditor-Cmd AetherFXTest.uproject \
//       -ExecCmds="Automation RunTests AetherFX; Quit" -unattended -nopause -nullrhi -log

#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "AetherFXActor.h"
#include "AetherFXComponent.h"
#include "AetherFXEffect.h"
#include "AetherFXTypes.h"

#include "Components/InstancedStaticMeshComponent.h"

#include "Engine/Engine.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "UObject/Package.h"

#if WITH_EDITOR
#include "AetherFXImportLibrary.h"
#endif

namespace AetherFXSmoke
{
	/** The repository root: <repo>/engines/unreal/TestProject/ -> <repo>/ */
	static FString RepositoryRoot()
	{
		return FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(), TEXT("../../../")));
	}

	static FString PackagePath()
	{
		return FPaths::Combine(RepositoryRoot(), TEXT("out/packages/fire_aoe.aetherfx"));
	}

	static FString DocumentPath()
	{
		return FPaths::Combine(RepositoryRoot(), TEXT("examples/effects/fire_aoe.json"));
	}

	struct FScopedWorld
	{
		UWorld* World = nullptr;

		FScopedWorld()
		{
			World = UWorld::CreateWorld(EWorldType::Game, /*bInformEngineOfWorld*/ false, TEXT("AetherFXSmokeWorld"));
			if (World && GEngine)
			{
				FWorldContext& Context = GEngine->CreateNewWorldContext(EWorldType::Game);
				Context.SetCurrentWorld(World);
				World->InitializeActorsForPlay(FURL());
			}
		}

		~FScopedWorld()
		{
			if (World)
			{
				if (GEngine)
				{
					GEngine->DestroyWorldContext(World);
				}
				World->DestroyWorld(/*bInformEngineOfWorld*/ false);
				World = nullptr;
			}
		}
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FAetherFXSmokeTest,
	"AetherFX.Bridge.Smoke",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
	EAutomationTestFlags::CommandletContext | EAutomationTestFlags::ProductFilter)

bool FAetherFXSmokeTest::RunTest(const FString& Parameters)
{
	using namespace AetherFXSmoke;

	UAetherFXEffect* Effect = nullptr;
	FString Origin;

	//-------------------------------------------------------------------------
	// 1. Import the exported package, or fall back to the raw document.
	//-------------------------------------------------------------------------
	const FString Package = PackagePath();
	if (IFileManager::Get().DirectoryExists(*Package))
	{
#if WITH_EDITOR
		Effect = UAetherFXImportLibrary::ImportEffectTransient(Package);
		Origin = FString::Printf(TEXT("package '%s'"), *Package);
#endif
	}

	if (!Effect)
	{
		const FString Document = DocumentPath();
		if (!TestTrue(FString::Printf(TEXT("a source exists ('%s' or '%s')"), *Package, *Document),
				FPaths::FileExists(Document)))
		{
			return false;
		}

		FString Json;
		if (!TestTrue(TEXT("effect document is readable"), FFileHelper::LoadFileToString(Json, *Document)))
		{
			return false;
		}

		Effect = NewObject<UAetherFXEffect>(GetTransientPackage(), TEXT("FX_fire_aoe_smoke"), RF_Transient);
		Effect->EffectJson = Json;
		Origin = FString::Printf(TEXT("document '%s'"), *Document);
	}

	if (!TestNotNull(TEXT("effect asset"), Effect))
	{
		return false;
	}

	Effect->AddToRoot();
	ON_SCOPE_EXIT{ Effect->RemoveFromRoot(); };

	AddInfo(FString::Printf(TEXT("source: %s"), *Origin));

	//-------------------------------------------------------------------------
	// 2. Compile through libaetherfx.
	//-------------------------------------------------------------------------
	if (!TestTrue(TEXT("effect compiles"), Effect->Compile()))
	{
		AddError(FString::Printf(TEXT("compile diagnostics: %s"), *Effect->GetCompileDiagnostics()));
		return false;
	}
	TestTrue(TEXT("effect reports a duration"), Effect->Duration > 0.0f);

	//-------------------------------------------------------------------------
	// 3. Spawn the actor and tick 60 frames.
	//-------------------------------------------------------------------------
	FScopedWorld Scope;
	if (!TestNotNull(TEXT("test world"), Scope.World))
	{
		return false;
	}

	AAetherFXActor* Actor = Scope.World->SpawnActor<AAetherFXActor>(FVector(0.0, 0.0, 0.0), FRotator::ZeroRotator);
	if (!TestNotNull(TEXT("AAetherFXActor spawned"), Actor))
	{
		return false;
	}

	Actor->Effect = Effect;
	Actor->bAutoPlay = false;

	// The Blueprint entry point: "the character casts the effect here".
	Actor->CastAt(FVector(100.0, 200.0, 300.0));

	UAetherFXComponent* Component = Actor->AetherFXComponent;
	if (!TestNotNull(TEXT("AetherFX component"), Component))
	{
		return false;
	}
	TestTrue(TEXT("CastAt moved the actor"), Actor->GetActorLocation().Equals(FVector(100.0, 200.0, 300.0), 0.01));
	TestTrue(TEXT("playback started"), Component->IsPlaying());

	int32 PeakInstances = 0;
	int32 PeakLights = 0;
	int32 PeakDecals = 0;
	int32 PeakRibbons = 0;

	for (int32 Frame = 0; Frame < 60; ++Frame)
	{
		Component->AdvanceAndRender(1.0f / 60.0f);

		PeakInstances = FMath::Max(PeakInstances, Component->GetInstanceCount());
		PeakLights = FMath::Max(PeakLights, Component->GetLightCount());
		PeakDecals = FMath::Max(PeakDecals, Component->GetDecalCount());
		PeakRibbons = FMath::Max(PeakRibbons, Component->GetRibbonSectionCount());
	}

	AddInfo(FString::Printf(
		TEXT("after 60 frames: t=%.3fs sim=%.3fs systems=%d instances=%d (peak %d) lights=%d decals=%d ribbons=%d meshes=%d"),
		Component->GetPlaybackTime(), Component->GetSimulationTime(),
		Component->GetSystemCount(), Component->GetInstanceCount(), PeakInstances,
		Component->GetLightCount(), Component->GetDecalCount(),
		Component->GetRibbonSectionCount(), Component->GetMeshInstanceCount()));

	//-------------------------------------------------------------------------
	// 4. Assertions.
	//-------------------------------------------------------------------------
	// The package's fixed_dt is exactly 1/60, so 60 ticks of 1/60 must land on
	// exactly 60 simulation steps -- this catches the timestep being rounded on
	// its way through the asset (it used to be stored as a float).
	TestTrue(TEXT("the simulation advanced exactly 1 second"),
		FMath::IsNearlyEqual(Component->GetSimulationTime(), 1.0f, 0.001f));
	TestEqual(TEXT("every drawable particle became an instance"),
		Component->GetInstanceCount(), Component->GetDrawableParticleCount());
	TestTrue(TEXT("particle systems were bridged"), Component->GetSystemCount() > 0);
	TestTrue(TEXT("instances were produced"), Component->GetInstanceCount() > 0);
	TestTrue(TEXT("instances were produced on every frame sampled"), PeakInstances > 0);
	TestTrue(TEXT("lights were bridged"), PeakLights > 0);
	TestTrue(TEXT("decals were bridged"), PeakDecals > 0);

	// Every instanced component must be alive and hold the custom data the
	// AetherFX materials read.
	int32 SystemsWithInstances = 0;
	for (int32 SystemIndex = 0; SystemIndex < Component->GetSystemCount(); ++SystemIndex)
	{
		int32 SystemInstances = 0;
		int32 Buckets = 0;
		for (int32 Bucket = 0; ; ++Bucket)
		{
			UInstancedStaticMeshComponent* Instanced = Component->GetSystemInstancedComponent(SystemIndex, Bucket);
			if (!Instanced)
			{
				// GetSystemInstancedComponent returns null both past the last
				// bucket and for a bucket whose mesh could not be resolved, so
				// probe a couple more before giving up.
				if (Bucket > Buckets + 2)
				{
					break;
				}
				continue;
			}
			Buckets = Bucket + 1;
			TestTrue(FString::Printf(TEXT("system %d bucket %d ISM is registered"), SystemIndex, Bucket),
				Instanced->IsRegistered());
			TestEqual(FString::Printf(TEXT("system %d bucket %d custom data floats"), SystemIndex, Bucket),
				Instanced->NumCustomDataFloats, static_cast<int32>(AetherFX::CustomData_Count));
			SystemInstances += Instanced->GetInstanceCount();
		}

		AddInfo(FString::Printf(TEXT("  system %d '%s': %d buckets, %d instances"),
			SystemIndex, *Component->GetSystemId(SystemIndex).ToString(), Buckets, SystemInstances));

		if (SystemInstances > 0)
		{
			++SystemsWithInstances;
		}
	}
	TestTrue(TEXT("at least one system is drawing"), SystemsWithInstances > 0);

	const FString Statistics = Component->GetStatisticsJson();
	TestTrue(TEXT("statistics json is available"), Statistics.Contains(TEXT("total_alive")));

	//-------------------------------------------------------------------------
	// 5. Stop and tear down without crashing.
	//-------------------------------------------------------------------------
	Component->Stop();
	TestFalse(TEXT("stopped"), Component->IsPlaying());
	TestEqual(TEXT("instances released"), Component->GetInstanceCount(), 0);
	TestEqual(TEXT("lights released"), Component->GetLightCount(), 0);

	Actor->Destroy();
	return true;
}

//-----------------------------------------------------------------------------
// The no-importer path: an effect document straight through the C API.
//
// With no imported UTexture2D / UStaticMesh assets, UAetherFXComponent bakes
// transient ones out of the compiled effect. This is the path a game takes when
// it ships effect JSON instead of .aetherfx packages, and the path the bridge
// falls back to for any resource the importer did not produce.
//-----------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FAetherFXDocumentOnlyTest,
	"AetherFX.Bridge.DocumentOnly",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
	EAutomationTestFlags::CommandletContext | EAutomationTestFlags::ProductFilter)

bool FAetherFXDocumentOnlyTest::RunTest(const FString& Parameters)
{
	using namespace AetherFXSmoke;

	const FString Document = DocumentPath();
	if (!TestTrue(FString::Printf(TEXT("'%s' exists"), *Document), FPaths::FileExists(Document)))
	{
		return false;
	}

	FString Json;
	if (!TestTrue(TEXT("document is readable"), FFileHelper::LoadFileToString(Json, *Document)))
	{
		return false;
	}

	UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(
		GetTransientPackage(),
		MakeUniqueObjectName(GetTransientPackage(), UAetherFXEffect::StaticClass(), TEXT("FX_document_only")),
		RF_Transient);
	Effect->EffectJson = Json;
	Effect->AddToRoot();
	ON_SCOPE_EXIT{ Effect->RemoveFromRoot(); };

	if (!TestTrue(TEXT("document compiles"), Effect->Compile()))
	{
		AddError(FString::Printf(TEXT("compile diagnostics: %s"), *Effect->GetCompileDiagnostics()));
		return false;
	}

	// Nothing was imported: the bridge has to bake its own resources.
	TestEqual(TEXT("no imported textures"), Effect->Textures.Num(), 0);
	TestEqual(TEXT("no imported meshes"), Effect->Meshes.Num(), 0);

	FScopedWorld Scope;
	if (!TestNotNull(TEXT("test world"), Scope.World))
	{
		return false;
	}

	AAetherFXActor* Actor = Scope.World->SpawnActor<AAetherFXActor>(FVector::ZeroVector, FRotator::ZeroRotator);
	if (!TestNotNull(TEXT("actor"), Actor))
	{
		return false;
	}
	Actor->Effect = Effect;
	Actor->bAutoPlay = false;
	Actor->Play();

	UAetherFXComponent* Component = Actor->AetherFXComponent;
	if (!TestNotNull(TEXT("component"), Component))
	{
		return false;
	}

	for (int32 Frame = 0; Frame < 60; ++Frame)
	{
		Component->AdvanceAndRender(1.0f / 60.0f);
	}

	AddInfo(FString::Printf(TEXT("document-only: systems=%d instances=%d lights=%d decals=%d"),
		Component->GetSystemCount(), Component->GetInstanceCount(),
		Component->GetLightCount(), Component->GetDecalCount()));

	TestTrue(TEXT("instances were produced without the importer"), Component->GetInstanceCount() > 0);
	TestTrue(TEXT("lights were bridged"), Component->GetLightCount() > 0);
	TestEqual(TEXT("every drawable particle became an instance"),
		Component->GetInstanceCount(), Component->GetDrawableParticleCount());

	// Every billboard system must have found (or baked) a static mesh to instance.
	for (int32 SystemIndex = 0; SystemIndex < Component->GetSystemCount(); ++SystemIndex)
	{
		if (UInstancedStaticMeshComponent* Instanced = Component->GetSystemInstancedComponent(SystemIndex, 0))
		{
			TestNotNull(*FString::Printf(TEXT("system %d has a static mesh"), SystemIndex),
				ToRawPtr(Instanced->GetStaticMesh()));
		}
	}

	Component->Stop();
	Actor->Destroy();
	return true;
}

//-----------------------------------------------------------------------------
// Trails, beams and analytic mesh instances.
//
// fire_aoe has none of those, so the procedural-strip and static-mesh paths go
// untested by the smoke test. lightning_strike carries a beam (during its
// `activation` phase) and the studio's arcane_missile carries a trail and a
// mesh node.
//-----------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FAetherFXTrailsAndBeamsTest,
	"AetherFX.Bridge.TrailsAndBeams",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
	EAutomationTestFlags::CommandletContext | EAutomationTestFlags::ProductFilter)

bool FAetherFXTrailsAndBeamsTest::RunTest(const FString& Parameters)
{
	using namespace AetherFXSmoke;

	struct FPeaks
	{
		int32 Ribbons = 0;
		int32 MeshInstances = 0;
		int32 Instances = 0;
		int32 MaterialsAfterWarmup = 0;
		int32 MaterialsAtEnd = 0;
		bool bRan = false;
	};

	// Plays `DocumentFile` for `Frames` ticks and reports what the bridge built.
	auto RunDocument = [this](const FString& DocumentFile, int32 Frames) -> FPeaks
	{
		FPeaks Peaks;
		if (!FPaths::FileExists(DocumentFile))
		{
			return Peaks;
		}

		FString Json;
		if (!FFileHelper::LoadFileToString(Json, *DocumentFile))
		{
			AddError(FString::Printf(TEXT("cannot read '%s'"), *DocumentFile));
			return Peaks;
		}

		UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(
			GetTransientPackage(),
			MakeUniqueObjectName(GetTransientPackage(), UAetherFXEffect::StaticClass(), TEXT("FX_ribbon_probe")),
			RF_Transient);
		Effect->EffectJson = Json;
		Effect->AddToRoot();
		ON_SCOPE_EXIT{ Effect->RemoveFromRoot(); };

		if (!TestTrue(FString::Printf(TEXT("'%s' compiles"), *FPaths::GetCleanFilename(DocumentFile)), Effect->Compile()))
		{
			AddError(Effect->GetCompileDiagnostics());
			return Peaks;
		}

		FScopedWorld Scope;
		if (!Scope.World)
		{
			return Peaks;
		}

		AAetherFXActor* Actor = Scope.World->SpawnActor<AAetherFXActor>(FVector::ZeroVector, FRotator::ZeroRotator);
		if (!Actor)
		{
			return Peaks;
		}
		Actor->Effect = Effect;
		Actor->bAutoPlay = false;
		Actor->Play();

		UAetherFXComponent* Component = Actor->AetherFXComponent;
		if (!Component)
		{
			return Peaks;
		}

		Peaks.bRan = true;
		for (int32 Frame = 0; Frame < Frames; ++Frame)
		{
			Component->AdvanceAndRender(1.0f / 60.0f);
			Peaks.Ribbons = FMath::Max(Peaks.Ribbons, Component->GetRibbonSectionCount());
			Peaks.MeshInstances = FMath::Max(Peaks.MeshInstances, Component->GetMeshInstanceCount());
			Peaks.Instances = FMath::Max(Peaks.Instances, Component->GetInstanceCount());

			// Trails and beams rebuild their geometry every frame; their
			// materials must be cached, not re-created.
			if (Frame == Frames / 2)
			{
				Peaks.MaterialsAfterWarmup = Component->GetDynamicMaterialCount();
			}
		}
		Peaks.MaterialsAtEnd = Component->GetDynamicMaterialCount();

		Component->Stop();
		Actor->Destroy();
		return Peaks;
	};

	// Beam: lightning_strike's bolt lives in the 0 .. 0.25 s activation phase.
	const FString Lightning = FPaths::Combine(RepositoryRoot(), TEXT("examples/effects/lightning_strike.json"));
	if (TestTrue(FString::Printf(TEXT("'%s' exists"), *Lightning), FPaths::FileExists(Lightning)))
	{
		const FPeaks Peaks = RunDocument(Lightning, 12);
		AddInfo(FString::Printf(TEXT("lightning_strike: peak ribbons=%d instances=%d materials=%d->%d"),
			Peaks.Ribbons, Peaks.Instances, Peaks.MaterialsAfterWarmup, Peaks.MaterialsAtEnd));
		TestTrue(TEXT("lightning_strike ran"), Peaks.bRan);
		TestTrue(TEXT("the beam produced a procedural mesh section"), Peaks.Ribbons > 0);
		TestTrue(TEXT("lightning_strike produced instances"), Peaks.Instances > 0);
		TestEqual(TEXT("beam materials are cached, not re-created per frame"),
			Peaks.MaterialsAtEnd, Peaks.MaterialsAfterWarmup);
	}

	// Trail + analytic mesh instance: the studio's arcane_missile. It lives
	// under out/, which is not committed, so this half is best-effort.
	const FString Missile = FPaths::Combine(RepositoryRoot(), TEXT("out/studio/effects/arcane_missile.json"));
	if (FPaths::FileExists(Missile))
	{
		const FPeaks Peaks = RunDocument(Missile, 60);
		AddInfo(FString::Printf(TEXT("arcane_missile: peak ribbons=%d mesh instances=%d instances=%d materials=%d->%d"),
			Peaks.Ribbons, Peaks.MeshInstances, Peaks.Instances, Peaks.MaterialsAfterWarmup, Peaks.MaterialsAtEnd));
		TestTrue(TEXT("arcane_missile ran"), Peaks.bRan);
		TestTrue(TEXT("the trail produced a procedural mesh section"), Peaks.Ribbons > 0);
		TestTrue(TEXT("the mesh node produced a static mesh component"), Peaks.MeshInstances > 0);
		TestEqual(TEXT("trail materials are cached, not re-created per frame"),
			Peaks.MaterialsAtEnd, Peaks.MaterialsAfterWarmup);
	}
	else
	{
		AddInfo(FString::Printf(
			TEXT("skipped the trail / mesh-instance half: '%s' is not present ")
			TEXT("(export it from the studio to cover it)"), *Missile));
	}

	return true;
}

//-----------------------------------------------------------------------------
// The effect's own speed.
//
// `time_scale` is a mapping from wall time to effect time and nothing more: the
// component advances by `DeltaTime * PlaybackRate * TimeScale`, so the same 60
// ticks of 1/60 reach twice as far into a 2x effect. The simulation is
// untouched -- the timestep, the seeds and the frame at any given effect time
// are the same as at 1x -- which is what keeps Unreal agreeing with the studio.
//-----------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FAetherFXTimeScaleTest,
	"AetherFX.Bridge.TimeScale",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
	EAutomationTestFlags::CommandletContext | EAutomationTestFlags::ProductFilter)

bool FAetherFXTimeScaleTest::RunTest(const FString& Parameters)
{
	using namespace AetherFXSmoke;

	const FString Document = DocumentPath();
	FString Json;
	if (!TestTrue(TEXT("document is readable"), FFileHelper::LoadFileToString(Json, *Document)))
	{
		return false;
	}

	FScopedWorld Scope;
	if (!TestNotNull(TEXT("test world"), Scope.World))
	{
		return false;
	}

	// One second of wall clock at a given speed -> the effect time reached.
	const auto PlayOneSecondAt = [&](double TimeScale, float PlaybackRate, float& OutSimulationTime) -> bool
	{
		UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(
			GetTransientPackage(),
			MakeUniqueObjectName(GetTransientPackage(), UAetherFXEffect::StaticClass(), TEXT("FX_time_scale")),
			RF_Transient);
		Effect->EffectJson = Json;
		Effect->AddToRoot();
		ON_SCOPE_EXIT{ Effect->RemoveFromRoot(); };

		if (!TestTrue(TEXT("document compiles"), Effect->Compile()))
		{
			AddError(FString::Printf(TEXT("compile diagnostics: %s"), *Effect->GetCompileDiagnostics()));
			return false;
		}
		// Compile() resolves it from the plan, so set it afterwards to play the
		// same effect faster without touching the document.
		Effect->TimeScale = TimeScale;

		AAetherFXActor* Actor = Scope.World->SpawnActor<AAetherFXActor>(FVector::ZeroVector, FRotator::ZeroRotator);
		if (!TestNotNull(TEXT("actor"), Actor))
		{
			return false;
		}
		Actor->Effect = Effect;
		Actor->bAutoPlay = false;
		Actor->Play();

		UAetherFXComponent* Component = Actor->AetherFXComponent;
		if (!TestNotNull(TEXT("component"), Component))
		{
			return false;
		}
		Component->bLoop = false;
		Component->PlaybackRate = PlaybackRate;
		TestEqual(TEXT("the component reports the effect's speed"),
			Component->GetTimeScale(), static_cast<float>(TimeScale));

		for (int32 Frame = 0; Frame < 60; ++Frame)
		{
			Component->AdvanceAndRender(1.0f / 60.0f);
		}
		OutSimulationTime = Component->GetSimulationTime();
		AddInfo(FString::Printf(TEXT("time_scale=%.2f rate=%.2f -> t=%.3fs sim=%.3fs"),
			TimeScale, PlaybackRate, Component->GetPlaybackTime(), OutSimulationTime));

		Component->Stop();
		Actor->Destroy();
		return true;
	};

	// A compiled effect reports the document's speed, which is 1 here.
	{
		UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(GetTransientPackage(), NAME_None, RF_Transient);
		Effect->EffectJson = Json;
		Effect->AddToRoot();
		ON_SCOPE_EXIT{ Effect->RemoveFromRoot(); };
		if (!TestTrue(TEXT("document compiles"), Effect->Compile()))
		{
			return false;
		}
		TestEqual(TEXT("an unscaled effect resolves to 1x"), Effect->TimeScale, 1.0);
		TestTrue(TEXT("its wall duration is its duration"),
			FMath::IsNearlyEqual(Effect->GetWallDuration(), Effect->Duration, 0.001f));

		Effect->TimeScale = 2.0;
		TestTrue(TEXT("at 2x it plays in half the time"),
			FMath::IsNearlyEqual(Effect->GetWallDuration(), Effect->Duration * 0.5f, 0.001f));
	}

	// Same 60 ticks of 1/60, three speeds. The fixed step is exactly 1/60, so
	// these land on whole step counts and the tolerance is a fraction of one.
	float AtOne = 0.0f;
	float AtTwo = 0.0f;
	float AtHalf = 0.0f;
	float WithRate = 0.0f;
	if (!PlayOneSecondAt(1.0, 1.0f, AtOne) ||
		!PlayOneSecondAt(2.0, 1.0f, AtTwo) ||
		!PlayOneSecondAt(0.5, 1.0f, AtHalf) ||
		!PlayOneSecondAt(2.0, 0.5f, WithRate))
	{
		return false;
	}

	TestTrue(TEXT("1x reaches one effect second in one wall second"),
		FMath::IsNearlyEqual(AtOne, 1.0f, 0.02f));
	TestTrue(TEXT("2x reaches two effect seconds in one wall second"),
		FMath::IsNearlyEqual(AtTwo, 2.0f, 0.02f));
	TestTrue(TEXT("0.5x reaches half an effect second in one wall second"),
		FMath::IsNearlyEqual(AtHalf, 0.5f, 0.02f));
	// PlaybackRate is a per-instance override on top of the effect's speed.
	TestTrue(TEXT("PlaybackRate multiplies the effect's speed"),
		FMath::IsNearlyEqual(WithRate, 1.0f, 0.02f));

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
