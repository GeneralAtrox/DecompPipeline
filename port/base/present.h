/* present.c, present_device.c, present_aspect.c: the base's presenter. A Direct3D 11 device and its flip-model swap chain on the
 * base's window, and the frame target a console draws into (a GPU renderer into g_gfx's targets, a CPU frame through
 * present_cpu.c). The frame target is the chosen shape at the output's size; present_show copies it into the swap
 * chain's back buffer, with bars where the shapes differ. Headless runs draw the same frame target with no window, so
 * frame dumps are the same either way. A renderer draws into the draw target: the frame itself, or with --ssaa K a
 * K-times larger target that gfx_resolve box-filters down into the frame (a look-review prototype, off by default). */
#ifndef PRESENT_H
#define PRESENT_H

#define COBJMACROS
#pragma warning(push)
#pragma warning(disable : 4201) /* the SDK's nameless unions, before C11 (a kit built without /std:c11) */
#include <d3d11.h>
#include <dxgi1_6.h>
#pragma warning(pop)
#include "base.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct GfxState {
    int headless, warp, fullscreen;
    u32 out_w, out_h;       /* the output: the window's client area in pixels, or --size when headless */
    u32 frame_w, frame_h;   /* the frame target: the chosen aspect's shape, as large as the output holds (present_aspect.c) */
    u32 frame_x, frame_y;   /* where the frame sits in the back buffer */
    u32 frames;             /* frames presented */
    ID3D11Device *dev;
    ID3D11DeviceContext *ctx;
    IDXGISwapChain1 *swap;  /* NULL when headless */
    ID3D11Texture2D *frame; /* R8G8B8A8_UNORM, render target and shader resource */
    ID3D11RenderTargetView *frame_rtv;
    ID3D11Texture2D *draw;  /* the renderer's target: the frame, or K x its size with --ssaa K (mip-mapped) */
    ID3D11RenderTargetView *draw_rtv;
    ID3D11ShaderResourceView *draw_srv; /* --ssaa only, to build its mips */
    u32 draw_w, draw_h;
    u32 ssaa;               /* --ssaa 2|4: supersampling factor per axis (0: off) */
    u32 aniso;              /* --aniso 2..16: anisotropic filtering for filtered textures (0: off) */
    int tearing;            /* the swap chain allows tearing (variable refresh) */
    int vsync;              /* --vsync on|off (default off); the host's menu changes it */
    UINT swap_flags;        /* the swap chain's flags, again for ResizeBuffers */
    HANDLE latency;         /* signalled when the swap chain can take a frame without blocking; NULL if none */
    u32 dropped;            /* frames not presented because the swap chain was still full */
    int aspect_set;         /* --aspect or the menu chose a shape; else 4:3, or fit in a play run of a widescreen game */
    f32 aspect;             /* the chosen shape, width over height; 0: fit the window */
    f32 wide_k, tall_k;     /* the frame's width and height over the 640x480 screen's (each 1 or more), set with the frame */
} GfxState;

extern GfxState g_gfx;

/* present_aspect.c: the frame's shape (no Direct3D in it) */
typedef struct GfxFrameShape {
    u32 w, h, x, y;
    f32 wide_k, tall_k;
} GfxFrameShape;
int gfx_aspect_parse(const char *text, f32 *ratio);
const char *gfx_aspect_name(f32 ratio, char buf[16]);
void gfx_frame_shape(u32 out_w, u32 out_h, f32 ratio, GfxFrameShape *s);
void gfx_screen_rect(u32 w, u32 h, f32 wide_k, f32 tall_k, u32 r[4]); /* the 640x480 screen in a frame: x, y, w, h */

/* present_device.c */
int gfx_option(const char *arg, const char *value); /* the arguments one option takes, 0 if not ours */
int gfx_init(void);                                 /* 0 on failure (logged) */
void gfx_pump(void);                                /* host_window.cpp: window events; closing the window ends the run */
void gfx_set_vsync(int on);
int gfx_vsync(void);

/* present.c */
int gfx_resize(unsigned out_w, unsigned out_h); /* (re)creates the frame target for an output size */
f32 gfx_aspect(void);                 /* the shape in use, width over height; 0: fit the window */
int gfx_set_aspect(const char *text); /* --aspect fit|4:3|16:9|21:9|W:H, at once; 0 if not one */
const char *gfx_aspect_text(void);    /* the shape in use, in those words */
f32 port_widescreen(void);            /* the frame's width and height over the screen's: a game's culling widens by them */
f32 port_tallscreen(void);
void gfx_set_border(u32 argb);        /* the colour around the picture (the console's border colour) */
void gfx_resolve(void);               /* the draw target into the frame (--ssaa); nothing otherwise */
void gfx_clear(u32 argb);             /* fills the frame with one colour (the alpha is ignored) */
int gfx_can_show(void);               /* 1 if the display would take a frame now without blocking */
void present_show(void);              /* the frame onto the window; a frame the display will not take is dropped */
double gfx_presented_fps(void);       /* the last 5 s readout of frames presented a second */
void gfx_shutdown(void);

#ifdef __cplusplus
}
#endif

#endif
