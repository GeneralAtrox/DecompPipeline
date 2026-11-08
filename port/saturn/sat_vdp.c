/* The video chips' memories and registers: VDP1 (VRAM 0x05c00000, framebuffer 0x05c80000, registers 0x05d00000), VDP2
 * (VRAM 0x05e00000, colour RAM 0x05f00000, registers 0x05f80000). Big-endian byte arrays; the registers that have
 * an effect when read or written (VDP1 EDSR/COPR/PTMR, VDP2 TVSTAT/HCNT/VCNT) are handled here. The drawing
 * itself (command list, scroll planes) is sat_vdp1.c / sat_vdp2.c. */
#include <stdio.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_part.h"

u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000], sat_v2_vram[0x80000], sat_v2_cram[0x1000];
u8 sat_v1_reg[0x20], sat_v2_reg[0x200];
static int g_vblank_flag, g_v1_draw_end, g_fb_swapped, g_manual_pending, g_erase_armed, g_auto_draw;

enum { FBCR_FCT = 1u, FBCR_FCM = 2u, TVMR_VBE = 8u }; /* FCM set: manual, and FCT says change (1) or erase (0) */

static u32 get(const u8 *m, u32 o, u32 size)
{
    u32 v = 0;
    for (u32 i = 0; i < size; i++) v = (v << 8) | m[o + i];
    return v;
}
static void put(u8 *m, u32 o, u32 size, u32 value)
{
    for (u32 i = 0; i < size; i++) m[o + i] = (u8)(value >> (8u * (size - 1u - i)));
}

static u32 v1vram_r(u32 p, u32 s) { return get(sat_v1_vram, p - 0x05c00000u, s); }
static void v1vram_w(u32 p, u32 s, u32 v) { put(sat_v1_vram, p - 0x05c00000u, s, v); }
static u32 v1fb_r(u32 p, u32 s) { return get(sat_v1_fb[1 - g_fb_swapped], (p - 0x05c80000u) & 0x3ffffu, s); }
static void v1fb_w(u32 p, u32 s, u32 v)
{
    put(sat_v1_fb[1 - g_fb_swapped], (p - 0x05c80000u) & 0x3ffffu, s, v);
    sat_wide_cpu(sat_v1_fb[1 - g_fb_swapped], (p - 0x05c80000u) & 0x3ffffu, s, v); /* and its wide twin (sat_wide.c) */
}
static u32 v2vram_r(u32 p, u32 s) { return get(sat_v2_vram, p - 0x05e00000u, s); }
u32 sat_vwatch_lo, sat_vwatch_hi, sat_vwatch_vb; /* --vwatch LO,HI[,VB]: from vertical blank VB, log guest writes that change VDP2 VRAM [LO,HI), with the call chain (a test aid) */
static u32 vw_last_vb, vw_n, vw_lo, vw_hi, vw_nvb;
static void vwatch_flush(void)
{
    if (vw_n) port_log("vwatch: vb %u: %u changing writes in %05x..%05x\n", vw_last_vb, vw_n, vw_lo, vw_hi);
    vw_n = 0;
}
static void v2vram_w(u32 p, u32 s, u32 v)
{
    u32 o = p - 0x05e00000u;
    if (sat_vwatch_hi && sat_vblank_count() >= sat_vwatch_vb && o >= sat_vwatch_lo && o < sat_vwatch_hi && get(sat_v2_vram, o, s) != v) {
        if (sat_vblank_count() != vw_last_vb) { /* one line per blank that changed the range, with the first writer's call chain */
            vwatch_flush();
            vw_lo = 0xfffff, vw_hi = 0;
            vw_last_vb = sat_vblank_count();
            port_log("vwatch: vb %u: first change %05x = %x (size %u)\n", vw_last_vb, o, v, s);
            if (vw_nvb++ < 40) sat_where();
        }
        vw_n++;
        if (o < vw_lo) vw_lo = o;
        if (o > vw_hi) vw_hi = o;
    }
    put(sat_v2_vram, o, s, v);
}
/* colour RAM as the CPU sees it: in mode 0 (RGB555, 1024 colours: RAMCTL bits 13-12 clear) address bit 11 is not decoded, so the upper 2 KB mirrors the lower */
static u32 cram_off(u32 p) { return (p - 0x05f00000u) & (get(sat_v2_reg, 0x0e, 2) & 0x3000u ? 0xfffu : 0x7ffu); }
static u32 cram_r(u32 p, u32 s) { return get(sat_v2_cram, cram_off(p), s); }
static void cram_w(u32 p, u32 s, u32 v)
{
    put(sat_v2_cram, cram_off(p), s, v);
}

u8 *sat_v1_draw_fb(void) { return sat_v1_fb[1 - g_fb_swapped]; }
const u8 *sat_v1_display_fb(void) { return sat_v1_fb[g_fb_swapped]; }

/* EWDR over the erase window (EWLR upper left, EWRR lower right) of a framebuffer */
static void v1_erase(u8 *fb)
{
    u32 data = get(sat_v1_reg, 6, 2), ul = get(sat_v1_reg, 8, 2), lr = get(sat_v1_reg, 10, 2);
    u32 x0 = ((ul >> 9) & 0x3fu) * 8u, y0 = ul & 0x1ffu, x1 = ((lr >> 9) & 0x7fu) * 8u, y1 = lr & 0x1ffu;
    for (u32 y = y0; y <= y1 && y < 256u; y++)
        for (u32 x = x0; x < x1 && x < 512u; x++) put(fb, (y * 512u + x) * 2u, 2, data);
    sat_wide_erase(fb, data, x0, y0, x1, y1); /* and its wide twin's (sat_wide.c) */
}

void sat_v1_fb_flip(void) { g_fb_swapped ^= 1; }
void sat_v1_erase_draw(void) { v1_erase(sat_v1_draw_fb()); }

static void v1_start_draw(void)
{
    sat_v1_draw();
    sat_inb_drawn(sat_v1_draw_fb());
    g_v1_draw_end = 1;
    sat_scu_raise(13u); /* sprite draw end */
}

/* the frame change, at the start of the display period. FCM clear: every frame swaps and erases. FCM set: what FBCR
 * asked since the last one: FCT set swaps (and with VBE erases the shown framebuffer first), FCT clear erases the shown
 * framebuffer behind the beam, so it is still on the screen for one more picture and is clean when it comes back to be
 * drawn into (SGL's 2-vertical-blank frame writes 2, then 3). PTMR 2 draws the list at a swap and only at a swap. */
static void v1_frame_change(void)
{
    u32 fbcr = get(sat_v1_reg, 2, 2), tvmr = get(sat_v1_reg, 0, 2);
    int change = !(fbcr & FBCR_FCM) || (g_manual_pending && (fbcr & FBCR_FCT));
    if (g_erase_armed || (change && (fbcr & FBCR_FCM) && (tvmr & TVMR_VBE))) v1_erase(sat_v1_fb[g_fb_swapped]);
    g_erase_armed = g_manual_pending && (fbcr & FBCR_FCM) && !(fbcr & FBCR_FCT);
    g_manual_pending = 0;
    if (!change) return;
    g_fb_swapped ^= 1;
    if (!(fbcr & FBCR_FCM)) v1_erase(sat_v1_draw_fb());
    if (g_auto_draw) v1_start_draw();
}

static u32 v1reg_r(u32 p, u32 s)
{
    u32 o = (p - 0x05d00000u) & 0x1fu;
    if (o == 0x10u) return (g_v1_draw_end ? 2u : 0u) | 1u; /* EDSR: CEF (bit1), BEF (bit0) */
    if (o == 0x12u) return 0u;                             /* LOPR */
    if (o == 0x14u) return 0u;                             /* COPR */
    if (o == 0x16u) return 0x0000u;                        /* MODR */
    return get(sat_v1_reg, o, s);
}
static void v1reg_w(u32 p, u32 s, u32 v)
{
    u32 o = (p - 0x05d00000u) & 0x1fu;
    put(sat_v1_reg, o, s, v);
    if ((o == 0x02u || o == 0x03u) && (get(sat_v1_reg, 2, 2) & FBCR_FCM)) g_manual_pending = 1; /* FBCR: manual, acted on at the next frame */
    if (o == 0x04u || o == 0x05u) { /* PTMR: 1 starts drawing at once, 2 at the frame change */
        u32 ptmr = get(sat_v1_reg, 4, 2) & 3u;
        g_auto_draw = ptmr == 2u;
        if (ptmr == 1u) v1_start_draw();
    }
}

static u32 v2reg_r(u32 p, u32 s)
{
    u32 o = (p - 0x05f80000u) & 0x1ffu, total = 313u, along = 0, line, active, tvmd = get(sat_v2_reg, 0, 2);
    if (o == 0x04u || o == 0x08u || o == 0x0au) {
        sat_irq_poll();
        line = sat_scanline(total, &along);
        active = ((tvmd >> 4) & 3u) == 0 ? 224u : ((tvmd >> 4) & 3u) == 1 ? 240u : 256u;
        if (o == 0x04u) /* TVSTAT: VBLANK bit3, HBLANK bit2, ODD bit1, PAL bit0 */
            return (line >= active ? 8u : 0u) | (along >= 220u ? 4u : 0u) | (((tvmd >> 6) & 3u) >= 2u ? (sat_vblank_count() & 1u) << 1 : 2u) | 1u;
        return o == 0x08u ? (along * 4u) : line; /* HCNT, VCNT */
    }
    return get(sat_v2_reg, o, s);
}
static void v2reg_w(u32 p, u32 s, u32 v)
{
    u32 o = (p - 0x05f80000u) & 0x1ffu;
    put(sat_v2_reg, o, s, v);
}

/* what the video chips are set to, for the log (--shot, and a bug report) */
u32 sat_peek_addr[16]; /* --peek A,B,...: words of guest memory logged with each shot's register report */
int sat_peek_n;

/* --vwatch 25f80000,25f80200,VB: at vertical blank VB, log every non-zero VDP2 register (offset:value), to read the priorities, plane enables and colour modes a screen runs with (a test aid) */
void sat_v2_dump_regs(void)
{
    char line[2048];
    int n = 0;
    for (u32 o = 0; o < 0x120u && n < 1900; o += 2)
        if (get(sat_v2_reg, o, 2)) n += snprintf(line + n, sizeof line - (size_t)n, " %x:%04x", o, get(sat_v2_reg, o, 2));
    port_log("vdp2 registers at vb %u:%s\n", sat_vblank_count(), line);
}

void sat_vdp_report(void)
{
    static const u16 offs[] = {0x00, 0x0e, 0x10, 0x12, 0x14, 0x16, 0x18, 0x1a, 0x1c, 0x1e, 0xc0, 0xc2, 0xc4, 0xc6, 0xc8, 0xca, 0xcc, 0xce, 0xd0, 0xd2, 0xd4, 0xd6, 0xd8, 0xda, 0xdc, 0xde, 0x20, 0x28, 0x2a, 0x2c, 0x30, 0x32, 0x34, 0x36, 0x38, 0x3a, 0x3c, 0x40, 0x42, 0x44, 0x46, 0x48, 0x4a, 0x4c, 0x4e,
                               0x70, 0x72, 0x74, 0x76, 0x78, 0x7c, 0x80, 0x84, 0x88, 0x8c, 0x90, 0x92, 0x94, 0x96, 0x98, 0x9a, 0xac, 0xae, 0xb0, 0xe0, 0xe4, 0xe6, 0xec,
                               0xf0, 0xf2, 0xf4, 0xf6, 0xf8, 0xfa, 0xfc, 0x100, 0x108, 0x10a, 0x110, 0x112};
    char line[1536];
    int n = 0, cmds = 0;
    u32 cmd = 0;
    for (size_t i = 0; i < sizeof offs / sizeof offs[0]; i++) n += snprintf(line + n, sizeof line - (size_t)n, " %03x=%04x", offs[i], get(sat_v2_reg, offs[i], 2));
    port_log("vdp2 regs:%s\n", line);
    vwatch_flush();
    sat_v2_hist_report();
    for (int i = 0; i < sat_peek_n; i++) port_log("peek: %08x = %08x\n", sat_peek_addr[i], sat_peek32(sat_peek_addr[i]));
    n = snprintf(line, sizeof line, "vdp1 TVMR %04x FBCR %04x PTMR %04x EWDR %04x EWLR %04x EWRR %04x; front fb %d", get(sat_v1_reg, 0, 2), get(sat_v1_reg, 2, 2),
                 get(sat_v1_reg, 4, 2), get(sat_v1_reg, 6, 2), get(sat_v1_reg, 8, 2), get(sat_v1_reg, 10, 2), g_fb_swapped);
    port_log("%s\n", line);
    {
        int nz[16];
        memset(nz, 0, sizeof nz);
        for (u32 i = 0; i < 0x80000u; i++) nz[i >> 15] += sat_v2_vram[i] != 0;
        n = 0;
        for (int i = 0; i < 16; i++) n += snprintf(line + n, sizeof line - (size_t)n, " %05x:%d", i * 0x8000, nz[i]);
        port_log("vdp2 vram nonzero bytes per 32K:%s\n", line);
        n = 0;
        for (int b = 0; b < 16; b++) {
            int c = 0;
            for (u32 i = 0; i < 128u; i++) c += get(sat_v2_cram, (u32)(b * 128 + (int)i) * 2u, 2) != 0;
            n += snprintf(line + n, sizeof line - (size_t)n, " %d:%d", b * 128, c);
        }
        port_log("vdp2 cram nonzero entries per 128:%s\n", line);
        for (int bank = 0; bank < 16; bank++) {
            n = 0;
            for (int i = 0; i < 16; i++) n += snprintf(line + n, sizeof line - (size_t)n, " %04x", get(sat_v2_cram, (u32)(bank * 16 + i) * 2u, 2));
            port_log("vdp2 cram[%d..%d]:%s\n", bank * 16, bank * 16 + 15, line);
        }
        for (int f = 0; f < 2; f++) {
            int c = 0;
            for (u32 i = 0; i < 0x40000u; i++) c += sat_v1_fb[f][i] != 0;
            port_log("vdp1 fb%d nonzero bytes %d\n", f, c);
        }
    }
    while (cmds < 12 && cmd < 0x80000u) {
        u32 ctrl = get(sat_v1_vram, cmd, 2);
        port_log("vdp1 cmd %05x: ctrl %04x link %04x pmod %04x colr %04x srca %04x size %04x A %04x,%04x B %04x,%04x C %04x,%04x D %04x,%04x grda %04x\n", cmd, ctrl,
                 get(sat_v1_vram, cmd + 2, 2), get(sat_v1_vram, cmd + 4, 2), get(sat_v1_vram, cmd + 6, 2), get(sat_v1_vram, cmd + 8, 2), get(sat_v1_vram, cmd + 10, 2),
                 get(sat_v1_vram, cmd + 12, 2), get(sat_v1_vram, cmd + 14, 2), get(sat_v1_vram, cmd + 16, 2), get(sat_v1_vram, cmd + 18, 2), get(sat_v1_vram, cmd + 20, 2),
                 get(sat_v1_vram, cmd + 22, 2), get(sat_v1_vram, cmd + 24, 2), get(sat_v1_vram, cmd + 26, 2), get(sat_v1_vram, cmd + 28, 2));
        if (ctrl & 0x8000u) break;
        cmd += 0x20u;
        cmds++;
    }
}

/* the frame boundary: the vertical blank starts */
void sat_vblank_in(void)
{
    g_vblank_flag = 1;
    sat_perf_vblank();
    sat_sample();
    sat_vblank_counted();
    sat_frame_limit_check();
    sat_present();
    v1_frame_change();
    sat_cd_frame();
    sat_part_frame();
    sat_scsp_frame();
    g_vblank_flag = 0;
    sat_scu_raise(0u); /* VBlank-in */
    sat_scu_raise(1u); /* VBlank-out */
}

void sat_vdp_init(void)
{
    memset(sat_v1_vram, 0, sizeof sat_v1_vram);
    sat_map(0x05c00000u, 0x05c80000u, "VDP1-VRAM", v1vram_r, v1vram_w);
    sat_map(0x05c80000u, 0x05d00000u, "VDP1-FB", v1fb_r, v1fb_w);
    sat_map(0x05d00000u, 0x05d10000u, "VDP1-REG", v1reg_r, v1reg_w);
    sat_map(0x05e00000u, 0x05e80000u, "VDP2-VRAM", v2vram_r, v2vram_w);
    sat_map(0x05f00000u, 0x05f80000u, "VDP2-CRAM", cram_r, cram_w);
    sat_map(0x05f80000u, 0x05f90000u, "VDP2-REG", v2reg_r, v2reg_w);
}
