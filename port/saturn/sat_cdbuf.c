/* The CD block's buffers: 200 sector blocks shared by 24 selectors. A selector is a filter (which sectors it takes:
 * the frame range and, for XA data, the subheader) and a buffer partition (where they are kept). A sector from the drive
 * enters at the selector the CD device is connected to; if the filter passes it, the partition keeps it; if not, it is
 * offered to the selector the filter's false output names. The host reads kept sectors through the data port.
 * Nearest existing: none (the Dreamcast GD-ROM has no buffer model). */
#include <stdlib.h>
#include <string.h>
#include "sat_cd.h"

typedef struct {
    u32 fad;
    u8 fn, cn, sm, ci;
    u16 size;   /* bytes kept, from the "put" sector length */
    u16 skip;   /* offset of the kept bytes in the raw sector */
    u8 data[SAT_SECTOR];
} Block;

static Block g_blk[CD_BLOCKS];
static Block *g_part[CD_SELECTORS][CD_BLOCKS];
static u32 g_n[CD_SELECTORS], g_used, g_last_part, g_cd_sel = CD_NONE;
static CdFilter g_flt[CD_SELECTORS];
static int g_get = 0, g_put = 0;
static const u16 k_size[4] = {2048, 2336, 2340, 2352}, k_skip[4] = {16, 16, 12, 0};
static u8 g_x[CD_BLOCKS * SAT_SECTOR + 4096];
static u32 g_xlen, g_xpos;
static int g_xactive, g_xdelete;
static u32 g_xpart, g_xfirst, g_xnum;

void cdb_reset(int selectors_too)
{
    memset(g_n, 0, sizeof g_n);
    g_used = 0;
    g_xactive = 0;
    if (!selectors_too) return;
    memset(g_flt, 0, sizeof g_flt);
    for (u32 i = 0; i < CD_SELECTORS; i++) {
        g_flt[i].ctrue = (u8)i;
        g_flt[i].cfalse = CD_NONE;
    }
    g_cd_sel = CD_NONE;
}

void cdb_set_lengths(int get_code, int put_code)
{
    if (get_code >= 0 && get_code <= 3) g_get = get_code;
    if (put_code >= 0 && put_code <= 3) g_put = put_code;
}

CdFilter *cdb_filter(u32 n) { return n < CD_SELECTORS ? &g_flt[n] : NULL; }
void cdb_connect_cd(u32 selector) { g_cd_sel = selector < CD_SELECTORS ? selector : CD_NONE; }
u32 cdb_cd_selector(void) { return g_cd_sel; }
u32 cdb_last_part(void) { return g_last_part; }
u32 cdb_free(void) { return CD_BLOCKS - g_used; }
u32 cdb_count(u32 part) { return part < CD_SELECTORS ? g_n[part] : 0; }

static int passes(const CdFilter *f, u32 fad, const u8 *raw)
{
    u8 fn = 0, cn = 0, sm = 0, ci = 0;
    int sub = 1;
    if (raw[15] == 2) { /* mode 2: the XA subheader */
        fn = raw[16];
        cn = raw[17];
        sm = raw[18];
        ci = raw[19];
    }
    if ((f->mode & 0x40) && (fad < f->fad || fad >= f->fad + f->count)) return 0; /* the frame range */
    if ((f->mode & 1) && fn != f->fid) sub = 0;                                  /* file number */
    if ((f->mode & 2) && cn != f->chan) sub = 0;                                 /* channel number */
    if ((f->mode & 4) && (sm & f->smmask) != f->smval) sub = 0;                  /* submode */
    if ((f->mode & 8) && (ci & f->cimask) != f->cival) sub = 0;                  /* coding information */
    if ((f->mode & 0x10) && (f->mode & 15)) sub = !sub;                          /* the subheader conditions inverted */
    return sub;
}

int cdb_store(const u8 *raw, u32 fad)
{
    u32 sel = g_cd_sel;
    for (int hops = 0; sel < CD_SELECTORS && hops < CD_SELECTORS; hops++) {
        const CdFilter *f = &g_flt[sel];
        if (passes(f, fad, raw)) {
            u32 part = f->ctrue;
            Block *b;
            if (part >= CD_SELECTORS) return 1; /* true output unconnected: the sector is dropped */
            if (g_used >= CD_BLOCKS) return 0;
            for (b = g_blk; b < g_blk + CD_BLOCKS; b++)
                if (b->size == 0) break;
            b->fad = fad;
            b->fn = raw[15] == 2 ? raw[16] : 0;
            b->cn = raw[15] == 2 ? raw[17] : 0;
            b->sm = raw[15] == 2 ? raw[18] : 0;
            b->ci = raw[15] == 2 ? raw[19] : 0;
            b->size = k_size[g_put];
            b->skip = k_skip[g_put];
            memcpy(b->data, raw, SAT_SECTOR);
            g_part[part][g_n[part]++] = b;
            g_used++;
            g_last_part = part;
            return 1;
        }
        sel = f->cfalse;
    }
    return 1; /* nowhere to go: dropped */
}

int cdb_info(u32 part, u32 pos, u32 *fad, u8 *fn, u8 *cn, u8 *sm, u8 *ci)
{
    const Block *b;
    if (part >= CD_SELECTORS || pos >= g_n[part]) return 0;
    b = g_part[part][pos];
    *fad = b->fad, *fn = b->fn, *cn = b->cn, *sm = b->sm, *ci = b->ci;
    return 1;
}

void cdb_delete(u32 part, u32 pos, u32 num)
{
    u32 n;
    if (part >= CD_SELECTORS || pos >= g_n[part]) return;
    n = g_n[part];
    if (num == 0xffffu || pos + num > n) num = n - pos;
    for (u32 i = 0; i < num; i++) g_part[part][pos + i]->size = 0;
    memmove(&g_part[part][pos], &g_part[part][pos + num], (n - pos - num) * sizeof g_part[part][0]);
    g_n[part] = n - num;
    g_used -= num;
}

u32 cdb_actual_size(u32 part, u32 pos, u32 num)
{
    u32 n = cdb_count(part);
    if (pos >= n) return 0;
    if (num == 0xffffu || pos + num > n) num = n - pos;
    return num * k_size[g_get];
}

void xfer_blocks(u32 part, u32 pos, u32 num, int delete_after)
{
    u32 n = cdb_count(part), len = 0;
    if (pos >= n) n = pos;
    if (num == 0xffffu || pos + num > n) num = n - pos;
    for (u32 i = 0; i < num; i++) {
        const Block *b = g_part[part][pos + i];
        memcpy(g_x + len, b->data + k_skip[g_get], k_size[g_get]);
        len += k_size[g_get];
    }
    g_xlen = len, g_xpos = 0, g_xactive = 1, g_xdelete = delete_after;
    g_xpart = part, g_xfirst = pos, g_xnum = num;
}

void xfer_bytes(const void *data, u32 len)
{
    if (len > sizeof g_x) len = sizeof g_x;
    memcpy(g_x, data, len);
    g_xlen = len, g_xpos = 0, g_xactive = 1, g_xdelete = 0;
}

u32 xfer_read(u32 size)
{
    u32 v = 0;
    for (u32 i = 0; i < size; i++) v = v << 8 | (g_xpos < g_xlen ? g_x[g_xpos] : 0u), g_xpos++;
    return v;
}

int xfer_active(void) { return g_xactive; }

u32 xfer_end(void)
{
    u32 moved = g_xpos < g_xlen ? g_xpos : g_xlen;
    if (g_xactive && g_xdelete) cdb_delete(g_xpart, g_xfirst, g_xnum);
    g_xactive = 0;
    return moved;
}
