/* Platform layer: the host services that SDK replacements (the kit's sdk/ modules and a game's own sources) use.
 * Those files include game.h, so they include no CRT or Windows header (game.h declares the CRT functions the game
 * links); runtime/host.c does the host calls. */
#ifndef PLATFORM_H
#define PLATFORM_H

#include "rd_rt.h"
#include "port_config.h" /* generated from game.json */

void port_log(const char *fmt, ...);
void port_exit(const char *why, int code);

/* host.c */
u64 host_ticks(u32 hz); /* ticks at hz since the first call (the performance counter) */
typedef struct HostTime {
    u16 year, month, day, hour, minute, second, day_of_week; /* day_of_week: 0 = Sunday */
} HostTime;
void host_local_time(HostTime *t);
void host_sleep_ms(u32 ms);
void host_set_fixed_clock(int on); /* --fixed-clock: virtual time (host.c), the same run every time */
int host_fixed_clock(void);
void host_set_fast_until(u32 vblank); /* --fast-until N: --fixed-clock until vertical blank N, then real time */
u32 host_fast_until(void);
#define PORT_VBLANK_HZ 60u
void port_wait_vblank(void); /* waits for the next vertical blank (the host clock at 60 Hz) and counts it */
typedef void (*PortVblankFn)(void *arg);
void port_set_vblank_hook(PortVblankFn fn, void *arg); /* called at each vertical blank, like an interrupt */
u32 port_vblank_count(void);  /* vertical blanks waited for: one per main-loop frame */
void port_add_vblank_task(PortVblankFn fn, void *arg); /* also called at each vertical blank, after the hook
                                                         * (a library's server, such as the memory cards') */

/* frame_extra.c: in-between frames (--present interp). The simulation keeps the original tick; while a frame the
 * game rendered waits for its vertical blank, the game's draw function draws more frames, which are shown and then
 * undone (guest RAM and the registered host state go back to what the tick left). alpha: the time through the
 * tick, 0..1, for the game's blending. ready: asked once per rendered frame, whether extra frames may follow it. */
typedef int (*PortExtraDrawFn)(f32 alpha); /* 1 if it showed a frame */
typedef int (*PortExtraReadyFn)(void);
void port_set_extra_draw(PortExtraDrawFn draw, PortExtraReadyFn ready);
void port_extra_state(void *p, u32 size); /* host-side state an in-between frame must leave as it found it */
typedef void (*PortUndoFn)(void *p, u32 size);
/* Inside an in-between frame: saves p[0..size) before it is overwritten, to be put back after the frame (then
 * after(p, size), e.g. to drop a cache of it). Outside one it does nothing. */
void port_extra_undo(void *p, u32 size, PortUndoFn after);
int port_extra_drawing(void);             /* 1 inside an in-between frame */
int port_extra_begin(u32 vblanks);        /* a rendered frame that waits vblanks: 1 if extra frames follow it */
u32 port_extra_count(void);               /* in-between frames shown since port_extra_begin */
int port_extra_idle(u64 deadline_us);     /* host.c, while it waits: draws one if there is time (1 if it did) */
void port_wait_vblank_extra(void);        /* port_wait_vblank, drawing in-between frames while it waits */
void port_extra_report(void);             /* logs how many were drawn (at exit) */
void *host_map_fixed(u32 addr, u32 size); /* commits read/write host memory at a fixed address; NULL if taken */
const char *host_arg(const char *name); /* the value after the command-line option name ("--vmu"), or NULL */
/* Save files, by plain file name, in the save directory (--save-dir DIR, default saves beside port.exe). A
 * --fixed-clock run keeps none unless --save-dir is given, so it is the same every time: then both return -1. */
int host_save_read(const char *name, void *buf, u32 size); /* 1: read, exactly size bytes; 0: missing or not size */
int host_save_write(const char *name, const void *buf, u32 size); /* 1: written (replacing it whole); 0: failed */

/* gfx/gfx_input.c: one controller, from every SDL3 gamepad and joystick and the keyboard, plus a --input script. The
 * buttons are in the Dreamcast controller's bit layout (set when held); the analog values in its ranges. */
#define HOST_PAD_C 0x00001u
#define HOST_PAD_B 0x00002u
#define HOST_PAD_A 0x00004u
#define HOST_PAD_START 0x00008u
#define HOST_PAD_UP 0x00010u
#define HOST_PAD_DOWN 0x00020u
#define HOST_PAD_LEFT 0x00040u
#define HOST_PAD_RIGHT 0x00080u
#define HOST_PAD_Z 0x00100u
#define HOST_PAD_Y 0x00200u
#define HOST_PAD_X 0x00400u
typedef struct HostPad {
    u32 buttons;
    u8 l, r;   /* triggers, 0..255 */
    s8 x, y;   /* stick, -128..127; y is negative up */
} HostPad;
void host_pad_read(HostPad *pad);             /* samples the host now; call once per game input poll */
s32 session_timer(s32 live); /* session.c: the SDK timer's count as the game sees it (recorded, or replayed) */
void host_pad_rumble(u32 strength, u32 ms);   /* both motors, strength 0..65535 for ms; 0 stops */

/* gfx/: the window, the Direct3D 11 device and the frame target (gfx.h) */
void gfx_clear(u32 argb); /* fills the frame with one colour (the alpha is ignored) */
void gfx_present(void);   /* shows the frame (dumping it if asked) and handles window events */
void gfx_frame_done(void); /* counts the frame and dumps it if asked, without showing it */
void gfx_show(void);       /* shows the frame without counting it (an in-between frame) */
void gfx_capture_extra(void); /* an in-between frame: dumps it if --dump-extra asks for it */
int gfx_can_show(void);    /* 1 if the display would take a frame now without blocking (gfx_show drops it if not) */
void gfx_set_border(u32 argb); /* the colour around the 4:3 picture */

/* heap.c: a first-fit allocator over one region, 32-byte aligned blocks */
#define HEAP_ALIGN 32u
typedef struct Heap {
    const char *name;
    void *first, *end;
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
