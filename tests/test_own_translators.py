"""The user's own translators, part of DP (targets/<target>/translator/, origin in ORIGIN.md there):
1. each translates a small function written here (no game, no Ghidra) into C that DP's lint accepts; Saturn's through
   DP's adapter (targets/saturn_sh2/translate.py), as the translator stage runs it;
2. the translator stage finds the translator inside DP when config.local.json "translators" names no other folder,
   and a folder named there still wins;
3. regress: every function of a real workspace translates to the same C, byte for byte, as the recorded baseline
   (translator/regress/*.json; GameCube's was recorded by ppc-translate itself, Saturn's from sh2-translate 0b28828
   and Dreamcast's (every 6th Toy Commander function) from sh4-translate 5a51194, before the import; Xbox 360's,
   Too Human's 500 translator-set functions in the generic signature, from xenon-translate 66b6b4a). Translation
   only: no judge, no compiler, no Ghidra. Skipped without this machine's config.local.json "translator_regress":
   {"saturn_sh2": "<Grandia ws-field>", "gamecube_ppc": "<Skies decomp-gekko>", "dreamcast_sh4": "<Toy Commander
   decomp>", "xbox360_ppc": "<Too Human decomp>"}.
"""
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import paths, toolcfg, translate_stage  # noqa: E402
from core.lint import lint  # noqa: E402

SH2 = TOOL / "targets" / "saturn_sh2"
SH4 = TOOL / "targets" / "dreamcast_sh4"
PPC = TOOL / "targets" / "gamecube_ppc"
X360 = TOOL / "targets" / "xbox360_ppc"
WORKSPACES = toolcfg.load(TOOL).get("translator_regress") or {}

# translates the functions of a regress list with one translator, in its own process; prints {addr: c_sha256}
RUNNER = r'''
import hashlib, json, pathlib, sys
kind, repo, ws, target_json, cache, addrs_file = sys.argv[1:7]
sys.path.insert(0, repo)
ws = pathlib.Path(ws)
addrs = sorted(json.loads(pathlib.Path(addrs_file).read_text(encoding="utf-8")))
out = {}
if kind == "sh2":
    from sh2translate.translate import Image, Signature, TargetFacts, TranslateError, translate_function
    facts = TargetFacts.from_target_json(json.loads(pathlib.Path(target_json).read_text(encoding="utf-8")))
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    img = Image((ws / proj["binary"]).read_bytes(), facts.image_base)
    entries = {int(ln.split()[0], 16) for ln in (ws / "state" / "function_bodies.txt").read_text().splitlines()
               if ln.strip()}
elif kind == "sh4":  # as its tools/dp_judge.py: the meta as the judge reads it, the catalog ranges, the wait loops
    from sh4translate.translate import Image, Signature, TargetFacts, TranslateError, spin_ranges, translate_function
    sys.path.insert(1, str(pathlib.Path(target_json).resolve().parents[2]))
    from core import ret_reg
    facts = TargetFacts.from_target_json(json.loads(pathlib.Path(target_json).read_text(encoding="utf-8")))
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    img = Image((ws / proj["binary"]).read_bytes(), facts.image_base, facts.little)
    entries, ranges = set(), {}
    for ln in (ws / "state" / "function_bodies.txt").read_text().splitlines():
        if len(ln.split()) == 3:
            e, lo, hi = (int(x, 16) for x in ln.split())
            entries.add(e)
            ranges.setdefault(e, []).append((lo, hi))
    fr0 = ret_reg.listed(ws / "state")
    spins = spin_ranges(proj)
elif kind == "x360":  # every signature generic (no harness meta): the C depends on the translator and executable only
    from xenontranslate import returns, workspace
    from xenontranslate.translate import Signature, TranslateError, translate_function
    w = workspace.Workspace(ws, target=target_json)
    w.meta = lambda addr: None
    kinds = returns.analyse(w, pathlib.Path(cache))
else:
    from ppctranslate import returns, workspace
    from ppctranslate.translate import Signature, TranslateError, translate_function
    w = workspace.Workspace(ws, target=target_json)
    kinds = returns.analyse(w, pathlib.Path(cache))
for a in addrs:
    if kind != "x360":
        if not (ws / "harness" / a / "meta.json").is_file():
            continue
        meta = json.loads((ws / "harness" / a / "meta.json").read_text(encoding="utf-8"))
    try:
        if kind == "x360":
            ad = int(a, 16)
            src, _ = translate_function(w.dol, ad, Signature.generic_for(ad), w.facts, w.bodies[ad], returns=kinds)
            out[a] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        elif kind == "sh2":
            body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks") or []]
            src = translate_function(img, int(a, 16), Signature.from_meta(meta), facts, body or None, entries=entries)
            out[a] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        elif kind == "sh4":
            ad, meta = int(a, 16), ret_reg.judged(meta, fr0, a)
            body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks") or []]
            src = translate_function(img, ad, Signature.from_meta(meta), facts, body or None, entries=entries,
                                     ranges=ranges.get(ad), spin=spins.get(ad))
            out[a] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        else:
            ad = int(a, 16)
            src, _ = translate_function(w.dol, ad, Signature.from_meta(meta), w.facts, w.bodies[ad], returns=kinds)
            out[a] = hashlib.sha256(src.replace("\n", "\r\n").encode("utf-8")).hexdigest()[:16]  # its tools' files
    except TranslateError as e:
        out[a] = "no translation: " + str(e)
print(json.dumps(out, sort_keys=True))
'''


def tmpdir(prefix):
    root = TOOL / "tests" / ".work"
    root.mkdir(exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))


class Small(unittest.TestCase):
    def test_saturn_through_the_adapter(self):  # mov #5,r0; rts; nop
        d = tmpdir("own_sh2_")
        try:
            (d / "bin").write_bytes(bytes.fromhex("E005000B00090009"))
            meta = {"name": "fn_06000000", "signature": "u32 fn_06000000(void)", "params": [], "ret": "u32",
                    "ret_reg": "r0", "blocks": [["06000000", "06000006"]]}
            (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (d / "bodies.txt").write_text("06000000 06000000 06000006\n", encoding="utf-8")
            cmd = [sys.executable, str(SH2 / "translate.py"), "--repo", str(SH2 / "translator"), "--binary",
                   str(d / "bin"), "--addr", "06000000", "--meta", str(d / "meta.json"), "--target-json",
                   str(SH2 / "target.json"), "--bodies", str(d / "bodies.txt"), "--out", str(d / "out.c")]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            src = (d / "out.c").read_text(encoding="utf-8")
            self.assertIn("r0 = 0x5u;", src)
            self.assertIn("return (u32)r0;", src)
            self.assertEqual(lint(src, meta), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_saturn_push_of_a_copy_of_r15(self):
        """mov r15,r14; mov.l r14,@-r15; mov.l @r15+,r0; rts; nop: the store writes r14 (the old r15), not r15 after
        its decrement (the copy pass once read `wr32(r15, r14)` as `wr32(r15, r15)`: Dragon Force 2 0601dbd0)."""
        p = subprocess.run([sys.executable, "-c", (
            "import json, sys; sys.path.insert(0, sys.argv[1]); "
            "from sh2translate.translate import Image, Signature, TargetFacts, translate_function; "
            "f = TargetFacts.from_target_json(json.load(open(sys.argv[2], encoding='utf-8'))); "
            "m = {'name': 'fn_06000000', 'signature': 'u32 fn_06000000(void)', 'params': [], 'ret': 'u32', "
            "'ret_reg': 'r0'}; "
            "print(translate_function(Image(bytes.fromhex('6EF32FE660F6000B0009'), f.image_base), 0x06000000, "
            "Signature.from_meta(m), f, [(0x06000000, 0x0600000a)], entries={0x06000000}))"),
            str(SH2 / "translator"), str(SH2 / "target.json")], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("wr32(r15, r15)", p.stdout)
        self.assertIn("wr32(r15, r14);", p.stdout)

    def saturn_table(self, code, hi):
        """The C of fn(u32 param_1) at 06000000 with body [06000000, 06000000 + hi)."""
        p = subprocess.run([sys.executable, "-c", (
            "import json, sys; sys.path.insert(0, sys.argv[1]); "
            "from sh2translate.translate import Image, Signature, TargetFacts, translate_function; "
            "f = TargetFacts.from_target_json(json.load(open(sys.argv[2], encoding='utf-8'))); "
            "m = {'name': 'fn_06000000', 'signature': 'u32 fn_06000000(u32 param_1)', 'ret': 'u32', 'ret_reg': 'r0', "
            "'params': [{'name': 'param_1', 'kind': 'u32', 'storage': 'r4', 'stack_offset': -1}]}; "
            "print(translate_function(Image(bytes.fromhex(sys.argv[3]), f.image_base), 0x06000000, "
            "Signature.from_meta(m), f, [(0x06000000, 0x06000000 + int(sys.argv[4]))], entries={0x06000000}))"),
            str(SH2 / "translator"), str(SH2 / "target.json"), code, str(hi)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_saturn_masked_byte_table(self):
        """mov r4,r0; and #3,r0; mov r0,r1; mova tbl,r0; mov.b @(r0,r1),r0; braf r0: the mask bounds the index to
        0..3, so the table has four entries (Dragon Force 2 06036790's memcpy tail)."""
        src = self.saturn_table("6043C9036103C708001C00230009" "E000000B0009E00A000B0009E014000B0009E01E000B0009"
                                "0009" "00060C12", 0x26)
        for k in ("0x0600000eu", "0x06000014u", "0x0600001au", "0x06000020u"):
            self.assertIn(f"case {k}:", src)

    def test_saturn_self_call(self):
        """bsr to the function's own entry: DP's oracle runs it (no mocked callee), so the C recurses through the
        body helper with the frame and the return address the bsr leaves (Saturn Bomberman 0601ea10)."""
        src = self.saturn_table("4F2224488901BFFB74FF4F26000B0009", 0x10)
        self.assertIn("static u32 body(u32 sp, u32 ret_addr, u32 param_1)", src)
        self.assertIn("r0 = (u32)body(r15, 0x0600000au, param_1);", src)
        self.assertIn("return body(0x060fe000u, 0x060ff000u, param_1);", src)
        self.assertNotIn("tc_call(", src)

    def test_saturn_table_jump_entered_by_bra(self):
        """The range check, then the table load, then bra to a block of its own holding `jmp @r0` (Dragon Force 2
        06036af8's printf format switch): the table is read from the bra's block."""
        src = self.saturn_table("E3033436" "8B01A0070009" "6143311CC70B011D301CA0030009" "E0FF000B0009" "402B0009"
                                "E00A000B0009E014000B0009E01E000B0009E028000B0009" "0009" "FFE6FFECFFF2FFF8", 0x3a)
        for k in ("0x06000022u", "0x06000028u", "0x0600002eu", "0x06000034u"):
            self.assertIn(f"case {k}:", src)

    # a goto form (reduced from Dragon Force 2 0606fd30 by tools/cfgcheck.py, statements and conditions made up)
    # where the hop out of a loop, then out of a do-while(0) block, needs a flag value carried through a second hop
    HOP_TWICE = """L_06000000:;
    if (c0) goto L_06000002;
L_06000002:;
    if (c0) goto L_06000004;
    x_ = 1u;
    goto L_06000006;
L_06000004:;
    if (c0) goto L_06000008;
    if (c3) goto L_0600000a;
    goto L_0600000c;
L_06000008:;
    if (c0) goto L_0600000e;
    if (c3) goto L_0600000a;
    goto L_06000010;
L_0600000a:;
    x_ = 2u;
L_06000010:;
    x_ = 3u;
L_0600000c:;
    goto L_06000006;
L_0600000e:;
    if (c0) goto L_06000012;
    goto L_06000000;
L_06000012:;
    x_ = 4u;
L_06000006:;
    x_ = 5u;
    x_ = 6u;
    x_ = 7u;
    return r0;"""

    def test_saturn_flag_hop_carried(self):
        """The layout runs as the gotos do: a hop's flag value is not overwritten by a second hop on the way to its
        test (that once ran x_ = 1 after x_ = 4, the 0606fd30 failure)."""
        p = subprocess.run([sys.executable, "-c", (
            "import sys; sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + '/tools'); "
            "from sh2translate.cfgstruct import structure_cfg; import cfgcheck; "
            "g = sys.stdin.read().splitlines(); o = structure_cfg(g); "
            "print('gotos' if o is None else cfgcheck.differs(g, o, 64))"), str(SH2 / "translator")],
            input=self.HOP_TWICE, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "None")

    def test_gamecube(self):  # li r3,5; blr, in a DOL with one text section
        code = bytes.fromhex("386000054E800020")
        head = bytearray(0x100)
        struct.pack_into(">I", head, 0, 0x100)
        struct.pack_into(">I", head, 0x48, 0x80003100)
        struct.pack_into(">I", head, 0x90, len(code))
        p = subprocess.run([sys.executable, "-c", (
            "import sys; sys.path.insert(0, sys.argv[1]); from ppctranslate.image import Dol; "
            "from ppctranslate.translate import Facts, Signature, translate_function; "
            "m = {'name': 'fn_80003100', 'signature': 'u32 fn_80003100(void)', 'params': [], 'ret': 'u32'}; "
            "print(translate_function(Dol(bytes.fromhex(sys.argv[2])), 0x80003100, Signature.from_meta(m), Facts(), "
            "[(0x80003100, 0x80003108)])[0])"), str(PPC / "translator"), (bytes(head) + code).hex()],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("return 5u;", p.stdout)
        meta = {"name": "fn_80003100", "signature": "u32 fn_80003100(void)", "params": [], "ret": "u32"}
        self.assertEqual(lint(p.stdout, meta), [])

    def test_gamecube_through_the_adapter(self):
        """targets/gamecube_ppc/translate.py, as the stage runs it (target.json's command): a workspace of two
        functions, the caller's harness only; the callee's result register comes from the whole-program pass, whose
        summaries are kept in state/translator/ (built once: a second run reads them)."""
        # 80003100: li r3,5; blr   80003108: mflr r0; stwu r1,-16(r1); stw r0,20(r1); bl 80003100; addi r3,r3,1;
        #           lwz r0,20(r1); mtlr r0; addi r1,r1,16; blr
        code = bytes.fromhex("386000054E800020" "7C0802A6" "9421FFF0" "90010014" "4BFFFFED" "38630001" "80010014"
                             "7C0803A6" "38210010" "4E800020")
        head = bytearray(0x100)
        struct.pack_into(">I", head, 0, 0x100)
        struct.pack_into(">I", head, 0x48, 0x80003100)
        struct.pack_into(">I", head, 0x90, len(code))
        d = tmpdir("own_ppc_")
        try:
            (d / "main.dol").write_bytes(bytes(head) + code)
            (d / "project.json").write_text(json.dumps({"target": "gamecube_ppc", "binary": "main.dol"}),
                                            encoding="utf-8")
            (d / "state").mkdir()
            (d / "state" / "function_bodies.txt").write_text("80003100 80003100 80003108\n"
                                                             "80003108 80003108 8000312c\n", encoding="utf-8")
            meta = {"name": "fn_80003108", "signature": "u32 fn_80003108(void)", "params": [], "ret": "u32",
                    "callees": [{"addr": "80003100", "regs": []}]}
            (d / "harness" / "80003108").mkdir(parents=True)
            (d / "harness" / "80003108" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            with mock.patch.multiple(paths, ROOT=d, TARGET=PPC, HARNESS=d / "harness"):
                cmd = translate_stage.command({"binary_path": d / "main.dol"}, json.loads(
                    (PPC / "target.json").read_text(encoding="utf-8"))["translator"]["command"],
                    str(PPC / "translator"), "80003108", d / "out.c")
            cache = d / "state" / "translator"
            for run in (1, 2):
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr)
                src = (d / "out.c").read_text(encoding="utf-8")
                self.assertIn("r3 = tc_call(0x80003100u, &a);", src)  # the callee returns in r3
                self.assertEqual(lint(src, meta), [])
                self.assertEqual(len(list(cache.glob("kinds_*.json"))), 1)
                self.assertEqual(len(list(cache.glob("summaries_*.json"))), 2 - run)
                self.assertFalse((cache / "building.lock").exists())
                if run == 1:  # the kept answer alone serves the next call: it reads no summaries
                    first = src
                    for s in cache.glob("summaries_*.json"):
                        s.unlink()
            self.assertEqual(src, first)
            p = subprocess.run([*cmd[:cmd.index("--addr") + 1], "80003110", *cmd[cmd.index("--addr") + 2:]],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, translate_stage.NO_TRANSLATION, p.stderr)  # not a catalogued entry
        finally:
            shutil.rmtree(d, ignore_errors=True)


class GameCubeCache(unittest.TestCase):
    """ppc-translate's whole-program caches (state/translator/): the summaries by the executable, the bodies, the
    facts and the translator's code; the return kinds by those and DP's harness signatures; KEEP of each kind."""
    def test_keys(self):
        sys.path.insert(0, str(PPC / "translator"))
        try:
            from ppctranslate import returns
        finally:
            sys.path.remove(str(PPC / "translator"))
        d = tmpdir("own_ppc_cache_")
        try:
            (d / "main.dol").write_bytes(b"\0" * 0x100)
            ws = mock.Mock(binary=d / "main.dol", facts="facts", bodies={0x80003100: [(0x80003100, 0x80003108)]})
            sig = mock.Mock(generic=True)
            ws.signature = lambda ad: sig
            path, kinds = returns.summaries_path(ws, d), returns.kinds_path(ws, d)
            with mock.patch.object(returns, "_CODE", ["other code"]):
                self.assertNotEqual(returns.summaries_path(ws, d), path)  # a translator edit: built again
                self.assertNotEqual(returns.kinds_path(ws, d), kinds)
            sig = mock.Mock(generic=False, ret="void")  # a harness signature now says void: rounds again
            self.assertEqual(returns.summaries_path(ws, d), path)
            self.assertNotEqual(returns.kinds_path(ws, d), kinds)
            for i in range(returns.KEEP + 2):
                returns._write(d / f"kinds_{i}.json", {})
                os.utime(d / f"kinds_{i}.json", (i, i))
            self.assertEqual(sorted(p.name for p in d.glob("kinds_*.json")),
                             [f"kinds_{i}.json" for i in range(2, returns.KEEP + 2)])
            self.assertIsNone(returns.cached_kinds(ws, d))  # none for these inputs
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Dreamcast(unittest.TestCase):
    def test_through_the_adapter(self):  # mov #5,r0; rts; nop (little endian), signature from the harness
        d = tmpdir("own_sh4_")
        try:
            (d / "state").mkdir()
            (d / "bin").write_bytes(bytes.fromhex("05E00B0009000900"))
            meta = {"name": "fn_8c010000", "signature": "u32 fn_8c010000(void)", "params": [], "ret": "u32",
                    "ret_reg": "r0", "blocks": [["8c010000", "8c010006"]]}
            (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (d / "state" / "function_bodies.txt").write_text("8c010000 8c010000 8c010006\n", encoding="utf-8")
            cmd = [sys.executable, str(SH4 / "translate.py"), "--repo", str(SH4 / "translator"), "--tool", str(TOOL),
                   "--binary", str(d / "bin"), "--addr", "8c010000", "--meta", str(d / "meta.json"), "--target-json",
                   str(SH4 / "target.json"), "--bodies", str(d / "state" / "function_bodies.txt"), "--out",
                   str(d / "out.c")]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            src = (d / "out.c").read_text(encoding="utf-8")
            self.assertRegex(src, r"0x5u|5u")
            self.assertEqual(lint(src, meta), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_entry(self):  # the stage runs it through the adapter, with DP's folder for core/ret_reg
        tj = json.loads((SH4 / "target.json").read_text(encoding="utf-8"))
        self.assertEqual(tj["translator"]["name"], "sh4-translate")
        self.assertIn("{target}/translate.py", tj["translator"]["command"])
        self.assertIn("{tool}", tj["translator"]["command"])


class Xbox360(unittest.TestCase):
    def test_through_the_adapter(self):
        """targets/xbox360_ppc/translate.py, as the stage runs it (target.json's command): a workspace of two
        functions, the caller's harness only; the callee's result register comes from the whole-program pass, whose
        summaries are kept in state/translator/ (built once: a second run reads them)."""
        # 82000000: li r3,5; blr   82000008: mflr r12; stw r12,-8(r1); stwu r1,-96(r1); bl 82000000; addi r3,r3,1;
        #           addi r1,r1,96; lwz r12,-8(r1); mtlr r12; blr
        code = bytes.fromhex("386000054E800020" "7D8802A6" "9181FFF8" "9421FFA0" "4BFFFFED" "38630001" "38210060"
                             "8181FFF8" "7D8803A6" "4E800020")
        d = tmpdir("own_x360_")
        try:
            (d / "image.bin").write_bytes(code.ljust(0x100, bytes(1)))
            (d / "project.json").write_text(json.dumps({"target": "xbox360_ppc", "binary": "image.bin"}),
                                            encoding="utf-8")
            (d / "image.json").write_text(json.dumps({"load_address": 0x82000000, "sections": [
                {"start": 0x82000000, "end": 0x82000100, "code": True}]}), encoding="utf-8")
            (d / "catalog_inputs.json").write_text(json.dumps({"helper_ranges": [], "functions": [
                {"addr": "82000000", "size": 8, "kind": "function"},
                {"addr": "82000008", "size": 36, "kind": "function"}]}), encoding="utf-8")
            (d / "state").mkdir()
            meta = {"name": "fn_82000008", "signature": "u32 fn_82000008(void)", "params": [], "ret": "u32",
                    "callees": [{"addr": "82000000", "regs": []}]}
            (d / "harness" / "82000008").mkdir(parents=True)
            (d / "harness" / "82000008" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            with mock.patch.multiple(paths, ROOT=d, TARGET=X360, HARNESS=d / "harness"):
                cmd = translate_stage.command({"binary_path": d / "image.bin"}, json.loads(
                    (X360 / "target.json").read_text(encoding="utf-8"))["translator"]["command"],
                    str(X360 / "translator"), "82000008", d / "out.c")
            cache = d / "state" / "translator"
            for run in (1, 2):
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr)
                src = (d / "out.c").read_text(encoding="utf-8")
                self.assertIn("r3 = tc_call(0x82000000u, &a);", src)  # the callee returns in r3
                self.assertIn("return (u32)r3;", src)
                self.assertEqual(lint(src, meta), [])
                self.assertEqual(len(list(cache.glob("kinds_*.json"))), 1)
                self.assertEqual(len(list(cache.glob("summaries_*.json"))), 2 - run)
                self.assertFalse((cache / "building.lock").exists())
                if run == 1:  # the kept answer alone serves the next call: it reads no summaries
                    first = src
                    for s in cache.glob("summaries_*.json"):
                        s.unlink()
            self.assertEqual(src, first)
            p = subprocess.run([*cmd[:cmd.index("--addr") + 1], "82000004", *cmd[cmd.index("--addr") + 2:]],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, translate_stage.NO_TRANSLATION, p.stderr)  # not a catalogued entry
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_entry(self):
        tj = json.loads((X360 / "target.json").read_text(encoding="utf-8"))
        self.assertEqual(tj["translator"]["name"], "xenon-translate")
        self.assertIn("{target}/translate.py", tj["translator"]["command"])

    def test_cache_keys(self):
        """xenon-translate's whole-program caches (state/translator/): the summaries by the executable, the bodies,
        the facts and the translator's code; the return kinds by those and DP's harness signatures; KEEP of each."""
        sys.path.insert(0, str(X360 / "translator"))
        try:
            from xenontranslate import returns
        finally:
            sys.path.remove(str(X360 / "translator"))
        d = tmpdir("own_x360_cache_")
        try:
            (d / "image.bin").write_bytes(b"\0" * 0x100)
            sig = mock.Mock(generic=True)
            ws = types.SimpleNamespace(binary=d / "image.bin", facts="facts", signature=lambda ad: sig,
                                       bodies={0x82000000: [(0x82000000, 0x82000008)]}, harnessed=lambda: {0x82000000})
            path, kinds = returns.summaries_path(ws, d), returns.kinds_path(ws, d)
            with mock.patch.object(returns, "_CODE", ["other code"]):
                self.assertNotEqual(returns.summaries_path(ws, d), path)  # a translator edit: built again
                self.assertNotEqual(returns.kinds_path(ws, d), kinds)
            sig = mock.Mock(generic=False, ret="void")  # a harness signature now says void: rounds again
            self.assertEqual(returns.summaries_path(ws, d), path)
            self.assertNotEqual(returns.kinds_path(ws, d), kinds)
            ws.harnessed = set  # no harness: only harnessed functions' signatures are asked
            self.assertEqual(returns.kinds_path(ws, d), kinds)
            for i in range(returns.KEEP + 2):
                returns._write(d / f"kinds_{i}.json", {})
                os.utime(d / f"kinds_{i}.json", (i, i))
            self.assertEqual(sorted(p.name for p in d.glob("kinds_*.json")),
                             [f"kinds_{i}.json" for i in range(2, returns.KEEP + 2)])
            self.assertIsNone(returns.cached_kinds(ws, d))  # none for these inputs
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Stage(unittest.TestCase):
    TX = {"name": "sh2-translate", "command": ["{python}"], "timeout_s": 5}

    def test_inside_dp_by_default(self):
        with mock.patch.object(paths, "TARGET", SH2):
            st, why = translate_stage.settings({"translator": self.TX, "translators": {}})
            self.assertIsNone(why)
            self.assertEqual(pathlib.Path(st[2]), SH2 / "translator")
            st, _ = translate_stage.settings({"translator": self.TX, "translators": {"sh2-translate": "elsewhere"}})
            self.assertEqual(st[2], "elsewhere")  # a folder named in config.local.json wins
        with mock.patch.object(paths, "TARGET", TOOL / "targets" / "no_such_target"):  # a target without one
            st, why = translate_stage.settings({"translator": self.TX, "translators": {}})
            self.assertIsNone(st)
            self.assertIn("no folder for translator", why)

    def test_origin_recorded(self):
        for t, commit in ((SH2, "0b28828"), (PPC, "5575050"), (SH4, "5a51194"), (X360, "66b6b4a")):
            self.assertIn(commit, (t / "translator" / "ORIGIN.md").read_text(encoding="utf-8"))


class Regress(unittest.TestCase):
    def regress(self, target, kind, baseline):
        ws = WORKSPACES.get(target)
        if not ws or not pathlib.Path(ws, "project.json").is_file():
            self.skipTest(f'no config.local.json "translator_regress" workspace for {target}')
        base = json.loads((TOOL / "targets" / target / "translator" / "regress" / baseline).read_text(encoding="utf-8"))
        want = {a: r["c_sha256"] for a, r in base.items() if r.get("c_sha256")}  # rows the judge gave no C are left out
        d = tmpdir(f"own_{kind}_")
        try:
            (d / "addrs.json").write_text(json.dumps(sorted(want)), encoding="utf-8")
            (d / "run.py").write_text(RUNNER, encoding="utf-8")
            p = subprocess.run([sys.executable, "-I", str(d / "run.py"), kind,
                                str(TOOL / "targets" / target / "translator"), ws,
                                str(TOOL / "targets" / target / "target.json"), str(d / "cache"),
                                str(d / "addrs.json")], capture_output=True, text=True, timeout=400)
            self.assertEqual(p.returncode, 0, p.stderr[-2000:])
            got = json.loads(p.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertGreater(len(want), 50)
        self.assertEqual({a: (want[a], got.get(a)) for a in want if want[a] != got.get(a)}, {})

    def test_saturn_grandia_field(self):
        self.regress("saturn_sh2", "sh2", "grandia_field.json")

    def test_gamecube_skies(self):
        self.regress("gamecube_ppc", "ppc", "skies_all.json")

    def test_dreamcast_toy_commander(self):
        self.regress("dreamcast_sh4", "sh4", "toycommander_sample.json")

    def test_xbox360_too_human(self):
        self.regress("xbox360_ppc", "x360", "toohuman_set.json")


if __name__ == "__main__":
    unittest.main()
