file(GLOB_RECURSE AETHER_procedural_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(procedural SOURCES ${AETHER_procedural_SOURCES} DEPS aether::core)
