"""GameCube: what a mocked call leaves besides r3 and f1 (TcCallFx, runtime/tc_fx.h; Skies round r9).

1. Scripted r4 (vectors.txt Q). A callee returning r3:r4 (TcFlow.returnsR4) left r4 = 0 while the oracle chose vectors
   and junk in the strict replay, so a loop that runs until the 64-bit value is 0 (MSL __ull2dec 8025baf4, longlong2str
   8025e14c, through __div2u) ended in the choice and never in the replay: 1 and 0 of 96 vectors. Call k's r4 is now the
   vector's own word in both runs; Q carries it and tc_lastr4 answers it for that call, junk or 0 for any other.
2. Last resorts (TcVectors.lastResort), only for a run the repairs gave up on: an image table randomised as a global
   (8008fc50's store offsets, 800f2464's and 80148024's switch tables: tableIndex) and callee writes (vectors.txt K: 800a40b0's task pointer, the flags 8028532c
   and 802846e4 poll: polled, flip), which the C runtime makes when the call returns (tc_fx.h fx_call).
Skipped without vcvars (C) or without ghidra_home and a JDK (Java). Run: python tests/test_gc_callfx.py
"""
import importlib.util
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_fmac import vcvars  # noqa: E402
from test_gc_r4_hw import at  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
RT = GC / "runtime"
WORK = TOOL / "tests" / ".work"

C_DRIVER = r"""
#include <stdio.h>
#include "tc_rt.h"
#include "tc_rt_internal.h"

int main(void)
{
    TcArgs a = {0};
    if (!tcrt_init("dummy.dol", "ranges.txt")) return 2;
    tcrt_strict_line("J 1");
    tcrt_strict_line("N 1234 7");
    tcrt_fx_line("Q 0 0000002a 2 00000000\n");
    (void)tc_call(0x80100000u, &a);
    printf("q0 %08x\n", tc_lastr4());
    (void)tc_call(0x80100000u, &a);
    printf("junk1 %08x\n", tc_lastr4());
    (void)tc_call(0x80100000u, &a);
    printf("q2 %08x\n", tc_lastr4());
    tcrt_reset();
    tcrt_strict_line("J 1");
    tcrt_strict_line("N 1234 7");
    (void)tc_call(0x80100000u, &a);
    printf("reset %08x\n", tc_lastr4());
    tcrt_reset();
    tcrt_strict_line("J 0");
    tcrt_fx_line("Q 0 ffffffff\n");
    (void)tc_call(0x80100000u, &a);
    printf("off %08x\n", tc_lastr4());
    return 0;
}
"""


def build_run(driver, extra=()):
    """Compile driver.c with the runtime at /W4 /WX and run it; its stdout."""
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gcfx_", dir=WORK) as d:
        pathlib.Path(d, "drv.c").write_text(driver, encoding="utf-8")
        pathlib.Path(d, "dummy.dol").write_bytes(bytes(0x100))
        pathlib.Path(d, "ranges.txt").write_text("", encoding="utf-8")
        for name, text in extra:
            pathlib.Path(d, name).write_text(text, encoding="utf-8")
        vc = str(vcvars(64)).replace("/", "\\")
        src = " ".join(f'"{RT / n}"' for n in ("tc_rt.c", "tc_ppc.c"))
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O2 /W4 /WX /I"{RT}" drv.c {src}"',
                           cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        run = subprocess.run([str(pathlib.Path(d, "drv.exe"))], cwd=d, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
        return run.stdout


def java_run(stub, args=()):
    """Compile tests' Java stub against the target's scripts (Ghidra on the class path) and run it; its stdout."""
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gcfxj_", dir=WORK) as d:
        f = pathlib.Path(d, "CallFxStub.java")
        f.write_text(stub, encoding="utf-8")
        src = os.pathsep.join(str(x) for x in (GC / "ghidra_scripts", GC / "template", GC.parents[1] / "core" / "ghidra_shared" / "ppc"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", d, "-cp", CP, "-sourcepath", src, str(f)],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        r = subprocess.run([jdk("java"), "-cp", d + os.pathsep + CP, "CallFxStub", *args], capture_output=True, text=True,
                           timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class Runtime(unittest.TestCase):
    def test_scripted_r4(self):
        got = dict(ln.split(" ", 1) for ln in build_run(C_DRIVER).splitlines())
        self.assertEqual(got["q0"], "0000002a")  # Q's word for call 0
        self.assertEqual(got["junk1"], f"{at(0x1234, 7, 1, 4) & 0xffffffff:08x}")  # no Q word: the junk, as before
        self.assertEqual(got["q2"], "00000000")  # a scripted 0 is 0, not junk
        self.assertEqual(got["reset"], f"{at(0x1234, 7, 0, 4) & 0xffffffff:08x}")  # tcrt_reset forgets Q
        self.assertEqual(got["off"], "ffffffff")  # mock_junk off: Q still answers


K_DRIVER = r"""
#include <stdio.h>
#include "tc_rt.h"
#include "tc_rt_internal.h"

int main(void)
{
    TcArgs a = {0};
    if (!tcrt_init("dummy.dol", "ranges.txt")) return 2;
    tcrt_fx_line("K 1 80003000 deadbeef\n");
    tcrt_fx_line("K 1 80003004 01\n");
    tcrt_fx_line("K 2 80003008 0000000000000007\n");
    (void)tc_call(0x80100000u, &a);
    printf("c0 %08x\n", rd32(0x80003000u));
    (void)tc_callf(0x80100000u, &a);
    printf("c1 %08x %02x\n", rd32(0x80003000u), rd8(0x80003004u));
    (void)tc_call(0x80100000u, &a);
    printf("c2 %08x\n", rd32(0x8000300cu));
    tcrt_reset();
    (void)tc_call(0x80100000u, &a);
    (void)tc_call(0x80100000u, &a);
    printf("reset %08x\n", rd32(0x80003000u));
    return 0;
}
"""


R4_STUB = r"""
import java.util.*;
public class CallFxStub {
    public static void main(String[] a) {
        long[] w = TcCallFx.r4Words(0x1234, 7), again = TcCallFx.r4Words(0x1234, 7), other = TcCallFx.r4Words(0x1234, 8);
        StringBuilder b = new StringBuilder();
        for (long x : w) b.append(Long.toHexString(x)).append(',');
        System.out.println("words " + b);
        System.out.println("same " + Arrays.equals(w, again) + " other " + Arrays.equals(w, other));
        System.out.println("past " + TcCallFx.r4(w, 16) + " null " + TcCallFx.r4(null, 0));
        System.out.print(TcCallFx.qLine(List.of(new long[] {0, 0x2a}, new long[] {3, 0xffffffffL})));
        System.out.println("empty [" + TcCallFx.qLine(List.of()) + "]");
    }
}
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class R4Words(unittest.TestCase):
    def test_words_and_line(self):
        out = java_run(R4_STUB).splitlines()
        words = [int(x, 16) for x in out[0].split(" ", 1)[1].strip(",").split(",")]
        self.assertEqual(len(words), 16)
        self.assertIn(0, words)  # a 64-bit result's low word can end a loop
        self.assertTrue(any(w > 15 for w in words))  # and can be any word
        self.assertEqual(out[1], "same true other false")  # deterministic per vector
        self.assertEqual(out[2], "past 0 null 0")  # past the 16, or no words: clobber's 0
        self.assertEqual(out[3], "Q 0 0000002a 3 ffffffff")
        self.assertEqual(out[4], "empty []")


class Hook(unittest.TestCase):
    def test_r4_calls(self):
        """hooks.r4_calls (target.json stamp scope save_helper_frame): a body that can call a callee returning r3:r4."""
        from unittest import mock
        from test_sync import TMP  # noqa: F401  (a project for core.paths)
        from core import paths
        spec = importlib.util.spec_from_file_location("gc_hooks", GC / "hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        base, blr = 0x80003100, 0x4E800020
        fns = {"sets": [0x38800000, blr],  # li r4,0
               "plain": [0x38600001, blr],  # li r3,1
               "callsSets": ["sets", blr], "callsPlain": ["plain", blr], "indirect": [0x4E800421, blr],  # bctrl
               "tail": ["b sets"], "none": [0x7C832378, blr]}  # mr r3,r4: names r4 but calls nothing
        at, e = base, {}
        for k, body in fns.items():
            e[k], at = at, at + 4 * len(body)
        words = []
        for k, body in fns.items():
            for i, w in enumerate(body):
                a = e[k] + 4 * i
                if isinstance(w, str):
                    t = e[w.split()[-1]]
                    w = 0x48000000 | ((t - a) & 0x03FFFFFC) | (0 if w.startswith("b ") else 1)
                words.append(w)
        code = b"".join(struct.pack(">I", x) for x in words)
        hdr = bytearray(0x100)
        struct.pack_into(">I", hdr, 0x00, 0x100)
        struct.pack_into(">I", hdr, 0x48, base)
        struct.pack_into(">I", hdr, 0x90, len(code))
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK) as d:
            p = pathlib.Path(d, "stub.dol")
            p.write_bytes(bytes(hdr) + code)
            bodies = pathlib.Path(d, "function_bodies.txt")
            bodies.write_text("".join(f"{e[k]:08x} {e[k]:x} {e[k] + 4 * len(b):x}\n" for k, b in fns.items()),
                              encoding="utf-8")
            with mock.patch.object(paths, "FUNCTION_BODIES", bodies):
                got = {k: hooks.r4_calls({"binary_path": p}, f"{a:08x}") for k, a in e.items()}
                self.assertTrue(hooks.r4_calls({"binary_path": p}, "80009000"))  # no body: in scope
                self.assertTrue(hooks.stamp_scope({"binary_path": p}, f"{e['callsSets']:08x}", "save_helper_frame"))
        self.assertEqual(got, {"sets": False, "plain": False, "callsSets": True, "callsPlain": False, "indirect": True,
                               "tail": True, "none": False})


LAST_STUB = r"""
import java.util.*;
public class CallFxStub {
    public static void main(String[] a) {
        long x = 0x9e3f1a77L, fault = (0x80302da8L + x) & 0xffffffffL;  // stbx r4,r10,r11 with a random offset
        System.out.println("table " + TcCallFx.tableIndex(fault, x, 0x10) + " " + TcCallFx.tableIndex(fault, x, 0x7f000000L)
            + " " + TcCallFx.tableIndex(fault, x, x) + " " + TcCallFx.tableIndex(0x00000024L, 0, 0x10));
        List<long[]> loads = new ArrayList<>();
        for (int i = 0; i < 40; i++) loads.add(new long[] {0x802853c4L, 0x80347df0L, 4, 0});
        long[] p = TcCallFx.polled(loads);
        List<long[]> few = loads.subList(0, 31), mixed = new ArrayList<>(loads);
        mixed.set(20, new long[] {0x802853c4L, 0x80347df0L, 4, 1});
        System.out.println("poll " + Long.toHexString(p[1]) + " " + (TcCallFx.polled(few) == null) + " "
            + (TcCallFx.polled(mixed) == null) + " flip " + TcCallFx.flip(0) + TcCallFx.flip(1) + TcCallFx.flip(7));
        List<long[]> fx = List.of(new long[] {1, 0x80347df0L, 4, 1}, new long[] {2, 0x803424f9L, 1, 0});
        System.out.print(TcCallFx.kLines(fx));
        System.out.println("has " + TcCallFx.has(fx, 2, 0x803424f9L) + " " + TcCallFx.has(fx, 1, 0x803424f9L)
            + " " + TcCallFx.has(null, 1, 0) + " none [" + TcCallFx.kLines(null) + "]");
    }
}
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class LastResorts(unittest.TestCase):
    def test_conditions(self):
        out = java_run(LAST_STUB).splitlines()
        self.assertEqual(out[0], "table true false false false")  # the image's offset brings the store back into RAM
        self.assertEqual(out[1], "poll 80347df0 true true flip 100")
        self.assertEqual(out[2:4], ["K 1 80347df0 00000001", "K 2 803424f9 00"])
        self.assertEqual(out[4], "has true false false none []")


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class CalleeWrites(unittest.TestCase):
    def test_k_lines(self):
        got = dict(ln.split(" ", 1) for ln in build_run(K_DRIVER).splitlines())
        self.assertEqual(got["c0"], "00000000")  # call 0 has no K
        self.assertEqual(got["c1"], "deadbeef 01")  # call 1's two writes, made as it returns (tc_callf too)
        self.assertEqual(got["c2"], "00000007")  # eight bytes, big endian
        self.assertEqual(got["reset"], "00000000")  # tcrt_reset forgets K (and the RAM is pristine again)


class Dropped(unittest.TestCase):
    def test_dropped(self):
        """hooks.dropped (stamp scope save_helper_frame): only a harness that lost a vector can see a last resort."""
        import json
        from unittest import mock
        from test_sync import TMP  # noqa: F401  (a project for core.paths)
        from core import paths
        spec = importlib.util.spec_from_file_location("gc_hooks", GC / "hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK) as d:
            for addr, summary in (("80000100", {"invalid": {}}), ("80000200", {"invalid": {"step limit": 3}}),
                                  ("80000300", {"dev_valid": 1})):
                pathlib.Path(d, addr).mkdir()
                pathlib.Path(d, addr, "oracle_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            with mock.patch.object(paths, "HARNESS", pathlib.Path(d)):
                got = [hooks.dropped(a) for a in ("80000100", "80000200", "80000300", "80000400")]
        self.assertEqual(got, [False, True, True, False])  # no summary: no oracle run


class Wiring(unittest.TestCase):
    def test_oracle_scripts_r4(self):
        o = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("if (calls < Q_CALLS && calleeSetsR4(pc)) {", o)  # the same word in the choice and the replay
        self.assertIn("static final int Q_CALLS = 64;", o)  # RETS grew to 1024 (test_gc_retptr.py): Q as before
        v = (GC / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8")
        self.assertIn("r4Of.put(v, TcCallFx.r4Words(seed, tried[0] + tried[1]));", v)
        self.assertIn("vout.append(TcCallFx.qLine(r.r4s))", v)
        self.assertIn("TcOracle.r4Of.put(c, TcOracle.r4Of.get(v));", (GC / "ghidra_scripts" / "TcBoundPass.java").read_text(encoding="utf-8"))
        self.assertIn("tcrt_fx_line(line);", (RT / "runner.c").read_text(encoding="utf-8"))

    def test_last_resorts(self):
        v = (GC / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8")
        self.assertIn("for (int last = 0; !r.ok && refused == null && last < LAST_REPAIRS; last++) {", v)
        self.assertEqual(v.count("lastResort(v, r)"), 1)  # after the repairs' loop only
        self.assertIn(".append(TcCallFx.kLines(fxOf.get(v)));", v)
        o = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("if (e[0] == calls) emu.writeMemory(toAddr(e[1]), TcCallFx.bytes(e));", o)
        self.assertIn("res.storedAt.put(st[0] & ~3L, calls);", o)
        self.assertIn("TcOracle.fxOf.put(c, new ArrayList<>(TcOracle.fxOf.get(v)));",
                      (GC / "ghidra_scripts" / "TcBoundPass.java").read_text(encoding="utf-8"))
        self.assertEqual((RT / "tc_rt.c").read_text(encoding="utf-8").count("fx_call(g_ncalls++);"), 2)
        self.assertIn("line[0] == 'K'", (RT / "runner.c").read_text(encoding="utf-8"))
        self.assertIn("or dropped(addr)", (GC / "hooks.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
