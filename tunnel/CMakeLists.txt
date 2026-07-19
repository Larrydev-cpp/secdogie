cmake_minimum_required(VERSION 3.10)
project(secdogie_tunnel C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

add_compile_options(-Wall -Wextra -Wno-unused-parameter)

# Opt-in AddressSanitizer + UBSan + LeakSanitizer for the whole build. Used to
# prove the packet parsers are memory-safe under adversarial input:
#   cmake -S . -B build-asan -DSDTP_SANITIZE=ON && cmake --build build-asan && ctest --test-dir build-asan
option(SDTP_SANITIZE "Build with Address/UB/Leak sanitizers" OFF)
if(SDTP_SANITIZE)
    add_compile_options(-fsanitize=address,undefined,leak -fno-sanitize-recover=all -g)
    add_link_options(-fsanitize=address,undefined,leak)
endif()

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
    src/hub.c
    src/hub_route.c
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
    src/hub_route.c
    src/net.c
    src/util.c
)
target_include_directories(test_protocol PRIVATE include ${SODIUM_INCLUDE_DIRS})
target_link_libraries(test_protocol PRIVATE ${SODIUM_LIBRARIES})
target_compile_options(test_protocol PRIVATE ${SODIUM_CFLAGS_OTHER})
add_test(NAME protocol COMMAND test_protocol)

# Receive-batching micro-benchmark (recvfrom-per-packet vs recvmmsg-per-batch).
# Runs a modest count as a plain ctest so it stays built + working; it reports
# numbers rather than asserting timing (which is machine-dependent).
add_executable(bench_recv
    tests/bench_recv.c
    src/net.c
)
target_include_directories(bench_recv PRIVATE include ${SODIUM_INCLUDE_DIRS})
add_test(NAME bench_recv COMMAND bench_recv 2000)

# Adversarial-packet fuzz harness. Runs a modest count as a plain ctest; run it
# with a large count under -DSDTP_SANITIZE=ON for the real memory-safety proof.
add_executable(fuzz_packets
    tests/fuzz_packets.c
    src/crypto.c
    src/handshake.c
    src/data.c
    src/hub_route.c
    src/util.c
)
target_include_directories(fuzz_packets PRIVATE include ${SODIUM_INCLUDE_DIRS})
target_link_libraries(fuzz_packets PRIVATE ${SODIUM_LIBRARIES})
target_compile_options(fuzz_packets PRIVATE ${SODIUM_CFLAGS_OTHER})
add_test(NAME fuzz COMMAND fuzz_packets 50000)
