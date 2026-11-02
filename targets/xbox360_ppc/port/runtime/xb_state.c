/* The device's render and sampler states. The game sets them through two tables of pointers inside the device: 82093680 calls
 * device[0x40 + state](device, value) and 820936c0 calls device[0x1d4 + state](device, sampler, value), states being byte offsets.
 * The library's rows (image 824b0900 for render states, 824b0dc0 for sampler states) are get, set, default. The kit fills the tables
 * with its own methods, keeps the values and takes the defaults from those rows; a draw reads them from here. */
#include "xb.h"
#include "base.h"

#define RS_ROWS 0x824B0900u
#define SS_ROWS 0x824B0DC0u
#define METHOD(cls, slot) (XB_XACT_BASE + (((u32)(cls) << 8 | (u32)(slot)) << 2))

static u32 s_rs[XB_RS_COUNT], s_ss[XB_SAMPLERS][XB_SS_COUNT];
static u8 s_rs_seen[XB_RS_COUNT], s_ss_seen[XB_SS_COUNT];

void xb_state_init(u32 device)
{
    unsigned i, k;
    for (i = 0; i < XB_RS_COUNT; i++) {
        wr32(device + 0x40 + i * 4, METHOD(XB_CLASS_RS, i));
        s_rs[i] = rd32(RS_ROWS + i * 12 + 8);
    }
    for (i = 0; i < XB_SS_COUNT; i++) {
        wr32(device + 0x1D4 + i * 4, METHOD(XB_CLASS_SS, i));
        for (k = 0; k < XB_SAMPLERS; k++) s_ss[k][i] = rd32(SS_ROWS + i * 12 + 8);
    }
}

u32 xb_rs(unsigned state) { return s_rs[state / 4]; }
u32 xb_ss(unsigned sampler, unsigned state) { return s_ss[sampler][state / 4]; }

void xb_ss_set(unsigned sampler, unsigned slot, u32 value)
{
    if (sampler >= XB_SAMPLERS || slot >= XB_SS_COUNT) xb_stop("d3d_state", "a sampler state past the tables", sampler << 8 | slot);
    if (!s_ss_seen[slot]) port_log("d3d sampler state %u first set to %x\n", slot * 4, value);
    s_ss_seen[slot] = 1;
    s_ss[sampler][slot] = value;
}

int xb_state_call(u32 target, const TcArgs *a, u64 *r3)
{
    u32 m = (target - XB_XACT_BASE) >> 2, slot = m & 255;
    if (m >> 8 == XB_CLASS_SS) {
        xb_ss_set((u32)a->r[1], slot, (u32)a->r[2]);
    } else {
        if (slot >= XB_RS_COUNT) xb_stop("d3d_state", "a render state past the table", slot);
        if (!s_rs_seen[slot]) port_log("d3d render state %u first set to %x\n", slot * 4, (u32)a->r[1]);
        s_rs_seen[slot] = 1;
        s_rs[slot] = (u32)a->r[1];
    }
    *r3 = 0;
    return 1;
}
