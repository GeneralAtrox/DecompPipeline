"""Registers a function reads before it sets them (fn_parts.uncarried, hand_over, vector_abi): in the judge's
form read from the vector where it seeds them (r3..r10, f1..f13) and refused otherwise, carried in the port form. Too Human's movie decoder passed its constants in v64..v77 and read them as 0 in the
port form, and its set-up branches into a catalogue function (82cbd238) that tests the caller's r11.

    python -m unittest tests.test_registers
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
X360 = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from xenontranslate import workspace  # noqa: E402
from xenontranslate.translate import Signature, TranslateError, translate_function  # noqa: E402

# address: (words, what it does)
CODE = {
    0x82000000: ("386B0001 4E800020", "addi r3,r11,1; blr: reads r11"),
    0x82000008: ("FBE1FFF8 3BE00001 7FE3FB78 EBE1FFF8 4E800020", "std r31,-8(r1); li r31,1; mr r3,r31; ld r31,-8(r1); blr: saves r31 only"),
    0x8200001c: ("1074A484 7C6019CE 4E800020", "vor v3,v20,v20; stvx v3,r0,r3; blr: reads v20"),
    0x82000028: ("2C030000 4E800020", "cmpwi r3,0; blr: copies XER[SO] into CR0, which nothing reads"),
    0x82000030: ("39600005 4BFFFFCC", "li r11,5; b 82000000: hands r11 on"),
    0x82000038: ("4BFFFFC8", "b 82000000: hands on r11 it never sets, its own caller's"),
    0x82000040: ("38650001 4E800020", "addi r3,r5,1; blr: reads r5"),
    0x82000048: ("FC21082A 4E800020", "fadd f1,f1,f1; blr: reads f1"),
    0x82000050: ("FC00701E D801FFF8 8061FFFC 4E800020", "fctiwz f0,f14; stfd f0,-8(r1); lwz r3,-4(r1); blr: reads f14, relayed through the frame"),
    0x82000060: ("396E0001 F961FFF8 8061FFFC 4E800020", "addi r11,r14,1; std r11,-8(r1); lwz r3,-4(r1); blr: reads r14, relayed through the frame"),
}


class ReadBeforeSet(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="x360_regs_"))
        img = bytearray(0x100)
        for a, (words, _) in CODE.items():
            b = bytes.fromhex(words.replace(" ", ""))
            img[a - 0x82000000:a - 0x82000000 + len(b)] = b
        (self.d / "image.bin").write_bytes(bytes(img))
        (self.d / "project.json").write_text(json.dumps({"target": "xbox360_ppc", "binary": "image.bin"}), encoding="utf-8")
        (self.d / "image.json").write_text(json.dumps({"load_address": 0x82000000, "sections": [
            {"start": 0x82000000, "end": 0x82000100, "code": True}]}), encoding="utf-8")
        (self.d / "catalog_inputs.json").write_text(json.dumps({"helper_ranges": [], "functions": [
            {"addr": f"{a:08x}", "size": len(w.replace(" ", "")) // 2, "kind": "function"} for a, (w, _) in CODE.items()]}),
            encoding="utf-8")
        (self.d / "state").mkdir()
        self.w = workspace.Workspace(self.d, target=X360 / "target.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def tx(self, a):
        return translate_function(self.w.dol, a, Signature.generic_for(a), self.w.facts, self.w.bodies[a])

    def test_judge_form_refuses(self):
        with self.assertRaisesRegex(TranslateError, "reads r11 before setting"):
            self.tx(0x82000000)
        with self.assertRaisesRegex(TranslateError, "reads v20 before setting"):
            self.tx(0x8200001c)
        self.assertEqual(self.tx(0x82000008)[1].uncarried, [])   # a save of the entry value into the frame is no read
        self.assertEqual(self.tx(0x82000028)[1].uncarried, [])   # nor is a read whose result nothing uses

    def test_frame_relay_is_a_read(self):
        """A value the function computes, stores into its frame and loads back is no save of an entry value: the register
        it was computed from is read first. Before the fix the store hid it, the judge form passed, and the port form took
        the caller's f14 / r14 as 0."""
        with self.assertRaisesRegex(TranslateError, "reads f14 before setting"):
            self.tx(0x82000050)
        with self.assertRaisesRegex(TranslateError, "reads r14 before setting"):
            self.tx(0x82000060)
        self.w.facts.port = True
        for a, n in ((0x82000050, "f14"), (0x82000060, "r14")):
            src, ft = self.tx(a)
            self.assertEqual((ft.read_first, ft.uncarried), ([n], []))
            self.assertIn(("tc_ldf(0xeu)" if n[0] == "f" else "tc_ldg(0xeu)"), src)

    def test_judge_form_seeds_vector_registers(self):
        """r3..r10 and f1..f13 outside the signature: every vector seeds them (TcOracle.setEntry), so the judge form
        reads them at entry from the runner (exit_form.seed_judge) instead of refusing the function."""
        import re
        only_r3 = Signature("fn", "", [("a", "u64", "r3", -1)], "u64")
        src, ft = translate_function(self.w.dol, 0x82000040, only_r3, self.w.facts, self.w.bodies[0x82000040])
        self.assertEqual((ft.read_first, ft.uncarried), (["r5"], []))
        self.assertRegex(src, r"tcx_in_r\((0x)?5u\)")
        none = Signature("fn", "", [], "f64")
        src, ft = translate_function(self.w.dol, 0x82000048, none, self.w.facts, self.w.bodies[0x82000048])
        self.assertEqual((ft.read_first, ft.uncarried), (["f1"], []))
        self.assertRegex(src, r"tcx_in_f\((0x)?1u\)")
        self.assertFalse(re.search(r"tcx_in_r\((0x)?5u\)", self.tx(0x82000040)[0]))   # a signature register stays a parameter

    def test_port_form_carries(self):
        self.w.facts.port = True
        src, ft = self.tx(0x8200001c)
        self.assertEqual((ft.read_first, ft.uncarried), (["v20"], []))
        self.assertIn("xv_ldv(0x14u)", src)   # the caller's v20, from the thread's vector file
        self.assertIn("xv_stv(3u, v3)", src)   # and v3 back into it at the return
        src, ft = self.tx(0x82000000)
        self.assertEqual((ft.read_first, ft.uncarried), (["r11"], []))
        self.assertIn("tc_ldg(0xbu)", src)   # r11 from the thread's register file
        self.assertIn("tc_gr[k]", src)
        self.w.facts.read_first_map = {0x82000000: ["r11"]}   # gen_xbox reads it from the last translation
        src, ft = self.tx(0x82000030)
        self.assertIn("tc_stg(0xbu, 5", src)   # the branch hands r11 on
        src, ft = self.tx(0x82000038)
        self.assertEqual((ft.read_first, ft.uncarried), (["r11"], []))   # a pass-through: loaded, handed on, and read first
        self.assertIn("tc_ldg(0xbu)", src)
        self.assertIn("tc_stg(0xbu", src)

    def test_planted_drops_are_caught(self):
        """Planted wrong answers: the hand-over loses the callee's load of r11, or the caller's store of it. Each is named."""
        from xenontranslate import fn_parts
        self.w.facts.port = True
        self.w.facts.read_first_map = {0x82000000: ["r11"]}
        real = fn_parts.hand_over
        try:
            def no_load(blocks, own, need, params=()):   # the callee's entry load of r11 lost
                extra = real(blocks, own, need, params)
                blocks[0].stmts = [s_ for s_ in blocks[0].stmts if getattr(s_, "dst", None) != "r11"]
                return extra
            fn_parts.hand_over = no_load
            self.assertEqual(self.tx(0x82000000)[1].uncarried, ["r11"])
            fn_parts.hand_over = lambda blocks, own, need, params=(): real(blocks, own, {}, params)   # the caller's store lost
            self.assertEqual(self.tx(0x82000030)[1].uncarried, ["r11>82000000"])
        finally:
            fn_parts.hand_over = real
        self.assertEqual(self.tx(0x82000030)[1].uncarried, [])   # and the real hand-over passes


if __name__ == "__main__":
    unittest.main()
