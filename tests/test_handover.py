"""The register hand-over check (core/handover.py, targets/xbox360_ppc/port/tools/handover.py) on a made-up image: it is
silent on a settled translation's proven-fixed edges, and each proven fault planted back is reported on exactly its edge.

Plants (the fix reverted on one function): a970b91c the caller's store of a register the callee reads first, and the
callee's load; the callee map one translation behind (what gen/ really is after a single pass); ef0c50f6 / 842bc5b1 the
vector file's load and store; f29cc3eb / 00023f62 a register the callee keeps, zeroed after the call.
usage: python tests/test_handover.py
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
X360 = REPO / "targets" / "xbox360_ppc"
sys.path[:0] = [str(REPO), str(X360 / "translator"), str(X360 / "port" / "tools")]
import handover as XH  # noqa: E402
from xenontranslate import fn_parts, workspace  # noqa: E402
from xenontranslate.translate import Signature, translate_function  # noqa: E402

A, V, C, D, E, VC, K, KA, S, SA, CRG, CRA, P, U, KB, M = (0x82000000, 0x8200001c, 0x82000030, 0x82000038, 0x8200003c, 0x82000044,
                                                        0x8200004c, 0x82000054, 0x82000064, 0x8200006c, 0x82000078, 0x82000084,
                                                        0x8200008c, 0x82000098, 0x820000a0, 0x820000ac)
CODE = {
    A: "386B0001 4E800020",            # addi r3,r11,1; blr                       reads the caller's r11
    V: "1074A484 7C6019CE 4E800020",   # vor v3,v20,v20; stvx v3,r0,r3; blr       reads the caller's v20
    C: "39600005 4BFFFFCC",            # li r11,5; b A                            hands r11 on
    D: "4BFFFFC8",                     # b A                                      hands on its own caller's r11
    E: "39600007 4BFFFFF8",            # li r11,7; b D
    VC: "1295AC84 4BFFFFD4",           # vor v20,v21,v21; b V                     sets v20 for V
    K: "38600001 4E800020",            # li r3,1; blr                             leaves r7 alone
    KA: "38E00009 4BFFFFF5 7CE33B78 4E800020",   # li r7,9; bl K; mr r3,r7; blr   reads r7 after the call
    S: "39600003 4E800020",            # li r11,3; blr                            sets r11 for its caller
    SA: "4BFFFFF9 7D635B78 4E800020",  # bl S; mr r3,r11; blr                     reads the callee's r11
    CRG: "4D820020 38600000 4E800020",  # beqlr; li r3,0; blr                     branches on the caller's CR0
    CRA: "2C030000 4BFFFFF0",          # cmpwi r3,0; b CRG
    P: "7C6903A6 4E800421 4E800020",   # mtctr r3; bctrl; blr                     a pointer call
    U: "7C030378 4E800020",            # mr r3,r0; blr                            reads r0, and nothing calls it
    KB: "4BFFFFAD 38630001 4E800020",  # bl K; addi r3,r3,1; blr                  reads K's result
    M: "2F030000 7C600026 4E800020",   # cmpwi cr6,r3,0; mfcr r3; blr              the whole CR, for the field it just set
}
KEEPS = {f"{K:08x}": ["r7"]}
EXPECT = {("fwd-nochannel", CRA, CRG, "c0eq"), ("ret-set", SA, S, "r11"), ("entry-uncalled", U, U, "r0"), ("ptr-target", A, A, "r11")}


class HandOver(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="x360_handover_"))
        img = bytearray(0x200)
        for a, words in CODE.items():
            b = bytes.fromhex(words.replace(" ", ""))
            img[a - A:a - A + len(b)] = b
        img[0x180:0x184] = A.to_bytes(4, "big")     # a data word holds A's address
        (self.d / "image.bin").write_bytes(bytes(img))
        (self.d / "project.json").write_text(json.dumps({"target": "xbox360_ppc", "ghidra_name": "fixture", "binary": "image.bin"}), encoding="utf-8")
        (self.d / "image.json").write_text(json.dumps({"load_address": A, "sections": [{"start": A, "end": A + 0x100, "code": True}]}), encoding="utf-8")
        (self.d / "catalog_inputs.json").write_text(json.dumps({"helper_ranges": [], "functions": [
            {"addr": f"{a:08x}", "size": len(w.replace(" ", "")) // 2, "kind": "function"} for a, w in CODE.items()]}), encoding="utf-8")
        (self.d / "state").mkdir()
        # handover._init imports gen_xbox (the game's setjmp, 3355c21b), which imports core.paths: it needs a project folder
        # (project.json with a target and a ghidra_name). This one has both. core.paths reads it once, at its first import, and keeps only the target from it.
        os.environ["DECOMP_PROJECT"] = str(self.d)
        self.w = workspace.Workspace(self.d, target=X360 / "target.json")
        self.w.facts.port = True
        self.gen, self.out = self.d / "gen", self.d / "ho"
        self.gen.mkdir()
        self.analysis = self.d / "analysis.json"
        self.write_analysis(KEEPS, {f"{K:08x}": "r"})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write_analysis(self, keeps, kinds):
        self.analysis.write_text(json.dumps({"keeps": keeps, "kinds": kinds}), encoding="utf-8")

    def translate(self, need_of, only=None, into=None):
        """gen/fn_ADDR.c as gen_xbox.Kit.translate writes it (no thunk); returns the next translation's callee map."""
        self.w.facts.read_first_map = need_of
        seen = {}
        for a in only or CODE:
            sig = Signature.generic_for(a)
            sig.keeps = {int(k, 16): set(v) for k, v in KEEPS.items()}
            src, ft = translate_function(self.w.dol, a, sig, self.w.facts, self.w.bodies[a])
            seen[a] = [n for n in ft.read_first if n[0] in "rf" and n[1:].isdigit()]
            (into or self.gen).joinpath(f"fn_{a:08x}.c").write_text(f"/* read before set: {' '.join(ft.read_first)} */\n" + src, encoding="utf-8")
        return {a: v for a, v in seen.items() if v}

    def settle(self):
        need = {}
        for _ in range(6):
            nxt = self.translate(need)
            if nxt == need:
                return need
            need = nxt
        self.fail("the callee map does not settle")

    def findings(self, overlay=None):
        XH._init(str(self.d))
        self.out.mkdir(exist_ok=True)
        with open(self.out / "records.jsonl", "w", encoding="utf-8") as fh:
            for a in sorted(CODE):
                _, line, why = XH._one((a, str(self.gen / f"fn_{a:08x}.c")))
                self.assertIsNone(why)
                fh.write(f"{a:08x}\t{line}\n")
        img, ev = XH.load(self.out, self.d, self.analysis, overlay)
        rows, seen = XH.H.check(img, ev)
        self.seen, self.img = seen, img
        return {(r["kind"], r["caller"], r["callee"], r["reg"]) for r in rows}

    def test_settled_translation(self):
        """Only what no fix covers yet is reported: a condition field handed on, a register a callee sets for its caller, a
        function nothing calls that reads r0, and a pointer-callable function that reads r11."""
        self.settle()
        self.assertEqual(self.findings(), EXPECT)
        self.assertEqual(self.seen["pointer calls"], 1)
        self.assertEqual(self.img.lost(D), ["r11"])          # passed through: D's own callers must hand it on
        self.assertEqual(self.img.need[E] & {"r11"}, set())   # E sets it
        self.assertEqual(self.img.lost(M), [])                # mfcr is no read of the caller's condition fields

    def test_case_word_in_code_is_no_pointer(self):
        """2741dcb2's cut with its real fault beside it: U's address as a word inside the code section (an inline jump table's
        case word, 82e00ffc's) makes no ptr-target; the same word in data does."""
        self.settle()
        img = bytearray((self.d / "image.bin").read_bytes())
        img[0xf0:0xf4] = U.to_bytes(4, "big")       # inside the code section A..A+0x100, past the last function
        (self.d / "image.bin").write_bytes(bytes(img))
        self.assertEqual(self.findings(), EXPECT)
        img[0x184:0x188] = U.to_bytes(4, "big")     # in data
        (self.d / "image.bin").write_bytes(bytes(img))
        self.assertEqual(self.findings(), EXPECT | {("ptr-target", U, U, "r0")})

    def test_jump_table_fallback_writes_nothing(self):
        """00023f62's callee 82c66960 is a jump table whose fallback is a pointer jump: it still leaves r7 alone."""
        rec = lambda b, c=(): json.dumps({"b": b, "s": [[] for _ in b], "c": list(c)})
        lines = {1: rec([[["c", 0, []], ["r", ["r3"]]]], [{"at": 1, "to": None, "tail": True, "sw": True}]),
                 2: rec([[["a", "r7", []], ["c", 0, []], ["r", ["r7"]]]], [{"at": 2, "to": 1, "tail": False}]),
                 3: rec([[["c", 0, []], ["r", ["r3"]]]], [{"at": 3, "to": None, "tail": True}]),
                 4: rec([[["a", "r7", []], ["c", 0, []], ["r", ["r7"]]]], [{"at": 4, "to": 3, "tail": False}])}
        img = XH.H.Image(lines, XH.ABI)
        got = {(r["kind"], r["caller"], r["callee"], r["reg"]) for r in XH.H.check(img, XH.H.Evidence())[0]}
        self.assertEqual(got, {("ret-zeroed", 2, 1, "r7"), ("ret-set", 4, 3, "r7")})

    def test_one_translation_behind(self):
        """a970b91c's map comes from the translation before: after a single pass neither function that sets r11 stores it."""
        self.translate({})
        got = self.findings() - EXPECT
        self.assertEqual(got, {("fwd-unstored", C, A, "r11"), ("fwd-unstored", E, D, "r11")})   # D only passes it through

    def test_planted_store_and_load(self):
        need = self.settle()
        over = self.d / "plant"
        over.mkdir()
        real = fn_parts.hand_over
        try:
            fn_parts.hand_over = lambda blocks, own, n, params=(): real(blocks, own, {}, params)   # the caller's store lost
            self.translate(need, [C], over)
            self.assertEqual(self.findings(over) - EXPECT, {("fwd-unstored", C, A, "r11")})

            def no_load(blocks, own, n, params=()):    # the callee's entry load lost
                extra = real(blocks, own, n, params)
                blocks[0].stmts = [s for s in blocks[0].stmts if getattr(s, "dst", None) != "r11"]
                return extra
            fn_parts.hand_over = no_load
            (over / f"fn_{C:08x}.c").unlink()
            self.translate(need, [A], over)
            self.assertEqual(self.findings(over) - EXPECT, {("fwd-unloaded", A, A, "r11")})
        finally:
            fn_parts.hand_over = real

    def test_planted_vector_file(self):
        """842bc5b1 / ef0c50f6: without the vector file the callee reads v20 as 0, or the caller never stores it."""
        need = self.settle()
        real = fn_parts.vector_abi
        try:
            fn_parts.vector_abi = lambda blocks: None
            over = self.d / "plant"
            over.mkdir()
            self.translate(need, [V], over)
            self.assertEqual(self.findings(over) - EXPECT, {("vec-unloaded", V, V, "v20")})
            (over / f"fn_{V:08x}.c").unlink()
            self.translate(need, [VC], over)
            self.assertEqual(self.findings(over) - EXPECT, {("vec-unstored", VC, V, "v20"), ("vec-unloaded", VC, VC, "v21")})   # VC reads v21 itself
        finally:
            fn_parts.vector_abi = real

    def test_declared_longjmp_and_exit_never_return(self):
        """Too Human's longjmp 82a566a0 has a path that calls RtlUnwind and then returns like any function, and its exit
        82a4f310 tail-calls doexit, which returns for _cexit; luaX_errorline 82809a50 ends in longjmp, or in the panic function
        and exit, and luaX_syntaxerror 82809ae8 ends in it. Their callers read registers after them: 57 ret-set rows. The game's
        tables (jumps.json's longjmp, noreturn.json) are never-return seeds, and nothing after a call of them is read."""
        rec = lambda b, s, c=(): json.dumps({"b": b, "s": s, "c": list(c), "t": {"st": {}, "ld": [], "vst": [], "nv": False}},
                                            separators=(",", ":"))
        UNW, LJ, LJC, SYN, SYNC, EXIT, ERRL, ERRC = 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80
        lines = {UNW: rec([[["r", ["r3", "f1"]]]], [[]]),                                   # RtlUnwind: returns
                 LJ: rec([[["u", ["r3"]]], [["a", "r5", ["r3"]], ["u", ["r5"]]],        # blr to the jmp_buf's LR
                          [["c", 0, ["r3"]], ["r", ["r3", "f1"]]]], [[1, 2], [], []],    # unwind, then a return
                         [{"at": LJ + 8, "to": UNW, "tail": False}]),
                 LJC: rec([[["c", 0, ["r3"]], ["u", ["r5"]], ["r", ["r3", "f1"]]]], [[]], [{"at": LJC, "to": LJ, "tail": False}]),
                 SYN: rec([[["c", 0, ["r3"]], ["r", ["r3", "f1"]]]], [[]], [{"at": SYN, "to": LJ, "tail": True}]),
                 SYNC: rec([[["c", 0, ["r3"]], ["u", ["r5"]], ["r", ["r3", "f1"]]]], [[]], [{"at": SYNC, "to": SYN, "tail": False}]),
                 EXIT: rec([[["a", "r5", []], ["r", ["r3", "f1"]]]], [[]]),                         # doexit's _cexit return
                 ERRL: rec([[["u", ["r3"]]], [["c", 0, ["r3"]]], [["c", 1, []], ["r", ["r3", "f1"]]]], [[1, 2], [2], []],
                           [{"at": ERRL + 4, "to": LJ, "tail": False}, {"at": ERRL + 8, "to": EXIT, "tail": False}]),
                 ERRC: rec([[["c", 0, ["r3"]], ["u", ["r5"]], ["r", ["r3", "f1"]]]], [[]], [{"at": ERRC, "to": ERRL, "tail": False}])}
        self.out.mkdir(exist_ok=True)
        (self.out / "records.jsonl").write_text("".join(f"{a:08x}\t{ln}\n" for a, ln in lines.items()), encoding="utf-8")

        game = self.d / "game"
        game.mkdir()
        (game / "jumps.json").write_text(json.dumps({"setjmp": ["00000099"], "longjmp": [f"{LJ:08x}"]}), encoding="utf-8")
        self.assertEqual(XH.game_noreturn(game), {LJ})
        (game / "noreturn.json").write_text(json.dumps({"noreturn": [f"{EXIT:08x}"]}), encoding="utf-8")
        self.assertEqual(XH.game_noreturn(game), {LJ, EXIT})

        def rows(noreturn):
            img, ev = XH.load(self.out, noreturn=noreturn)
            return {(r["kind"], r["caller"], r["callee"], r["reg"]) for r in XH.H.check(img, ev)[0]}
        self.assertEqual(rows(()), {("ret-set", LJC, LJ, "r5"), ("ret-set", SYNC, SYN, "r5"), ("ret-set", ERRC, ERRL, "r5")})
        self.assertEqual(rows({LJ}), {("ret-set", ERRC, ERRL, "r5")})    # the panic path still ends in exit
        self.assertEqual(rows(XH.game_noreturn(game)), set())

    def test_planted_zeroed_keep_and_result_kind(self):
        """f29cc3eb / 00023f62: K leaves r7 alone; a translation that does not know it zeroes r7 under KA's read. And a
        callee taken as returning nothing while its caller reads the r3 it sets."""
        self.settle()
        self.write_analysis({}, {f"{K:08x}": "r"})
        self.assertEqual(self.findings() - EXPECT, {("ret-zeroed", KA, K, "r7")})
        self.write_analysis(KEEPS, {f"{K:08x}": "v"})
        self.assertEqual(self.findings() - EXPECT, {("ret-kind", KB, K, "r3")})


if __name__ == "__main__":
    unittest.main()
