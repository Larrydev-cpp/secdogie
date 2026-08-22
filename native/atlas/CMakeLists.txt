cmake_minimum_required(VERSION 3.16)
project(secdogie_atlas LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(secdogie_atlas STATIC
  src/readonly_handle.cpp
  src/privilege_manager.cpp
  src/process_perception.cpp
  src/hybrid_control_loop.cpp
)
target_include_directories(secdogie_atlas PUBLIC include)

if (WIN32)
  target_compile_definitions(secdogie_atlas PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN)
  target_link_libraries(secdogie_atlas PUBLIC
    ole32 oleaut32 oleacc psapi advapi32 userenv wtsapi32 user32 gdi32
  )
endif()

add_executable(atlas_test tests/test_atlas.cpp)
target_link_libraries(atlas_test PRIVATE secdogie_atlas)
target_include_directories(atlas_test PRIVATE include)

enable_testing()
add_test(NAME atlas_test COMMAND atlas_test)
