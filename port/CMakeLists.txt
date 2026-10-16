# Dreamcast port kit: one game's native port, 32-bit x86 Windows (MSVC). Configure through tools/build.py, which first
# runs tools/gen_port.py (game.h, stubs, dispatch table and the per-game files from game.json) into PORT_GEN.
cmake_minimum_required(VERSION 3.24)
project(DreamcastPort C)

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
  message(FATAL_ERROR "The port is 32-bit x86 for now (guest pointers are host pointers): use the x86 toolchain.")
endif()

set(PORT_GEN "" CACHE PATH "the game's tools/gen_port.py output folder (<game>/build/gen)")
if(NOT EXISTS "${PORT_GEN}/port_files.cmake")
  message(FATAL_ERROR "PORT_GEN (${PORT_GEN}) has no port_files.cmake: run tools/gen_port.py first")
endif()

# PORT_READABLE (the game's readable C), PORT_READABLE_INCLUDE, PORT_RD_RT (the target's rd_rt.h folder),
# PORT_SDK_SOURCES (the kit's sdk/ modules game.json chose), PORT_GAME_SOURCES (game.json sources)
include("${PORT_GEN}/port_files.cmake")
file(GLOB PORT_GENERATED CONFIGURE_DEPENDS "${PORT_GEN}/stubs_*.c" "${PORT_GEN}/dispatch_*.c")
file(GLOB PORT_RUNTIME CONFIGURE_DEPENDS runtime/*.c)

# The judge's flags for readable C (core/readable_check.py C_FLAGS, /O1): the port compiles exactly what was verified.
set(PORT_C_FLAGS /fp:strict /arch:SSE2 /W4 /WX /O1)
# A checked build (tools/build.py --checked, its own build folder): no optimisation and run-time stack checks, which
# stop the run naming a local that a callee wrote past (an undersized stack buffer).
option(PORT_CHECKED "unoptimised build with /RTC1 stack checks" OFF)
if(PORT_CHECKED)
  set(PORT_C_FLAGS /fp:strict /arch:SSE2 /W4 /WX /Od /RTC1)
endif()

add_library(readable STATIC ${PORT_READABLE})
target_include_directories(readable BEFORE PRIVATE "${PORT_GEN}/include" "${PORT_READABLE_INCLUDE}" "${PORT_RD_RT}")
target_compile_options(readable PRIVATE ${PORT_C_FLAGS} /TC)
set_property(TARGET readable PROPERTY C_STANDARD 11)

add_executable(port ${PORT_RUNTIME} ${PORT_SDK_SOURCES} ${PORT_GAME_SOURCES} ${PORT_GENERATED} "${PORT_GEN}/port_entry.c")
target_include_directories(port BEFORE PRIVATE "${PORT_GEN}/include" "${PORT_READABLE_INCLUDE}" runtime "${PORT_RD_RT}")
target_compile_options(port PRIVATE ${PORT_C_FLAGS} /TC)
set_property(TARGET port PROPERTY C_STANDARD 11)
target_link_libraries(port PRIVATE readable legacy_stdio_definitions)
# /BASE keeps the executable clear of guest RAM and its mirrors (runtime/guest_mem.c), which are mapped at fixed
# addresses (0x0c000000, 0x8c000000, 0xac000000).
target_link_options(port PRIVATE /WX /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /BASE:0x5a000000
                    /MAP:${CMAKE_CURRENT_BINARY_DIR}/port.map /STACK:8388608)
