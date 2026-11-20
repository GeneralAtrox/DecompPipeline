"""Two faults DP's judge found in Too Human (2026-11-14), on hand-made code:

- A jump table's default arm (the tail call through CTR, emit.py: it runs only when no case does) was run after the cases'
  live sets in fn_parts.uncarried, so the registers its mocked call clobbers dropped out of what the case arms read. In
  82cc6bc8 a case arm's r6 was read as 0 (`r5 = r5;` for `add r5,r5,r6`).
- lfs / lfsx / lfsu / lfsux widened with C's (f64), x86 cvtss2sd, which quiets a signalling NaN (7fa00001 came back
  7fe00001 in 82e40fe0, ffbfffff came back ffffffff in 82e41578). Xenon copies the fraction and quiets nothing; stfs narrows
  by bits (ppc_single_bits), so a single copied through an FPR keeps its bits.
- A tail branch kept the volatile registers its callee's meta "keeps" lists, which are a bl's (TcPrepare.keeps); the
  oracle's mock zeroes them all after a tail branch (TcOracle.clobbered), so the exit-state form of 82e72728 (b 82dbf070)
  differed on r4..r8 and f2..f12 in every vector.

    python -m unittest tests.test_carry
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
X360 = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from xenontranslate import emit, workspace  # noqa: E402
from xenontranslate.translate import Signature, TranslateError, translate_function  # noqa: E402

BASE = 0x82000000
# address: (words, what it does)
CODE = {
    0x82000000: ("28030001 4D810020 3D808200 398C0030 5460103A 7C0C002E 7C0903A6 4E800420 386B0001 4E800020 38600002 4E800020",
                 "cmplwi r3,1; bgtlr; lis r12,0x8200; addi r12,r12,0x30; slwi r0,r3,2; lwzx r0,r12,r0; mtctr r0; bctr; "
                 "case 0: addi r3,r11,1; blr; case 1: li r3,2; blr: a case arm reads r11, which the default's call clobbers"),
    0x82000100: ("3C808200 C0240180 D0240184 7C43242E D0440188 4E800020",
                 "lis r4,0x8200; lfs f1,0x180(r4); stfs f1,0x184(r4); lfsx f2,r3,r4; stfs f2,0x188(r4); blr"),
    0x82000120: ("C4640004 D0630010 7C832C6E D0830014 4E800020",
                 "lfsu f3,4(r3); stfs f3,0x10(r3); lfsux f4,r3,r5; stfs f4,0x14(r3); blr"),
    0x82000140: ("38800005 38C00007 48000038", "li r4,5; li r6,7; b 0x82000180: a tail branch"),
    0x82000160: ("38800005 38C00007 48000019 4E800020", "li r4,5; li r6,7; bl 0x82000180; blr"),
    0x82000180: ("38600000 4E800020", "li r3,0; blr: the callee, which leaves r4 and r6 alone"),
}
DATA = {0x82000030: "82000020 82000028"}   # the jump table, outside the function's body


class Carry(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="x360_carry_"))
        img = bytearray(0x200)
        for a, words in [(a, w) for a, (w, _) in CODE.items()] + list(DATA.items()):
            b = bytes.fromhex(words.replace(" ", ""))
            img[a - BASE:a - BASE + len(b)] = b
        (self.d / "image.bin").write_bytes(bytes(img))
        (self.d / "project.json").write_text(json.dumps({"target": "xbox360_ppc", "binary": "image.bin"}), encoding="utf-8")
        (self.d / "image.json").write_text(json.dumps({"load_address": BASE, "sections": [
            {"start": BASE, "end": BASE + 0x200, "code": True}]}), encoding="utf-8")
        size = {0x82000000: 0x30}   # the table words are data after the body
        (self.d / "catalog_inputs.json").write_text(json.dumps({"helper_ranges": [], "functions": [
            {"addr": f"{a:08x}", "size": size.get(a, len(w.replace(" ", "")) // 2), "kind": "function"}
            for a, (w, _) in CODE.items()]}), encoding="utf-8")
        (self.d / "state").mkdir()
        self.w = workspace.Workspace(self.d, target=X360 / "target.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def tx(self, a):
        return translate_function(self.w.dol, a, Signature.generic_for(a), self.w.facts, self.w.bodies[a])

    def test_switch_default_keeps_case_reads(self):
        """The default arm is a path of its own: its call's clobber of r11 does not hide the case arm's read of r11."""
        with self.assertRaisesRegex(TranslateError, "reads r11 before setting"):   # the judge's form: refused, not read as 0
            self.tx(0x82000000)
        self.w.facts.port = True
        src, ft = self.tx(0x82000000)
        self.assertIn("r11", ft.read_first)
        self.assertIn("tc_ldg(0xbu)", src)   # the port form loads the caller's r11 from the thread's register file

    def test_lfs_widens_by_bits(self):
        """Every lfs form widens through px_lfs; no single reaches a double through C's (f64)."""
        for form in (False, True):
            self.w.facts.port = form
            src = self.tx(0x82000100)[0] + self.tx(0x82000120)[0]
            self.assertEqual(src.count("px_lfs(rdf32("), 4, src)
            self.assertNotIn("(f64)rdf32(", src)
            self.assertNotIn("(f64)px_w2f(", src)
            self.assertIn("wrf32d(", src)   # stfs: ppc_single_bits, by bits

    def test_lfs_runtime(self):
        """px_lfs then stfs's ppc_single_bits gives every single back bit for bit, the signalling NaNs too; C's (f64) (the
        planted wrong answer) quiets them. Built with the port's flags (/O1 /fp:strict)."""
        from test_vmx import msvc
        cl, env = msvc()
        rt = X360 / "runtime"
        drv = "\n".join(["#include <stdio.h>", "#include <string.h>", '#include "tc_rt.h"', emit.HELPERS["px_b2f"],
                         emit.HELPERS["px_lfs"], r"""
static f32 w2f(u32 w) { f32 f; memcpy(&f, &w, 4); return f; }
int main(void)
{
    static volatile u32 w[] = {0x7fa00001u, 0xffbfffffu, 0x7f800001u, 0xff81dfa3u, 0x7fc00000u, 0x7f800000u, 0xff800000u,
                               0x3f800000u, 0x00000001u, 0x80000000u, 0x00800000u, 0x7f7fffffu};
    int i;
    for (i = 0; i < (int)(sizeof w / sizeof w[0]); i++)
        printf("%08x %08x %08x\n", w[i], ppc_single_bits(px_lfs(w2f(w[i]))), ppc_single_bits((f64)w2f(w[i])));
    return 0;
}
"""])
        with tempfile.TemporaryDirectory() as t:
            pathlib.Path(t, "drv.c").write_text(drv, encoding="utf-8")
            r = subprocess.run([cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", "/wd4505", f"/I{rt}", "drv.c",
                                str(rt / "tc_rt.c"), str(rt / "tc_ppc.c"),
                                *[str(p) for p in (rt / "tc_exit.c",) if p.exists()],   # the judge line's tc_rt.c reads tcx_on
                                f"/Fe{t}\\drv.exe", f"/Fo{t}\\"],
                               capture_output=True, text=True, timeout=300, env=env, cwd=t)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
            run = subprocess.run([os.path.join(t, "drv.exe")], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        rows = [ln.split() for ln in run.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(rows), 12, run.stdout)
        self.assertEqual([lfs for _, lfs, _ in rows], [word for word, _, _ in rows])
        plain = {word: p for word, _, p in rows}
        self.assertEqual((plain["7fa00001"], plain["ffbfffff"]), ("7fe00001", "ffffffff"))   # the planted wrong answer is caught

    def test_tail_branch_keeps_nothing(self):
        """The judge's exit-state form: a bl's callee leaves r4 and r6 alone (meta keeps); after a tail branch to the same
        callee they are the mock's 0, as in the oracle. The port form keeps its own keeps."""
        callee = 0x82000180
        self.w.facts.exit_state = True

        def tx(a, port=False):
            self.w.facts.port = port
            sig = Signature(f"fn_{a:08x}", f"void fn_{a:08x}(void)", [], "void", {callee: []}, keeps={callee: {"r4", "r6"}})
            return translate_function(self.w.dol, a, sig, self.w.facts, self.w.bodies[a])[0]
        tail, bl = tx(0x82000140), tx(0x82000160)
        for r in (4, 6):
            self.assertIn(f"tcx_r({r}u, 0ull);", tail)
            self.assertIn(f"tcx_r({r}u, {5 if r == 4 else 7}ull);", bl)
        self.w.facts.exit_state = False
        port = tx(0x82000140, port=True)
        self.assertNotIn("tcx_", port)


if __name__ == "__main__":
    unittest.main()
