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

/* A library the kit does not translate (port/boundary.json): a call to any address in it is a host call (xb_lib_entry) or a stop. */
typedef struct { u32 lo, hi; } XbRange;
extern const XbRange xb_boundary[];
extern const unsigned xb_boundary_count;
int xb_lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1);   /* runtime/xb_d3d.c: 1 when the entry is implemented */

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
#define XB_XACT_BASE 0x7FFC0000u   /* the kit's XACT methods: never mapped, port/boundary.json makes them host calls */
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
void xb_sound_stop_all(void);
void xb_sound_mix(s32 *mix, u32 frames);
int xb_sound_up(void);
void xb_sound_counts(u32 *pcm, u32 *xma);
void xb_sound_tick(void);           /* xb_sound_out.c */
void xb_sound_close(void);

/* runtime/xb_state.c: the device's render and sampler states */
enum { XB_CLASS_RS = 6, XB_CLASS_SS = 7, XB_RS_COUNT = 101, XB_SS_COUNT = 20, XB_SAMPLERS = 26 };
unsigned xb_frame_number(void);   /* frames swapped so far */
void xb_state_init(u32 device);
int xb_state_call(u32 target, const TcArgs *a, u64 *r3);
void xb_ss_set(unsigned sampler, unsigned slot, u32 value);
u32 xb_rs(unsigned state);
u32 xb_ss(unsigned sampler, unsigned state);
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
void xb_token_set_event(u32 token);
int xb_file_import(unsigned ord, const TcArgs *a, u64 *r3);   /* runtime/xb_file.c: the kernel's file calls */

/* "No caller, no code": anything missing logs what it is and ends the process with exit code 3; NAME.stop holds the one
 * line gen_xbox.py reads next ("missing_function 82001234", "import 00020130", "fault 7fc86110"). */
void xb_stop(const char *kind, const char *detail, u32 value);

int xb_mem_init(const char *image_path, u32 load_addr);
u32 xb_alloc(u32 size);              /* committed, zeroed guest memory (64 KB aligned); a stop when the reserve is used up */
void *xb_host_object(u32 guest, unsigned size);   /* the host object standing for the guest structure at an address (made zeroed on first use) */
extern __declspec(thread) u32 tc_sp;
extern __declspec(thread) u32 tc_r13;   /* the guest thread's processor block */

u32 xb_touched_addr(unsigned table_index);   /* the address when the run called that translated function, else 0 */
unsigned xb_touched(void);          /* functions called at least once */
void xb_report(void);               /* the run summary lines of the game log */
void xb_trace_dump(void);           /* the last calls, for a stop */


/* The shaders of the image, converted offline (tools/shader_convert.py; gen/shaders.c): the HLSL of each container, how many vertex
 * inputs it fetches, and for a vertex shader the geometry shader that makes the fourth corner of a rectangle list. hlsl is NULL
 * for a container the converter could not do. */
typedef struct { u32 addr; u32 is_ps, n_inputs, n_interp_in; const char *hlsl, *gs_rect; } XbShader;
extern const XbShader xb_shader_table[];
extern const unsigned xb_shader_count;
const XbShader *xb_shader_find(u32 container);

/* runtime/xb_gpu.c: the host Direct3D 11 device, window and swap chain */
typedef struct XbHostTex XbHostTex;
XbHostTex *xb_gpu_tex(u32 guest, unsigned w, unsigned h, int format, int target, int depth);
XbHostTex *xb_gpu_tex_plain(u32 guest, unsigned w, unsigned h);
void xb_gpu_upload(XbHostTex *t, const void *rgba, unsigned pitch);
void xb_gpu_bind_target(unsigned i, XbHostTex *t);
void xb_gpu_bind_depth(XbHostTex *t);
void xb_gpu_bind_texture(unsigned i, XbHostTex *t, u32 guest);
void xb_gpu_bind_shaders(u32 vs_container, u32 ps_container);
void xb_gpu_viewport(float x, float y, float w, float h, float zmin, float zmax);
void xb_gpu_game_size(unsigned w, unsigned h);
void xb_gpu_clear(u32 flags, const float *rgba, float z, u32 stencil);
void xb_gpu_set_constants(int pixel, unsigned first, unsigned count, const float *v);
void xb_gpu_draw(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata);
void xb_gpu_resolve(XbHostTex *src, XbHostTex *dst);
void xb_gpu_present(XbHostTex *front);
int xb_gpu_capture(const char *path);   /* the frame as shown, to a 24-bit BMP; 0 on failure */
void xb_tick_end(void);                 /* xb_main.c: a shown frame's end, the base's per-frame work and the wait for the blank */
void xb_pads_poll(void);                /* console_x360.c: samples the host's pads */
typedef struct XbPadState { u16 buttons; u8 lt, rt; s16 lx, ly, rx, ry; } XbPadState;   /* XInput's */
int xb_pad_state(u32 user, XbPadState *out);   /* 0: no pad at that user */
void xb_gpu_dump(XbHostTex *t, const char *path);
XbHostTex *xb_gpu_main_target(void);
unsigned xb_gpu_draw_count(void);
unsigned xb_gpu_frame_count(void);

#endif
