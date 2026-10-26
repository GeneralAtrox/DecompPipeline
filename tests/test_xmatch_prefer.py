"""xproject.py match: of several exact twins, OLD's matched ones first; never a function with itself; stage --only.
Spawn ovl holds two copies of a division helper: 8c5f621c (skipped) and 8c94aeec (matched). Address order took the
skipped one, so gend 8c564b14 and ovl 8c5f621c got no import.
1. match (the CLI) pairs NEW's copy with OLD's matched twin and its own pool word; stage --only writes that import
   and leaves the others in import/ alone; stage refuses an OLD that import.json was not matched against;
2. a project matched against itself: a function is never its own twin (exact or near), a twin still pairs;
3. stage never rewrites the import of a function NEW has matched (as restage), --only included.
Run: python -W error tests/test_xmatch_prefer.py (also from test_tooling.py).
"""
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from core import xmatch  # noqa: E402

CODE = (0xD101, 0x000B, 0x6012, 0x0009)  # mov.l @(1,PC),r1 (the pool word at +8); rts; mov.l @r1,r0; nop
OTHER = (0xE001, 0x000B, 0x0009, 0x0009)  # mov #1,r0; rts; nop; nop
C = "u32 fn_{0}(void)\n{{\n    return rd32(0x{1:08x}u);\n}}\n"


def workspace(root, name, fns, ledger=None, matched=()):
    """fns: {addr: (code words, pool word or None)}, each 16 bytes apart in an image at 8c010000."""
    ws = pathlib.Path(root) / name
    (ws / "state").mkdir(parents=True)
    img = bytearray(0x100)
    for a, (code, pool) in fns.items():
        off = int(a, 16) - 0x8C010000
        struct.pack_into("<4H", img, off, *code)
        if pool is not None:
            struct.pack_into("<I", img, off + 8, pool)
    (ws / "game.bin").write_bytes(bytes(img))
    (ws / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name": name, "binary": "game.bin",
                                                 "game_end": "8c010100"}), encoding="utf-8")
    (ws / "state" / "catalog.json").write_text(json.dumps({"functions": [{"addr": a, "bytes": 8} for a in fns]}),
                                               encoding="utf-8")
    (ws / "state" / "code_ranges.txt").write_text("".join(f"{a} {int(a, 16) + 8:08x}\n" for a in fns), encoding="utf-8")
    (ws / "state" / "ledger.json").write_text(json.dumps({"functions": ledger or {}}), encoding="utf-8")
    (ws / "matched").mkdir()
    for a in matched:
        (ws / "matched" / f"{a}.c").write_text(C.format(a, fns[a][1]), encoding="utf-8")
    return ws


class Prefer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xmprefer_", dir=TOOL / "tests" / ".work")
        # OLD (ovl): 8c010000 skipped and 8c010020 matched share CODE; 8c010040 differs
        self.old = workspace(self.tmp.name, "old", {"8c010000": (CODE, 0x8C0A0000), "8c010020": (CODE, 0x8C0A0100),
                                                    "8c010040": (OTHER, None)},
                             {"8c010000": {"status": "skipped"}, "8c010020": {"status": "matched"},
                              "8c010040": {"status": "matched"}}, matched=("8c010020",))
        # NEW (gend): two more copies, each with its own pool word
        self.new = workspace(self.tmp.name, "new", {"8c010040": (CODE, 0x8C0A0300), "8c010060": (CODE, 0x8C0A0400)})

    def tearDown(self):
        self.tmp.cleanup()

    def run_x(self, old, *args):
        return subprocess.run([sys.executable, str(TOOL / "xproject.py"), "--project", str(self.new), "--from",
                               str(old), *args], capture_output=True, text=True, timeout=120)

    def test_matched_twin_first_and_stage_only(self):
        p = self.run_x(self.old, "match")
        self.assertEqual(p.returncode, 0, p.stderr)
        m = json.loads((self.new / "state" / "import.json").read_text(encoding="utf-8"))["matches"]
        self.assertEqual(m["8c010040"], {"from": "8c010020", "kind": "exact", "ratio": 1.0,
                                         "pairs": {"8c0a0100": "8c0a0300"}})  # address order: the skipped 8c010000
        keep = "/* staged from the first source */\n"
        (self.new / "import").mkdir()
        (self.new / "import" / "8c010060.c").write_text(keep, encoding="utf-8")
        p = self.run_x(self.old, "stage", "--only", "8c010040")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("staged 1 file(s)", p.stdout)
        text = (self.new / "import" / "8c010040.c").read_text(encoding="utf-8")
        self.assertIn("u32 fn_8c010040(void)", text)
        self.assertIn("rd32(0x8c0a0300u)", text)
        self.assertEqual((self.new / "import" / "8c010060.c").read_text(encoding="utf-8"), keep)
        p = self.run_x(self.new, "stage")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("was matched against", p.stderr)

    def test_stage_keeps_matched(self):
        # as restage: a function NEW has matched keeps its import (the C its PASS was judged on, or none)
        (self.new / "state" / "ledger.json").write_text(json.dumps({"functions": {"8c010040": {"status": "matched"},
                                                                                  "8c010060": {"status": "queued"}}}),
                                                        encoding="utf-8")
        self.assertEqual(self.run_x(self.old, "match").returncode, 0)
        keep = "/* the import 8c010040 was matched with */\n"
        (self.new / "import").mkdir()
        (self.new / "import" / "8c010040.c").write_text(keep, encoding="utf-8")
        p = self.run_x(self.old, "stage")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("staged 1 file(s)", p.stdout)
        self.assertIn("1 matched, kept", p.stdout)
        self.assertEqual((self.new / "import" / "8c010040.c").read_text(encoding="utf-8"), keep)
        self.assertIn("u32 fn_8c010060(void)", (self.new / "import" / "8c010060.c").read_text(encoding="utf-8"))
        p = self.run_x(self.old, "stage", "--only", "8c010040")  # --only does not override it
        self.assertIn("staged 0 file(s)", p.stdout)
        self.assertEqual((self.new / "import" / "8c010040.c").read_text(encoding="utf-8"), keep)

    def test_never_itself(self):
        old = xmatch.Image(self.old)
        m = xmatch.match(old, xmatch.Image(self.old), prefer={"8c010020", "8c010040"})
        self.assertEqual(m["8c010000"]["from"], "8c010020")  # its matched twin
        self.assertEqual(m["8c010020"]["from"], "8c010000")  # the only twin left: never itself
        self.assertNotIn("8c010040", m)  # no twin: no exact, and not its own near match either
        self.assertEqual(xmatch.match(old, xmatch.Image(self.new))["8c010040"]["from"], "8c010000")  # no prefer: order


if __name__ == "__main__":
    unittest.main()
