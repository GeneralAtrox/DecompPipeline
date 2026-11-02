/* The SCU (0x05fe0000): interrupt mask and status, the three DMA levels, the timers' registers, the DSP's port.
 * An interrupt source sets its IST bit (sat_scu_raise); sat_sys.c delivers the unmasked ones. DMA runs when started
 * (immediate start factor, or the game starting it by hand) and raises its end interrupt. */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"

#define SCU_LO 0x05fe0000u
#define SCU_HI 0x05ff0000u

static u32 g_reg[0x100 / 4];
static u32 g_ist, g_ims = 0xffffffffu;

u32 sat_scu_deliverable(void) { return g_ist & ~g_ims & 0x3fffu; }
void sat_scu_ack(u32 bit) { g_ist &= ~(1u << bit); }
void sat_scu_state(u32 *ims, u32 *ist) { *ims = g_ims; *ist = g_ist; }
void sat_scu_set_ims(u32 mask) { g_ims = mask; }
void sat_scu_raise(u32 bit) { g_ist |= 1u << bit; }

static u32 add_value(u32 code)
{
    static const u32 tbl[8] = {0, 2, 4, 8, 16, 32, 64, 128};
    return tbl[code & 7u];
}

/* A write to the B-bus (sound, VDP1, VDP2: 0x05a00000..0x05fdffff) is two 16-bit writes per 32-bit word, each stepping the
 * destination by the write add value; anywhere else it is one 32-bit write and one step. */
static int is_bbus(u32 a)
{
    a &= 0x1fffffffu;
    return a >= 0x05a00000u && a < 0x05fe0000u;
}

static void dma_put(u32 *dst, u32 v, u32 add)
{
    if (is_bbus(*dst)) {
        wr16(*dst, v >> 16);
        *dst += add;
        wr16(*dst, v & 0xffffu);
        *dst += add;
    } else {
        wr32(*dst, v);
        *dst += add;
    }
}

static void dma_run(int lv)
{
    u32 *r = g_reg + lv * 8, src, dst, cnt, ad = r[3], md = r[5];
    u32 src_add = (ad & 0x100u) ? 4u : 0u, dst_add = add_value(ad);
    static int nlog;
    if (nlog++ < 20) port_log("scu: vb %u dma level %d: read %08x write %08x count %x add %x mode %08x\n", sat_vblank_count(), lv, r[0], r[1], r[2], ad, md);
    if (md & 0x1000000u) { /* indirect: a table at the write address of {count, dst, src|end<<31} */
        u32 tbl = r[1];
        for (int n = 0; n < 4096; n++, tbl += 12) {
            u32 c = rd32(tbl), d = rd32(tbl + 4), s = rd32(tbl + 8), last = s >> 31;
            s &= 0x7fffffffu;
            for (u32 i = 0; i < c; i += 4, s += src_add) dma_put(&d, rd32(s), dst_add);
            if (last) break;
        }
    } else {
        src = r[0];
        dst = r[1];
        cnt = r[2] ? r[2] : (lv == 0 ? 0x100000u : 0x1000u);
        for (u32 i = 0; i < cnt; i += 4, src += src_add) dma_put(&dst, rd32(src), dst_add);
        if (md & 0x10000u) r[0] = src;
        if (md & 0x100u) r[1] = dst;
    }
    r[4] &= ~0x100u;
    sat_scu_raise(lv == 0 ? 11u : lv == 1 ? 10u : 9u);
}

static u32 scu_read(u32 p, u32 size)
{
    u32 o = p - SCU_LO, v;
    (void)size;
    if (o == 0xa4u) return g_ist;
    if (o == 0xa0u) return g_ims;
    if (o == 0xc8u) return 4u; /* SCU version */
    if (o == 0x7cu) return 0u; /* DMA status: idle */
    if (o == 0x80u) return 0u; /* DSP program control: idle */
    v = g_reg[(o >> 2) & 63u];
    return v;
}

static void scu_write(u32 p, u32 size, u32 value)
{
    u32 o = (p - SCU_LO) & ~3u;
    if (size == 1u) value <<= 8u * (3u - ((p - SCU_LO) & 3u));
    else if (size == 2u) value <<= 8u * (2u - ((p - SCU_LO) & 2u));
    if (o >= 0x80u && o <= 0x8cu) {
        static u32 n;
        if (n++ < 40u) port_log("scu: DSP port %02x written %08x\n", o, value);
    }
    if (o == 0xa0u) { g_ims = value; return; }
    if (o == 0xa4u) { g_ist &= value; return; } /* a 0 bit clears */
    g_reg[(o >> 2) & 63u] = value;
    if (o < 0x60u && (o & 0x1fu) == 0x10u && (value & 0x100u)) { /* DnEN: enable */
        int lv = (int)(o >> 5);
        u32 mode = g_reg[lv * 8 + 5] & 7u;
        if ((value & 1u) && mode == 7u) dma_run(lv); /* immediate */
        else if (mode == 7u) dma_run(lv);
    }
}

void sat_scu_init(void)
{
    memset(g_reg, 0, sizeof g_reg);
    g_ist = 0;
    g_ims = 0xffffffffu;
    sat_map(SCU_LO, SCU_HI, "SCU", scu_read, scu_write);
}
