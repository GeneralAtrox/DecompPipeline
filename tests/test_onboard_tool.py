"""tools/AutoOnboard/autoonboard.py, the new-game onboarding orchestrator, with no Ghidra and no real disc (a synthetic .gdi):
1. resume: a run starts at the first stage without an ok or not-applicable verdict; --from and --only drop the
   verdicts of that stage and every later one; a stage whose needs are not done is a hard error;
2. hard errors: no disc image in the root, a missing tool, a stage not built yet, heavy work without --here; each
   names what is missing, exits 2, and the overview is still written;
3. the original Xbox, arcade, an unknown console and a first run without --console are refused at the first stage;
4. OVERVIEW.md is generated from fixture verdict files (table, what is missing, next steps);
5. the scaffold's .gitignore ignores generated C wherever it lands (and the disc, game data, Ghidra, harnesses)
   and tracks hand-written sources; a .gitignore that would let C through stops the project stage;
6. the Dreamcast stages project, disc and extract run end to end on a synthetic disc; census stops as heavy work
   (symbols, after it, is not reached); a second run resumes at census;
8. estimate: equal size bands, a seeded draw, the projection and its bootstrap range (fixture numbers);
9. translate: no harness_reuse needed (no key check runs); a run that does not finish writes a partial GAPS.md and
   says how much is done; the chunk is sized from the estimate; a jobreg reuse of a result this project lacks runs
   again with --changed (onboard_stages.run_tool);
12. estimate: the PASS range is widened to the Wilson interval, never 100-100 from a finite sample;
13. boot (Dreamcast): a starter port/game.json with the entry left to a person, every missing input named with what
   supplies it; with the inputs there, the build and run's port.log read into vertical blanks, scenes and the first
   error;
10. gaps: GAPS.md from fixture records (reasons grouped, refused, not tried, Dreamcast kit gaps from the kit's own
   source, an unnamed library function not known), and "not known - no feature list" for another console;
11. census: the count is labelled as found by analysis, with the hand-added share a finished project needed;
7. the remote runner (tools/AutoOnboard/onboard_remote.py) runs stages in a "remote" folder through one command over stdin
   (ONBOARD_SSH: the local Python standing in for ssh) and copies the verdicts back with the host's name.
Run: python -W error tests/test_onboard_tool.py   (needs git on PATH for 5-7)
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools" / "AutoOnboard"), str(TOOL / "tools"), str(TOOL / "tests")]
import autoonboard as onboard  # noqa: E402
import onboard_stages as S  # noqa: E402
from test_gdrom_iso_tracks import BODY, sectors  # noqa: E402


def gdi(folder):
    """A synthetic Dreamcast disc (IP.BIN, one 1ST_READ.BIN) as a .gdi with 2048-byte data tracks."""
    (folder / "track03.bin").write_bytes(b"".join(sectors()))
    (folder / "game.gdi").write_text('3\n1 0 4 2352 "track01.bin" 0\n2 450 0 2352 "track02.raw" 0\n'
                                     '3 45000 4 2048 "track03.bin" 0\n', encoding="utf-8")
    return folder / "game.gdi"


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = onboard.main([str(a) for a in argv])
    return rc, out.getvalue()


def verdicts(project):
    return onboard.load(pathlib.Path(project))


class Base(unittest.TestCase):
    def setUp(self):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="onb_", dir=TOOL / "tests" / ".work"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def fixture(self, **status):
        for n, st in status.items():
            onboard.save(self.d, {"stage": n, "status": st, "console": "dreamcast", "name": "Fixture", "machine": "m",
                                  "message": f"{n} said so", "numbers": {"count": 7}, "paths": {},
                                  "missing": ["the thing"] if st == "error" else [], "supply": "the person",
                                  "next": [f"after {n}"] if st == "ok" else []})


class Resume(Base):
    def plan(self, **kw):
        a = type("A", (), {"only": kw.get("only"), "from_": kw.get("from_")})
        return onboard.plan(verdicts(self.d), a)

    def test_first_stage_without_ok(self):
        self.fixture(project="ok", disc="ok")
        self.assertEqual(self.plan()[0], "extract")
        self.fixture(extract="ok", symbols="not-applicable")
        self.assertEqual(self.plan()[0], "census")
        self.fixture(census="error")
        self.assertEqual(self.plan()[0], "census")
        self.assertEqual(self.plan()[-1], "gaps")  # the overview is not a planned stage: it always runs

    def test_from_and_only(self):
        self.assertEqual(self.plan(from_="disc")[:2], ["disc", "extract"])
        self.assertEqual(self.plan(only="census"), ["census"])

    def test_drop_from_removes_later_verdicts(self):
        self.fixture(project="ok", disc="ok", extract="ok", census="ok")
        v = verdicts(self.d)
        onboard.drop_from(self.d, v, "extract")
        # a verdict that was ok is kept as stale, with what it proved; the earlier ones are untouched
        got = verdicts(self.d)
        self.assertEqual(sorted(got), ["census", "disc", "extract", "project"])
        self.assertEqual((got["project"]["status"], got["disc"]["status"]), ("ok", "ok"))
        self.assertEqual((got["extract"]["status"], got["extract"]["was"]), ("stale", "ok"))
        self.assertEqual(got["census"]["numbers"], {"count": 7})
        self.assertIn("extract ran again", got["census"]["stale"])
        self.assertEqual(sorted(v), ["disc", "project"])  # stale is not done: the in-memory verdicts lose them
        self.fixture(extract="error")
        onboard.drop_from(self.d, verdicts(self.d), "extract")  # one that proved nothing is deleted
        self.assertNotIn("extract", verdicts(self.d))

    def test_needs_not_done(self):
        self.fixture(project="ok")
        rc, _ = run("--project", self.d, "--only", "extract")
        self.assertEqual(rc, 2)
        self.assertIn("needs disc done first", verdicts(self.d)["extract"]["message"])

    def test_unreadable_verdict_is_an_error(self):
        self.fixture(project="ok")
        (self.d / S.STATE_DIR / "disc.json").write_text("{", encoding="utf-8")
        self.assertEqual(verdicts(self.d)["disc"]["status"], "error")


class Refused(Base):
    def test_consoles(self):
        for console, words in (("xbox", "original Xbox"), ("arcade", "no translator target"), ("psx", "not a console")):
            rc, out = run("--project", self.d / console, "--console", console)
            self.assertEqual(rc, 2, console)
            v = verdicts(self.d / console)["project"]
            self.assertEqual(v["status"], "error")
            self.assertIn(words, v["message"])
            self.assertTrue((self.d / console / S.STATE_DIR / "OVERVIEW.md").exists())
            self.assertFalse((self.d / console / ".gitignore").exists())  # nothing scaffolded

    def test_first_run_needs_console(self):
        rc, _ = run("--project", self.d)
        self.assertEqual(rc, 2)
        self.assertIn("needs --console", verdicts(self.d)["project"]["message"])


class Overview(Base):
    def test_from_fixture_verdicts(self):
        self.fixture(project="ok", disc="ok", extract="error")
        text = onboard.overview(self.d, verdicts(self.d), None)
        self.assertIn("| disc | read the disc | ok | m | count 7 |", text)
        self.assertRegex(text, r"\| extract \| [^|]+ \| error \|")
        self.assertRegex(text, r"\| census \| [^|]+ \| pending \|")
        self.assertIn("**Where it stands:** 2 of 11 stages done; stopped at **extract**", text)
        self.assertIn("- extract: extract said so. Missing: the thing. Supplied by: the person", text)
        self.assertIn("1. Fix extract", text)
        self.assertIn("after disc", text)
        self.assertEqual(text, (self.d / S.STATE_DIR / "OVERVIEW.md").read_text(encoding="utf-8"))
        self.assertLess(len(text.splitlines()), 40)

    def test_not_built_listed(self):
        self.fixture(project="ok", disc="ok", extract="ok", symbols="not-applicable", census="ok")
        text = onboard.overview(self.d, verdicts(self.d), None)
        self.assertNotIn("Not built yet for this console: boot", text)  # the Dreamcast boot stage is built now
        self.fixture(project="ok")
        (self.d / S.STATE_DIR / "project.json").write_text(json.dumps(dict(verdicts(self.d)["project"],
                                                                           console="saturn")), encoding="utf-8")
        self.assertNotIn("Not built yet for this console", onboard.overview(self.d, verdicts(self.d), None))  # all built


class Errors(Base):
    def test_no_disc_in_root(self):
        rc, out = run("--project", self.d, "--console", "dreamcast", "--name", "Test Game")
        self.assertEqual(rc, 2)
        v = verdicts(self.d)
        self.assertEqual(v["project"]["status"], "ok")
        self.assertEqual(v["disc"]["status"], "error")
        self.assertIn(".gdi or .cue", v["disc"]["missing"][0])
        self.assertIn("project root", v["disc"]["supply"])
        self.assertIn("| disc | read the disc | error |", out)  # the overview is printed after the error

    def test_missing_tool(self):
        self.fixture(project="ok")
        a = onboard.argparse.Namespace(disc=None, here=False, host=None, dry_run=False)
        ctx = onboard.Ctx(a, self.d, "dreamcast", "X")
        ctx.tool, ctx.verdicts = self.d / "no_dp", verdicts(self.d)
        v = onboard.execute(ctx, S.BY_NAME["disc"])
        self.assertEqual(v["status"], "error")
        self.assertIn("targets/dreamcast_sh4/tools/gdrom.py", v["missing"])

    def test_no_saturn_disc_in_root(self):
        rc, _ = run("--project", self.d, "--console", "saturn")
        self.assertEqual(rc, 2)
        v = verdicts(self.d)["disc"]
        self.assertTrue(v["message"].startswith("no disc image in"), v["message"])
        self.assertIn("saturn disc image", v["missing"][0])

    def test_heavy_without_here(self):
        self.fixture(project="ok", disc="ok", extract="ok", symbols="not-applicable")
        rc, _ = run("--project", self.d, "--only", "census")
        self.assertEqual(rc, 2)
        self.assertIn("heavy work", verdicts(self.d)["census"]["message"])

    def test_ghidra_project_under_a_dot_folder(self):
        """Ghidra refuses a project path with an element starting with '.' (this test's folder is under tests/.work)."""
        self.fixture(project="ok", disc="ok", extract="ok", symbols="not-applicable")
        (self.d / "decomp").mkdir()
        (self.d / "decomp" / "project.json").write_text(json.dumps({"ghidra_name": "G", "binary": "x"}),
                                                       encoding="utf-8")
        rc, _ = run("--project", self.d, "--only", "census", "--here")
        self.assertEqual(rc, 2)
        v = verdicts(self.d)["census"]
        self.assertIn("starts with '.'", v["message"])
        self.assertIn("ghidra_dir", v["missing"][0])

    def test_dry_run_writes_nothing(self):
        rc, out = run("--project", self.d, "--console", "dreamcast", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("game_setup.py setup", out)
        self.assertFalse((self.d / S.STATE_DIR).exists())


class Gitignore(Base):
    def test_scaffold_rejects_generated_c(self):
        run("--project", self.d, "--console", "dreamcast", "--only", "project")
        self.assertEqual(verdicts(self.d)["project"]["status"], "ok")
        leaks, hidden = S.check_ignore(self.d, ["decomp/matched/sub/8c0a0000.c", "decomp/readable/x.h",
                                                "port/out/translated.cpp", "Some Game (Track 01).bin"])
        self.assertEqual((leaks, hidden), ([], []))
        self.assertTrue((self.d / "LOCATIONS.md").exists())

    def test_a_leaky_gitignore_stops_the_stage(self):
        self.d.mkdir(exist_ok=True)
        (self.d / ".gitignore").write_text("/game-data/\n", encoding="utf-8")
        rc, _ = run("--project", self.d, "--console", "dreamcast", "--only", "project")
        self.assertEqual(rc, 2)
        v = verdicts(self.d)["project"]
        self.assertEqual(v["status"], "error")
        self.assertIn("decomp/matched/8c010000.c", v["message"])


class Dreamcast(Base):
    def test_synthetic_disc_to_census(self):
        gdi(self.d)
        rc, out = run("--project", self.d, "--console", "dreamcast", "--name", "Test Game")
        self.assertEqual(rc, 2)
        v = verdicts(self.d)
        self.assertEqual([v[n]["status"] for n in ("project", "disc", "extract", "census")],
                         ["ok", "ok", "ok", "error"], out)
        self.assertNotIn("symbols", v)  # after the census since phase 5 (the GameCube matches to the census catalog)
        self.assertEqual(v["disc"]["numbers"]["boot file"], "1ST_READ.BIN")
        self.assertEqual(v["disc"]["numbers"]["title"], "TESTGAME")
        self.assertEqual((self.d / "game-data" / "1ST_READ.BIN").read_bytes(), BODY)
        pj = json.loads((self.d / "decomp" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual((pj["name"], pj["binary"]), ("Test Game", "disc/1ST_READ.BIN"))
        self.assertIn("heavy work", v["census"]["message"])
        a = type("A", (), {"only": None, "from_": None})
        self.assertEqual(onboard.plan(v, a)[0], "census")
        rc, _ = run("--project", self.d, "--from", "extract")  # a re-run reuses what is there
        self.assertIn("reused workspace, game-data", verdicts(self.d)["extract"]["message"])

    def test_disc_of_the_wrong_console(self):
        (self.d / "game.iso").write_bytes(bytes(4096))
        rc, _ = run("--project", self.d, "--console", "dreamcast", "--disc", self.d / "game.iso")
        self.assertEqual(rc, 2)
        self.assertEqual(verdicts(self.d)["disc"]["status"], "error")


class Remote(Base):
    def test_one_command_and_verdicts_back(self):
        far = self.d / "far"
        far.mkdir()
        gdi(far)
        os.environ["ONBOARD_SSH"] = json.dumps([sys.executable, "-I", "-"])
        try:
            base = ("--project", self.d / "here", "--console", "dreamcast", "--host", "Build box B", "--remote-project",
                    far, "--remote-dp", TOOL)
            rc, out = run(*base, "--only", "disc")
            self.assertEqual(rc, 0, out)
            v = verdicts(self.d / "here")
            self.assertEqual((v["project"]["status"], v["disc"]["status"], v["disc"]["machine"]),
                             ("ok", "ok", "Build box B"))
            self.assertEqual(verdicts(far)["disc"]["numbers"]["title"], "TESTGAME")  # written there, copied here
            rc, _ = run(*base[:2], "--host", "Build box B", "--remote-project", far, "--remote-dp", self.d / "nothing", "--no-sync",
                        "--only", "extract")
            self.assertEqual(rc, 2)
            self.assertIn("no tools/AutoOnboard/autoonboard.py", verdicts(self.d / "here")["extract"]["message"])
        finally:
            os.environ.pop("ONBOARD_SSH", None)


class CensusWording(unittest.TestCase):
    def test_count_is_a_floor(self):
        import onboard_dc
        m = onboard_dc.denominator(4040)
        self.assertIn("found by analysis: 4040 functions. Not the whole game", m)
        self.assertIn("3.5% more", m)
        self.assertIn("Expected shortfall for a new game", m)
        self.assertIn("entry tables are not scanned", m)


class Estimate(Base):
    def test_bands_and_draw(self):
        import onboard_estimate as E
        funcs = [{"addr": f"8c01{i:04x}", "insns": i % 37} for i in range(103)]
        bs = E.bands(funcs, 5)
        self.assertEqual([len(b) for b in bs], [20, 21, 20, 21, 21])
        self.assertEqual(sorted(a for b in bs for a in b), sorted(f["addr"] for f in funcs))
        size = {f["addr"]: f["insns"] for f in funcs}
        self.assertLessEqual(max(size[a] for a in bs[0]), min(size[a] for a in bs[1]))  # bands follow size
        d = E.draw(bs, 20)
        self.assertEqual(d, E.draw(bs, 20))  # seeded
        self.assertEqual([len(v) for v in d.values()], [4] * 5)

    def test_projection(self):
        import onboard_estimate as E

        def row(p, t, ok, b):
            return {"prepare_s": p, "translate_s": t, "pass": ok, "bytes": b}
        per = {0: [row(10, 2, 1, 2**20)] * 3, 1: [row(30, 6, 0, 3 * 2**20), row(None, 6, 0, 3 * 2**20)]}
        got = E.project({0: 100, 1: 50}, per, slots=2, jobs=4, calib=1.5, reps=50)
        hours = ((100 * 10 + 50 * 30) / 2 + (100 * 2 + 50 * 6) / 4) * 1.5 / 3600
        self.assertAlmostEqual(got["hours"][0], round(hours, 3))
        self.assertAlmostEqual(got["pass"][0], round(100 / 150, 3))
        self.assertAlmostEqual(got["GB"][0], round((100 + 150) / 1024, 3))
        for k in got:
            self.assertLessEqual(got[k][1], got[k][2])

    def test_rerun_keeps_earlier_prepares(self):  # a re-run's projection must not drop the prepare time
        import onboard_estimate as E
        (self.d / "state").mkdir()
        (self.d / "state" / "prepare_timing.jsonl").write_text(chr(10).join(json.dumps(r) for r in [
            {"addr": "a", "at": 100, "ok": True, "phases": {"total": 7.0}},
            {"addr": "b", "at": 900, "ok": True, "phases": {"total": 9.0}},
            {"addr": "c", "at": 950, "ok": False, "phases": {"total": 1.0}}]), encoding="utf-8")
        fresh, every = E.sample_prepares(self.d, ["a", "b", "c"], 500)
        self.assertEqual((fresh, every), ({"b": 9.0}, {"a": 7.0, "b": 9.0}))


class Translate(Base):
    def ctx(self, seconds=2900):
        self.fixture(project="ok", disc="ok", extract="ok", symbols="not-applicable", census="ok")
        a = onboard.argparse.Namespace(disc=None, here=True, host=None, dry_run=False, seconds=seconds)
        ctx = onboard.Ctx(a, self.d, "dreamcast", "X")
        ctx.verdicts = verdicts(self.d)
        return ctx

    def test_partial_pass_needs_no_key_and_writes_partial_gaps(self):
        import onboard_estimate as E
        from unittest import mock
        ctx, ws, calls = self.ctx(1000), self.d / "decomp", []
        (ws / "state").mkdir(parents=True)
        (ws / "project.json").write_text("{}", encoding="utf-8")  # no harness_reuse

        def run_tool(c, stage, argv, seconds, why=None, produces=None, changed=None):
            calls.append((argv, seconds))
            (ws / "state" / "wave_game.json").write_text(json.dumps(
                {"steps": {"scope": 1, "prepare": 1}, "scope": [f"8c0100{i}0" for i in range(5)],
                 "translated": ["8c010000", "8c010010"]}), encoding="utf-8")
            return 124, ["killed"]
        with mock.patch.object(S, "run_tool", run_tool), mock.patch.object(onboard, "heavy_refusal", lambda *x: None), \
                mock.patch.object(E.subprocess, "run", side_effect=AssertionError("no key check")):
            v = onboard.execute(ctx, S.BY_NAME["translate"])
        self.assertEqual(v["status"], "partial")  # a budget stop keeps its numbers; not DONE, so the next run resumes
        self.assertEqual((v["numbers"]["rows recorded"], v["numbers"]["left"], v["numbers"]["scope"]), (2, 3, 5))
        for k in ("recorded this run", "minutes", "per minute", f"timeouts at {E.FN_S} s",
                  f"timeouts at {E.FN_LATE_S} s", "projected minutes left"):
            self.assertIn(k, v["numbers"])
        self.assertIn("--fn-seconds-late", calls[0][0])
        self.assertNotIn(v["status"], onboard.DONE)
        self.assertIn("2 of 5 recorded (per function; chunks of 100), 3 left", v["message"])
        self.assertIn("--fn-seconds", calls[0][0])
        self.assertIn("not tried yet", v["message"])
        self.assertEqual(calls[0][1], 800)  # --seconds 1000 bounds the run
        self.assertIn("--stop-after", calls[0][0])
        gaps = (self.d / S.STATE_DIR / "GAPS.md").read_text(encoding="utf-8")
        self.assertIn("**PARTIAL:** 2 of 5 functions (40%) recorded", gaps)
        self.assertIn("partial GAPS.md written", v["message"])

    def test_resume_counts_per_function_and_names_its_progress(self):  # Saturn Bomberman, 2026-11-15
        import onboard_estimate as E
        from unittest import mock
        ctx, ws, calls = self.ctx(1000), self.d / "decomp", []
        (ws / "state").mkdir(parents=True)
        (ws / "project.json").write_text("{}", encoding="utf-8")
        scope = [f"8c0100{i}0" for i in range(5)]
        st = ws / "state" / "wave_game.json"
        st.write_text(json.dumps({"steps": {"scope": 1, "prepare": 1}, "scope": scope}), encoding="utf-8")
        (ws / "state" / "wave_game_translate.tsv").write_text("8c010000\tPASS\tcompare\tcurrent\t\n", encoding="utf-8")
        (ws / "state" / "onboard_translate_start.json").write_text('{"recorded": 0}', encoding="utf-8")

        def run_tool(c, stage, argv, seconds, why=None, produces=None, changed=None):
            calls.append(changed)
            with open(ws / "state" / "wave_game_translate.tsv", "a", encoding="utf-8") as f:  # killed mid-chunk,
                f.write("8c010010\tFAIL\tcompare\tcurrent\tdev 3/48\n")  # its functions kept one by one
            with open(ws / "state" / "wave_game_prepare.tsv", "a", encoding="utf-8") as f:
                f.write(f"8c010020\ttimeout\t{E.FN_S}\tRuntimeError: Ghidra timeout\n")
            return 124, ["killed"]
        with mock.patch.object(S, "run_tool", run_tool), mock.patch.object(onboard, "heavy_refusal", lambda *x: None):
            v = onboard.execute(ctx, S.BY_NAME["translate"])
            self.assertIn("resume: 1 of 5 functions recorded (was 0", calls[0])  # jobreg --changed: it moved on
            self.assertIn("3 of 5 recorded (per function", v["message"])
            self.assertIn("this run recorded 2 in", v["message"])
            self.assertIn("1 prepare timeout(s) at 60 s and 0 at 300 s", v["message"])
            self.assertEqual(v["numbers"]["timeouts at 60 s"], 1)
            gaps = (self.d / S.STATE_DIR / "GAPS.md").read_text(encoding="utf-8")
            self.assertIn("prepare killed at its limit (60 s)", gaps)
            onboard.execute(ctx, S.BY_NAME["translate"])  # progress since the last start: named again
            self.assertIn("resume: 3 of 5", calls[1])
            (ws / "state" / "onboard_translate_start.json").write_text('{"recorded": 3}', encoding="utf-8")
            with mock.patch.object(S, "run_tool", lambda *a, **k: calls.append(k.get("changed")) or (124, [])):
                onboard.execute(ctx, S.BY_NAME["translate"])
            self.assertIsNone(calls[2])  # none since the last start: no --changed, so jobreg refuses the same job

    def test_chunk_from_the_estimate(self):
        import onboard_estimate as E
        ctx = self.ctx()
        self.assertEqual(E.chunk_size(ctx, 2700), E.TX_CHUNK)  # no estimate yet
        (self.d / S.STATE_DIR / "estimate_detail.json").write_text(json.dumps(
            {"scope": 3951, "projection": {"hours": [17.0, 10.9, 23.1]}}), encoding="utf-8")
        self.assertEqual(E.chunk_size(ctx, 2700), int(0.66 * 2700 / (17.0 * 3600 / 3951)))  # 115
        st = S.ws_of(ctx) / "state"  # measured prepares slower than the estimate (Saturn Bomberman): they set the size
        st.mkdir(parents=True, exist_ok=True)
        rows = [{"phases": {"total": 5.0}}] * 25 + [{"phases": {"total": 900.0}, "ok": False}] * 5
        (st / "prepare_timing.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        per = (25 * 5 + 5 * 900) / 30 / E.SLOTS
        self.assertEqual(E.chunk_size(ctx, 2700), max(10, int(0.66 * 2700 / per)))
        (st / "prepare_timing.jsonl").write_text(json.dumps({"phases": {"total": 900.0}}) + "\n", encoding="utf-8")
        self.assertEqual(E.chunk_size(ctx, 2700), 115)  # one record is too few to measure a pace

    def test_jobreg_reuse_of_a_result_this_project_lacks(self):
        """jobreg points to an earlier finished job (a scratch project deleted and made again): run once more with
        --changed, so the stage does not stop on a result that is not there."""
        fake = self.d / "dp"
        (fake / "tools").mkdir(parents=True)
        (fake / "tools" / "bounded.py").write_text("import subprocess, sys\n"
                                                    "sys.exit(subprocess.run(sys.argv[3:]).returncode)\n")
        (fake / "tools" / "jobreg.py").write_text(
            "import pathlib, subprocess, sys\n"
            "a = sys.argv\n"
            "if '--changed' not in a: print('jobreg: NOT RUN: this job already finished'); sys.exit(0)\n"
            "sys.exit(subprocess.run(a[a.index('--') + 1:]).returncode)\n")
        out = self.d / "made.txt"
        (fake / "work.py").write_text(f"import pathlib; pathlib.Path(r'{out}').write_text('x')\n")
        a = onboard.argparse.Namespace(disc=None, here=True, host=None, dry_run=False)
        ctx = onboard.Ctx(a, self.d, "dreamcast", "X")
        ctx.tool = fake
        rc, _ = S.run_tool(ctx, "t", ["work.py"], 60, why="w", produces=out)
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        self.assertIn("--changed", S.log_path(ctx, "t").read_text(encoding="utf-8"))


class PassRange(unittest.TestCase):
    def test_never_100_100(self):
        import onboard_estimate as E
        p = E.pass_range(1.0, 1.0, 1.0, 60, 60)
        self.assertEqual(p[0], 1.0)
        self.assertLess(p[1], 0.95)  # 60 of 60 still allows about 6% FAIL
        self.assertGreater(p[1], 0.9)
        self.assertLessEqual(E.pass_range(0.94, 0.9, 0.99, 64, 68)[1], 64 / 68)
        for n in (1, 5, 60, 600):
            for k in (0, n // 2, n):
                lo, hi = E.wilson(k, n)
                self.assertLess(lo, hi)
                self.assertTrue(0 <= lo <= k / n <= hi <= 1)


class Boot(Base):
    def setup_ws(self, entry=None, registry=False, tx=False):
        gdi(self.d)
        ws = self.d / "decomp"
        (ws / "readable" / "names").mkdir(parents=True)
        (ws / "project.json").write_text(json.dumps({"binary_sha256": "ab" * 32, "load_address": "8c010000"}),
                                         encoding="utf-8")
        if registry:
            (ws / "readable" / "names" / "S01.json").write_text(json.dumps(
                {"functions": {"8c010100": {"proto": "void Game_Main(void)"}}}), encoding="utf-8")
        if tx:
            (ws / "translated_port").mkdir()
            (ws / "translated_port" / "8c010100.c").write_text("/* */", encoding="utf-8")
        self.fixture(project="ok", disc="ok", extract="ok", symbols="not-applicable", census="ok")
        v = verdicts(self.d)
        v["disc"]["paths"] = {"image": str(self.d / "game.gdi")}
        onboard.save(self.d, v["disc"])
        a = onboard.argparse.Namespace(disc=None, here=True, host=None, dry_run=False)
        ctx = onboard.Ctx(a, self.d, "dreamcast", "Test Game")
        ctx.verdicts = verdicts(self.d)
        if entry:
            (self.d / "port").mkdir()
            g = dict(json.loads(json.dumps(__import__("onboard_dc").starter(ctx, self.d / "game.gdi"))), entry=entry)
            (self.d / "port" / "game.json").write_text(json.dumps(g), encoding="utf-8")
        return ctx

    def run_boot(self, ctx, **patches):
        from unittest import mock
        sys.path.insert(0, str(TOOL / "port" / "tools"))
        import game_config
        with mock.patch.object(onboard, "heavy_refusal", lambda *x: None), \
                mock.patch.object(game_config, "vcvars32", lambda: patches.get("vcvars")), \
                mock.patch.object(S, "run_tool", patches.get("run_tool", S.run_tool)), \
                mock.patch("shutil.which", lambda n: patches.get("ninja")):
            return onboard.execute(ctx, S.BY_NAME["boot"])

    def test_missing_inputs_named(self):
        ctx = self.setup_ws()
        v = self.run_boot(ctx)
        self.assertEqual(v["status"], "error")
        self.assertIn("no build tried: wrote a starter", v["message"])
        self.assertIn('game.json "entry", a function registry, ', v["message"])  # a path's drive letter is no cut
        miss = " ".join(v["missing"])
        for words in ('"entry"', "function registry", "translator's C judged in port form", "vcvars32", "ninja"):
            self.assertIn(words, miss)
        self.assertIn("dp_judge.py", v["supply"])
        g = json.loads((self.d / "port" / "game.json").read_text(encoding="utf-8"))
        self.assertEqual((g["entry"], g["disc"], g["boot_sha256"], g["decomp"]),
                         ({"name": None, "addr": None}, "../game.gdi", ["ab" * 32], "../decomp"))

    def test_build_and_run_read(self):
        ctx = self.setup_ws(entry={"name": "Game_Main", "addr": "8c010100"}, registry=True, tx=True)
        seen = []

        def run_tool(c, stage, argv, seconds, why=None, produces=None):
            seen.append([str(x) for x in argv])
            (self.d / "port" / "build" / "onboard").mkdir(parents=True)
            (self.d / "port" / "build" / "onboard" / "port.exe").write_bytes(b"MZ")
            (self.d / "port" / "build" / "onboard_run").mkdir(parents=True)
            (self.d / "port" / "build" / "onboard_run" / "port.log").write_text(
                "ta: scene 1 (vblank 3): 1 pass(es)\nta: scene 2 (vblank 5): 1 pass(es)\n"
                "stub fn_8c0a0000 first call at vblank 9\nexit: must_work stub fn_8c0a0000 (3) after 9 ticks\n",
                encoding="utf-8")
            return 3, ["exit 3"]
        v = self.run_boot(ctx, vcvars="vcvars32.bat", ninja="ninja.exe", run_tool=run_tool)
        self.assertIn("--fixed-clock", seen[0])
        self.assertIn("--build-dir", seen[0])
        self.assertEqual(v["status"], "error")
        self.assertIn("boot: built, build.py exit 3, 9 vertical blanks, 2 scenes; first error: exit: must_work", v["message"])


class Gaps(Base):
    def ws(self):
        ws = self.d / "decomp"
        for sub in ("state", "readable/names", "harness/8c010040", "harness/8c010050"):
            (ws / sub).mkdir(parents=True, exist_ok=True)

        def w(p, x):
            (ws / p).write_text(x if isinstance(x, str) else json.dumps(x), encoding="utf-8")
        w("project.json", {"name": "Fixture"})
        w("state/wave_game.json", {"steps": {}, "scope": [f"8c0100{i}0" for i in range(7)], "no_harness": ["8c010060"]})
        w("state/ledger.json", {"functions": {"8c010000": {"status": "matched", "source": "translated"},
                                              "8c010030": {"status": "matched", "source": "agent"}}})
        w("state/wave_game_translate.tsv", "8c010010\tNOTRANSLATE\t\tcurrent\tno translation: ftrv at 8c010012\n"
          "8c010020\tFAIL\tcompare\tcurrent\tdev 3/48; r0 0x1 != 0x2\n"
          "8c010010\tNOTRANSLATE\t\tcurrent\tno translation: ftrv at 8c010014\n")
        w("harness/8c010040/meta.json", {"unsupported": "reads the TMU at 0xffd80000"})
        w("harness/8c010050/meta.json", {"signature": "void f(void)"})
        w("state/graph.tsv", "8c010000\t8c0a0000,8c0a0010,8c0a0020\t\n8c010010\t8c0a0000\t\n")
        w("readable/names/S01.json", {"functions": {"8c0a0000": {"proto": "void *syMalloc(u32 n)"},
                                                    "8c0a0010": {"proto": "void pdNotInKit(void)"},
                                                    "8c0a0020": {"proto": "void fn_8c0a0020(void)"}}})
        return ws

    def test_from_fixture_records(self):
        import onboard_gaps
        ws, out = self.ws(), self.d / "GAPS.md"
        n = onboard_gaps.write(ws, "dreamcast", out)
        text = out.read_text(encoding="utf-8")
        self.assertEqual((n["scope"], n["translated"], n["not translated"], n["refused/unjudged"], n["not tried"]),
                         (7, 1, 3, 2, 1), text)
        self.assertIn("Entry tables not scanned", text)
        self.assertIn("| no C: no translation: ftrv at # | 1 | 8c010010 |", text)  # the newer row, numbers folded
        self.assertIn("| FAIL at compare: dev #/#; r# # != # | 1 | 8c010020 |", text)
        self.assertIn("matched by an agent (no translator record) | 1 | 8c010030", text)
        self.assertIn("oracle refused: reads the TMU at #", text)
        self.assertIn("prepare failed (no current harness) | 1 | 8c010060", text)
        self.assertIn("prepared, never translated | 1 | 8c010050", text)
        self.assertIn("`pdNotInKit` (8c0a0010", text)
        self.assertNotIn("`syMalloc`", text)  # port/sdk/sy_heap.c defines it
        self.assertIn("- unnamed: 1 - kit gaps for these: not known", text)
        self.assertEqual(n["kit gaps"], 1)

    def test_saturn_without_bodies_says_not_known(self):
        import onboard_gaps
        out = self.d / "GAPS.md"
        n = onboard_gaps.write(self.ws(), "saturn", out)
        self.assertIn("kit gaps: not known - no state/function_bodies.txt", out.read_text(encoding="utf-8"))
        self.assertEqual(n["kit gaps"], "not known")

    def test_kit_list_is_read_from_the_kit(self):
        sys.path.insert(0, str(TOOL / "port" / "tools"))
        import kit_features
        f = kit_features.features()
        self.assertEqual(f["names"]["syMalloc"], "sy_heap")
        self.assertIn("gdfs", f["modules"])
        self.assertNotIn("main", f["names"])


class StaleLocks(unittest.TestCase):
    def test_a_held_lock_is_left_not_fatal(self):
        """WinError 32 on a lock the killed tree still held ended Saturn Bomberman's translate stage with no verdict."""
        import onboard_estimate as E
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "ghidra_slots" / "slot1").mkdir(parents=True)
        held, free = d / "ghidra_slots" / "slot1" / "G.lock~", d / "ghidra_slots" / "slot1" / "G.lock"
        held.write_text("x"), free.write_text("x")
        real, sleep = pathlib.Path.unlink, E.time.sleep

        def unlink(self, *a, **k):
            if self.name == "G.lock~":
                raise PermissionError(32, "in use")
            return real(self, *a, **k)
        pathlib.Path.unlink, E.time.sleep = unlink, lambda s: None
        try:
            got = E.stale_ghidra_locks(d)
        finally:
            pathlib.Path.unlink, E.time.sleep = real, sleep
        self.assertEqual(got, [free])
        self.assertTrue(held.exists())
        self.assertFalse(free.exists())


if __name__ == "__main__":
    unittest.main()
