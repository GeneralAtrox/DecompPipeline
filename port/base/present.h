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
#include "work_count.h"

/* The calls that do the work, counted for --work-counts (work_count.h). A Map to read is left out: it may be a poll of
 * the GPU, and how many depends on the GPU's speed. */
#undef ID3D11DeviceContext_Draw
#undef ID3D11DeviceContext_DrawIndexed
#undef ID3D11DeviceContext_DrawInstanced
#undef ID3D11DeviceContext_DrawIndexedInstanced
#undef ID3D11DeviceContext_Map
#undef ID3D11DeviceContext_UpdateSubresource
#undef ID3D11DeviceContext_CopyResource
#undef ID3D11DeviceContext_CopySubresourceRegion
#undef ID3D11Device_CreateTexture2D
#undef ID3D11Device_CreateBuffer
#define WORK_D3D(id, n, call) (work_add((id), (n)), (call))
#define ID3D11DeviceContext_Draw(c, n, s) WORK_D3D(WC_DRAWS, 1, (c)->lpVtbl->Draw(c, n, s))
#define ID3D11DeviceContext_DrawIndexed(c, n, s, b) WORK_D3D(WC_DRAWS, 1, (c)->lpVtbl->DrawIndexed(c, n, s, b))
#define ID3D11DeviceContext_DrawInstanced(c, n, i, s, si) WORK_D3D(WC_DRAWS, 1, (c)->lpVtbl->DrawInstanced(c, n, i, s, si))
#define ID3D11DeviceContext_DrawIndexedInstanced(c, n, i, s, b, si) WORK_D3D(WC_DRAWS, 1, (c)->lpVtbl->DrawIndexedInstanced(c, n, i, s, b, si))
#define ID3D11DeviceContext_Map(c, r, sub, t, f, m) WORK_D3D(WC_UPLOADS, (t) != D3D11_MAP_READ, (c)->lpVtbl->Map(c, r, sub, t, f, m))
#define ID3D11DeviceContext_UpdateSubresource(c, r, sub, b, p, rp, dp) WORK_D3D(WC_UPLOADS, 1, (c)->lpVtbl->UpdateSubresource(c, r, sub, b, p, rp, dp))
#define ID3D11DeviceContext_CopyResource(c, d, s) WORK_D3D(WC_GPU_COPIES, 1, (c)->lpVtbl->CopyResource(c, d, s))
#define ID3D11DeviceContext_CopySubresourceRegion(c, d, ds, x, y, z, s, ss, b) WORK_D3D(WC_GPU_COPIES, 1, (c)->lpVtbl->CopySubresourceRegion(c, d, ds, x, y, z, s, ss, b))
#define ID3D11Device_CreateTexture2D(d, t, i, o) (work_add(WC_GPU_CREATES, 1), WORK_D3D(WC_UPLOADS, (i) != NULL, (d)->lpVtbl->CreateTexture2D(d, t, i, o)))
#define ID3D11Device_CreateBuffer(d, t, i, o) (work_add(WC_GPU_CREATES, 1), WORK_D3D(WC_UPLOADS, (i) != NULL, (d)->lpVtbl->CreateBuffer(d, t, i, o)))

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
/* host_window.cpp: --present-thread (runtime/frame_extra.c). The thread that shows in-between frames and the game's thread
 * share the device context: each holds this lock around its use. It does nothing until gfx_lock_on. The lock nests. */
void gfx_lock_on(void);
void gfx_lock(void);
void gfx_unlock(void);
void gfx_lock_present_thread(void); /* called by that thread, once */
int gfx_on_present_thread(void);
int gfx_lock_wanted(void);          /* the game's thread is waiting for the lock: that thread lets it have it */
unsigned gfx_present_stall_us(void); /* --present-stall-us (present_device.c) */
int gfx_present_paced(void);         /* --present-pace on (default) or off */
void gfx_set_vsync(int on);
int gfx_vsync(void);

/* present.c */
int gfx_resize(unsigned out_w, unsigned out_h); /* (re)creates the frame target for an output size */
ID3D11RenderTargetView *gfx_back_rtv(void); /* the swap chain's back buffer (the level editor draws its own picture there) */
f32 gfx_aspect(void);                 /* the shape in use, width over height; 0: fit the window */
int gfx_set_aspect(const char *text); /* --aspect fit|4:3|16:9|21:9|W:H, at once; 0 if not one */
const char *gfx_aspect_text(void);    /* the shape in use, in those words */
f32 port_widescreen(void);            /* the frame's width and height over the screen's: a game's culling widens by them */
f32 port_tallscreen(void);
int port_wide_cull_begin(u32 viewport, f32 saved[4]); /* game.json wide_cull: the viewport is the frame for one call */
void port_wide_cull_end(u32 viewport, const f32 saved[4]);
int port_shape_chosen(void);          /* 0 only in a check at its original 4:3: a picture upgrade that also applies to a chosen 4:3 asks this */
void gfx_set_border(u32 argb);        /* the colour around the picture (the console's border colour) */
u32 gfx_border(void);                 /* that colour */
void gfx_resolve(void);               /* the draw target into the frame (--ssaa); nothing otherwise */
void gfx_clear(u32 argb);             /* fills the frame with one colour (the alpha is ignored) */
int gfx_can_show(void);               /* 1 if the display would take a frame now without blocking */
void gfx_show_wait(void);             /* --present-thread: waits a moment for that */
u64 gfx_last_present_us(void);         /* the last Present call's duration (runtime/frame_cost.c) */
void present_show(void);              /* the frame onto the window; a frame the display will not take is dropped */
double gfx_presented_fps(void);       /* the last 5 s readout of frames presented a second */
void gfx_shutdown(void);

#ifdef __cplusplus
}
#endif

#endif
