/* The host graphics layer: an SDL3 window, a Direct3D 11 device and its flip-model swap chain, and the frame render
 * target the PowerVR renderer draws into. The frame target is the 4:3 picture at the output's size (the window's
 * pixels, or --size headless); presenting copies it into the swap chain's back buffer, pillarboxed. Headless runs
 * draw the same frame target with no window, so frame dumps are the same either way. The renderer draws into the
 * draw target: the frame itself, or with --ssaa K a K-times larger target that gfx_resolve box-filters down into the
 * frame (a look-review prototype, off by default).
 * The game-facing calls (gfx_clear, gfx_present) are in platform.h; this header is for the kit's own C files. */
#ifndef GFX_H
#define GFX_H

#define COBJMACROS
#include <d3d11.h>
#include <dxgi1_6.h>
#include "port.h"

typedef struct GfxState {
    int headless, warp, fullscreen;
    u32 out_w, out_h;       /* the output: the window's client area in pixels, or --size when headless */
    u32 frame_w, frame_h;   /* the frame target: the 4:3 picture inside the output (widescreen: up to 16:9) */
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
    int wide;               /* --widescreen on|off: 1, 0; -1 (default): on in play runs (port_widescreen) */
    f32 wide_k;             /* the frame's width over a 4:3 frame's (1: 4:3), set with the frame target */
} GfxState;

extern GfxState g_gfx;

/* gfx_device.c */
int gfx_option(const char *arg, const char *value); /* the arguments one port.exe option takes, 0 if not ours */
int gfx_init(void);                                 /* 0 on failure (logged) */
void gfx_pump(void);                                /* window events; closing the window ends the run */

/* gfx_present.c */
int gfx_resize(u32 out_w, u32 out_h); /* (re)creates the frame target for an output size */
void gfx_set_border(u32 argb);        /* the colour around the picture (the console's border colour) */
void gfx_resolve(void);               /* the draw target into the frame (--ssaa); nothing otherwise */
void gfx_shutdown(void);

/* gfx_capture.c */
int gfx_capture_option(const char *arg, const char *value);
void gfx_capture_frame(u32 frame); /* writes the frame's PNGs if --dump-frames or --dump-every asks for it */
int gfx_capture_bmp(const char *path); /* the frame target as a 24-bit BMP; 0 on failure */
double gfx_presented_fps(void);        /* gfx_present.c: the last 5 s readout of frames presented a second */

/* pvr/ta_parse.c: the renderer's own options (--ta-dump SCENE) */
int pvr_option(const char *arg, const char *value);

#endif
