/* The Saturn's save states (base/save_state.h): its blocks, its safe point and the resume after a load. Guest state only,
 * never the game's own save: the backup RAM (sat_bup.c, the game's BUP files) is not in a state, so saving or loading
 * one never writes it.
 *
 * The safe point is a call (sat_call.c tc_call) on the master SH-2, taken while a request waits: not in an interrupt
 * handler, a kit hook (a movie, the opening logos, a kit menu, a call's feature hooks) or untranslated code the
 * interpreter runs, no recursive function inside itself, and every open frame's return address and stack pointer known
 * (sat_call_unsafe says which fails). The slave SH-2 must be idle in its program's entry loop: on a load it starts that
 * entry again, as SSHON leaves it (Dragon Force's slave keeps nothing between commands). A KRNL task game (sat_task.c)
 * registers no provider: File's entries show greyed. A request no call can take in SAT_STATE_GIVE_UP vertical blanks is
 * dropped, and the log says why.
 *
 * A load leaves everything the game thread was running (a longjmp to sat_main.c's game thread), then runs the call
 * that was pending at the safe point natively, and the frames that were open resume in the SH-2 interpreter at their
 * return addresses (sat_interp.c sat_interp_resume): their code calls out to translated C as interpreted code always
 * does, so only the outer loops run interpreted until they return. A save made after a load works the same way.
 *
 *   SH2      the pending call (target, arguments, return address, the caller's r15); each CPU's registers and counters;
 *            the master's open frames, outermost first: the function, the return address its caller's call handed it
 *            (site), the caller's r8..r14 (saved), popped, and the frame's entry sp (ftop; bit 0: the call gave no sp)
 *   WRAMH, WRAML   work RAM; VDP1 (VRAM, both frame buffers, registers); VDP2 (VRAM, colour RAM, registers);
 *   SCSP     sound RAM and registers;
 *   DEV      the devices' own state, each from its file (k_dev): the clock, the SCU, the SMPC, the CD block and its
 *            buffer, the 68000 and the SCSP's timers and voices, the on-chip registers and caches, the BIOS' tables, the
 *            VDP1's frame state and the interpreter's VBR
 * The identity is the state layout and the translated functions' table: a state from a build with other code at other
 * addresses is refused, and a load checks every block's length before it changes anything.
 * Not in a state: the presentation (the picture's caches, wide twins, in-between frames: made again each frame), the
 * host's sound and CD audio output, the session recording (a load is not recorded yet), the movie player (refused). */
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_vdp.h"
#include "sat_scsp.h"
#include "sat_sys.h"
#include "save_state.h"

SatCpu *sat_cpu_cur(void); /* sat_call.c */

#define SAT_STATE_LAYOUT 2u
enum { SAT_STATE_GIVE_UP = 600 }; /* vertical blanks a request may wait for a safe point (10 s at 60 Hz) */
enum { ST_MEASURE, ST_SAVE, ST_LOAD };
struct SatSt {
    int mode;
    SaveStateOut *out;
    const u8 *in;
    u32 n, len;
};

void sat_st(SatSt *io, void *p, u32 len)
{
    if (io->mode == ST_SAVE) save_state_put(io->out, p, len);
    else if (io->mode == ST_LOAD && io->n + len <= io->len) memcpy(p, io->in + io->n, len);
    io->n += len;
}
int sat_st_loading(const SatSt *io) { return io->mode == ST_LOAD; }

typedef void (*Visit)(SatSt *io);
static const Visit k_dev[] = {sat_sys_state, sat_scu_dev_state, sat_smpc_state, sat_cd_state, cdb_state,     sat_scsp_state,
                              sat_mem_state, sat_bios_state, sat_vdp_state, sat_interp_state};
enum { NDEV = sizeof k_dev / sizeof k_dev[0] };

static struct {
    u32 target, site, csp;
    TcArgs a;
} g_pend;                     /* the call the safe point was at: a save writes it, a load runs it first */
jmp_buf sat_state_jb;         /* sat_main.c's game thread: where a load leaves everything to */
static int g_ready, g_at_point;
static long g_req_seen;
static u32 g_req_vb;
static const char *g_why[8]; /* while a request waits: each reason a call could not take it, and how many calls */
static u32 g_why_n[8];

static void pend_visit(SatSt *io)
{
    sat_st(io, &g_pend.target, 4), sat_st(io, &g_pend.site, 4), sat_st(io, &g_pend.csp, 4);
    sat_st(io, &g_pend.a, sizeof g_pend.a);
}
static u32 measure(Visit v)
{
    SatSt io = {ST_MEASURE, NULL, NULL, 0, 0};
    v(&io);
    return io.n;
}
static void dev_visit(SatSt *io)
{
    for (int i = 0; i < NDEV; i++) k_dev[i](io);
}

static int sat_state_save(SaveStateOut *o)
{
    SatSt io = {ST_SAVE, o, NULL, 0, 0};
    if (!g_at_point) return 0;
    if (!save_state_begin(o, "SH2", 2)) return 0;
    pend_visit(&io), sat_call_state(&io);
    if (!save_state_end(o)) return 0;
    if (!save_state_begin(o, "DEV", 1)) return 0;
    dev_visit(&io);
    return save_state_end(o) && save_state_block(o, "WRAMH", 1, sat_ram_h(), SAT_WRAM_SIZE) &&
           save_state_block(o, "WRAML", 1, sat_ram_l(), SAT_WRAM_SIZE) && save_state_begin(o, "VDP1", 1) &&
           save_state_put(o, sat_v1_vram, sizeof sat_v1_vram) && save_state_put(o, sat_v1_fb, sizeof sat_v1_fb) &&
           save_state_put(o, sat_v1_reg, sizeof sat_v1_reg) && save_state_end(o) && save_state_begin(o, "VDP2", 1) &&
           save_state_put(o, sat_v2_vram, sizeof sat_v2_vram) && save_state_put(o, sat_v2_cram, sizeof sat_v2_cram) &&
           save_state_put(o, sat_v2_reg, sizeof sat_v2_reg) && save_state_end(o) && save_state_begin(o, "SCSP", 1) &&
           save_state_put(o, sat_sound_ram_bytes, sizeof sat_sound_ram_bytes) &&
           save_state_put(o, sat_scsp_regs, sizeof sat_scsp_regs) && save_state_end(o);
}

/* Every block there at its length before anything changes; then all of it, then the kit's own fix-ups. */
static int sat_state_load(const SaveStateIn *in)
{
    static const struct { const char *tag; u32 len; } need[] = {
        {"WRAMH", SAT_WRAM_SIZE}, {"WRAML", SAT_WRAM_SIZE},
        {"VDP1", sizeof sat_v1_vram + sizeof sat_v1_fb + sizeof sat_v1_reg},
        {"VDP2", sizeof sat_v2_vram + sizeof sat_v2_cram + sizeof sat_v2_reg},
        {"SCSP", sizeof sat_sound_ram_bytes + sizeof sat_scsp_regs},
    };
    const u8 *b[5], *sh2, *dev;
    u32 len, sh2_len, dev_len, d, pend = measure(pend_visit);
    u32 fixed = measure(sat_call_state) - ((u32)g_cpu[MASTER].depth + 1u) * SAT_ST_FRAME;
    if (!g_at_point) return 0;
    for (unsigned i = 0; i < 5; i++)
        if (!(b[i] = save_state_find(in, need[i].tag, 1, &len)) || len != need[i].len) return 0;
    if (!(dev = save_state_find(in, "DEV", 1, &dev_len)) || dev_len != measure(dev_visit)) return 0;
    if (!(sh2 = save_state_find(in, "SH2", 2, &sh2_len)) || sh2_len < pend + 4u) return 0;
    memcpy(&d, sh2 + pend, 4);
    if (d < 1u || d > MAXDEPTH || sh2_len != pend + fixed + (d + 1u) * SAT_ST_FRAME) return 0;

    memcpy(sat_ram_h(), b[0], SAT_WRAM_SIZE), memcpy(sat_ram_l(), b[1], SAT_WRAM_SIZE);
    memcpy(sat_v1_vram, b[2], sizeof sat_v1_vram), memcpy(sat_v1_fb, b[2] + sizeof sat_v1_vram, sizeof sat_v1_fb);
    memcpy(sat_v1_reg, b[2] + sizeof sat_v1_vram + sizeof sat_v1_fb, sizeof sat_v1_reg);
    memcpy(sat_v2_vram, b[3], sizeof sat_v2_vram), memcpy(sat_v2_cram, b[3] + sizeof sat_v2_vram, sizeof sat_v2_cram);
    memcpy(sat_v2_reg, b[3] + sizeof sat_v2_vram + sizeof sat_v2_cram, sizeof sat_v2_reg);
    memcpy(sat_sound_ram_bytes, b[4], sizeof sat_sound_ram_bytes);
    memcpy(sat_scsp_regs, b[4] + sizeof sat_sound_ram_bytes, sizeof sat_scsp_regs);
    {
        SatSt io = {ST_LOAD, NULL, dev, 0, dev_len};
        dev_visit(&io);
    }
    {
        SatSt io = {ST_LOAD, NULL, sh2, 0, sh2_len};
        pend_visit(&io), sat_call_state(&io);
    }
    sat_body_nest = 0;
    sat_call_loaded();
    sat_interp_loaded();
    port_log("state: loaded: %u open frame(s), the pending call %08x returns to %08x\n", d, g_pend.target, g_pend.site);
    return 1;
}

static const char *sat_state_identity(void)
{
    static char id[64];
    if (!id[0]) { /* FNV-1a over the translated functions' addresses and frames */
        u32 h = 2166136261u;
        for (int i = 0; i < sat_nfns; i++) {
            u32 w[2];
            w[0] = sat_fns[i].addr, w[1] = sat_fns[i].frame;
            for (unsigned b = 0; b < sizeof w; b++) h = (h ^ ((const u8 *)w)[b]) * 16777619u;
        }
        snprintf(id, sizeof id, "saturn state %u; %d functions %08x", SAT_STATE_LAYOUT, sat_nfns, h);
    }
    return id;
}

static const SaveStateProvider k_provider = {sat_state_identity, sat_state_save, sat_state_load};

void sat_state_register(void)
{
    if (sat_task_on) {
        port_log("state: not offered: the game switches KRNL tasks (sat_task.c), whose contexts a state does not hold yet\n");
        return;
    }
    save_state_provider(&k_provider);
}

void sat_state_ready(void) { g_ready = 1; }

/* tc_call, while a request waits: here if it can be done, else the next call is asked */
void sat_state_point(u32 target, const TcArgs *a, u32 site, u32 csp)
{
    long req = save_state_pending;
    const char *why;
    int loaded;
    if (!req) return;
    why = !g_ready ? "the game has not started" : sat_call_unsafe((req >> 8) == SAVE_STATE_SAVE, site, csp);
    if (why) {
        u32 vb = sat_vblank_count();
        int i;
        if (g_req_seen != req) g_req_seen = req, g_req_vb = vb, memset(g_why_n, 0, sizeof g_why_n);
        for (i = 0; i < 8 && g_why_n[i] && g_why[i] != why; i++) {}
        if (i < 8) g_why[i] = why, g_why_n[i]++;
        if (vb - g_req_vb >= SAT_STATE_GIVE_UP) {
            char text[600];
            int n = snprintf(text, sizeof text, "no moment to do it in %u vertical blanks (calls: ", (u32)SAT_STATE_GIVE_UP);
            for (i = 0; i < 8 && g_why_n[i] && n > 0 && n < (int)sizeof text; i++)
                n += snprintf(text + n, sizeof text - (size_t)n, "%s%s x%u", i ? "; " : "", g_why[i], g_why_n[i]);
            if (n > 0 && n < (int)sizeof text - 1) text[n] = ')', text[n + 1] = 0;
            save_state_drop(text);
            g_req_seen = 0;
        }
        return;
    }
    g_req_seen = 0;
    g_pend.target = target, g_pend.site = site, g_pend.csp = csp, g_pend.a = *a;
    g_at_point = 1;
    loaded = save_state_poll();
    g_at_point = 0;
    if (loaded) longjmp(sat_state_jb, 1); /* the game thread resumes the state (sat_state_resume) */
}

/* After a load: the pending call natively, then the open frames from its return address */
void sat_state_resume(void)
{
    SatCpu *m = sat_cpu_cur();
    int k = m->depth;
    u32 r;
    sat_ret = g_pend.site;
    r = tc_call_sp(g_pend.target, &g_pend.a, g_pend.csp);
    /* frame k's calls pass its r15 or not as the pending call did (ftop[k + 1] bit 0: resume_call reads it) */
    m->ftop[k + 1] = (m->ftop[k + 1] & ~1u) | (g_pend.csp ? 0u : 1u);
    sat_interp_resume(r, g_pend.site, g_pend.csp);
}
