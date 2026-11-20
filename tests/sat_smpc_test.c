/* The SMPC's INTBACK (sat_smpc.c, included whole for its static functions), driven by byte writes to IREG0..2 and
 * COMREG as SBL's handler makes them (Saturn Bomberman, 0x0609d0aa): status and peripheral data with CONTINUE by an
 * inverted IREG0 bit 7, BREAK, a peripheral-only INTBACK (Robo Pit), a port in 0-byte mode and a report split over two
 * packets. Run by tests/test_sat_smpc.py, built with /DSAT_SMPC_HARDWARE (game.json "smpc_intback": "hardware") for those
 * checks, and without it for the older report a game without the key keeps (SR 0x90, one packet, only a CONTINUE with
 * bit 7 set answered). */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_smpc.c"

static int g_fail, g_raised;
static u32 g_pad[2] = {0xfff7u, 0xffffu}, g_there = 1u; /* pad 1 holds Start; pad 2 absent */

/* a stand-in for each kit function sat_smpc.c calls */
void port_log(const char *fmt, ...) { (void)fmt; }
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr) { (void)lo, (void)hi, (void)name, (void)rd, (void)wr; }
u32 sat_vblank_count(void) { return 8000; }
u32 sat_sys_hz(void) { return 60; }
void sat_scu_raise(u32 bit) { g_raised += bit == 7u; }
u32 sat_pad_data(u32 data[2]) { data[0] = g_pad[0], data[1] = g_pad[1]; return g_there; }
void sat_slave_off(void) {}
void sat_scsp_cpu(int on) { (void)on; }
void sat_minit(void) {}
void sat_sinit(void) {}
int session_note_replayed(const char *name, void *buf, u32 size) { (void)name, (void)buf, (void)size; return 0; }
int session_replaying(void) { return 0; }
void session_save_read(const char *name, const void *buf, u32 size, int got) { (void)name, (void)buf, (void)size, (void)got; }
void host_local_time(HostTime *t) { memset(t, 0, sizeof *t); }

static void check(int ok, const char *what)
{
    if (!ok) g_fail++;
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
}

static void w8(u32 reg, u32 v) { smpc_write(SMPC_LO + reg, 1, v); }
static u32 r8(u32 reg) { return smpc_read(SMPC_LO + reg, 1); }
static u32 sr(void) { return r8(0x61); }

static void intback(u32 ireg0, u32 ireg1)
{
    w8(IREG0, ireg0);
    w8(IREG1, ireg1);
    w8(0x05, 0xf0);
    w8(0x1f, 0x10);
}

/* port 1's report: F1 02 and Start held (data byte 0 = f7), port 2 nothing (F0) */
static int pad_report(void)
{
    return r8(OREG(0)) == 0xf1 && r8(OREG(1)) == 0x02 && r8(OREG(2)) == 0xf7 && r8(OREG(3)) == 0xff && r8(OREG(4)) == 0xf0;
}

#ifdef SAT_SMPC_HARDWARE
int main(void)
{
    u8 cont = 0; /* SBL's copy of IREG0 bit 7: inverted for each CONTINUE */
    sat_smpc_init();
    for (int round = 0; round < 2; round++) { /* the second round's CONTINUE is 0x00 */
        int n = g_raised;
        intback(0x01, 0x08);
        check(g_raised == n + 1 && !(sr() & 0x80u) && (sr() & 0x20u), "status: interrupt, SR bit 7 clear, NPE set");
        check(sr() == 0x60u, "status SR as the kit always gave it (0x60)");
        cont ^= 0x80u;
        w8(IREG0, cont);
        check(g_raised == n + 2, round ? "CONTINUE 0x00 (bit 7 inverted back) answered" : "CONTINUE 0x80 answered");
        check((sr() & 0xc0u) == 0xc0u, "first peripheral packet: SR & 0xc0 = 0xc0 (SBL takes it)");
        check(!(sr() & 0x10u), "RESB clear: the reset button is not held");
        check(!(sr() & 0x20u), "NPE clear: two pads fit one packet");
        check(pad_report(), "OREG: F1 02 f7 ff, F0 (Start held on port 1)");
        w8(IREG0, (u32)cont | 0x40u); /* SBL's closing BREAK, bit 7 kept */
        check(g_raised == n + 2, "a BREAK after the last packet raises nothing");
    }
    {
        int n = g_raised;
        intback(0x01, 0x08);
        w8(IREG0, (cont ^ 0x80u) | 0x40u); /* BREAK with bit 7 inverted: still a BREAK */
        w8(IREG0, cont ^ 0x80u);
        check(g_raised == n + 1, "BREAK after the status ends the command: a CONTINUE then gets nothing");
    }
    {
        int n = g_raised;
        intback(0x00, 0x08);
        check(g_raised == n + 1 && sr() == 0xc0u && pad_report(), "peripheral-only INTBACK: one packet, SR 0xc0");
    }
    {
        g_there = 3u;
        intback(0x00, 0xc8); /* port 2 in 0-byte mode */
        check(r8(OREG(4)) == 0x00 && (sr() & 0x0fu) == 0x0cu, "0-byte mode: port 2 left out, SR has the port modes");
        intback(0x00, 0x08);
        check(r8(OREG(4)) == 0xf1 && r8(OREG(5)) == 0x02 && r8(OREG(6)) == 0xff, "a second pad reported on port 2");
        g_there = 1u;
    }
    {
        int n = g_raised;
        intback(0x01, 0x08);
        for (u32 i = 0; i < 40u; i++) g_peri[i] = (u8)(i + 1u);
        g_peri_len = 40u, g_peri_at = 0;
        peripheral_packet(1); /* a 40-byte report */
        check((sr() & 0xe0u) == 0xe0u && r8(OREG(31)) == 32u, "a long report: the first 32 bytes, PDL and NPE set");
        cont ^= 0x80u;
        w8(IREG0, cont);
        check(g_raised == n + 3 && (sr() & 0xe0u) == 0x80u && r8(OREG(0)) == 33u && r8(OREG(7)) == 40u && r8(OREG(8)) == 0,
              "its CONTINUE: the last 8 bytes, SR 0x80");
    }
    printf(g_fail ? "%d failed\n" : "all ok\n", g_fail);
    return g_fail != 0;
}
#else
/* the older report: the status reply, a CONTINUE with bit 7 set for the report (SR 0x90, both ports in one packet), a
 * CONTINUE without bit 7 never answered, a peripheral-only INTBACK in one packet, and no 0-byte mode or split */
int main(void)
{
    int n;
    sat_smpc_init();
    n = g_raised;
    intback(0x01, 0x08);
    check(g_raised == n + 1 && sr() == 0x60u, "old: status interrupt, SR 0x60");
    w8(IREG0, 0x00u);
    check(g_raised == n + 1, "old: a CONTINUE of 0x00 is not answered");
    w8(IREG0, 0x80u);
    check(g_raised == n + 2 && sr() == 0x90u, "old: CONTINUE with bit 7 answered, SR 0x90");
    check(pad_report(), "old: OREG F1 02 f7 ff, F0");
    w8(IREG0, 0x80u);
    check(g_raised == n + 2, "old: a second CONTINUE with nothing pending raises nothing");
    n = g_raised;
    intback(0x01, 0x08);
    w8(IREG0, 0x40u);
    w8(IREG0, 0x80u);
    check(g_raised == n + 1, "old: BREAK drops the pending report");
    n = g_raised;
    intback(0x00, 0x08);
    check(g_raised == n + 1 && sr() == 0x90u && pad_report(), "old: peripheral-only INTBACK, one packet, SR 0x90");
    printf(g_fail ? "%d failed\n" : "all ok\n", g_fail);
    return g_fail != 0;
}
#endif
