"""Scope list, process-bound parts (core.paths binds one project per process, so each case runs a child process on
a fixture workspace): the orchestrator's eligible set, tiers and overview with extras; the catalog fixups' game-range
arguments unchanged by the key (the catalog cannot change); decomp.py audit's extra rows; tools/final_audit.py with
extras open (INCOMPLETE), failing (FAIL) and done (PASS). The final audit compiles for real (skipped without Visual
Studio), as tests/test_batch19.py's.

usage: python -W error tests/test_scope_runs.py   (python tests/test_tooling.py runs these too)
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_batch19 import AUDIT_RUNNER, HAS_VS, audit_ws, src  # noqa: E402
from test_sync import TMP, TOOL, meta, put  # noqa: E402

STATS = {k: 0 for k in ("bytes", "insns", "blocks", "cyclo", "loops", "callees", "callers", "indirect_calls",
                        "jump_tables", "fpu", "fpu_special", "fp_mode", "hw_refs", "sys_ops", "globals", "func_ptrs")}
G0, G1, X1, SDK, X2 = "8c010000", "8c010040", "8c070000", "8c070040", "8c070080"

ORCH = """
import json, sys
sys.path[:0] = [{tool!r}]
from core import ghidra, orch, paths
o = orch.Orchestrator()
print(json.dumps({{"functions": sorted(o.functions), "extras": sorted(o.extras), "open": o._open(),
                  "tiers": {{a: f["tier"] for a, f in sorted(o.functions.items())}}, "overview": o.overview(),
                  "fixups": [str(x).replace(str(paths.ROOT), "WS") for x in ghidra._fixups()]}}))
"""


def child(ws, code, *args):
    r = subprocess.run([sys.executable, "-W", "error", *(["-c", code] if code else []), *args],
                       env=dict(os.environ, DECOMP_PROJECT=str(ws)), capture_output=True, text=True, timeout=600)
    return r


def orch_ws(name, extras):
    """Game G0 (matched) and G1 (hardware: excluded); extras X1 and X2 (privileged op: excluded); SDK between."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix=name, dir=TMP))
    pj = {"name": "scope fixture", "target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin",
          "game_end": "8c068000", "priority": []}
    put(ws / "project.json", dict(pj, extra_functions=extras) if extras else pj)
    put(ws / "state/catalog.json", {"functions": [dict(STATS, addr=G0, insns=10), dict(STATS, addr=G1, hw_refs=1),
                                                  dict(STATS, addr=X1, insns=12, bytes=40),
                                                  dict(STATS, addr=SDK, insns=5),
                                                  dict(STATS, addr=X2, sys_ops=1)]})
    put(ws / "state/ledger.json", {"loop": False, "functions": {G0: {"addr": G0, "status": "matched",
                                                                     "attempts": []}}})
    return ws


class Orchestrator(unittest.TestCase):
    def run_orch(self, ws):
        r = child(ws, ORCH.format(tool=str(TOOL)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.splitlines()[-1])

    def test_extras_are_eligible_with_tiers(self):
        got = self.run_orch(orch_ws("orch", [X1, X2]))
        self.assertEqual(got["functions"], [G0, G1, X1, X2])
        self.assertEqual(got["extras"], [X1, X2])
        self.assertEqual(got["tiers"], {G0: "easy", G1: "excluded", X1: "easy", X2: "excluded"})  # exclusion applies
        self.assertEqual(got["open"], [X1])  # queued: G0 is matched, G1 and X2 excluded
        ov = got["overview"]
        self.assertEqual((ov["game_functions"], ov["extra_functions"], ov["extra_eligible"], ov["extra_matched"]),
                         (2, 2, 1, 0))
        self.assertEqual((ov["eligible"], ov["sdk_functions"], ov["remaining"], ov["matched"]), (2, 1, 1, 1))
        self.assertEqual(ov["tiers"], {"easy": 2, "excluded": 2})
        self.assertEqual(ov["bytes_total"], 40)

    def test_control_without_the_key_and_the_catalog_arguments(self):
        with_key = self.run_orch(orch_ws("orchk", [X1, X2]))
        got = self.run_orch(orch_ws("orch0", None))
        self.assertEqual((got["functions"], got["extras"], got["open"]), ([G0, G1], [], []))
        self.assertEqual(got["overview"]["sdk_functions"], 3)
        self.assertFalse([k for k in got["overview"] if k.startswith("extra")])  # the overview as before
        self.assertEqual(with_key["fixups"], got["fixups"])  # TcRuns/TcCont get the game ranges: catalog unchanged
        self.assertIn("8c010000-8c068000", got["fixups"])


class Audit(unittest.TestCase):
    def test_extra_rows(self):
        ws = orch_ws("aud", [X1, X2])
        r = child(ws, None, str(TOOL / "decomp.py"), "audit", "--project", str(ws), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout)[str(ws)]
        self.assertEqual(res["extra"], {"open": 1, "excluded": 1})
        self.assertEqual(res["pending"], {"excluded": 2, "not started": 1})
        r = child(ws, None, str(TOOL / "decomp.py"), "audit", "--project", str(ws))
        self.assertRegex(r.stdout, r"extra: open +1")
        ws0 = orch_ws("aud0", None)
        r = child(ws0, None, str(TOOL / "decomp.py"), "audit", "--project", str(ws0))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("extra", r.stdout)  # control: no rows without the key


XA, XB, XSDK = "8c010100", "8c010140", "8c010180"  # two extras and an SDK function after game_end 8c010100

STAMP = """
import sys
sys.path[:0] = [{tool!r}]
from core import oracle_fresh, paths, readable
st = oracle_fresh.Stamper(paths.config())
(readable.INCLUDE / 'game.h').write_text(readable.game_h(readable.Registry()))
print(' '.join(st.today(x) for x in {funcs!r}))
"""


def add_extra(ws, addr, name):
    """Matched, with harness meta, a registry name and a passing-by-construction readable file."""
    led = json.loads((ws / "state/ledger.json").read_text(encoding="utf-8"))
    led["functions"][addr] = {"status": "matched"}
    put(ws / "state/ledger.json", led)
    names = json.loads((ws / "readable/names/S02.json").read_text(encoding="utf-8"))
    names["functions"][addr] = {"proto": f"u32 {name}(u32 x)"}
    put(ws / "readable/names/S02.json", names)
    put(ws / f"readable/src/{addr}.c", src(name))
    put(ws / f"matched/{addr}.c", "/* raw */\n")
    put(ws / f"harness/{addr}/meta.json", meta(addr, f"u32 fn_{addr}(u32 param_r4)", [("param_r4", "r4")]))
    rows = json.loads((ws / "state/readable.json").read_text(encoding="utf-8"))
    put(ws / "state/readable.json", dict(rows, **{addr: {"status": "readable"}}))


def stamp(ws):
    led = json.loads((ws / "state/ledger.json").read_text(encoding="utf-8"))["functions"]
    funcs = sorted(a for a, r in led.items() if r["status"] == "matched")
    r = child(ws, STAMP.format(tool=str(TOOL), funcs=funcs))
    for x, s in zip(funcs, r.stdout.split()):
        m = json.loads((ws / f"harness/{x}/meta.json").read_text(encoding="utf-8"))
        put(ws / f"harness/{x}/meta.json", dict(m, oracle_stamp=s))


def extras_ws():
    ws, funcs = audit_ws()
    (ws / "game.bin").write_bytes(bytes(0x200))
    pj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    put(ws / "project.json", dict(pj, extra_functions=[XA, XB]))
    cat = json.loads((ws / "state/catalog.json").read_text(encoding="utf-8"))
    put(ws / "state/catalog.json", {"functions": cat["functions"] + [dict(STATS, addr=x) for x in (XA, XB, XSDK)]})
    sysj = json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))
    put(ws / "state/systems.json", {"systems": sysj["systems"] + [{"id": "S02", "hint": "extra",
                                                                   "functions": [XA, XB]}]})
    with open(ws / "state/function_bodies.txt", "a", encoding="utf-8") as f:
        f.write("".join(f"{x} {x} {int(x, 16) + 0x40:08x}\n" for x in (XA, XB, XSDK)))
    put(ws / "readable/names/S02.json", {"functions": {}, "globals": {}})
    add_extra(ws, XA, "Eng_Run")
    stamp(ws)
    return ws


@unittest.skipUnless(HAS_VS, "Visual Studio not installed")
class FinalAuditExtras(unittest.TestCase):
    def audit(self, ws, runner=AUDIT_RUNNER):
        code = runner.format(tool=str(TOOL), tools=str(TOOL / "tools"), ws=str(ws),
                             script=str(TOOL / "tools" / "final_audit.py"))
        r = child(ws, code)
        return r, r.stdout.splitlines()

    def test_open_failing_and_done(self):
        ws = extras_ws()
        r, lines = self.audit(ws)
        for name in ("oracle", "headers", "verify", "coverage", "raw names", "library", "completeness",
                     "consistency", "excluded"):
            self.assertTrue(any(ln.startswith(f"PASS {name}:") for ln in lines), (name, r.stdout, r.stderr))
        self.assertIn("2/2 pass (N = 3 catalog game functions - 1 excluded hw/sys; 2 matched)", r.stdout)
        self.assertIn("extra functions: 1/2 pass, 1 open (no readable file)", r.stdout)
        self.assertIn("1 open extra function(s) not counted", r.stdout)
        self.assertIn("extra functions: 1/1 files clean", r.stdout)
        self.assertIn("INFO extras: 1/2 extra function(s) pass verify; open 1 (1 not matched, 0 matched without a "
                      "readable file); 1 matched", r.stdout)
        self.assertTrue(any(ln.startswith("VERDICT INCOMPLETE") and "extra functions are open" in ln
                            for ln in lines), r.stdout)
        self.assertEqual(r.returncode, 1)
        detail = next((ws / "state").glob("final_audit-*.txt")).read_text(encoding="utf-8")
        self.assertIn("extra functions open, not matched (1): 8c010140", detail)
        self.assertIn("extra functions (project.json extra_functions, outside the game ranges): 2 of 2 listed",
                      detail)  # completeness: their own line

        failing = AUDIT_RUNNER.replace('{{"verdict": "PASS"', f'{{{{"verdict": "FAIL" if addr == "{XA}" else "PASS"')
        r, lines = self.audit(ws, failing)
        self.assertTrue(any(ln.startswith("FAIL extras:") and f"FAILING 1: {XA}" in ln for ln in lines), r.stdout)
        self.assertTrue(any(ln.startswith("VERDICT FAIL") for ln in lines), r.stdout)

        add_extra(ws, XB, "Eng_Two")
        stamp(ws)
        r, lines = self.audit(ws)
        self.assertTrue(any(ln.startswith("PASS extras: 2/2 extra function(s) pass verify; open 0") for ln in lines),
                        r.stdout)
        self.assertTrue(any(ln.startswith("VERDICT PASS") for ln in lines), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0)

    def test_control_without_the_key(self):
        ws, _ = audit_ws()
        stamp(ws)
        r, lines = self.audit(ws)
        self.assertTrue(any(ln.startswith("VERDICT PASS") for ln in lines), r.stdout + r.stderr)
        self.assertFalse([ln for ln in lines if " extras:" in ln or "extra functions" in ln], r.stdout)


if __name__ == "__main__":
    unittest.main(warnings="error")
