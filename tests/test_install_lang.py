"""targets/dreamcast_sh4/ghidra_lang/install_lang.py, offline (no Ghidra), from the outside user's feedback of 2026-11-13:
1. sleigh output holding byte 0x81 (German 'pause' line) is decoded without error and the real failure is reported;
2. sleigh gets stdin=DEVNULL (sleigh.bat pauses on error) and a None stdout never gets indexed;
3. the BSR patch is applied to Ghidra 12.1's text, skipped when the stock file has PR = inst_next (12.2), and fails
   loudly when neither text is there;
4. a failed run removes the empty .sla.lock it leaves behind.
Run: python -W error tests/test_install_lang.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "targets" / "dreamcast_sh4" / "ghidra_lang")]
import install_lang as il  # noqa: E402


def fake_sinc(bsr):
    fv = ":fipr FVN_2t,FVM_2t is x {\n\tlocal a = FVN_2t &:4 FVN_2t +  0);\n\tlocal b = FVM_2t &:4 FVM_2t +  4);\n}\n"
    ft = ":ftrv XMTRX,FVN_2t is x {\n\tlocal a = FVN_2t &:4 FVN_2t +  8);\n}\n"
    return ("\n:frchg is x {\n" + il.FRCHG_OLD + "\n}\n:fschg is x {\n" + il.FSCHG_OLD + "\n}\n:fpchg is x {\n"
            + il.FPCHG_OLD + "\n" + bsr + "\n}\n" + fv + ft)


class Bsr(unittest.TestCase):
    def test_old_text_patched(self):
        self.assertIn("PR = inst_next;", il.patch_bsr(il.BSR_OLD))

    def test_stock_fix_skipped(self):
        self.assertEqual(il.patch_bsr("a\n" + il.BSR_NEW + "\nb"), "a\n" + il.BSR_NEW + "\nb")

    def test_neither_fails(self):
        with self.assertRaises(SystemExit):
            il.patch_bsr(":bsr something else {")


class Sleigh(unittest.TestCase):
    def test_bytes_and_stdin(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 1, b"Dr\x81cken Sie eine Taste", None)

        with mock.patch("subprocess.run", run):
            self.assertEqual(il.run_sleigh("sleigh.bat", "x.slaspec"), 1)
        self.assertIs(seen["stdin"], subprocess.DEVNULL)
        self.assertNotIn("text", seen)

    @unittest.skipUnless(sys.platform == "win32", "uses a .bat")
    def test_real_fake_sleigh_byte_0x81(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="il_"))
        bat = d / "sleigh.bat"
        bat.write_bytes(b"@echo off\r\necho Dr\x81cken Sie eine Taste\r\nexit /b 1\r\n")
        self.assertEqual(il.run_sleigh(bat, d / "x.slaspec"), 1)

    def test_failed_install_clears_empty_lock_and_exits(self):
        home = pathlib.Path(tempfile.mkdtemp(prefix="il_"))
        lang = home / "Ghidra/Processors/SuperH4/data/languages"
        lang.mkdir(parents=True)
        (lang / "SuperH4.sinc").write_text(fake_sinc(il.BSR_NEW), encoding="utf-8")
        (lang / "SuperH4_tc_le.sla.lock").write_bytes(b"")
        with mock.patch.object(il, "run_sleigh", lambda *a: 1), mock.patch.object(sys, "argv", ["x", str(home)]):
            with self.assertRaises(SystemExit) as e:
                il.main()
        self.assertIn("sleigh compile failed", str(e.exception))
        self.assertFalse((lang / "SuperH4_tc_le.sla.lock").exists())


if __name__ == "__main__":
    unittest.main()
