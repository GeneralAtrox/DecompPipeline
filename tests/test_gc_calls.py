"""GameCube: results for the mocked calls after the 16th (Skies of Arcadia, 2026-11-13). The oracle scripted 16 call
results a vector (vectors.txt C) and every later call returned 0 in the oracle and both C runners, and TcVectors.repair
looked only at those 16: a pointer a 17th or later call returns was always NULL, every vector faulted and was dropped.
Skies lost about 24 functions so: straight-line ones (8012ab84: bl 801db124; lwz r3,4(r3) as the 17th call) and the
801978xx..801995xx cluster, whose 6-pass loop calls an allocator twice a pass.

The rule:
1. TcOracle.lateOf holds a vector's results for calls 16..TcOracle.RETS-1 (64 in all; 1024 since tests/test_gc_retptr.py), beside the Vec as hwOf (each
   workspace's TcInputs.java keeps its 16), all 0 until a repair sets one: TcOracle.ret gives call k's r3.
2. TcVectors.repair: when the run made more than 16 calls, the newest call's result is tried first if it explains the
   fault (it and the next 15 later results become fresh chunks: TcVectors.lateRepair), any older later one after the
   workspace's repairVec. A run of 16 calls or fewer repairs as before, with the same generator draws.
3. vectors.txt C carries the later results after its 16 words, up to the last one that is not 0 (TcVectors.lateTail):
   a vector without a repaired later result is written byte for byte as before.
4. Both C runners read up to TCRT_RETS (= RETS) words of C and D, a missing word 0 (runner.c, rd_runner.c; tc_rt.c's and
   rd_veh.c's tc_call return them, tc_lastr too).
Planted wrong answers: a C that treats the results after the 16th as 0 (the old runtime), one that keeps the wrong
call's result.

Ghidra end to end (Stub, skipped unless DP_GC_GHIDRA_E2E=1 and config ghidra_home): a stub DOL with a function that
dereferences its 17th call's result at once, one that dereferences its 19th after a 20th call, one with 20 calls and
no dereference and one with 3 calls that dereferences the 2nd's. The first two get valid vectors (none before: every
vector faulted), the translator's C PASSes and the plants FAIL. With DP_GC_OLD_TOOL (a checkout of the parent commit)
the other two are prepared by it too and their vectors.txt and expected.txt must be byte for byte the same.
DP_GC_GHIDRA_DIR: a folder for the Ghidra projects (Ghidra refuses a path with a part starting with a dot).

Run: python tests/test_gc_calls.py
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
GC = TOOL / "targets" / "gamecube_ppc"
RT, RD, SCR = GC / "runtime", GC / "runtime_rd", GC / "ghidra_scripts"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(GC / "translator"), str(GC)]

from test_fmac import vcvars  # noqa: E402
from test_tail import CP, jdk  # noqa: E402


def work():
    w = TOOL / "tests" / ".work"
    w.mkdir(parents=True, exist_ok=True)
    return w


class Wiring(unittest.TestCase):
    def test_sizes_agree(self):
        orc = (SCR / "TcOracle.java").read_text(encoding="utf-8")
        h = (RT / "tc_rt_internal.h").read_text(encoding="utf-8")
        rets = int(re.search(r"static final int RETS = (\d+);", orc).group(1))
        self.assertEqual(int(re.search(r"#define TCRT_RETS (\d+)", h).group(1)), rets)
        self.assertIn('emu.writeRegister("r3", u(ret(v, calls)));', orc)
        self.assertNotIn("calls < 16", orc)
        self.assertIn("#define RD_RETS TCRT_RETS", (RD / "rd_internal.h").read_text(encoding="utf-8"))
        for f in (RT / "runner.c", RD / "rd_runner.c"):
            t = f.read_text(encoding="utf-8")
            self.assertIn("words(line + 1, rets, TCRT_RETS);", t, f.name)
            self.assertIn("dwords(line + 1, frets, TCRT_RETS);", t, f.name)
            self.assertIn("rets[TCRT_RETS]", t, f.name)
        rt = (RT / "tc_rt.c").read_text(encoding="utf-8")
        self.assertNotRegex(rt, r"g_ncalls <=? 16")
        vec = (SCR / "TcVectors.java").read_text(encoding="utf-8")
        self.assertIn("vout.append(lateTail(lateOf.get(v)))", vec)
        self.assertEqual(vec.count("lateRepair(v, r, "), 2)
        tpl = (GC / "template" / "TcInputs.java").read_text(encoding="utf-8")
        self.assertIn("long[] rets = new long[16];", tpl)  # makeVec's draws are unchanged: no new random numbers


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Repair(unittest.TestCase):
    def test_cases(self):
        with tempfile.TemporaryDirectory(dir=work()) as out:
            src = os.pathsep.join([str(SCR), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcCallsStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 603e65ac: no TcOracle.lateOf, ret or lateTail
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcCallsStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        self.assertEqual(got["call_past_16"], "rets late " + " ".join(str(k) for k in range(19, 35)))  # 603e65ac: null
        self.assertEqual(got["older_late"], "rets late " + " ".join(str(k) for k in range(18, 35)))
        self.assertEqual(got["sixteen_calls"], "rets 15 nolate")  # as before: Vec.rets only
        self.assertEqual(got["ret_plain"], "15 0 0 0 0")
        self.assertEqual(got["ret_late"], "81001100 81001500 0 0")
        self.assertEqual(got["tail_none"], "[][]")
        self.assertEqual(got["tail_late"], "[ 00000000 00000000 81001100]")
        self.assertEqual(got["rets_length"], "16 1024")  # 64 until test_gc_retptr.py


# A function of 20 calls that returns the word at 4 past the result of call PICK (0-based); PLANT_IGNORE treats the
# results after the 16th as 0. Raw C through tc_call, as the translator writes it, run by both C runners.
WORK = r"""
#include "tc_rt.h"
#ifndef PICK
#define PICK 18
#endif

u32 fn_test(void)
{
    TcArgs a = {0};
    u32 keep = 0;
    for (u32 k = 0; k < 20; k++) {
        a.r[0] = k;
        u32 x = tc_call(0x80003200u, &a);
#ifdef PLANT_IGNORE
        if (k >= 16) x = 0;
#endif
        if (k == PICK) keep = x;
    }
    return rd32(keep + 4u);
}

void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1)
{
    (void)r;
    (void)f;
    (void)out_r4;
    (void)out_f1;
    *out_r3 = fn_test();
}
"""
PROTOS = '#include "rd_internal.h"\nconst RdProto rd_protos[] = {\n    {0u, "", \'i\', ""},\n};\nconst int rd_nprotos = 0;\n'
ZERO_R, ZERO_F = "R" + " 00000000" * 8, "F" + " 0000000000000000" * 8
LATE = "C" + " 00000000" * 16 + " 00000000 00000000 81001100 81001200"  # calls 18 and 19: two chunks
CHUNKS = "M 81001100 " + "00000000deadbeef" + "00" * 0xf8 + "00000000cafef00d" + "00" * 8


def vectors():
    """V 0: the later results; V 1: the same with C's 16 words only (an older harness: the later calls return 0)."""
    d = "D" + " 0000000000000000" * 16
    return "\n".join(["V 0 dev", ZERO_R, ZERO_F, LATE, d, CHUNKS, "E",
                      "V 1 dev", ZERO_R, ZERO_F, "C" + " 00000000" * 16, d, CHUNKS, "E"]) + "\n"


def run_runner(kind, defines):
    """(the RET r3 of vectors 0 and 1, the CALL count of vector 0, the output) of a runner built from WORK."""
    with tempfile.TemporaryDirectory(prefix="gccalls_", dir=work()) as d:
        p = pathlib.Path(d)
        (p / "work.c").write_text(WORK, encoding="utf-8")
        (p / "main.dol").write_bytes(bytes(0x100))
        (p / "ranges.txt").write_text("", encoding="utf-8")
        (p / "vectors.txt").write_text(vectors(), encoding="utf-8")
        dflags = " ".join(f"/D{x}" for x in defines)
        if kind == "raw":
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{RT / n}"' for n in ("runner.c", "tc_rt.c", "tc_ppc.c"))
            cmd = f'cl /nologo /O1 /fp:strict /W4 /WX /TC {dflags} /I"{RT}" work.c {src} /Fe:runner.exe'
        else:
            (p / "protos.c").write_text(PROTOS, encoding="utf-8")
            vc = str(vcvars(32)).replace("/", "\\")
            src = " ".join(f'"{s}"' for s in [RD / n for n in ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_be.c")]
                           + [RT / "tc_ppc.c"])
            cmd = (f'cl /nologo /O1 /fp:strict /arch:SSE2 /std:c11 /W4 /WX /TC {dflags} /I"{RD}" /I"{RT}" '
                   f'/Ferunner.exe work.c protos.c {src} /link /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /FIXED:NO '
                   f'/BASE:0x5a000000')
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cmd}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        run = subprocess.run([str(p / "runner.exe"), "main.dol", "ranges.txt", "vectors.txt", "all", "out.txt"],
                             cwd=d, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        out = (p / "out.txt").read_text(encoding="utf-8")
    per = re.split(r"(?m)^V \d+ dev\n", out)[1:]
    rets = [re.search(r"(?m)^RET (\w+)", v).group(1) for v in per]
    return rets, per[0].count("\nCALL") + per[0].startswith("CALL"), out


class Runners(unittest.TestCase):
    def check(self, kind):
        rets, calls, out = run_runner(kind, [])
        self.assertEqual(calls, 20, out)
        self.assertEqual(rets[0], "deadbeef", out)  # 603e65ac: 00000000 (call 18 returned 0) and a fault at 4
        self.assertNotEqual(rets[1], "deadbeef", out)  # 16 words: the later calls return 0, as before
        self.assertRegex(out.split("V 1 dev")[1], r"FAULT read 00000004|CRASH", out)
        self.assertEqual(run_runner(kind, ["PICK=19"])[0][0], "cafef00d")
        for plant in (["PLANT_IGNORE"], ["PICK=17"]):  # result 17+ taken as 0; the wrong call's result
            got = run_runner(kind, plant)[0][0]
            self.assertNotEqual(got, "deadbeef", plant)

    @unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
    def test_raw_runner(self):
        self.check("raw")

    @unittest.skipUnless(vcvars(32), "no 32-bit vcvars (MSVC) on this machine")
    def test_readable_runner(self):
        self.check("rd")


# ---------- Ghidra end to end ----------
BASE = 0x80003100
BLR = 0x4E800020
PRO = [0x9421FFF0, 0x7C0802A6, 0x90010014, 0x93E1000C]  # stwu r1,-0x10(r1); mflr r0; stw r0,0x14(r1); stw r31,0xc(r1)
EPI = [0x83E1000C, 0x80010014, 0x7C0803A6, 0x38210010, BLR]
LWZ_R3_4_R3, LWZ_R3_4_R31, MR_R31_R3 = 0x80630004, 0x807F0004, 0x7C7F1B78


def bl(frm, to):
    return 0x48000001 | ((to - frm) & 0x03FFFFFC)


def stub():
    """(DOL bytes, entries): the callee (a blr, never run: mocked), then deref17 (16 calls, the 17th's result
    dereferenced at once: Skies 8012ab84), deref19 (18 calls, the 19th's result kept in r31 across a 20th, then
    dereferenced: the allocator loops), calls20 (20 calls, no dereference), deref3 (the 2nd of 3 calls' result
    dereferenced) and loop6 (one call site, run 6 times)."""
    shapes = {
        "deref17": ["call"] * 17 + [LWZ_R3_4_R3],
        "deref19": ["call"] * 19 + [MR_R31_R3, "call", LWZ_R3_4_R31],
        "calls20": ["call"] * 20,
        "deref3": ["call"] * 2 + [LWZ_R3_4_R3, MR_R31_R3, "call", 0x7FE3FB78],  # ...; mr r3,r31
        "loop6": [0x3BE00006, "call", 0x37FFFFFF, 0x4082FFF8],  # li r31,6; L: bl; addic. r31,r31,-1; bne L
    }
    callee = BASE
    words, entries, at = [BLR], {"callee": callee}, BASE + 4
    for name, body in shapes.items():
        entries[name] = at
        fn = PRO + body + EPI
        for i, w in enumerate(fn):
            words.append(bl(at + 4 * i, callee) if w == "call" else w)
        at += 4 * len(fn)
    code = b"".join(struct.pack(">I", x) for x in words)
    hdr = bytearray(0x100)
    struct.pack_into(">I", hdr, 0x00, 0x100)
    struct.pack_into(">I", hdr, 0x48, BASE)
    struct.pack_into(">I", hdr, 0x90, len(code))
    struct.pack_into(">I", hdr, 0xE0, BASE)
    return bytes(hdr) + code, entries


SAME = ("calls20", "deref3", "loop6")  # never fault through a later call's result: harnesses as before


class Hook(unittest.TestCase):
    def test_late_calls(self):
        """hooks.late_calls (target.json stamp scope save_helper_frame): a body that can make a 17th call."""
        import importlib.util
        from unittest import mock
        from test_sync import TMP  # noqa: F401  (a project for core.paths)
        from core import paths
        spec = importlib.util.spec_from_file_location("gc_hooks", GC / "hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        img, e = stub()
        ends = sorted(e.values()) + [BASE + len(img) - 0x100]
        with tempfile.TemporaryDirectory(dir=work()) as d:
            p = pathlib.Path(d, "stub.dol")
            p.write_bytes(img)
            bodies = pathlib.Path(d, "function_bodies.txt")
            bodies.write_text("".join(f"{a:08x} {a:x} {ends[ends.index(a) + 1]:x}\n" for a in e.values()),
                              encoding="utf-8")
            with mock.patch.object(paths, "FUNCTION_BODIES", bodies):
                got = {k: hooks.late_calls({"binary_path": p}, f"{a:08x}") for k, a in e.items()}
                self.assertTrue(hooks.late_calls({"binary_path": p}, "80009000"))  # no body: in scope
                self.assertTrue(hooks.stamp_scope({"binary_path": p}, f"{e['deref17']:08x}", "save_helper_frame"))
        self.assertEqual(got, {"callee": False, "deref17": True, "deref19": True, "calls20": True, "deref3": False,
                               "loop6": True})


PLANT_HEAD = r"""
static u32 plant_n, plant_prev;
static u32 plant_call(u32 t, const TcArgs *a)
{
    u32 x = tc_call(t, a), p = plant_prev;
    plant_prev = x;
    if (t == 0x80003100u && plant_n++ >= 16u) return PLANT_MODE == 0 ? 0u : p;
    return x;
}
#define tc_call plant_call
"""


def plant(src, mode, entry):
    """The translated C with every call after the 16th returning 0 (mode 0) or the call before's result (mode 1). The
    count restarts each vector: reset before the function's first call."""
    head = f"#define PLANT_MODE {mode}\n" + PLANT_HEAD
    last = list(re.finditer(r"(?m)^#include .*\n", src))[-1]
    src = src[:last.end()] + head + src[last.end():]
    body = re.search(rf"(?m)^[^\n;]*\bfn_{entry:08x}\([^)]*\)\n\{{\n", src)
    assert body, src
    m = re.compile(r"(?m)^ *[^\n]*tc_call\(").search(src, body.end())
    assert m, src
    return src[:m.start()] + "    plant_n = 0;\n    plant_prev = 0;\n" + src[m.start():]


def _cfg_local():
    try:
        return json.loads((TOOL / "config.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@unittest.skipUnless(os.environ.get("DP_GC_GHIDRA_E2E") == "1" and _cfg_local().get("ghidra_home") and vcvars(64),
                     "Ghidra end to end: set DP_GC_GHIDRA_E2E=1 (config.local.json ghidra_home, MSVC)")
class Stub(unittest.TestCase):
    def dp(self, tool, ws, *args, timeout=1200):
        r = subprocess.run([sys.executable, str(tool / "decomp.py"), "--project", str(ws), *args], cwd=str(tool),
                           capture_output=True, text=True, errors="replace", timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def workspace(self, tool, img, tag):
        ws = pathlib.Path(tempfile.mkdtemp(prefix=f"gccalls{tag}_", dir=work()))
        gdir = pathlib.Path(os.environ.get("DP_GC_GHIDRA_DIR") or ws) / ws.name
        self.addCleanup(shutil.rmtree, ws, True)
        if gdir != ws / ws.name:
            self.addCleanup(shutil.rmtree, gdir, True)
        (ws / "stub.dol").write_bytes(img)
        (ws / "project.json").write_text(json.dumps({
            "name": "calls stub", "target": "gamecube_ppc", "ghidra_name": f"callsstub{tag}", "binary": "stub.dol",
            "binary_sha256": hashlib.sha256(img).hexdigest(),
            "game_ranges": [[f"{BASE:08x}", f"{BASE + len(img) - 0x100:08x}"]], "global_ranges": [],
            "fixed_regs": {"r2": "80350000", "r13": "8034e720"}, "port": 8899, "references": [],
            "ghidra_dir": str(gdir).replace("\\", "/")}), encoding="utf-8")
        (ws / "oracle").mkdir()  # as tools/game_setup.py does: the target's template inputs
        shutil.copyfile(tool / "targets" / "gamecube_ppc" / "template" / "TcInputs.java", ws / "oracle" / "TcInputs.java")
        rc, log = self.dp(tool, ws, "setup")
        self.assertEqual(rc, 0, log[-3000:])
        return ws

    def test_prepare_and_judge(self):
        img, e = stub()
        ws = self.workspace(TOOL, img, "new")
        for k in ("deref17", "deref19") + SAME:
            rc, log = self.dp(TOOL, ws, "prepare", f"{e[k]:08x}")
            self.assertEqual(rc, 0, f"{k}: {log[-3000:]}")
        for k in ("deref17", "deref19"):
            h = ws / "harness" / f"{e[k]:08x}"
            summ = json.loads((h / "oracle_summary.json").read_text(encoding="utf-8"))
            vt = (h / "vectors.txt").read_text(encoding="utf-8")
            self.assertGreater(summ["dev_valid"], 0, f"{k}: {summ}")  # 603e65ac: 0 (every vector faulted)
            self.assertGreater(summ["hidden_valid"], 0, f"{k}: {summ}")
            longest = max(len(ln.split()) for ln in vt.splitlines() if ln.startswith("C "))
            self.assertGreater(longest, 17, k)  # a repaired later result on line C
            out = ws / f"tr_{e[k]:08x}.c"
            r = subprocess.run([sys.executable, str(GC / "translate.py"), "--repo", str(GC / "translator"),
                                "--workspace", str(ws), "--addr", f"{e[k]:08x}", "--meta", str(h / "meta.json"),
                                "--target-json", str(GC / "target.json"), "--out", str(out)],
                               capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            src = out.read_text(encoding="utf-8")
            verdicts = {"translated": self.check(ws, e[k], src),
                        "zero": self.check(ws, e[k], plant(src, 0, e[k])),
                        "previous": self.check(ws, e[k], plant(src, 1, e[k]))}
            print(f"{k} {e[k]:08x}: dev {summ['dev_valid']} hidden {summ['hidden_valid']} C words {longest - 1} "
                  f"{verdicts}")
            self.assertEqual(verdicts, {"translated": "PASS", "zero": "FAIL", "previous": "FAIL"}, k)
        for k in SAME:
            vt = (ws / "harness" / f"{e[k]:08x}" / "vectors.txt").read_text(encoding="utf-8")
            self.assertEqual({len(ln.split()) for ln in vt.splitlines() if ln.startswith("C ")}, {17}, k)
        old = os.environ.get("DP_GC_OLD_TOOL")
        if not old:
            return
        ows = self.workspace(pathlib.Path(old), img, "old")
        for k in SAME:
            rc, log = self.dp(pathlib.Path(old), ows, "prepare", f"{e[k]:08x}")
            self.assertEqual(rc, 0, f"old {k}: {log[-3000:]}")
            for n in ("vectors.txt", "expected.txt"):
                a = (ws / "harness" / f"{e[k]:08x}" / n).read_bytes()
                b = (ows / "harness" / f"{e[k]:08x}" / n).read_bytes()
                print(f"{k} {n}: {hashlib.sha256(a).hexdigest()[:16]} new, {hashlib.sha256(b).hexdigest()[:16]} old")
                self.assertEqual(a, b, f"{k} {n}")
        for k in ("deref17", "deref19"):  # the parent's harness, for the record (the fails-before of the rule)
            rc, log = self.dp(pathlib.Path(old), ows, "prepare", f"{e[k]:08x}")
            p = ows / "harness" / f"{e[k]:08x}" / "oracle_summary.json"
            s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            print(f"old {k}: prepare rc {rc}, dev {s.get('dev_valid')} hidden {s.get('hidden_valid')} "
                  f"invalid {s.get('invalid')}")

    def check(self, ws, entry, src):
        f = ws / f"try_{entry:08x}.c"
        f.write_text(src, encoding="utf-8")
        rc, log = self.dp(TOOL, ws, "check", f"{entry:08x}", "--work", str(f), "--json", timeout=600)
        m = re.search(r'"verdict":\s*"(\w+)"', log)
        return m.group(1) if m else f"no verdict: {log[-800:]}"


if __name__ == "__main__":
    unittest.main()
