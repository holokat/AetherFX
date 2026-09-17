# One test executable per module: tests/<module>/*.cpp -> aether_<module>_tests,
# linking only that module (and transitively its dependencies). This lets
# modules be developed and tested independently.
include(Catch)
foreach(module core imageio procedural compiler sim render tools capi)
  file(GLOB _srcs CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/${module}/*.cpp")
  if(_srcs)
    set(_target aether_${module}_tests)
    add_executable(${_target} ${_srcs})
    target_link_libraries(${_target} PRIVATE aether::${module} Catch2::Catch2WithMain)
    target_compile_definitions(${_target} PRIVATE
      AETHER_SOURCE_DIR="${CMAKE_SOURCE_DIR}"
      AETHER_TEST_OUTPUT_DIR="${CMAKE_BINARY_DIR}/test_output/${module}")
    aether_set_warnings(${_target})
    file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/test_output/${module}")
    catch_discover_tests(${_target} TEST_PREFIX "${module}.")
  endif()
endforeach()
# The C ABI is also driven from pure C: it proves the public header is valid C99
# and that the shared library alone (no C++ headers, no static engine modules)
# is enough to build against.
if(TARGET aether::capi)
  file(GLOB _capi_c_srcs CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/capi/*.c")
  if(_capi_c_srcs)
    add_executable(aether_capi_c_smoke ${_capi_c_srcs})
    target_link_libraries(aether_capi_c_smoke PRIVATE aether::capi)
    target_compile_definitions(aether_capi_c_smoke PRIVATE AETHER_SOURCE_DIR="${CMAKE_SOURCE_DIR}")
    set_target_properties(aether_capi_c_smoke PROPERTIES
      C_STANDARD 99 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
    aether_set_warnings(aether_capi_c_smoke)
    add_test(NAME capi.c_smoke COMMAND aether_capi_c_smoke)
  endif()
endif()
# Integration tests (whole pipeline) link the tools module.
file(GLOB _int_srcs CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/integration/*.cpp")
if(_int_srcs)
  add_executable(aether_integration_tests ${_int_srcs})
  target_link_libraries(aether_integration_tests PRIVATE aether::tools Catch2::Catch2WithMain)
  target_compile_definitions(aether_integration_tests PRIVATE
    AETHER_SOURCE_DIR="${CMAKE_SOURCE_DIR}"
    AETHER_TEST_OUTPUT_DIR="${CMAKE_BINARY_DIR}/test_output/integration")
  aether_set_warnings(aether_integration_tests)
  file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/test_output/integration")
  catch_discover_tests(aether_integration_tests TEST_PREFIX "integration.")
endif()
if(AETHER_WITH_GPU)
  file(GLOB _gpu_srcs CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/gpu/*.cpp")
  if(_gpu_srcs)
    add_executable(aether_gpu_tests ${_gpu_srcs})
    target_link_libraries(aether_gpu_tests PRIVATE aether::gpu Catch2::Catch2WithMain)
    aether_set_warnings(aether_gpu_tests)
    catch_discover_tests(aether_gpu_tests TEST_PREFIX "gpu.")
  endif()
endif()
