"""A mocked callee's stores into the store queues (TcSq.callee; Toy Commander's near-plane clippers 8c0702b0 and
8c06fd38: 8c074f18 and 8c074e84 store u, v and the colours through r4 = r14 before `pref @r14`). On the SH-4 a queue
holds every store made into the window, whichever function made it; the oracle used to count a mocked callee's as
none, so every judged C copied the old block over them and the port drew the previous vertex's u, v. Pass and fail
controls:
1. the oracle's sends (tests/java/TcP4Stub, TcSq.send): the bytes a callee stores through r4 count (sent from their
   RAM bytes, the callee's in the port), the bytes it does not store (words 6 and 7, +0x30) still come from the old
   block 0x40 back; with no callee entry, a loose one ("?") or r4 outside the window, the sends are as before;
   line S carries the callee's bytes (xemu) and meta sq_callee names them, "?" for a loose register;
2. the translator's queue model (sq_callee_helpers, compiled) sends the same bytes as the oracle in every case,
   and SQ_HELPERS (no callee entry) the old ones;
3. the translator: sq_mark before a listed callee's call, with r4 as the delay slot sets it, ahead of the argument
   lines; no sq_callee, or an entry for another callee, gives byte-identical C to before; meta's "?" stores nothing;
4. the wiring: TcLive.after marks the callee's bytes before its r8..r14 change (TcLive.apply: sq.called clears the
   blocks the callee filled), TcPrepare builds the table for every store-queue function, xemu reads line S's table.
TcLive.Deltas.args (the byte sets from the callee's p-code) needs Ghidra: the re-prepare of the affected functions
judges it (meta sq_callee of 8c0702b0: r4 10-17,20-2f,34-3f).
Synthetic data. Run: python -W error tests/test_sq_callee.py.
"""
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402
from sh4translate import translate as tx  # noqa: E402
import game_config  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
WORK = ROOT / "tests" / ".work"
CLIP = "10-17,20-2f,34-3f"  # 8c0702b0's callee 8c074f18 through r4: u, v, base argb, offset rgb


def expected(spec):
    """The two blocks 8c0702b0's prefs send, as the stub's scenario sets it up (TcP4Stub M)."""
    marked = set()
    for part in spec.split(",") if spec not in ("-", "?") else ():
        lo, _, hi = part.partition("-")
        marked |= set(range(int(lo, 16), int(hi or lo, 16) + 1))
    out = []
    for blk in (0, 0x20):
        for j in range(32):
            o = blk + j
            out.append(0xaa if o < 16 else 0xbb if 0x20 <= o < 0x24 else 0x40 + o if o in marked else o)
    return bytes(out[:32]).hex(), bytes(out[32:]).hex()


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="sqc_", dir=WORK)
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcP4Stub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcP4Stub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}

    def test_callee_bytes_count(self):
        got = self.run_stub("M count e0100440 %s\nM none e0100440 -\nM loose e0100440 ?\nM ram 8c100440 %s\n"
                            "M other e0100440 18-1f\n" % (CLIP, CLIP))
        sends = {k[2:]: v.split(" | ")[0].split() for k, v in got.items()}
        self.assertEqual(sends["count"], list(expected(CLIP)))
        b0, b1 = sends["count"]
        self.assertEqual(b0[0x10 * 2:0x18 * 2], "5051525354555657")  # u, v: the callee's (RAM at the block)
        self.assertEqual(b0[0x18 * 2:], "18191a1b1c1d1e1f")  # words 6 and 7: not stored, the old block's
        self.assertEqual(b1[0x10 * 2:0x14 * 2], "30313233")  # +0x30: not stored, the old block's
        self.assertEqual(b1[:8], "bbbbbbbb")  # base_a stored again after the call: the function's own
        old = list(expected("-"))
        self.assertEqual(sends["none"], old)  # control: no callee entry, as before
        self.assertEqual(sends["loose"], old)  # control: loose stores count as none
        self.assertEqual(sends["ram"], old)  # control: a pointer outside the window marks nothing
        self.assertEqual(sends["other"], list(expected("18-1f")))  # exact: only the bytes it stores
        self.assertNotEqual(sends["other"], sends["count"])

    def test_header_and_meta(self):
        got = self.run_stub("M count e0100440 %s\nM none e0100440 -\nM loose e0100440 ?\n" % CLIP)
        hm = {k[2:]: v.split(" | ")[1] for k, v in got.items()}
        self.assertTrue(hm["count"].startswith("S 14 8c074f18 4 %016x %016x " % (bits(CLIP) & (1 << 64) - 1, 0)))
        self.assertIn('"sq_callee": {"8c074f18":{"r4":"%s"}}' % CLIP, hm["count"])
        self.assertEqual(hm["none"], 'S 14 "r14"')  # control: line S and meta as before
        self.assertTrue(hm["loose"].startswith('S 14 "r14"'))  # loose: nothing for xemu
        self.assertIn('"sq_callee": {"8c074f18":{"r4":"?"}}', hm["loose"])


def bits(spec):
    out = 0
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        for j in range(int(lo, 16), int(hi or lo, 16) + 1):
            out |= 1 << j
    return out


DRIVER = r"""
#include <stdio.h>
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef float f32;
static u8 ram[0x200];
static u32 at(u32 a) { return (a & 0x00ffffffu) - 0x100400u; }  /* 8c100400.., and the window's view of it */
static u8 rd8(u32 a) { return ram[at(a)]; }
static void wr8(u32 a, u8 v) { ram[at(a)] = v; }
static void wr16(u32 a, u16 v) { wr8(a, (u8)v); wr8(a + 1, (u8)(v >> 8)); }
static void wr32(u32 a, u32 v) { wr16(a, (u16)v); wr16(a + 2, (u16)(v >> 16)); }
static void wrf32(u32 a, f32 v) { union { u32 u; f32 f; } b; b.f = v; wr32(a, b.u); }
@HELPERS@
int main(void)
{
    SqQueues sq;
    u32 i;
    for (i = 0; i < sizeof ram; i++) ram[i] = (u8)(0x00 + i);
    sq_start(&sq, 0xe0100440u);
    for (i = 0; i < 16; i += 4) sq_wr32(&sq, 0xe0100440u + i, 0xaaaaaaaau);
    @MARK@
    sq_wr32(&sq, 0xe0100460u, 0xbbbbbbbbu);
    sq_pref(&sq, 0xe0100440u);
    sq_pref(&sq, 0xe0100460u);
    for (i = 0x40; i < 0x80; i++) printf("%02x%s", ram[i], i == 0x5f ? " " : "");
    printf("\n");
    (void)wr8; (void)wr16; (void)wrf32; (void)sq_wr8; (void)sq_wr16; (void)sq_wrf32; (void)sq_moved;
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class TranslatorModel(unittest.TestCase):
    def run_c(self, helpers, mark):
        d = pathlib.Path(tempfile.mkdtemp(dir=WORK if WORK.is_dir() else None))
        try:
            (d / "p.c").write_text(DRIVER.replace("@HELPERS@", "\n".join(helpers)).replace("@MARK@", mark),
                                   encoding="utf-8")
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX /Od p.c"', cwd=d,
                               capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            p = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            return p.stdout.split()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def mark(self, a, spec):
        b = bits(spec)
        return f"sq_mark(&sq, {a}u, 0x{b & (1 << 64) - 1:016x}ull, 0x{b >> 64:016x}ull);"

    def test_same_bytes_as_the_oracle(self):
        new = tx.sq_callee_helpers()
        self.assertEqual(self.run_c(new, self.mark("0xe0100440", CLIP)), list(expected(CLIP)))
        self.assertEqual(self.run_c(new, self.mark("0xe0100440", "18-1f")), list(expected("18-1f")))
        self.assertEqual(self.run_c(new, self.mark("0x8c100440", CLIP)), list(expected("-")))  # outside the window
        self.assertEqual(self.run_c(new, ""), list(expected("-")))
        self.assertEqual(self.run_c(tx.SQ_HELPERS, ""), list(expected("-")))  # control: today's helpers


class Translator(unittest.TestCase):
    # jsr @r3 (8c000100 from the pool), its delay slot mov r14,r4, then pref @r14; rts
    WORDS = [0xd303, 0x430b, 0x64e3, 0x0009, 0x0e83, 0x000b, 0x0009, 0x0009]

    def translate(self, sq_callee):
        data = b"".join(struct.pack("<H", w) for w in self.WORDS) + struct.pack("<I", 0x8c000100)
        sig = tx.Signature("fn_8c000000", "void fn_8c000000(void)", [], "void", None, {0x8c000100: ["r4"]},
                           sq_window="r14", sq_callee=sq_callee)
        src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts())
        return src[0] if isinstance(src, tuple) else src

    def test_mark_before_the_call(self):
        src = self.translate(tx.sq_callee_bytes({"8c000100": {"r4": CLIP}}))
        body = src[src.index("void fn_8c000000(void)"):].splitlines()
        mark = next(i for i, ln in enumerate(body) if ln.strip().startswith("sq_mark(&sq, r4, "))
        args = next(i for i, ln in enumerate(body) if ln.strip().startswith("a.r[0] = "))
        call = next(i for i, ln in enumerate(body) if "tc_call(0x8c000100u" in ln)
        r4 = next(i for i, ln in enumerate(body) if ln.strip() == "r4 = r14;")
        self.assertLess(r4, mark)  # the delay slot's r4
        self.assertLess(mark, args)
        self.assertLess(args, call)
        self.assertIn(f"0x{bits(CLIP) & (1 << 64) - 1:016x}ull", body[mark])
        self.assertIn("static void sq_mark(", src)
        self.assertIn("u32 src[2][32]; } SqQueues;", src)

    def test_controls_identical(self):
        before = self.translate(None)
        self.assertNotIn("sq_mark", before)
        self.assertIn("\n".join(tx.SQ_HELPERS), before)  # today's helpers, unchanged
        self.assertEqual(self.translate(tx.sq_callee_bytes({"8c000200": {"r4": CLIP}})), before)  # another callee
        self.assertEqual(self.translate(tx.sq_callee_bytes({"8c000100": {"r4": "?"}})), before)  # loose

    def test_meta_bytes(self):
        self.assertEqual(tx.sq_callee_bytes({"8c074f18": {"r4": "10-17,34", "r5": "?"}}),
                         {0x8c074f18: {"r4": (0xff0000 | 1 << 0x34, 0)}})
        self.assertEqual(tx.sq_callee_bytes({"8c074f18": {"r4": "40-41"}}), {0x8c074f18: {"r4": (0, 3)}})
        self.assertIsNone(tx.sq_callee_bytes({"8c04f090": {"r4": "?"}}))
        self.assertIsNone(tx.sq_callee_bytes(None))


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


class Prompts(unittest.TestCase):
    """The agents' notes on the window (core/agent.abi_notes, core/readable_context.state_notes) name the bytes an
    exact callee stores; a loose ("?") callee changes nothing."""

    def test_notes(self):
        from core import agent, readable_context
        base = {"addr": "8c0702b0", "params": [], "callees": [], "sq_window": "r14"}
        exact = dict(base, sq_callee={"8c074f18": {"r4": CLIP}, "8c04f090": {"r5": "?"}})
        loose = dict(base, sq_callee={"8c04f090": {"r4": "?"}})
        for notes in (agent.abi_notes, readable_context.state_notes):
            self.assertEqual(notes(loose), notes(base))
            extra = " ".join(notes(exact)[len(notes(base)):])
            self.assertIn(f"fn_8c074f18 through r4: {CLIP}", extra)
            self.assertNotIn("8c04f090", extra)


class Wiring(unittest.TestCase):
    def test_wiring(self):
        orc = text(SH4 / "ghidra_scripts" / "TcOracle.java")  # r4..r7 read before TcStrict's junk clobbers them
        self.assertLess(orc.index("sq.callee(this, emu, pc);"), orc.index("TcStrict.call(res, emu, calls);"))
        prep = text(SH4 / "ghidra_scripts" / "TcPrepare.java")
        self.assertLess(prep.index("sq = TcSq.of("), prep.index("sq.callees(getCurrentProgram(), calledBy(fn, body), facts.fpscrEntry)"))
        sq = text(SH4 / "ghidra_scripts" / "TcSq.java")
        self.assertIn("d.args(g)", sq)
        xe = text(SH4 / "xemu" / "xe_main.c")
        self.assertIn("sq_callee(x, target);", xe)
        self.assertIn("tcrt_sq_line[i] = line[i];", text(SH4 / "runtime" / "runner.c"))
        for name in ("TcLive.java", "TcSq.java", "TcTail.java", "TcPrepare.java", "TcOracle.java"):
            self.assertLessEqual(len(text(SH4 / "ghidra_scripts" / name).splitlines()), 400, name)


if __name__ == "__main__":
    unittest.main()
