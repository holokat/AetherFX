// Copyright AetherFX.
//
// Verifies the AetherFX -> Unreal unit and coordinate mapping documented in
// AetherFXCoordinates.h, twice over:
//
//   1. as pure arithmetic on the conversion helpers, and
//   2. end to end, by compiling a two-system probe effect through libaetherfx,
//      playing it with a real UAetherFXComponent and reading the instance
//      transforms back out of the instanced-mesh components.
//
// The probe effect bursts four particles straight along AetherFX +x and four
// straight along AetherFX +y at 1 m/s with no forces, so after one simulated
// second the first group must sit at Unreal +X * 100 and the second at
// Unreal +Z * 100.

#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "AetherFXActor.h"
#include "AetherFXComponent.h"
#include "AetherFXCoordinates.h"
#include "AetherFXEffect.h"

#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "UObject/Package.h"

namespace AetherFXTestUtil
{
	/** Four particles along +x, four along +y, 1 m/s, no forces, 5 s lifetime. */
	static const TCHAR* AxisProbeJson = TEXT(R"JSON(
{
  "schema_version": "0.1.0",
  "name": "Axis Probe",
  "duration": 2.0,
  "seed": 1,
  "nodes": [
    { "id": "ps_x", "type": "particle_system",
      "parameters": { "lifetime": 5.0, "size": 0.1, "max_particles": 16, "drag": 0.0,
                      "render_mode": "billboard",
                      "opacity_over_life": [[0.0, 1.0], [1.0, 1.0]],
                      "size_over_life": [[0.0, 1.0], [1.0, 1.0]] } },
    { "id": "em_x", "type": "emitter", "inputs": { "particle": "ps_x" },
      "parameters": { "shape": "point", "rate": 0.0, "burst_count": 4, "burst_times": [0.0],
                      "velocity": 1.0, "velocity_variance": 0.0,
                      "direction": [1.0, 0.0, 0.0], "spread": 0.0 } },
    { "id": "ps_y", "type": "particle_system",
      "parameters": { "lifetime": 5.0, "size": 0.1, "max_particles": 16, "drag": 0.0,
                      "render_mode": "billboard",
                      "opacity_over_life": [[0.0, 1.0], [1.0, 1.0]],
                      "size_over_life": [[0.0, 1.0], [1.0, 1.0]] } },
    { "id": "em_y", "type": "emitter", "inputs": { "particle": "ps_y" },
      "parameters": { "shape": "point", "rate": 0.0, "burst_count": 4, "burst_times": [0.0],
                      "velocity": 1.0, "velocity_variance": 0.0,
                      "direction": [0.0, 1.0, 0.0], "spread": 0.0 } }
  ]
}
)JSON");

	/** A throwaway game world an automation test can spawn actors into. */
	struct FScopedTestWorld
	{
		UWorld* World = nullptr;

		FScopedTestWorld()
		{
			World = UWorld::CreateWorld(EWorldType::Game, /*bInformEngineOfWorld*/ false, TEXT("AetherFXTestWorld"));
			if (World && GEngine)
			{
				FWorldContext& Context = GEngine->CreateNewWorldContext(EWorldType::Game);
				Context.SetCurrentWorld(World);
				World->InitializeActorsForPlay(FURL());
			}
		}

		~FScopedTestWorld()
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

	inline UAetherFXEffect* MakeEffect(const TCHAR* Json, const TCHAR* Name)
	{
		UAetherFXEffect* Effect = NewObject<UAetherFXEffect>(GetTransientPackage(), FName(Name), RF_Transient);
		Effect->EffectJson = Json;
		Effect->FixedTimeStep = 0.0;    // library default, 1/60
		return Effect;
	}
}

//-----------------------------------------------------------------------------
// 1. The conversion helpers, on their own
//-----------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FAetherFXUnitMappingTest,
	"AetherFX.Units.Mapping",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
	EAutomationTestFlags::CommandletContext | EAutomationTestFlags::ProductFilter)

bool FAetherFXUnitMappingTest::RunTest(const FString& Parameters)
{
	const float Tolerance = 1e-4f;

	// +x (forward, 1 m) -> +X (forward, 100 cm)
	TestEqual(TEXT("+x -> +X"), AetherFX::PositionToUnreal(1.0f, 0.0f, 0.0f), FVector(100.0, 0.0, 0.0), Tolerance);
	// +y (up, 1 m) -> +Z (up, 100 cm)
	TestEqual(TEXT("+y -> +Z"), AetherFX::PositionToUnreal(0.0f, 1.0f, 0.0f), FVector(0.0, 0.0, 100.0), Tolerance);
	// +z -> +Y: the handedness flip (AetherFX's -z forward becomes Unreal's -y left)
	TestEqual(TEXT("+z -> +Y"), AetherFX::PositionToUnreal(0.0f, 0.0f, 1.0f), FVector(0.0, 100.0, 0.0), Tolerance);

	// Directions carry no unit factor.
	TestEqual(TEXT("direction +y -> +Z"), AetherFX::DirectionToUnreal(0.0f, 1.0f, 0.0f), FVector(0.0, 0.0, 1.0), Tolerance);

	// Round trip.
	{
		float Back[3] = { 0.0f, 0.0f, 0.0f };
		AetherFX::PositionToAetherFX(FVector(100.0, 200.0, 300.0), Back);
		TestNearlyEqual(TEXT("round trip x"), Back[0], 1.0f, Tolerance);
		TestNearlyEqual(TEXT("round trip y"), Back[1], 3.0f, Tolerance);   // Unreal Z -> AetherFX y
		TestNearlyEqual(TEXT("round trip z"), Back[2], 2.0f, Tolerance);   // Unreal Y -> AetherFX z
	}

	// A 90 degree rotation about AetherFX +y takes +x to -z. After the mapping,
	// the equivalent Unreal rotation must take +X to -Y.
	{
		const float Root = FMath::Sqrt(0.5f);
		const float Quat[4] = { 0.0f, Root, 0.0f, Root };   // (x, y, z, w)
		const FQuat Converted = AetherFX::OrientationToUnreal(Quat);
		const FVector Rotated = Converted.RotateVector(FVector::ForwardVector);
		TestEqual(TEXT("rot about +y by 90deg: +X -> -Y"), Rotated, FVector(0.0, -1.0, 0.0), 1e-3f);
	}

	// A 90 degree rotation about AetherFX +x takes +y to +z, i.e. Unreal +Z to +Y.
	{
		const float Root = FMath::Sqrt(0.5f);
		const float Quat[4] = { Root, 0.0f, 0.0f, Root };
		const FQuat Converted = AetherFX::OrientationToUnreal(Quat);
		const FVector Rotated = Converted.RotateVector(FVector::UpVector);
		TestEqual(TEXT("rot about +x by 90deg: +Z -> +Y"), Rotated, FVector(0.0, 1.0, 0.0), 1e-3f);
	}

	// Scale swaps axes but keeps its magnitude.
	{
		const float Scale[3] = { 1.0f, 2.0f, 3.0f };
		TestEqual(TEXT("scale swap"), AetherFX::Scale3ToUnreal(Scale), FVector(1.0, 3.0, 2.0), Tolerance);
	}

	// Column-major identity with a translation of (1, 2, 3) metres.
	{
		float Matrix[16] = {
			1, 0, 0, 0,
			0, 1, 0, 0,
			0, 0, 1, 0,
			1, 2, 3, 1,
		};
		const FMatrix Converted = AetherFX::TransformToUnreal(Matrix);
		TestEqual(TEXT("matrix origin"), Converted.GetOrigin(), FVector(100.0, 300.0, 200.0), 1e-3f);
		TestEqual(TEXT("matrix X axis"), Converted.GetScaledAxis(EAxis::X), FVector(1.0, 0.0, 0.0), 1e-4f);
		TestEqual(TEXT("matrix Y axis"), Converted.GetScaledAxis(EAxis::Y), FVector(0.0, 1.0, 0.0), 1e-4f);
		TestEqual(TEXT("matrix Z axis"), Converted.GetScaledAxis(EAxis::Z), FVector(0.0, 0.0, 1.0), 1e-4f);
	}

	TestNearlyEqual(TEXT("length"), static_cast<float>(AetherFX::LengthToUnreal(2.5f)), 250.0f, Tolerance);

	return true;
}

//-----------------------------------------------------------------------------
// 2. End to end, through a real component
//-----------------------------------------------------------------------------

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FAetherFXAxisProbeTest,
	"AetherFX.Units.AxisProbe",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext |
	EAutomationTestFlags::CommandletContext | EAutomationTestFlags::ProductFilter)

bool FAetherFXAxisProbeTest::RunTest(const FString& Parameters)
{
	AetherFXTestUtil::FScopedTestWorld Scope;
	if (!TestNotNull(TEXT("test world"), Scope.World))
	{
		return false;
	}

	UAetherFXEffect* Effect = AetherFXTestUtil::MakeEffect(AetherFXTestUtil::AxisProbeJson, TEXT("AetherFX_AxisProbe"));
	Effect->AddToRoot();
	ON_SCOPE_EXIT{ Effect->RemoveFromRoot(); };

	if (!TestTrue(TEXT("probe effect compiles"), Effect->Compile()))
	{
		AddError(FString::Printf(TEXT("diagnostics: %s"), *Effect->GetCompileDiagnostics()));
		return false;
	}

	AAetherFXActor* Actor = Scope.World->SpawnActor<AAetherFXActor>(FVector::ZeroVector, FRotator::ZeroRotator);
	if (!TestNotNull(TEXT("actor spawned"), Actor))
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

	// 60 steps of 1/60 s = one simulated second.
	for (int32 Frame = 0; Frame < 60; ++Frame)
	{
		Component->AdvanceAndRender(1.0f / 60.0f);
	}

	TestEqual(TEXT("two particle systems"), Component->GetSystemCount(), 2);
	TestTrue(TEXT("instances were produced"), Component->GetInstanceCount() > 0);

	bool bSawX = false;
	bool bSawY = false;

	for (int32 SystemIndex = 0; SystemIndex < Component->GetSystemCount(); ++SystemIndex)
	{
		const FName SystemId = Component->GetSystemId(SystemIndex);
		UInstancedStaticMeshComponent* Instanced = Component->GetSystemInstancedComponent(SystemIndex, 0);
		if (!Instanced || Instanced->GetInstanceCount() == 0)
		{
			continue;
		}

		FTransform InstanceTransform;
		if (!Instanced->GetInstanceTransform(0, InstanceTransform, /*bWorldSpace*/ false))
		{
			AddError(FString::Printf(TEXT("could not read instance 0 of '%s'"), *SystemId.ToString()));
			continue;
		}

		const FVector Location = InstanceTransform.GetLocation();

		if (SystemId == FName(TEXT("ps_x")))
		{
			bSawX = true;
			// 1 m/s for 1 s along AetherFX +x -> Unreal +X by 100 cm.
			TestEqual(TEXT("+x particle is at Unreal +X"), Location, FVector(100.0, 0.0, 0.0), 1.0f);
			TestTrue(TEXT("+x particle has positive X"), Location.X > 50.0);
			TestTrue(TEXT("+x particle did not drift in Z"), FMath::Abs(Location.Z) < 1.0);
		}
		else if (SystemId == FName(TEXT("ps_y")))
		{
			bSawY = true;
			// 1 m/s for 1 s along AetherFX +y -> Unreal +Z by 100 cm.
			TestEqual(TEXT("+y particle is at Unreal +Z"), Location, FVector(0.0, 0.0, 100.0), 1.0f);
			TestTrue(TEXT("+y particle has positive Z"), Location.Z > 50.0);
			TestTrue(TEXT("+y particle did not drift in X"), FMath::Abs(Location.X) < 1.0);
		}
	}

	TestTrue(TEXT("saw the +x system"), bSawX);
	TestTrue(TEXT("saw the +y system"), bSawY);

	Component->Stop();
	Actor->Destroy();
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
