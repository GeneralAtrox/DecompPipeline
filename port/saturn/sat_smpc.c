/* The SMPC (0x00100000): the system manager. A command written to COMREG runs at once and clears SF. INTBACK returns
 * the status block (clock, region, system flags) and then the peripheral data, in OREG, and raises the SCU's
 * system-manager interrupt for each packet: the game's handler copies OREG and, when SR says more is coming (NPE),
 * writes IREG0 = CONTINUE for the next. SNDON / SNDOFF start and stop the sound CPU (sat_scsp.c). */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"

#define SMPC_LO 0x00100000u
#define OREG(n) (0x21u + 2u * (n))
#define IREG0 0x01u
#define IREG1 0x03u
#define SR_PDE 0x80u
#define SR_RESB 0x40u
#define SR_NPE 0x20u
#define SR_PDL 0x10u

static u8 g_reg[0x80];
static int g_peri_pending; /* an INTBACK that asked for peripheral data and has not had it yet */

static u32 smpc_read(u32 p, u32 size)
{
    u32 o = (p - SMPC_LO) & 0x7fu;
    (void)size;
    if (o == 0x75u) return 0xffu; /* PDR1: no peripherals through the direct port */
    return g_reg[o];
}

static int n_logged;

/* OREG0..30: SETTIME flag, the clock (BCD), cartridge code, area code, system status, SMEM */
static void intback_status(void)
{
    memset(g_reg + 0x20, 0, 0x40);
    g_reg[OREG(0)] = 0x80;  /* SETTIME not done */
    g_reg[OREG(1)] = 0x19;  /* 1997-01-01 00:00:00, a Wednesday */
    g_reg[OREG(2)] = 0x97;
    g_reg[OREG(3)] = 0x31;
    g_reg[OREG(4)] = 0x01;
    g_reg[OREG(8)] = 0x00;  /* no cartridge */
    g_reg[OREG(9)] = 0x0c;  /* area code: Europe */
    g_reg[OREG(10)] = 0x34; /* system status 1 */
    g_reg[OREG(11)] = 0x00;
    g_reg[OREG(31)] = 0x00;
}

/* each port: the digital pad (F1 02 data0 data1), or nothing connected (F0) */
static void intback_peripheral(void)
{
    static u32 last = 0xffffffffu, n_diff;
    u32 pads[2], there = sat_pad_data(pads), d = pads[0];
    if (d != last && n_diff++ < 200) port_log("smpc: vb %u the game read pad data %04x\n", sat_vblank_count(), d);
    last = d;
    memset(g_reg + 0x20, 0, 0x40);
    g_reg[OREG(0)] = 0xf1;
    g_reg[OREG(1)] = 0x02;
    g_reg[OREG(2)] = (u8)d;
    g_reg[OREG(3)] = (u8)(d >> 8);
    g_reg[OREG(4)] = there & 2u ? 0xf1 : 0xf0;
    if (there & 2u) {
        g_reg[OREG(5)] = 0x02;
        g_reg[OREG(6)] = (u8)pads[1];
        g_reg[OREG(7)] = (u8)(pads[1] >> 8);
    }
}

static void command(u32 cmd)
{
    switch (cmd) {
    case 0x10: { /* INTBACK */
        int want_status = g_reg[IREG0] & 1, want_peri = (g_reg[IREG1] & 8) != 0;
        static int n_intback;
        if (sat_vblank_count() >= 1000 && n_intback++ < 400) port_log("smpc: vb %u INTBACK ireg %02x %02x %02x\n", sat_vblank_count(), g_reg[IREG0], g_reg[IREG1], g_reg[0x05]);
        if (want_status) {
            intback_status();
            g_peri_pending = want_peri;
            g_reg[0x61] = (u8)(SR_RESB | (want_peri ? SR_NPE : 0));
        } else if (want_peri) {
            intback_peripheral();
            g_peri_pending = 0;
            g_reg[0x61] = (u8)(SR_PDE | SR_PDL);
        } else {
            break;
        }
        sat_scu_raise(7u);
        break;
    }
    case 0x02: /* SSHON: the slave comes up in the BIOS idle loop (the next MINIT starts its program) */
    case 0x03: /* SSHOFF */
        sat_slave_off();
        break;
    case 0x06: /* SNDON: the 68K starts running the driver the SH-2 loaded */
    case 0x07: /* SNDOFF */
        sat_scsp_cpu(cmd == 0x06u);
        break;
    default:
        break;
    }
    g_reg[0x63] = 0; /* SF clear */
    if (n_logged++ < 12) port_log("smpc: command %02x\n", cmd);
}

static void smpc_write(u32 p, u32 size, u32 value)
{
    u32 o = (p - SMPC_LO) & 0x7fu;
    for (u32 i = 0; i < size; i++) g_reg[(o + i) & 0x7fu] = (u8)(value >> (8u * (size - 1u - i)));
    if (o == 0x1fu) {
        command(value & 0xffu);
    } else if (o == IREG0 && (value & 0x80u) && g_peri_pending) { /* INTBACK CONTINUE: the peripheral data */
        g_peri_pending = 0;
        intback_peripheral();
        g_reg[0x61] = (u8)(SR_PDE | SR_PDL);
        sat_scu_raise(7u);
    } else if (o == IREG0 && (value & 0x80u)) {
        port_log("smpc: vb %u INTBACK CONTINUE with nothing pending\n", sat_vblank_count());
    } else if (o == IREG0 && (value & 0x40u)) { /* INTBACK BREAK */
        g_peri_pending = 0;
    }
}

/* MINIT / SINIT (0x01000000 / 0x01800000): one SH-2 signals the other's input capture. The slave is not run yet: a
 * signal is logged (the first few) so the game's use of it shows in the boot trace. */
static u32 sig_read(u32 p, u32 size)
{
    (void)p;
    (void)size;
    return 0;
}
static void sig_write(u32 p, u32 size, u32 value)
{
    static int n;
    (void)size;
    if (n++ < 20) port_log("signal: %s = %x\n", p < 0x01800000u ? "MINIT" : "SINIT", value);
    if (p < 0x01800000u) sat_minit();
    else sat_sinit();
}

void sat_smpc_init(void)
{
    sat_map(0x01000000u, 0x01010000u, "MINIT", sig_read, sig_write);
    sat_map(0x01800000u, 0x01810000u, "SINIT", sig_read, sig_write);
    memset(g_reg, 0, sizeof g_reg);
    sat_map(SMPC_LO, SMPC_LO + 0x10000u, "SMPC", smpc_read, smpc_write);
}
