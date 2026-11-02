"""The "vmx" stamp scope (target.json "stamp_scopes"): TcVectors' extra tries (VMX_TRIES) reach only the functions
with vector words, so every other function keeps the stamp of its harness. Checked: which words count as vector
words (a superset of TcBase.vmxOpAt: opcodes 4, 5, 6 and the vector loads and stores in opcode 31), that the entry's
"after" hash is TcVectors.java's own (any later edit of the file must update the entry, or every stamp moves), and
the stamps of a vector and a scalar function around the change.
usage: python tests/test_x360_scope_vmx.py
"""
import hashlib
import json
import pathlib
import shutil
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360, tmpdir  # noqa: E402

from core import oracle_stamp, paths, target_hooks  # noqa: E402
import x360_scope  # noqa: E402

WORDS = {"vadduwm": 0x10000080, "vaddfp128": 0x14000010, "lvx": 0x7C0318CE, "stvx": 0x7C03C1CE,
         "addi": 0x38600001, "fmuls": 0xEC2100B2, "lwz": 0x81630000, "blr": 0x4E800020, "or": 0x7C611B78}


class Scope(unittest.TestCase):
    def test_vector_words(self):
        self.assertEqual({n: x360_scope.vector_word(w) for n, w in WORDS.items()},
                         {"vadduwm": True, "vaddfp128": True, "lvx": True, "stvx": True, "addi": False,
                          "fmuls": False, "lwz": False, "blr": False, "or": False})

    def test_entry_names_the_current_script(self):
        # a scoped change names today's file; none is in force after the oracle changes that reach every function
        for e in json.loads((X360 / "target.json").read_text(encoding="utf-8"))["stamp_scopes"]:
            self.assertEqual(e["scope"], "vmx")
            before, after = e["scripts"]["TcVectors.java"]
            self.assertEqual(after, hashlib.sha256((X360 / "ghidra_scripts" / "TcVectors.java").read_bytes()).hexdigest())
            self.assertNotEqual(before, after)

    def test_stamps(self):
        d = tmpdir("x360vmx_")
        try:
            # 0x82000000: vadduwm; 0x82000004: addi
            (d / "image.bin").write_bytes(bytes.fromhex("10000080" "38600001"))
            (d / "bodies.txt").write_text("82000000 82000000 82000004\n82000004 82000004 82000008\n", encoding="utf-8")
            cfg = {"binary_path": d / "image.bin", "load_address": "0x82000000", "binary_sha256": "ab"}
            with mock.patch.object(paths, "FUNCTION_BODIES", d / "bodies.txt"), \
                    mock.patch.object(paths, "PROJECT", {"target": "xbox360_ppc"}), \
                    mock.patch.object(target_hooks, "get", return_value=x360_scope.in_scope):
                (d / "TcVectors.java").write_text("old", encoding="utf-8")
                scripts = {"TcVectors.java": d / "TcVectors.java"}
                before = {a: oracle_stamp.stamp_of(cfg, a, scripts, "-", None, 48, 48) for a in ("82000000", "82000004")}
                (d / "TcVectors.java").write_text("new!", encoding="utf-8")
                sha = {t: hashlib.sha256(t.encode()).hexdigest() for t in ("old", "new!")}
                sc = dict(cfg, stamp_scopes=[{"scope": "vmx", "scripts": {"TcVectors.java": [sha["old"], sha["new!"]]}}])
                now = {a: oracle_stamp.stamp_of(sc, a, scripts, "-", None, 48, 48) for a in before}
                self.assertNotEqual(now["82000000"], before["82000000"])   # the vector function: re-prepared
                self.assertEqual(now["82000004"], before["82000004"])      # the scalar one: kept
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
