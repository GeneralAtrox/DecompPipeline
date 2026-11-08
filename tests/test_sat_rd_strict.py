"""Saturn: the readable runtime's side of the stricter judge (runtime_rd/rd_veh.c includes tc_strict.h, as
runtime/tc_rt.c does; Dreamcast's and Xbox 360's readable runtimes already do). Builds three real runners (32-bit, the
judges' C flags) on one synthetic vector with every flag on (J 15): a readable function that stores into a guest
pointer, passes it, a host local and a pointer into a code page to a trapped call (06020000), and returns the call's
result plus the local's last byte; the same function as raw C on the readable runtime (tc_call); and that raw C on the
raw runtime (runtime/, the raw judge's answer key). It checks:
  - the call snapshots: the guest bytes, the local's bytes, and the code page's bytes through its guard;
  - the mock writes: into the guest pointer (W lines), into the local (seen in the result), none into code;
  - the exit frame: XR, r15 and r8..r14 (line G) and four zero words, as tc_rt.c prints it;
  - the three runners agree on all of it (raw C and readable C judged alike);
  - planted mutants of the readable function (the guest store moved after the call; the local read before it) are
    told apart from it with the strict lines, and not without them (the stricter judge is what kills them);
  - with no strict lines, no CS and no XR, and the C's own writes and result as before.
Synthetic data. Skipped without a 32-bit Visual Studio environment.
usage: python tests/test_sat_rd_strict.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_fmac import vcvars  # noqa: E402
from test_strict_judge import stream  # noqa: E402

S = TOOL / "targets" / "saturn_sh2"
RD, RT = S / "runtime_rd", S / "runtime"
RD_SOURCES = [RD / n for n in ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_sh4.c", "rd_p4.c", "rd_trace.c",
                               "rd_be.c")] + [RT / "tc_p4.c"]  # core/readable_check.RUNTIME_SOURCES
RAW_SOURCES = [RT / n for n in ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")]  # core/check.RUNTIME_SRCS
FLAGS = "/fp:strict /arch:SSE2 /std:c11 /W4 /WX /TC"  # core/readable_check.C_FLAGS
LINK = "/link /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /FIXED:NO /BASE:0x5a000000"  # readable_check.link_flags

READABLE = r"""
#include "rd_rt.h"

typedef u32 (*Fn)(u8 *a, u8 *b, u8 *code);
u32 fn_test(u8 *p);
u32 fn_test(u8 *p)
{
    u8 local[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    u32 rv;
    u8 last = local[7];
    p[0] = 0x11;
    LATE0
    rv = ((Fn)(uintptr_t)0x06020000u)(p, local, (u8 *)(uintptr_t)0x06000010u);
    LATE1
    (void)last;
    return rv + READ;
}

void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    *out_fr0 = 0.0f;
    *out_r0 = fn_test((u8 *)(uintptr_t)r[0]);
}
"""
GOOD = {"LATE0": "p[1] = 0x22;", "LATE1": "", "READ": "local[7]"}
MUTANTS = {"late_store": {**GOOD, "LATE0": "", "LATE1": "p[1] = 0x22;"},  # caught by the call snapshot
           "early_read": {**GOOD, "READ": "last"}}  # caught by the mock write into the local
RAW = r"""
#include "tc_rt.h"

static u32 fn_raw(u32 p)
{
    TcArgs a = {0};
    u32 s = 0x060fd000u; /* the original's own stack local */
    for (u32 k = 0; k < 8; k++) wr8(s + k, (u8)(k + 1));
    wr8(p, 0x11);
    wr8(p + 1, 0x22);
    a.r[0] = p;
    a.r[1] = s;
    a.r[2] = 0x06000010u;
    u32 rv = tc_call(0x06020000u, &a); /* the call first: the local is read after it */
    return rv + rd8(s + 7);
}

void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    *out_fr0 = 0.0f;
    *out_r0 = fn_raw(r[0]);
}
"""
PROTOS = ('#include "rd_internal.h"\nconst RdProto rd_protos[] = {\n    {0x06020000u, "PPP", \'i\', ""},\n};\n'
          "const int rd_nprotos = 1;\n")
P = 0x06040000
STRICT = "J 15\nU 06020000 1 8\nU 06020000 2 8\nU 06020000 3 8\n"
VECTOR = "V 0 dev\n%sR %x 0 0 0\nG 1 2 3 4 5 6 7\nC 5\nE\n"
SNAPS = "N 1234 7\nW 0 1 8\nW 0 2 8\nW 0 3 8\n"
XR = "XR 060fe000 " + " ".join(f"{k:08x}" for k in range(1, 8)) + " 00000000" * 4


def results(out):
    """{"ret": r0, "mem": {addr: byte} of the W lines, "cs": [CS lines], "xr": XR line or None}."""
    r = {"mem": {}, "cs": [], "xr": None, "other": []}
    for line in out.splitlines():
        t = line.split()
        if t[0] == "RET":
            r["ret"] = int(t[1], 16)
        elif t[0] == "W":
            r["mem"].update({int(t[1], 16) + k: b for k, b in enumerate(bytes.fromhex(t[2]))})
        elif t[0] == "CS":
            r["cs"].append(line)
        elif t[0] == "XR":
            r["xr"] = line
        elif t[0] in ("CRASH", "FAULT", "CODEREAD", "HALT"):
            r["other"].append(line)
    return r


def at_p(r):
    return bytes(r["mem"].get(P + k, 0) for k in range(8))


@unittest.skipUnless(vcvars(32), "no 32-bit vcvars (MSVC) on this machine")
class ReadableStrict(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="satrdstrict_", dir=work)
        d = pathlib.Path(cls.tmp.name)
        (d / "image.bin").write_bytes(bytes(range(256)))
        (d / "ranges.txt").write_text("06000000 06000100\n", encoding="utf-8")
        (d / "fsca.bin").write_bytes(bytes(2 * 65536 * 8))
        (d / "on.txt").write_text(STRICT + VECTOR % (SNAPS, P), encoding="utf-8")
        (d / "off.txt").write_text(VECTOR % ("", P), encoding="utf-8")
        (d / "protos.c").write_text(PROTOS, encoding="utf-8")
        cls.d, cls.got = d, {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build_run(self, name, source, sources, rd=True):
        d = self.d / name
        d.mkdir()
        (d / "work.c").write_text(source, encoding="utf-8")
        vc = str(vcvars(32)).replace("/", "\\")
        own = f'work.c "{self.d / "protos.c"}"' if rd else "work.c"
        inc = f'/I"{RD}" /I"{RT}"' if rd else f'/I"{RT}"'
        src = " ".join(f'"{s}"' for s in sources)
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 {FLAGS} {inc} /Ferun.exe {own} {src} '
                           f'{LINK}"', cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        out = {}
        for k in ("on", "off"):
            run = subprocess.run([str(d / "run.exe"), str(self.d / "image.bin"), str(self.d / "ranges.txt"),
                                  str(self.d / "fsca.bin"), str(self.d / f"{k}.txt"), "all", f"{k}.out"], cwd=d,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            out[k] = results((d / f"{k}.out").read_text(encoding="utf-8"))
            self.assertEqual(out[k]["other"], [], k)
        return out

    def readable(self, subs):
        src = READABLE
        for k, v in subs.items():
            src = src.replace(k, v)
        return src

    def test_readable_raw_and_raw_runtime_alike(self):
        got = {"readable": self.build_run("readable", self.readable(GOOD), RD_SOURCES),
               "raw_on_rd": self.build_run("raw_on_rd", RAW, RD_SOURCES),
               "raw": self.build_run("raw", RAW, RAW_SOURCES, rd=False)}
        for name, g in got.items():
            on, off = g["on"], g["off"]
            self.assertEqual(on["cs"], ["CS 0 1 1122000000000000", "CS 0 2 0102030405060708",
                                        "CS 0 3 1011121314151617"], name)  # guest; local; a code page, guarded
            self.assertEqual(at_p(on), stream(1, 8), name)
            self.assertFalse([a for a in on["mem"] if 0x06000000 <= a < 0x06000100], name)  # nothing into code
            self.assertEqual(on["ret"], 5 + stream(2, 8)[7], name)  # the callee's bytes in the local, read back
            self.assertEqual(on["xr"], XR, name)
            self.assertEqual((off["cs"], off["xr"], off["ret"]), ([], None, 5 + 8), name)
            self.assertEqual(at_p(off), bytes([0x11, 0x22]) + bytes(6), name)

    def test_planted_mutants(self):
        good = self.build_run("good", self.readable(GOOD), RD_SOURCES)
        for name, subs in MUTANTS.items():
            m = self.build_run(name, self.readable(subs), RD_SOURCES)
            key = lambda r: (r["cs"], r["ret"], at_p(r), r["xr"])  # noqa: E731
            self.assertNotEqual(key(m["on"]), key(good["on"]), f"{name} survives the stricter judge")
            self.assertEqual(key(m["off"]), key(good["off"]), f"{name}: told apart without it (not a strict test)")


if __name__ == "__main__":
    unittest.main()
