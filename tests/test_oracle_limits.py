"""Per-function oracle limits and the stored value of a listed hardware register (core/oracle_limits,
targets/dreamcast_sh4/hooks.py, TcCalls.limits, TcHw.atExit)."""
import importlib.util
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import oracle_limits  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"


def text(p):
    return p.read_text(encoding="utf-8")


def hooks():
    spec = importlib.util.spec_from_file_location("sh4_hooks", SH4 / "hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Arg(unittest.TestCase):
    def test_a_function_not_listed_has_no_arg(self):
        self.assertEqual(oracle_limits.arg({}, "8c0b1940"), "-")
        self.assertEqual(oracle_limits.arg({"oracle_limits": {"8c000000": {"calls": 8192, "why": "x"}}}, "8c0b1940"), "-")

    def test_a_listed_function_keeps_the_defaults_it_does_not_name(self):
        cfg = {"oracle_limits": {"8c0b1940": {"calls": 8192, "rets": 512, "why": "30 fades of 190 calls"}}}
        self.assertEqual(oracle_limits.arg(cfg, "8c0b1940"), "lim:8192_512_500000_0")

    def test_a_budget_is_that_functions_alone(self):
        cfg = {"ghidra_timeout_s": 900, "ghidra_kill_s": 950,
               "oracle_limits": {"8c0b1940": {"budget_s": 3000, "why": "x"}, "8c084ea0": {"calls": 8192, "why": "x"}}}
        self.assertIs(oracle_limits.run_cfg(cfg, "8c010000"), cfg)
        self.assertIs(oracle_limits.run_cfg(cfg, "8c084ea0"), cfg)
        run = oracle_limits.run_cfg(cfg, "8c0b1940")
        self.assertEqual(run["ghidra_timeout_s"], 3000)
        self.assertNotIn("ghidra_kill_s", run)
        self.assertEqual(cfg["ghidra_timeout_s"], 900)

    def test_refused(self):
        for e in ({"calls": 8192}, {"calls": 8193, "why": "x"}, {"calls": 100, "why": "x"}, {"rets": 1025, "why": "x"},
                  {"steps": 10 ** 9, "why": "x"}, {"call": 8192, "why": "x"}, {"calls": "8192", "why": "x"}, "8192"):
            with self.assertRaises(ValueError, msg=repr(e)):
                oracle_limits.arg({"oracle_limits": {"8c0b1940": e}}, "8c0b1940")


class Wiring(unittest.TestCase):
    def test_the_tops_are_the_runners_arrays(self):
        self.assertIn(f"#define TCRT_RETS {oracle_limits.RETS[1]} ", text(SH4 / "runtime" / "tc_rt_internal.h"))
        self.assertIn(f"#define MAX_CALLS {oracle_limits.CALLS[1]} ", text(SH4 / "runtime" / "tc_rt.c"))
        self.assertIn(f"#define RD_MAX_CALLS {oracle_limits.CALLS[1]} ", text(SH4 / "runtime_rd" / "rd_internal.h"))
        for f in (SH4 / "runtime" / "tc_fpu.c", SH4 / "runtime_rd" / "rd_sh4.c"):
            self.assertIn(f"#define MAX_FC {oracle_limits.CALLS[1]} ", text(f))

    def test_the_defaults_are_the_scripts(self):
        self.assertIn(f"static int CALL_LIMIT = {oracle_limits.CALLS[0]};", text(SCRIPTS / "TcOracle.java"))
        self.assertIn(f"static int RETS = {oracle_limits.RETS[0]};", text(SCRIPTS / "TcOracle.java"))
        self.assertIn(f"static int STEP_LIMIT = {oracle_limits.STEPS[0]};", text(SCRIPTS / "TcBase.java"))
        d = oracle_limits.DEFAULTS
        self.assertIn(f'{{"{d["calls"]}", "{d["rets"]}", "{d["steps"]}"}}', text(SCRIPTS / "TcCalls.java"))

    def test_every_prepare_sets_them(self):
        # a long-lived Ghidra (TcServe) keeps statics between prepares, and stepLimit was set when the object was made
        prepare = text(SCRIPTS / "TcPrepare.java")
        self.assertIn('TcCalls.limits(a.length > 14 ? a[14] : "-");', prepare)
        self.assertIn("stepLimit = TcBase.STEP_LIMIT;", prepare)

    def test_the_exit_log_reads_what_was_stored(self):
        # a read through TcFold serves a listed read's fixed value, which made every W line of a register listed both
        # ways the read value: a correct store failed and a store dropping the OR passed
        exit_ = text(SCRIPTS / "TcExit.java")
        self.assertRegex(exit_, r"atExit = true;\s+res\.writes\.add\(TcBase\.hex\(lo\)[^\n]+\s+[^\n]+atExit = false;")
        self.assertRegex(text(SCRIPTS / "TcHw.java"), r"byte\[\] read\(long off, int size\) \{\s+if \(atExit && written\(off, off \+ size\)\) return null;")


class Scope(unittest.TestCase):
    def test_only_listed_functions_are_in_scope(self):
        h = hooks()
        cfg = {"oracle_limits": {"8c0b1940": {"calls": 8192, "why": "x"}},
               "hw_ops": {"8c093f00": {"ops": ["read ff00001c", "write ff00001c"]},
                          "8c04f4c0": {"ops": ["read a05f689c", "ldc gbr"]},
                          "8c04f500": {"ops": ["read a05f689c", "write a05f6900"]}}}
        for addr, want in (("8c0b1940", True), ("8c093f00", True), ("8c04f4c0", False), ("8c04f500", False),
                           ("8c010000", False)):
            self.assertEqual(h.stamp_scope(cfg, addr, "listed_limits_or_rw"), want, addr)

    def test_an_unknown_scope_counts_as_in_scope(self):
        self.assertTrue(hooks().stamp_scope({}, "8c010000", "some_other_scope"))

    def test_the_target_lists_the_scope_with_the_current_hashes(self):
        import hashlib
        import json
        sc = json.loads(text(SH4 / "target.json"))["stamp_scopes"]
        files = next(s for s in sc if s["scope"] == "listed_limits_or_rw")["scripts"]
        self.assertEqual(sorted(files), ["TcBase.java", "TcCalls.java", "TcExit.java", "TcHw.java", "TcOracle.java",
                                         "TcPrepare.java"])
        for name, (before, after) in files.items():
            self.assertEqual(hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest(), after, name)
            self.assertTrue(re.fullmatch("[0-9a-f]{64}", before), name)


if __name__ == "__main__":
    unittest.main()
