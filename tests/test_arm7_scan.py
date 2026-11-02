"""The kit's AICA ARM7 scan fast paths (port/runtime/arm7_scan.c) against the interpreter (arm7.c), and the game.json
switch for them (aica_arm_fast). Random scan loops of all three kinds; the whole ARM state is compared after each slice.
The C part only where Visual Studio's 32-bit tools are found (as tests/test_port_kit.py); the harness is the one of
tests/test_port_arm7.py.
Run: python -W error tests/test_arm7_scan.py
"""
import pathlib
import subprocess
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402
from test_port_arm7 import build  # noqa: E402
from test_port_audio import work_dir  # noqa: E402


# The scan fast paths (arm7_scan.c) against the interpreter: random scan loops of both kinds over random tables, run
# in random slices with the FIQ line and mask toggled, the whole ARM state compared after each slice.
SCAN_MAIN_C = r"""#include <stdio.h>
#include <string.h>
#include "arm7.h"

void port_log(const char *fmt, ...) { (void)fmt; }
static u32 dev_read(Arm7 *a, u32 addr, u32 size) { (void)a; (void)size; return addr; }
static void dev_write(Arm7 *a, u32 addr, u32 value, u32 size) { (void)a; (void)addr, (void)value, (void)size; }
static u32 g_seed = 12345;
static u32 rnd(void) { g_seed = g_seed * 1664525u + 1013904223u; return g_seed >> 8; }

static u8 g_ram[2][0x200000];

static u32 b_at(u32 at, u32 target, u32 cond) { return cond << 28 | 0x0a000000u | (((target - at - 8u) >> 2) & 0xffffffu); }

int main(int argc, char **argv)
{
    int rounds = argc > 1 ? atoi(argv[1]) : 2000, bad = 0, scanned = 0;
    u64 total = 0, by_kind[3] = {0, 0, 0};
    for (int round = 0; round < rounds && !bad; round++) {
        u32 kind = rnd() % 3u, h = 0x100u + 4u * (rnd() & 0xffu), rt = rnd() % 8u, rb = 8u + rnd() % 3u, rc = 11u + rnd() % 2u;
        u32 step = 1u + rnd() % 80u, imm = 1u << (rnd() % 8u), lim = 1u + rnd() % 200u, n_flag = 1u + rnd() % 30u;
        u32 base = 0x2000u + (rnd() & 0xfffu), cnt = kind == 1 ? rnd() % 150u : (rnd() % 4u ? rnd() % lim : rnd() % 300u);
        if (kind == 2) cnt = base + step * (rnd() % 4u ? 1u + rnd() % 60u : rnd() % 300u);
        u32 code[8], n = 0;
        if (rt == rb || rt == rc) rt = 0;
        if (kind == 0) {
            code[n++] = 0xe5d00000u | rb << 16 | rt << 12;
            code[n++] = 0xe3100000u | rt << 16 | imm;
            code[n++] = b_at(h + 8u, h + 28u, 1);
            code[n++] = 0xe2800000u | rb << 16 | rb << 12 | step;
            code[n++] = 0xe2800001u | rc << 16 | rc << 12;
            code[n++] = 0xe3500000u | rc << 16 | lim;
            code[n++] = b_at(h + 24u, h, 1);
        } else if (kind == 2) {
            code[n++] = 0xe2800000u | rb << 16 | rb << 12 | step;
            code[n++] = 0xe1500000u | rb << 16 | rc;
            code[n++] = 0x03a00000u | rt << 12;
            code[n++] = b_at(h + 12u, h + 28u, 0);
            code[n++] = 0xe5d00000u | rb << 16 | rt << 12;
            code[n++] = 0xe3100000u | rt << 16 | imm;
            code[n++] = b_at(h + 24u, h, 0);
        } else {
            code[n++] = 0xe5d00000u | rb << 16 | rt << 12;
            code[n++] = 0xe1b00000u | rt << 12 | rt;
            code[n++] = b_at(h + 8u, h + 16u, 0);
            code[n++] = b_at(h + 12u, h + 28u, 14);
            code[n++] = 0xe2800000u | rb << 16 | rb << 12 | step;
            code[n++] = 0xe2500001u | rc << 16 | rc << 12;
            code[n++] = b_at(h + 24u, h, 1);
        }
        for (int v = 0; v < 2; v++) {
            memset(g_ram[v], 0, sizeof g_ram[v]);
            for (u32 i = 0; i < n; i++) memcpy(g_ram[v] + h + 4u * i, &code[i], 4);
            u32 mem = 0xe1a00000u;  /* an exit: a loop of nops that ends in b . */
            (void)mem;
        }
        u32 end = h + 28u, trap = 0xeafffffeu;
        for (int v = 0; v < 2; v++) {
            memcpy(g_ram[v] + end, &trap, 4);
            memcpy(g_ram[v] + 0x1c, &trap, 4);
        }
        /* the table: zero bytes (for kind A: bytes without imm bits) with some flagged ones */
        u32 save = g_seed;
        for (int v = 0; v < 2; v++) {
            g_seed = save;
            for (u32 i = 0; i < 0x1000u; i++) {
                u32 addr = base + i;
                u32 b = kind == 1 ? 0u : (rnd() & 0xffu & ~imm);
                if (rnd() % 1000u < n_flag) b = kind == 1 ? 1u + (rnd() & 0xffu) : (b | imm);
                g_ram[v][addr] = (u8)b;
            }
        }
        Arm7 a[2];
        memset(a, 0, sizeof a);
        u32 fiq_unmasked = rnd() & 1u;
        for (int v = 0; v < 2; v++) {
            a[v].ram = g_ram[v];
            a[v].ram_mask = sizeof g_ram[v] - 1u;
            a[v].ram_size = 0x800000u;
            a[v].rd = dev_read;
            a[v].wr = dev_write;
            arm7_reset(&a[v]);
            a[v].fast = v;
            if (fiq_unmasked) arm7_set_cpsr(&a[v], 0x13u);
            a[v].next = h;
            a[v].r[rb] = base;
            a[v].r[rc] = cnt;
            a[v].r[rt] = rnd() & 0xffu;
            a[v].n = 1, a[v].z = 1, a[v].c = 0, a[v].v = 1;
            if (a[v].fast) a[v].r[rt] = a[0].r[rt];
        }
        u32 seed2 = g_seed;
        for (int chunk = 0; chunk < 40 && !bad; chunk++) {
            g_seed = seed2 + (u32)chunk * 7919u;
            s64 cycles = 1 + rnd() % 900u;
            int fiq = fiq_unmasked && rnd() % 8u == 0;
            Arm7 pre[2] = {a[0], a[1]};
            for (int v = 0; v < 2; v++) {
                a[v].fiq = fiq;
                arm7_run(&a[v], cycles);
            }
            scanned = (int)a[1].scanned;
            if (memcmp(a[0].r, a[1].r, 15 * 4) || a[0].next != a[1].next || a[0].n != a[1].n || a[0].z != a[1].z ||
                a[0].c != a[1].c || a[0].v != a[1].v || a[0].psr != a[1].psr || a[0].cycles != a[1].cycles ||
                a[0].executed != a[1].executed || a[0].spsr != a[1].spsr) {
                printf("FAIL round %d kind %u chunk %d cycles %lld: executed %llu/%llu cycles %lld/%lld next %x/%x nzcv %u%u%u%u/%u%u%u%u\n",
                       round, kind, chunk, (long long)cycles, (unsigned long long)a[0].executed,
                       (unsigned long long)a[1].executed, (long long)a[0].cycles, (long long)a[1].cycles, a[0].next,
                       a[1].next, a[0].n, a[0].z, a[0].c, a[0].v, a[1].n, a[1].z, a[1].c, a[1].v);
                for (int i = 0; i < 15; i++)
                    if (a[0].r[i] != a[1].r[i]) printf("  r%d %x/%x\n", i, a[0].r[i], a[1].r[i]);
                bad = 1;
                if (argc > 2) {
                    Arm7 t = pre[0];
                    t.fiq = fiq;
                    printf("pre: next %x cycles %lld executed %llu fiq %d psr %x r[rb]=%x r[rc]=%x h=%x step=%u\n", pre[0].next, (long long)pre[0].cycles, (unsigned long long)pre[0].executed, fiq, pre[0].psr, pre[0].r[rb], pre[0].r[rc], h, step);
                    s64 left = pre[0].cycles + cycles;
                    for (int i = 0; i < 400 && left > 0; i++) {
                        printf("%x(%lld) ", t.next, (long long)left);
                        t.cycles = 1;
                        arm7_run(&t, 0);
                        left -= 1 - t.cycles;
                    }
                    printf("\n");
                }
            }
        }
        total += a[1].scanned;
        by_kind[kind] += a[1].scanned;
        g_seed = seed2 + 1u;
        (void)scanned;
    }
    printf("iterations skipped: %llu (A %llu, B %llu, C %llu)\n", (unsigned long long)total,
           (unsigned long long)by_kind[0], (unsigned long long)by_kind[1], (unsigned long long)by_kind[2]);
    printf(bad ? "BAD\n" : "OK\n");
    return bad;
}
"""


class ArmScan(unittest.TestCase):
    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_scan_fast_paths_match_the_interpreter(self):
        """2000 random scan loops (kinds A, B and C, random steps, limits, counts, flagged bytes, run in random slices of
        1..900 cycles, the FIQ line up and the mask open at times) end every slice in the interpreter's state:
        registers, flags, CPSR, next address, cycles left and instructions executed."""
        d = work_dir()
        r, exe = build(d, SCAN_MAIN_C.replace("#include <string.h>", "#include <string.h>\n#include <stdlib.h>"),
                       ["arm7.c", "arm7_ls.c", "arm7_scan.c", "arm7_native.c"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = subprocess.run([str(exe), "2000", "v"], capture_output=True, text=True, timeout=300)
        self.assertEqual(out.stdout.strip().split("\n")[-1], "OK", out.stdout)
        self.assertGreater(int(out.stdout.split("iterations skipped: ")[1].split()[0]), 20000, out.stdout)
        for kind in ("A ", "B ", "C "):
            self.assertGreater(int(out.stdout.split(kind)[1].split(",")[0].split(")")[0]), 1000, out.stdout)


class ArmSwitch(unittest.TestCase):
    def cfg(self, **extra):
        cfg = {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
               "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": [], "aica_arm": True}
        cfg.update(extra)
        return game_config.port_config_h(cfg)

    def test_game_json_switch_for_the_scan_fast_path(self):
        """aica_arm_fast defines PORT_AICA_ARM_FAST; it is off by default and needs aica_arm."""
        self.assertIn("PORT_AICA_ARM_FAST", self.cfg(aica_arm_fast=True))
        self.assertNotIn("PORT_AICA_ARM_FAST", self.cfg(aica_arm_fast=False))
        self.assertNotIn("PORT_AICA_ARM_FAST", self.cfg())


if __name__ == "__main__":
    unittest.main()
