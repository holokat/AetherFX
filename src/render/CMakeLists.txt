file(GLOB_RECURSE AETHER_render_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(render SOURCES ${AETHER_render_SOURCES} DEPS aether::core aether::imageio)
