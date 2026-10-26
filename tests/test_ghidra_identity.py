"""The oracle stamp's Ghidra line (core/oracle_stamp.ghidra_identity; the second waste audit's finding A): what decides
the install's output, not its folder.
1. The same install in two folders gives the same identity and the same harness stamp (another machine, a move).
2. A changed install changes both: another version or build, a changed language file (the SH-4 tcfix language
   installed over it), another decompiler, an extension's changed jar. The time comment of application.properties
   does not.
3. No install: '-'.
Run: python -W error tests/test_ghidra_identity.py (also from test_tooling.py).
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from core import oracle_stamp  # noqa: E402

PROPS = "#Fri Jun 05 13:17:06 EDT 2026\napplication.version=12.1.2\napplication.build.date.short=20260605\n"
LDEFS = ('<language_definitions><language id="SuperH4:LE:32:tcfix" slafile="SuperH4_tc_le.sla"/>'
         '</language_definitions>\n')
GC = '<language_definitions><language id="PowerPC:BE:32:Gekko_Broadway"/></language_definitions>\n'


def install(where):
    g = pathlib.Path(where) / "ghidra_12.1.2_PUBLIC" / "Ghidra"
    lang = g / "Processors" / "SuperH4" / "data" / "languages"
    lang.mkdir(parents=True)
    (g / "application.properties").write_text(PROPS, encoding="utf-8")
    (lang / "SuperH4_tc.ldefs").write_text(LDEFS, encoding="utf-8")
    (lang / "SuperH4_tc.sinc").write_text("define token ...\n", encoding="utf-8")
    (lang / "SuperH4_tc_le.sla").write_bytes(b"sla\x04compiled")
    (lang / "SuperH4.ldefs").write_text('<language_definitions><language id="SuperH4:LE:32:default"/>'
                                        "</language_definitions>\n", encoding="utf-8")
    dec = g / "Features" / "Decompiler" / "os" / "win_x86_64"
    dec.mkdir(parents=True)
    (dec / "decompile.exe").write_bytes(b"MZ decompiler")
    ext = g / "Extensions" / "GameCubeLoader"
    (ext / "data" / "languages").mkdir(parents=True)
    (ext / "lib").mkdir()
    (ext / "data" / "languages" / "ppc_gekko_broadway.ldefs").write_text(GC, encoding="utf-8")
    (ext / "lib" / "GameCubeLoader.jar").write_bytes(b"PK loader")
    (ext / "extension.properties").write_text("name=GameCubeLoader\nversion=12.1.2\n", encoding="utf-8")
    return g.parent


class Identity(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="gid_", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.a, self.b = install(self.d / "laptop1" / "Tools"), install(self.d / "D" / "elsewhere")

    def cfg(self, home, lang="SuperH4:LE:32:tcfix"):
        return {"ghidra_home": str(home), "ghidra_language": lang, "binary_sha256": "ab"}

    def stamp(self, home, lang="SuperH4:LE:32:tcfix"):
        return oracle_stamp.stamp_of(self.cfg(home, lang), "8c010080", {}, "-", None, 48, 16)

    def test_same_install_elsewhere(self):
        for lang in ("SuperH4:LE:32:tcfix", "PowerPC:BE:32:Gekko_Broadway"):
            self.assertEqual(oracle_stamp.ghidra_identity(self.cfg(self.a, lang)),
                             oracle_stamp.ghidra_identity(self.cfg(self.b, lang)))
            self.assertEqual(self.stamp(self.a, lang), self.stamp(self.b, lang))
        self.assertNotEqual(oracle_stamp.ghidra_identity(self.cfg(self.a)),
                            oracle_stamp.ghidra_identity(self.cfg(self.a, "PowerPC:BE:32:Gekko_Broadway")))

    def test_changed_install(self):
        g = self.b / "Ghidra"
        lang = g / "Processors" / "SuperH4" / "data" / "languages"
        ext = g / "Extensions" / "GameCubeLoader"
        base, gc = self.stamp(self.b), self.stamp(self.b, "PowerPC:BE:32:Gekko_Broadway")
        (g / "application.properties").write_text("#Sat Jun 06 09:00:00 EDT 2026\n" + PROPS.split("\n", 1)[1],
                                                  encoding="utf-8")
        self.assertEqual(self.stamp(self.b), base)  # only the time comment: the same build
        for change in (lambda: (g / "application.properties").write_text(PROPS.replace("12.1.2", "12.2"),
                                                                         encoding="utf-8"),
                       lambda: (lang / "SuperH4_tc.sinc").write_text("define token ... fixed\n", encoding="utf-8"),
                       lambda: (lang / "SuperH4_tc_le.sla").write_bytes(b"sla\x04recompiled"),
                       lambda: (g / "Features" / "Decompiler" / "os" / "win_x86_64" / "decompile.exe").write_bytes(
                           b"MZ decompiler 2")):
            change()
            oracle_stamp._GHIDRA.clear()  # a new process (one keeps its identity while the two stats hold)
            now = self.stamp(self.b)
            self.assertNotEqual(now, base)
            base = now
        (ext / "lib" / "GameCubeLoader.jar").write_bytes(b"PK loader 2")
        oracle_stamp._GHIDRA.clear()
        self.assertNotEqual(self.stamp(self.b, "PowerPC:BE:32:Gekko_Broadway"), gc)

    def test_reinstalled_language_seen_in_process(self):  # install_lang rewrites the .ldefs: a live process sees it
        lang = self.b / "Ghidra" / "Processors" / "SuperH4" / "data" / "languages"
        base = self.stamp(self.b)
        (lang / "SuperH4_tc.sinc").write_text("define token ... fixed\n", encoding="utf-8")
        (lang / "SuperH4_tc.ldefs").write_text(LDEFS + "\n", encoding="utf-8")
        self.assertNotEqual(self.stamp(self.b), base)

    def test_no_install(self):
        self.assertEqual(oracle_stamp.ghidra_identity({}), "-")
        self.assertEqual(oracle_stamp.ghidra_identity({"ghidra_home": str(self.d / "none")}), "-")


if __name__ == "__main__":
    unittest.main()
