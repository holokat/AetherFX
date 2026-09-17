// Copyright AetherFX.

#include "AetherFXActor.h"

#include "AetherFXComponent.h"
#include "AetherFXEffect.h"
#include "AetherFXLog.h"

#include "Engine/World.h"

AAetherFXActor::AAetherFXActor()
{
	PrimaryActorTick.bCanEverTick = false;

	AetherFXComponent = CreateDefaultSubobject<UAetherFXComponent>(TEXT("AetherFXComponent"));
	RootComponent = AetherFXComponent;

	// The actor owns the "auto play" decision so a designer sees one flag.
	AetherFXComponent->bAutoPlay = false;
}

void AAetherFXActor::PostInitializeComponents()
{
	Super::PostInitializeComponents();

	if (AetherFXComponent)
	{
		AetherFXComponent->Effect = Effect;
		AetherFXComponent->bLoop = bLoop;
		AetherFXComponent->bDestroyOwnerWhenFinished = bDestroyWhenFinished;
	}
}

void AAetherFXActor::BeginPlay()
{
	Super::BeginPlay();

	if (bAutoPlay)
	{
		Play();
	}
}

void AAetherFXActor::Play()
{
	if (!AetherFXComponent)
	{
		return;
	}
	AetherFXComponent->Effect = Effect;
	AetherFXComponent->bLoop = bLoop;
	AetherFXComponent->bDestroyOwnerWhenFinished = bDestroyWhenFinished;
	AetherFXComponent->Play();
}

void AAetherFXActor::Stop()
{
	if (AetherFXComponent)
	{
		AetherFXComponent->Stop();
	}
}

void AAetherFXActor::CastAt(FVector Location)
{
	CastAtWithRotation(Location, GetActorRotation());
}

void AAetherFXActor::CastAtWithRotation(FVector Location, FRotator Rotation)
{
	SetActorLocationAndRotation(Location, Rotation);
	Play();
}

AAetherFXActor* AAetherFXActor::SpawnEffectAtLocation(
	const UObject* WorldContextObject,
	UAetherFXEffect* InEffect,
	FVector Location,
	FRotator Rotation,
	bool bAutoDestroy)
{
	if (!InEffect)
	{
		UE_LOG(LogAetherFX, Warning, TEXT("SpawnEffectAtLocation called with no effect."));
		return nullptr;
	}

	UWorld* World = GEngine ? GEngine->GetWorldFromContextObject(WorldContextObject, EGetWorldErrorMode::LogAndReturnNull) : nullptr;
	if (!World)
	{
		return nullptr;
	}

	FActorSpawnParameters SpawnParameters;
	SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	SpawnParameters.ObjectFlags |= RF_Transient;

	AAetherFXActor* Actor = World->SpawnActor<AAetherFXActor>(AAetherFXActor::StaticClass(), Location, Rotation, SpawnParameters);
	if (!Actor)
	{
		return nullptr;
	}

	Actor->Effect = InEffect;
	Actor->bAutoPlay = true;
	Actor->bDestroyWhenFinished = bAutoDestroy;
	Actor->Play();
	return Actor;
}
