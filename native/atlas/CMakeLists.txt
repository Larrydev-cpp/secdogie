cmake_minimum_required(VERSION 3.16)
project(secdogie_atlas LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(secdogie_atlas STATIC
  src/readonly_handle.cpp
  src/unique_handle.cpp
  src/privilege_manager.cpp
  src/token_wall.cpp
  src/process_perception.cpp
  src/hybrid_control_loop.cpp
  src/memory_inspector.cpp
  src/hybrid_tree.cpp
)
target_include_directories(secdogie_atlas PUBLIC include)

if (WIN32)
  target_compile_definitions(secdogie_atlas PRIVATE UNICODE _UNICODE)
  target_compile_definitions(secdogie_atlas PUBLIC NOMINMAX)
  target_link_libraries(secdogie_atlas PUBLIC
    ole32 oleaut32 oleacc psapi advapi32 userenv wtsapi32 user32 gdi32 shell32
  )
endif()
if (APPLE)
  target_link_libraries(secdogie_atlas PUBLIC
    "-framework ApplicationServices"
    "-framework CoreFoundation"
  )
endif()

add_executable(atlas_test
  tests/test_atlas.cpp
  tests/test_memory_inspector.cpp
)
target_link_libraries(atlas_test PRIVATE secdogie_atlas)
target_include_directories(atlas_test PRIVATE include)

add_executable(atlas_inspect src/atlas_inspect.cpp)
target_link_libraries(atlas_inspect PRIVATE secdogie_atlas)
target_include_directories(atlas_inspect PRIVATE include)

add_executable(atlas_target src/atlas_target.cpp)

if (WIN32)
  target_link_libraries(atlas_target PRIVATE user32)
  if (MSVC)
    foreach(tgt secdogie_atlas atlas_test atlas_inspect atlas_target)
      target_compile_options(${tgt} PRIVATE /utf-8 /W3 /EHsc)
    endforeach()
  endif()
endif()

enable_testing()
add_test(NAME atlas_test COMMAND atlas_test)
