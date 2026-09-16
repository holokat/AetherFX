# All third-party dependencies, pinned by URL + SHA256. See docs/DEPENDENCIES.md.
# Each build directory keeps its own copy under <build>/_deps so that several
# build trees (and several agents) can configure and build concurrently.
include(FetchContent)
set(FETCHCONTENT_QUIET OFF)

# --- nlohmann/json v3.12.0 (MIT) ---------------------------------------------
FetchContent_Declare(nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz
  URL_HASH SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
set(JSON_BuildTests OFF CACHE INTERNAL "")
set(JSON_Install OFF CACHE INTERNAL "")
FetchContent_MakeAvailable(nlohmann_json)

# --- stb (MIT / Unlicense), commit 2c980bb 2026-08-02 -------------------------
FetchContent_Declare(stb
  URL https://github.com/nothings/stb/archive/2c980bb59875b0d32144a71867fbdebb2f77cd20.tar.gz
  URL_HASH SHA256=9a955b1b49a4410088a2e0ee2a9c057c3c907d0c1d75454144cb980aca0ba515
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  SOURCE_SUBDIR cmake_disabled)
FetchContent_MakeAvailable(stb)
add_library(stb INTERFACE)
target_include_directories(stb INTERFACE "${stb_SOURCE_DIR}")
add_library(stb::stb ALIAS stb)

# --- tinyexr v3.2.0 (BSD-3-Clause, bundles miniz MIT) ------------------------
FetchContent_Declare(tinyexr
  URL https://github.com/syoyo/tinyexr/archive/refs/tags/v3.2.0.tar.gz
  URL_HASH SHA256=df2bd61124a35d8138f8b0bc22418a1d4fe33622c818e0e022f5522afc0821b0
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
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
    URL https://github.com/catchorg/Catch2/archive/refs/tags/v3.16.0.tar.gz
    URL_HASH SHA256=0957cae5821b17ce07f0833aaa52b5137643a8382203221f363a8303c109af34
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
  FetchContent_MakeAvailable(Catch2)
  list(APPEND CMAKE_MODULE_PATH "${Catch2_SOURCE_DIR}/extras")
  set(CMAKE_MODULE_PATH "${CMAKE_MODULE_PATH}" PARENT_SCOPE)
endif()
