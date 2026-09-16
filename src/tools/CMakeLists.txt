file(GLOB_RECURSE AETHER_tools_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(tools SOURCES ${AETHER_tools_SOURCES} DEPS aether::core aether::compiler aether::sim aether::render aether::procedural)
