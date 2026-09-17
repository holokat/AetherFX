// Copyright AetherFX.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "AetherFXActor.generated.h"

class UAetherFXComponent;
class UAetherFXEffect;

/**
 * A placeable/spawnable AetherFX effect.
 *
 * This is the "the character casts the effect" entry point: give the actor an
 * effect asset and call CastAt(Location) from Blueprint or C++, or use the
 * static SpawnEffectAtLocation() helper to spawn and play in one call.
 */
UCLASS(BlueprintType, Blueprintable, ClassGroup = (AetherFX), meta = (DisplayName = "AetherFX Actor"))
class AETHERFXRUNTIME_API AAetherFXActor : public AActor
{
	GENERATED_BODY()

public:
	AAetherFXActor();

	/** The AetherFX effect this actor plays. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	TObjectPtr<UAetherFXEffect> Effect;

	/** Start playing on BeginPlay. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX", meta = (DisplayName = "Auto Play"))
	bool bAutoPlay = true;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	bool bLoop = false;

	/** Destroy this actor once a non-looping effect has finished. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "AetherFX")
	bool bDestroyWhenFinished = false;

	/** The scene component that owns the simulation and every drawn primitive. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "AetherFX")
	TObjectPtr<UAetherFXComponent> AetherFXComponent;

	/** Moves the actor to `Location` and plays the effect from the start. */
	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void CastAt(FVector Location);

	/** Moves and orients the actor, then plays from the start. */
	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void CastAtWithRotation(FVector Location, FRotator Rotation);

	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void Play();

	UFUNCTION(BlueprintCallable, Category = "AetherFX")
	void Stop();

	/**
	 * Spawns an AAetherFXActor at `Location` playing `InEffect`. The usual
	 * one-liner for "cast this spell here".
	 */
	UFUNCTION(BlueprintCallable, Category = "AetherFX", meta = (WorldContext = "WorldContextObject", AdvancedDisplay = "Rotation,bAutoDestroy"))
	static AAetherFXActor* SpawnEffectAtLocation(
		const UObject* WorldContextObject,
		UAetherFXEffect* InEffect,
		FVector Location,
		FRotator Rotation = FRotator::ZeroRotator,
		bool bAutoDestroy = true);

	//~ AActor
	virtual void BeginPlay() override;
	virtual void PostInitializeComponents() override;
};
