"""GameCube translator behaviour test: the C the translator writes for single instructions and short sequences is
compiled as DP builds it, run, and compared with a reference interpreter (tests/gc_ppc_ref.py) that shares no code
with it. Before this, no test ran the translator's output for an instruction and checked the result (translator
review, 2026-11-10).

Each case is a few instruction words over r3..r10. It becomes up to three functions that end by returning the
result register, CR (mfcr) or XER (mfxer); every function runs on VECTORS inputs made of edge values and random
words. Memory cases work in a 256-byte window of guest RAM filled with a per-address byte; the window's hash after
the call is compared too. A divide whose quotient the architecture leaves undefined is given other inputs.

Planted wrong answers: plant.c holds hand-written wrong C for `add` (off by one only when rA is 0x80000000) and for
`addze` after `extsh` (the cast fold this test found: the sign carried into bit 32); both must be reported.

Found by this test: (u64)(u32)(s16)x folded to (u64)(s16)x, so adde / addze / subfe after extsb, extsh or lha set CA
from the sign (expr.cast); stwcx. crashed the translator (its address was taken as a displacement).
The text checks run anywhere; the runtime part is skipped without vcvars (MSVC). Run: python tests/test_gc_behaviour.py
"""
import importlib.util
import pathlib
import random
import re
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
GC = TOOL / "targets" / "gamecube_ppc"
RT = GC / "runtime"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(GC / "translator")]

import gc_ppc_ref as ref  # noqa: E402
from test_fmac import vcvars  # noqa: E402
from ppctranslate import expr as X  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402
from ppctranslate.translate import Facts, Signature, TranslateError, translate_function  # noqa: E402

BLR = 0x4E800020
SP, SENTINEL = 0x817FE000, 0x817FF000
WIN, WIN_N, MEMBASE = 0x80400000, 0x100, 0x80400080
VECTORS = 10
EDGE = [0, 1, 0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 0x8000, 0xFFFF8000, 0x7FFF, 0x80, 0xFF, 31, 32, 33, 0x12345678,
        0xDEADBEEF]
XERS = [0, 0x20000000, 0x80000000, 0xC0000000, 0xE0000000, 0xA000007F, 0x40000000]


def x31(xo, d, a, b=0, rc=0, oe=0):
    return 31 << 26 | d << 21 | a << 16 | b << 11 | oe << 10 | xo << 1 | rc


def dform(op, d, a, imm):
    return op << 26 | d << 21 | a << 16 | imm & 0xFFFF


def mform(op, s, a, sh, mb, me, rc=0):
    return op << 26 | s << 21 | a << 16 | sh << 11 | mb << 6 | me << 1 | rc


def bc(bo, bi, off):
    return 16 << 26 | bo << 21 | bi << 16 | off & 0xFFFC


MTXER, MFXER, MFCR = x31(467, 10, 1), x31(339, 3, 1), x31(19, 3, 0)
MEMPRE = [dform(15, 9, 0, 0x8040), dform(24, 9, 9, 0x80), mform(21, 10, 10, 0, 26, 29)]  # r9 = base, r10 = 0..0x3c


def mr3(k):
    return x31(444, k, 3, k)


def cases():
    """[(name, words, result register, observers)]; observers: r (register), c (CR), x (XER)."""
    rnd = random.Random(20261111)
    out = []

    def add(name, words, reg=3, obs="rcx"):
        out.append((name, list(words), reg, obs))
    for name, xo, two in (("add", 266, 1), ("addc", 10, 1), ("adde", 138, 1), ("addme", 234, 0), ("addze", 202, 0),
                          ("subf", 40, 1), ("subfc", 8, 1), ("subfe", 136, 1), ("subfme", 232, 0), ("subfze", 200, 0),
                          ("neg", 104, 0), ("mullw", 235, 1)):
        for oe in (0, 1):
            for rc in (0, 1):
                add(f"{name}{'o' * oe}{'.' * rc}", [MTXER, x31(xo, 5, 3, 4 if two else 0, rc, oe)], 5)
    for name, xo in (("mulhw", 75), ("mulhwu", 11), ("divw", 491), ("divwu", 459)):
        for rc in (0, 1):
            add(f"{name}{'.' * rc}", [MTXER, x31(xo, 5, 3, 4, rc)], 5)
    for name, xo in (("and", 28), ("or", 444), ("xor", 316), ("nand", 476), ("nor", 124), ("eqv", 284), ("andc", 60),
                     ("orc", 412), ("slw", 24), ("srw", 536), ("sraw", 792)):
        for rc in (0, 1):
            add(f"{name}{'.' * rc}", [MTXER, x31(xo, 3, 5, 4, rc)], 5)
    for sh in (0, 1, 7, 31):
        for rc in (0, 1):
            add(f"srawi {sh}{'.' * rc}", [MTXER, x31(824, 3, 5, sh, rc)], 5)
    for name, xo in (("cntlzw", 26), ("extsh", 922), ("extsb", 954)):
        for rc in (0, 1):
            add(f"{name}{'.' * rc}", [MTXER, x31(xo, 3, 5, 0, rc)], 5)
    for name, op in (("addi", 14), ("addis", 15), ("addic", 12), ("addic.", 13), ("subfic", 8), ("mulli", 7)):
        for imm in (0x7FFF, 0xFFFF, rnd.getrandbits(16)):
            add(f"{name} {imm:#x}", [MTXER, dform(op, 5, 3, imm)], 5)
    for name, op in (("ori", 24), ("oris", 25), ("xori", 26), ("xoris", 27), ("andi.", 28), ("andis.", 29)):
        add(name, [MTXER, dform(op, 3, 5, rnd.getrandbits(16))], 5)
    for k in range(6):
        sh, mb, me = (rnd.randrange(32) for _ in range(3))
        add(f"rlwinm {sh},{mb},{me}", [MTXER, mform(21, 3, 5, sh, mb, me, k & 1)], 5)
        add(f"rlwimi {sh},{mb},{me}", [MTXER, mform(20, 3, 4, sh, mb, me, k & 1)], 4)
        add(f"rlwnm {mb},{me}", [MTXER, mform(23, 3, 5, 4, mb, me, k & 1)], 5)
    for f in (0, 3, 7):
        add(f"cmpw cr{f}", [MTXER, x31(0, f << 2, 3, 4)], obs="c")
        add(f"cmplw cr{f}", [MTXER, x31(32, f << 2, 3, 4)], obs="c")
        add(f"cmpwi cr{f}", [MTXER, dform(11, f << 2, 3, rnd.getrandbits(16))], obs="c")
        add(f"cmplwi cr{f}", [MTXER, dform(10, f << 2, 3, rnd.getrandbits(16))], obs="c")
    two = [x31(0, 1 << 2, 3, 4), x31(32, 6 << 2, 5, 6)]                         # cmpw cr1 ; cmplw cr6
    for name, xo in (("crand", 257), ("cror", 449), ("crxor", 193), ("crnand", 225), ("crnor", 33), ("creqv", 289),
                     ("crandc", 129), ("crorc", 417)):
        add(name, two + [19 << 26 | rnd.randrange(32) << 21 | rnd.randrange(4, 8) << 16 |
                         rnd.randrange(24, 28) << 11 | xo << 1], obs="c")
    add("mcrf", two + [19 << 26 | 2 << 23 | 6 << 18], obs="c")
    add("mtcrf", [x31(144, 3, 0) | 0xA5 << 12], obs="c")
    add("mtcrf all", [x31(144, 3, 0) | 0xFF << 12], obs="c")
    add("mcrxr", [MTXER, x31(512, 2 << 2, 0)], obs="cx")
    # the cast fold: a sign-extended value into the 64-bit carry sum
    add("extsh addze", [MTXER, x31(922, 3, 4), x31(202, 5, 4)], 5)
    add("extsb adde", [MTXER, x31(954, 3, 6), x31(138, 5, 6, 4)], 5)
    add("extsh subfe", [MTXER, x31(922, 3, 6), x31(136, 5, 4, 6)], 5)
    add("extsb addme.", [MTXER, x31(954, 3, 6), x31(234, 5, 6, 0, 1)], 5)
    add("srawi addze", [x31(824, 3, 0, 4), x31(202, 5, 0)], 5)                 # the signed divide by 16
    # control flow
    add("bdnz loop", [mform(21, 5, 5, 0, 28, 31), dform(14, 5, 5, 1), x31(467, 5, 9), x31(266, 3, 3, 4),
                      bc(16, 0, -4)], 3, "r")
    add("blt skip", [x31(0, 0, 3, 4), bc(12, 0, 8), x31(444, 4, 3, 4)], 3, "rc")
    add("bge cr3", [x31(32, 3 << 2, 3, 4), bc(4, 12, 8), dform(14, 3, 3, 5)], 3, "rc")
    add("bdnzf", [mform(21, 5, 5, 0, 29, 31), dform(14, 5, 5, 1), x31(467, 5, 9), dform(14, 3, 3, -1),
                  dform(11, 0, 3, 0), bc(0, 2, -8)], 3, "rc")
    add("bdz", [mform(21, 5, 5, 0, 30, 31), dform(14, 5, 5, 1), x31(467, 5, 9), bc(18, 0, 8), dform(14, 3, 3, 9),
                x31(339, 4, 9), x31(266, 3, 3, 4)], 3, "r")
    # memory
    for name, op in (("lwz", 32), ("lwzu", 33), ("lbz", 34), ("lbzu", 35), ("lhz", 40), ("lhzu", 41), ("lha", 42),
                     ("lhau", 43)):
        add(name, MEMPRE + [dform(op, 5, 9, rnd.randrange(-0x40, 0x3D)), x31(266, 5, 5, 9)], 5, "r")
    for name, op in (("stw", 36), ("stwu", 37), ("stb", 38), ("stbu", 39), ("sth", 44), ("sthu", 45), ("stmw", 47)):
        add(name, MEMPRE + [dform(op, 29 if op == 47 else 4, 9, rnd.randrange(-0x40, 0x3D))], 9, "r")
    for name, xo in (("lwzx", 23), ("lwzux", 55), ("lbzx", 87), ("lbzux", 119), ("lhzx", 279), ("lhzux", 311),
                     ("lhax", 343), ("lhaux", 375), ("lwbrx", 534), ("lhbrx", 790), ("lwarx", 20)):
        add(name, MEMPRE + [x31(xo, 5, 9, 10), x31(266, 5, 5, 9)], 5, "r")
    for name, xo in (("stwx", 151), ("stwux", 183), ("stbx", 215), ("stbux", 247), ("sthx", 407), ("sthux", 439),
                     ("stwbrx", 662), ("sthbrx", 918)):
        add(name, MEMPRE + [x31(xo, 4, 9, 10)], 9, "r")
    add("lwarx stwcx.", MEMPRE + [x31(20, 5, 9, 10), x31(150, 4, 9, 10, 1)], 5, "rc")
    add("lha addze", MEMPRE + [dform(42, 6, 9, 2), x31(202, 5, 6)], 5, "rx")
    return out


def functions():
    """[(label, words)]: one function per case and observer."""
    out = []
    for name, words, reg, obs in cases():
        for o in obs:
            tail = {"r": mr3(reg), "c": MFCR, "x": MFXER}[o]
            out.append((f"{name} -> {o}", words + [tail, BLR]))
    return out


def translate(words, base, sig=None):
    code = b"".join(struct.pack(">I", w) for w in words)
    hdr = bytearray(0x100)
    struct.pack_into(">I", hdr, 0x00, 0x100)
    struct.pack_into(">I", hdr, 0x48, base)
    struct.pack_into(">I", hdr, 0x90, len(code))
    struct.pack_into(">I", hdr, 0xE0, base)
    import json
    facts = Facts.from_target(json.loads((GC / "target.json").read_text(encoding="utf-8")), {})
    src, ft = translate_function(Dol(bytes(hdr) + code), base, sig or Signature.generic_for(base), facts,
                                 [(base, base + len(code))])
    return src, ft


def window_hash(rd8):
    h = 0x811C9DC5
    for a in range(WIN, WIN + WIN_N):
        h = ((h ^ rd8(a)) * 16777619) & 0xFFFFFFFF
    return h


def expected(words, vec):
    """(r3, window hash) of the reference, or None when the architecture leaves the result undefined."""
    c = ref.Cpu(words, 0x80003100, SP, SENTINEL)
    c.r[3:11] = vec
    try:
        c.run()
    except ref.Skip:
        return None
    return c.r[3], window_hash(lambda a: c.mem.rd(a, 1))


def vectors(words, rnd, mem):
    out = []
    while len(out) < VECTORS:
        v = [rnd.choice(EDGE) if rnd.random() < 0.6 else rnd.getrandbits(32) for _ in range(8)]
        if not mem:
            v[7] = rnd.choice(XERS)
        if expected(words, v) is not None:
            out.append(v)
    return out


PLANTS = [("plant add", [x31(266, 3, 3, 4), BLR], [[0x80000000, 5] + [0] * 6, [7, 9] + [0] * 6],
           "u32 fn_plant0(u32 a, u32 b) { return a + b + (u32)(a == 0x80000000u); }", "fn_plant0(V[0], V[1])"),
          ("plant extsh addze", [x31(922, 3, 4), x31(202, 3, 4), MFXER, BLR], [[0xFFFF8000] + [0] * 7, [5] + [0] * 7],
           "u32 fn_plant1(u32 a) { return (u32)((u64)(s16)a >> 32) << 29; }", "fn_plant1(V[0])")]


# The driver's guest: only the window is memory (the judge's runtime, tc_rt.c, needs a harness to set up its RAM).
# The translated C still calls DP's own instruction helpers (tc_ppc.c). An access outside the window ends the run.
GUEST = r"""
static u8 g_win[SIZE];
static u8 *at(u32 a, u32 n) { if (a < LO || a - LO + n > SIZE) { printf("bad address %08x\n", a); exit(3); } return g_win + (a - LO); }
static u64 rdn(u32 a, u32 n) { u8 *p = at(a, n); u64 v = 0; for (u32 k = 0; k < n; k++) v = v << 8 | p[k]; return v; }
static void wrn(u32 a, u64 v, u32 n) { u8 *p = at(a, n); for (u32 k = 0; k < n; k++) p[k] = (u8)(v >> (8 * (n - 1 - k))); }
u8 rd8(u32 a) { return (u8)rdn(a, 1); }
u16 rd16(u32 a) { return (u16)rdn(a, 2); }
u32 rd32(u32 a) { return (u32)rdn(a, 4); }
u64 rd64(u32 a) { return rdn(a, 8); }
f32 rdf32(u32 a) { u32 w = rd32(a); f32 f; memcpy(&f, &w, 4); return f; }
f64 rdf64(u32 a) { u64 w = rd64(a); f64 f; memcpy(&f, &w, 8); return f; }
void wr8(u32 a, u8 v) { wrn(a, v, 1); }
void wr16(u32 a, u16 v) { wrn(a, v, 2); }
void wr32(u32 a, u32 v) { wrn(a, v, 4); }
void wr64(u32 a, u64 v) { wrn(a, v, 8); }
void wrf32(u32 a, f32 v) { u32 w; memcpy(&w, &v, 4); wr32(a, w); }
void wrf32d(u32 a, f64 v) { wr32(a, ppc_single_bits(v)); }
void wrf64(u32 a, f64 v) { u64 w; memcpy(&w, &v, 8); wr64(a, w); }
void tc_xr(u32 k, u32 v) { (void)k; (void)v; }
void tc_xrf(u32 k, f64 v) { (void)k; (void)v; }
void tc_xmsr(u32 v) { (void)v; }
u32 tc_call(u32 t, const TcArgs *a) { (void)t; (void)a; exit(4); }
f64 tc_callf(u32 t, const TcArgs *a) { (void)t; (void)a; exit(4); }
u32 tc_lastr(void) { return 0; }
u32 tc_lastr4(void) { return 0; }
f64 tc_lastf(void) { return 0.0; }
static void fill(void) { for (u32 a = LO; a < LO + SIZE; a++) wr8(a, (u8)((a * 0x9E3779B1u) >> 21)); }
static u32 hash(void) { u32 h = 0x811C9DC5u; for (u32 a = LO; a < LO + SIZE; a++) h = (h ^ rd8(a)) * 16777619u; return h; }
int main(void)
{"""


def build_and_run(fns, work):
    """Compile every function with the runtime as DP does and run the driver: {(k, j): (r3, hash)}."""
    rnd = random.Random(7)
    protos, calls, names = [], [], []
    for k, (label, words) in enumerate(fns):
        src, _ = translate(words, 0x80003100)
        m = re.search(r"^(\w+) fn_80003100\(([^)]*)\)\s*$", src, re.M)
        assert m and m.group(1) == "u32", f"{label}: unexpected prototype in\n{src}"
        name = f"fn_b{k}"
        src = src.replace("fn_80003100", name)
        pathlib.Path(work, f"f{k}.c").write_text(src, encoding="utf-8")
        names.append(f"f{k}.c")
        ps = [p.split()[-1] for p in m.group(2).split(",") if p.strip() != "void"]
        args = ", ".join(f"V[{int(p[5:]) - 3}]" for p in ps)
        protos.append(f"u32 {name}({m.group(2)});")
        vs = vectors(words, rnd, mem=words[:3] == MEMPRE)
        fns[k] = (label, words, vs)
        rows = ", ".join("{" + ", ".join(f"0x{x:08x}u" for x in v) + "}" for v in vs)
        calls.append(f"    {{ static const u32 T[{len(vs)}][8] = {{{rows}}};\n      for (int j = 0; j < {len(vs)}; j++) "
                     f"{{ const u32 *V = T[j]; (void)V; fill(); u32 r = {name}({args}); "
                     f'printf("{k} %d %08x %08x\\n", j, r, hash()); }} }}')
    for n, (label, words, vs, csrc, call) in enumerate(PLANTS):
        protos.append(csrc)
        rows = ", ".join("{" + ", ".join(f"0x{x:08x}u" for x in v) + "}" for v in vs)
        calls.append(f"    {{ static const u32 T[{len(vs)}][8] = {{{rows}}};\n      for (int j = 0; j < {len(vs)}; j++) "
                     f'{{ const u32 *V = T[j]; (void)V; fill(); u32 r = {call}; printf("p{n} %d %08x %08x\\n", j, r, hash()); }} }}')
    drv = "\n".join(['#include <stdio.h>', '#include <stdlib.h>', '#include <string.h>', '#include "tc_rt.h"', *protos,
                     GUEST.replace("LO", f"0x{WIN:08x}u").replace("SIZE", f"0x{WIN_N:x}u"), *calls,
                     "    return 0;\n}\n"])
    pathlib.Path(work, "drv.c").write_text(drv, encoding="utf-8")
    pathlib.Path(work, "fns.rsp").write_text("\n".join(names + [f'"{RT / "tc_ppc.c"}"']), encoding="utf-8")
    vc = str(vcvars(64)).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /MP /std:c11 /O1 /fp:strict /W4 /WX /TC '
                       f'/I"{RT}" /Fedrv.exe drv.c @fns.rsp"', cwd=work, capture_output=True, text=True,
                       errors="replace", timeout=1500)
    bad = [ln for ln in r.stdout.splitlines() if "error" in ln or "warning" in ln]
    assert r.returncode == 0, "\n".join(bad[:12]) + r.stderr[-1500:]
    run = subprocess.run([str(pathlib.Path(work, "drv.exe"))], cwd=work, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-800:] + run.stderr[-800:]
    got = {}
    for ln in run.stdout.splitlines():
        k, j, r3, h = ln.split()
        got[(k, int(j))] = (int(r3, 16), int(h, 16))
    return got


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class Behaviour(unittest.TestCase):
    def test_translated_c_matches_the_reference(self):
        fns = functions()
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcbeh_", dir=work) as d:
            got = build_and_run(fns, d)
        wrong = []
        for k, (label, words, vs) in enumerate(fns):
            for j, v in enumerate(vs):
                want = expected(words, v)
                if got.get((str(k), j)) != want:
                    g = got.get((str(k), j), (0, 0))
                    wrong.append(f"{label}: in {[f'{x:x}' for x in v]} got r3 {g[0]:08x} mem {g[1]:08x}, "
                                 f"want r3 {want[0]:08x} mem {want[1]:08x}")
        print(f"behaviour: {len(fns)} functions, {sum(len(f[2]) for f in fns)} runs, {len(wrong)} wrong")
        self.assertEqual(wrong[:12], [], f"{len(wrong)} wrong in {len({w.split(':')[0] for w in wrong})} functions")
        for n, (label, words, vs, _, _) in enumerate(PLANTS):   # each planted wrong C is reported, on its edge input only
            diff = [j for j, v in enumerate(vs) if got[(f"p{n}", j)] != expected(words, v)]
            self.assertEqual(diff, [0], label)


class Text(unittest.TestCase):
    def test_cast_fold_keeps_a_sign_extension_under_an_unsigned_middle(self):
        v = X.Var("v", "u32")
        e = X.cast("u64", X.cast("u32", X.cast("s16", v)))
        self.assertEqual(X.render(e), "(u64)(u32)(s16)v")       # was (u64)(s16)v: the sign ran into the high word
        self.assertEqual(X.render(X.cast("u64", X.cast("u32", X.cast("u8", v)))), "(u64)(u8)v")
        self.assertEqual(X.render(X.cast("s64", X.cast("s32", X.cast("s16", v)))), "(s64)(s16)v")
        self.assertEqual(X.render(X.cast("u16", X.cast("u32", X.cast("s8", v)))), "(u16)(s8)v")
        src, _ = translate([x31(922, 3, 4), x31(202, 3, 4), MFXER, BLR], 0x80003100)
        self.assertNotIn("(u64)(s16)", src)

    def test_stwcx_translates(self):                             # was AttributeError: d (an indexed store)
        src, _ = translate([x31(20, 5, 0, 3), x31(150, 4, 0, 3, 1), MFCR, BLR], 0x80003100)
        self.assertIn("wr32(arg_r3, arg_r4)", src)

    def test_an_unlisted_argument_register_is_refused(self):
        def sig(regs):
            s = Signature.generic_for(0x80003100)
            s.callees = {0x80004000: regs}
            return s
        bl = 18 << 26 | (0x80004000 - 0x80003100) | 1
        src, _ = translate([bl, BLR], 0x80003100, sig(["r3", "f1", "s0"]))
        self.assertIn("tc_call", src)
        for bad in ("r11", "f9", "s8", "v1", "r2"):
            with self.assertRaisesRegex(TranslateError, f"argument register {bad}"):
                translate([bl, BLR], 0x80003100, sig([bad]))

    def test_a_jump_table_whose_base_is_set_before_a_branch_is_a_switch(self):
        """Skies 8024d5ec: lis/addi of the table before the loop, the lwzx and bctr inside it. The straight run back
        from the bctr ends at the branch, the base was not found, and the cases became one tail call."""
        base = 0x80003100
        tab = base + 14 * 4
        words = [dform(15, 5, 0, tab >> 16), dform(14, 5, 5, tab & 0xFFFF),   # lis r5 / addi r5: the table
                 18 << 26 | 8,                                                # b +8
                 BLR,
                 dform(10, 0, 3, 1), bc(12, 1, -8),                           # cmplwi r3,1 / bgt
                 mform(21, 3, 0, 2, 0, 29), x31(23, 0, 5, 0),                 # slwi r0,r3,2 / lwzx r0,r5,r0
                 x31(467, 0, 9), 0x4E800420,                                  # mtctr r0 / bctr
                 dform(14, 3, 0, 7), BLR, dform(14, 3, 0, 9), BLR,            # the two cases
                 base + 10 * 4, base + 12 * 4]
        hdr = bytearray(0x100)
        struct.pack_into(">I", hdr, 0x00, 0x100)
        struct.pack_into(">I", hdr, 0x48, base)
        struct.pack_into(">I", hdr, 0x90, 4 * len(words))
        import json
        facts = Facts.from_target(json.loads((GC / "target.json").read_text(encoding="utf-8")), {})

        def tr(ws):
            code = b"".join(struct.pack(">I", w) for w in ws)
            return translate_function(Dol(bytes(hdr) + code), base, Signature.generic_for(base), facts,
                                      [(base, base + 14 * 4)])
        src, ft = tr(words)
        self.assertEqual(ft.tables, {base + 9 * 4: [base + 10 * 4, base + 12 * 4]})
        self.assertIn("switch (", src)
        self.assertIn("7u", src)
        self.assertIn("9u", src)
        # a base the caller passes, or one a call may have changed, is still not a table
        passed = list(words)
        passed[0] = dform(14, 5, 3, 0)                                        # addi r5,r3,0
        self.assertEqual(tr(passed)[1].tables, {})
        called = list(words)
        called[2:4] = [18 << 26 | 4 | 1, 18 << 26 | 4]                        # bl next / b next: r5 after a call
        self.assertEqual(tr(called)[1].tables, {})

    def test_the_adapter_keeps_the_notes(self):
        spec = importlib.util.spec_from_file_location("gc_adapter", GC / "translate.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        src, ft = translate([x31(4, 31, 0, 0)], 0x80003100)     # tw 31,r0,r0: a trap
        self.assertTrue(any("trap at 80003100" in n for n in ft.notes))
        out = mod.with_notes(src, ft.notes + ["an odd */ note"])
        self.assertIn(" *   trap at 80003100", out)
        self.assertEqual(out.count("*/"), src.count("*/") + 1)
        self.assertTrue(out.startswith('#include "tc_rt.h"\n/* ppc-translate notes'))
        self.assertEqual(mod.with_notes(src, []), src)


if __name__ == "__main__":
    unittest.main()
