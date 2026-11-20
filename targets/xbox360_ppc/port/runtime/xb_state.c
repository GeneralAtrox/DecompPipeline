/* The device's render and sampler states. The game sets them through two tables of pointers inside the device: 82093680 calls
 * device[0x40 + state](device, value) and 820936c0 calls device[0x1d4 + state](device, sampler, value), states being byte offsets.
 * The library's rows (image 824b0900 for render states, 824b0dc0 for sampler states) are get, set, default. The kit fills the tables
 * with its own methods, keeps the values and takes the defaults from those rows; a draw reads them from here. */
#include "xb.h"
#include "base.h"
#include "xb_game.h"

#define RS_ROWS XB_RS_ROWS
#define SS_ROWS XB_SS_ROWS
#define METHOD(cls, slot) (XB_XACT_BASE + (((u32)(cls) << 8 | (u32)(slot)) << 2))
/* The render states' get table follows the two set tables (0x1D4 + 20 * 4): Too Human's 829a72a8 reads device[0x224 + state](device) and sets the
 * state back only if it differs. A get method is its state's slot with this bit (every count is below 128). The sampler states' get table follows
 * it (0x224 + 101 * 4 = 0x3B8): Too Human's 829a7338 reads device[0x3b8 + state](device, sampler) the same way */
#define RS_GETS 0x224u
#define SS_GETS (RS_GETS + XB_RS_COUNT * 4u)
#define GET 0x80u

static u32 s_rs[XB_RS_COUNT], s_ss[XB_SAMPLERS][XB_SS_COUNT];
static u8 s_rs_seen[XB_RS_COUNT], s_ss_seen[XB_SS_COUNT];
/* What the game's getters see: the device's copy the library keeps, which every setter the game calls changes when it is called, kept or
 * not. A command buffer's setters are made again later on the render thread, where they change only what draws see (s_rs). With one
 * set for both, Too Human's 829a72a8, which sets a state only if the getter says it differs, would read the other buffer's replay */
static u32 s_rs_cpu[XB_RS_COUNT], s_ss_cpu[XB_SAMPLERS][XB_SS_COUNT];
static __declspec(thread) int t_replay;

void xb_state_replaying(int on) { t_replay = on; }

/* The library's setters for render states 40, 44 and 48 keep the device's depth-control word, its own copy of 40 and a dirty bit; the kit's
 * methods do the same, so the game's inline code that saves, changes and restores the word (its depth-only pre-pass) and the draws, which read
 * the word (xb_gpu_draw.h depth_for_draw), see what the console's would. 8274c8c8 keeps z enable off with no depth surface bound (device
 * + XB_DEV_DEPTH_SURFACE); the kit does not: it binds its own depth target, and the slot is written only by calls the kit sees */
static void depth_word(u32 slot, const TcArgs *a)
{
#ifdef XB_DEV_DEPTHCONTROL
    u32 dev = (u32)a->r[0], v = (u32)a->r[1], w;
    if (!dev || (slot != 10 && slot != 11 && slot != 12)) return;
    w = rd32(dev + XB_DEV_DEPTHCONTROL);
    if (slot == 10) w = (w & ~2u) | (v ? 2u : 0u), wr32(dev + XB_DEV_ZENABLE, v);
    else if (slot == 11) w = (w & ~0x70u) | (v & 7u) << 4;
    else w = (w & ~4u) | (v ? 4u : 0u);
    wr32(dev + XB_DEV_DEPTHCONTROL, w);
    wr32(dev + 0x10, rd32(dev + 0x10) | XB_DEV_DIRTY_DEPTH);
#else
    (void)slot, (void)a;
#endif
}

/* The same for the colour write masks (212..224), the colour control (96 alpha test, 104 its comparison, 336 alpha to coverage, 340 its offsets) and
 * the mode control (52 fill modes, 56 cull mode), as the library's setters keep them (xb_game.h). The masks' rule for an unbound target (its bits
 * written 0) is not kept, for the reason the depth word's surface rule is not. The dirty bits are the 64-bit word at +0x10, as the library's ld/std */
static void dev_dirty(u32 dev, unsigned long long bits)
{
    unsigned long long d = (unsigned long long)rd32(dev + 0x10) << 32 | rd32(dev + 0x14);
    d |= bits;
    wr32(dev + 0x10, (u32)(d >> 32)), wr32(dev + 0x14, (u32)d);
}

static void dev_words_set(u32 dev, u32 slot, u32 v)
{
#ifdef XB_DEV_COLORMASK
    u32 w;
    if (!dev) return;
    if (slot >= 53 && slot <= 56) {
        unsigned k = slot - 53;
        wr32(dev + XB_DEV_COLORMASK_COPY + 4 * k, v);
        w = rd32(dev + XB_DEV_COLORMASK);
        wr32(dev + XB_DEV_COLORMASK, (w & ~(15u << 4 * k)) | (v & 15u) << 4 * k);
        dev_dirty(dev, 1ull << 37);
    } else if (slot == 24 || slot == 26 || slot == 84 || slot == 85) {
        w = rd32(dev + XB_DEV_COLORCONTROL);
        if (slot == 24) w = (w & ~8u) | (v & 1u) << 3;
        else if (slot == 26) w = (w & ~7u) | (v & 7u);
        else if (slot == 84) w = (w & ~16u) | (v & 1u) << 4;
        else w = (w & 0x00ffffffu) | (v & 255u) << 24;
        wr32(dev + XB_DEV_COLORCONTROL, w);
        dev_dirty(dev, slot == 24 ? 0x40200ull : 0x200ull);
    } else if (slot == 13 || slot == 14) {
        w = rd32(dev + XB_DEV_MODECONTROL);
        w = slot == 14 ? (w & ~7u) | (v & 7u) : (w & ~0x7f8u) | (v & 255u) << 3;
        wr32(dev + XB_DEV_MODECONTROL, w);
        dev_dirty(dev, 0x40ull);
    }
#else
    (void)dev, (void)slot, (void)v;
#endif
}

static void dev_words(u32 slot, const TcArgs *a) { dev_words_set((u32)a->r[0], slot, (u32)a->r[1]); }

/* The library's blend-enable setter (8274c238, render state 60) keeps the value in the top bit of device + 0x2e4c, and Too Human reads
 * that field itself: its 8299a790 saves it before a material's draw and sets blend enable back to it afterwards. Left at 0, every
 * material draw switched blending off for the next one and the text's glyphs drew as boxes. The kit's method keeps the bit as the
 * library's would */
static void blend_bit(u32 slot, const TcArgs *a)
{
    u32 dev = (u32)a->r[0];
    if (slot == 15 && dev) wr32(dev + 0x2e4c, (rd32(dev + 0x2e4c) & 0x7fffffffu) | ((u32)a->r[1] & 1u) << 31);
}

void xb_state_init(u32 device)
{
    unsigned i, k;
    for (i = 0; i < XB_RS_COUNT; i++) {
        wr32(device + 0x40 + i * 4, METHOD(XB_CLASS_RS, i));
        wr32(device + RS_GETS + i * 4, METHOD(XB_CLASS_RS, i | GET));
        s_rs[i] = s_rs_cpu[i] = rd32(RS_ROWS + i * 12 + 8);
        dev_words_set(device, i, s_rs[i]);   /* the words start as the states' defaults say, as the library's device starts them */
    }
    for (i = 0; i < XB_SS_COUNT; i++) {
        wr32(device + 0x1D4 + i * 4, METHOD(XB_CLASS_SS, i));
        wr32(device + SS_GETS + i * 4, METHOD(XB_CLASS_SS, i | GET));
        for (k = 0; k < XB_SAMPLERS; k++) s_ss[k][i] = s_ss_cpu[k][i] = rd32(SS_ROWS + i * 12 + 8);
    }
}

/* The kit's method for a state call: its own method by address, a row's set or get function by the rows, or 0 */
static u32 state_method(u32 target, u32 entry)
{
    unsigned i;
    if (target >= METHOD(XB_CLASS_RS, 0) && target < XB_XACT_BASE + 0x2000) return target;
    for (i = 0; i < XB_RS_COUNT; i++)
        if (rd32(RS_ROWS + i * 12 + 4) == entry) return METHOD(XB_CLASS_RS, i);
    for (i = 0; i < XB_SS_COUNT; i++)
        if (rd32(SS_ROWS + i * 12 + 4) == entry) return METHOD(XB_CLASS_SS, i);
    return 0;
}

void xb_state_recorded(u32 target, u32 entry, const TcArgs *a)
{
    u32 m = state_method(target, entry), slot;
    if (!m) return;
    m = (m - XB_XACT_BASE) >> 2, slot = m & 255;
    if (slot & GET) return;
    if (m >> 8 == XB_CLASS_SS) { if ((u32)a->r[1] < XB_SAMPLERS && slot < XB_SS_COUNT) s_ss_cpu[(u32)a->r[1]][slot] = (u32)a->r[2]; }
    else if (slot < XB_RS_COUNT) s_rs_cpu[slot] = (u32)a->r[1], blend_bit(slot, a), depth_word(slot, a), dev_words(slot, a);
}

u32 xb_rs(unsigned state) { return s_rs[state / 4]; }
u32 xb_ss(unsigned sampler, unsigned state) { return s_ss[sampler][state / 4]; }

void xb_ss_set(unsigned sampler, unsigned slot, u32 value)
{
    if (sampler >= XB_SAMPLERS || slot >= XB_SS_COUNT) xb_stop("d3d_state", "a sampler state past the tables", sampler << 8 | slot);
    if (!s_ss_seen[slot]) port_log("d3d sampler state %u first set to %x\n", slot * 4, value);
    s_ss_seen[slot] = 1;
    s_ss[sampler][slot] = value;
    if (!t_replay) s_ss_cpu[sampler][slot] = value;
}

/* A row's own set function called directly, not through the device's table: Too Human's 829a5600 calls render states 15, 76 and 77 and sampler
 * states 4, 5 and 7 so. The kit's method for that row takes it, so a draw sees the value either way. A get row goes the same way */
int xb_state_row_call(u32 target, const TcArgs *a, u64 *r3)
{
    unsigned i;
    for (i = 0; i < XB_RS_COUNT; i++)
        if (rd32(RS_ROWS + i * 12 + 4) == target) return xb_state_call(METHOD(XB_CLASS_RS, i), a, r3);
        else if (rd32(RS_ROWS + i * 12) == target) return xb_state_call(METHOD(XB_CLASS_RS, i | GET), a, r3);
    for (i = 0; i < XB_SS_COUNT; i++)
        if (rd32(SS_ROWS + i * 12 + 4) == target) return xb_state_call(METHOD(XB_CLASS_SS, i), a, r3);
        else if (rd32(SS_ROWS + i * 12) == target) return xb_state_call(METHOD(XB_CLASS_SS, i | GET), a, r3);
    return 0;
}

/* What a call is to the states, for a command buffer that records (xb_d3d_cb.h): 0 none, 1 a setter, 2 a getter. The kit's own methods
 * by their slot, the game's entries by the rows */
int xb_state_kind(u32 target, u32 entry)
{
    unsigned i;
    if (target >= METHOD(XB_CLASS_RS, 0) && target < XB_XACT_BASE + 0x2000) return (((target - XB_XACT_BASE) >> 2) & GET) ? 2 : 1;
    for (i = 0; i < XB_RS_COUNT; i++)
        if (rd32(RS_ROWS + i * 12 + 4) == entry) return 1;
        else if (rd32(RS_ROWS + i * 12) == entry) return 2;
    for (i = 0; i < XB_SS_COUNT; i++)
        if (rd32(SS_ROWS + i * 12 + 4) == entry) return 1;
        else if (rd32(SS_ROWS + i * 12) == entry) return 2;
    return 0;
}

int xb_state_call(u32 target, const TcArgs *a, u64 *r3)
{
    u32 m = (target - XB_XACT_BASE) >> 2, slot = m & 255;
    if (m >> 8 == XB_CLASS_RS && (slot & GET)) {
        if ((slot & ~GET) >= XB_RS_COUNT) xb_stop("d3d_state", "a render state past the table", slot);
        *r3 = s_rs_cpu[slot & ~GET];
        return 1;
    }
    if (m >> 8 == XB_CLASS_SS && (slot & GET)) {
        if ((u32)a->r[1] >= XB_SAMPLERS || (slot & ~GET) >= XB_SS_COUNT) xb_stop("d3d_state", "a sampler state past the tables", (u32)a->r[1] << 8 | slot);
        *r3 = s_ss_cpu[(u32)a->r[1]][slot & ~GET];
        return 1;
    }
    if (m >> 8 == XB_CLASS_SS) {
        xb_ss_set((u32)a->r[1], slot, (u32)a->r[2]);
    } else {
        if (slot >= XB_RS_COUNT) xb_stop("d3d_state", "a render state past the table", slot);
        if (!s_rs_seen[slot]) port_log("d3d render state %u first set to %x\n", slot * 4, (u32)a->r[1]);
        s_rs_seen[slot] = 1;
        s_rs[slot] = (u32)a->r[1];
        if (!t_replay) s_rs_cpu[slot] = (u32)a->r[1], blend_bit(slot, a), depth_word(slot, a), dev_words(slot, a);
    }
    *r3 = 0;
    return 1;
}
