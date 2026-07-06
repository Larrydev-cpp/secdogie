cmake_minimum_required(VERSION 3.10)
project(secdogie_tunnel C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

add_compile_options(-Wall -Wextra -Wno-unused-parameter)

find_package(PkgConfig REQUIRED)
pkg_check_modules(SODIUM REQUIRED libsodium)

add_executable(secdogie-tunnel
    src/main.c
    src/crypto.c
    src/handshake.c
    src/data.c
    src/tun.c
    src/net.c
    src/config.c
    src/util.c
)

target_include_directories(secdogie-tunnel PRIVATE include ${SODIUM_INCLUDE_DIRS})
target_link_libraries(secdogie-tunnel PRIVATE ${SODIUM_LIBRARIES})
target_compile_options(secdogie-tunnel PRIVATE ${SODIUM_CFLAGS_OTHER})

enable_testing()
add_executable(test_protocol
    tests/test_protocol.c
    src/crypto.c
    src/handshake.c
    src/data.c
    src/util.c
)
target_include_directories(test_protocol PRIVATE include ${SODIUM_INCLUDE_DIRS})
target_link_libraries(test_protocol PRIVATE ${SODIUM_LIBRARIES})
target_compile_options(test_protocol PRIVATE ${SODIUM_CFLAGS_OTHER})
add_test(NAME protocol COMMAND test_protocol)
