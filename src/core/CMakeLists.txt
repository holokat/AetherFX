file(GLOB_RECURSE AETHER_core_SOURCES CONFIGURE_DEPENDS
  ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
aether_add_module(core SOURCES ${AETHER_core_SOURCES} DEPS nlohmann_json::nlohmann_json)
