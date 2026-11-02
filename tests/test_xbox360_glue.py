"""The Xbox 360 target's judge glue and C runtime, without Ghidra (split from test_xbox360_ppc.py, which has the
shared synthetic image):
- ppcscan.py: instruction classes, the save/restore helpers by encoding, the catalog's entries and refusals;
- the judge glue: thunks for every parameter and return kind, the fma switch, the 64-bit compare;
- the C runtime (MSVC): the runner on a synthetic image (regions, faults, R8, 64-bit records, the per-vector reset).

usage: python tests/test_xbox360_glue.py
"""
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import (BASE, IDATA, TEXT, THUNK, TOOL, X360, bl, synthetic_image, synthetic_xex,  # noqa: E402
                              tmpdir)
from core import toolcfg  # noqa: E402

import ppcscan  # noqa: E402
import x360_agent  # noqa: E402
import x360_check  # noqa: E402
import x360_compare as cmp  # noqa: E402
import xex  # noqa: E402


class Scan(unittest.TestCase):
    def test_classes(self):
        cases = {
            0x100B61CB: ("vmx",),        # stvx128
            0x7DCB61CE: ("vmx",),        # stvx
            0x7C0802A6: (),              # mflr r0 (mfspr LR)
            0x7D8043A6: ("vmx",),        # mtspr VRSAVE,r12 (spr 256)
            0x7C6C42E6: ("sys",),        # mftb
            0xF8610010: ("ld64",),       # std r3,16(r1)
            0xFC22007A: ("fpu", "fma"),  # fmadd
            0xEC220830: ("fpu", "estimate"),  # fres
            0xFC00048E: ("fpu", "fp_mode"),   # mffs
            0xFC20081E: ("fpu", "fconv"),     # fctiwz
            0x7C601828: ("atomic",),     # lwarx
            0x7C0018AC: ("cache",),      # dcbf
            0x7C001A2C: (),              # dcbt (a hint)
            0x7C0004AC: ("barrier",),    # sync
            0x0CC30000: ("trap",),       # twi
            0x44000002: ("sys",),        # sc
            0x38600001: (),              # li r3,1
        }
        for word, want in cases.items():
            self.assertEqual(ppcscan.classify(word), want, f"{word:08x}")
        self.assertEqual(ppcscan.branch_target(bl(0x82000010, 0x82000000), 0x82000010), (0x82000000, True))
        self.assertTrue(ppcscan.uses_r13(0x816D0000))  # lwz r11,0(r13)

    def test_catalog(self):
        img = synthetic_image()
        x = xex.Xex(synthetic_xex(img))
        d = xex.describe(x, img)
        cat = ppcscan.scan(xex.resolve_imports(img, d), d)
        self.assertEqual(cat["helpers"]["savegprlr_14"], TEXT + 0x100)
        self.assertEqual(cat["helpers"]["savegprlr_31"], TEXT + 0x100 + 4 * 17)
        self.assertEqual(cat["helper_ranges"], [["savegprlr", TEXT + 0x100, TEXT + 0x150]])
        f = {x["addr"]: x for x in cat["functions"]}
        self.assertEqual(sorted(f), [f"{a:08x}" for a in (TEXT, TEXT + 0x100, TEXT + 0x200, TEXT + 0x300,
                                                          TEXT + 0x400)])
        main = f[f"{TEXT:08x}"]
        self.assertEqual((main["source"], main["callees"], main["imports"], main["frame_helpers"]),
                         ("pdata", [f"{TEXT + 0x200:08x}"], [f"{THUNK:08x}"], 1))
        self.assertEqual(main["counts"], {"fpu": 1, "fma": 1, "ld64": 1})
        self.assertIsNone(main["refuse"])
        self.assertEqual((f[f"{TEXT + 0x200:08x}"]["source"], f[f"{TEXT + 0x200:08x}"]["size"]), ("bl", 8))
        self.assertEqual(f[f"{TEXT + 0x400:08x}"]["source"], "pointer")
        self.assertIsNone(f[f"{TEXT + 0x300:08x}"]["refuse"])  # vector code is judged now (TcVmx), not refused
        self.assertEqual(f[f"{TEXT + 0x100:08x}"]["kind"], "frame_helper")
        self.assertEqual(cat["stats"]["phase1"], 4)

    def test_proof_shape(self):
        import proof_pick
        call = lambda src, dst: struct.pack(">II", 0x7D8802A6, bl(src + 4, dst))  # noqa: E731
        self.assertEqual(proof_pick.shape(call(0x82000000, 0x82100000)), proof_pick.shape(call(0x82300000, 0x82000000)))
        self.assertNotEqual(proof_pick.shape(struct.pack(">I", 0x38600001)), proof_pick.shape(struct.pack(">I", 0x38600002)))
        self.assertEqual(sum(n for _, n, _ in proof_pick.GROUPS), 50)


# ---------- the judge glue ----------

class Glue(unittest.TestCase):
    def test_thunk(self):
        meta = {"name": "fn_82000000", "signature": "s32 fn_82000000(u32 a, s32 b, u64 c, f32 x, f64 y, u32 s)",
                "ret": "s32", "params": [
                    {"name": "a", "storage": "r3", "kind": "u32"}, {"name": "b", "storage": "r4", "kind": "s32"},
                    {"name": "c", "storage": "r10", "kind": "u64"}, {"name": "x", "storage": "f1", "kind": "f32"},
                    {"name": "y", "storage": "f13", "kind": "f64"},
                    {"name": "s", "storage": "stack", "kind": "u32", "stack_offset": 0x50}]}
        t = x360_check.gen_thunk(meta)
        self.assertIn("*out_r3 = (u64)(s64)fn_82000000((u32)r[0], (s32)(u32)r[1], r[7], (f32)f[0], f[12], "
                      "(u32)rd64(0x700fe050u));", t)
        for ret, want in (("u32", "(u64)fn"), ("u64", "(u64)fn"), ("f32", "*out_f1 = (f64)fn"), ("void", "    fn")):
            self.assertIn(want, x360_check.gen_thunk(dict(meta, ret=ret)))
        with self.assertRaises(ValueError):
            x360_check.gen_thunk(dict(meta, params=[{"name": "q", "storage": "f14", "kind": "f64"}]))
        self.assertEqual(x360_check.fma_define({}), "/DTC_FMA_FUSED=0")
        self.assertEqual(x360_check.fma_define({"fma": "fused"}), "/DTC_FMA_FUSED=1")
        with self.assertRaises(ValueError):
            x360_check.fma_define({"fma": "maybe"})

    def test_compare(self):
        meta = {"ret": "u32", "params": [], "callees": [{"addr": "82000300", "regs": ["r3", "f13", "s0"]}]}
        vec = {"patches": [(0x40000000, b"\x01\x02\x03\x04")]}
        call = ["82000300", "ffffffff00000001"] + ["0"] * 7 + ["0"] * 12 + ["3ff0000000000000"] + ["5"] + ["0"] * 7
        exp_txt = ("V 1 dev\nRET 00000000ffffffff 0\nW 40000000 0102aabb\nW 700fd000 ff\nCALL " + " ".join(call)
                   + "\nE\n")
        d = tmpdir("x360cmp_")
        try:
            (d / "e.txt").write_text(exp_txt, encoding="utf-8")
            exp = cmp.parse_results(d / "e.txt")[1]
            act_txt = exp_txt.replace("RET 00000000ffffffff", "RET ffffffffffffffff").replace("W 700fd000 ff", "")
            (d / "a.txt").write_text(act_txt, encoding="utf-8")
            act = cmp.parse_results(d / "a.txt")[1]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(len(exp["calls"][0]), cmp.CALL_FIELDS)
        diffs = cmp.compare_vector(meta, b"", vec, exp, act)
        self.assertEqual(len(diffs), 1)  # the stack write is not compared
        self.assertIn("only the upper word differs: u32 zero-extends", diffs[0])
        act["calls"][0][cmp.REG_INDEX["f13"]] = 0
        act["calls"][0][cmp.REG_INDEX["s0"]] = 6
        act["r3"] = exp["r3"]
        diffs = cmp.compare_vector(meta, b"", vec, exp, act)
        self.assertEqual([x.split(":")[1].split(" expected")[0].strip() for x in diffs],
                         ["argument f13 (args.f[12])", "argument s0 (stack doubleword at r1+0x50 at the call, args.s[0])"])
        self.assertEqual(cmp.Memory(b"\x07" * 8, {"patches": []}).initial(BASE + 3), 7)

    def test_target_json(self):
        t = json.loads((X360 / "target.json").read_text(encoding="utf-8"))
        self.assertEqual(t["fma"], "fused")  # the user's decision, 2026-10-24 (DESIGN.md, open question 1)
        self.assertIn(t["fma"], x360_check.FMA_MODES)
        self.assertEqual(int(t["abi"]["entry_sp"], 16), cmp.SP)
        self.assertEqual(int(t["judge_memory"]["stack_lo"], 16), cmp.STACK[0])
        self.assertEqual(t["abi"]["stack_args"]["offset"], cmp.STACK_ARGS)
        for name, (lo, hi) in (("heap", cmp.HEAP), ("stack", cmp.STACK), ("kernel", cmp.KERNEL)):
            self.assertEqual([int(x, 16) for x in t["memory"][name]], [lo, hi], name)
        rt = (X360 / "runtime" / "tc_rt_internal.h").read_text(encoding="utf-8")
        for const, v in (("TCRT_SP", cmp.SP), ("TCRT_HEAP_BASE", cmp.HEAP[0]), ("TCRT_STACK_BASE", cmp.STACK[0]),
                         ("TCRT_KERNEL_BASE", cmp.KERNEL[0]), ("TCRT_IMAGE_BASE", cmp.IMAGE_BASE)):
            self.assertIn(f"#define {const} 0x{v:08x}u", rt)
        self.assertEqual(xex.KERNEL_VARS, int(t["memory"]["kernel_vars"][0], 16))

    def test_agent_text(self):
        task = x360_agent.TASK.format(addr="82000000", box="X", signature="u64 fn_82000000(void)",
                                      ret_reg="r3", entry_sp=cmp.SP, previous="")
        self.assertIn("0x700fe000", task)
        self.assertIn("all 64 bits", task)


# ---------- the C runtime ----------

def vcvars64():
    vc = toolcfg.load(TOOL).get("vcvars")
    return pathlib.Path(vc) if vc and pathlib.Path(vc).is_file() else None


def cl(d, args, timeout=300):
    vc = str(vcvars64()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 /fp:strict /W4 /WX /TC {args}"', cwd=d,
                       capture_output=True, text=True, errors="replace", timeout=timeout)
    return r.returncode, r.stdout


WORK_C = r"""#include "tc_rt.h"
u64 fn_82010000(u64 a, u32 b, f64 x)
{
    TcArgs args = {0};
    wr32(b, 0x11223344u);
    wr64(0x700fd000u, a);
    wr8(0x7fff0010u, (u8)rd32(0x82000100u));
    wrf32d(b + 8u, x);
    args.r[0] = a;
    args.r[1] = rd64(0x82000100u);
    args.f[12] = x;
    args.s[0] = 0x0102030405060708ull;
    (void)tc_call(rd32(0x82000108u), &args);
    if (b == 0x40000010u) {
        (void)rd8(0x82010004u);
        (void)rd32(0x30000000u);
    }
    return a + tc_call(0x82010300u, &args);
}
"""
META = {"name": "fn_82010000", "signature": "u64 fn_82010000(u64 a, u32 b, f64 x)", "ret": "u64",
        "params": [{"name": "a", "storage": "r3", "kind": "u64"}, {"name": "b", "storage": "r4", "kind": "u32"},
                   {"name": "x", "storage": "f1", "kind": "f64"}]}
ZEROS = " ".join(["0"] * 12)
VECTORS = f"""V 1 dev
R ffffffff00000001 0000000040000010 0 0 0 0 0 0
F 3ff8000000000000 {ZEROS}
C 0 5
M 82000100 1122334455667788aabbccdd
E
V 2 dev
R 2 00000000400ffffe 0 0 0 0 0 0
F 0 {ZEROS}
E
"""


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Runtime(unittest.TestCase):
    def test_runner(self):
        d = tmpdir("x360rt_")
        try:
            (d / "work.c").write_text(WORK_C, encoding="utf-8")
            (d / "thunk.c").write_text(x360_check.gen_thunk(META), encoding="utf-8")
            rt = X360 / "runtime"
            srcs = " ".join(f'"{rt / s}"' for s in x360_check.SOURCES)
            rc, out = cl(d, f'/DTC_FMA_FUSED=0 /I"{rt}" /Ferunner.exe work.c thunk.c {srcs}')
            self.assertEqual(rc, 0, out[-3000:])
            image = bytearray(0x10400)
            image[0x100:0x10C] = bytes.fromhex("0102030405060708" "82010200")
            (d / "image.bin").write_bytes(bytes(image))
            (d / "code.txt").write_text("82010000 82010010\n", encoding="utf-8")
            (d / "vectors.txt").write_text(VECTORS, encoding="utf-8")
            run = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all", "out.txt"],
                                 cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            got = (d / "out.txt").read_text(encoding="utf-8")
            res = cmp.parse_results(d / "out.txt")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        v1 = got.split("V ")[1].splitlines()
        self.assertEqual(v1[1], "RET ffffffff00000006 0000000000000000")  # a + 5, all 64 bits
        self.assertEqual([ln for ln in v1 if ln.startswith("W ")],  # no stack write; 1.5 stored as a single
                         ["W 40000010 11223344", "W 40000018 3fc00000", "W 7fff0010 44"])
        calls = [ln.split() for ln in v1 if ln.startswith("CALL ")]
        self.assertEqual(len(calls[0]), 1 + cmp.CALL_FIELDS)
        self.assertEqual(calls[0][1:4], ["aabbccdd", "ffffffff00000001", "1122334455667788"])  # patched target
        self.assertEqual(calls[0][1 + cmp.REG_INDEX["f13"]], "3ff8000000000000")
        self.assertEqual(calls[0][1 + cmp.REG_INDEX["s0"]], "0102030405060708")
        self.assertEqual(calls[1][1], "82010300")
        self.assertIn("FAULT read 30000000", v1)
        self.assertIn("CODEREAD 82010004", v1)
        v2 = got.split("V ")[2].splitlines()
        self.assertIn("FAULT write 400ffffe", v2)  # a word across the end of the heap
        self.assertIn("FAULT write 40100006", v2)
        self.assertIn("W 7fff0010 04", v2)  # the image is restored after vector 1's patch
        self.assertEqual(res[2]["calls"][0][0], 0x82010200)
        self.assertEqual(res[2]["r3"], 2)
        self.assertNotIn("CODEREAD", got.split("V ")[2])


class ScopedStamp(unittest.TestCase):
    """A scoped oracle change (target.json "stamp_scopes") re-stamps only the functions its scope reaches."""

    def test_scope(self):
        import hashlib
        from unittest import mock
        from core import oracle_stamp, paths, target_hooks
        import x360_scope
        d = tmpdir("x360scope_")
        try:
            # 0x82000000: fmuls f1,f1,f2 (opcode 59); 0x82000004: fmul f1,f1,f2 (63, a double); 0x82000008: frsp f1,f1
            (d / "image.bin").write_bytes(bytes.fromhex("ec2100b2" "fc2100b2" "fc200818"))
            (d / "bodies.txt").write_text("82000000 82000000 82000004\n82000004 82000004 82000008\n"
                                          "82000008 82000008 8200000c\n", encoding="utf-8")
            cfg = {"binary_path": d / "image.bin", "load_address": "0x82000000", "binary_sha256": "ab"}
            # (the FPSCR.SZ entry modes are an SH-4 fact: another target's project gets none)
            with mock.patch.object(paths, "FUNCTION_BODIES", d / "bodies.txt"), \
                    mock.patch.object(paths, "PROJECT", {"target": "xbox360_ppc"}):
                self.assertEqual([x360_scope.in_scope(cfg, a, "single_fp") for a in ("82000000", "82000004", "82000008")],
                                 [True, False, True])
                self.assertTrue(x360_scope.in_scope(cfg, "8200000c", "single_fp"))  # no body: in scope
                self.assertTrue(x360_scope.in_scope(cfg, "82000004", "another"))  # unknown scope: in scope
                (d / "TcFixups.java").write_text("old", encoding="utf-8")
                old = {"TcFixups.java": d / "TcFixups.java"}
                before = {a: oracle_stamp.stamp_of(cfg, a, old, "-", None, 48, 48) for a in ("82000000", "82000004")}
                (d / "TcFixups.java").write_text("new!", encoding="utf-8")
                (d / "TcSingle.java").write_text("added", encoding="utf-8")  # a file the change adds
                new = dict(old, **{"TcSingle.java": d / "TcSingle.java"})
                sha = {t: hashlib.sha256(t.encode()).hexdigest() for t in ("old", "new!", "added")}
                sc = dict(cfg, stamp_scopes=[{"scope": "single_fp", "scripts": {
                    "TcFixups.java": [sha["old"], sha["new!"]], "TcSingle.java": [None, sha["added"]]}}])
                with mock.patch.object(target_hooks, "get", return_value=x360_scope.in_scope):
                    now = {a: oracle_stamp.stamp_of(sc, a, new, "-", None, 48, 48) for a in before}
                    self.assertNotEqual(now["82000000"], before["82000000"])  # fmuls: re-prepared
                    self.assertEqual(now["82000004"], before["82000004"])  # fmul only: kept
                    (d / "TcFixups.java").write_text("newer!", encoding="utf-8")  # a later edit: every stamp moves
                    self.assertNotEqual(oracle_stamp.stamp_of(sc, "82000004", new, "-", None, 48, 48), before["82000004"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Setup(unittest.TestCase):
    """x360setup.py without Ghidra: one command from a default.xex to a workspace (the hand steps of the proof)."""

    def test_setup(self):
        import contextlib
        import io
        import x360setup
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))  # setup's summary lines
        d = tmpdir("x360setup_")
        try:
            img = synthetic_image()
            src = d / "default.xex"
            src.write_bytes(synthetic_xex(img, encrypt=False))
            (d / "roots.txt").write_text(f"{TEXT:08x}  # main\n\n", encoding="utf-8")
            ws = d / "ws"
            cfg = x360setup.setup(src, ws, "Too Human!", roots_file=d / "roots.txt")
            for n in ("image.bin", "image.json", "catalog_inputs.json", "code_ranges.txt", "ghidra_setup.txt",
                      "setup_roots.json", "project.json", "oracle/TcInputs.java"):
                self.assertTrue((ws / n).is_file(), n)
            self.assertEqual((ws / "image.bin").read_bytes()[TEXT - BASE:IDATA - BASE], img[TEXT - BASE:IDATA - BASE])
            self.assertEqual(json.loads((ws / "project.json").read_text(encoding="utf-8")), cfg)
            self.assertEqual((cfg["ghidra_name"], cfg["binary"], cfg["priority"], cfg["ghidra_setup_timeout_s"]),
                             ("TooHuman", "image.bin", [f"{TEXT:08x}"], 1800))
            self.assertEqual(cfg["game_ranges"], [[f"{TEXT:08x}", f"{TEXT + 0x10000:08x}"]])
            self.assertEqual(cfg["global_ranges"], [["82020000", "82030000"]])
            self.assertEqual(cfg["fixed_regs"], {"r13": "0x7fff0000"})
            self.assertTrue(8700 <= cfg["port"] < 8800)
            listed = (ws / "ghidra_setup.txt").read_text(encoding="utf-8")
            self.assertIn(f"{TEXT:08x}", listed)  # the root and the leaf it calls
            self.assertIn(f"{TEXT + 0x200:08x}", listed)
            self.assertNotIn(f"{TEXT + 0x300:08x}", listed)  # not reached from the root
            with self.assertRaises(SystemExit):  # never overwrites a workspace
                x360setup.setup(src, ws, "Too Human!", roots_file=d / "roots.txt")
            (d / "bad.json").write_text(json.dumps({"functions": [{"addr": "82010004"}]}), encoding="utf-8")
            with self.assertRaises(SystemExit):  # a root the catalog doesn't have
                x360setup.setup(src, d / "ws2", roots_file=d / "bad.json")
            whole = x360setup.setup(src, d / "ws3")  # no roots: the whole catalog
            self.assertNotIn("priority", whole)
            self.assertEqual(whole["ghidra_setup_timeout_s"], 7200)
            self.assertIn(f"{TEXT + 0x300:08x}", (d / "ws3" / "ghidra_setup.txt").read_text(encoding="utf-8"))

            # gate G2: a disagreement counts only in a function the target prepares
            word = lambda a: struct.unpack_from(">I", img, a - BASE)[0]  # noqa: E731
            (d / "audit.tsv").write_text(
                f"{TEXT:08x}\t{word(TEXT):08x}\tmfspr\n"          # agrees
                f"{TEXT + 0x300:08x}\t{word(TEXT + 0x300):08x}\ttcvmx:stvx128\n"  # a vector word TcVmx reads: agrees
                f"{TEXT + 4:08x}\t{word(TEXT + 4):08x}\t??\n"      # in a prepared function: fails G2
                f"{THUNK + 4:08x}\t{word(THUNK + 4):08x}\t??\n"    # an import record (the loader's): expected
                f"{THUNK + 8:08x}\t{word(THUNK + 8):08x}\t??\n"    # the thunk's code: fails G2
                "# a comment line\n", encoding="utf-8")
            words, bad, expected, patched = x360setup.g2(d / "audit.tsv", ws, BASE)
            self.assertEqual((words, expected, patched, [b[:8] for b in bad]),
                             (5, 0, 1, [f"{TEXT + 4:08x}", f"{THUNK + 8:08x}"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_roots_and_runs(self):
        import x360setup
        d = tmpdir("x360roots_")
        try:
            (d / "a.json").write_text(json.dumps({"functions": [{"addr": "8201ABCD"}, "82010000"]}), encoding="utf-8")
            (d / "b.txt").write_text("8201ABCD # a\n# none\n\n82010000\n", encoding="utf-8")
            for n in ("a.json", "b.txt"):
                self.assertEqual(x360setup.read_roots(d / n), ["8201abcd", "82010000"], n)
            # the decode audit's functions: every one the setup made by default (no arguments), the roots on request
            self.assertEqual(x360setup.audit_args(d, "all"), [])
            self.assertEqual(x360setup.audit_args(d, "roots"), [])  # no setup_roots.json: the whole catalog
            (d / "setup_roots.json").write_text(json.dumps({"functions": ["8201abcd", "82010000"]}), encoding="utf-8")
            self.assertEqual(x360setup.audit_args(d, "all"), [])
            self.assertEqual(x360setup.audit_args(d, "roots"), ["8201abcd", "82010000"])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        sec = lambda lo, hi, code: {"start": lo, "end": hi, "code": code}  # noqa: E731
        self.assertEqual(x360setup.code_runs([sec(0x3000, 0x4000, True), sec(0x1000, 0x2000, True),
                                              sec(0x2000, 0x3000, True), sec(0x5000, 0x6000, False),
                                              sec(0x7000, 0x8000, True)]),
                         [["00001000", "00004000"], ["00007000", "00008000"]])


class KitStart(unittest.TestCase):
    def test_no_pad_read_before_the_window(self):
        """The base decides once, at the first read, whether host devices can be read: a read before the window opens leaves a played run with no keyboard or pads."""
        src = (X360 / "port" / "runtime" / "xb_main.c").read_text(encoding="utf-8")
        self.assertNotIn("xb_pads_poll", src[src.index("int port_main("):])


if __name__ == "__main__":
    unittest.main()
