#!/usr/bin/env bash
#
# sync_libs.sh -- populate the Unreal plugin's ThirdParty module from the CMake build.
#
# Builds libaetherfx (if it is not built already, or with --rebuild) and copies
#   * the C header      -> Source/ThirdParty/AetherFXLib/include/aetherfx/aetherfx.h
#   * the static libs   -> Source/ThirdParty/AetherFXLib/lib/<Platform>/
#
# The archives are deliberately NOT committed (see .gitignore); run this script
# after every engine change before rebuilding the plugin.
#
# Usage:
#   engines/unreal/scripts/sync_libs.sh [--rebuild] [--preset <name>] [--build-dir <dir>]
#                                      [--osx-deployment-target <version>]
#
# The archives the default preset produces target the host's macOS SDK, which is
# usually newer than Unreal's deployment target (14.0), so the editor link emits
# a harmless "built for newer macOS version" warning per object file. To silence
# it, build a dedicated tree pinned to Unreal's target:
#
#   engines/unreal/scripts/sync_libs.sh --build-dir build-unreal \
#       --osx-deployment-target 14.0 --rebuild
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
PLUGIN_THIRDPARTY="${REPO_ROOT}/engines/unreal/AetherFX/Source/ThirdParty/AetherFXLib"

PRESET="default"
BUILD_DIR=""
FORCE_REBUILD=0
OSX_DEPLOYMENT_TARGET=""

while [[ $# -gt 0 ]]; do
	case "$1" in
		--rebuild)   FORCE_REBUILD=1; shift ;;
		--preset)    PRESET="$2"; shift 2 ;;
		--build-dir) BUILD_DIR="$2"; shift 2 ;;
		--osx-deployment-target) OSX_DEPLOYMENT_TARGET="$2"; shift 2 ;;
		-h|--help)   sed -n '2,23p' "$0"; exit 0 ;;
		*) echo "sync_libs.sh: unknown argument '$1'" >&2; exit 2 ;;
	esac
done

[[ -n "${BUILD_DIR}" ]] || BUILD_DIR="${REPO_ROOT}/build"

case "$(uname -s)" in
	Darwin) UE_PLATFORM="Mac";   LIB_EXT="a" ;;
	Linux)  UE_PLATFORM="Linux"; LIB_EXT="a" ;;
	*) echo "sync_libs.sh: unsupported host $(uname -s); build the Win64 libs with MSVC by hand" >&2; exit 1 ;;
esac

# Archives the plugin links, in dependency order. tinyexr comes from _deps and
# lands either next to the modules or at the top of the build tree depending on
# the CMake version, so it is located rather than assumed.
ARCHIVES=(
	"libaetherfx_static.${LIB_EXT}"
	"libaether_sim.${LIB_EXT}"
	"libaether_compiler.${LIB_EXT}"
	"libaether_procedural.${LIB_EXT}"
	"libaether_imageio.${LIB_EXT}"
	"libaether_core.${LIB_EXT}"
	"libtinyexr.${LIB_EXT}"
)

need_build=0
if [[ "${FORCE_REBUILD}" == "1" ]]; then
	need_build=1
elif [[ ! -d "${BUILD_DIR}" ]]; then
	need_build=1
else
	for archive in "${ARCHIVES[@]}"; do
		if [[ -z "$(find "${BUILD_DIR}" -name "${archive}" -type f -print -quit 2>/dev/null)" ]]; then
			need_build=1
			break
		fi
	done
fi

if [[ "${need_build}" == "1" ]]; then
	echo "==> building libaetherfx (preset '${PRESET}')"
	CONFIGURE_ARGS=(--preset "${PRESET}")
	BUILD_ARGS=(--build --preset "${PRESET}")
	if [[ "${BUILD_DIR}" != "${REPO_ROOT}/build" ]]; then
		CONFIGURE_ARGS+=(-B "${BUILD_DIR}")
		BUILD_ARGS=(--build "${BUILD_DIR}")
	fi
	if [[ -n "${OSX_DEPLOYMENT_TARGET}" ]]; then
		CONFIGURE_ARGS+=(-DCMAKE_OSX_DEPLOYMENT_TARGET="${OSX_DEPLOYMENT_TARGET}")
	fi
	cmake "${CONFIGURE_ARGS[@]}"
	cmake "${BUILD_ARGS[@]}"
else
	echo "==> libaetherfx archives already present in ${BUILD_DIR} (pass --rebuild to force)"
fi

DEST_LIB="${PLUGIN_THIRDPARTY}/lib/${UE_PLATFORM}"
DEST_INCLUDE="${PLUGIN_THIRDPARTY}/include/aetherfx"
mkdir -p "${DEST_LIB}" "${DEST_INCLUDE}"

echo "==> copying header"
cp -f "${REPO_ROOT}/src/capi/include/aetherfx/aetherfx.h" "${DEST_INCLUDE}/aetherfx.h"

echo "==> copying archives to ${DEST_LIB}"
missing=0
for archive in "${ARCHIVES[@]}"; do
	src="$(find "${BUILD_DIR}" -name "${archive}" -type f -print -quit 2>/dev/null || true)"
	if [[ -z "${src}" ]]; then
		echo "    MISSING ${archive}" >&2
		missing=1
		continue
	fi
	cp -f "${src}" "${DEST_LIB}/${archive}"
	printf '    %-28s <- %s\n' "${archive}" "${src#${REPO_ROOT}/}"
done

if [[ "${missing}" != "0" ]]; then
	echo "sync_libs.sh: some archives were not found; run with --rebuild" >&2
	exit 1
fi

echo "==> done. ${UE_PLATFORM} libs are in ${DEST_LIB#${REPO_ROOT}/}"
