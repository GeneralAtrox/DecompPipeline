"""The GameCube target's byte-match check (targets/gamecube_ppc/ppc_match.py): the ELF symbol reader, dtk's
symbols.txt, objdiff's match percent, the three verdicts (matched / equivalent / FAIL, with the check that gave each),
and the compile -> find the split object -> objdiff flow, with fake compiler and objdiff runs. Synthetic objects only
(no game data). Run: python -W error tests/test_gamecube_match.py (also from test_tooling.py).
"""
import json
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "targets" / "gamecube_ppc"))
import test_sync  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import ppc_match  # noqa: E402


def elf(symbols, big=True):
    """A relocatable 32-bit ELF with a .text section and `symbols` [(name, value, size, STT)] defined in it."""
    e = ">" if big else "<"
    shstr = b"\0.text\0.symtab\0.strtab\0.shstrtab\0"
    strtab = b"\0" + b"".join(n.encode() + b"\0" for n, *_ in symbols)
    text = b"\x4e\x80\x00\x20" * 4
    syms = b"\0" * 16
    off = 1
    for name, value, size, stt in symbols:
        syms += struct.pack(e + "IIIBBH", off, value, size, (1 << 4) | stt, 0, 1)
        off += len(name) + 1
    body = [text, syms, strtab, shstr]
    pos, offs = 0x34, []
    for b in body:
        offs.append(pos)
        pos += len(b)
    shoff = pos
    hdr = b"\x7fELF" + bytes([1, 2 if big else 1, 1]) + b"\0" * 9
    hdr += struct.pack(e + "HHIIIIIHHHHHH", 1, 20, 1, 0, 0, shoff, 0, 0x34, 0, 0, 40, 5, 4)
    sh = b"\0" * 40
    sh += struct.pack(e + "IIIIIIIIII", 1, 1, 6, 0, offs[0], len(text), 0, 0, 4, 0)
    sh += struct.pack(e + "IIIIIIIIII", 7, 2, 0, 0, offs[1], len(syms), 3, 1, 4, 16)
    sh += struct.pack(e + "IIIIIIIIII", 15, 3, 0, 0, offs[2], len(strtab), 0, 0, 1, 0)
    sh += struct.pack(e + "IIIIIIIIII", 23, 3, 0, 0, offs[3], len(shstr), 0, 0, 1, 0)
    return hdr + b"".join(body) + sh


class ElfSymbols(unittest.TestCase):
    def test_defined_functions_and_objects(self):
        s = ppc_match.elf_symbols(elf([("fn_80001000", 0, 16, 2), ("lbl_80002000", 8, 4, 1)]))
        self.assertEqual(s["fn_80001000"], {"section": ".text", "value": 0, "size": 16, "type": 2})
        self.assertEqual(s["lbl_80002000"]["type"], 1)

    def test_little_endian_and_not_elf(self):
        self.assertIn("f", ppc_match.elf_symbols(elf([("f", 0, 4, 2)], big=False)))
        with self.assertRaises(ValueError):
            ppc_match.elf_symbols(b"MZ\0\0" + b"\0" * 60)


class DtkSymbols(unittest.TestCase):
    TEXT = ("fn_80003100 = .init:0x80003100; // type:function size:0x40 scope:global\n"
            "OSReport = .text:0x8000ABCD; // type:function size:0x80\n"
            "lbl_80300000 = .data:0x80300000; // type:object size:0x4\n"
            "# a comment\n")

    def test_functions_only_by_address(self):
        t = ppc_match.dtk_symbols(self.TEXT)
        self.assertEqual(t, {0x80003100: "fn_80003100", 0x8000ABCD: "OSReport"})

    def test_symbol_for_falls_back_to_dtk_default(self):
        self.assertEqual(ppc_match.symbol_for(0x8000ABCD, ppc_match.dtk_symbols(self.TEXT)), "OSReport")
        self.assertEqual(ppc_match.symbol_for(0x801f58b8, {}), "fn_801F58B8")


class MatchPercent(unittest.TestCase):
    def test_snake_and_camel_and_missing(self):
        doc = {"left": {"symbols": [{"symbol": {"name": "a"}, "match_percent": 50.0},
                                    {"symbol": {"name": "fn_1"}, "match_percent": 100.0}]},
               "right": {"symbols": [{"symbol": {"name": "fn_1"}, "matchPercent": 100}]}}
        self.assertEqual(ppc_match.match_percent(doc, "fn_1"), 100.0)
        self.assertEqual(ppc_match.match_percent(doc, "a"), 50.0)
        self.assertIsNone(ppc_match.match_percent(doc, "b"))


class Grade(unittest.TestCase):
    def test_three_verdicts_and_the_check_behind_each(self):
        full, part = {"percent": 100.0}, {"percent": 87.5}
        self.assertEqual(ppc_match.grade("PASS", full), ("matched", "objdiff", None))
        self.assertEqual(ppc_match.grade("PASS", part), ("equivalent", "behaviour", None))
        self.assertEqual(ppc_match.grade("PASS", None), ("equivalent", "behaviour", None))
        self.assertEqual(ppc_match.grade("FAIL", part), ("FAIL", "behaviour", None))
        self.assertEqual(ppc_match.grade(None, part), ("FAIL", "objdiff", None))
        g, c, note = ppc_match.grade("FAIL", full)
        self.assertEqual((g, c), ("matched", "objdiff"))
        self.assertIn("oracle or harness defect", note)


class Flow(unittest.TestCase):
    """run() and attach() with a fake runner standing in for mwcceppc and objdiff-cli."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "obj" / "auto").mkdir(parents=True)
        (self.tmp / "obj" / "auto" / "unit.o").write_bytes(elf([("fn_80001000", 0, 16, 2)]))
        (self.tmp / "obj" / "other.o").write_bytes(elf([("fn_80002000", 0, 16, 2)]))
        (self.tmp / "symbols.txt").write_text("", encoding="utf-8")
        mw = self.tmp / "mw" / "GC" / "1.3.2"
        mw.mkdir(parents=True)
        (mw / "mwcceppc.exe").write_bytes(b"")
        self.cfg = {"mwcc_root": str(self.tmp / "mw"), "objdiff_cli": "objdiff-cli.exe",
                    "match": {"compiler": "GC/1.3.2", "cflags": ["-O4,p"], "target_objects": "obj",
                              "symbols": "symbols.txt", "src_dir": "src"}}
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runner(self, define="fn_80001000", percent=100.0, compile_rc=0):
        def run(cmd, timeout, cwd):
            self.calls.append(cmd)
            if cmd[0].endswith("mwcceppc.exe"):
                if compile_rc:
                    return compile_rc, "work.c line 3: undefined identifier 'x'\n", None
                pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(elf([(define, 0, 16, 2)]))
                return 0, "", None
            out = pathlib.Path(cmd[cmd.index("-o") + 1])
            out.write_text(json.dumps({"left": {"symbols": [{"symbol": {"name": define}, "match_percent": percent}]}}),
                           encoding="utf-8")
            return 0, "", None
        return run

    def test_settings_none_without_match_object(self):
        self.assertIsNone(ppc_match.settings({}, self.tmp))

    def test_full_match_commands_and_result(self):
        st = ppc_match.settings(self.cfg, self.tmp)
        res = ppc_match.run(st, "80001000", self.tmp / "a.c", self.tmp / "b", runner=self.runner())
        self.assertEqual((res["stage"], res["percent"], res["symbol"]), ("done", 100.0, "fn_80001000"))
        self.assertTrue(res["target_object"].endswith("unit.o"))
        cc, od = self.calls
        self.assertEqual(cc[1], "-O4,p")
        self.assertIn(str(ppc_match.INCLUDE), cc)
        self.assertEqual(cc[cc.index("-i") + 1], str(self.tmp.resolve()))  # the source's own folder first
        self.assertEqual(od[:2], ["objdiff-cli.exe", "diff"])
        self.assertEqual(od[od.index("-1") + 1], res["target_object"])

    def test_compile_error_wrong_name_missing_target(self):
        st = ppc_match.settings(self.cfg, self.tmp)
        r = ppc_match.run(st, "80001000", self.tmp / "a.c", self.tmp / "b", runner=self.runner(compile_rc=1))
        self.assertEqual(r["stage"], "compile")
        self.assertIn("undefined identifier", r["errors"][0])
        r = ppc_match.run(st, "80001000", self.tmp / "a.c", self.tmp / "b", runner=self.runner(define="other"))
        self.assertEqual(r["stage"], "target")
        self.assertIn("name the function fn_80001000", r["errors"][0])
        r = ppc_match.run(st, "80003000", self.tmp / "a.c", self.tmp / "b", runner=self.runner())
        self.assertIn("no split object defines fn_80003000", r["errors"][0])

    def test_attach_grades_and_finds_the_native_source(self):
        work = self.tmp / "work.c"
        work.write_text("", encoding="utf-8")
        res = ppc_match.attach(self.cfg, self.tmp, "80001000", work, {"verdict": "PASS"}, self.tmp / "b",
                               runner=self.runner())
        self.assertNotIn("match", res)  # no original-style C: behaviour only
        self.assertEqual((res["grade"], res["check"]), ("equivalent", "behaviour"))
        (self.tmp / "work.mw.c").write_text("", encoding="utf-8")
        res = ppc_match.attach(self.cfg, self.tmp, "80001000", work, {"verdict": "FAIL"}, self.tmp / "b",
                               runner=self.runner())
        self.assertEqual((res["grade"], res["check"], res["verdict"]), ("matched", "objdiff", "PASS"))
        self.assertIn("grade_note", res)
        res = ppc_match.attach(self.cfg, self.tmp, "80001000", work, {"verdict": "PASS"}, self.tmp / "b",
                               runner=self.runner(percent=93.0))
        self.assertEqual((res["grade"], res["match"]["percent"]), ("equivalent", 93.0))

    def test_attach_reports_a_missing_compiler(self):
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "80001000.c").write_text("", encoding="utf-8")
        cfg = dict(self.cfg, mwcc_root=None)
        res = ppc_match.attach(cfg, self.tmp, "80001000", None, {"verdict": "FAIL"}, self.tmp / "b",
                               runner=self.runner())
        self.assertEqual(res["match"]["stage"], "error")
        self.assertIn("mwcc_root", res["match"]["errors"][0])
        self.assertEqual(res["grade"], "FAIL")


if __name__ == "__main__":
    unittest.main()
