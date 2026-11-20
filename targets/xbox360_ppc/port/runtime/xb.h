/* The Xbox 360 port kit's runtime interface. Guest memory and calls follow tc_rt.h (built with TC_PORT); this header holds
 * what the kit adds: the tables gen_xbox.py writes, the import call, and the stop. */
#ifndef XB_H
#define XB_H

#include "tc_rt.h"
#include "base.h"

/* One translated function: the original's address and the thunk gen_xbox.py wrote beside it (xg_ADDR). */
typedef void (*XbThunk)(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);
typedef struct { u32 addr; XbThunk fn; } XbFn;
extern const XbFn xb_fn_table[];   /* sorted by addr */
extern const unsigned xb_fn_count;

/* One import: the thunk the game calls, the library and the ordinal (the XEX holds no names). */
typedef struct { u32 thunk; u16 lib; u16 ord; } XbImp;
extern const XbImp xb_imp_table[]; /* sorted by thunk */
extern const unsigned xb_imp_count;
extern const char *const xb_lib_name[];
extern const unsigned xb_lib_count;

/* A library the kit does not translate (port/games/<game>/boundary.json): a call to any address in it is a host call (xb_lib_entry) or a stop. */
typedef struct { u32 lo, hi; } XbRange;
extern const XbRange xb_boundary[];
extern const unsigned xb_boundary_count;
int xb_lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1);   /* runtime/xb_d3d.c: 1 when the entry is implemented */
void xb_gcb_report(void);   /* runtime/xb_d3d_res.h: the last GPU callbacks and whether this thread is inside one, for a guest fault's report */
void xb_gpu_callbacks_run(const TcArgs *a);   /* runtime/xb_d3d_res.h: InsertCallback's callbacks waiting for a vertical blank (XB_GPU_CB_DEFER) */

/* The export names (data/export_names.json, from Xenia's tables): the XEX holds ordinals, the logs show names. */
typedef struct { const char *lib; unsigned ord; const char *name; } XbName;
extern const XbName xb_name_table[];
extern const unsigned xb_name_count;
const char *xb_export_name(const char *lib, unsigned ord);   /* "?" when the tables do not have it */

/* An address the game was handed for a library function it looked up by ordinal (XexGetProcedureAddress): it resolves like an
 * import thunk, so calling it is a host call or a stop that names the function. 0 when the library is not one of the imports. */
u32 xb_dyn_import(const char *lib, unsigned ord);

/* A kernel variable slot of the import table: the guest address the loader stores in .idata. */
typedef struct { u32 slot; u32 value; u16 lib; u16 ord; } XbVar;
extern const XbVar xb_var_table[];
extern const unsigned xb_var_count;

/* Facts of the XEX header that imports answer from (gen_xbox.py writes them from image.json). */
extern const u32 xb_xex_system_flags;

/* The host side of an import. Return 1 and fill *r3 / *f1 when implemented, 0 when not (the call then stops). */
int xb_import_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1);
#define XB_XACT_BASE 0x7FFC0000u   /* the kit's XACT methods: never mapped, boundary.json makes them host calls */
int xb_xact_create(const TcArgs *a, u64 *r3);   /* runtime/xb_xact.c */
int xb_xaudio2_create(const TcArgs *a, u64 *r3);
int xb_xact_call(u32 target, const TcArgs *a, u64 *r3);
/* runtime/xb_sound.c: the game's banks and cues and the mix */
enum { XB_SOUND_RATE = 44100, XB_CUE_PLAYING = 8, XB_CUE_STOPPED = 0x20 };
void xb_sound_settings(u32 xgs, u32 callback);
u32 xb_sound_category(u32 name);
void xb_sound_category_volume(u32 category, float volume);
int xb_sound_wave_bank(u32 data, u32 size, u32 *obj);
void xb_sound_wave_bank_object(int i, u32 obj);
void xb_sound_wave_bank_destroy(int i);
int xb_sound_sound_bank(u32 data, u32 *obj);
void xb_sound_sound_bank_object(int i, u32 obj);
void xb_sound_sound_bank_destroy(int i);
int xb_sound_play(int sound_bank, u32 index, int owned);
u32 xb_sound_cue_object(int i);
void xb_sound_cue_set_object(int i, u32 obj);
u32 xb_sound_cue_state(int i);
void xb_sound_cue_destroy(int i);
void xb_sound_notify(u32 desc, int on);
void xb_sound_do_work(const TcArgs *a);
u32 xb_xma_create(u32 out_p);      /* xb_xma_hw.c: Too Human's silent XMA contexts (XB_XMA_WINDOW) */
void xb_xma_release(u32 context);
void xb_xma_report(void);
void xb_sound_stop_all(void);
void xb_sound_mix(s32 *mix, u32 frames);
int xb_sound_up(void);
void xb_sound_counts(u32 *pcm, u32 *xma);
void xb_sound_tick(void);           /* xb_sound_out.c */
void xb_sound_close(void);
void xb_sound_driver_frame(u32 samples); /* xb_sound_out.c: one frame the game's own mixer gave the audio driver */

/* runtime/xb_state.c: the device's render and sampler states */
enum { XB_CLASS_RS = 6, XB_CLASS_SS = 7, XB_RS_COUNT = 101, XB_SS_COUNT = 20, XB_SAMPLERS = 26 };
unsigned xb_frame_number(void);   /* frames swapped so far */
void xb_state_init(u32 device);
int xb_state_call(u32 target, const TcArgs *a, u64 *r3);
int xb_state_row_call(u32 target, const TcArgs *a, u64 *r3);
int xb_state_kind(u32 target, u32 entry);   /* 0 not a state call, 1 a setter, 2 a getter */
void xb_state_recorded(u32 target, u32 entry, const TcArgs *a);   /* a setter a command buffer kept: what the game's getters see from now */
void xb_state_replaying(int on);   /* this thread runs a command buffer: its setters change only what draws see */
void xb_ss_set(unsigned sampler, unsigned slot, u32 value);
u32 xb_rs(unsigned state);
u32 xb_ss(unsigned sampler, unsigned state);
extern u32 xb_tex_gamma[16];   /* xb_d3d_tex.h: each fetch slot's gamma channels, 4 bits, for the Kit constant buffer (xb_gpu_draw.h) */
extern u32 xb_slot_fetch[16][4];
extern u32 xb_slot_fetch_w05[16][3];   /* xb_d3d_tex.h: each fetch slot's constant words 0 (clamp x/y/z bits 10..18) and 5 (border colour bits 0..1) at its last bind, then 1 when this draw's texture came from the fetch constant (XB_FETCH_CLAMP) */   /* xb_d3d_tex.h: each fetch slot's constant words 1..3 at its last bind, then 1 decoded or 2 a resolve's host copy (XB_DIAG_REGION) */
int xb_content_import(unsigned ord, const TcArgs *a, u64 *r3);   /* runtime/xb_content.c: saved content */
int xb_net_import(unsigned ord, const TcArgs *a, u64 *r3);   /* runtime/xb_net.c: sockets and Live between two players, with XB_NET set; 0 otherwise */
int xb_net_voice_start(const TcArgs *a, u64 *r3);
int xb_net_stage_message(const TcArgs *a, u64 *r3);
void xb_net_game_members_heard(u32 now);   /* runtime/xb_net_game.c: the game's 5-second rule answers to the link */
void xb_net_test_stall(void);
void xb_net_check_tick(void);   /* runtime/xb_net_check.c: the players compare what the game decided */
u32 xb_overlapped_finish(u32 overlapped, u32 error, u32 information);   /* runtime/xb_content.c: what a call that finishes now returns */
int xb_content_path(const char *name, char *out, unsigned room);
void xb_content_token_closed(u32 token);
/* A kernel status or an HRESULT comes back sign-extended: 821b8588 tests NtCreateFile with a 64-bit "or.", so a zero-extended failure reads as success */
#define XB_STATUS(x) ((u64)(long long)(int)(x))
u32 xb_token_new(void);
extern volatile long xb_suspends;   /* runtime/xb_imports.c: threads NtSuspendThread holds; each stops at its next guest call */
void xb_thread_safe_point(void);
void xb_cs_waits_dump(void);        /* runtime/xb_imports.c: each thread held at a critical section, and its owner (the watchdog) */
void xb_token_set_event(u32 token);
int xb_file_import(unsigned ord, const TcArgs *a, u64 *r3);   /* runtime/xb_file.c: the kernel's file calls */
int xb_apc_run(const TcArgs *a);                              /* runtime/xb_file.c: an alertable wait runs this thread's finished reads' routines; 1 if any ran */
#define STATUS_USER_APC_ 0xC0u                                /* what such a wait then answers (windows.h's own is a different type) */

/* "No caller, no code": anything missing logs what it is and ends the process with exit code 3; NAME.stop holds the one
 * line gen_xbox.py reads next ("missing_function 82001234", "import 00020130", "fault 7fc86110"). */
void xb_stop(const char *kind, const char *detail, u32 value);

int xb_mem_init(const char *image_path, u32 load_addr);
u32 xb_alloc(u32 size);              /* committed, zeroed guest memory (64 KB aligned); a stop when the reserve is used up */
u32 xb_alloc_block(u32 size, u32 align);   /* the same for a block the game may give back (4 KB pages, at least align) */
u32 xb_alloc_block_large(u32 size, u32 align);   /* the same from 0x40000000, the console's 64 KB-page range */
u32 xb_free_block(u32 at);           /* gives back an xb_alloc_block block, zeroed for reuse; its size, or 0 when it is not one */
int xb_mem_readable(u32 a, u32 n);   /* every byte of a..a+n-1 is committed guest memory (the kit's page map, xb_mem.c) */

/* One vertex input of a draw, planned once a draw (xb_d3d_vconv.h) and converted by xb_vconv_run (xb_vconv.c, built /O2) */
typedef struct {
    u32 kind, nc, t;            /* kind 0: nc 32-bit floats as written (formats 36 37 38 57), 1: nc half floats, 2: nc 16-bit integers (t: signed, integer), 3: four bytes */
    u32 mode;                   /* 0: at base + i * step, 1: at base + vindex[i] * step, 2: an indexed fetch */
    u32 base, step;             /* modes 0 and 1, the element's offset and the first vertex already in base */
    u32 isrc, icomp, round;     /* mode 2: the index is input isrc's component icomp of this vertex (isrc >= the input's own number: ix), rounded or floored */
    float ix;
    u32 reach, ibase, isz, k;   /* an index under reach is at ibase + index * isz; else slow(k, index) */
    u8 mask[16];                /* the selects as one byte shuffle of the decoded (kind 0: as written) components */
    u32 one[4], absent[4];      /* the lanes a select sets to 1.0; the four floats an absent element gives */
} XbVcEl;
void xb_vconv_run(const XbVcEl *el, unsigned nat, unsigned n, const u32 *vindex, float *v, const float *half, const float (*byte)[256], u32 (*slow)(unsigned k, u32 index));
unsigned xb_vconv_unique(const u32 *vindex, unsigned n, u32 lo, u32 span, u32 *map, u32 *uv, u32 *remap);   /* xb_vconv.c: a draw's distinct vertex numbers (all in lo..lo+span-1) in first-use order into uv, each entry's row into remap; how many */
void xb_mem_uncommit(u32 at, u32 size);   /* decommits those pages, out of the page map first */
#define XB_WW_PAGES 4096u   /* xb_mem_write_gen's largest range in pages (16 MB) */
u64 xb_mem_write_gen(u32 a, u32 n, u64 *now);   /* the highest write generation of a page of a..a+n-1, after collecting the writes there; *now the
                                                 * current one. ~0 when not known (no write watch, or over XB_WW_PAGES): hash again (xb_mem.c) */
void *xb_host_object(u32 guest, unsigned size);   /* the host object standing for the guest structure at an address (made zeroed on first use) */
extern __declspec(thread) u32 tc_sp;
extern __declspec(thread) u32 tc_r13;   /* the guest thread's processor block */
u64 xb_setjmp_landed(void);                /* xb_call.c: the game's setjmp / longjmp (jumps.json); xb_setjmp_slot is declared where setjmp.h is */
void xb_longjmp(const TcArgs *a);
typedef struct { u32 begin, end, funcinfo, parent; const char *bad; } XbEhFn;   /* tables.c: the functions sharing a FuncInfo of the game's frame handler (eh.json) */
extern const XbEhFn xb_eh_fn[];
extern const unsigned xb_eh_fn_count;
typedef struct { u32 begin, end, scope; const char *bad; } XbSeh;   /* tables.c: the __C_specific_handler functions (eh_tables.read_seh) */
extern const XbSeh xb_eh_seh[];
extern const unsigned xb_eh_seh_count;
int xb_eh_raise(const TcArgs *a);         /* xb_call.c (xb_eh.h): RtlRaiseException of a C++ throw; 0 for any other raise */
void xb_tls_new(u32 pcr);

u32 xb_touched_addr(unsigned table_index);   /* the address when the run called that translated function, else 0 */
unsigned xb_touched(void);          /* functions called at least once */
void xb_report(void);               /* the run summary lines of the game log */
void xb_stack_line(char *buf, unsigned size);   /* xb_call.c: this thread's innermost calls, for a log line */
void xb_trace_dump(void);           /* the last calls, for a stop */
void xb_file_reads_over(u32 lo, u32 hi);   /* xb_file.c: the recent reads that landed in [lo, hi), for a stop */
void xb_scan_fill(u32 word);              /* xb_mem.c: the long runs of one word in guest memory, and the reads on each, for a stop */
void xb_vblank_start(u32 device);   /* the console's vertical blank, for a game whose xb_game.h names XB_VBLANK_CALLBACK */
void xb_peek_dump(void);            /* runtime/xb_d3d.c: XB_PEEK, guest words shown at each sample */
void xb_threads_dump(void);         /* every guest thread's call stack, for a run the watchdog ends */
u32 xb_thread_entry(void);           /* runtime/xb_call.c: the guest function this thread started in (XB_WAIT_STATS), 0 if none yet */
u32 xb_thread_entry_of(unsigned long tid);   /* the same for a host thread id */
unsigned xb_thread_ids(unsigned long *out, unsigned max);   /* runtime/xb_call.c */
u32 xb_watch_resolve(const char *chain);                    /* runtime/xb_d3d.c: an XB_WATCH address, pointers followed */
void xb_databp_frame(unsigned frame);                       /* runtime/xb_mem.c: arms an XB_DATABP given as a pointer chain, at its frame */
void xb_databp_thread(void);   /* runtime/xb_mem.c: this thread's hardware breakpoint on XB_DATABP's word, when it is set */
void xb_import_counts_start(void);   /* runtime/xb_call.c: count each import's calls from now, for XB_SAMPLE */
void xb_import_counts_since(void);   /* runtime/xb_call.c: the imports called since the last sample, how often, and their last result */
void xb_file_reads_since(void);   /* runtime/xb_file.c: the reads since the last call, for XB_SAMPLE */
void xb_d3d_times_since(void);    /* runtime/xb_d3d.c: the host time each graphics entry took since the last call, for XB_SAMPLE */


/* The shaders of the image, converted offline (tools/shader_convert.py; gen/shaders.c): the HLSL of each container, how many vertex
 * inputs it fetches, and for a vertex shader the geometry shader that makes the fourth corner of a rectangle list. hlsl is NULL
 * for a container the converter could not do. */
/* addr is the container's address in the image, or for a container the game made at run time XB_MADE_SHADER and a hash of its bytes.
 * For a made vertex shader fetch[k] is 1 + the place of vertex fetch k in its microcode block, where the game patches the format, offset
 * and stride when it binds a declaration; 0 past the last and for the image's shaders, which come with a declaration */
#define XB_MADE_SHADER 0xF0000000u
/* code_off/code_len, gs_off/gs_len: the shader and its rectangle-list geometry shader compiled at build time (gen_xbox.py, shader_convert.py
 * Bytecode), their place in xb_shader_dxbc; a length of 0 has the host compile the HLSL itself */
typedef struct { u32 addr; u32 is_ps, n_inputs, n_interp_in; const char *hlsl, *gs_rect; u32 fetch[32]; const char *why; u32 code_off, code_len, gs_off, gs_len; } XbShader;   /* why: the converter's reason when hlsl is NULL */
extern const XbShader xb_shader_table[];
extern const unsigned xb_shader_count;
const XbShader *xb_shader_find(u32 container);

/* runtime/xb_gpu.c: the host Direct3D 11 device, window and swap chain */
typedef struct XbHostTex XbHostTex;
XbHostTex *xb_gpu_tex(u32 guest, unsigned w, unsigned h, int format, int target, int depth);   /* target 1 a texture a resolve writes, 2 a surface */
XbHostTex *xb_gpu_tex_plain(u32 guest, unsigned w, unsigned h);
XbHostTex *xb_gpu_tex_cube(u32 guest, unsigned size);   /* six square faces, +X -X +Y -Y +Z -Z, sampled through a cube view */
void xb_gpu_upload_face(XbHostTex *t, unsigned face, const void *rgba, unsigned pitch);
int xb_depth_resolve_on(void);   /* XB_DEPTH_RESOLVE=1: a Resolve of the depth surface copies the host's depth (xb_gpu.c) */
XbHostTex *xb_gpu_depth_now(void);   /* the depth texture the draws use now, or 0 */
unsigned xb_mark_frame(void);            /* the frame the scene marker last started at (XB_MARK, xb_d3d.c), or ~0u */
int xb_edram_on(void);                    /* XB_EDRAM=1: colour targets keyed by their EDRAM tiles (xb_d3d_edram.h) */
void xb_edram_cleared(XbHostTex *t, const float *rgba);   /* a whole clear: the tiles hold one value */
void xb_gpu_clear_tex(XbHostTex *t, const float *rgba);   /* XB_EDRAM: clear one host texture */
void xb_edram_written(XbHostTex *t);      /* a draw or clear wrote the host texture: its record is the newest writer of its tiles */
void xb_edram_sync_host(XbHostTex *t);    /* before a resolve reads it: the picture of the tiles' newest writer at the same base */
int xb_depth_on(void);   /* the game's depth states and cull mode on the host, depth-only passes drawn; on by default, XB_DEPTH=0 turns it off */
u32 xb_caller(unsigned up);   /* xb_call.c: the guest function `up` calls out on this thread's stack (0 the innermost), or 0 */
int xb_cube_on(void);   /* XB_CUBE, on unless 0: cube shaders and textures; 0 refuses them as before 2026-11-07 */
void xb_gpu_upload(XbHostTex *t, const void *rgba, unsigned pitch);
unsigned xb_gpu_tex_w(const XbHostTex *t);   /* the size it was made at */
unsigned xb_gpu_tex_h(const XbHostTex *t);
void xb_gpu_bind_target(unsigned i, XbHostTex *t);
void xb_gpu_bind_depth(XbHostTex *t);
void xb_gpu_bind_texture(unsigned i, XbHostTex *t, u32 guest);
void xb_gpu_bind_shaders(u32 vs_container, u32 ps_container);
void xb_gpu_viewport(float x, float y, float w, float h, float zmin, float zmax);
void xb_gpu_game_size(unsigned w, unsigned h);
void xb_gpu_clear(u32 flags, const float *rgba, float z, u32 stencil);
void xb_gpu_clear_rects(u32 flags, const float *rgba, float z, u32 stencil, u32 rects, unsigned n);
void xb_gpu_set_constants(int pixel, unsigned first, unsigned count, const float *v);
const float *xb_gpu_constants(int pixel);   /* the host's registers as the next draw sends them (XB_CONST_CHECK) */
void xb_gpu_draw(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata);
void xb_gpu_draw_kept(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata, void **keep);   /* keep: where the host's buffer of these vertices stays between draws (made when 0 there), or 0 */
void xb_gpu_draw_indexed(unsigned prim, unsigned nrows, unsigned nattr, const float *vdata, unsigned nidx, const u32 *idx, void **keep, void **ikeep);   /* nrows rows drawn by nidx 32-bit indices into them (XB_VTX_UNIQUE); ikeep: as keep, for the index buffer */
void xb_gpu_buffer_free(void *vb);   /* a buffer xb_gpu_draw_kept made */
void xb_gpu_depth_word(u32 word);    /* the draw's device depth-control word (XB_DEV_DEPTHCONTROL), read at its entry or as recorded */
void xb_gpu_dev_words(u32 colour_mask, u32 colour_control, u32 mode_control);   /* its colour mask, colour control and mode control words (XB_DEV_COLORMASK ..), the same way */
int xb_gpu_draw_nothing(void);       /* the draw bound now can change nothing (no pixel shader, z off): counted as drawn, to be left before its vertices are read */
int xb_draw_fast(void);              /* xb_gpu.c: XB_DRAW_FAST=0 turns the draw path's shortcuts off */
void xb_gpu_resolve(XbHostTex *src, XbHostTex *dst, int exp_bias);
void xb_gpu_target_format(unsigned i, int fmt);   /* the guest colour format of the target in slot i (RB_COLOR_INFO bits 16..19, -1 unknown): what its texels can hold */
void xb_gpu_target_bias(unsigned i, int exp_bias);   /* the exponent bias of the target in slot i (RB_COLOR_INFO), used with XB_COGBOX_RT_BIAS=1 */
void xb_gpu_present(XbHostTex *front);
void xb_gpu_gamma_ramp(const u32 *pairs);   /* the display's piecewise-linear gamma ramp for the next presents, 384 entries (xb_xenos_gamma.h), or 0 for none */
/* The frame capture (port/base/frame_capture.h, xb_gpu_fcap.h): a draw the host makes or leaves (why 0: drawn), a draw the d3d layer leaves, a resolve it leaves */
void xb_fcap_draw(unsigned prim, unsigned nverts, unsigned nattr, const char *why);
void xb_fcap_skip(const char *why, u32 vs, u32 ps);
void xb_fcap_stream0(u32 kit_at, u32 device_at, u32 device_bytes, u32 stride, u32 fetch_stride, const u32 *streams0_9);   /* stream 0 for the next draw record: the kit's and the device's (stream0_at) */
void xb_fcap_resolve_skip(const char *why, u32 dst);
/* xb_flow.c: the game's screen flow (movies, screens, loading) in the log and the title bar */
unsigned xb_flow_file_count(void);      /* game files opened so far */
void xb_flow_file(const char *name);      /* a file the game opened (its guest name) */
void xb_flow_close(const char *path);     /* one it closed (the host path) */
void xb_flow_movie_picture(void);         /* the movie quad (XB_MOVIE_PS) drew in this frame */
void xb_flow_frame(void);                 /* a frame was shown */
int xb_flow_auto_set(void);               /* XB_AUTO_A is set: pad 1 is there for its presses */
int xb_flow_auto_a(void);                 /* XB_AUTO_A holds A now */
void xb_flow_call(u32 target, const TcArgs *a);   /* XB_FLOW_CALLS: a call into the game's script functions */
void xb_prof_start(void);   /* xb_prof.c: XB_PROF, the real-clock sampler */
void xb_prof_write(void);
int xb_gpu_capture(const char *path);   /* the frame as shown, to a 24-bit BMP; 0 on failure */
void xb_tick_end(void);                 /* xb_main.c: a shown frame's end, the base's per-frame work and the wait for the blank */
void xb_pads_poll(void);                /* console_x360.c: samples the host's pads */
typedef struct XbPadState { u16 buttons; u8 lt, rt; s16 lx, ly, rx, ry; } XbPadState;   /* XInput's */
int xb_pad_state(u32 user, XbPadState *out);   /* 0: no pad at that user */
void xb_gpu_dump(XbHostTex *t, const char *path);
XbHostTex *xb_gpu_main_target(void);
unsigned xb_gpu_draw_count(int frame);   /* xb_gpu_draw.h: 1 the frame being shown, 0 all since the start */
unsigned xb_gpu_frame_count(void);

#endif
