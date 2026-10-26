"""The targeted regress gate (tools/regress.py --targeted; tools/regress_targets.py; the harness cache of
tools/regress_project.py).
1. a changed file's class comes from the gate's import graph: the oracle path is prepare, the raw judge and runtime
   judge, the readable judge and runtime readable, the gate's own files and unknown files gate, the rest none;
2. a file under targets/T/ affects only T; config.json and target.json are classed by the keys that changed;
3. the prepare code hash changes with a prepare module and not with a judge module;
4. the harness cache: an entry is written once (with its packet), read back whole, and each group keeps two;
5. regress_project judges only the readable items for "items": "readable", and reuses a cached harness only for
   "reuse_cache" with the same stamp and prepare hash; regress.py passes --catalog to every targeted project.
Run: python -W error tests/test_regress_targets.py (also from test_tooling.py).
"""
import json
import pathlib
import shutil
import sys
import time
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import regress_project as rp  # noqa: E402
import regress_targets as rt  # noqa: E402

TARGETS = ["dreamcast_sh4", "gamecube_ppc"]


def kinds(*files, old_text=None, root=TOOL):
    return {f: (k, t) for f, k, t in rt.classify(list(files), root=root, old_text=old_text)}


class RegressTargets(unittest.TestCase):
    def test_classes(self):
        got = kinds("core/compare.py", "core/ghidra_serve/TcServe.java", "core/lint.py", "core/check.py",
                    "core/readable_lint.py", "core/ghidra.py",
                    "core/oracle_stamp.py", "core/paths.py", "core/agent.py", "refine.py", "tools/usage_report.py",
                    "tools/regress.py", "docs/PROOF_GATES.md", "tests/test_scope.py", "port/runtime/x.c",
                    "somewhere/new.txt")
        # compare.py: core.ghidra imports its stack constants (FRAME_MAX, SP, STACK_LO), so it is prepare code too
        want = {"core/compare.py": "prepare", "core/ghidra_serve/TcServe.java": "prepare", "core/lint.py": "judge",
                "core/check.py": "judge",
                "core/readable_lint.py": "readable", "core/ghidra.py": "prepare", "core/oracle_stamp.py": "prepare",
                "core/paths.py": "prepare", "core/agent.py": "none", "refine.py": "none",
                "tools/usage_report.py": "none", "tools/regress.py": "gate", "docs/PROOF_GATES.md": "none",
                "tests/test_scope.py": "none", "port/runtime/x.c": "none", "somewhere/new.txt": "gate"}
        self.assertEqual({f: k for f, (k, _) in got.items()}, want)
        self.assertTrue(all(t is None for _, t in got.values()))

    def test_closures(self):
        mods = rt.module_classes()
        self.assertEqual(mods["tools/regress_project.py"], "gate")
        self.assertEqual(mods["tools/bounded.py"], "gate")  # from bounded import kill_tree: a tools/ sibling
        graph = rt.modules()
        # every module the gate can load is classed, and the judged paths never reach the agent
        gate = rt.closure(graph, ["tools.regress", "tools.regress_project"])
        self.assertNotIn("core.agent", gate)
        self.assertTrue(all(mods[graph[m][0].relative_to(TOOL).as_posix()] != "none" for m in gate))

    def test_targets(self):
        got = kinds("targets/dreamcast_sh4/runtime/tc_rt.c", "targets/dreamcast_sh4/runtime_rd/rd_sh4.c",
                    "targets/dreamcast_sh4/ghidra_scripts/TcPrepare.java", "targets/gamecube_ppc/ppc_check.py",
                    "targets/dreamcast_sh4/README.md", "targets/dreamcast_sh4/newdir/x")
        self.assertEqual(got, {"targets/dreamcast_sh4/runtime/tc_rt.c": ("judge", "dreamcast_sh4"),
                               "targets/dreamcast_sh4/runtime_rd/rd_sh4.c": ("readable", "dreamcast_sh4"),
                               "targets/dreamcast_sh4/ghidra_scripts/TcPrepare.java": ("prepare", "dreamcast_sh4"),
                               "targets/gamecube_ppc/ppc_check.py": ("prepare", "gamecube_ppc"),
                               "targets/dreamcast_sh4/README.md": ("none", "dreamcast_sh4"),
                               "targets/dreamcast_sh4/newdir/x": ("gate", "dreamcast_sh4")})
        self.assertEqual(kinds("core/ghidra_shared/ppc/TcSingle.java"),
                         {"core/ghidra_shared/ppc/TcSingle.java": ("prepare", None)})  # every target's prepare
        rows = rt.classify(["targets/gamecube_ppc/ppc_check.py", "core/readable_lint.py"])
        self.assertEqual(rt.plan(rows, TARGETS), {"dreamcast_sh4": "readable", "gamecube_ppc": "prepare"})
        self.assertEqual(rt.plan(rt.classify(["docs/x.md"]), TARGETS), {t: "none" for t in TARGETS})
        self.assertEqual(rt.plan(rt.classify(["core/compare.py", "core/ghidra.py"]), TARGETS),
                         {t: "prepare" for t in TARGETS})

    def test_config_keys(self):
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))

        def with_change(**kv):
            new = dict(cfg, **kv)
            return kinds("config.json", old_text=lambda f: json.dumps(cfg)), new
        self.assertEqual(rt.changed_keys(json.dumps(cfg), json.dumps(dict(cfg, effort="high"))), ["effort"])
        self.assertEqual(rt.changed_keys(json.dumps(cfg), json.dumps({k: v for k, v in cfg.items() if k != "effort"})),
                         ["effort"])
        self.assertIsNone(rt.changed_keys("{", "{}"))
        mods = rt.module_classes()
        self.assertEqual(rt.key_classes(["effort", "cache_ttl"], mods), [])  # agent sessions only
        self.assertIn(("prepare", None), rt.key_classes(["vectors_hidden"], mods))
        self.assertIn(("prepare", "dreamcast_sh4"), rt.key_classes(["fpscr_model"], mods))
        # the working tree's config.json against itself: no key changed, nothing to gate
        same = rt.classify(["config.json"], old_text=lambda f: (TOOL / f).read_text(encoding="utf-8"))
        self.assertEqual(same, [("config.json", "none", None)])
        # no base text known: the whole gate
        self.assertEqual(rt.classify(["config.json"]), [("config.json", "gate", None)])

    def test_prepare_hash(self):
        root = TMP / "regress_targets_tree"
        shutil.rmtree(root, ignore_errors=True)
        for d in ("core", "tools"):
            shutil.copytree(TOOL / d, root / d, ignore=shutil.ignore_patterns("__pycache__"))
        for t in TARGETS:
            (root / "targets" / t).mkdir(parents=True)
            for p in (TOOL / "targets" / t).glob("*.py"):
                shutil.copyfile(p, root / "targets" / t / p.name)
            shutil.copyfile(TOOL / "targets" / t / "target.json", root / "targets" / t / "target.json")
            for d in ("template", "ghidra_lang"):
                if (TOOL / "targets" / t / d).exists():
                    shutil.copytree(TOOL / "targets" / t / d, root / "targets" / t / d)
        h0 = rt.prepare_hash("dreamcast_sh4", root)
        self.assertEqual(h0, rt.prepare_hash("dreamcast_sh4", TOOL))
        with open(root / "core" / "lint.py", "a", encoding="utf-8") as fh:
            fh.write("\n# a judge change\n")
        self.assertEqual(rt.prepare_hash("dreamcast_sh4", root), h0)
        with open(root / "core" / "ghidra_serve" / "TcServe.java", "a", encoding="utf-8") as fh:
            fh.write("\n// the slot server's TcPrepare run\n")
        h1 = rt.prepare_hash("dreamcast_sh4", root)
        self.assertNotEqual(h1, h0)
        h0 = h1
        with open(root / "targets" / "gamecube_ppc" / "hooks.py", "a", encoding="utf-8") as fh:
            fh.write("\n# another target\n")
        self.assertEqual(rt.prepare_hash("dreamcast_sh4", root), h0)
        with open(root / "core" / "ghidra.py", "a", encoding="utf-8") as fh:
            fh.write("\n# a prepare change\n")
        self.assertNotEqual(rt.prepare_hash("dreamcast_sh4", root), h0)
        shutil.rmtree(root, ignore_errors=True)

    def test_cache(self):
        work = TMP / "regress_cache"
        shutil.rmtree(work, ignore_errors=True)
        harness, packet, cache = work / "harness" / "8c010000", work / "packets" / "8c010000", work / "cache"
        harness.mkdir(parents=True)
        packet.mkdir(parents=True)
        (harness / "meta.json").write_text('{"oracle_stamp": "s1"}', encoding="utf-8")
        (packet / "asm.txt").write_text("asm", encoding="utf-8")
        self.assertFalse(rp.cache_get(cache, "8c010000", "s1-h", work / "h2", work / "p2"))
        rp.cache_put(cache, "8c010000", "s1-h", harness, packet)
        (harness / "meta.json").write_text('{"oracle_stamp": "changed"}', encoding="utf-8")
        rp.cache_put(cache, "8c010000", "s1-h", harness, packet)  # written once: the first entry stays
        self.assertTrue(rp.cache_get(cache, "8c010000", "s1-h", work / "h2", work / "p2"))
        self.assertEqual((work / "h2" / "meta.json").read_text(encoding="utf-8"), '{"oracle_stamp": "s1"}')
        self.assertEqual((work / "p2" / "asm.txt").read_text(encoding="utf-8"), "asm")
        self.assertFalse(rp.cache_get(cache, "8c010000", "s1-other", work / "h3", work / "p3"))
        for key in ("s2-h", "s3-h"):
            time.sleep(0.02)
            rp.cache_put(cache, "8c010000", key, harness, packet)
        self.assertEqual(sorted(d.name for d in (cache / "8c010000").iterdir()), ["s2-h", "s3-h"])
        shutil.rmtree(work, ignore_errors=True)

    def test_readable_items(self):
        """A readable-mode project is run only when it has an item the child keeps (a fixture on a readable runtime,
        or --parity): R1 spent ~33 s of setup on each of 28 projects that judged 0 items."""
        import regress
        self.assertFalse(regress.readable_items([{"id": "a"}, {"id": "b", "runtime": "raw"}], False))
        self.assertTrue(regress.readable_items([{"id": "a", "runtime": "readable"}], False))
        self.assertTrue(regress.readable_items([{"id": "a", "runtime": "readable-src"}], False))
        self.assertTrue(regress.readable_items([], True))
        src = (TOOL / "tools" / "regress_project.py").read_text(encoding="utf-8")  # the child's filter: the same runtimes
        self.assertIn('if f.get("runtime") == "readable"', src)
        self.assertIn('if f.get("runtime") == "readable-src"', src)

    def test_wiring(self):
        src = (TOOL / "tools" / "regress_project.py").read_text(encoding="utf-8")
        self.assertIn('if plan.get("items") == "readable":', src)
        self.assertIn('from_cache = bool(cache and plan.get("reuse_cache"))', src)
        self.assertIn('key = f"{now}-{a.prepare_hash}" if cache else None', src)
        reg = (TOOL / "tools" / "regress.py").read_text(encoding="utf-8")
        self.assertIn('["--catalog"] * (a.catalog or bool(a.targeted))', reg)
        self.assertIn('"reuse_cache": mode in ("judge", "readable")', reg)


if __name__ == "__main__":
    unittest.main()
