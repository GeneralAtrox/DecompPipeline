# Dreamcast port kit: one game's native port, 32-bit x86 Windows (MSVC). Configure through tools/build.py, which first
# runs tools/gen_port.py (game.h, stubs, dispatch table and the per-game files from game.json) into PORT_GEN.
cmake_minimum_required(VERSION 3.24)
project(DreamcastPort C CXX)

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
file(GLOB PORT_GENERATED CONFIGURE_DEPENDS "${PORT_GEN}/stubs_*.c" "${PORT_GEN}/dispatch_*.c"
     "${PORT_GEN}/scene_map.c" "${PORT_GEN}/fsca_table.c")
file(GLOB PORT_RUNTIME CONFIGURE_DEPENDS runtime/*.c runtime/gfx/*.c pvr/*.c)
# The desktop host (C++20): entry, data folder, settings, crash reports. Laid out after Lodoss's port (MIT, the
# user's own); the guest runtime, the renderer and the SDK modules stay C.
file(GLOB PORT_HOST CONFIGURE_DEPENDS host/*.cpp)
# Host libraries from vcpkg (vcpkg.json; tools/build.py configures with its toolchain, x86-windows): SDL3 for the
# window and input. Direct3D 11, DXGI and WIC come with the Windows SDK.
find_package(SDL3 CONFIG REQUIRED)

# The judge's flags for readable C (core/readable_check.py C_FLAGS, /O1): the port compiles exactly what was verified.
set(PORT_C_FLAGS /fp:strict /arch:SSE2 /W4 /WX /O1)
# A checked build (tools/build.py --checked, its own build folder): no optimisation and run-time stack checks, which
# stop the run naming a local that a callee wrote past (an undersized stack buffer).
option(PORT_CHECKED "unoptimised build with /RTC1 stack checks" OFF)
if(PORT_CHECKED)
  set(PORT_C_FLAGS /fp:strict /arch:SSE2 /W4 /WX /Od /RTC1)
endif()
# The host's C++: no guest code, so no judge flags; the same zero-warning bar.
set(PORT_CXX_FLAGS /W4 /WX /permissive- /utf-8 /EHsc /arch:SSE2 /DNOMINMAX /DWIN32_LEAN_AND_MEAN)
if(PORT_CHECKED)
  list(APPEND PORT_CXX_FLAGS /Od /RTC1)
else()
  list(APPEND PORT_CXX_FLAGS /O2)
endif()

# A game before its readable stage (a bring-up) has no readable files yet: every function is a logging stub, and the
# library gets one declaration so that it still has a source (an empty file is warning C4206 at /W4).
if(NOT PORT_READABLE)
  set(PORT_NO_READABLE "${CMAKE_CURRENT_BINARY_DIR}/no_readable.c")
  file(WRITE "${PORT_NO_READABLE}" "/* No readable C yet: every game function is a logging stub. */
typedef int port_no_readable;
")
  set(PORT_READABLE "${PORT_NO_READABLE}")
endif()
add_library(readable STATIC ${PORT_READABLE})
target_include_directories(readable BEFORE PRIVATE "${PORT_GEN}/include" "${PORT_READABLE_INCLUDE}" "${PORT_RD_RT}")
target_compile_options(readable PRIVATE ${PORT_C_FLAGS} /TC)
# game.h, precompiled once: every readable file starts with it, and it is most of each file's compile time. A file
# that starts otherwise, or has game.json wrap defines, compiles without it (gen_port.py no_pch).
option(PORT_PCH "precompile game.h for the readable C" ON)
if(PORT_PCH)
  target_precompile_headers(readable PRIVATE "${PORT_GEN}/include/game.h")
  if(PORT_READABLE_NO_PCH)
    set_source_files_properties(${PORT_READABLE_NO_PCH} PROPERTIES SKIP_PRECOMPILE_HEADERS ON)
  endif()
endif()
# Zeroed stack frames for the readable C (runtime/stack_zero.c): /Gh calls _penter at every function's entry, so a
# local the original leaves partly unset reads 0, not host stack leftovers (the /GS cookie differs run to run): runs
# repeat exactly. Off by default: 0 is not what the console leaves either, and the frames cost about 15% speed.
option(PORT_STACK_ZERO "zero each readable C function's stack frame on entry" OFF)
if(PORT_STACK_ZERO)
  target_compile_options(readable PRIVATE /Gh)
endif()
set_property(TARGET readable PROPERTY C_STANDARD 11)

# The PowerVR renderer's shaders: fxc (Windows SDK) compiles pvr/pvr.hlsl into headers at build time.
find_program(PORT_FXC fxc REQUIRED)
set(PORT_SHADERS "${CMAKE_CURRENT_BINARY_DIR}/shaders")
set(PORT_SHADER_HEADERS "${PORT_SHADERS}/pvr_vs.h" "${PORT_SHADERS}/pvr_ps.h" "${PORT_SHADERS}/pvr_gather.h"
    "${PORT_SHADERS}/pvr_full.h" "${PORT_SHADERS}/pvr_resolve.h" "${PORT_SHADERS}/pvr_shadow.h")
add_custom_command(OUTPUT ${PORT_SHADER_HEADERS}
  COMMAND ${CMAKE_COMMAND} -E make_directory "${PORT_SHADERS}"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T vs_5_0 /E vs_main /Vn g_pvr_vs /Fh "${PORT_SHADERS}/pvr_vs.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_main /Vn g_pvr_ps /Fh "${PORT_SHADERS}/pvr_ps.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_shadow /Vn g_pvr_shadow /Fh "${PORT_SHADERS}/pvr_shadow.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_gather /Vn g_pvr_gather /Fh "${PORT_SHADERS}/pvr_gather.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr_oit.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T vs_5_0 /E vs_full /Vn g_pvr_full /Fh "${PORT_SHADERS}/pvr_full.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr_oit.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_resolve /Vn g_pvr_resolve /Fh "${PORT_SHADERS}/pvr_resolve.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr_oit.hlsl"
  DEPENDS pvr/pvr.hlsl pvr/pvr_oit.hlsl
  COMMENT "fxc pvr/pvr.hlsl pvr/pvr_oit.hlsl")

add_executable(port ${PORT_HOST} ${PORT_RUNTIME} ${PORT_SDK_SOURCES} ${PORT_GAME_SOURCES} ${PORT_GENERATED} "${PORT_GEN}/port_entry.c"
               ${PORT_SHADER_HEADERS})
target_include_directories(port BEFORE PRIVATE "${PORT_GEN}/include" "${PORT_READABLE_INCLUDE}" runtime pvr sdk host
                           "${PORT_RD_RT}" "${PORT_SHADERS}")
target_compile_options(port PRIVATE "$<$<COMPILE_LANGUAGE:C>:${PORT_C_FLAGS};/TC>"
                       "$<$<COMPILE_LANGUAGE:CXX>:${PORT_CXX_FLAGS}>")
if(PORT_CHECKED)
  target_compile_definitions(port PRIVATE PORT_CHECKED=1) # runtime/main.c logs the /RTC failures
endif()
set_property(TARGET port PROPERTY C_STANDARD 11)
set_property(TARGET port PROPERTY CXX_STANDARD 20)
set_property(TARGET port PROPERTY CXX_STANDARD_REQUIRED ON)
target_link_libraries(port PRIVATE readable legacy_stdio_definitions SDL3::SDL3 d3d11 dxgi dxguid windowscodecs ole32 uuid
                      dbghelp comctl32 bcrypt)
# /BASE keeps the executable clear of guest RAM and its mirrors (runtime/guest_mem.c), which are mapped at fixed
# addresses (0x0c000000, 0x8c000000, 0xac000000).
target_link_options(port PRIVATE /WX /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /BASE:0x5a000000
                    /MAP:${CMAKE_CURRENT_BINARY_DIR}/port.map /STACK:8388608)
# The game's overlays (tools/gen_overlays.py from <game>/overlays.json), each compiled as its own library.
include("${PORT_GEN}/overlays/overlays.cmake" OPTIONAL)
