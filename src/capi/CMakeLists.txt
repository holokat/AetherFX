# The stable C ABI game engines link against.
#
#   aetherfx_c         shared  -> libaetherfx.dylib / .so / .dll  (drop into the
#                               engine's plugin folder; Unreal, Unity, Godot)
#   aetherfx_c_static  static  -> libaetherfx_static.a            (link the whole
#                               engine into a monolithic game binary)
#
# Both build the same translation unit. Visibility is hidden by default and a
# linker export list keeps the dynamic symbol table down to `aetherfx_*`, so the
# C++ modules linked in here (core, compiler, sim) never leak into the host.
set(AETHER_CAPI_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/aetherfx_c.cpp")
set(AETHER_CAPI_HEADER "${CMAKE_CURRENT_SOURCE_DIR}/include/aetherfx/aetherfx.h")

# --- shared -------------------------------------------------------------------
add_library(aetherfx_c SHARED ${AETHER_CAPI_SOURCES} ${AETHER_CAPI_HEADER})
add_library(aether::capi ALIAS aetherfx_c)
target_include_directories(aetherfx_c PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
# PRIVATE: consumers of the shared library see the C header only.
target_link_libraries(aetherfx_c PRIVATE aether::sim)
target_compile_definitions(aetherfx_c PRIVATE AETHERFX_BUILD_DLL)
set_target_properties(aetherfx_c PROPERTIES
  OUTPUT_NAME aetherfx            # libaetherfx.dylib / libaetherfx.so
  C_VISIBILITY_PRESET hidden
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN ON
  FOLDER "aether")
if(WIN32)
  # Keep one file name on every platform: libaetherfx.dll + libaetherfx.lib.
  set_target_properties(aetherfx_c PROPERTIES PREFIX "lib" IMPORT_PREFIX "lib")
endif()
aether_set_warnings(aetherfx_c)

if(APPLE)
  target_link_options(aetherfx_c PRIVATE
    "-Wl,-exported_symbols_list,${CMAKE_CURRENT_SOURCE_DIR}/aetherfx.exported_symbols")
  set_property(TARGET aetherfx_c APPEND PROPERTY LINK_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/aetherfx.exported_symbols")
elseif(UNIX)
  target_link_options(aetherfx_c PRIVATE
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/aetherfx.map")
  set_property(TARGET aetherfx_c APPEND PROPERTY LINK_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/aetherfx.map")
endif()
# __declspec(dllexport) already limits the export set on Windows.

# --- static -------------------------------------------------------------------
add_library(aetherfx_c_static STATIC ${AETHER_CAPI_SOURCES} ${AETHER_CAPI_HEADER})
add_library(aether::capi_static ALIAS aetherfx_c_static)
target_include_directories(aetherfx_c_static PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
# PUBLIC: a static consumer links the engine modules itself.
target_link_libraries(aetherfx_c_static PUBLIC aether::sim)
target_compile_definitions(aetherfx_c_static PUBLIC AETHERFX_STATIC)
set_target_properties(aetherfx_c_static PROPERTIES
  OUTPUT_NAME aetherfx_static     # libaetherfx_static.a, never colliding with the import lib
  FOLDER "aether")
aether_set_warnings(aetherfx_c_static)
