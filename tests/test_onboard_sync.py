"""onboard.py --host: the build box is brought up to date before the run, with no hand steps (tools/AutoOnboard/onboard_remote.sync).

1. a remote with no DecompPipeline gets a clone from a git bundle at the local commit; the bundle is gone both sides.
2. a remote one commit behind is fetched and detached to the local commit; its git-ignored config.local.json stays.
3. uncommitted tracked changes in the remote checkout stop the sync with an error and are left as they are.
4. the disc's .cue and the .bin it names are copied to the remote project root when missing or of another size, and
   not again when the size matches.
5. remote_batch sends the remote's own disc path to the remote run, never the local --disc path; a failed sync is an
   error verdict and the run call is not made.
ONBOARD_SSH (the local Python for ssh) and ONBOARD_SCP (a file copy for scp) stand in for the machine; no ssh runs.

Nearest existing: tests/test_onboard_tool.py (class Remote) - the same ONBOARD_SSH stand-in, for the run call only.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools" / "AutoOnboard"), str(TOOL / "tools")]  # autoonboard.py moved there (076dc3ae4)
import autoonboard as onboard  # noqa: E402
import onboard_remote as R  # noqa: E402

COPY = ("import shutil,sys,os\n"
        "for s in sys.argv[1:-1]:\n"
        "    shutil.copy(s, os.path.join(sys.argv[-1], os.path.basename(s)))\n")


def git(cwd, *a):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *a],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class Sync(unittest.TestCase):
    def setUp(self):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="sync_", dir=TOOL / "tests" / ".work"))
        self.dp = self.d / "localdp"
        (self.dp / "tools").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "br", str(self.dp)], check=True, timeout=60)
        (self.dp / "tools" / "a.txt").write_text("one", encoding="utf-8")
        git(self.dp, "add", "-A")
        git(self.dp, "commit", "-qm", "one")
        self.proj = self.d / "proj"
        self.proj.mkdir()
        (self.proj / "g.cue").write_text('FILE "g.bin" BINARY\n  TRACK 01 MODE1/2352\n', encoding="utf-8")
        (self.proj / "g.bin").write_bytes(b"x" * 5000)
        self.far = self.d / "far"
        self.far.mkdir()
        self.rproj = self.far / "proj"
        self.rdp = self.far / "DecompPipeline"
        os.environ["ONBOARD_SSH"] = json.dumps([sys.executable, "-I", "-"])
        os.environ["ONBOARD_SCP"] = json.dumps([sys.executable, "-I", "-c", COPY])

    def tearDown(self):
        os.environ.pop("ONBOARD_SSH", None)
        os.environ.pop("ONBOARD_SCP", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def sync(self, disc=None):
        return R.sync("Build box A", self.dp, self.rdp.as_posix(), self.proj, self.rproj.as_posix(), disc, (".cue",))

    def test_clone_then_fetch_keeps_local_config(self):
        self.rproj.mkdir()
        got = self.sync()
        self.assertNotIn("error", got, got)
        self.assertEqual(git(self.rdp, "rev-parse", "HEAD"), git(self.dp, "rev-parse", "HEAD"))
        self.assertEqual((self.rdp / "tools" / "a.txt").read_text(encoding="utf-8"), "one")
        self.assertEqual(list(self.far.glob("*.bundle")), [])
        self.assertEqual(list((self.proj / ".work" / "onboard").glob("*.bundle")), [])
        # a second commit locally; the remote keeps an untracked config
        (self.rdp / "config.local.json").write_text("{}", encoding="utf-8")
        (self.dp / "tools" / "a.txt").write_text("two", encoding="utf-8")
        git(self.dp, "commit", "-qam", "two")
        got = self.sync()
        self.assertNotIn("error", got, got)
        self.assertEqual(git(self.rdp, "rev-parse", "HEAD"), git(self.dp, "rev-parse", "HEAD"))
        self.assertEqual((self.rdp / "tools" / "a.txt").read_text(encoding="utf-8"), "two")
        self.assertEqual((self.rdp / "config.local.json").read_text(encoding="utf-8"), "{}")
        self.assertEqual(list(self.far.glob("*.bundle")), [])

    def test_first_run_makes_the_remote_folders(self):
        # a build box that has never seen the game: neither the project nor the checkout's parent exists yet
        self.rproj = self.far / "Game" / "auto"
        self.rdp = self.far / "Game" / "dp"
        got = self.sync()
        self.assertNotIn("error", got, got)
        self.assertEqual(git(self.rdp, "rev-parse", "HEAD"), git(self.dp, "rev-parse", "HEAD"))
        self.assertTrue((self.rproj / "g.cue").is_file())

    def test_new_checkout_takes_a_sibling_machine_config(self):
        # the build box's other checkouts know its Ghidra, compiler and vcpkg paths; a new one must too
        other = self.far / "Other" / "dp"
        other.mkdir(parents=True)
        (other / "config.local.json").write_text('{"ghidra_home": "G:/ghidra", "vcpkg": "V:/v"}', encoding="utf-8")
        (self.far / "Bad" / "dp").mkdir(parents=True)
        (self.far / "Bad" / "dp" / "config.local.json").write_text('{"java_home": "J"}', encoding="utf-8")
        self.rproj = self.far / "Game" / "auto"
        self.rdp = self.far / "Game" / "dp"
        got = self.sync()
        self.assertNotIn("error", got, got)
        cfg = json.loads((self.rdp / "config.local.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["ghidra_home"], "G:/ghidra")
        # an existing machine config is never replaced
        (self.rdp / "config.local.json").write_text('{"ghidra_home": "mine"}', encoding="utf-8")
        (self.dp / "tools" / "a.txt").write_text("two", encoding="utf-8")
        git(self.dp, "commit", "-qam", "two")
        self.assertNotIn("error", self.sync())
        self.assertIn("mine", (self.rdp / "config.local.json").read_text(encoding="utf-8"))

    def test_symbol_sets_go_to_the_remote_reference_folder(self):
        sigs = self.d / "sigs"
        sigs.mkdir()
        (sigs / "sgl21.sig").write_bytes(b"s" * 300)
        got = R.sync("Build box A", self.dp, self.rdp.as_posix(), self.proj, self.rproj.as_posix(), None, (".cue",),
                     refs=[sigs / "sgl21.sig"])
        self.assertNotIn("error", got, got)
        self.assertEqual((self.rproj / "reference" / "sgl21.sig").read_bytes(), b"s" * 300)

    def test_shared_symbol_folder_found_from_a_project_inside_a_game_folder(self):
        import types
        import onboard_symbols
        shared = self.d / "Integrations" / "SegaSaturn" / "reference" / "symbols"
        shared.mkdir(parents=True)
        (shared / "sbl60.sig").write_bytes(b"x")
        proj = self.d / "Integrations" / "SegaSaturn" / "Some Game" / ".work" / "auto"
        proj.mkdir(parents=True)
        ctx = types.SimpleNamespace(project=proj, tool=self.d / "notool", console="saturn")
        self.assertEqual([d for d, _ in onboard_symbols.folders(ctx)], [shared.resolve()])
        self.assertEqual([f.name for f in onboard.symbol_files(ctx)], ["sbl60.sig"])

    def test_game_src_goes_to_the_remote_project_and_replaces_the_old(self):
        # the game's own native code (the game folder's port/game/*.c, copied to <project>/game_src): a stale remote file goes
        (self.rproj / "game_src").mkdir(parents=True)
        (self.rproj / "game_src" / "old.c").write_text("old", encoding="utf-8")
        src = self.d / "gs"
        src.mkdir()
        (src / "df_dialogue.c").write_text("int x;", encoding="utf-8")
        got = R.sync("Build box A", self.dp, self.rdp.as_posix(), self.proj, self.rproj.as_posix(), None, (".cue",),
                     game_src=[src / "df_dialogue.c"])
        self.assertNotIn("error", got, got)
        self.assertEqual(sorted(f.name for f in (self.rproj / "game_src").iterdir()), ["df_dialogue.c"])

    def test_game_src_is_copied_from_the_game_folder_and_reaches_the_build(self):
        import onboard_sat as SAT
        game = self.d / "Game"
        (game / "port" / "game").mkdir(parents=True)
        (game / "GameFiles").mkdir()
        (game / "LOCATIONS.md").write_text("x", encoding="utf-8")
        (game / "port" / "game" / "a.c").write_text("int a;", encoding="utf-8")
        (game / "port" / "game" / "a.h").write_text("extern int a;", encoding="utf-8")  # the game's header goes too
        (game / "port" / "game" / "notes.txt").write_text("x", encoding="utf-8")
        proj = game / ".work" / "auto"
        (proj / "game_src").mkdir(parents=True)
        (proj / "game_src" / "gone.c").write_text("old", encoding="utf-8")
        self.assertEqual(SAT.sync_game_src(proj), ["a.c", "a.h"])
        self.assertEqual(sorted(f.name for f in (proj / "game_src").iterdir()), ["a.c", "a.h"])
        src = (TOOL / "tools" / "AutoOnboard" / "onboard_sat.py").read_text(encoding="utf-8")
        self.assertIn('"--game-src", ctx.project / GAME_SRC', src)
        # the boot's sync to the remote project sends the same files (Saturn Bomberman's bm_game.h, 2026-11-18)
        boot = (TOOL / "tools" / "AutoOnboard" / "autoonboard.py").read_text(encoding="utf-8")
        self.assertIn('for g in ("*.c", "*.h") + S.GAME_FONTS for f in (ctx.project / "game_src").glob(g)', boot)
        # and the fonts the game's code loads, with their licences: flat in game_src, beside the exe in fonts/ (Dragon Force)
        (game / "port" / "game" / "fonts").mkdir()
        for n in ("F-Bold.ttf", "OFL-F.txt", "readme.txt"):
            (game / "port" / "game" / "fonts" / n).write_text(n, encoding="utf-8")
        self.assertEqual(SAT.sync_game_src(proj), ["F-Bold.ttf", "OFL-F.txt", "a.c", "a.h"])
        exe_dir = self.d / "build"
        (exe_dir / "fonts").mkdir(parents=True)
        (exe_dir / "fonts" / "stale.ttf").write_text("x", encoding="utf-8")
        self.assertEqual(SAT.S.place_fonts(proj / "game_src", exe_dir), ["F-Bold.ttf", "OFL-F.txt"])
        self.assertEqual(sorted(f.name for f in (exe_dir / "fonts").iterdir()), ["F-Bold.ttf", "OFL-F.txt"])
        self.assertIn("S.place_fonts(ctx.project / GAME_SRC, exe.parent)", src)
        self.assertIn('S.place_fonts(ctx.project / "game_src", dest)', boot)

    def test_dirty_remote_is_not_overwritten(self):
        self.rproj.mkdir()
        self.assertNotIn("error", self.sync())
        (self.rdp / "tools" / "a.txt").write_text("hand edit", encoding="utf-8")
        (self.dp / "tools" / "a.txt").write_text("two", encoding="utf-8")
        git(self.dp, "commit", "-qam", "two")
        got = self.sync()
        self.assertIn("uncommitted tracked changes", got["error"])
        self.assertEqual((self.rdp / "tools" / "a.txt").read_text(encoding="utf-8"), "hand edit")

    def test_disc_files_copied_when_missing_or_other_size(self):
        self.rproj.mkdir()
        got = self.sync()
        self.assertNotIn("error", got, got)
        self.assertEqual(got["disc"], self.rproj.as_posix() + "/g.cue")
        self.assertEqual((self.rproj / "g.bin").stat().st_size, 5000)
        self.assertTrue((self.rproj / "g.cue").is_file())
        stamp = (self.rproj / "g.bin").stat().st_mtime_ns
        self.assertNotIn("disc files copied", " ".join(self.sync()["notes"]))  # same sizes: nothing copied again
        self.assertEqual((self.rproj / "g.bin").stat().st_mtime_ns, stamp)
        (self.rproj / "g.bin").write_bytes(b"y" * 10)
        self.assertIn("g.bin", " ".join(self.sync()["notes"]))
        self.assertEqual((self.rproj / "g.bin").stat().st_size, 5000)

    def test_cue_naming_a_missing_track_is_an_error(self):
        (self.proj / "g.bin").unlink()
        self.assertIn("lists g.bin", self.sync()["error"])

    def test_explicit_disc_elsewhere_lands_in_the_remote_root(self):
        self.rproj.mkdir()
        other = self.d / "elsewhere"
        other.mkdir()
        (other / "h.cue").write_text('FILE "h.bin" BINARY\n', encoding="utf-8")
        (other / "h.bin").write_bytes(b"z" * 7)
        got = self.sync(disc=other / "h.cue")
        self.assertTrue(got["disc"].endswith("/proj/h.cue"), got)
        self.assertEqual((self.rproj / "h.bin").stat().st_size, 7)


class Batch(unittest.TestCase):
    def test_remote_gets_its_own_disc_path_and_a_failed_sync_stops_the_run(self):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        a = types.SimpleNamespace(host="Build box A", remote_dp="D:/dp", remote_project="D:/p", disc="C:/local/disc.cue",
                                  inputs=None, only=None, seconds=100, late_seconds=None, changed=None, here=False, dry_run=False)
        project = pathlib.Path(tempfile.mkdtemp(prefix="b_", dir=TOOL / "tests" / ".work"))
        ctx = onboard.Ctx(a, project, "saturn", "G")
        seen, old = {}, (R.run, R.sync)
        R.run = lambda host, dp, project, args, seconds: seen.setdefault("args", args) and {
            "error": "stop", "missing": [], "supply": ""}
        R.sync = lambda *x, **k: {"disc": "D:/p/disc.cue", "notes": []}
        try:
            onboard.remote_batch(ctx, a, ["disc"], {})
            args = seen["args"]
            self.assertEqual(args[args.index("--disc") + 1], "D:/p/disc.cue")
            self.assertNotIn("C:/local/disc.cue", args)
            seen.clear()
            R.sync = lambda *x, **k: {"error": "no scp", "missing": ["scp"], "supply": "scp"}
            self.assertFalse(onboard.remote_batch(ctx, a, ["disc"], {}))
            self.assertNotIn("args", seen)  # no run call after a failed sync
            self.assertIn("no scp", onboard.load(project)["disc"]["message"])
        finally:
            R.run, R.sync = old
            shutil.rmtree(project, ignore_errors=True)

    def test_a_remote_run_writes_the_game_folders_start_game_bat_here(self):
        # Dragon Force 2026-11-16: the project is <game>\.workuto and every stage ran on build box A, so no root bat
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        top = pathlib.Path(tempfile.mkdtemp(prefix="g_", dir=TOOL / "tests" / ".work"))
        game = top / "Some Game"
        (game / "GameFiles").mkdir(parents=True)
        (game / "LOCATIONS.md").write_text("x", encoding="utf-8")
        project = game / ".work" / "auto"
        (project / ".work" / "onboard").mkdir(parents=True)
        a = types.SimpleNamespace(host="Build box A", remote_dp="D:/dp", remote_project="D:/p", disc=None, inputs=None,
                                  only="boot", seconds=100, late_seconds=None, changed=None, here=False, dry_run=False)
        ctx = onboard.Ctx(a, project, "saturn", "Some Game")
        old = (R.run, R.sync)
        R.run = lambda *x, **k: {"error": "stop", "missing": [], "supply": ""}
        R.sync = lambda *x, **k: {"disc": None, "notes": []}
        try:
            onboard.remote_batch(ctx, a, ["boot"], {})
            bat = (game / "Start Game.bat").read_text(encoding="utf-8")
            self.assertIn(r".work\auto\Start Game.bat", bat)
            self.assertTrue((project / "Start Game.bat").is_file())
        finally:
            R.run, R.sync = old
            shutil.rmtree(top, ignore_errors=True)

    def test_a_remote_boot_brings_its_proven_build_and_flags_here(self):
        # Dragon Force 2026-11-16: boot proved port/build/onboard on build box A with --fallback on, but nothing brought that
        # build here; it was copied by hand and the bat started it with no flags, and the user's launch crashed.
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        top = pathlib.Path(tempfile.mkdtemp(prefix="g_", dir=TOOL / "tests" / ".work"))
        game = top / "Some Game"
        (game / "GameFiles").mkdir(parents=True)
        (game / "LOCATIONS.md").write_text("x", encoding="utf-8")
        project = game / ".work" / "auto"
        (project / ".work" / "onboard").mkdir(parents=True)
        far = top / "far"
        rbuild = far / "port" / "build" / "onboard_np"
        rbuild.mkdir(parents=True)
        files = {"game.exe": b"exe", "image.bin": b"img", "SDL3.dll": b"dll"}
        for n, b in files.items():
            (rbuild / n).write_bytes(b)
        (project / "port" / "build" / "onboard_np").mkdir(parents=True)
        (project / "port" / "build" / "onboard_np" / "game.exe").write_bytes(b"old")
        a = types.SimpleNamespace(host="Build box A", remote_dp="D:/dp", remote_project=far.as_posix(), disc=None, inputs=None,
                                  only="boot", seconds=100, late_seconds=None, changed=None, here=False, dry_run=False,
                                  fallback="on", pointers="off")
        ctx = onboard.Ctx(a, project, "saturn", "Some Game")
        launch = {"dir": "port/build/onboard_np", "files": sorted(files), "args": ["--fallback", "on"],
                  "config": "port/game.json"}
        # the game.json the build was made from comes back too: the boot's menu probe wrote confirm and cancel there
        cfg = '{"saturn": {"confirm": "C", "cancel": "B"}}\n'
        (far / "port" / "game.json").write_text(cfg, encoding="utf-8")
        old = (R.run, R.sync)
        R.run = lambda *x, **k: {"verdicts": {"project": {"stage": "project", "status": "ok"},
                                              "boot": {"stage": "boot", "status": "ok", "message": "ran",
                                                       "launch": launch}}, "log": "L", "rc": 0}
        R.sync = lambda *x, **k: {"disc": None, "notes": []}
        os.environ["ONBOARD_SCP"] = json.dumps([sys.executable, "-I", "-c", COPY])
        try:
            self.assertTrue(onboard.remote_batch(ctx, a, ["boot"], {}))
            here = project / "port" / "build" / "onboard_np"
            self.assertEqual({n: (here / n).read_bytes() for n in files if (here / n).is_file()}, files)
            self.assertFalse((project / ".work" / "onboard" / "fetch").exists())
            self.assertTrue((project / "port" / "game.json").is_file(), "the build's game.json was not brought here")
            self.assertEqual((project / "port" / "game.json").read_text(encoding="utf-8"), cfg)
            bat = (project / "Start Game.bat").read_text(encoding="ascii")
            line = next(ln for ln in bat.splitlines() if ln.startswith('start "" '))
            self.assertIn(r'"%~dp0port\build\onboard_np\game.exe"', line)
            self.assertIn(r'--image "%~dp0port\build\onboard_np\image.bin"', line)
            self.assertTrue(line.endswith(' --data-directory "%~dp0data" --fallback on %*'), line)
            root = (game / "Start Game.bat").read_text(encoding="ascii")
            self.assertIn(r'call "%~dp0.work\auto\Start Game.bat" %*', root)
            self.assertIn("copied to", onboard.load(project)["boot"]["here"])
            # a copy that fails leaves an error verdict, not an ok one the launcher would trust
            (rbuild / "SDL3.dll").unlink()
            self.assertFalse(onboard.remote_batch(ctx, a, ["boot"], {}))
            self.assertEqual(onboard.load(project)["boot"]["status"], "error")
        finally:
            R.run, R.sync = old
            os.environ.pop("ONBOARD_SCP", None)
            shutil.rmtree(top, ignore_errors=True)


    def test_a_later_run_that_drops_the_boot_verdict_keeps_the_bats_proven_flags(self):
        # Saturn Bomberman 2026-11-17: boot proved port/build/onboard with --fallback on and the bat got it; a translate run
        # at 00:36 dropped boot.json (drop_from) and rewrote Start Game.bat with no flags, so the user's battle ran without
        # the interpreter: STUB 0606b78e and 0606b734, no enemies.
        import onboard_sat as SAT
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        top = pathlib.Path(tempfile.mkdtemp(prefix="g_", dir=TOOL / "tests" / ".work"))
        try:
            project = top / "auto"
            state = project / ".work" / "onboard"
            state.mkdir(parents=True)
            (project / "port" / "build" / "onboard").mkdir(parents=True)
            (project / "port" / "build" / "onboard" / "game.exe").write_bytes(b"exe")
            launch = {"dir": "port/build/onboard", "files": ["game.exe", "image.bin"], "args": ["--fallback", "on"]}
            (state / "boot.json").write_text(json.dumps({"stage": "boot", "status": "ok", "launch": launch}),
                                             encoding="utf-8")

            def start_line():
                bat = (project / "Start Game.bat").read_text(encoding="ascii")
                return next(ln for ln in bat.splitlines() if ln.startswith('start "" '))

            SAT.launcher_files(project, "G")
            self.assertTrue(start_line().endswith(" --fallback on %*"), start_line())
            onboard.drop_from(project, {}, "boot")  # what autoonboard.py --only boot (or an earlier stage) does first
            kept = json.loads((state / "boot.json").read_text(encoding="utf-8"))  # kept as stale, with its launch
            self.assertEqual((kept["status"], kept["was"], kept["launch"]), ("stale", "ok", launch))
            SAT.launcher_files(project, "G")
            self.assertTrue(start_line().endswith(" --fallback on %*"), start_line())
            # with the build gone, the kept launch names nothing to start: the default build and no flags again
            shutil.rmtree(project / "port" / "build" / "onboard")
            SAT.launcher_files(project, "G")
            self.assertTrue(start_line().endswith('--data-directory "%~dp0data" %*'), start_line())
        finally:
            shutil.rmtree(top, ignore_errors=True)

    def test_play_logs_here_seed_the_remote_boot(self):
        # Saturn Bomberman 2026-11-17: the user's battle called 0606b78e and 0606b734 (KRNL code the census missed); boot's
        # seeds came only from its own 15 s headless run on the build box, which never reaches a battle, so they were
        # never translated.
        import onboard_sat as SAT
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        top = pathlib.Path(tempfile.mkdtemp(prefix="g_", dir=TOOL / "tests" / ".work"))
        try:
            project = top / "auto"
            (project / ".work" / "onboard").mkdir(parents=True)
            logs, dumps = project / "data" / "logs", project / "data" / "scene_dumps"
            logs.mkdir(parents=True)
            (dumps / "1_replay").mkdir(parents=True)
            (logs / "port.1.log").write_text(
                "STUB #1 (master): call to 0606b78e has no translated function (returns 0); the guest holds 7ffcd32b\n"
                "STUB #2 (master): call to 00000000 has no translated function (returns 0)\n", encoding="utf-8")
            (dumps / "1.json").write_text(json.dumps({"host": {"log_warnings": [
                "STUB #2 (slave): call to 0606b734 has no translated function (returns 0); the guest holds d341e030"]}}),
                encoding="utf-8")
            (dumps / "1_replay" / "port.log").write_text(
                "fallback: 0607a000 entries 3 instructions 96 from 0606b6bc < 0606ae6c\n", encoding="utf-8")
            self.assertEqual(SAT.play_seeds(project), ["0606b734", "0606b78e", "0607a000"])
            a = types.SimpleNamespace(host="Build box A", remote_dp="D:/dp", remote_project="D:/p", disc=None, inputs=None,
                                      only="boot", seconds=100, late_seconds=None, changed=None, here=False,
                                      dry_run=False, fallback="on", pointers="on")
            ctx = onboard.Ctx(a, project, "saturn", "G")
            seen, old = {}, (R.run, R.sync)
            R.run = lambda host, dp, project, args, seconds: seen.setdefault("args", args) and {
                "error": "stop", "missing": [], "supply": ""}
            R.sync = lambda *x, **k: {"disc": None, "notes": []}
            try:
                onboard.remote_batch(ctx, a, ["boot"], {})
            finally:
                R.run, R.sync = old
            args = seen["args"]
            self.assertEqual(args[args.index("--seeds") + 1], "0606b734,0606b78e,0607a000")
            # the remote side: --seeds reaches the boot's context and goes into the seeds file it translates from
            got = onboard.Ctx(types.SimpleNamespace(disc=None, here=True, host=None, dry_run=False,
                                                    seeds="0606b734,0606b78e"), project, "saturn", "G")
            ws = top / "decomp"
            self.assertEqual(SAT.take_seeds(ws, got.seeds), ["0606b734", "0606b78e"])
            self.assertEqual(SAT.take_seeds(ws, got.seeds), [])
            self.assertEqual(SAT.read_seeds(ws), ["0606b734", "0606b78e"])
        finally:
            shutil.rmtree(top, ignore_errors=True)


class BootRunsSeeChanged(unittest.TestCase):
    """onboard_sat.run_exe: the boot's game runs pass --changed to jobreg, as the build does (a rebuilt exe is a new job)."""

    def test_the_boot_runs_pass_changed_to_jobreg(self):
        import onboard_sat
        from unittest import mock
        top = pathlib.Path(tempfile.mkdtemp())
        try:
            got = []
            ctx = types.SimpleNamespace(tool=TOOL, changed="game_src/x.c fixed")
            with mock.patch.object(onboard_sat.S, "log_path", lambda c, st: top / "boot.log"),                     mock.patch.object(onboard_sat.subprocess, "run", lambda cmd, **k: got.append(cmd) or types.SimpleNamespace(returncode=0)):
                onboard_sat.run_exe(ctx, top / "game.exe", ["--headless"], 5, "why")
                onboard_sat.run_exe(types.SimpleNamespace(tool=TOOL, changed=None), top / "game.exe", ["--headless"], 5, "why")
            first = got[0][:got[0].index("--")]
            self.assertIn("--changed", first, "a rebuilt exe's runs must name --changed for jobreg")
            self.assertEqual(first[first.index("--changed") + 1], "game_src/x.c fixed")
            self.assertNotIn("--changed", got[1][:got[1].index("--")])
        finally:
            shutil.rmtree(top, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
