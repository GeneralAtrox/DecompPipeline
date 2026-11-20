"""CodeWarrior's register save/restore helper entries, judged on their own (Skies of Arcadia 8025a22c..8025a318: 12
_savegpr_N, 12 _restgpr_N, 2 _savefpr_N, 2 _restfpr_N). They address the frame through r11, which TcOracle.setEntry
left 0, so every vector faulted (8025a278: lfd f25,-0x38(r11) read ffffffc8) and none was judged.

The rule, for a function whose own entry is a helper (TcFlow.isSaveHelper; the translator's helper_walk; hooks
save_helper), nothing else:
1. the oracle runs the whole chain to its blr (Ghidra splits the chains: the fall-through into the next entry is the
   helper, not a call) and enters it with a frame (TcVectors.frame, vectors.txt "X r11 r14..r31 f14..f31"): r11 at a
   save area just below the stack window (moved in 8-byte steps), the 0x200 bytes up to 0x817f0000 random (an M
   patch), r14..r31 and f14..f31 random;
2. both runners read X (tcrt_set_frame): the C gets r11 and the entry registers with tc_inr / tc_inf, and the exit
   frame reports the entry values unless the C reports others; the translator emits them for a helper entry only;
3. a save is judged by its W lines (the area is below the stack window's cut), a restore by the exit frame (XR:
   r14..r31, f14..f31; target.json exit_frame, enforced).
Planted wrong answers: a store of the wrong register, a store at the wrong offset, a restore into the wrong register
or from the wrong offset, for GPRs and FPRs. Every other function: no X line, no patch, no tc_inr, its entry
registers 0, its oracle stamp unchanged (target.json stamp_scopes "save_helper_frame").

Ghidra end to end (Stub, skipped unless DP_GC_GHIDRA_E2E=1 and config ghidra_home): a stub DOL with two callers, a
_savegpr/_restgpr chain and a _savefpr/_restfpr chain is set up and prepared; the helper entries get valid vectors with
X lines, the translator's C PASSes and the plants FAIL; the callers' vectors have no X line. DP_GC_GHIDRA_DIR: a
folder for the Ghidra project (Ghidra refuses a path with a part starting with a dot).

Run: python tests/test_gc_r11.py
"""
import hashlib
import json
import os
import pathlib
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
GC = TOOL / "targets" / "gamecube_ppc"
RT = GC / "runtime"
SCR = GC / "ghidra_scripts"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(GC / "translator"), str(GC)]

from test_fmac import vcvars  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402
from ppctranslate import translate as tr  # noqa: E402
from ppctranslate.translate import Facts, Signature, translate_function  # noqa: E402


def helper_walk(dol, t):
    return tr.helper_walk(dol, t)  # at call time: the module before this change has none (fails-before runs it all)

BASE = 0x80003100
BLR = 0x4E800020
STACK_LO, SP = 0x817F0000, 0x817FE000
TJ = json.loads((GC / "target.json").read_text(encoding="utf-8"))


def mem_op(op, rs, d, ra=11):
    return op << 26 | rs << 21 | ra << 16 | (d & 0xFFFF)


def bl(frm, to):
    return 0x48000001 | ((to - frm) & 0x03FFFFFC)


def stub():
    """(DOL bytes, entries): two callers, then the chains _savefpr_30.._31, _restfpr_30.._31, _savegpr_27.._31,
    _restgpr_27.._31 (CodeWarrior's offsets: rN at -4 * (32 - N), fN at -8 * (32 - N))."""
    sf = [mem_op(54, n, -8 * (32 - n)) for n in (30, 31)] + [BLR]
    rf = [mem_op(50, n, -8 * (32 - n)) for n in (30, 31)] + [BLR]
    sg = [mem_op(36, n, -4 * (32 - n)) for n in range(27, 32)] + [BLR]
    rg = [mem_op(32, n, -4 * (32 - n)) for n in range(27, 32)] + [BLR]
    c1_len, c2_len = 16, 10
    at = BASE + 4 * (c1_len + c2_len)
    e = {"sf": at, "rf": at + 4 * len(sf), "sg": at + 4 * (len(sf) + len(rf))}
    e["rg"] = e["sg"] + 4 * len(sg)
    c1 = BASE
    w = [0x9421FFA0, 0x7C0802A6, 0x90010064, 0x39610060]  # stwu r1,-0x60(r1); mflr r0; stw r0,0x64(r1); addi r11,r1,0x60
    w += [bl(c1 + 4 * len(w), e["sf"])]
    w += [bl(c1 + 4 * len(w), e["sg"])]  # _savegpr_27
    w += [0x7C7B1B78, 0x3BE00005, 0x3BC00007]  # mr r27,r3; li r31,5; li r30,7
    w += [0x39610060, bl(c1 + 4 * 10, e["rf"]), bl(c1 + 4 * 11, e["rg"])]  # addi r11,r1,0x60; _restfpr_30; _restgpr_27
    w += [0x80010064, 0x7C0803A6, 0x38210060, BLR]
    assert len(w) == c1_len
    c2 = BASE + 4 * c1_len
    v = [0x9421FFC0, 0x7C0802A6, 0x90010044, 0x39610040]
    v += [bl(c2 + 16, e["sg"] + 8), bl(c2 + 20, e["rg"] + 8)]  # _savegpr_29, _restgpr_29: Ghidra splits the chains
    v += [0x80010044, 0x7C0803A6, 0x38210040, BLR]
    assert len(v) == c2_len
    code = b"".join(struct.pack(">I", x) for x in w + v + sf + rf + sg + rg)
    hdr = bytearray(0x100)
    struct.pack_into(">I", hdr, 0x00, 0x100)
    struct.pack_into(">I", hdr, 0x48, BASE)
    struct.pack_into(">I", hdr, 0x90, len(code))
    struct.pack_into(">I", hdr, 0xE0, BASE)
    entries = {"save_fpr_30": e["sf"], "rest_fpr_30": e["rf"], "save_gpr_27": e["sg"], "save_gpr_29": e["sg"] + 8,
               "rest_gpr_27": e["rg"], "rest_gpr_29": e["rg"] + 8, "caller": c1, "caller2": c2}
    return bytes(hdr) + code, entries


def translate(image, entry):
    """The translator's C for a helper entry given a one-instruction body (as Ghidra splits the chains)."""
    sig = Signature(f"fn_{entry:08x}", f"void fn_{entry:08x}(void)", [], "void")
    facts = Facts.from_target(TJ, {})
    return translate_function(Dol(image), entry, sig, facts, [(entry, entry + 4)])[0]


def fields(word):
    """A D-form instruction's opcode, rS/rD and signed displacement."""
    return word >> 26, (word >> 21) & 31, word & 0xFFFF if word & 0x8000 == 0 else (word & 0xFFFF) - 0x10000


def chain(image, entry):
    """The words of the helper chain at entry up to its blr, read from the stub image itself (the model does not use
    the code under test): D-form stw/stfd/lwz/lfd on r11 only."""
    out, a = [], entry
    while True:
        w = struct.unpack(">I", image[0x100 + a - BASE:0x100 + a - BASE + 4])[0]
        if w == BLR:
            return out
        assert w >> 26 in (36, 54, 32, 50) and (w >> 16) & 31 == 11, hex(w)
        out.append(w)
        a += 4


def run_model(image, entry, fr, area_lo, area):
    """What the oracle computes for a helper entry: its chain on the frame; (memory after, gpr r14..r31, fpr bits)."""
    mem = bytearray(area)
    gpr, fpr = list(fr[1:19]), list(fr[19:37])
    for word in chain(image, entry):
        op, rs, d = fields(word)
        a = fr[0] + d - area_lo
        if op == 36:
            mem[a:a + 4] = struct.pack(">I", gpr[rs - 14])
        elif op == 54:
            mem[a:a + 8] = struct.pack(">Q", fpr[rs - 14])
        elif op == 32:
            gpr[rs - 14] = struct.unpack(">I", mem[a:a + 4])[0]
        elif op == 50:
            fpr[rs - 14] = struct.unpack(">Q", mem[a:a + 8])[0]
    return mem, gpr, fpr


def frame(rng):
    """TcVectors.frame's shape (its own generator, here Python's): r11, r14..r31, f14..f31, the area's bytes."""
    fr = [STACK_LO - 0x10 - 8 * rng.randrange(32)] + [rng.getrandbits(32) for _ in range(18)]
    for _ in range(18):
        b = rng.getrandbits(64)
        fr.append(b & ~(1 << 62) if (b >> 52) & 0x7FF == 0x7FF else b)
    return fr, bytes(rng.getrandbits(8) for _ in range(0x200))


def harness(image, entry, n=6, seed=11):
    """vectors.txt and expected.txt text in the oracle's form for a helper entry (J 8: exit_frame)."""
    rng = random.Random(seed * 1000 + entry)
    vo, eo = ["J 8"], []
    lo = STACK_LO - 0x200
    for vid in range(n):
        s = "dev" if vid < n // 2 else "hidden"
        fr, area = frame(rng)
        vo += [f"V {vid} {s}", "R" + " 00000000" * 8, "F" + " 0000000000000000" * 8, "C" + " 00000000" * 16,
               "D" + " 0000000000000000" * 16,
               "X " + " ".join(f"{x:08x}" for x in fr[:19]) + " " + " ".join(f"{x:016x}" for x in fr[19:]),
               f"M {lo:08x} {area.hex()}", "E"]
        mem, gpr, fpr = run_model(image, entry, fr, lo, area)
        eo += [f"V {vid} {s}", "RET 00000000 0000000000000000 00000000"]
        diff = [k for k in range(len(area)) if mem[k] != area[k] or k in written(image, entry, fr, lo)]
        for a, b in runs(sorted(set(diff))):
            eo.append(f"W {lo + a:08x} {bytes(mem[a:b]).hex()}")
        eo += ["XR " + f"{SP:08x} " + " ".join(f"{x:08x}" for x in gpr) + " " + " ".join(f"{x:016x}" for x in fpr),
               "COV 0", "E"]
    return "\n".join(vo) + "\n", "\n".join(eo) + "\n"


def written(image, entry, fr, lo):
    out = set()
    for word in chain(image, entry):
        op, _, d = fields(word)
        n = {36: 4, 54: 8}.get(op, 0)
        out |= {fr[0] + d - lo + k for k in range(n)}
    return out


def runs(xs):
    out = []
    for x in xs:
        if out and out[-1][1] == x:
            out[-1][1] = x + 1
        else:
            out.append([x, x + 1])
    return [tuple(r) for r in out]


class Wiring(unittest.TestCase):
    def test_oracle(self):
        orc = (SCR / "TcOracle.java").read_text(encoding="utf-8")
        vec = (SCR / "TcVectors.java").read_text(encoding="utf-8")
        self.assertIn("selfHelper = TcFlow.isSaveHelper(listing, fn.getEntryPoint());", orc)
        self.assertIn("if (selfHelper) walks.add(fn.getEntryPoint());", orc)
        self.assertIn("long[] fr = frameOf.get(v);\n        if (fr != null) {", orc)  # setEntry: only with a frame
        self.assertIn('emu.writeRegister("r11", u(fr[0]));', orc)
        self.assertEqual(vec.count("frame(v, "), 1)
        self.assertIn("if (selfHelper) frame(v, seed, tried[0] + tried[1]);", vec)  # the only caller
        self.assertIn("vout.append('X');", vec)
        self.assertEqual(vec.count("frameOf.put("), 2)  # frame() and the repair's carry-over
        area = int(re.search(r"FRAME_AREA = (0x[0-9a-f]+);", vec).group(1), 16)
        lowest = STACK_LO - 0x10 - 8 * 31
        self.assertIn("fr[0] = STACK_LO - 0x10 - 8L * h.nextInt(32);", vec)
        self.assertGreaterEqual(lowest - 0x90, STACK_LO - area)  # f14's slot of the lowest r11 is in the area
        self.assertLessEqual(STACK_LO - 0x10 + 8, STACK_LO)       # the highest slot (+4, lwz r0) too
        self.assertTrue(TJ.get("exit_frame"))  # restores are judged by XR

    def test_stamp_scope(self):
        sc = next(s for s in TJ["stamp_scopes"] if s["scope"] == "save_helper_frame")
        for name, (_, after) in sc["scripts"].items():
            self.assertEqual(hashlib.sha256((SCR / name).read_bytes()).hexdigest(), after, name)
        hooks = (GC / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('if scope == "save_helper_frame":\n        return save_helper(cfg, addr)', hooks)

    def test_runtimes(self):
        h = (RT / "tc_rt.h").read_text(encoding="utf-8")
        self.assertIn("u32 tc_inr(u32 k);", h)
        self.assertIn("f64 tc_inf(u32 k);", h)
        for f in (RT / "runner.c", GC / "runtime_rd" / "rd_runner.c"):
            self.assertIn("} else if (line[0] == 'X') {\n            frame_line(line + 1);", f.read_text(encoding="utf-8"))
        rd = (GC / "runtime_rd" / "rd_mem.c").read_text(encoding="utf-8")
        self.assertIn("u32 tc_inr(u32 k)", rd)
        self.assertIn("f64 tc_inf(u32 k)", rd)


class Translator(unittest.TestCase):
    def test_helper_entries(self):
        img, e = stub()
        sg = translate(img, e["save_gpr_29"])
        self.assertIn("wr32(tc_inr(0xbu) - 0xcu, tc_inr(0x1du));", sg)  # stw r29,-0xc(r11)
        self.assertIn("wr32(tc_inr(0xbu) - 4u, tc_inr(0x1fu));", sg)    # the chain runs on to r31: one-insn body
        rg = translate(img, e["rest_gpr_27"])
        self.assertIn("tc_xr(27u, rd32(tc_inr(0xbu) - 0x14u));", rg)
        self.assertIn("tc_xr(31u, rd32(tc_inr(0xbu) - 4u));", rg)
        self.assertIn("wrf64(tc_inr(0xbu) - 0x10u, tc_inf(0x1eu));", translate(img, e["save_fpr_30"]))
        self.assertIn("tc_xrf(31u, rdf64(tc_inr(0xbu) - 8u));", translate(img, e["rest_fpr_30"]))

    def test_other_functions(self):  # no frame: their C is as before
        img, e = stub()
        self.assertIsNone(helper_walk(Dol(img), e["caller"]))
        sig = Signature.generic_for(e["caller"])
        src = translate_function(Dol(img), e["caller"], sig, Facts.from_target(TJ, {}),
                                 [(e["caller"], e["caller"] + 64)])[0]
        self.assertNotIn("tc_inr", src)
        self.assertNotIn("tc_inf", src)


class Hook(unittest.TestCase):
    def test_save_helper(self):
        import importlib.util
        from test_sync import TMP  # noqa: F401  (a project for core.paths)
        spec = importlib.util.spec_from_file_location("gc_hooks", GC / "hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        from unittest import mock
        from core import paths
        img, e = stub()
        with tempfile.TemporaryDirectory(dir=work()) as d:
            p = pathlib.Path(d, "stub.dol")
            p.write_bytes(img)
            cfg = {"binary_path": p}
            bodies = pathlib.Path(d, "function_bodies.txt")  # the callers' bodies: hooks.late_calls reads them
            bodies.write_text(f"{e['caller']:08x} {e['caller']:x} {e['caller'] + 64:x}\n"
                              f"{e['caller2']:08x} {e['caller2']:x} {e['caller2'] + 40:x}\n", encoding="utf-8")
            stack = mock.patch.object(paths, "FUNCTION_BODIES", bodies)
            stack.start()
            self.addCleanup(stack.stop)
            for k in ("save_fpr_30", "rest_fpr_30", "save_gpr_27", "save_gpr_29", "rest_gpr_27", "rest_gpr_29"):
                self.assertTrue(hooks.save_helper(cfg, f"{e[k]:08x}"), k)
                self.assertTrue(hooks.stamp_scope(cfg, f"{e[k]:08x}", "save_helper_frame"), k)
            for k in ("caller", "caller2"):
                self.assertFalse(hooks.stamp_scope(cfg, f"{e[k]:08x}", "save_helper_frame"), k)
            self.assertFalse(hooks.save_helper(cfg, f"{e['save_gpr_27'] + 20:08x}"))  # the blr


def work():
    w = TOOL / "tests" / ".work"
    w.mkdir(parents=True, exist_ok=True)
    return w


def build_runner(d, src, entry):
    import ppc_check
    meta = {"name": f"fn_{entry:08x}", "signature": f"void fn_{entry:08x}(void)", "params": [], "ret": "void"}
    pathlib.Path(d, "work.c").write_text(src, encoding="utf-8")
    pathlib.Path(d, "thunk.c").write_text(ppc_check.gen_thunk(meta), encoding="utf-8")
    vc = str(vcvars(64)).replace("/", "\\")
    rt = " ".join(f'"{RT / n}"' for n in ("runner.c", "tc_rt.c", "tc_ppc.c"))
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 /fp:strict /W4 /WX /TC /I"{RT}" '
                       f'work.c thunk.c {rt} /Fe:runner.exe"', cwd=d, capture_output=True, text=True,
                       errors="replace", timeout=300)
    return r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]


def judge(src, entry, image):
    """The diffs of every vector of a modelled harness for the C src: [] is a PASS."""
    import ppc_compare as cmp
    with tempfile.TemporaryDirectory(prefix="gcr11_", dir=work()) as d:
        ok, log = build_runner(d, src, entry)
        if not ok:
            return [f"build: {log}"]
        vt, et = harness(image, entry)
        pathlib.Path(d, "stub.dol").write_bytes(image)
        pathlib.Path(d, "code.txt").write_text(f"{BASE:x} {BASE + len(image) - 0x100:x}\n", encoding="utf-8")
        for n, t in (("vectors.txt", vt), ("expected.txt", et)):
            pathlib.Path(d, n).write_text(t, encoding="utf-8")
        r = subprocess.run([str(pathlib.Path(d, "runner.exe")), "stub.dol", "code.txt", "vectors.txt", "all", "out.txt"],
                           cwd=d, capture_output=True, text=True, timeout=60)
        if r.returncode:
            return [f"runner {r.returncode}: {r.stderr}"]
        vecs = cmp.parse_vectors(pathlib.Path(d, "vectors.txt"))
        exp = cmp.parse_results(pathlib.Path(d, "expected.txt"))
        act = cmp.parse_results(pathlib.Path(d, "out.txt"))
        meta = {"ret": "void", "params": [], "callees": []}
        secs = cmp.dol_sections(image)
        out = []
        for i in vecs:
            self_frame = vecs[i].get("frame")
            assert self_frame and len(self_frame) == 37
            out += cmp.compare_vector(meta, secs, vecs[i], exp[i], act.get(i),
                                      {"call_snapshots": "enforce", "exit_frame": "enforce"}, [])
        return out


# (entry, the translated text a plant changes, the planted text): wrong register, wrong offset, GPR and FPR
PLANTS = [
    ("save_gpr_27", "- 0xcu, tc_inr(0x1du)", "- 0xcu, tc_inr(0x1cu)"),      # stw r28 where r29 belongs
    ("save_gpr_27", "- 0xcu, tc_inr(0x1du)", "- 0x10u, tc_inr(0x1du)"),     # r29 one slot off
    ("rest_gpr_27", "tc_xr(29u, ", "tc_xr(28u, "),                          # lwz into r28 for r29
    ("rest_gpr_27", "tc_xr(29u, rd32(tc_inr(0xbu) - 0xcu))", "tc_xr(29u, rd32(tc_inr(0xbu) - 8u))"),
    ("save_fpr_30", "tc_inf(0x1eu)", "tc_inf(0x1fu)"),                       # stfd f31 where f30 belongs
    ("rest_fpr_30", "tc_xrf(30u, rdf64(tc_inr(0xbu) - 0x10u))", "tc_xrf(30u, rdf64(tc_inr(0xbu) - 0x18u))"),
]


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class Judge(unittest.TestCase):
    def test_translated_helpers_pass(self):
        img, e = stub()
        for k in ("save_gpr_27", "save_gpr_29", "rest_gpr_27", "rest_gpr_29", "save_fpr_30", "rest_fpr_30"):
            self.assertEqual(judge(translate(img, e[k]), e[k], img), [], k)

    def test_plants_rejected(self):
        img, e = stub()
        for k, old, new in PLANTS:
            src = translate(img, e[k])
            self.assertIn(old, src, k)
            d = judge(src.replace(old, new, 1), e[k], img)
            self.assertTrue(d, f"{k}: {new} passed")
            self.assertTrue(all(x.startswith(("memory 0x", "r", "f")) for x in d), d)

    def test_no_frame_is_as_before(self):
        """A vector without X: the entry registers are 0 and the exit frame reports 0, as before this change."""
        import ppc_compare as cmp
        img, e = stub()
        src = "#include \"tc_rt.h\"\nvoid fn_x(void)\n{\n    tc_xr(14u, tc_inr(11u) + tc_inr(20u) + (u32)tc_inf(31u));\n}\n"
        with tempfile.TemporaryDirectory(prefix="gcr11_", dir=work()) as d:
            meta_src = src.replace("fn_x", f"fn_{BASE:08x}")
            ok, log = build_runner(d, meta_src, BASE)
            self.assertTrue(ok, log)
            pathlib.Path(d, "stub.dol").write_bytes(img)
            pathlib.Path(d, "code.txt").write_text("80003100 80003104\n", encoding="utf-8")
            pathlib.Path(d, "vectors.txt").write_text("J 8\nV 0 dev\nR" + " 00000001" * 8 + "\nE\n", encoding="utf-8")
            subprocess.run([str(pathlib.Path(d, "runner.exe")), "stub.dol", "code.txt", "vectors.txt", "all", "out.txt"],
                           cwd=d, timeout=60, check=True)
            act = cmp.parse_results(pathlib.Path(d, "out.txt"))[0]
        self.assertEqual(act["xr"][1:], [0] * 36)


def _cfg_local():
    try:
        return json.loads((TOOL / "config.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@unittest.skipUnless(os.environ.get("DP_GC_GHIDRA_E2E") == "1" and _cfg_local().get("ghidra_home") and vcvars(64),
                     "Ghidra end to end: set DP_GC_GHIDRA_E2E=1 (config.local.json ghidra_home, MSVC)")
class Stub(unittest.TestCase):
    def dp(self, ws, *args, timeout=1200):
        r = subprocess.run([sys.executable, str(TOOL / "decomp.py"), "--project", str(ws), *args], cwd=str(TOOL),
                           capture_output=True, text=True, errors="replace", timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def test_prepare_and_judge(self):
        img, e = stub()
        ws = pathlib.Path(tempfile.mkdtemp(prefix="gcr11ws_", dir=work()))
        gdir = pathlib.Path(os.environ.get("DP_GC_GHIDRA_DIR") or ws) / ws.name
        try:
            (ws / "stub.dol").write_bytes(img)
            (ws / "project.json").write_text(json.dumps({
                "name": "r11 stub", "target": "gamecube_ppc", "ghidra_name": "r11stub", "binary": "stub.dol",
                "binary_sha256": hashlib.sha256(img).hexdigest(),
                "game_ranges": [[f"{BASE:08x}", f"{BASE + len(img) - 0x100:08x}"]], "global_ranges": [],
                "fixed_regs": {"r2": "80350000", "r13": "8034e720"}, "port": 8899, "references": [],
                "ghidra_dir": str(gdir).replace("\\", "/")}), encoding="utf-8")
            (ws / "oracle").mkdir()  # as tools/game_setup.py does: the target's template inputs
            shutil.copyfile(TOOL / "targets" / "gamecube_ppc" / "template" / "TcInputs.java",
                            ws / "oracle" / "TcInputs.java")
            rc, log = self.dp(ws, "setup")
            self.assertEqual(rc, 0, log[-3000:])
            helpers = ("save_gpr_27", "save_gpr_29", "rest_gpr_27", "rest_gpr_29", "save_fpr_30", "rest_fpr_30")
            for k in helpers + ("caller", "caller2"):
                rc, log = self.dp(ws, "prepare", f"{e[k]:08x}")
                self.assertEqual(rc, 0, f"{k}: {log[-3000:]}")
                h = ws / "harness" / f"{e[k]:08x}"
                summ = json.loads((h / "oracle_summary.json").read_text(encoding="utf-8"))
                vt = (h / "vectors.txt").read_text(encoding="utf-8")
                if k.startswith("caller"):
                    self.assertNotRegex(vt, r"(?m)^X ", k)
                    continue
                self.assertGreater(summ["dev_valid"], 0, f"{k}: {summ}")
                self.assertGreater(summ["hidden_valid"], 0, f"{k}: {summ}")
                self.assertEqual(len(re.findall(r"(?m)^X ", vt)), summ["dev_valid"] + summ["hidden_valid"], k)
                self.assertRegex((h / "expected.txt").read_text(encoding="utf-8"), r"(?m)^XR ", k)
                src = translate(img, e[k])
                verdicts = {"translated": self.check(ws, e[k], src)}
                for pk, old, new in PLANTS:
                    if pk == k:
                        verdicts[new] = self.check(ws, e[k], src.replace(old, new, 1))
                print(f"{k} {e[k]:08x}: dev {summ['dev_valid']} hidden {summ['hidden_valid']} {verdicts}")
                self.assertEqual(verdicts.pop("translated"), "PASS", k)
                for new, v in verdicts.items():
                    self.assertEqual(v, "FAIL", f"{k}: plant {new}")
        finally:
            if os.environ.get("DP_GC_KEEP_WS"):  # kept for a comparison after (the caller's printed path)
                print(f"workspace kept: {ws}")
            else:
                shutil.rmtree(ws, ignore_errors=True)
                if gdir != ws / ws.name:
                    shutil.rmtree(gdir, ignore_errors=True)

    def check(self, ws, entry, src):
        f = ws / f"try_{entry:08x}.c"
        f.write_text(src, encoding="utf-8")
        rc, log = self.dp(ws, "check", f"{entry:08x}", "--work", str(f), "--json", timeout=600)
        m = re.search(r'"verdict":\s*"(\w+)"', log)
        return m.group(1) if m else f"no verdict: {log[-800:]}"


if __name__ == "__main__":
    unittest.main()
