"""The proof gates' third reason (core/unchecked_args, tools/args_marks.py): a call whose arguments no judge compares.
1. harness_calls: a recorded call to a target no callee record lists, with no CA line after it, is "none"; with a CA
   line that lists no register, "empty"; a listed callee's call and a CA line with registers are checked;
2. the stamp scope unlisted_call_args (sh_in_scope, ppc_in_scope over the image's words): a body that runs on past its
   end, or calls or branches to code no function starts, is in scope; one with a computed call or jump (the CA lines
   were there already), or whose calls all reach function entries or frame helpers, is not;
3. the marks: a marked matched row turns "unproven" (the ledger-truth gate's shape) and its readable row `unproven`
   with "args"; another gate's marks stay; a lifted mark puts both back; proof.scan gives the state unchecked_args
   and proof.readable_state keeps the readable row unproven while marked;
4. tools/args_marks.py --apply backs the files up and writes them; a second run changes nothing;
5. the callee's proven signature (core/callee_drift.signature_regs, call_regs): a matched callee's harness params are
   compared at a call no record lists, whether or not the caller set them; no harness, not matched or a parameter the
   index cannot place gives None; only at a direct or run-on call (a pointer call keeps its CA line); the gate marks
   a direct or run-on target without one "unsigned", and a proven one whose registers a verdict from before SINCE did
   not compare "rejudge".
Run: python -W error tests/test_unchecked_args.py
"""
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import callee_drift, compare, proof, unchecked_args as ua  # noqa: E402

EXP = """V 0 dev
CALL 8c001000 1 2 3
CALL 8c002000 1 2 3
CA r4 r5
CALL 8c003000 1 2 3
CA
CALL 8c004000 1 2 3
E
V 1 dev
CALL 8c005000 1 2 3
E
"""


def harness(d, addr, callees, text):
    h = pathlib.Path(d) / "harness" / addr
    h.mkdir(parents=True)
    (h / "meta.json").write_text(json.dumps({"callees": [{"addr": c, "regs": []} for c in callees]}), encoding="utf-8")
    (h / "expected.txt").write_text(text, encoding="utf-8")


class Calls(unittest.TestCase):
    def test_none_empty_checked(self):
        with tempfile.TemporaryDirectory() as d:
            harness(d, "8c000000", ["8c001000"], EXP)
            self.assertEqual(ua.harness_calls(pathlib.Path(d) / "harness", "8c000000"),
                             {"none": ["8c004000", "8c005000"], "empty": ["8c003000"]})
            self.assertEqual(ua.unchecked({"none": ["8c004000"], "empty": ["8c003000"]}), ["8c004000"])
            self.assertIsNone(ua.harness_calls(pathlib.Path(d) / "harness", "8c00ffff"))


def sh_words(base, words):
    return lambda a: words[(a - base) // 2] if 0 <= a - base < 2 * len(words) else None


class Scope(unittest.TestCase):
    B = 0x8C010000

    def test_sh(self):
        rts, nop, add = 0x000B, 0x0009, 0x7001
        bsr_far = 0xB000 | 0x100  # bsr to B + 4 + 0x200
        jsr = 0x410B
        span = [(self.B, self.B + 6)]
        self.assertTrue(ua.sh_in_scope(sh_words(self.B, [add, add, add]), span, set()))  # runs on past its end
        self.assertFalse(ua.sh_in_scope(sh_words(self.B, [add, rts, nop]), span, set()))
        self.assertFalse(ua.sh_in_scope(sh_words(self.B, [jsr, rts, nop]), span, set()))  # computed: CA already
        self.assertTrue(ua.sh_in_scope(sh_words(self.B, [bsr_far, rts, nop]), span, set()))
        self.assertFalse(ua.sh_in_scope(sh_words(self.B, [bsr_far, rts, nop]), span, {self.B + 0x204}))  # an entry

    def test_ppc(self):
        base, blr = 0x82000000, 0x4E800020
        bl = lambda frm, to: 0x48000001 | ((to - frm) & 0x03FFFFFC)  # noqa: E731
        ws = lambda w: (lambda a: w[(a - base) // 4] if 0 <= a - base < 4 * len(w) else None)  # noqa: E731
        span = [(base, base + 8)]
        self.assertTrue(ua.ppc_in_scope(ws([bl(base, base + 0x100), blr]), span, set()))
        self.assertFalse(ua.ppc_in_scope(ws([bl(base, base + 0x100), blr]), span, {base + 0x100}))
        self.assertFalse(ua.ppc_in_scope(ws([bl(base, base + 0x100), blr]), span, set(), {base + 0x100}))  # helper
        self.assertTrue(ua.ppc_in_scope(ws([0x60000000, bl(base + 4, base + 0x100)]), span, {base + 0x100}))  # runs on
        self.assertFalse(ua.ppc_in_scope(ws([0x4E800421, blr]), span, set()))  # bctrl: CA already


def image(ws, funcs):
    """image.bin at 0x8c000000 and function_bodies.txt: {entry: [16-bit words]} (little-endian, SH-4)."""
    img = bytearray(0x400)
    for a, words in funcs.items():
        o = int(a, 16) - 0x8C000000
        img[o:o + 2 * len(words)] = struct.pack(f"<{len(words)}H", *words)
    (ws / "image.bin").write_bytes(bytes(img))
    (ws / "state" / "function_bodies.txt").write_text(
        "".join(f"{a} {a} {int(a, 16) + 2 * len(w):08x}\n" for a, w in funcs.items()), encoding="utf-8")


def workspace(d):
    ws = pathlib.Path(d) / "ws"
    (ws / "state").mkdir(parents=True)
    (ws / "project.json").write_text(json.dumps({"name": "t", "target": "dreamcast_sh4", "binary": "image.bin",
                                                 "load_address": "8c000000"}), encoding="utf-8")
    image(ws, {a: [0x410B, 0x000B, 0x0009] for a in ("8c000000", "8c000100", "8c000200", "8c000300")})  # jsr: a pointer
    rows = {"8c000000": {"status": "matched"}, "8c000100": {"status": "matched", "unverified": {"reason": "x"}},
            "8c000200": {"status": "matched", "unproven": {"reason": "other gate"}},
            "8c000300": {"status": "matched", "proof": {"state": "verified"}}}
    (ws / "state" / "ledger.json").write_text(json.dumps({"functions": rows}), encoding="utf-8")
    rd = {a: {"status": "readable", "verified": True} for a in rows}
    rd["8c000300"] = {"status": "unproven", "unproven": {"ok": False, "stores": ["8c000304 mov.l r1,@r2"], "n": 1,
                                                         "oracle": "tried"}, "note": "1 store(s)"}
    (ws / "state" / "readable.json").write_text(json.dumps(rd), encoding="utf-8")
    for a in rows:
        harness(ws, a, [], "V 0 dev\nCALL 8c009000 1\nE\n")
    return ws


class Marks(unittest.TestCase):
    def run_tool(self, ws, backup):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "args_marks.py"), "--project", str(ws),
                            "--apply", "--backup", str(backup)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_mark_and_lift(self):
        with tempfile.TemporaryDirectory() as d:
            ws = workspace(d)
            out = self.run_tool(ws, pathlib.Path(d) / "bak")
            self.assertIn("marked 4", out)
            self.assertIn("raw verified -> unproven 2", out)  # 8c000100: unverified already; 8c000200: other gate
            self.assertTrue(list((pathlib.Path(d) / "bak").glob("*/ledger.json")))
            led = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
            self.assertEqual(led["8c000000"]["unproven"]["reason"], ua.REASON)
            self.assertEqual(led["8c000100"]["unverified"], {"reason": "x"})
            self.assertEqual(led["8c000200"]["unproven"], {"reason": "other gate"})
            rd = json.loads((ws / "state" / "readable.json").read_text(encoding="utf-8"))
            self.assertEqual(rd["8c000000"]["status"], "unproven")
            self.assertEqual(rd["8c000000"]["unproven"]["args"], ["8c009000"])
            self.assertIn(ua.REASON, rd["8c000000"]["note"])
            self.assertEqual(rd["8c000300"]["unproven"]["stores"], ["8c000304 mov.l r1,@r2"])
            self.assertEqual(proof.scan(ws, ["8c000300"], cfg={})["8c000300"]["state"], ua.STATE)
            st, fields = proof.readable_state(ws, "8c000000", rd["8c000000"])
            self.assertEqual((st, fields["unproven"]["args"]), (proof.UNPROVEN, ["8c009000"]))
            self.assertIn("marked 0, lifted 0", self.run_tool(ws, pathlib.Path(d) / "bak2"))
            for a in ("8c000000", "8c000100", "8c000200", "8c000300"):  # re-prepared with the CA lines
                (ws / "harness" / a / "expected.txt").write_text("V 0 dev\nCALL 8c009000 1\nCA r4\nE\n",
                                                                  encoding="utf-8")
            out = self.run_tool(ws, pathlib.Path(d) / "bak3")
            self.assertIn("lifted 4", out)
            led = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
            self.assertNotIn("unproven", led["8c000000"])
            self.assertEqual(led["8c000200"]["unproven"], {"reason": "other gate"})
            rd = json.loads((ws / "state" / "readable.json").read_text(encoding="utf-8"))
            self.assertEqual(rd["8c000000"], {"status": "readable", "verified": True})
            self.assertEqual((rd["8c000300"]["status"], rd["8c000300"]["unproven"].get("args")), ("unproven", None))
            self.assertEqual(proof.scan(ws, ["8c000300"], cfg={})["8c000300"]["state"], proof.VERIFIED)

    def test_lift_option(self):
        with tempfile.TemporaryDirectory() as d:
            ws = workspace(d)
            self.run_tool(ws, pathlib.Path(d) / "bak")
            (ws / "none.txt").write_text("", encoding="utf-8")  # a state-only copy: no harness read, the marks kept
            r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "args_marks.py"), "--project", str(ws),
                                "--from", str(ws / "none.txt"), "--lift", "8c000000", "--apply", "--backup",
                                str(pathlib.Path(d) / "bak2")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("lifted 1", r.stdout)
            self.assertNotIn("8c000000", ua.read(ws))
            led = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
            self.assertNotIn("unproven", led["8c000000"])
            self.assertEqual(led["8c000100"]["unverified"], {"reason": "x"})


class Signature(unittest.TestCase):
    def ws(self, d, matched=True, params=None):
        ws = pathlib.Path(d) / "ws"
        h = ws / "harness" / "8c000300"
        h.mkdir(parents=True)
        (ws / "state").mkdir()
        (ws / "project.json").write_text(json.dumps({"name": "t", "target": "dreamcast_sh4", "binary": "image.bin",
                                                     "load_address": "8c000000"}), encoding="utf-8")
        ps = params if params is not None else [{"storage": "r4", "stack_offset": -1},
                                                {"storage": "stack", "stack_offset": 4}]
        (h / "meta.json").write_text(json.dumps({"params": ps, "callees": []}), encoding="utf-8")
        rows = {"8c000300": {"status": "matched" if matched else "failed", "finished": "2026-11-01T00:00:00"}}
        (ws / "state" / "ledger.json").write_text(json.dumps({"functions": rows}), encoding="utf-8")
        return ws

    def test_signature_regs(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.ws(d)
            idx, cr = compare.REG_INDEX, callee_drift.call_regs
            self.assertEqual(callee_drift.signature_regs(0x8C000300, idx, ws), ["r4", "s1"])
            self.assertEqual(cr({}, 0x8C000300, ["r5"], idx, ws), ["r5", "r4", "s1"])
            self.assertEqual(cr({0x8C000300: ["r6"]}, 0x8C000300, [], idx, ws), ["r6"])  # a record wins
            self.assertEqual(cr({}, 0x8C000400, ["r5"], idx, ws), ["r5"])  # no harness: the CA line alone
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(callee_drift.signature_regs("8c000300", compare.REG_INDEX, self.ws(d, matched=False)))
        with tempfile.TemporaryDirectory() as d:
            ws = self.ws(d, params=[{"storage": "xmm0"}])
            self.assertIsNone(callee_drift.signature_regs("8c000300", compare.REG_INDEX, ws))  # cannot place it

    def test_gate(self):
        bsr = 0xB000 | ((0x300 - 4) // 2)  # bsr 8c000300 from 8c000000
        with tempfile.TemporaryDirectory() as d:
            ws = self.ws(d)
            image(ws, {"8c000000": [bsr, 0x0009, 0x000B, 0x0009], "8c000100": [0x7001, 0x7001],  # runs on to 8c000104
                       "8c000200": [0x410B, 0x000B, 0x0009], "8c000300": [0x000B, 0x0009]})  # 8c000200: jsr
            harness(ws, "8c000200", [], "V 0 dev\nCALL 8c000300 1\nCA r4\nE\n")  # through a pointer
            harness(ws, "8c000000", [], "V 0 dev\nCALL 8c000300 1\nCA r4\nE\n")
            harness(ws, "8c000100", [], "V 0 dev\nCALL 8c000104 1\nCA r4\nE\n")
            rows = {"8c000000": {"status": "matched", "finished": "2026-11-02T00:00:00"},
                    "8c000100": {"status": "matched", "finished": "2026-11-02T00:00:00"}}
            g = lambda a, r: ua.gate(ws, a, ua.harness_calls(ws / "harness", a), r)  # noqa: E731
            self.assertEqual(g("8c000100", rows)["unsigned"], ["8c000104"])  # run-on, the next one unproven
            self.assertEqual(g("8c000000", rows)["rejudge"], ["8c000300"])  # judged before s1 was compared
            rj = {"8c000000": {"8c000300": "2026-11-08T00:00:00"}}  # args_marks --lift after a re-judge elsewhere
            hc = ua.gate(ws, "8c000000", ua.harness_calls(ws / "harness", "8c000000"), rows, rj)
            self.assertEqual(ua.unchecked(hc), [])
            rows["8c000000"]["finished"] = "2026-11-08T00:00:00"
            self.assertEqual(ua.unchecked(g("8c000000", rows)), [])  # judged after: checked
            self.assertEqual(ua.unchecked(g("8c000200", rows)), [])  # a pointer call: its CA line, as before
            idx, cr = compare.REG_INDEX, callee_drift.call_regs
            self.assertEqual(cr({}, 0x8C000300, ["r4"], idx, ws, caller="8c000000"), ["r4", "s1"])
            self.assertEqual(cr({}, 0x8C000300, ["r4"], idx, ws, caller="8c000200"), ["r4"])
            led = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))
            led["functions"]["8c000300"]["status"] = "failed"
            (ws / "state" / "ledger.json").write_text(json.dumps(led), encoding="utf-8")
            self.assertEqual(ua.unchecked(g("8c000000", rows)), ["8c000300"])  # signature no longer proven


if __name__ == "__main__":
    unittest.main()
