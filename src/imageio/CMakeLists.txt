file(GLOB_RECURSE AETHER_imageio_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(imageio SOURCES ${AETHER_imageio_SOURCES} DEPS aether::core stb::stb tinyexr::tinyexr)
