file(GLOB_RECURSE AETHER_compiler_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(compiler SOURCES ${AETHER_compiler_SOURCES} DEPS aether::core aether::procedural)
