# dpimg: the pipeline's image library (docs/IMGTOOL.md), a shared library and its command-line front end. Configured
# and built by port/tools/dpimg.py (CMake + Ninja under the Visual Studio environment the kit's build uses), for the
# bitness of the Python that loads it.
cmake_minimum_required(VERSION 3.20)
project(dpimg C)

set(DPIMG_SOURCES src/dpimg_io.c src/dpimg_ops.c src/dpimg_resize.c src/dpimg_geom.c src/dpimg_draw.c
                  src/dpimg_text.c src/dpimg_tex.c)
add_library(dpimg SHARED ${DPIMG_SOURCES})
target_include_directories(dpimg PUBLIC include PRIVATE src third_party/stb)
target_compile_definitions(dpimg PRIVATE _CRT_SECURE_NO_WARNINGS)
# The proofs compare against Pillow's arithmetic, so no fast-math: /fp:precise (MSVC's default) is set explicitly.
if(MSVC)
  target_compile_options(dpimg PRIVATE /O2 /fp:precise /W3 /wd4244 /wd4267)
endif()

add_executable(dpimg-cli src/dpimg_cli.c)
target_link_libraries(dpimg-cli PRIVATE dpimg)
target_compile_definitions(dpimg-cli PRIVATE _CRT_SECURE_NO_WARNINGS)
