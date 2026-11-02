"""The sound driver translated to C (tools/arm7_translate.py, port/runtime/arm7_native.h) against the interpreter: random
ARM programs (data processing with every operand form and condition, loads and stores to sound RAM, to the device bus
and into the program's own words, block transfers, halfwords, multiplies, swaps, PSR transfers, loops, calls and
returns) are run once on the interpreter with the executed-address recorder, translated from that record, and run
again on the interpreter and the translated blocks side by side in random slices with the FIQ line toggled. After
every slice the whole state must be equal: registers, flags, PSR, banks, next address, cycles left, instructions
executed, bus accesses, the sound RAM, and a hash of every device access with the instruction and cycle counts it
saw. The C part only where Visual Studio's 32-bit tools are found.
Run: python -W error tests/test_arm7_native.py
"""
import pathlib
import random
import subprocess
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL / "tools"))

import arm7_translate  # noqa: E402
import test_port_arm7 as t  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402
from test_port_audio import work_dir  # noqa: E402

MAIN_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "arm7.h"
#ifdef CMP
extern const Arm7Native arm7_driver_native;
#endif

void port_log(const char *fmt, ...) { (void)fmt; }
#define RAM 0x200000u
static u8 g_ram[3][RAM];
static u64 g_hash[3];
static u32 g_seed;
static u32 rnd(void) { g_seed = g_seed * 1664525u + 1013904223u; return g_seed >> 8; }

static void mix(Arm7 *a, u64 x)
{
    u64 *h = (u64 *)a->user;
    *h = (*h ^ x) * 0x100000001b3ull;
}
static u32 dev_read(Arm7 *a, u32 addr, u32 size)
{
    mix(a, addr), mix(a, size), mix(a, a->executed), mix(a, (u64)a->cycles), mix(a, a->next);
    return (u32)(addr * 2654435761u + (u32)a->bus * 40503u);
}
static void dev_write(Arm7 *a, u32 addr, u32 value, u32 size)
{
    mix(a, addr), mix(a, value), mix(a, size), mix(a, a->executed), mix(a, (u64)a->cycles), mix(a, a->next);
}

static int differs(const Arm7 *x, const Arm7 *y, const char **what)
{
    if (memcmp(x->r, y->r, sizeof x->r)) { *what = "registers"; return 1; }
    if (x->n != y->n || x->z != y->z || x->c != y->c || x->v != y->v) { *what = "flags"; return 1; }
    if (x->psr != y->psr || x->spsr != y->spsr) { *what = "psr"; return 1; }
    if (memcmp(x->bank, y->bank, sizeof x->bank) || memcmp(x->usr_hi, y->usr_hi, sizeof x->usr_hi) ||
        memcmp(x->fiq_hi, y->fiq_hi, sizeof x->fiq_hi)) { *what = "banks"; return 1; }
    if (x->next != y->next) { *what = "next"; return 1; }
    if (x->cycles != y->cycles) { *what = "cycles"; return 1; }
    if (x->executed != y->executed) { *what = "executed"; return 1; }
    if (x->bus != y->bus || x->fiqs != y->fiqs || x->undefined != y->undefined) { *what = "bus/fiqs/undefined"; return 1; }
    return 0;
}

int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(g_ram[0], 1, RAM, f) < 4) return 2;
    fclose(f);
    g_seed = (u32)atoi(argv[2]);
    memcpy(g_ram[1], g_ram[0], RAM);
    memcpy(g_ram[2], g_ram[0], RAM);
    Arm7 a[2];
    memset(a, 0, sizeof a);
    u32 fiq_open = rnd() & 1u, init[13];
    for (int i = 0; i < 13; i++) init[i] = i >= 8 ? 0 : rnd();
    init[8] = 0x8000u, init[9] = 0x8400u, init[10] = 0x8800u, init[11] = 0x300u, init[12] = rnd();
    init[7] = 0;
    u32 fl = rnd();
#ifndef CMP
    u8 *pcs = (u8 *)calloc(RAM / 4, 1);
#endif
    for (int v = 0; v < 2; v++) {
        a[v].ram = g_ram[v], a[v].ram_mask = RAM - 1u, a[v].ram_size = RAM;
        a[v].rd = dev_read, a[v].wr = dev_write, a[v].user = &g_hash[v];
        arm7_reset(&a[v]);
        if (fiq_open) arm7_set_cpsr(&a[v], 0x13u);
        for (int i = 0; i < 13; i++) a[v].r[i] = init[i];
        a[v].n = fl & 1u, a[v].z = fl >> 1 & 1u, a[v].c = fl >> 2 & 1u, a[v].v = fl >> 3 & 1u;
    }
#ifdef CMP
    a[1].native = &arm7_driver_native;
#else
    a[0].pcs = pcs;
#endif
    u32 s2 = g_seed;
    int chunks = atoi(argv[3]);
    for (int chunk = 0; chunk < chunks; chunk++) {
        g_seed = s2 + (u32)chunk * 7919u;
        s64 cycles = 1 + rnd() % 900u;
        if (argc > 5) cycles = atoi(argv[5]);
        int fiq = fiq_open && rnd() % 6u == 0;
        for (int v = 0; v < 2; v++) {
#ifdef REC
            if (v == 1) continue;
#endif
            a[v].fiq = fiq;
            arm7_run(&a[v], cycles);
        }
#ifdef CMP
        const char *what = "";
        int bad = differs(&a[0], &a[1], &what);
        if (!bad && memcmp(g_ram[0], g_ram[1], RAM)) bad = 1, what = "sound RAM";
        if (!bad && g_hash[0] != g_hash[1]) bad = 1, what = "device accesses";
        if (bad) {
            printf("DIFF chunk %d (%lld cycles) in %s: executed %llu/%llu cycles %lld/%lld next %x/%x\n", chunk, (long long)cycles, what,
                   (unsigned long long)a[0].executed, (unsigned long long)a[1].executed, (long long)a[0].cycles,
                   (long long)a[1].cycles, a[0].next, a[1].next);
            for (int i = 0; i < 16; i++)
                if (a[0].r[i] != a[1].r[i]) printf("  r%d %x/%x\n", i, a[0].r[i], a[1].r[i]);
            printf("  nzcv %u%u%u%u/%u%u%u%u psr %x/%x native blocks %llu\n", a[0].n, a[0].z, a[0].c, a[0].v, a[1].n, a[1].z,
                   a[1].c, a[1].v, a[0].psr, a[1].psr, (unsigned long long)a[1].native_blocks);
            return 1;
        }
#endif
    }
#ifdef CMP
    printf("OK blocks %llu instructions %llu of %llu executed\n", (unsigned long long)a[1].native_blocks,
           (unsigned long long)a[1].native_instr, (unsigned long long)a[1].executed);
#else
    FILE *o = fopen(argv[4], "wb");
    fwrite(pcs, 1, RAM / 4, o);
    fclose(o);
    printf("recorded\n");
#endif
    return 0;
}
"""


def with_cond(w, cond):
    return (w & 0x0fffffff) | cond << 28


class Gen:
    def __init__(self, rng):
        self.rng = rng

    def cond(self):
        return t.AL if self.rng.random() < 0.7 else self.rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15])

    def dp(self):
        g = self.rng
        op = g.choice(list(t.OPS))
        rd = g.randrange(0, 7)
        rn = 15 if g.random() < 0.05 else g.randrange(0, 13)
        s = g.random() < 0.5
        form = g.random()
        if form < 0.4:
            return t.enc_dp(op, rd, rn, imm=g.randrange(256), rot=g.randrange(16), s=s, cond=self.cond())
        rm = 15 if g.random() < 0.04 else g.randrange(0, 13)
        if form < 0.85:
            return t.enc_dp(op, rd, rn, rm=rm, sh=g.choice(list(t.SHIFTS)), amount=g.choice([0, 1, 5, 31, g.randrange(32)]),
                            s=s, cond=self.cond())
        return t.enc_dp(op, rd, rn, rm=g.randrange(0, 7), sh=g.choice(list(t.SHIFTS)), rs=g.randrange(0, 7), s=s,
                        cond=self.cond())

    def base(self):
        g = self.rng
        x = g.random()
        if x < 0.12:
            return 11  # points into the program's own words
        if x < 0.2:
            return g.randrange(0, 7)  # wild: mostly the device bus
        return g.choice([8, 9, 10])

    def ldst(self):
        g = self.rng
        rn = self.base()
        rm = g.randrange(0, 7) if g.random() < 0.1 else None
        return t.enc_ldst(g.random() < 0.55, g.randrange(0, 7), rn, g.randrange(0, 0x100), byte=g.random() < 0.4,
                          pre=g.random() < 0.7, up=g.random() < 0.7, wb=g.random() < 0.3, rm=rm, cond=self.cond())

    def other(self):
        g = self.rng
        k = g.random()
        if k < 0.25:
            return with_cond(t.enc_half(g.random() < 0.5, g.randrange(0, 7), self.base(), g.randrange(0, 0x40, 2),
                                        g.choice([1, 1, 2, 3]), pre=g.random() < 0.7, up=g.random() < 0.7, wb=g.random() < 0.3),
                             self.cond())
        if k < 0.55:
            regs = sorted(g.sample(range(0, 7), g.randrange(1, 5)))
            return with_cond(t.enc_block(g.random() < 0.5, g.choice([8, 9, 10, 11]), regs, pre=g.random() < 0.5,
                                         up=g.random() < 0.5, wb=g.random() < 0.5), self.cond())
        if k < 0.7:
            rd, rm, rs = g.sample(range(0, 7), 3)
            return with_cond(t.enc_mul(rd, rm, rs, acc=g.randrange(0, 7) if g.random() < 0.5 else None, s=g.random() < 0.5), self.cond())
        if k < 0.78:
            return with_cond(t.enc_swp(g.randrange(0, 7), g.randrange(0, 7), g.choice([8, 9, 10]), byte=g.random() < 0.5), self.cond())
        if k < 0.9:
            return with_cond(t.enc_mrs(g.randrange(0, 7)), self.cond())
        return with_cond(t.enc_msr(g.randrange(0, 7), fields=8), self.cond())

    def instr(self):
        x = self.rng.random()
        if x < 0.5:
            return self.dp()
        if x < 0.8:
            return self.ldst()
        return self.other()


def random_program(seed):
    rng = random.Random(seed)
    g = Gen(rng)
    nseg = rng.randint(25, 50)
    segs = []
    for _ in range(nseg):
        body = [g.instr() for _ in range(rng.randint(2, 9))]
        for j in range(len(body)):
            if rng.random() < 0.1:  # a store into the block's own later words: str/strb rX, [pc, #4*d - 8]
                d = rng.randrange(0, 6)
                body[j] = t.enc_ldst(False, rng.randrange(0, 7), 15, abs(4 * d - 8), byte=rng.random() < 0.3, up=4 * d >= 8)
        segs.append((body, rng.choice(["fall", "b", "cb", "loop", "call", "cb", "b"]), rng.randint(2, 6)))
    # layout: each segment body, then its terminator words
    sizes = []
    for body, term, _ in segs:
        extra = {"fall": 0, "b": 1, "cb": 1, "loop": 3, "call": 1}[term]
        sizes.append(len(body) + extra + (1 if term == "loop" else 0))
    start = [0x100]
    for sz in sizes:
        start.append(start[-1] + 4 * sz)
    leaf = start[-1]
    leaf_words = [g.dp() for _ in range(3)] + [t.enc_dp("mov", 15, 0, rm=14)]
    halt_at = leaf + 4 * len(leaf_words)
    img = bytearray(0x200000)

    def put(at, w):
        img[at:at + 4] = (w & 0xffffffff).to_bytes(4, "little")
    put(0x00, t.enc_b(0, 0x100))
    put(0x04, t.HALT)
    put(0x08, t.enc_dp("mov", 15, 0, rm=14, s=True))
    put(0x1c, t.enc_dp("sub", 15, 14, imm=4, s=True))
    for k, w in enumerate(leaf_words):
        put(leaf + 4 * k, w)
    put(halt_at, t.HALT)
    for i, (body, term, cnt) in enumerate(segs):
        at = start[i]
        nxt = start[i + 1] if i + 1 < len(segs) else halt_at
        skip = start[i + 2] if i + 2 < len(segs) else halt_at
        pre = [t.enc_dp("mov", 7, 0, imm=cnt)] if term == "loop" else []
        words = pre + body
        loop_head = at + 4 * len(pre)
        for k, w in enumerate(words):
            put(at + 4 * k, w)
        e = at + 4 * len(words)
        if term == "b":
            put(e, t.enc_b(e, nxt))
        elif term == "cb":
            put(e, t.enc_b(e, skip, cond=rng.choice([0, 1, 2, 3, 4, 5, 10, 11])))
        elif term == "call":
            put(e, t.enc_b(e, leaf, link=True))
        elif term == "loop":
            put(e, t.enc_dp("sub", 7, 7, imm=1, s=True))
            put(e + 4, t.enc_b(e + 4, loop_head, cond=1))
            put(e + 8, t.enc_b(e + 8, nxt))
    # make sure a segment that falls through, falls into the next one
    return bytes(img)


class Arm7Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_VC:
            return
        d = work_dir()
        r, cls.rec = t.build(d, "#define REC 1\n" + MAIN_C, ["arm7.c", "arm7_ls.c", "arm7_scan.c", "arm7_native.c"])
        assert r.returncode == 0, r.stdout + r.stderr
        cls.dir = d

    def run_seed(self, seed, chunks=120):
        d = work_dir()
        img = d / "image.bin"
        img.write_bytes(random_program(seed))
        out = subprocess.run([str(self.rec), str(img), str(seed), "40", str(d / "pcs.bin")], capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        text, n = arm7_translate.generate(img.read_bytes(), (d / "pcs.bin").read_bytes())
        (d / "native.c").write_text(text, encoding="utf-8")
        r, exe = t.build(d, "#define CMP 1\n" + MAIN_C, ["arm7.c", "arm7_ls.c", "arm7_scan.c", "arm7_native.c", str(d / "native.c")])
        self.assertEqual(r.returncode, 0, f"seed {seed}\n" + r.stdout + r.stderr)
        out = subprocess.run([str(exe), str(img), str(seed), str(chunks)], capture_output=True, text=True, timeout=120)
        self.assertTrue(out.stdout.startswith("OK"), f"seed {seed}: {out.stdout}{out.stderr}")
        return out.stdout

    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_random_programs_match_the_interpreter(self):
        """Random programs, run once to record what they execute, translated, and run again side by side: the state
        after every slice is the interpreter's, and the translated blocks did run."""
        ran = 0
        for seed in range(1, 9):
            ran += int(self.run_seed(seed).split("blocks ")[1].split()[0])
        self.assertGreater(ran, 200)


if __name__ == "__main__":
    unittest.main()
