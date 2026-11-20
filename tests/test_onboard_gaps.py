"""AutoOnboard's Saturn gaps closed 2026-11-19 (tools/AutoOnboard/onboard_sat.py, onboard_stages.py, autoonboard.py,
onboard_prove.py), offline and with no build:
1. the stage order: machine right after project, register right after boot; machine and register are Saturn's;
2. machine: another checkout's config.local.json fills a missing setting, a working one stays, a missing one is an error
   naming the key and config.local.json;
3. register: Phangrade's catalogue.json gets one compact line (CRLF kept, other lines byte for byte), a person's values
   stay and only missing keys are filled, a path under a dist folder is never used;
4. a root Start Game.bat that calls another project is left alone;
5. boot's run flags (variants, remember_flags) and seed loop (seed_rounds, translatable, run_seeds, segment_entries);
6. a stale boot verdict still names the build the launcher starts; --until, --from and the remote run's arguments;
7. `onboard.py prove`: its command line, the title reference and the frame check.
Run: py -3.12 -m pytest -q tests/test_onboard_gaps.py   (no network, no ssh; about a second)
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
AO = TOOL / "tools" / "AutoOnboard"
sys.path[:0] = [str(AO), str(TOOL / "tools"), str(TOOL)]
import autoonboard as onboard  # noqa: E402
import onboard_sat as SAT  # noqa: E402
import onboard_stages as S  # noqa: E402


class Tmp(unittest.TestCase):
    def tmp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="gaps_")).resolve()
        self.addCleanup(shutil.rmtree, d, True)
        return d


class StageTable(unittest.TestCase):
    def test_machine_after_project_and_register_after_boot(self):
        self.assertIn("machine", S.NAMES)
        self.assertIn("register", S.NAMES)
        self.assertEqual(S.NAMES.index("machine"), S.NAMES.index("project") + 1)
        self.assertEqual(S.NAMES.index("register"), S.NAMES.index("boot") + 1)
        self.assertEqual(S.NAMES[-1], "overview")

    def test_saturn_only_and_where_they_run(self):
        self.assertTrue(hasattr(S, "LOCAL_STAGES"))
        self.assertEqual(S.LOCAL_STAGES, ("project", "register", "overview"))
        self.assertFalse(S.BY_NAME["register"].remote)  # Base is on the user's laptop
        self.assertTrue(S.BY_NAME["machine"].remote)
        for c in ("dreamcast", "gamecube", "xbox360"):
            self.assertEqual(S.BY_NAME["machine"].adapters[c].kind, "not-applicable")
            self.assertEqual(S.BY_NAME["register"].adapters[c].kind, "not-applicable")
        self.assertEqual(S.BY_NAME["machine"].adapters["saturn"].kind, "built")
        self.assertEqual(S.BY_NAME["register"].adapters["saturn"].kind, "built")


class Machine(Tmp):
    def fake_machine(self, root):
        g = root / "ghidra"
        (g / "support").mkdir(parents=True)
        (g / "support" / "analyzeHeadless.bat").write_text("", encoding="utf-8")
        v = root / "vs" / "vcvars64.bat"
        v.parent.mkdir(parents=True)
        v.write_text("", encoding="utf-8")
        (v.parent / "vcvars32.bat").write_text("", encoding="utf-8")
        p = root / "vcpkg"
        (p / "scripts" / "buildsystems").mkdir(parents=True)
        (p / "scripts" / "buildsystems" / "vcpkg.cmake").write_text("", encoding="utf-8")
        j = root / "jdk"
        (j / "bin").mkdir(parents=True)
        (j / "bin" / "java.exe").write_text("", encoding="utf-8")
        self.jdk = j
        return g, v, p

    def tool(self, root, local=None):
        t = root / "tool"
        t.mkdir()
        (t / "config.json").write_text("{}", encoding="utf-8")
        if local is not None:
            (t / "config.local.json").write_text(json.dumps(local), encoding="utf-8")
        return t

    def settings(self, tool, siblings):
        env = {k: v for k, v in os.environ.items() if k not in ("VCVARS32", "VCPKG_ROOT")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(S, "sibling_configs", lambda t: siblings), \
                mock.patch.object(SAT, "_known", lambda globs: []):
            return SAT.machine_settings(tool)

    def test_a_missing_setting_comes_from_another_checkout(self):
        self.assertTrue(hasattr(SAT, "machine_settings") and hasattr(S, "sibling_configs"))
        root = self.tmp()
        g, v, p = self.fake_machine(root)
        other = root / "other.json"
        other.write_text(json.dumps({"ghidra_home": str(g), "java_home": str(self.jdk), "vcvars": str(v), "vcpkg": str(p)}),
                         encoding="utf-8")
        tool = self.tool(root)
        sets, gone, notes = self.settings(tool, [other])
        self.assertEqual(sets, {"ghidra_home": str(g), "java_home": str(self.jdk), "vcvars": str(v), "vcpkg": str(p)})
        self.assertEqual(gone, [])
        # a setting that works here stays: only the other two are taken
        tool2 = root / "tool2"
        tool2.mkdir()
        (tool2 / "config.json").write_text("{}", encoding="utf-8")
        (tool2 / "config.local.json").write_text(json.dumps({"ghidra_home": str(g)}), encoding="utf-8")
        sets, gone, _ = self.settings(tool2, [other])
        self.assertEqual(sorted(sets), ["java_home", "vcpkg", "vcvars"])

    def test_missing_settings_name_the_key_and_the_file(self):
        self.assertTrue(hasattr(SAT, "Machine") and hasattr(SAT, "machine_settings"))
        import onboard_symbols as SYM
        root = self.tmp()
        tool = self.tool(root)
        sets, gone, _ = self.settings(tool, [])
        self.assertEqual(sets, {})
        self.assertEqual([k for k, *_ in gone], ["ghidra_home", "java_home", "vcvars", "vcpkg"])
        ctx = types.SimpleNamespace(tool=tool, project=root / "proj", name="G", console="saturn", verdicts={})
        (root / "proj").mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("VCVARS32", "VCPKG_ROOT")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(S, "sibling_configs", lambda t: []), \
                mock.patch.object(SAT, "_known", lambda globs: []), mock.patch.object(SYM, "folders", lambda c: []):
            with self.assertRaises(S.StageError) as cm:
                SAT.Machine().run(ctx)
        e = cm.exception
        for key in ("ghidra_home", "java_home", "vcvars", "vcpkg"):
            self.assertIn(key, str(e))
            self.assertTrue(any(key in m and "config.local.json" in m for m in e.missing), e.missing)
        self.assertIn("config.local.json", e.supply)

    def test_config_local_keeps_what_is_there(self):
        self.assertTrue(hasattr(S, "set_config_local"))
        root = self.tmp()
        tool = self.tool(root, {"ghidra_home": "mine", "vcpkg": ""})
        S.set_config_local(tool, {"ghidra_home": "theirs", "vcpkg": "V", "vcvars": "C"})
        self.assertEqual(S.config_local(tool), {"ghidra_home": "mine", "vcpkg": "V", "vcvars": "C"})

    def test_sibling_configs_finds_another_checkout_and_not_this_one(self):
        self.assertTrue(hasattr(S, "sibling_configs"))
        root = self.tmp()
        tool = self.tool(root, {"x": 1})
        (root / "dpx" / "G" / "dp").mkdir(parents=True)
        other = root / "dpx" / "G" / "dp" / "config.local.json"
        other.write_text("{}", encoding="utf-8")
        found = S.sibling_configs(tool)
        self.assertIn(other.resolve(), found)
        self.assertNotIn((tool / "config.local.json").resolve(), found)


CATALOGUE = ('{\r\n  "version": 1,\r\n  "games": [\r\n'
             '    {"id":"alpha","title":"Alpha","platform":"Saturn"},\r\n'
             '    {"id":"beta","title":"Mine","extra":{"k":[1,2]}}\r\n'
             '  ]\r\n}\r\n')


class RunKey(unittest.TestCase):
    def test_every_run_names_the_exe_it_starts(self):
        """Dragon Force, 2026-11-19: seed round 2 rebuilt game.exe at the same path and jobreg refused its run as one that
        failed before (the 20 s run ends on its watchdog, exit 2)."""
        self.assertTrue(hasattr(SAT, "exe_key"))
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        exe = d / "game.exe"
        exe.write_bytes(b"one")
        ctx = types.SimpleNamespace(tool=pathlib.Path(SAT.__file__).resolve().parents[2], project=d, changed=None)
        cmds = []
        with mock.patch.object(SAT.S, "log_path", lambda c, s: d / "boot.log"),                 mock.patch.object(SAT.subprocess, "run", lambda cmd, **k: cmds.append(cmd) or types.SimpleNamespace(returncode=0)):
            SAT.run_exe(ctx, exe, ["--headless"], 30, "a run")
            exe.write_bytes(b"two")
            SAT.run_exe(ctx, exe, ["--headless"], 30, "a run")
        keys = [c[c.index("--changed") + 1] for c in cmds]
        self.assertEqual(len(set(keys)), 2, keys)
        self.assertTrue(all(k.startswith("exe ") for k in keys), keys)


class PartialAgain(unittest.TestCase):
    def test_a_partial_stage_runs_again_by_itself(self):
        self.assertTrue(hasattr(onboard, "partial_at"))
        names = ["machine", "census", "symbols", "boot"]
        v = {"census": {"status": "ok"}, "symbols": {"status": "ok"}, "boot": {"status": "partial"}}
        self.assertEqual(onboard.partial_at(names, 0, 4, v), 3)
        self.assertIsNone(onboard.partial_at(names, 0, 4, {**v, "boot": {"status": "error"}}))
        self.assertGreaterEqual(onboard.PARTIAL_AGAIN, 1)
        src = pathlib.Path(onboard.__file__).read_text(encoding="utf-8")
        self.assertIn("partial, running it again", src)


class ProveSampled(unittest.TestCase):
    def test_a_saturn_replay_compares_sampled_frames(self):
        """Dragon Force, 2026-11-19: the Saturn kit has --dump-frames, not --dump-every; replay_compare saw 0 frames."""
        sys.path.insert(0, str(pathlib.Path(onboard.__file__).resolve().parent))
        import onboard_prove as PR
        self.assertTrue(hasattr(PR, "sampled_replay"))
        self.assertGreater(PR.SAMPLE_EVERY, 0)


class MachineKeys(unittest.TestCase):
    def test_the_census_and_the_build_settings_are_all_checked(self):
        self.assertTrue(hasattr(SAT, "MACHINE_KEYS"))
        self.assertEqual([k[0] for k in SAT.MACHINE_KEYS], ["ghidra_home", "java_home", "vcvars", "vcpkg"])

    def test_known_places_take_a_wildcard_in_any_part(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "2022" / "Community" / "VC").mkdir(parents=True)
        self.assertEqual(SAT._known([f"{d}/2022/*/VC"]), [d / "2022" / "Community" / "VC"])


class Catalogue(Tmp):
    def setUp(self):
        self.d = self.tmp()
        self.f = self.d / "catalogue.json"
        self.f.write_bytes(CATALOGUE.encode("utf-8"))

    def test_a_new_entry_is_one_line_and_every_other_byte_stays(self):
        self.assertTrue(hasattr(SAT, "put_in_catalogue"))
        ent = SAT.catalogue_entry(self.d, "Dragon Force")
        done, differ = SAT.put_in_catalogue(self.f, ent)
        self.assertEqual((done, differ), ("added", []))
        raw = self.f.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))  # CRLF kept, no bare LF
        old, new = CATALOGUE.encode().split(b"\r\n"), raw.split(b"\r\n")
        self.assertEqual(len(new), len(old) + 1)
        last = max(i for i, x in enumerate(old) if x.lstrip().startswith(b'{"id":'))
        self.assertEqual(new[:last], old[:last])
        self.assertEqual(new[last], old[last] + b",")  # only the old last entry gains its comma
        self.assertEqual(new[last + 1][:4], b"    ")
        self.assertEqual(json.loads(new[last + 1].decode().strip()), ent)
        self.assertEqual(new[last + 2:], old[last + 1:])
        self.assertEqual([g["id"] for g in json.loads(raw)["games"]], ["alpha", "beta", "dragonforce"])
        self.assertEqual(SAT.put_in_catalogue(self.f, ent)[0], "there already")
        self.assertEqual(self.f.read_bytes(), raw)

    def test_an_existing_entry_keeps_a_persons_values(self):
        self.assertTrue(hasattr(SAT, "put_in_catalogue"))
        ent = {"id": "beta", "title": "Beta", "folder": "Beta", "executable": "game.exe"}
        done, differ = SAT.put_in_catalogue(self.f, ent)
        self.assertEqual(differ, ["title"])
        self.assertIn("folder", done)
        lines = self.f.read_bytes().decode().split("\r\n")
        beta = json.loads(lines[4].strip())
        self.assertEqual(beta, {"id": "beta", "title": "Mine", "extra": {"k": [1, 2]}, "folder": "Beta",
                                "executable": "game.exe"})
        self.assertEqual(lines[3], '    {"id":"alpha","title":"Alpha","platform":"Saturn"},')
        self.assertEqual(SAT.put_in_catalogue(self.f, ent), ("there already", ["title"]))

    def test_the_catalogue_is_found_above_the_project_and_never_under_dist(self):
        self.assertTrue(hasattr(S, "phangrade_catalogue"))
        root = self.tmp()
        tool = root / "dp"
        tool.mkdir()
        good = root / "Base" / "data" / "catalogue.json"
        good.parent.mkdir(parents=True)
        good.write_text("{}", encoding="utf-8")
        (root / "games" / "G").mkdir(parents=True)
        ctx = types.SimpleNamespace(tool=tool, project=root / "games" / "G")
        self.assertEqual(S.phangrade_catalogue(ctx), good)
        bad = self.tmp()
        dist = bad / "dist" / "Base" / "data" / "catalogue.json"
        dist.parent.mkdir(parents=True)
        dist.write_text("{}", encoding="utf-8")
        (bad / "dist" / "Base" / "G").mkdir()
        (bad / "dp").mkdir()
        with self.assertRaises(S.StageError):
            S.phangrade_catalogue(types.SimpleNamespace(tool=bad / "dp", project=bad / "dist" / "Base" / "G"))

    def test_entry_naming(self):
        self.assertTrue(hasattr(SAT, "camel"))
        self.assertEqual(SAT.camel("Robo Pit"), "RoboPit")
        ent = SAT.catalogue_entry(self.d, "Dragon Force")
        self.assertEqual((ent["folder"], ent["repository"], ent["localExecutable"]),
                         ("DragonForce", "DragonForce_Saturn_NativePort", "Start Game.bat"))


class RootBat(Tmp):
    def game(self):
        g = self.tmp() / "SaturnBomberman"
        (g / "GameFiles").mkdir(parents=True)
        (g / "LOCATIONS.md").write_text("x", encoding="utf-8")
        d = g / ".work" / "auto"
        d.mkdir(parents=True)
        return g, d

    def test_a_root_bat_calling_another_project_is_kept(self):
        self.assertTrue(hasattr(SAT, "root_target"))
        g, d = self.game()
        live = (b'@echo off\r\nrem written by AutoOnboard (tools/AutoOnboard/autoonboard.py)\r\n'
                b'call "%~dp0.work\\live\\Start Game.bat" %*\r\n')
        (g / "Start Game.bat").write_bytes(live)
        made = SAT.launcher_files(d, "Saturn Bomberman")
        self.assertEqual((g / "Start Game.bat").read_bytes(), live)
        self.assertTrue(any(m.startswith("kept ") and "live" in m for m in made), made)
        self.assertTrue((d / "Start Game.bat").is_file())  # the project's own bat is still written
        # one that calls this project is ours and is rewritten when it differs
        (g / "Start Game.bat").write_bytes(live.replace(b"live", b"auto") + b"rem old\r\n")
        SAT.launcher_files(d, "Saturn Bomberman")
        self.assertNotIn(b"rem old", (g / "Start Game.bat").read_bytes())

    def test_a_stale_boot_verdict_still_names_the_build(self):
        g, d = self.game()
        (d / S.STATE_DIR).mkdir(parents=True)
        launch = {"dir": "port/build/onboard_np", "args": ["--fallback", "on"], "files": ["game.exe"]}
        (d / S.STATE_DIR / "boot.json").write_text(json.dumps({"status": "stale", "was": "ok", "launch": launch}),
                                                   encoding="utf-8")
        self.assertEqual(SAT.launch_of(d)["dir"], "port/build/onboard")  # no game.exe there: the default build
        (d / "port" / "build" / "onboard_np").mkdir(parents=True)
        (d / "port" / "build" / "onboard_np" / "game.exe").write_bytes(b"x")
        self.assertEqual(SAT.launch_of(d)["dir"], "port/build/onboard_np")
        (d / S.STATE_DIR / "boot.json").write_text(json.dumps({"status": "stale", "was": "error", "launch": launch}),
                                                   encoding="utf-8")
        self.assertEqual(SAT.launch_of(d)["dir"], "port/build/onboard")  # a stale verdict that proved nothing: no launch.json, the default


class Flags(Tmp):
    def ctx(self, **kw):
        return types.SimpleNamespace(**{"pointers": None, "overlays": None, "fallback": None, **kw})

    def gj(self, onboard_flags=None):
        f = self.tmp() / "game.json"
        f.write_text(json.dumps({"saturn": {"entry": "06004000"}, **({"onboard": onboard_flags} if onboard_flags else {})}),
                     encoding="utf-8")
        return f

    def test_no_flags_tries_the_variants_in_order(self):
        self.assertTrue(hasattr(SAT, "VARIANTS") and hasattr(SAT, "variants"))
        self.assertEqual(SAT.variants(self.ctx(), self.tmp() / "none.json"), list(SAT.VARIANTS))
        self.assertEqual(SAT.variants(self.ctx(), self.gj()), list(SAT.VARIANTS))

    def test_the_proven_set_goes_first_and_is_not_repeated(self):
        self.assertTrue(hasattr(SAT, "VARIANTS") and hasattr(SAT, "variants"))
        two = SAT.VARIANTS[2]
        got = SAT.variants(self.ctx(), self.gj(dict(zip(SAT.FLAG_NAMES, two))))
        self.assertEqual(got[0], two)
        self.assertEqual(len(got), len(SAT.VARIANTS))
        self.assertEqual(len(set(got)), len(got))
        odd = ("off", "on", "off")
        self.assertNotIn(odd, SAT.VARIANTS)
        got = SAT.variants(self.ctx(), self.gj(dict(zip(SAT.FLAG_NAMES, odd))))
        self.assertEqual(got, [odd] + list(SAT.VARIANTS))

    def test_a_flag_given_is_the_only_variant_with_the_rest_filled(self):
        self.assertTrue(hasattr(SAT, "variants"))
        first = dict(zip(SAT.FLAG_NAMES, SAT.VARIANTS[0]))
        want = {**first, "fallback": "off" if first["fallback"] == "on" else "on"}
        self.assertEqual(SAT.variants(self.ctx(fallback=want["fallback"]), self.tmp() / "none.json"),
                         [tuple(want[k] for k in SAT.FLAG_NAMES)])
        proven = dict(zip(SAT.FLAG_NAMES, ("off", "on", "on")))
        got = SAT.variants(self.ctx(overlays="off"), self.gj(proven))
        self.assertEqual(got, [("off", "off", "on")])

    def test_remember_flags_writes_once(self):
        self.assertTrue(hasattr(SAT, "remember_flags"))
        gj = self.gj()
        v = SAT.VARIANTS[1]
        self.assertTrue(SAT.remember_flags(gj, v))
        self.assertFalse(SAT.remember_flags(gj, v))
        cfg = json.loads(gj.read_text(encoding="utf-8"))
        self.assertEqual(cfg["onboard"], dict(zip(SAT.FLAG_NAMES, v)))
        self.assertEqual(cfg["saturn"], {"entry": "06004000"})  # the rest of game.json stays
        self.assertEqual(SAT.proven_flags(gj), v)
        self.assertTrue(SAT.remember_flags(gj, SAT.VARIANTS[2]))


class Seeds(Tmp):
    def test_seed_rounds_stop_when_nothing_is_new(self):
        self.assertTrue(hasattr(SAT, "seed_rounds"))
        seen = []

        def round_(n):
            seen.append(n)
            return f"r{n}", (["06010000", "06010010"] if n < 3 else [])
        self.assertEqual(SAT.seed_rounds(round_), ("r3", 3, ["06010000", "06010010"] * 2))
        self.assertEqual(seen, [1, 2, 3])
        self.assertEqual(SAT.seed_rounds(lambda n: ("only", [])), ("only", 1, []))

    def test_seed_rounds_stop_at_the_limit(self):
        self.assertTrue(hasattr(SAT, "seed_rounds"))
        got, n, found = SAT.seed_rounds(lambda n: (n, [f"0601000{n}"]), limit=2)
        self.assertEqual((got, n, found), (2, 2, ["06010001", "06010002"]))

    def test_seed_rounds_stop_partial_when_the_deadline_would_pass(self):
        self.assertTrue(hasattr(SAT, "seed_rounds"))
        now = [0.0]

        def round_(n):
            now[0] += 100
            return n, ["06010000"]
        with self.assertRaises(S.StageError) as cm:
            SAT.seed_rounds(round_, limit=4, deadline=250, clock=lambda: now[0])
        self.assertEqual(cm.exception.status, "partial")
        self.assertEqual(cm.exception.numbers["seed rounds"], 2)
        now[0] = 0.0
        self.assertEqual(SAT.seed_rounds(round_, limit=4, deadline=10000, clock=lambda: now[0])[1], 4)

    PLAN = {"segments": [{"base": 0x00200000, "size": 0x1000, "entries": ["00200010"]}]}

    def test_translatable(self):
        self.assertTrue(hasattr(SAT, "translatable"))
        for a in ("00000000", "00210000", "00500000", "06100000", "05c00000"):
            self.assertFalse(SAT.translatable(a, self.PLAN), a)
        for a in ("06004000", "060ffffc", "00200100"):
            self.assertTrue(SAT.translatable(a, self.PLAN), a)
        self.assertFalse(SAT.translatable("00200100", {}))

    def test_run_seeds_takes_only_what_a_boot_can_translate(self):
        self.assertTrue(hasattr(SAT, "run_seeds"))
        ws = self.tmp() / "decomp"
        text = ("STUB #1 (master): call to 00000000 has no translated function (returns 0)\n"
                "STUB #2 (slave): call to 00500000 has no translated function (returns 0)\n"
                "STUB #3 (master): call to 00200100 has no translated function (returns 0)\n"
                "STUB #4 (master): call to 0606b78e has no translated function (returns 0)\n"
                "fallback: 06020000 entries 3 instructions 96 from 0606b6bc < 0606ae6c\n")
        self.assertEqual(sorted(SAT.run_seeds(ws, self.PLAN, text)), ["00200100", "06020000", "0606b78e"])
        self.assertNotIn("00000000", SAT.read_seeds(ws))
        self.assertNotIn("00500000", SAT.read_seeds(ws))
        self.assertEqual(SAT.run_seeds(ws, self.PLAN, text), [])  # nothing new: the loop stops

    def test_the_start_check_runs_feed_the_seeds(self):
        """Saturn Bomberman's 0606b78e and 0606b734 run only after Start is pressed (2026-11-19, the title run of
        onboard.py prove): the seed round reads the start check's logs, not only the idle run's."""
        src = (pathlib.Path(SAT.__file__)).read_text(encoding="utf-8")
        self.assertIn('st["start"] = start_check(ctx, exe, run_args)', src)
        self.assertIn('"start_check" / f"{t_}.log"', src)
        ws = self.tmp() / "decomp"
        two = ("fallback: 0606b78e entries 4 instructions 112 from 0606b6bc < 0606b0de\n"
               "fallback: 0606b78e entries 4 instructions 112 from 0606b6bc < 0606b0de\n")
        self.assertEqual(SAT.run_seeds(ws, self.PLAN, two), ["0606b78e"])

    def test_segment_entries_add_the_seeds_inside_the_block(self):
        self.assertTrue(hasattr(SAT, "segment_entries"))
        ws = self.tmp() / "decomp"
        SAT.run_seeds(ws, self.PLAN, "STUB #3 (master): call to 00200100 has no translated function (returns 0)\n"
                      "STUB #4 (master): call to 0606b78e has no translated function (returns 0)\n"
                      "STUB #5 (master): call to 00200010 has no translated function (returns 0)\n")
        seg = self.PLAN["segments"][0]
        self.assertEqual(SAT.segment_entries(ws, seg), ["00200010", "00200100"])


class Resume(Tmp):
    def a(self, **kw):
        return types.SimpleNamespace(**{"only": None, "from_": None, "until": None, **kw})

    def test_machine_is_not_the_resume_point_and_until_stops(self):
        self.assertIn("machine", S.NAMES)
        v = {"project": {"status": "ok"}}
        self.assertEqual(onboard.plan(v, self.a())[0], "disc")
        self.assertEqual(onboard.plan(v, self.a(until="census"))[-1], "census")
        self.assertEqual(onboard.plan(v, self.a(from_="boot", until="register")), ["boot", "register"])

    def test_remote_args(self):
        self.assertTrue(hasattr(onboard, "remote_args"))
        self.assertEqual(onboard.remote_args(self.a(), ["machine", "disc", "extract"]), ["--from", "disc", "--until", "extract"])
        self.assertEqual(onboard.remote_args(self.a(), ["machine"]), ["--only", "machine"])
        self.assertEqual(onboard.remote_args(self.a(only="boot"), ["boot"]), ["--only", "boot"])

    def test_flags_not_given_are_not_passed_to_the_remote_run(self):
        self.assertTrue(hasattr(onboard, "remote_args"))
        d = self.tmp()
        a = types.SimpleNamespace(host="Build box A", remote_dp="D:/dp", remote_project="D:/p", disc=None, inputs=None,
                                  only="boot", seconds=100, late_seconds=None, changed=None, here=False, dry_run=False)
        ctx = onboard.Ctx(a, d, "saturn", "G")
        import onboard_remote as R
        seen, old = {}, (R.run, R.sync)
        R.run = lambda host, dp, project, args, seconds: seen.setdefault("args", args) and {"error": "stop", "missing": [],
                                                                                           "supply": ""}
        R.sync = lambda *x, **k: {"disc": None, "notes": []}
        try:
            onboard.remote_batch(ctx, a, ["boot"], {})
        finally:
            R.run, R.sync = old
        for flag in ("--fallback", "--pointers", "--overlays"):
            self.assertNotIn(flag, seen["args"])


class Prove(Tmp):
    def test_the_prove_command_is_dispatched(self):
        self.assertTrue((AO / "onboard_prove.py").is_file())
        r = subprocess.run([sys.executable, str(TOOL / "tools" / "onboard.py"), "prove", "--help"], capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("--title", r.stdout)

    def test_parse_title_and_the_frame_check(self):
        self.assertTrue((AO / "onboard_prove.py").is_file())
        import onboard_prove as P
        self.assertEqual(P.parse_title("8600:20DEB903:START@8000+10"),
                         {"vb": 8600, "sha": "20deb903", "input": "START@8000+10"})
        self.assertEqual(P.parse_title("5:AB"), {"vb": 5, "sha": "ab", "input": None})
        folder = self.tmp()
        self.assertFalse(P.frame_verdict({"vb": 5, "sha": "ab"}, folder)["ok"])  # no frame
        data = b"\x89PNG not really a png"
        (folder / "frame.png").write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        good = P.frame_verdict({"vb": 5, "sha": sha[:10].upper().lower()}, folder)
        self.assertTrue(good["ok"], good)
        self.assertIn("the reference", good["why"])
        bad = P.frame_verdict({"vb": 5, "sha": "0" * 10}, folder)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["sha256"], sha[:12])


if __name__ == "__main__":
    unittest.main()
