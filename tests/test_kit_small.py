"""Two small port-kit checks (split from the port kit's main test file at its 400-line ratchet): tools/regress.py fails
a script whose frames cannot repeat (RegressUnstableFrames), and sdk/bu_vmu.c's file image functions are on only
when the game's header declares their type (BuFileImageGate). No compiler.

usage: python -W error tests/test_kit_small.py
"""
import pathlib
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))

import game_config  # noqa: E402


class RegressUnstableFrames(unittest.TestCase):
    """A pass past the renderer's autosort pool drops the translucent fragments the GPU appended last, which differ
    from run to run: tools/regress.py fails the script, since its frames cannot repeat."""
    def test_overflow_fails(self):
        # by path: the pipeline's own tools/regress.py has the same module name, and in a full suite run it can be
        # imported first
        import importlib.util
        spec = importlib.util.spec_from_file_location("kit_regress_t", KIT / "tools" / "regress.py")
        regress = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(regress)
        log = ("ta: list 2: polygon vertex type 0, isp e4000000 tsp 949800c0 (frame 3)\n"
               "pvr: a pass drew 1245066 translucent fragments, past the autosort pool of 1228800 (frame 6862)\n"
               "pvr: autosort pool of 1556332 fragments; the largest pass drew 1245066; 1 pass(es) overflowed\n")
        self.assertEqual(regress.unstable(log), [
            "pvr: a pass drew 1245066 translucent fragments, past the autosort pool of 1228800 (frame 6862)"])
        self.assertEqual(regress.unstable(log.splitlines()[2]), [])


class BuFileImageGate(unittest.TestCase):
    """sdk/bu_vmu.c's file image functions take the game's BUS_BACKUPFILEHEADER: port_config.h turns them on only
    when the game's header declares it (a game without it, which never calls them, still builds)."""
    CFG = {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
           "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}

    def test_gate(self):
        self.assertIn("#define PORT_BU_FILE_IMAGE 1", game_config.port_config_h(self.CFG, (), True))
        self.assertNotIn("PORT_BU_FILE_IMAGE", game_config.port_config_h(self.CFG))
        src = (KIT / "sdk" / "bu_vmu.c").read_text(encoding="utf-8")
        self.assertIn('#include "port_config.h"', src)
        gated = src[src.index("#ifdef PORT_BU_FILE_IMAGE"):]
        self.assertIn("buMakeBackupFileImage", gated)
        self.assertIn("buAnalyzeBackupFileImage", gated)


if __name__ == "__main__":
    unittest.main()
