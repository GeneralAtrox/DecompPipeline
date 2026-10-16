"""Batch 28: the function under test's own address in the readable runner (readable_abi.thunk_c names it to the
runtime: rd_set_self; rd_mem.c and rd_veh.c report that host address as the guest one), and the oracle's call targets
from the inputs (TcCalls.java: no unit here, a Ghidra script; tests/patterns.json tc-call-target-8c08d48c and
rds-self-addr-8c06dc28 judge both end to end).
Run: python -W error tests/test_batch28.py (also from tests/test_tooling.py)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import A, B  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import readable, readable_abi  # noqa: E402

RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "targets/dreamcast_sh4/runtime_rd"


def thunk(addr, proto):
    reg = readable.Registry.empty({"functions": {addr: {"proto": proto}}})
    meta = {"addr": addr, "ret": "s32", "callees": [], "params": [
        {"name": "param_1", "kind": "u32", "storage": "r4", "stack_offset": -1}]}
    return readable_abi.thunk_c(reg, addr, meta)


class SelfAddress(unittest.TestCase):
    def test_thunk_names_the_function_under_test(self):
        text = thunk(A, "s32 Lib_InstallHooks(u8 *player)")
        self.assertIn("void rd_set_self(u32 host, u32 guest);", text)
        self.assertIn("rd_set_self((u32)(uintptr_t)&Lib_InstallHooks, 0x8c010000u);", text)
        self.assertLess(text.index("rd_set_self((u32)"), text.index("Lib_InstallHooks(("))  # before the call

    def test_the_guest_address_is_the_function_own(self):
        text = thunk(B, "s32 Lib_InstallHooks(u8 *player)")
        self.assertIn("&Lib_InstallHooks, 0x8c010100u);", text)
        self.assertNotIn("0x8c010000u);", text)

    def test_runtime_maps_every_reported_word(self):
        mem = (RUNTIME / "rd_mem.c").read_text(encoding="utf-8")
        veh = (RUNTIME / "rd_veh.c").read_text(encoding="utf-8")
        self.assertIn("rd_self_guest(r0)", mem)  # the result
        self.assertIn("rd_self_guest(w)", mem)  # stored words
        self.assertIn("rd_abi_reset", veh)
        self.assertGreaterEqual(veh.count("rd_self_guest("), 4)  # its definition and r, st, lo of a call record


if __name__ == "__main__":
    unittest.main()
