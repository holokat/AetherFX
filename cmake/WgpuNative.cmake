# WgpuNative.cmake - prebuilt wgpu-native binaries for the optional GPU module.
#
# Upstream : https://github.com/gfx-rs/wgpu-native
# Version  : v29.0.1.1  (published 2026-06-23; latest stable release as of
#            2026-09-17, verified against the GitHub releases API)
# License  : Apache-2.0 OR MIT. Both license texts ship in the repository; the
#            prebuilt archives contain only headers + libraries, so the NOTICE /
#            license text must be carried by us when we redistribute the binary.
#            See docs/DEPENDENCIES.md.
#
# We consume the *prebuilt* release archives rather than building Rust from
# source: no cargo/rustup in the toolchain, and the archives are reproducible
# and hash-pinned. Each build directory gets its own copy under
# <build>/_deps/wgpu-native so that concurrent build trees never race.
#
# Archive layout (identical on every platform, no top-level directory):
#     include/webgpu/webgpu.h      standard webgpu.h (futures/WGPUStringView era)
#     include/webgpu/wgpu.h        wgpu-native extensions (wgpuDevicePoll, ...)
#     lib/libwgpu_native.a         static library   (wgpu_native.lib on MSVC)
#     lib/libwgpu_native.dylib     shared library   (.so / .dll elsewhere)
#     wgpu-native-meta/webgpu.yml
#     wgpu-native-meta/wgpu-native-git-tag
# We prefer the static library: it removes all rpath/@loader_path handling and
# the binary is not redistributed separately.
#
# ---------------------------------------------------------------------------
# Asset SHA256 table for v29.0.1.1
#
#   wgpu-macos-aarch64-release.zip       a5797a37b1adf720bcd5dcffb291edbbd5b7b14be0a3874c28e6393a655a7a3e  [VERIFIED locally: downloaded + shasum -a 256 on macOS arm64, 2026-09-17]
#   wgpu-macos-x86_64-release.zip        8e2f7378548ddd0e2cf21e7d864dda46e953f0af724855a33778b85ead206d41  [UNVERIFIED: GitHub asset digest, no local download]
#   wgpu-linux-x86_64-release.zip        95a4d90c071005a98d03eab348beaa6b07e16eb00d1dcdb9f8348f75eb97ec5a  [UNVERIFIED: GitHub asset digest, no local download]
#   wgpu-linux-aarch64-release.zip       015fcdf1dbae82e614a783cc38017e5399ae0927a889fe9b69c9b664bc61b47a  [UNVERIFIED: GitHub asset digest, no local download]
#   wgpu-windows-x86_64-msvc-release.zip 7e67d7445c42aeb85e30f88930fd8d7d83ee769e3390aeb1ada75ebf3cf78132  [UNVERIFIED: GitHub asset digest, no local download]
#
# Only the macOS arm64 entry has been downloaded, hashed and link-tested on a
# real machine. The other four come from the `digest` field GitHub publishes for
# each release asset; they are believed correct but nobody has built against
# them yet. The FIRST agent to build on one of those platforms must:
#   1. run the refresh command below,
#   2. confirm the printed hash matches the table,
#   3. link + run tests/gpu/gpu_smoke_test.cpp,
#   4. change that line's marker to [VERIFIED locally: <platform>, <date>] and
#      note any extra system libraries the linker demanded.
# If a hash does NOT match, STOP and investigate - do not "fix" the table.
#
# Refresh / bump procedure (also how to move to a newer wgpu-native):
#   curl -s https://api.github.com/repos/gfx-rs/wgpu-native/releases/latest \
#     | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['tag_name']); \
#       [print(a['name'], a['digest']) for a in r['assets'] if a['name'].endswith('release.zip')]"
# Then update AETHER_WGPU_NATIVE_VERSION, this comment block, the hashes below,
# and the wgpu-native row in docs/DEPENDENCIES.md in the same commit. webgpu.h
# is still an evolving C API - expect source breakage across minor bumps.
# ---------------------------------------------------------------------------

include_guard(GLOBAL)

if(TARGET wgpu::native)
  return()
endif()

set(AETHER_WGPU_NATIVE_VERSION "v29.0.1.1" CACHE STRING
    "wgpu-native prebuilt release tag to download")
set(AETHER_WGPU_NATIVE_URL_BASE
    "https://github.com/gfx-rs/wgpu-native/releases/download/${AETHER_WGPU_NATIVE_VERSION}")

# --- host platform -> release asset ------------------------------------------
# CMAKE_SYSTEM_PROCESSOR spellings vary a lot, so normalise first. On Apple,
# CMAKE_OSX_ARCHITECTURES (when single-valued) wins over the host processor so
# that an explicit cross-compile picks the right archive.
set(_wgpu_arch "${CMAKE_SYSTEM_PROCESSOR}")
if(APPLE AND CMAKE_OSX_ARCHITECTURES)
  list(LENGTH CMAKE_OSX_ARCHITECTURES _wgpu_osx_arch_count)
  if(_wgpu_osx_arch_count GREATER 1)
    message(FATAL_ERROR
      "WgpuNative: universal builds are not supported - the release archives are "
      "single-architecture. Configure with one value in CMAKE_OSX_ARCHITECTURES.")
  endif()
  set(_wgpu_arch "${CMAKE_OSX_ARCHITECTURES}")
endif()
string(TOLOWER "${_wgpu_arch}" _wgpu_arch)
if(_wgpu_arch MATCHES "^(arm64|aarch64)$")
  set(_wgpu_arch "aarch64")
elseif(_wgpu_arch MATCHES "^(x86_64|amd64|x64)$")
  set(_wgpu_arch "x86_64")
endif()

if(APPLE)
  set(_wgpu_asset "wgpu-macos-${_wgpu_arch}-release.zip")
  if(_wgpu_arch STREQUAL "aarch64")
    set(_wgpu_sha256 "a5797a37b1adf720bcd5dcffb291edbbd5b7b14be0a3874c28e6393a655a7a3e")
  elseif(_wgpu_arch STREQUAL "x86_64")
    set(_wgpu_sha256 "8e2f7378548ddd0e2cf21e7d864dda46e953f0af724855a33778b85ead206d41") # UNVERIFIED
  endif()
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(_wgpu_asset "wgpu-linux-${_wgpu_arch}-release.zip")
  if(_wgpu_arch STREQUAL "x86_64")
    set(_wgpu_sha256 "95a4d90c071005a98d03eab348beaa6b07e16eb00d1dcdb9f8348f75eb97ec5a") # UNVERIFIED
  elseif(_wgpu_arch STREQUAL "aarch64")
    set(_wgpu_sha256 "015fcdf1dbae82e614a783cc38017e5399ae0927a889fe9b69c9b664bc61b47a") # UNVERIFIED
  endif()
elseif(WIN32)
  # Only the MSVC x86_64 build is mapped. The MinGW archive
  # (wgpu-windows-x86_64-gnu-release.zip,
  #  d471e3614733c1d4ddd61bfd19868356477d0d37bf531bf8c6cb64a7f579bd2a) and the
  # aarch64/i686 MSVC archives exist upstream; add them here when needed.
  if(_wgpu_arch STREQUAL "x86_64" AND MSVC)
    set(_wgpu_asset "wgpu-windows-x86_64-msvc-release.zip")
    set(_wgpu_sha256 "7e67d7445c42aeb85e30f88930fd8d7d83ee769e3390aeb1ada75ebf3cf78132") # UNVERIFIED
  endif()
endif()

if(NOT _wgpu_asset OR NOT _wgpu_sha256)
  message(FATAL_ERROR
    "WgpuNative: no prebuilt wgpu-native ${AETHER_WGPU_NATIVE_VERSION} archive is "
    "mapped for ${CMAKE_SYSTEM_NAME}/${CMAKE_SYSTEM_PROCESSOR}. Add the mapping and "
    "its SHA256 in cmake/WgpuNative.cmake, or configure with -DAETHER_WITH_GPU=OFF.")
endif()

# --- download + extract -------------------------------------------------------
set(_wgpu_root "${CMAKE_BINARY_DIR}/_deps/wgpu-native")
set(_wgpu_prefix "${_wgpu_root}/${AETHER_WGPU_NATIVE_VERSION}")
set(_wgpu_zip "${_wgpu_root}/${_wgpu_asset}")
set(_wgpu_stamp "${_wgpu_prefix}/.extracted-${_wgpu_sha256}")

if(NOT EXISTS "${_wgpu_stamp}")
  message(STATUS "wgpu-native: fetching ${_wgpu_asset} (${AETHER_WGPU_NATIVE_VERSION})")
  file(DOWNLOAD
    "${AETHER_WGPU_NATIVE_URL_BASE}/${_wgpu_asset}"
    "${_wgpu_zip}"
    EXPECTED_HASH SHA256=${_wgpu_sha256}
    TLS_VERIFY ON
    SHOW_PROGRESS
    STATUS _wgpu_dl_status)
  list(GET _wgpu_dl_status 0 _wgpu_dl_code)
  if(NOT _wgpu_dl_code EQUAL 0)
    list(GET _wgpu_dl_status 1 _wgpu_dl_msg)
    file(REMOVE "${_wgpu_zip}")
    message(FATAL_ERROR "wgpu-native: download of ${_wgpu_asset} failed: ${_wgpu_dl_msg}")
  endif()
  # Extract into a scratch dir and move into place, so an interrupted extract
  # never leaves a half-populated prefix that a later configure would trust.
  file(REMOVE_RECURSE "${_wgpu_prefix}" "${_wgpu_prefix}.tmp")
  file(MAKE_DIRECTORY "${_wgpu_prefix}.tmp")
  file(ARCHIVE_EXTRACT INPUT "${_wgpu_zip}" DESTINATION "${_wgpu_prefix}.tmp")
  file(RENAME "${_wgpu_prefix}.tmp" "${_wgpu_prefix}")
  file(WRITE "${_wgpu_stamp}" "${_wgpu_asset}\n${_wgpu_sha256}\n")
  message(STATUS "wgpu-native: extracted to ${_wgpu_prefix}")
endif()

set(_wgpu_include "${_wgpu_prefix}/include")
if(NOT EXISTS "${_wgpu_include}/webgpu/webgpu.h" OR NOT EXISTS "${_wgpu_include}/webgpu/wgpu.h")
  message(FATAL_ERROR
    "wgpu-native: ${_wgpu_include} does not contain webgpu/webgpu.h + webgpu/wgpu.h. "
    "The release archive layout changed; update cmake/WgpuNative.cmake. "
    "Delete ${_wgpu_root} to force a re-download.")
endif()

# --- pick the library ---------------------------------------------------------
# Static first (no rpath, no install-time copy), shared as a fallback.
set(_wgpu_kind "STATIC")
set(_wgpu_lib "")
foreach(_cand
    "${_wgpu_prefix}/lib/libwgpu_native.a"
    "${_wgpu_prefix}/lib/wgpu_native.lib")
  if(EXISTS "${_cand}")
    set(_wgpu_lib "${_cand}")
    break()
  endif()
endforeach()
if(NOT _wgpu_lib)
  set(_wgpu_kind "SHARED")
  foreach(_cand
      "${_wgpu_prefix}/lib/libwgpu_native.dylib"
      "${_wgpu_prefix}/lib/libwgpu_native.so"
      "${_wgpu_prefix}/lib/wgpu_native.dll")
    if(EXISTS "${_cand}")
      set(_wgpu_lib "${_cand}")
      break()
    endif()
  endforeach()
endif()
if(NOT _wgpu_lib)
  file(GLOB _wgpu_found "${_wgpu_prefix}/lib/*")
  message(FATAL_ERROR
    "wgpu-native: no usable library in ${_wgpu_prefix}/lib (found: ${_wgpu_found}).")
endif()

# --- system libraries the Rust staticlib needs --------------------------------
# The prebuilt .a carries no LC_LINKER_OPTION / .drectve records, so every
# system dependency has to be listed by hand.
#
# macOS, measured against v29.0.1.1 / libwgpu_native.a on macOS 15 arm64 by
# dropping one entry at a time from a real link of aether_gpu_tests:
#   Metal       REQUIRED - _MTLCopyAllDevices etc.
#   Foundation  REQUIRED - _NSKeyValueChangeNewKey etc.; also re-exports libobjc
#                          and CoreFoundation, which is why those two link fine
#                          on their own.
#   QuartzCore, CoreFoundation, IOKit, IOSurface, objc, iconv
#               not demanded today. Kept on the list deliberately: QuartzCore is
#               needed the moment we create a CAMetalLayer surface, IOSurface for
#               shared-texture interop, and the explicit objc/CoreFoundation
#               entries stop a future wgpu bump from breaking the link silently.
#               Cost is a few lazily-resolved dylib references.
# (libwgpu_native.dylib's own otool -L list is: QuartzCore, Metal, Foundation,
#  CoreFoundation, libobjc, libiconv, libSystem.)
set(_wgpu_system_libs "")
if(APPLE)
  foreach(_fw Metal Foundation QuartzCore CoreFoundation IOKit IOSurface)
    find_library(WGPU_FW_${_fw} ${_fw} REQUIRED)
    list(APPEND _wgpu_system_libs "${WGPU_FW_${_fw}}")
    mark_as_advanced(WGPU_FW_${_fw})
  endforeach()
  list(APPEND _wgpu_system_libs objc iconv)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  find_package(Threads REQUIRED)
  list(APPEND _wgpu_system_libs ${CMAKE_DL_LIBS} Threads::Threads m)
elseif(WIN32)
  # Standard Rust std + DX12/Vulkan loader dependency set. Extend if the linker
  # complains; record what you added in the header comment above.
  list(APPEND _wgpu_system_libs
    ws2_32 userenv bcrypt ntdll advapi32 d3dcompiler opengl32 propsys runtimeobject)
endif()

add_library(wgpu::native ${_wgpu_kind} IMPORTED GLOBAL)
set_target_properties(wgpu::native PROPERTIES
  IMPORTED_LOCATION "${_wgpu_lib}"
  INTERFACE_INCLUDE_DIRECTORIES "${_wgpu_include}"
  INTERFACE_LINK_LIBRARIES "${_wgpu_system_libs}")
if(_wgpu_kind STREQUAL "SHARED" AND WIN32)
  set_target_properties(wgpu::native PROPERTIES
    IMPORTED_IMPLIB "${_wgpu_prefix}/lib/wgpu_native.dll.lib")
endif()

# Note: CMake treats INTERFACE_INCLUDE_DIRECTORIES of an IMPORTED target as
# SYSTEM includes by default, so webgpu.h / wgpu.h do not trip our
# -Wall -Wextra -Wpedantic -Wshadow settings. Do not set IMPORTED_NO_SYSTEM.

set(AETHER_WGPU_NATIVE_LIBRARY "${_wgpu_lib}" CACHE INTERNAL "wgpu-native library in use")
set(AETHER_WGPU_NATIVE_INCLUDE_DIR "${_wgpu_include}" CACHE INTERNAL "wgpu-native headers in use")
message(STATUS "wgpu-native ${AETHER_WGPU_NATIVE_VERSION}: ${_wgpu_kind} ${_wgpu_lib}")
