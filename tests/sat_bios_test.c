/* The BIOS services (sat_bios.c, included whole): SYS_SETUINT must not touch the vector SYS_SETSINT set. SBL's CD stream
 * library sets SETSINT(0x4b, handler) and then SETUINT(0x4b, 0); with the two aliased the handler was wiped and Saturn
 * Bomberman's round never started (2026-11-17). Run by tests/test_sat_bios.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_bios.c"

static u32 g_ram[0x400u / 4u]; /* 0x06000000..0x060003ff, big-endian words kept as values */
static int g_fail;
u32 rd32(u32 a) { return a - 0x06000000u < 0x400u ? g_ram[(a - 0x06000000u) / 4u] : 0u; }
void wr32(u32 a, u32 v) { if (a - 0x06000000u < 0x400u) g_ram[(a - 0x06000000u) / 4u] = v; }
void sat_scu_set_ims(u32 m) { (void)m; }
void sat_scu_state(u32 *ims, u32 *ist) { *ims = 0, *ist = 0; }
SatNative sat_bup_native(u32 addr) { (void)addr; return 0; }
void sat_bup_init(void) {}

static void check(int ok, const char *what)
{
    if (!ok) g_fail++;
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
}
static u32 call(u32 slot, u32 a, u32 b)
{
    SatNative f = sat_bios_native(rd32(slot));
    return f ? f(a, b, 0, 0) : 0xdeadu;
}

int main(void)
{
    sat_bios_init();
    call(0x06000300u, 0x4bu, 0x060a54c0u);          /* SYS_SETSINT */
    call(0x06000310u, 0x4bu, 0u);                   /* SYS_SETUINT */
    check(rd32(0x0600012cu) == 0x060a54c0u, "SETUINT(0x4b, 0) leaves the vector SETSINT set");
    check(call(0x06000304u, 0x4bu, 0u) == 0x060a54c0u, "GETSINT returns the vector");
    check(call(0x06000314u, 0x4bu, 0u) == 0u, "GETUINT returns the user handler, 0");
    call(0x06000310u, 0x4bu, 0x06012345u);
    check(call(0x06000314u, 0x4bu, 0u) == 0x06012345u && rd32(0x0600012cu) == 0x060a54c0u,
          "SETUINT stores its own handler apart from the vector");
    if (!g_fail) printf("all ok\n");
    return g_fail ? 1 : 0;
}
