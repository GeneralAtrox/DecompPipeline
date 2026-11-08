"""Xbox 360: the readable runtime's side of the stricter judge (runtime_rd/rd_veh.c includes tc_strict.h, as
runtime/tc_rt.c does). The test builds the real readable runner (x360_readable's sources, 64-bit, the judge's C flags
and link options) with one prototype, a trapped call to 82020000 with three pointers, and a function that stores into
a guest pointer and a host local and passes both and a pointer into a code page. With every flag on (J 15) it checks:
  - the call snapshots: the guest bytes, the host local's bytes, and the code page's bytes through its guard;
  - the mock writes: into the guest pointer (W lines), into the host local (seen in the result), none into code;
  - the exit frame: the entry r1 and 36 zero doublewords (r14..r31, f14..f31);
  - with no strict lines at all, none of it, and the C's own writes and result as before.
Synthetic data. Skipped without a 64-bit Visual Studio environment.
usage: python tests/test_x360_rd_strict.py
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

X = TOOL / "targets" / "xbox360_ppc"
RD, RT = X / "runtime_rd", X / "runtime"
SOURCES = [RD / n for n in ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_be.c")] + [RT / "tc_ppc.c"]
FLAGS = "/fp:strict /arch:SSE2 /std:c11 /W4 /WX /TC"  # core/readable_check.C_FLAGS
LINK = "/link /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /FIXED:NO /BASE:0x5a000000 /IGNORE:4281"  # link_flags
WORK = r"""
#include "rd_rt.h"

typedef u64 (*Fn)(u8 *a, u8 *b, u8 *code);
u64 fn_test(u8 *p);
u64 fn_test(u8 *p)
{
    u8 local[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    u64 rv;
    p[0] = 0x11;
    p[1] = 0x22;
    rv = ((Fn)(uintptr_t)0x82020000u)(p, local, (u8 *)(uintptr_t)0x82000010u);
    return rv + local[7];
}

void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1)
{
    (void)f;
    (void)out_f1;
    *out_r3 = fn_test((u8 *)(uintptr_t)(u32)r[0]);
}
"""
PROTOS = ('#include "rd_internal.h"\nconst RdProto rd_protos[] = {\n    {0x82020000u, "PPP", \'L\', ""},\n};\n'
          "const int rd_nprotos = 1;\n")
P = 0x40001000
STRICT = "J 15\nU 82020000 1 8\nU 82020000 2 8\nU 82020000 3 8\n"
VECTOR = "V 0 dev\n%sR %x 0 0 0 0 0 0 0\nC 5\nE\n"


def results(out):
    """{"ret": r3, "mem": {addr: byte} of the W lines, "cs": [CS lines], "xr": XR line or None}."""
    r = {"mem": {}, "cs": [], "xr": None}
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
    return r


@unittest.skipUnless(vcvars(64), "no 64-bit vcvars (MSVC) on this machine")
class ReadableStrict(unittest.TestCase):
    def test_trapped_call(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="x360rdstrict_", dir=work) as d:
            p = pathlib.Path(d)
            (p / "work.c").write_text(WORK, encoding="utf-8")
            (p / "protos.c").write_text(PROTOS, encoding="utf-8")
            (p / "image.bin").write_bytes(bytes(range(256)))
            (p / "ranges.txt").write_text("82000000 82000100\n", encoding="utf-8")
            (p / "on.txt").write_text(STRICT + VECTOR % ("N 1234 7\nW 0 1 8\nW 0 2 8\nW 0 3 8\n", P), encoding="utf-8")
            (p / "off.txt").write_text(VECTOR % ("", P), encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{s}"' for s in SOURCES)
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 {FLAGS} /I"{RD}" /I"{RT}" '
                               f'/Ferd_runner.exe work.c protos.c {src} {LINK}"', cwd=d, capture_output=True,
                               text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            got = {}
            for k in ("on", "off"):
                run = subprocess.run([str(p / "rd_runner.exe"), "image.bin", "ranges.txt", f"{k}.txt", "all",
                                      f"{k}.out"], cwd=d, capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)
                got[k] = results((p / f"{k}.out").read_text(encoding="utf-8"))
        on, off = got["on"], got["off"]
        self.assertEqual(on["cs"], ["CS 0 1 1122000000000000", "CS 0 2 0102030405060708",
                                    "CS 0 3 1011121314151617"])  # guest; host local; a code page, through its guard
        self.assertEqual(bytes(on["mem"].get(P + k, 0) for k in range(8)), stream(1, 8))
        self.assertFalse([a for a in on["mem"] if a >= 0x82000000], on["mem"])  # nothing into code
        self.assertEqual(on["ret"], 5 + stream(2, 8)[7])  # the callee's bytes in the local, read back after the call
        self.assertEqual(on["xr"], "XR 700fe000" + (" " + "0" * 16) * 36)
        self.assertEqual((off["cs"], off["xr"], off["ret"]), ([], None, 5 + 8))
        self.assertEqual(off["mem"], {P: 0x11, P + 1: 0x22})


if __name__ == "__main__":
    unittest.main()
