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
file(GLOB PORT_RUNTIME CONFIGURE_DEPENDS runtime/*.c runtime/gfx/*.c runtime/media/*.c pvr/*.c)
# The port base (base/, README.md "The port base"): the window, menu bar, settings, log, session, scene dump, pads and
# clock every console kit stands on. Its C joins the kit's C; its C++20 is the desktop host: entry, data folder,
# settings, crash reports (laid out after Lodoss's port, MIT, the user's own). The guest runtime, the renderer and the
# SDK modules stay C.
file(GLOB PORT_BASE_C CONFIGURE_DEPENDS base/*.c)
list(APPEND PORT_RUNTIME ${PORT_BASE_C})
file(GLOB PORT_HOST CONFIGURE_DEPENDS base/*.cpp base/third_party/*.cpp)
# Host libraries from vcpkg (vcpkg.json; tools/build.py configures with its toolchain, x86-windows): SDL3 for the
# window and input. Direct3D 11, DXGI and WIC come with the Windows SDK.
find_package(SDL3 CONFIG REQUIRED)

# The judge's flags for readable C (core/readable_check.py C_FLAGS, /O1): the port compiles exactly what was verified.
set(PORT_C_BASE /fp:strict /arch:SSE2 /W4 /WX)
set(PORT_C_FLAGS ${PORT_C_BASE} /O1)
# The kit's own C (runtime, renderer, SDK modules, tx_rt.c) is not judged: it gets /O2 (same /fp:strict arithmetic, so the
# same floating-point results), while the readable C, the translator C, the game's own sources and the generated glue
# keep the judge's /O1 exactly. PORT_KIT_O2=OFF puts the kit back on /O1.
option(PORT_KIT_O2 "compile the kit's own C at /O2 (the judged and generated C stay at the judge's /O1)" ON)
set(PORT_KIT_OPT /O1)
if(PORT_KIT_O2)
  set(PORT_KIT_OPT /O2)
endif()
# PORT_GAME_O2: the game's own C (readable, translator, game sources, generated glue) at /O2 too. Off by default: it is not
# the judge's flag, so a game turns it on (game.json game_o2) only with a whole-replay proof that its frames and audio
# are identical (docs/RUNTIME_TARGET.md, "What native code must match"). /fp:strict stays.
option(PORT_GAME_O2 "compile the game's readable and translator C at /O2 instead of the judge's /O1" OFF)
set(PORT_GAME_OPT /O1)
if(PORT_GAME_O2)
  set(PORT_GAME_OPT /O2)
endif()
# A checked build (tools/build.py --checked, its own build folder): no optimisation and run-time stack checks, which
# stop the run naming a local that a callee wrote past (an undersized stack buffer).
option(PORT_CHECKED "unoptimised build with /RTC1 stack checks" OFF)
if(PORT_CHECKED)
  set(PORT_C_BASE /fp:strict /arch:SSE2 /W4 /WX)
  set(PORT_C_FLAGS ${PORT_C_BASE} /Od /RTC1)
  set(PORT_KIT_OPT /Od)
  set(PORT_GAME_OPT /Od /RTC1)
endif()
# AddressSanitizer (tools/build.py --asan, its own folder build/x86asan): the play build's optimisation, with every host
# load and store checked against the bounds of the heap block, stack local or global it belongs to; a bad one is reported
# with its stack (ASAN_OPTIONS=continue_on_error=1 reports each and goes on). Guest RAM is a mapped section, not the heap,
# so the game's own reads and writes of it are never flagged, nor is a guest address past a table's end: only the host's
# own errors. MSVC has no UndefinedBehaviorSanitizer; /fsanitize=address does not combine with /RTC1 (--checked).
option(PORT_ASAN "AddressSanitizer build (/fsanitize=address)" OFF)
if(PORT_ASAN)
  if(PORT_CHECKED)
    message(FATAL_ERROR "PORT_ASAN with PORT_CHECKED: /fsanitize=address does not combine with /RTC1")
  endif()
  add_compile_options(/fsanitize=address /Z7)
  add_link_options(/DEBUG /INCREMENTAL:NO)
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
target_compile_options(readable PRIVATE ${PORT_C_BASE} ${PORT_GAME_OPT} /TC)
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

# Translator C (tools/tx_port.py, game.json "translator_c"): each judged translator file, compiled byte for byte inside
# its glue (tx_ADDR.c) against the judge's own tc_rt.h, with the readable C's flags; runtime/tx/tx_rt.c and tc_call's
# glue join the executable. Without it the build is as before.
if(PORT_TX)
  add_library(txc STATIC ${PORT_TX})
  # runtime/tx/tc_rt_inline.h (forced in) wraps the judge's tc_rt.h (PORT_TC_RT_H) with the hot half of it inline; the judged header is
  # still the one the translated C is written against and stays on the path behind the wrapper.
  target_include_directories(txc BEFORE PRIVATE runtime/tx "${PORT_GEN}/include" "${PORT_TC_RT}")
  target_compile_definitions(txc PRIVATE "PORT_TC_RT_H=\"${PORT_TC_RT}/tc_rt.h\"")
  target_compile_options(txc PRIVATE "/FI${CMAKE_CURRENT_SOURCE_DIR}/runtime/tx/tc_rt_inline.h")
  target_compile_options(txc PRIVATE ${PORT_C_BASE} ${PORT_GAME_OPT} /TC)
  set_property(TARGET txc PROPERTY C_STANDARD 11)
  set(PORT_TX_RUNTIME runtime/tx/tx_rt.c runtime/tx/tx_spin.c ${PORT_TX_CALLS})
endif()

# The PowerVR renderer's shaders: fxc (Windows SDK) compiles pvr/pvr.hlsl into headers at build time.
find_program(PORT_FXC fxc REQUIRED)
set(PORT_SHADERS "${CMAKE_CURRENT_BINARY_DIR}/shaders")
set(PORT_SHADER_HEADERS "${PORT_SHADERS}/pvr_vs.h" "${PORT_SHADERS}/pvr_blend_vs.h" "${PORT_SHADERS}/pvr_ps.h" "${PORT_SHADERS}/pvr_gather.h"
    "${PORT_SHADERS}/pvr_full.h" "${PORT_SHADERS}/pvr_resolve.h" "${PORT_SHADERS}/pvr_shadow.h"
    "${PORT_SHADERS}/pvr_tvolume.h" "${PORT_SHADERS}/pvr_tmerge.h" "${PORT_SHADERS}/gfx_movie_ps.h")
add_custom_command(OUTPUT ${PORT_SHADER_HEADERS}
  COMMAND ${CMAKE_COMMAND} -E make_directory "${PORT_SHADERS}"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T vs_5_0 /E vs_main /Vn g_pvr_vs /Fh "${PORT_SHADERS}/pvr_vs.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T vs_5_0 /E vs_blend /Vn g_pvr_blend_vs /Fh "${PORT_SHADERS}/pvr_blend_vs.h"
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
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_volume /Vn g_pvr_tvolume /Fh "${PORT_SHADERS}/pvr_tvolume.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr_oit.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_merge /Vn g_pvr_tmerge /Fh "${PORT_SHADERS}/pvr_tmerge.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/pvr/pvr_oit.hlsl"
  COMMAND ${PORT_FXC} /nologo /O3 /WX /T ps_5_0 /E ps_movie /Vn g_gfx_movie_ps /Fh "${PORT_SHADERS}/gfx_movie_ps.h"
          "${CMAKE_CURRENT_SOURCE_DIR}/runtime/gfx/gfx_movie.hlsl"
  DEPENDS pvr/pvr.hlsl pvr/pvr_oit.hlsl runtime/gfx/gfx_movie.hlsl
  COMMENT "fxc pvr/pvr.hlsl pvr/pvr_oit.hlsl runtime/gfx/gfx_movie.hlsl")

add_executable(port ${PORT_HOST} ${PORT_RUNTIME} ${PORT_TX_RUNTIME} ${PORT_SDK_SOURCES} ${PORT_GAME_SOURCES} ${PORT_GENERATED} "${PORT_GEN}/port_entry.c"
               ${PORT_SHADER_HEADERS})
target_include_directories(port BEFORE PRIVATE "${PORT_GEN}/include" "${PORT_READABLE_INCLUDE}" runtime runtime/tx pvr
                           sdk base "${PORT_RD_RT}" "${PORT_SHADERS}")
target_compile_options(port PRIVATE "$<$<COMPILE_LANGUAGE:C>:${PORT_C_BASE};/TC>"
                       "$<$<COMPILE_LANGUAGE:CXX>:${PORT_CXX_FLAGS}>")
# Optimisation per file: the kit's own C at PORT_KIT_OPT; everything that comes from the game or its translation at the
# judge's flags (with PORT_CHECKED both are /Od /RTC1).
set(PORT_KIT_C ${PORT_RUNTIME} ${PORT_SDK_SOURCES})
if(PORT_TX)
  list(APPEND PORT_KIT_C runtime/tx/tx_rt.c runtime/tx/tx_spin.c)
endif()
set(PORT_JUDGED_C ${PORT_GENERATED} ${PORT_GAME_SOURCES} "${PORT_GEN}/port_entry.c")
if(PORT_TX)
  list(APPEND PORT_JUDGED_C ${PORT_TX_CALLS})
endif()
set_source_files_properties(${PORT_KIT_C} PROPERTIES COMPILE_OPTIONS "${PORT_KIT_OPT}")
if(PORT_CHECKED)
  set_source_files_properties(${PORT_JUDGED_C} PROPERTIES COMPILE_OPTIONS "/Od;/RTC1")
else()
  set_source_files_properties(${PORT_JUDGED_C} PROPERTIES COMPILE_OPTIONS "${PORT_GAME_OPT}")
endif()
if(PORT_CHECKED)
  target_compile_definitions(port PRIVATE PORT_CHECKED=1) # runtime/main.c logs the /RTC failures
endif()
set_property(TARGET port PROPERTY C_STANDARD 11)
set_property(TARGET port PROPERTY CXX_STANDARD 20)
set_property(TARGET port PROPERTY CXX_STANDARD_REQUIRED ON)
if(PORT_TX)
  target_link_libraries(port PRIVATE txc)
endif()
target_link_libraries(port PRIVATE readable legacy_stdio_definitions SDL3::SDL3 d3d11 dxgi dxguid windowscodecs ole32 uuid
                      dbghelp comctl32 bcrypt)
# /BASE keeps the executable clear of guest RAM and its mirrors (runtime/guest_mem.c), which are mapped at fixed
# addresses (0x0c000000, 0x8c000000, 0xac000000).
target_link_options(port PRIVATE /WX /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /BASE:0x5a000000
                    /MAP:${CMAKE_CURRENT_BINARY_DIR}/port.map /STACK:8388608)
# The game's overlays (tools/gen_overlays.py from <game>/overlays.json), each compiled as its own library.
include("${PORT_GEN}/overlays/overlays.cmake" OPTIONAL)
