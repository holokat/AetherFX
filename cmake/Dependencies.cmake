# All third-party dependencies, pinned. See docs/DEPENDENCIES.md for licenses.
include(FetchContent)
set(FETCHCONTENT_QUIET OFF)
set(FETCHCONTENT_BASE_DIR "${CMAKE_SOURCE_DIR}/third_party/_fetched" CACHE PATH "" FORCE)

# --- nlohmann/json v3.12.0 (MIT) ---------------------------------------------
FetchContent_Declare(nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz
  URL_HASH SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
set(JSON_BuildTests OFF CACHE INTERNAL "")
set(JSON_Install OFF CACHE INTERNAL "")
FetchContent_MakeAvailable(nlohmann_json)

# --- stb (MIT / Unlicense) pinned 2026-08-02 ---------------------------------
FetchContent_Declare(stb
  GIT_REPOSITORY https://github.com/nothings/stb.git
  GIT_TAG 2c980bb59875b0d32144a71867fbdebb2f77cd20
  GIT_SHALLOW FALSE
  SOURCE_SUBDIR cmake_disabled)
FetchContent_MakeAvailable(stb)
add_library(stb INTERFACE)
target_include_directories(stb INTERFACE "${stb_SOURCE_DIR}")
add_library(stb::stb ALIAS stb)

# --- tinyexr v3.2.0 (BSD-3-Clause, bundles miniz MIT) ------------------------
FetchContent_Declare(tinyexr
  GIT_REPOSITORY https://github.com/syoyo/tinyexr.git
  GIT_TAG 6f470c9ab24bf3992bc512ce07e8ecb00d9bf105
  GIT_SHALLOW FALSE
  SOURCE_SUBDIR cmake_disabled)
FetchContent_MakeAvailable(tinyexr)
add_library(tinyexr STATIC "${tinyexr_SOURCE_DIR}/deps/miniz/miniz.c")
target_include_directories(tinyexr PUBLIC "${tinyexr_SOURCE_DIR}" "${tinyexr_SOURCE_DIR}/deps/miniz")
target_compile_definitions(tinyexr PUBLIC TINYEXR_USE_MINIZ=1 TINYEXR_USE_STB_ZLIB=0)
set_target_properties(tinyexr PROPERTIES FOLDER "third_party")
add_library(tinyexr::tinyexr ALIAS tinyexr)

# --- Catch2 v3.16.0 (BSL-1.0), tests only ------------------------------------
if(AETHER_BUILD_TESTS)
  FetchContent_Declare(Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.16.0
    GIT_SHALLOW TRUE)
  FetchContent_MakeAvailable(Catch2)
  list(APPEND CMAKE_MODULE_PATH "${Catch2_SOURCE_DIR}/extras")
  set(CMAKE_MODULE_PATH "${CMAKE_MODULE_PATH}" PARENT_SCOPE)
endif()
