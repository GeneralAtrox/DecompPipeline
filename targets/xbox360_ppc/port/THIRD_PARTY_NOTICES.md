# Third-party code in the Xbox 360 port kit

## COGBox (MIT)

https://github.com/Shagg88/COGBox, commit a0557e9. Copied and put into C: `runtime/xb_xenos_colour.h` (from
`Gears2-Native/src/layer0_xenos/include/xenos/colour_formats.h`: the resolve exponent bias, 7e3 encode and decode, the 7e3
range clamp, 16-bit fixed point decode), and a render target's own exponent bias applied to the pixel shader's colour
(from `renderer/shadow_renderer.cpp` and the shader epilogue in `hlsl.cpp`; used by `runtime/xb_gpu_draw.h`,
`runtime/xb_d3d_th.h` and `tools/shader_convert.py`). The full MIT notice is in the file's header.

    MIT License. Copyright (c) 2026 Shagg.

## Xenia (BSD-3-Clause), through COGBox

COGBox's colour rules follow Xenia Canary e6cd2e4bc (`gpu/dxbc_shader_translator_om.cc`, `gpu/xenos.cc`,
`gpu/shaders/resolve.xesli`, `gpu/shaders/pixel_formats.xesli`). The parts of `runtime/xb_xenos_colour.h` taken from them
keep Xenia's notice, given in full in the file's header.

    Copyright (c) Ben Vanik and Xenia project contributors. All rights reserved. BSD-3-Clause.

The COGBox-derived code is a bring-up layer (emulated hardware), off by default behind `XB_COGBOX_7E3=1` and `XB_COGBOX_RT_BIAS=1`, and listed in
DecompPipeline's `docs/RUNTIME_TARGET.md` emulated-layers table.
