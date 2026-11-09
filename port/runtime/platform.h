/* Platform layer: the host services that SDK replacements (the kit's sdk/ modules and a game's own sources) use.
 * Those files include game.h, so they include no CRT or Windows header (game.h declares the CRT functions the game
 * links); runtime/host.c does the host calls. */
#ifndef PLATFORM_H
#define PLATFORM_H

#include "rd_rt.h"
#include "port_config.h" /* generated from game.json */
#include "base.h"      /* the port base: log, clock, args, saves, pads (base/base.h) */
#include "console.h"   /* what this kit supplies the base */

/* host.c: the clock, args and saves are the base's (base.h) */
/* base/present.c: a frame wider or taller than 4:3 (--aspect fit|4:3|16:9|21:9|W:H; game.json "widescreen": a play run
 * follows the window's shape): the 640x480 screen is its middle, so 2D stays 4:3. port_widescreen is the frame's width over
 * the screen's, port_tallscreen its height over the screen's (each 1 at 4:3, otherwise one of them is above 1): a game
 * that supports it widens its culling by them, horizontally and vertically. port_shape_chosen is 0 only in a check at its
 * original 4:3 (not --aspect, the menu or a play run following the window): a picture upgrade that also applies to a
 * chosen 4:3, where the two above are 1, asks it. */
f32 port_widescreen(void);
f32 port_tallscreen(void);
/* base/present.c, for game.json "wide_cull" (gen_port's WideCull_ wrappers): a renderer that drops what falls outside
 * the viewport (four floats W, H, X, Y at guest address viewport) sees it grown by the frame's share for one call when
 * it is centred on the 640x480 screen (the full screen, a wipe) and the frame is not 4:3. begin returns 1 if it
 * widened them (saved holds the four); end puts them back. A view off centre, a nested call or a 4:3 frame is left
 * alone. */
int port_wide_cull_begin(u32 viewport, f32 saved[4]);
void port_wide_cull_end(u32 viewport, const f32 saved[4]);
/* pvr/pvr_parts.c: game.json part_draw's wrapper brackets each call of the game's per-part 3D draw */
void port_part_begin(const void *obj, const void *part, u32 stamp_at, u32 world_at);
void port_part_end(void);
int port_shape_chosen(void);
void port_read_whole_frame(int on); /* pvr_read.c: while on, reading the frame buffer gives the whole frame, squeezed to the size read, not the 640x480 screen in its middle */
#define PORT_VBLANK_HZ 60u
void port_wait_vblank(void); /* waits for the next vertical blank (the host clock at 60 Hz) and counts it */
typedef void (*PortVblankFn)(void *arg);
void port_set_vblank_hook(PortVblankFn fn, void *arg); /* called at each vertical blank, like an interrupt */
/* The time through the current vertical blank, 0..65536 (65536: it is over, the next one is due). look: the game
 * looks at a clock (a sound chip's position), which under --fixed-clock moves the virtual time as host_ticks does. */
u32 port_vblank_phase(int look);
void port_spin_word(u32 addr); /* tx/tx_rt.c: a word the game spins on until an interrupt changes it */
/* tx/tx_rt.c: runs before(target, arg) before and after(target, arg) after each call to guest address addr that goes
 * through translator C's tc_call (any function, translated or not; whatever unit's code that is; either hook may be
 * NULL; up to 32 hooks). For a game that changes what a function reads for the length of one call (a flag word, say)
 * and puts it back, or that notes a call happened. A call the translator made direct, inside one function, is not seen. */
typedef void (*PortCallHookFn)(u32 target, void *arg);
void port_hook_call(u32 addr, PortCallHookFn before, PortCallHookFn after, void *arg);
/* From a before hook: this one call does not happen; it returns 0 and the after hooks still run. Registers the callee would
 * have kept stay as they are. The next call made takes it, so a hook that makes calls of its own says so last. For a screen a
 * game draws itself part of the time. */
void port_hook_skip_call(void);
void port_add_vblank_task(PortVblankFn fn, void *arg); /* also called at each vertical blank, after the hook
                                                         * (a library's server, such as the memory cards') */

/* frame_extra.c: in-between frames (--present interp). The simulation keeps the original tick; while a frame the
 * game rendered waits for its vertical blank, the game's draw function draws more frames, which are shown and then
 * undone (guest RAM and the registered host state go back to what the tick left). alpha: the time through the
 * tick, 0..1, for the game's blending. ready: asked once per rendered frame, whether extra frames may follow it. */
typedef int (*PortExtraDrawFn)(f32 alpha); /* 1 if it showed a frame */
typedef int (*PortExtraReadyFn)(void);
void port_set_extra_draw(PortExtraDrawFn draw, PortExtraReadyFn ready);
/* --interp-scene: a game with no draw function still gets in-between frames, from the scene before this one: the
 * renderer (pvr/pvr_blend.c) blends this scene's vertices toward where they were a tick ago. Nothing runs of the
 * game, so nothing is undone. extra_scene: it is on (the renderer keeps the old scene); pvr_blend_draw draws and shows
 * one at alpha; pvr_blend_ready: whether one can be drawn now. extra_scene_mode: --interp-scene for the menu, 0 off,
 * 1 on, 2 only; -1 when the game's own draw function makes them. */
int extra_scene(void);
int extra_scene_mode(void);
int pvr_blend_draw(f32 alpha);
int pvr_blend_ready(void);
/* frame_copy.c: guest RAM copied in slices by helper threads (--interp-copy-threads) */
void extra_copy_init(void);
void extra_copy_ram(u8 *dst, const u8 *src);
void extra_copy_threads(u32 threads);
u32 extra_copy_thread_count(void);
/* --present-thread: in-between frames of a blended scene are drawn and shown by a thread of their own, so a tick takes no
 * time from them. hold: none until the next go; the renderer and gfx_present call it, holding gfx_lock, while the frame
 * target has a frame not yet counted or shown. go: from a vertical blank's wait: they may go on; returns whether that
 * thread is running. */
void port_extra_thread_hold(void);
void port_extra_thread_early(int on);  /* --present-thread on: hold does nothing, and rendered starts them */
void port_extra_thread_rendered(void); /* the renderer, holding gfx_lock, at a render's end */
void port_extra_thread_tick(void);   /* the host, as a tick starts */
int port_extra_thread_shows(void);    /* --present-pace on: the thread shows the tick's own frames too */
void port_extra_thread_counted(void);  /* gfx_frame_done: the frame is counted; the thread may take the device again */
int gfx_present_paced(void);           /* base/present.h: --present-pace */
u64 port_extra_thread_period(void);    /* --present-thread on: microseconds between the last two renders' ends, 0 unknown */
int port_extra_thread_is_early(void);
void port_extra_shown(void);           /* gfx_present, holding gfx_lock: a frame was shown by the tick */
int gfx_capture_asked(void);           /* a dump of the frame target is asked for */
int port_extra_thread_go(void);
void port_extra_thread_own(int wanted); /* a game's own draw function: the thread shows the last frame again */
void port_extra_thread_shown(void);     /* gfx_show, holding gfx_lock */
int port_extra_thread_reshow(u64 now_us, u32 fps, u32 *count);
void gfx_lock_on(void); /* base/present.h */
void gfx_lock(void);
void gfx_unlock(void);
void gfx_lock_present_thread(void);
int gfx_lock_wanted(void);
void port_extra_thread_begin(int wanted); /* a frame was rendered; starts the thread the first time it is wanted */
int port_extra_thread_on(void);
int port_extra_thread_show(u64 now_us, u64 start_us, int *finished);
u64 port_extra_cap_wait(u64 now_us, u32 fps, u64 cost_us); /* --fps N: 0 if one started now is in its slot */
void port_extra_cap_shown(u64 at_us, u32 fps);              /* one was shown at at_us: the next slot */
/* A flip that does not wait (the game waited for the blank itself, as a Kamui 1 game's loop does) holds its frame instead:
 * port_extra_flip starts the in-between frames for it (1: held, counted and dumped, not shown); the next wait for a
 * vertical blank (port_extra_pending) draws them, then port_extra_land shows the frame if none was drawn. */
int port_extra_flip(u32 vblanks);
int port_extra_pending(void);
void port_extra_land(void);
void port_extra_state(void *p, u32 size); /* host-side state an in-between frame must leave as it found it */
typedef void (*PortUndoFn)(void *p, u32 size);
/* Inside an in-between frame: saves p[0..size) before it is overwritten, to be put back after the frame (then
 * after(p, size), e.g. to drop a cache of it). Outside one it does nothing. */
void port_extra_undo(void *p, u32 size, PortUndoFn after);
/* pvr/pvr_state.c: texture memory through its 64-bit window (0x04000000..0x047fffff, any area) for a guest copy into
 * it: the host bytes of [addr, addr + size), NULL outside the window; port_texture_written after the write. */
u8 *port_texture_window(u32 addr, u32 size);
void port_texture_written(u32 addr, u32 size);
int port_extra_begin(u32 vblanks);        /* a rendered frame that waits vblanks: 1 if extra frames follow it */
u32 port_extra_count(void);               /* in-between frames shown since port_extra_begin */
int port_extra_idle(u64 deadline_us);     /* host.c, while it waits: draws one if there is time (1 if it did) */
void port_wait_vblank_extra(void);        /* port_wait_vblank, drawing in-between frames while it waits */
void port_extra_report(void);             /* logs how many were drawn (at exit) */
void pvr_tex_report(void); /* pvr/pvr_tex.c: textures decoded, kept and decoded again (at exit) */
void pvr_oit_report(void); /* pvr/pvr_oit.c: the autosort pool, its largest pass and any overflow (at exit) */
void *host_map_fixed(u32 addr, u32 size); /* commits read/write host memory at a fixed address; NULL if taken */
/* Save files: host_save_read / host_save_write (base.h). A --fixed-clock run keeps none unless --save-dir is
 * given, so it is the same every time: then both return -1. */
/* The console's flash ROM image from --flash FILE (e.g. an emulator's dc_nvmem.bin), recorded and replayed like a
 * save file: 1 read, exactly size bytes; 0 no --flash; -1 unreadable or not size bytes. */
int host_flash_read(void *buf, u32 size);

/* coro.c: host coroutines, for a library's own execution contexts (sdk/lib_ctx.c). Each runs fn(arg) on a host
 * stack of its own and runs only when switched to, so everything happens in the program's own order, the same
 * every run. fn must not return. */
typedef struct HostCoro HostCoro;
typedef void (*HostCoroFn)(void *arg);
HostCoro *host_coro_self(void);                       /* the running one (the thread itself, before any switch) */
HostCoro *host_coro_create(HostCoroFn fn, void *arg); /* NULL if the host cannot make one */
void host_coro_switch(HostCoro *to);                  /* resumes to; returns when something switches back */
void host_coro_delete(HostCoro *c);                   /* one that is not running */
/* dispatch.c: the host function that runs the code at addr: for guest code, the function starting there (an
 * overlay's or the program's), NULL if the port has none; host code in its P0 form (addr & 0x1fffffff) as that host
 * function; any other address is a host function already. */
typedef void (*PortCodeFn)(void);
PortCodeFn port_code_function(u32 addr);

/* base/input.c: up to HOST_PADS controllers (base.h) */
s32 session_timer(s32 live); /* session.c: the SDK timer's count as the game sees it (recorded, or replayed) */
/* sdk/maple_pad.c: the memory card sockets (--vmu A1,B1,..|none; default A1): bit c * 2 + s is connector c's (A..D)
 * socket s + 1. gfx/gfx_vmu.c: a block reached that card's LCD; dots[48 * 32] row by row as the player sees the
 * screen in the controller, 1 dark. */
u32 maple_card_sockets(void);
void host_vmu_lcd(u32 card, const u8 *dots);

/* gfx/: the window, the Direct3D 11 device and the frame target (gfx.h) */
void gfx_clear(u32 argb); /* fills the frame with one colour (the alpha is ignored) */
void gfx_present(void);   /* shows the frame (dumping it if asked) and handles window events */
void gfx_hold(int on);    /* on: frames are neither shown, counted nor dumped; the screen keeps the last one */
int gfx_held(void);       /* gfx_hold is on */
void gfx_frame_done(void); /* counts the frame and dumps it if asked, without showing it */
void gfx_show(void);       /* shows the frame without counting it (an in-between frame) */
void gfx_capture_extra(void); /* an in-between frame: dumps it if --dump-extra asks for it */
int gfx_can_show(void);    /* 1 if the display would take a frame now without blocking (gfx_show drops it if not) */
void gfx_show_wait(void);  /* --present-thread: waits a moment for that */
u64 gfx_last_present_us(void); /* the last Present call's duration, microseconds */
void gfx_set_border(u32 argb); /* the colour around the 4:3 picture */
u32 gfx_border(void);          /* that colour */
/* pvr_read.c: one pixel of the displayed 640x480 frame buffer as the console's video memory holds it (RGB565, the
 * mean of the frame pixels under it when the frame is bigger). 0 if x or y is off the screen or no frame was drawn. */
int gfx_read_pixel(u32 x, u32 y, u16 *rgb565_out);

/* heap.c: the console syMalloc's allocator over one region: 32-byte units, a {next, units} header unit per block */
#define HEAP_ALIGN 32u
typedef struct Heap {
    const char *name;
    void *first, *end;
    void *rover;   /* the free block the next search starts after */
    u32 ring[2];   /* the free ring's sentinel {next, 0 units} */
    u32 used, peak;
    int failed_logged;
} Heap;
void heap_init(Heap *h, const char *name, void *region, u32 size);
void heap_finish(Heap *h);
void *heap_alloc(Heap *h, u32 size);
int heap_free(Heap *h, void *ptr); /* 0 if ptr is not a live block */

/* gdi.c: the ISO 9660 volume of the disc the port booted from, for gdFs. LBAs are absolute; sectors are 2048 bytes.
 * Names are without the ";1" version; "." and ".." name the self and parent records. */
typedef struct HostDirEnt {
    u32 lba, size;
    u8 is_dir;
    char name[32];
} HostDirEnt;
int host_disc_root(u32 *lba, u32 *size);
int host_disc_read(u32 lba, u32 count, void *out); /* 0 on a read error */
int host_disc_find(u32 lba, u32 size, const char *name, HostDirEnt *out); /* case-insensitive; 0 if absent */
/* Lists the directory at lba/size without "." and "..": the count, -1 on a read error, -2 if over max. */
int host_disc_list(u32 lba, u32 size, HostDirEnt *out, int max);
int host_disc_toc(u32 *toc); /* u32[102], the high-density area's TOC in the GD-ROM form; 0 if unknown */
int host_disc_audio(u32 track, u32 *lba, u32 *sectors); /* an audio track's first sector and length; 0 if none */
int host_disc_read_audio(u32 lba, u32 count, void *out); /* raw 2352-byte CD audio sectors, 588 stereo frames each */

/* cdda.c: the drive's CD audio, which the AICA's CD input mixes (aica.h). repeat is the GD-ROM's count: 0 plays the
 * tracks once, 15 for ever. */
int host_cd_play(u32 first_track, u32 last_track, u32 repeat); /* 1 playing; 0 if they are not audio tracks */
void host_cd_stop(void);
void host_cd_pause(int paused);
int host_cd_playing(void); /* 1 while playing; 0 when stopped, paused or past the end */

#endif
