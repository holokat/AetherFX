# wgpu-native bootstrap. Prebuilt binaries are downloaded by cmake/WgpuNative.cmake.
include(WgpuNative)
file(GLOB_RECURSE AETHER_gpu_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(gpu SOURCES ${AETHER_gpu_SOURCES} DEPS aether::core wgpu::native)
