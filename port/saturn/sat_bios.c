/* The BIOS' services, as the game reaches them: through function pointers the BIOS leaves in work RAM 0x06000300 and up.
 * No BIOS image runs here, so the pointers lead to native functions at made-up ROM addresses (0x00000e00..): tc_call
 * checks sat_bios_native before it reports a missing function. The game's code image has zeros in 0x06000000..0x060003ff,
 * so these words are the kit's. Services are added as the game is seen to use them (each one logged on first use). */
#include <stdint.h>
#include "sat.h"
#include "sat_sys.h"

enum { ROM = 0x00000e00u };
static u32 g_clock; /* the work-RAM word at 0x06000324: the clock mode the BIOS last set */

static u32 set_scu_vector(u32 vec, u32 handler, u32 c, u32 d)
{
    (void)c;
    (void)d;
    wr32(0x06000000u + 4u * (vec & 0xffu), handler);
    return 0;
}
static u32 get_scu_vector(u32 vec, u32 b, u32 c, u32 d)
{
    (void)b;
    (void)c;
    (void)d;
    return rd32(0x06000000u + 4u * (vec & 0xffu));
}
static u32 set_clock(u32 mode, u32 b, u32 c, u32 d)
{
    (void)b;
    (void)c;
    (void)d;
    g_clock = mode;
    wr32(0x06000324u, mode);
    return 0;
}
static u32 set_scu_mask(u32 mask, u32 b, u32 c, u32 d)
{
    (void)b;
    (void)c;
    (void)d;
    sat_scu_set_ims(mask);
    return 0;
}
static u32 change_scu_mask(u32 keep, u32 set, u32 c, u32 d)
{
    u32 ims, ist;
    (void)c;
    (void)d;
    sat_scu_state(&ims, &ist);
    sat_scu_set_ims((ims & keep) | set);
    return 0;
}

typedef struct {
    u32 slot; /* the work-RAM word the game reads the service's address from */
    SatNative fn;
} Service;
static const Service k_services[] = {
    {0x06000300u, set_scu_vector},  {0x06000304u, get_scu_vector}, {0x06000310u, set_scu_vector},
    {0x06000314u, get_scu_vector},  {0x06000320u, set_clock},      {0x06000340u, set_scu_mask},
    {0x06000344u, change_scu_mask},
};
enum { N = sizeof k_services / sizeof k_services[0] };

SatNative sat_bios_native(u32 addr)
{
    u32 i = (addr - ROM) / 4u;
    return addr >= ROM && i < N ? k_services[i].fn : sat_bup_native(addr);
}

void sat_bios_init(void)
{
    for (u32 i = 0; i < N; i++) wr32(k_services[i].slot, ROM + 4u * i);
    wr32(0x06000324u, 0xffffffffu);
    sat_bup_init();
    (void)g_clock;
}
