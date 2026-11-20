"""GameCube: a float parameter in an integer register. Ghidra types r3..r10 float when the code only stores the
register into a float field (Skies of Arcadia 801c4f24: r6 goes to four stw). The register holds the float's 32 bits,
but the translator set it with a value conversion, r6 = (u32)param_7, and the judge's thunk refused the function
(ValueError "float parameter param_7 in r6"), so six Skies functions and seven of Pikmin's had no verdict.
Now the translator takes the bits (px_s2w) and the thunk passes the float whose bits the vector's register holds
(tc_w2s); an f64 in an integer register is still refused on both sides. The test checks:
  - the translation of stw r3,0(r4); blr with an f32 param_1 in r3 stores px_s2w(param_1), never (u32)param_1;
  - the thunk of that prototype calls with tc_w2s(r[0]); a thunk without such a parameter is unchanged;
  - built with MSVC, every bit pattern, a signalling NaN among them, comes through thunk and function unchanged.
The MSVC part is skipped without config.local.json's vcvars.
usage: python tests/test_gc_fparam.py
"""
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
PPC = TOOL / "targets" / "gamecube_ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(PPC), str(PPC / "translator")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import ppc_check  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402
from ppctranslate.translate import Facts, Signature, TranslateError, translate_function  # noqa: E402
from test_fmac import vcvars  # noqa: E402

META = {"name": "fn_80003100", "signature": "void fn_80003100(f32 param_1, u32 param_2)", "ret": "void",
        "params": [{"name": "param_1", "kind": "f32", "storage": "r3", "stack_offset": -1},
                   {"name": "param_2", "kind": "u32", "storage": "r4", "stack_offset": -1}]}
PLAIN = {"name": "fn_80003100", "signature": "void fn_80003100(u32 param_1, f32 param_2)", "ret": "void",
         "params": [{"name": "param_1", "kind": "u32", "storage": "r3", "stack_offset": -1},
                    {"name": "param_2", "kind": "f32", "storage": "f1", "stack_offset": -1}]}
BITS = (0x00000000, 0x80000000, 0x3f800000, 0x40490fdb, 0x7fa00001, 0xffc12345, 0x00000001, 0x4b800000)
DRIVER = r"""
#include <stdio.h>
#include "tc_rt.h"
u32 got;
void wr32(u32 a, u32 v) { (void)a; got = v; }
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1);
int main(void)
{
    static const u32 bits[] = {%s};
    u32 r[8] = {0}, r3 = 0, r4 = 0;
    f64 f[8] = {0}, f1 = 0;
    for (int i = 0; i < (int)(sizeof bits / sizeof bits[0]); i++) {
        r[0] = bits[i];
        tc_thunk(r, f, &r3, &r4, &f1);
        printf("%%08x\n", got);
    }
    return 0;
}
"""


def dol():  # stw r3,0(r4); blr, in a DOL with one text section
    code = bytes.fromhex("906400004E800020")
    head = bytearray(0x100)
    struct.pack_into(">I", head, 0, 0x100)
    struct.pack_into(">I", head, 0x48, 0x80003100)
    struct.pack_into(">I", head, 0x90, len(code))
    return Dol(bytes(head) + code)


def translate(meta):
    return translate_function(dol(), 0x80003100, Signature.from_meta(meta), Facts(), [(0x80003100, 0x80003108)])[0]


class FloatInGpr(unittest.TestCase):
    def test_translator_takes_the_bits(self):
        src = translate(META)
        self.assertIn("static u32 px_s2w(f32 f)", src)
        self.assertIn("wr32(param_2, px_s2w(param_1));", src)
        self.assertNotIn("(u32)param_1", src)
        self.assertEqual(ppc_check.lint(src, META), [])

    def test_f64_in_gpr_refused(self):
        m = {**META, "params": [{**META["params"][0], "kind": "f64"}, META["params"][1]]}
        with self.assertRaises(TranslateError):
            translate(m)
        with self.assertRaises(ValueError):
            ppc_check.gen_thunk(m)

    def test_thunk(self):
        t = ppc_check.gen_thunk(META)
        self.assertIn("fn_80003100(tc_w2s(r[0]), r[1]);", t)
        self.assertEqual(t.count("static f32 tc_w2s(u32 w)"), 1)
        self.assertNotIn("tc_w2s", ppc_check.gen_thunk(PLAIN))  # an unused static would fail /W4 /WX
        self.assertIn("fn_80003100(r[0], (f32)f[0]);", ppc_check.gen_thunk(PLAIN))

    @unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
    def test_bits_survive_the_call(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcfparam_", dir=work) as d:
            pathlib.Path(d, "work.c").write_text(translate(META), encoding="utf-8")
            pathlib.Path(d, "thunk.c").write_text(ppc_check.gen_thunk(META), encoding="utf-8")
            pathlib.Path(d, "drv.c").write_text(DRIVER % ", ".join(f"0x{b:08x}u" for b in BITS), encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 /fp:strict /W4 /WX /TC '
                               f'/I"{PPC / "runtime"}" drv.c work.c thunk.c"',
                               cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            out = subprocess.run([str(pathlib.Path(d, "drv.exe"))], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split(), [f"{b:08x}" for b in BITS])


if __name__ == "__main__":
    unittest.main()
