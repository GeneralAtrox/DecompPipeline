"""The readable stage on the big-endian targets (Saturn SH-2, GameCube PowerPC), without Ghidra or a workspace:
- rd_be.h / rd_be.c: the two targets' copies agree, and (with Visual Studio) be_get/be_set store the guest's byte
  order and a plain use of a be_ type does not compile;
- core/readable_be: BE1 (headers, globals, prototypes), BE2 (pointers in readable C), BE3 (unmapped guest RAM);
- core/compare's per-target readable constants (stack end, RAM, FPU argument registers) from judge_memory, the
  Dreamcast's without it, and core/readable_abi's no-FPU placement;
- the GameCube's EABI placement, call-trap letters and entry thunk (ppc_readable_abi), and its sandbox context.md;
- each target's readable TASK formats, and the sandbox's do-nothing return statement.

usage: python tests/test_readable_be.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import readable, readable_abi, readable_be, readable_sandbox, toolcfg  # noqa: E402

SAT = TOOL / "targets" / "saturn_sh2"
GC = TOOL / "targets" / "gamecube_ppc"
sys.path.insert(0, str(GC))
import ppc_readable_abi as pabi  # noqa: E402
import ppc_readable_sandbox  # noqa: E402

TYPEDEFS = {"Vec3": "struct", "Actor": "struct", "Angle": "s16", "Count": "u32", "Name": "u8", "Kind": "enum"}


def reg_of(functions=None, globals_=None, typedefs=None):
    reg = readable.Registry.empty({"functions": functions or {}, "globals": globals_ or {}})
    reg.typedefs = dict(typedefs or TYPEDEFS)
    return reg


class Copies(unittest.TestCase):
    def test_rd_be_identical(self):
        for name in ("rd_be.h", "rd_be.c"):
            self.assertEqual((SAT / "runtime_rd" / name).read_bytes(), (GC / "runtime_rd" / name).read_bytes(), name)

    def test_rd_rt_includes_rd_be(self):
        for t in (SAT, GC):
            self.assertIn('#include "rd_be.h"', (t / "runtime_rd" / "rd_rt.h").read_text(encoding="utf-8"))
        sat = (SAT / "runtime_rd" / "rd_rt.h").read_text(encoding="utf-8")
        self.assertNotIn("sh4_fmac", sat)  # the SH-2 has no FPU: no float instruction helpers


class HeaderLint(unittest.TestCase):
    def test_members(self):
        h = {"types.h": """typedef struct Actor {
            u8 kind; u8 name[8]; be_u16 hp; be_s32 x; BE_PTR(Actor) next; be_fnptr update;
            u32 plain; Angle dir; f32 speed; Actor *link; void (*cb)(void); be_u32 flags : 3; Kind k;
            struct { be_u16 a; s16 b; } pos;
        } Actor;"""}
        errs = "\n".join(readable_be.header_lint(h, TYPEDEFS))
        for bad in ("Actor.plain is a plain u32", "Actor.dir is a plain Angle", "Actor.speed is a plain f32",
                    "Actor.link is a plain pointer", "plain function pointer", "a bit-field", "Actor.k is a plain Kind",
                    ".b is a plain s16"):
            self.assertIn(bad, errs)
        for good in ("Actor.kind", "Actor.name", "Actor.hp", "Actor.x", "Actor.next", "Actor.update", ".a is"):
            self.assertNotIn(good, errs)

    def test_globals_and_protos(self):
        errs = readable_be.header_lint({}, TYPEDEFS, ["be_u32 g_ok", "u32 g_bad", "Actor g_actor", "u8 g_buf[16]"],
                                       ["void F(Actor *a, be_u32 *p, u8 *s, u32 n)", "u32 *G(void)",
                                        "void H(u32 **pp, Count *c)"])
        text = "\n".join(errs)
        self.assertIn("global `u32 g_bad`", text)
        self.assertNotIn("g_ok", text)
        self.assertNotIn("g_actor", text)
        self.assertNotIn("g_buf", text)
        self.assertIn("G: its result `u32 *`", text)
        self.assertIn("H: pp `u32 **` points at a plain pointer", text)
        self.assertIn("H: c `Count *` points at a plain Count", text)
        self.assertFalse([e for e in errs if e.startswith("BE1: F:")])

    def test_source_lint(self):
        src = """#include "game.h"
        void F(Actor *a, u32 n) {
            u32 *p = (u32 *)a;          /* BE2 */
            f32 *q;                     /* BE2 */
            Actor **pp;                 /* BE2 */
            u8 *bytes = (u8 *)a;        /* fine: bytes */
            be_u32 *w = (be_u32 *)a;    /* fine */
            u32 area = n * n;           /* a product, not a pointer */
            /* u32 *in_a_comment; */
        }"""
        errs = "\n".join(readable_be.source_lint(src, TYPEDEFS))
        self.assertIn("`u32 *`", errs)
        self.assertIn("`f32 *`", errs)
        self.assertIn("`Actor * *` points at a plain pointer", errs)
        self.assertNotIn("u8", errs)
        self.assertNotIn("be_u32", errs)
        self.assertEqual(len(readable_be.source_lint(src, TYPEDEFS)), 3)

    def test_unmapped(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="rdbe_"))
        try:
            low = [(0x00200000, 0x00300000)]
            (d / "vectors.txt").write_text("V 0 dev\nM 060e0000 00\nE\n", encoding="utf-8")
            (d / "expected.txt").write_text("V 0\nW 06001000 00000001\nE\n", encoding="utf-8")
            self.assertEqual(readable_be.unmapped_lint(low, "wr32(0x06001000, 1);", d), [])
            self.assertIn("0x00201000", readable_be.unmapped_lint(low, "wr32(0x00201000, 1);", d)[0])
            (d / "expected.txt").write_text("V 0\nW 00280000 00000001\nE\n", encoding="utf-8")
            self.assertIn("0x00280000", readable_be.unmapped_lint(low, "", d)[0])
            self.assertEqual(readable_be.unmapped_lint([], "wr32(0x00201000, 1);", d), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Constants(unittest.TestCase):
    def run_in(self, target, code):
        d = pathlib.Path(tempfile.mkdtemp(prefix="rdjm_"))
        try:
            if target:
                (d / "project.json").write_text(json.dumps({"target": target}), encoding="utf-8")
            r = subprocess.run([sys.executable, "-c", code], cwd=TOOL, capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, DECOMP_PROJECT=str(d)))
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.split()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_judge_memory(self):
        code = ("import core.compare as c; print(hex(c.STACK_HI), hex(c.RAM[0]), hex(c.RAM[1]), c.FLOAT_REGS, "
                "len(c.UNMAPPED))")
        self.assertEqual(self.run_in("saturn_sh2", code), ["0x6100000", "0x6000000", "0x6100000", "False", "1"])
        self.assertEqual(self.run_in("dreamcast_sh4", code), ["0x8d000000", "0x8c000000", "0x8d000000", "True", "0"])

    def test_no_fpu_placement(self):
        reg = reg_of()
        proto = "void F(f32 x, u32 n, f32 y)"
        self.assertEqual(readable_abi.storages(reg, proto), ["fr4", "r4", "fr5"])
        with mock.patch.object(readable_abi, "FLOAT_REGS", False):
            self.assertEqual(readable_abi.storages(reg, proto), ["r4", "r5", "r6"])
            self.assertEqual(readable_abi.name_storage(reg, [("f32", "param_1"), ("u32", "param_2")]), ["r4", "r5"])


class PowerPC(unittest.TestCase):
    def test_place(self):
        self.assertEqual(pabi.place("ifid"), ["r3", "f1", "r4", "f2"])
        self.assertEqual(pabi.place("iL"), ["r3", "r5:r6"])
        self.assertEqual(pabi.place("iiiiiiiii"), ["r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "s0"])
        self.assertEqual(pabi.place("iiiiiiiL"), ["r3", "r4", "r5", "r6", "r7", "r8", "r9", "s0:s1"])
        with self.assertRaises(readable.RegistryError):
            pabi.place("d" * 9)

    def test_kinds(self):
        reg = reg_of()
        self.assertEqual(pabi.kinds(reg, "f64 F(f32 a, f64 b, Actor *p, u64 n, Angle t)"), ("fdiLh", "d"))
        self.assertEqual(pabi.kinds(reg, "void P(const char *fmt, ...)"), ("i*", "v"))
        with self.assertRaises(readable.RegistryError):
            pabi.kinds(reg, "void F(Vec3 v)")

    def test_thunk(self):
        reg = reg_of({"80001000": {"proto": "f64 Mix(Actor *a, f64 t, u64 key)"}})
        c = pabi.thunk_c(reg, "80001000", "f64 Mix(Actor *a, f64 t, u64 key)")
        self.assertIn("*out_f1 = (f64)Mix((Actor *)(uintptr_t)r[0], (f64)f[0], (u64)(((u64)r[2] << 32) | r[3]));", c)
        self.assertIn("rd_set_self((u32)(uintptr_t)&Mix, 0x80001000u);", c)
        self.assertIn('_Static_assert(sizeof(u64) == 8, "size of u64");', c)

    def test_context_md(self):
        meta = {"addr": "80001000", "signature": "s32 fn_80001000(f64 param_1, u32 param_2)", "ret": "s32",
                "params": [{"name": "param_1", "kind": "f64", "storage": "f1", "stack_offset": -1},
                           {"name": "param_2", "kind": "u32", "storage": "r3", "stack_offset": -1}],
                "callees": [{"addr": "80002000", "signature": "void fn_80002000(u32 param_1)", "regs": ["r3"]}],
                "globals": []}
        reg = reg_of({"80001000": {"proto": "s32 Snap(f64 step, Actor *a)"}})
        md = ppc_readable_sandbox.context_md(reg, "80001000", meta, types_body="")
        self.assertIn("- param_1 (f1) -> `f64 step`", md)
        self.assertIn("- param_2 (r3) -> `Actor * a`", md.replace("Actor *a", "Actor * a"))
        self.assertIn("tc_call(0x80002000) -> `void fn_80002000(u32 param_1)` (registers compared at the call: r3)", md)
        self.assertEqual(ppc_readable_sandbox.sandbox_decls(reg, "80001000", meta)[0],
                         {"80002000": "void fn_80002000(u32 param_1)", "80001000": meta["signature"]})


class Sandbox(unittest.TestCase):
    def test_tasks_format(self):
        for t in (SAT, GC):
            task = (t / "readable_task.md").read_text(encoding="utf-8").format(
                addr="06001000", name="F", proto="void F(void)", box="B", previous="")
            self.assertIn("be_get", task)
            self.assertIn("## Procedure", task)
            self.assertNotIn("FPSCR", task)

    def test_return_stub(self):
        self.assertEqual(readable_sandbox.return_stub("void F(u32 a)"), "")
        self.assertEqual(readable_sandbox.return_stub("f64 F(void)"), "    return 0;\n")
        self.assertEqual(readable_sandbox.return_stub("f32 F(void)"), "    return 0;\n")


def vcvars32():
    vc = toolcfg.load(TOOL).get("vcvars")
    return pathlib.Path(vc.replace("vcvars64.bat", "vcvars32.bat")) if vc and pathlib.Path(vc).is_file() else None


BE_TEST = r"""#include <stdio.h>
#include <string.h>
#include "rd_be.h"
typedef struct { unsigned char tag; unsigned char pad; be_u16 h; be_s32 s; be_f32 f; be_ptr p; be_u32 w[2];
                 be_f64 d; } Rec;
int main(void)
{
    static Rec r;
    const unsigned char *b = (const unsigned char *)&r;
    int i;
    be_set(r.h, 0x1234);
    be_set(r.s, -2);
    be_set(r.f, 1.0f);
    be_set(r.p, &r);
    be_set(r.d, 2.0);
    for (i = 0; i < (int)sizeof r; i++) printf("%02x", b[i]);
    printf(" %x %d %g %d %g %u\n", be_get(r.h), be_get(r.s), be_get(r.f), be_get(r.p) == (void *)&r, be_get(r.d),
           (unsigned)sizeof r);
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and vcvars32() and vcvars32().is_file(), "no Visual Studio tools here")
class BigEndianC(unittest.TestCase):
    def cl(self, d, src, out):
        (d / "t.c").write_text(src, encoding="utf-8")
        rt = SAT / "runtime_rd"
        return subprocess.run(f'cmd /d /s /c ""{str(vcvars32())}" >nul 2>&1 && cl /nologo /W4 /WX /std:c11 /TC '
                              f'/I"{rt}" /Fe{out} t.c "{rt / "rd_be.c"}""', cwd=d, capture_output=True, text=True,
                              errors="replace", timeout=300)

    def test_byte_order(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="rdbec_"))
        try:
            r = self.cl(d, BE_TEST, "t.exe")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            out = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60).stdout.split()
            raw = bytes.fromhex(out[0])
            self.assertEqual(raw[2:4], bytes.fromhex("1234"))
            self.assertEqual(raw[4:8], bytes.fromhex("fffffffe"))
            self.assertEqual(raw[8:12], bytes.fromhex("3f800000"))
            self.assertEqual(raw[24:32], bytes.fromhex("4000000000000000"))  # be_f64 8-aligned, as on the PowerPC
            self.assertEqual(out[1:], ["1234", "-2", "1", "1", "2", "32"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_hw_log_reset(self):  # Saturn rd_p4_reset empties the raw runtime's hardware-store log every vector
        d = pathlib.Path(tempfile.mkdtemp(prefix="rdbec_"))
        try:
            rt, raw = SAT / "runtime_rd", SAT / "runtime"
            (d / "t.c").write_text('#include <stdio.h>\nvoid rd_p4_reset(void);\nstatic int resets;\n'
                                   "void tcrt_p4_reset(void) { resets++; }\n"
                                   'int main(void) { rd_p4_reset(); rd_p4_reset(); printf("%d", resets); return 0; }\n',
                                   encoding="utf-8")
            r = subprocess.run(f'cmd /d /s /c ""{str(vcvars32())}" >nul 2>&1 && cl /nologo /W4 /WX /TC /I"{rt}" '
                               f'/I"{raw}" /Fet.exe t.c "{rt / "rd_p4.c"}""', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            out = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60).stdout
            self.assertEqual(out, "2")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_plain_use_refused(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="rdbec_"))
        try:
            r = self.cl(d, '#include "rd_be.h"\nint f(be_u32 *p) { return (int)(*p + 1); }\n'
                           "int main(void) { return 0; }\n", "u.exe")
            self.assertNotEqual(r.returncode, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
