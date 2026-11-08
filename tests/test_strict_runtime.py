"""The stricter judge's runtimes (pre-wave item 2; tests/test_strict_judge.py has the rest).

3. Runtimes: tc_strict.h's tcs_at and tcs_fr against the Python models (a small C program), and each raw runner (DC,
   Saturn, GameCube, Xbox 360) on a synthetic function with one mocked call: the snapshots (stored bytes, a code
   pointer, a range past RAM's end "-"), the junk in fr1..fr11 (DC), the mock writes (not into code, not past RAM,
   not for another callee), the exit frame, each flag alone; with the flags off ("J 0" or no header) the same output
   as with no strict lines at all.
4. Readable: the readable runner on the same shape through a trapped call: a guest pointer and a host local.
5. Xbox 360's judge (x360_compare): CJ, CS (STRICT_INDEX) and XR in 64 bits; its stamp scope (x360_scope).
Synthetic data. Run: python -W error tests/test_strict_runtime.py.
"""
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_strict_judge import FLAGS, ROOT, T, WORK, at, at8, keys, result_file, stream  # noqa: E402
import game_config  # noqa: E402

from core import check, paths, readable_check, toolcfg  # noqa: E402

VC64 = toolcfg.load(ROOT).get("vcvars")

# One mocked call to TARGET with p (r4 / r3), q, a code pointer, a pointer 2 bytes before RAM's end, and s1 = p + 0x40.
WORK_C = """#include "tc_rt.h"
u32 fn_test(u32 p, u32 q);
u32 fn_test(u32 p, u32 q)
{
    TcArgs a = {0};
    u32 rv;
    wr32(p, 0x11223344u);
    wr16(q + 2, 0x5566);
    a.r[0] = p;
    a.r[1] = q;
    a.r[2] = 0x%(code)08xu;
    a.r[3] = 0x%(edge)08xu;
    a.s[1] = p + 0x40;
    rv = %(cast)stc_call(0x%(target)08xu, &a);
    %(fr5)s
    return rv + rd32(p + 4);
}
"""
THUNK_SH = """#include "tc_rt.h"
u32 fn_test(u32 p, u32 q);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_test(r[0], r[1]);
}
"""
THUNK_X360 = """#include "tc_rt.h"
u32 fn_test(u32 p, u32 q);
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1)
{
    (void)f;
    (void)out_f1;
    *out_r3 = fn_test((u32)r[0], (u32)r[1]);
}
"""
THUNK_GC = """#include "tc_rt.h"
u32 fn_test(u32 p, u32 q);
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1);
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1)
{
    (void)f;
    (void)out_r4;
    (void)out_f1;
    *out_r3 = fn_test(r[0], r[1]);
}
"""


def fr(z):
    """A float register's junk (TcJunk.fr, tc_strict.h tcs_fr): the sign and fraction of z, +-[1, 2)."""
    return z & 0x807FFFFF | 0x3F800000


PARITY_C = """#include <stdio.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned int u32;
static int tcs_read(u32 p, u32 n, u8 *out) { (void)p; memset(out, 0, n); return 1; }
static void tcs_write(u32 p, u32 n, const u8 *in) { (void)p; (void)n; (void)in; }
static void tcs_scratch(int i, u32 bits) { (void)i, (void)bits; }
#include "tc_strict.h"
int main(int argc, char **argv)
{
    long long s, v, c, sl;
    int k;
    FILE *f = NULL;
    if (argc != 2 || fopen_s(&f, argv[1], "r")) return 2;
    tcrt_strict_line("J 0");
    tcs_call(0, 0, NULL);
    while (fscanf_s(f, "%lld %lld %lld %lld %d", &s, &v, &c, &sl, &k) == 5) {
        uint64_t b = tcs_at((uint64_t)s, (uint64_t)v, (uint64_t)c, (uint64_t)sl + (uint64_t)(k / 8));
        uint64_t z = tcs_at((uint64_t)s, (uint64_t)v, (uint64_t)c, (uint64_t)sl);
        printf("%llx %u %x\\n", z, (unsigned)(u8)(b >> (8 * (k % 8))), tcs_fr(z));
    }
    tcrt_strict_report(stdout);
    fclose(f);
    return tcrt_strict_flags();
}
"""
# target: (p, q, code range, edge, callee, exit sp, fields p q code edge s1, srcs, little-endian, fr5 junk)
RT = {"dreamcast_sh4": (0x8C040000, 0x8C040100, (0x8C010000, 0x8C010100), 0x8CFFFFFE, 0x8C020000, 0x8CFFE000,
                        (1, 2, 3, 4, 14), ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c"), True, True),
      "saturn_sh2": (0x06040000, 0x06040100, (0x06000000, 0x06000100), 0x060FFFFE, 0x06020000, 0x060FE000,
                     (1, 2, 3, 4, 14), ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c"), False, False),
      "gamecube_ppc": (0x80040000, 0x80040100, (0x80003100, 0x80003200), 0x817FFFFE, 0x80020000, 0x817FE000,
                       (1, 2, 3, 4, 18), ("runner.c", "tc_rt.c", "tc_ppc.c"), False, False),
      "xbox360_ppc": (0x40040000, 0x40040100, (0x82000000, 0x82000100), 0x400FFFFE, 0x82020000, 0x700FE000,
                      (1, 2, 3, 4, 18), ("runner.c", "tc_rt.c", "tc_ppc.c"), False, False)}
NO_FSCA = ("gamecube_ppc", "xbox360_ppc")  # runners without the SH-4 fsca table argument


def vectors(t, header=True, flags=15, masks=()):
    p, q, _code, _edge, target, _sp, (fp, fq, fc, fe, fs), *_ = RT[t]
    head = [f"J {flags}"] + [f"U {target:08x} {f} {n}" for f, n in ((fp, 8), (fq, 4), (fc, 4), (fe, 4), (fs, 4))]
    head += [f"U 01234567 {fp} 4"] + [f"U {a:08x} 0 {m}" for a, m in masks]  # masks: (callee, fr mask)
    strict = ["N 1234 7"] + [f"W 0 {f} {n}" for f, n in ((fp, 8), (fq, 4), (fc, 4), (fe, 4))] + [f"W 1 {fp} 4"]
    body = ["V 0 dev"] + (strict if header else []) + [f"R {p:08x} {q:08x} 0 0", "C 00000005", "E"]
    return "\n".join((head if header else []) + body) + "\n"


def results(out):
    """{"ret": first RET word, "w": {addr: hex}, "cs": [CS lines], "xr": XR line or None}."""
    r = {"w": {}, "cs": [], "xr": None}
    for line in out.splitlines():
        t = line.split()
        if t[0] == "RET":
            r["ret"] = int(t[1], 16)
        elif t[0] == "W":
            r["w"][int(t[1], 16)] = t[2]
        elif t[0] == "CS":
            r["cs"].append(line)
        elif t[0] == "XR":
            r["xr"] = line
    return r


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runtimes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="strict_rt_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        cmds = []
        for t, (p, q, code, edge, target, *_r, srcs, _le, fr5) in RT.items():
            b = d / t
            b.mkdir()
            line = "wrf32(p + 0x20, sh4_fr(5));" if fr5 else "wr32(p + 0x20, 0u);"
            cast = "(u32)" if t == "xbox360_ppc" else ""  # its tc_call returns all of r3
            (b / "work.c").write_text(WORK_C % {"code": code[0], "edge": edge, "target": target, "fr5": line,
                                                "cast": cast})
            (b / "thunk.c").write_text({"gamecube_ppc": THUNK_GC, "xbox360_ppc": THUNK_X360}.get(t, THUNK_SH))
            rt = T / t / "runtime"
            cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
                        + " ".join(f'"{rt / s}"' for s in srcs))
        (d / "parity.c").write_text(PARITY_C)
        cmds.append(f'cd /d "{d}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{T / "dreamcast_sh4" / "runtime"}" parity.c')
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                           text=True, errors="replace", timeout=600)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:])
        (d / "image.bin").write_bytes(bytes(0x100))
        (d / "fsca.bin").write_bytes(bytes(1 << 20))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_one(self, t, vecs):
        b = self.d / t
        code = RT[t][2]
        (b / "code.txt").write_text(f"{code[0]:08x} {code[1]:08x}\n")
        (b / "vectors.txt").write_text(vecs)
        args = [str(self.d / "image.bin"), str(b / "code.txt")]  # GameCube: a DOL with no sections
        if t not in NO_FSCA:
            args.append(str(self.d / "fsca.bin"))
        r = subprocess.run([str(b / "runner.exe"), *args, str(b / "vectors.txt"), "all", str(b / "out.txt")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, t + r.stderr)
        return (b / "out.txt").read_text()

    def test_parity(self):
        ks = keys()
        (self.d / "keys.txt").write_text("".join(" ".join(map(str, k)) + "\n" for k in ks))
        r = subprocess.run([str(self.d / "parity.exe"), str(self.d / "keys.txt")], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(), [f"{z:x} {at8(*k)} {fr(z):x}" for k in ks for z in [at(*k[:4])]])

    def test_strict(self):
        for t, (p, q, _code, _edge, _target, sp, (fp, fq, fc, fe, fs), _srcs, le, fr5) in RT.items():
            got = results(self.run_one(t, vectors(t)))
            word = "<I" if le else ">I"
            stored = "4433221100000000" if le else "1122334400000000"
            self.assertEqual(got["cs"], [f"CS 0 {fp} {stored}", f"CS 0 {fq} " + ("00006655" if le else "00005566"),
                                         f"CS 0 {fc} 00000000", f"CS 0 {fe} -"], t)
            junk = struct.pack(word, fr(at(0x1234, 7, 0, 21))) if fr5 else bytes(4)
            self.assertEqual(got["w"], {p: stream(fp, 8).hex(), p + 0x20: junk.hex(), p + 0x40: stream(fs, 4).hex(),
                                        q: stream(fq, 4).hex()}, t)  # nothing into code, past RAM, for 01234567
            self.assertEqual(got["ret"], (5 + struct.unpack(word, stream(fp, 8)[4:])[0]) & 0xFFFFFFFF, t)
            n = {"gamecube_ppc": (18, 0), "xbox360_ppc": (0, 0)}.get(t, (7, 4))
            wide = {"gamecube_ppc": 18, "xbox360_ppc": 36}.get(t, 0)  # 64-bit words: GC f14..f31, X360 r14..f31
            want = f"XR {sp:08x}" + " 00000000" * sum(n) + (" " + "0" * 16) * wide
            self.assertEqual(got["xr"], want, t)
            for f in (1, 2, 4, 8):  # one flag alone: only its own effect
                one = results(self.run_one(t, vectors(t, flags=f)))
                self.assertEqual(bool(one["cs"]), f == 4, (t, f))
                self.assertEqual(one["xr"] is not None, f == 8, (t, f))
                self.assertEqual(p + 0x40 in one["w"], f == 2, (t, f))
                self.assertEqual(one["w"][p + 0x20] == "00000000", f != 1 or not fr5, (t, f))

    def test_junk_masks(self):
        """A callee's "U callee 0 mask" line: only the fr it may change are junk (TcStrict.changes); a mask line is
        never a write fact."""
        for t, (p, _q, _code, _edge, target, *_r, le, fr5) in RT.items():
            junk = struct.pack("<I", fr(at(0x1234, 7, 0, 21))).hex() if fr5 else "00000000"
            for masks, want in ((((target, 0xFFE & ~(1 << 5)),), "00000000"),  # keeps fr5
                                (((target, 1 << 5),), junk),                   # changes fr5 alone
                                (((0x01234567, 0),), junk)):                   # another callee's: the ABI's
                got = results(self.run_one(t, vectors(t, flags=1, masks=masks)))
                self.assertEqual(got["w"][p + 0x20], want, (t, masks))
            writes = results(self.run_one(t, vectors(t, flags=2)))["w"]
            self.assertEqual(results(self.run_one(t, vectors(t, flags=2, masks=((target, 0),))))["w"], writes, t)

    def test_off(self):
        for t, (p, q, *_r, le, _fr5) in RT.items():
            plain = self.run_one(t, vectors(t, header=False))
            self.assertEqual(self.run_one(t, vectors(t, flags=0)), plain, t)
            got = results(plain)
            self.assertEqual((got["cs"], got["xr"], got["ret"]), ([], None, 5), t)
            self.assertEqual(got["w"], {p: "44332211" if le else "11223344", p + 0x20: "00000000",
                                        q + 2: "6655" if le else "5566"}, t)


READABLE_C = """#include "rd_rt.h"
typedef u32 (*Fn)(u32 *a, u32 *b);
u32 fn_test(u32 *p);
u32 fn_test(u32 *p)
{
    u32 local[2];
    u32 rv;
    union { f32 f; u32 u; } x;
    local[0] = 0x01020304u;
    local[1] = 0x05060708u;
    p[0] = 0x11223344u;
    rv = ((Fn)(uintptr_t)0x8c020000u)(p, local);
    x.f = sh4_fr(5);
    p[8] = x.u;
    return rv + p[1] + local[1];
}
"""
READABLE_THUNK = """#include "rd_rt.h"
u32 fn_test(u32 *p);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_test((u32 *)(uintptr_t)r[0]);
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class Readable(unittest.TestCase):
    def test_trapped_call(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="strict_rd_", dir=WORK) as tmp:
            d = pathlib.Path(tmp)
            (d / "work.c").write_text(READABLE_C)
            ok, msgs = readable_check.build(paths.config(), [d / "work.c"], READABLE_THUNK,
                                            {0x8C020000: ("ii", "i")}, d / "b")
            self.assertTrue(ok, msgs)
            (d / "image.bin").write_bytes(bytes(0x100))
            (d / "fsca.bin").write_bytes(bytes(1 << 20))
            (d / "code.txt").write_text("8c010000 8c010100\n")
            p = 0x8C040000
            (d / "v.txt").write_text("J 15\nU 8c020000 1 8\nU 8c020000 2 8\nV 0 dev\nN 1234 7\nW 0 1 8\nW 0 2 8\n"
                                     f"R {p:08x} 0 0 0\nC 00000005\nE\n")
            r = subprocess.run([str(d / "b" / "rd_runner.exe"), str(d / "image.bin"), str(d / "code.txt"),
                                str(d / "fsca.bin"), str(d / "v.txt"), "all", str(d / "out.txt")],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = results((d / "out.txt").read_text())
        self.assertEqual(got["cs"], ["CS 0 1 4433221100000000", "CS 0 2 0403020108070605"])  # guest; host local
        junk = fr(at(0x1234, 7, 0, 21))
        self.assertEqual(got["w"], {p: stream(1, 8).hex(), p + 0x20: struct.pack("<I", junk).hex()})
        word = (lambda b: struct.unpack("<I", b[4:])[0])
        self.assertEqual(got["ret"], (5 + word(stream(1, 8)) + word(stream(2, 8))) & 0xFFFFFFFF)
        self.assertEqual(got["xr"], "XR 8cffe000" + " 00000000" * 11)


class X360Judge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(T / "xbox360_ppc"))
        import x360_compare
        cls.xc = x360_compare

    def test_xbox360(self):
        """x360_compare: the CJ alternative (the CALL record's field), CS through STRICT_INDEX, XR in 64 bits."""
        xc = self.xc
        meta = {"ret": "u32", "callees": [{"addr": "82020000", "regs": ["r3", "r4", "s1"]}]}
        z = "0" * 16
        call = (lambda r4: f"CALL 82020000 {0x40040000:016x} {r4:016x} " + " ".join([z] * 19)
                + f" {0x40040040:016x} " + " ".join([z] * 6))
        ret = f"RET {5:016x} {z}"
        xr = "XR 700fe000 " + " ".join(f"{i:016x}" for i in range(18)) + " " + " ".join(["7ff8000000000001"] * 18)
        exp = result_file([ret, call(0xDEADBEEF), "CJ 0 2 " + z, "CS 0 1 11223344xx", "CS 0 18 55667788", xr], xc)
        self.assertEqual(xc.STRICT_INDEX["s1"], 18)
        for r4, cs, x, n in ((0xDEADBEEF, "CS 0 1 1122334400", xr, 0), (0, "CS 0 1 1122334400", xr, 0),
                             (1, "CS 0 1 1122334400", xr, 1), (0, "CS 0 1 11223345ff", xr, 1),
                             (0, "CS 0 1 1122334400", xr.replace("7ff8000000000001", "7ff0000000000002"), 0),
                             (0, "CS 0 1 1122334400", xr.replace("0000000000000001", "0000000100000001", 1), 1)):
            act = result_file([ret, call(r4), cs, "CS 0 18 55667788", x], xc)
            d = xc.compare_vector(meta, b"", {"patches": []}, exp, act)
            self.assertEqual(len(d), n, (hex(r4), cs, x[:40], d))
        act = result_file([ret, call(0), "CS 0 1 1122334400", "CS 0 18 55667789", xr], xc)
        self.assertEqual(len(xc.compare_vector(meta, b"", {"patches": []}, exp, act)), 1)  # s1's snapshot
        rep = []
        act = result_file([ret, call(0), "CS 0 1 -", "CS 0 18 55667788", xr], xc)
        self.assertEqual(xc.compare_vector(meta, b"", {"patches": []}, exp, act, {"call_snapshots": "report"},
                                           rep), [])
        self.assertEqual(len(rep), 1)

    def test_wiring(self):
        """test_strict_judge's Wiring for this target: one copy of the shared files, locked, flags on, the hooks in
        both runtimes (the readable one runs it: tests/test_x360_rd_strict.py)."""
        x = T / "xbox360_ppc"
        g, rt = x / "ghidra_scripts", x / "runtime"
        for n in ("ghidra_scripts/TcJunk.java", "runtime/tc_strict.h"):
            self.assertEqual((x / n).read_bytes(), (T / "gamecube_ppc" / n).read_bytes(), n)
        cfg = json.loads((x / "target.json").read_text(encoding="utf-8"))
        self.assertTrue({"TcJunk.java", "TcStrict.java"} <= set(cfg["locked_scripts"]))
        self.assertEqual([cfg[f] for f in FLAGS], [True] * 4)
        call = "TcStrict.call(res, emu, calls, zeroed)"  # in clobber's place, which it may still call
        for f, hooks in (("TcOracle", ("TcStrict.of(", call, "TcStrict.exit(res, emu);")),
                         ("TcVectors", ("TcStrict.vec(r)", "TcStrict.exp(r)", "TcStrict.replay(this, v, r, set)")),
                         ("TcPrepare", ("TcStrict.header(facts)", "TcStrict.meta(facts)", "TcStrict.dropped[s];")),
                         ("TcFacts", ('case "mock_write":', 'case "exit_frame":'))):
            for h in hooks:
                self.assertEqual((g / f"{f}.java").read_text(encoding="utf-8").count(h), 1, (f, h))
        src = (rt / "tc_rt.c").read_text(encoding="utf-8")
        for h in ('#include "tc_strict.h"', "tcrt_strict_reset();", "tcrt_strict_report(out);", "tcs_call("):
            self.assertEqual(src.count(h), 1, h)
        veh = (x / "runtime_rd" / "rd_veh.c").read_text(encoding="utf-8")
        for h, n in (('#include "tc_strict.h"', 1), ("strict_call(target, r, s);", 2), ("tcrt_strict_reset();", 1)):
            self.assertEqual(veh.count(h), n, h)
        mem = (x / "runtime_rd" / "rd_mem.c").read_text(encoding="utf-8")
        self.assertEqual(mem.count("tcrt_strict_report(out);"), 1)
        for r in (rt / "runner.c", x / "runtime_rd" / "rd_runner.c"):
            run = r.read_text(encoding="utf-8")
            self.assertEqual(run.count("tcrt_strict_line(line);"), 2, r.name)
            self.assertLess(run.index("} else if (!active) {"), run.index("|| line[0] == 'W'"), r.name)
            self.assertLess(run.index("line[0] == 'J' || line[0] == 'U'"), run.index("} else if (line[0] == 'V') {"))
        self.assertRegex((x / "x360_check.py").read_text(encoding="utf-8"), r"strict = strict_judge\.modes\(cfg\)[^$]*"
                         r"compare_vector\(.*strict, report := \[\]\)\n\s+strict_judge\.note\(result, s, i, report\)")
        lines = (g / "TcStrict.java").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 400)
        self.assertEqual([s for s in lines if len(s) > 120], [])

    def test_stamp_scope(self):
        """With the six flags off the scripts' change would leave every stamp as it was; with any on (target.json
        since 2026-11-06) every function is in scope (x360_scope)."""
        import x360_scope
        self.assertFalse(x360_scope.in_scope({"mock_junk": False}, "82000000", "prewave_flags"))
        for f in x360_scope.FLAG_SCOPES["prewave_flags"]:
            self.assertTrue(x360_scope.in_scope({f: True}, "82000000", "prewave_flags"), f)


if __name__ == "__main__":
    unittest.main()
