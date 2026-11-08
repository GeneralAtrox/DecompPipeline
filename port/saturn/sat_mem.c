/* The Saturn's SH-2 bus for translated code: work RAM high (1 MB, repeated through 0x07ffffff) and low (1 MB at
 * 0x00200000), the cache-through views (bit 29), the cache control areas, the SH-2's own registers (the divider and
 * the free-running timer are modelled; the rest keep what is written), and the hardware regions other modules map
 * with sat_map. Guest memory is big-endian, so rd32/wr32 swap; a guest pointer is never a host pointer.
 * Any hardware address nothing claims is logged once per 256 bytes and reads 0, so a bring-up run names what the
 * game reaches next (tools: the log's "hw:" lines). */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"

#define PAGES 0x2000u /* 64 KB pages of the 29-bit physical space */

typedef struct {
    u32 lo, hi;
    const char *name;
    SatRead rd;
    SatWrite wr;
    u32 count; /* accesses */
} Region;

static u8 g_wh[SAT_WRAM_SIZE], g_wl[SAT_WRAM_SIZE];
static Region g_reg[32];
static u8 g_page[PAGES]; /* region index + 1 per 64 KB page, 0 none */
static int g_nreg;
static u8 g_ocs[2][0x200]; /* each SH-2's on-chip registers 0xfffffe00..0xffffffff */
static u8 *g_oc = g_ocs[0];
void sat_mem_cpu(int id) { g_oc = g_ocs[id]; }
void sat_mem_icf(int cpu) { g_ocs[cpu][0x11] |= 0x80u; } /* FTCSR.ICF: the other CPU's input-capture signal */
static u8 g_cache_tag[0x1000];  /* address array 0x60000000.. (kept, not modelled) */
static u8 g_cache_data[0x1000]; /* data array 0xc0000000.. */
static volatile LONG g_unhandled_logged;

u8 *sat_ram_h(void) { return g_wh; }
u8 *sat_ram_l(void) { return g_wl; }

static u32 bswap32(u32 v) { return _byteswap_ulong(v); }
static u16 bswap16(u16 v) { return _byteswap_ushort(v); }

void sat_load(u32 addr, const u8 *data, u32 size)
{
    u32 p = addr & 0x1fffffffu;
    u8 *base = p - SAT_WRAM_L_BASE < SAT_WRAM_SIZE ? g_wl + (p - SAT_WRAM_L_BASE) : g_wh + (p & (SAT_WRAM_SIZE - 1u));
    if (size > SAT_WRAM_SIZE) size = SAT_WRAM_SIZE;
    memcpy(base, data, size);
}

u32 sat_peek32(u32 addr)
{
    u32 p = addr & 0x1fffffffu;
    if (p >> 24 == 6u || p >> 24 == 7u) return bswap32(*(u32 *)(g_wh + (p & (SAT_WRAM_SIZE - 1u))));
    if (p - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) return bswap32(*(u32 *)(g_wl + (p - SAT_WRAM_L_BASE)));
    return 0;
}

void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr)
{
    Region *r = &g_reg[g_nreg];
    if (g_nreg >= 31) return;
    r->lo = lo;
    r->hi = hi;
    r->name = name;
    r->rd = rd;
    r->wr = wr;
    g_nreg++;
    for (u32 pg = lo >> 16; pg <= (hi - 1u) >> 16; pg++) g_page[pg] = (u8)g_nreg;
}

/* ---- plain register files: store, return, and log each distinct register's first access */
#define REGFILE 0x40
typedef struct {
    u32 lo;
    const char *name;
    u8 mem[0x10000];
    u8 seen[0x10000 / 4];
} RegFile;
static RegFile *g_rf[8];
static u32 g_rf_lo[8], g_rf_hi[8];
static int g_nrf;

static RegFile *rf_of(u32 phys)
{
    for (int i = 0; i < g_nrf; i++)
        if (phys >= g_rf_lo[i] && phys < g_rf_hi[i]) return g_rf[i];
    return NULL;
}

static u32 rf_read(u32 phys, u32 size)
{
    RegFile *f = rf_of(phys);
    u32 o = (phys - f->lo) & 0xffffu, v = 0;
    if (!(f->seen[o >> 2] & 1u)) {
        f->seen[o >> 2] |= 1u;
        port_log("hw: read %s %08x (%u)\n", f->name, phys, size);
    }
    for (u32 i = 0; i < size; i++) v = (v << 8) | f->mem[(o + i) & 0xffffu];
    return v;
}

static void rf_write(u32 phys, u32 size, u32 value)
{
    RegFile *f = rf_of(phys);
    u32 o = (phys - f->lo) & 0xffffu;
    if (!(f->seen[o >> 2] & 2u)) {
        f->seen[o >> 2] |= 2u;
        port_log("hw: write %s %08x (%u) = %x\n", f->name, phys, size, value);
    }
    for (u32 i = 0; i < size; i++) f->mem[(o + i) & 0xffffu] = (u8)(value >> (8u * (size - 1u - i)));
}

void sat_map_regs(u32 lo, u32 hi, const char *name)
{
    RegFile *f = (RegFile *)calloc(1, sizeof *f);
    if (!f || g_nrf >= 8) return;
    f->lo = lo & ~0xffffu;
    f->name = name;
    g_rf[g_nrf] = f;
    g_rf_lo[g_nrf] = lo;
    g_rf_hi[g_nrf] = hi;
    g_nrf++;
    sat_map(lo, hi, name, rf_read, rf_write);
}

/* ---- the SH-2's own registers */
static u32 g_divu_vcr;

static void divu_run(int wide)
{
    u32 dvsr = bswap32(*(u32 *)(g_oc + 0x100));
    u32 hi = bswap32(*(u32 *)(g_oc + 0x110)), lo = bswap32(*(u32 *)(g_oc + 0x114));
    int64_t n = wide ? (int64_t)(((u64)hi << 32) | lo) : (int64_t)(s32)lo;
    int64_t q = 0, r = 0;
    u32 ovf = 0;
    if (dvsr == 0u) {
        ovf = 1;
        q = n < 0 ? (int64_t)(s32)0x80000000 : 0x7fffffff;
        r = (int64_t)(s32)(u32)n;
    } else {
        q = n / (s32)dvsr;
        r = n % (s32)dvsr;
        if (q > 0x7fffffffLL || q < -0x80000000LL) {
            ovf = 1;
            q = q < 0 ? (int64_t)(s32)0x80000000 : 0x7fffffff;
        }
    }
    *(u32 *)(g_oc + 0x114) = bswap32((u32)q);
    *(u32 *)(g_oc + 0x104) = bswap32((u32)q);
    *(u32 *)(g_oc + 0x110) = bswap32((u32)r);
    if (ovf) *(u32 *)(g_oc + 0x108) = bswap32(bswap32(*(u32 *)(g_oc + 0x108)) | 1u);
    (void)g_divu_vcr;
}

u32 sat_frt_count(void)
{
    u32 cks = g_oc[0x16] & 3u, shift = cks == 3u ? 0u : 2u * cks; /* TCR's CKS: clock / 8, 32 or 128 (3 is an external clock, not modelled) */
    if (sat_sys_det()) return sat_det_frt(shift);
    return (u32)(sat_clock_ticks(3579545u >> shift) & 0xffffu);
}

static u32 oc_read(u32 a, u32 size)
{
    u32 o = a & 0x1ffu, v = 0;
    if (o >= 0x120u && o < 0x140u) o -= 0x20u; /* the divider repeats at 0x...ff20 */
    if (o >= 0x118u && o < 0x120u) o -= 8u;    /* DVDNTH/DVDNTL are also read at 0x...ff18 and 0x...ff1c */
    if (o == 0x11u) sat_poll_hint(8); /* FTCSR: a wait for the other CPU's signal */
    if (o == 0x12u) { /* FRC: reading its upper byte latches the lower one, so a byte pair is one count (Robo Pit's movie clock) */
        u32 c = sat_frt_count();
        *(u16 *)(g_oc + 0x12) = bswap16((u16)c);
    }
    for (u32 i = 0; i < size; i++) v = (v << 8) | g_oc[(o + i) & 0x1ffu];
    return v;
}

/* The on-chip DMA controller, auto-request channels only (what the game uses to copy and clear memory): a transfer
 * runs to completion when the channel is enabled and DMAOR's master enable is set, and sets CHCR's TE. With 16-byte
 * units TCR counts longwords, 4 per unit (the game sets it to bytes / 4), so a unit takes 4 off the count. */
static u32 oc32(u32 o) { return bswap32(*(u32 *)(g_oc + o)); }
static void dmac_run(u32 ch)
{
    u32 b = 0x180u + 0x10u * ch, chcr = oc32(b + 12u), sar = oc32(b), dar = oc32(b + 4u), n = oc32(b + 8u) & 0xffffffu;
    u32 ts = (chcr >> 10) & 3u, sm = (chcr >> 12) & 3u, dm = (chcr >> 14) & 3u, unit = ts == 0 ? 1u : ts == 1 ? 2u : ts == 2 ? 4u : 16u;
    if (!(chcr & 1u) || (chcr & 2u) || !(oc32(0x1b0u) & 1u)) return;
    if (!(chcr & 0x200u)) { /* module request: a peripheral starts it; none is modelled */
        port_log("hw: DMAC channel %u enabled for a module request (CHCR %04x): not run\n", ch, chcr);
        return;
    }
    if (n == 0) n = 0x1000000u;
    for (u32 i = 0; i < n; i += unit == 16u ? 4u : 1u) {
        if (unit == 1u) wr8(dar, rd8(sar));
        else if (unit == 2u) wr16(dar, rd16(sar));
        else if (unit == 4u) wr32(dar, rd32(sar));
        else
            for (u32 k = 0; k < 16u && i + k / 4u < n; k += 4u) wr32(dar + k, rd32(sar + k));
        sar += sm == 1u ? unit : sm == 2u ? (u32)-(s32)unit : 0u;
        dar += dm == 1u ? unit : dm == 2u ? (u32)-(s32)unit : 0u;
    }
    *(u32 *)(g_oc + b) = bswap32(sar);
    *(u32 *)(g_oc + b + 4u) = bswap32(dar);
    *(u32 *)(g_oc + b + 8u) = 0;
    *(u32 *)(g_oc + b + 12u) = bswap32(chcr | 2u);
}

static void oc_write(u32 a, u32 size, u32 value)
{
    u32 o = a & 0x1ffu;
    if (o >= 0x120u && o < 0x140u) o -= 0x20u;
    if (o >= 0x118u && o < 0x120u) o -= 8u;
    for (u32 i = 0; i < size; i++) g_oc[(o + i) & 0x1ffu] = (u8)(value >> (8u * (size - 1u - i)));
    if (o >= 0x18cu && o < 0x190u) dmac_run(0);
    else if (o >= 0x19cu && o < 0x1a0u) dmac_run(1);
    else if (o >= 0x1b0u && o < 0x1b4u) {
        dmac_run(0);
        dmac_run(1);
    }
    if (o == 0x104u) { /* DVDNT: 32 / 32 */
        *(u32 *)(g_oc + 0x114) = *(u32 *)(g_oc + 0x104);
        *(u32 *)(g_oc + 0x110) = bswap32(((s32)value) < 0 ? 0xffffffffu : 0u);
        divu_run(0);
    } else if (o == 0x114u) { /* DVDNTL: 64 / 32 */
        divu_run(1);
    }
}

/* ---- the bus */
static void unhandled(u32 phys, u32 size, int write, u32 value)
{
    static u8 seen[0x2000000 / 256 / 8 + 1]; /* 256-byte blocks of the first 32 MB; above: always log */
    u32 blk = phys >> 8;
    if (blk < 0x2000000u / 256u) {
        if (seen[blk >> 3] & (1u << (blk & 7u))) return;
        seen[blk >> 3] |= (u8)(1u << (blk & 7u));
    }
    if (InterlockedIncrement(&g_unhandled_logged) > 400) return;
    port_log("hw: %s unclaimed %08x (%u)%s\n", write ? "write" : "read", phys, size, write ? "" : " -> 0");
    if (write) port_log("hw:   value %x\n", value);
}

static u32 bus_read(u32 a, u32 size)
{
    u32 p = a & 0x1fffffffu;
    switch (a >> 29) {
    case 0:
    case 1:
        break;
    case 3: /* cache address array */
        return 0;
    case 6: /* cache data array */
        return 0;
    case 7:
        if (a >= 0xfffffe00u) return oc_read(a, size);
        return 0;
    default:
        return 0;
    }
    u32 pg = g_page[p >> 16];
    if (pg) {
        const Region *r = &g_reg[pg - 1u];
        if (p >= r->lo && p < r->hi && r->rd) return r->rd(p, size);
    }
    unhandled(p, size, 0, 0);
    return 0;
}

static void bus_write(u32 a, u32 size, u32 value)
{
    u32 p = a & 0x1fffffffu;
    switch (a >> 29) {
    case 0:
    case 1:
        break;
    case 2: /* associative purge: nothing to purge */
    case 3:
    case 6:
        return;
    case 7:
        if (a >= 0xfffffe00u) oc_write(a, size, value);
        return;
    default:
        return;
    }
    u32 pg = g_page[p >> 16];
    if (pg) {
        const Region *r = &g_reg[pg - 1u];
        if (p >= r->lo && p < r->hi && r->wr) {
            r->wr(p, size, value);
            return;
        }
    }
    unhandled(p, size, 1, value);
}

u32 sat_rd_ring[8], sat_rd_n; /* the last reads: what a stuck wait was reading */
static u32 g_reads; /* plain reads: a long run of them is a wait on a flag an interrupt or the other CPU sets */
#define RAMH(a) (((a) >> 24 & 0xdu) == 6u || ((a) >> 24 & 0xdu) == 7u) /* 0x06.. / 0x07.. through bit 29 */

u32 rd32(u32 a)
{
    sat_rd_ring[sat_rd_n++ & 7u] = a;
    u32 q = a & 0xdfffffffu; /* the cache-through bit folded */
    if (a & 0x20000000u) sat_poll_hint(256);
    else if ((++g_reads & 0xffffu) == 0) sat_busy_poll();
    if (q - 0x06000000u < 0x02000000u) return bswap32(*(u32 *)(g_wh + (a & (SAT_WRAM_SIZE - 1u))));
    if (q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) return bswap32(*(u32 *)(g_wl + (q - SAT_WRAM_L_BASE)));
    return bus_read(a, 4);
}

u32 sat_dma_rd32(u32 a) /* a read by the SCU's DMA (sat_scu.c), not by a CPU: no poll hint */
{
    u32 q = a & 0xdfffffffu;
    return q - 0x06000000u < 0x02000000u || q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE ? sat_peek32(a) : bus_read(a, 4);
}

u16 rd16(u32 a)
{
    sat_rd_ring[sat_rd_n++ & 7u] = a;
    u32 q = a & 0xdfffffffu;
    if (a & 0x20000000u) sat_poll_hint(256);
    else if ((++g_reads & 0xffffu) == 0) sat_busy_poll();
    if (q - 0x06000000u < 0x02000000u) return bswap16(*(u16 *)(g_wh + (a & (SAT_WRAM_SIZE - 1u))));
    if (q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) return bswap16(*(u16 *)(g_wl + (q - SAT_WRAM_L_BASE)));
    return (u16)bus_read(a, 2);
}

u8 rd8(u32 a)
{
    sat_rd_ring[sat_rd_n++ & 7u] = a;
    u32 q = a & 0xdfffffffu;
    if (a & 0x20000000u) sat_poll_hint(256);
    else if ((++g_reads & 0xffffu) == 0) sat_busy_poll();
    if (q - 0x06000000u < 0x02000000u) return g_wh[a & (SAT_WRAM_SIZE - 1u)];
    if (q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) return g_wl[q - SAT_WRAM_L_BASE];
    return (u8)bus_read(a, 1);
}

void wr32(u32 a, u32 v)
{
    sat_wr_n++;
    u32 q = a & 0xdfffffffu;
    if (q - 0x06000000u < 0x02000000u) *(u32 *)(g_wh + (a & (SAT_WRAM_SIZE - 1u))) = bswap32(v);
    else if (q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) *(u32 *)(g_wl + (q - SAT_WRAM_L_BASE)) = bswap32(v);
    else bus_write(a, 4, v);
}

void wr16(u32 a, u16 v)
{
    sat_wr_n++;
    u32 q = a & 0xdfffffffu;
    if (q - 0x06000000u < 0x02000000u) *(u16 *)(g_wh + (a & (SAT_WRAM_SIZE - 1u))) = bswap16(v);
    else if (q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) *(u16 *)(g_wl + (q - SAT_WRAM_L_BASE)) = bswap16(v);
    else bus_write(a, 2, v);
}

void wr8(u32 a, u8 v)
{
    sat_wr_n++;
    u32 q = a & 0xdfffffffu;
    if (q - 0x06000000u < 0x02000000u) g_wh[a & (SAT_WRAM_SIZE - 1u)] = v;
    else if (q - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) g_wl[q - SAT_WRAM_L_BASE] = v;
    else bus_write(a, 1, v);
}

f32 rdf32(u32 a)
{
    u32 v = rd32(a);
    f32 f;
    memcpy(&f, &v, 4);
    return f;
}

void wrf32(u32 a, f32 f)
{
    u32 v;
    memcpy(&v, &f, 4);
    wr32(a, v);
}

/* tc_rt.h's hardware calls: the same bus */
u32 tc_hw_read32(u32 reg) { return rd32(reg); }
u16 tc_hw_read16(u32 reg) { return rd16(reg); }
u8 tc_hw_read8(u32 reg) { return rd8(reg); }
void tc_hw_write32(u32 reg, u32 value) { wr32(reg, value); }
void tc_hw_write16(u32 reg, u16 value) { wr16(reg, value); }
void tc_hw_write8(u32 reg, u8 value) { wr8(reg, value); }

void sat_mem_init(void)
{
    memset(g_wh, 0, sizeof g_wh);
    memset(g_wl, 0, sizeof g_wl);
    memset(g_oc, 0, sizeof g_oc);
    (void)g_cache_tag;
    (void)g_cache_data;
    *(u32 *)(g_oc + 0x108) = 0; /* DVCR */
}

void sat_mem_report(void)
{
    for (int i = 0; i < g_nreg; i++)
        if (g_reg[i].count) port_log("hw: region %s %u accesses\n", g_reg[i].name, g_reg[i].count);
}
