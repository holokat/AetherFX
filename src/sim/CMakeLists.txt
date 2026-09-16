file(GLOB_RECURSE AETHER_sim_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(sim SOURCES ${AETHER_sim_SOURCES} DEPS aether::core aether::compiler)
