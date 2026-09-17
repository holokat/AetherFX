// Copyright AetherFX.
//
// =============================================================================
// AetherFX <-> Unreal unit and coordinate mapping
// =============================================================================
//
// AetherFX  : metres, seconds, right-handed, **Y up**, -Z forward (the glTF
//             convention). Quaternions are (x, y, z, w). Matrices are
//             column-major with the translation in the last column. Colours are
//             linear. Angles in the frame buffers are radians; fields whose name
//             ends in `_deg` are degrees.
// Unreal    : centimetres, left-handed, **Z up**, +X forward. FQuat is
//             (X, Y, Z, W). FMatrix is row-major (row 0/1/2 are the X/Y/Z basis
//             vectors, row 3 is the translation). FLinearColor is linear.
//
// ---- Positions and directions ------------------------------------------------
//
//     X_ue = x * 100
//     Y_ue = z * 100
//     Z_ue = y * 100
//
// i.e. swap the Y and Z components, then convert metres to centimetres. This is
// the axis map S(x, y, z) = (x, z, y). It has determinant -1, which is exactly
// the handedness flip we need: AetherFX is right-handed, Unreal is left-handed.
//
// Sanity checks (covered by FAetherFXCoordinateMappingTest):
//   * a particle moving +x in AetherFX moves +X in Unreal (forward stays forward)
//   * a particle rising +y in AetherFX rises +Z in Unreal (up stays up)
//   * a particle moving +z in AetherFX moves +Y in Unreal (AetherFX's -Z forward
//     becomes Unreal's -Y, i.e. left; that is the handedness flip)
//
// Directions use the same swap with no 100x factor. Lengths (size, width,
// radius, attenuation) are plain metres and only need the 100x factor.
//
// ---- Orientations ------------------------------------------------------------
//
//     (x, y, z, w)  ->  (x, z, y, -w)
//
// Why: a rotation R in AetherFX becomes S * R * S in Unreal. S * R * S fixes
// S(axis), so it is a rotation about the swapped axis; because S flips
// handedness the sense of the rotation flips too, so the angle becomes -theta.
// With q = (sin(theta/2) * axis, cos(theta/2)) that is
// (-S(axis) * sin(theta/2), cos(theta/2)), and negating the whole quaternion
// (which names the same rotation) gives (S(axis) * sin(theta/2), -cos(theta/2))
// = (x, z, y, -w).
//
// ---- Scale -------------------------------------------------------------------
//
// Per-axis scale multipliers follow the axis swap but keep no unit factor:
//
//     Scale_ue = (sx, sz, sy)
//
// Baked meshes are authored in metres, so this plugin converts their *vertices*
// to centimetres (and swaps their axes) once at import; an instance transform
// then carries only the dimensionless `size * scale3`.
//
// ---- Matrices ----------------------------------------------------------------
//
// A column-major AetherFX matrix with columns (c0, c1, c2, c3) becomes the
// row-major FMatrix
//
//     row 0 (X axis) = S(c0)
//     row 1 (Y axis) = S(c2)      <- Unreal's Y is AetherFX's Z
//     row 2 (Z axis) = S(c1)      <- Unreal's Z is AetherFX's Y
//     row 3 (origin) = S(c3) * 100
//
// ---- Colour ------------------------------------------------------------------
//
// Linear on both sides: copy RGB straight through. AetherFX carries alpha in the
// separate `opacity` buffer, not in `color.a`.
//
// =============================================================================

#pragma once

#include "CoreMinimal.h"
#include "Math/Quat.h"
#include "Math/UnrealMathUtility.h"

namespace AetherFX
{
	/** Metres -> Unreal units. */
	inline constexpr double MetersToUnreal = 100.0;

	/** Unreal units -> metres. */
	inline constexpr double UnrealToMeters = 0.01;

	/** Position in metres (AetherFX, Y up, right-handed) -> FVector in cm (Unreal, Z up, left-handed). */
	FORCEINLINE FVector PositionToUnreal(const float* InXYZ)
	{
		return FVector(
			static_cast<double>(InXYZ[0]) * MetersToUnreal,
			static_cast<double>(InXYZ[2]) * MetersToUnreal,
			static_cast<double>(InXYZ[1]) * MetersToUnreal);
	}

	FORCEINLINE FVector PositionToUnreal(float X, float Y, float Z)
	{
		return FVector(
			static_cast<double>(X) * MetersToUnreal,
			static_cast<double>(Z) * MetersToUnreal,
			static_cast<double>(Y) * MetersToUnreal);
	}

	/** Direction (unitless) -> Unreal. Same axis swap, no unit conversion. */
	FORCEINLINE FVector DirectionToUnreal(const float* InXYZ)
	{
		return FVector(
			static_cast<double>(InXYZ[0]),
			static_cast<double>(InXYZ[2]),
			static_cast<double>(InXYZ[1]));
	}

	FORCEINLINE FVector DirectionToUnreal(float X, float Y, float Z)
	{
		return FVector(static_cast<double>(X), static_cast<double>(Z), static_cast<double>(Y));
	}

	/** Unreal position (cm) -> AetherFX position (m). The inverse of PositionToUnreal. */
	FORCEINLINE void PositionToAetherFX(const FVector& In, float* OutXYZ)
	{
		OutXYZ[0] = static_cast<float>(In.X * UnrealToMeters);
		OutXYZ[1] = static_cast<float>(In.Z * UnrealToMeters);
		OutXYZ[2] = static_cast<float>(In.Y * UnrealToMeters);
	}

	/** Quaternion (x, y, z, w) -> FQuat (x, z, y, -w), normalised. */
	FORCEINLINE FQuat OrientationToUnreal(const float* InXYZW)
	{
		FQuat Result(
			static_cast<double>(InXYZW[0]),
			static_cast<double>(InXYZW[2]),
			static_cast<double>(InXYZW[1]),
			-static_cast<double>(InXYZW[3]));
		if (!Result.IsNormalized())
		{
			const double SizeSq = Result.SizeSquared();
			Result = (SizeSq > UE_SMALL_NUMBER) ? Result.GetNormalized() : FQuat::Identity;
		}
		return Result;
	}

	/** Per-axis scale multipliers (x, y, z) -> (x, z, y). Dimensionless, no 100x. */
	FORCEINLINE FVector Scale3ToUnreal(const float* InXYZ)
	{
		return FVector(
			static_cast<double>(InXYZ[0]),
			static_cast<double>(InXYZ[2]),
			static_cast<double>(InXYZ[1]));
	}

	/** A length in metres -> Unreal units. */
	FORCEINLINE double LengthToUnreal(float Meters)
	{
		return static_cast<double>(Meters) * MetersToUnreal;
	}

	/**
	 * Column-major AetherFX transform (metres) -> FMatrix (Unreal units).
	 * See the header comment for the derivation.
	 */
	FORCEINLINE FMatrix TransformToUnreal(const float* M)
	{
		auto Swap = [M](int32 Column) -> FVector
		{
			const int32 Base = Column * 4;
			return FVector(
				static_cast<double>(M[Base + 0]),
				static_cast<double>(M[Base + 2]),
				static_cast<double>(M[Base + 1]));
		};

		FMatrix Out = FMatrix::Identity;
		Out.SetAxis(0, Swap(0));              // Unreal X  <- AetherFX x
		Out.SetAxis(1, Swap(2));              // Unreal Y  <- AetherFX z
		Out.SetAxis(2, Swap(1));              // Unreal Z  <- AetherFX y
		Out.SetOrigin(Swap(3) * MetersToUnreal);
		return Out;
	}

	/** Linear RGB triple/quad -> FLinearColor. Alpha is supplied separately. */
	FORCEINLINE FLinearColor ColorToUnreal(const float* RGBA, float Alpha = 1.0f)
	{
		return FLinearColor(RGBA[0], RGBA[1], RGBA[2], Alpha);
	}
}
