"""tools/wave_game.py, the game route of `tools/wave.py run --game WS`, and the Xbox 360 reference-workspace helpers
(proof_pick.py sample, x360setup.py from another workspace). No Ghidra, no agent: the stage tools are fakes.
Run: python -W error tests/test_wave_game.py
"""
import contextlib
import io
import json
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

import wave_game  # noqa: E402

PY = sys.executable
W = wave_game.wave()


def workspace(name):
    ws = TMP / name
    (ws / "state").mkdir(parents=True, exist_ok=True)
    (ws / "project.json").write_text("{}", encoding="utf-8")
    (ws / "state" / "catalog.json").write_text('{"functions": []}', encoding="utf-8")
    return ws


def opts(**kw):
    kw = dict(dict(only=None, rd_max=None, stop_after=None, tx_chunk=0, fn_seconds=0, fn_seconds_late=0,
                  late_max=0, retry_late=False, novectors_max=0, novectors_give_up=False,
                  normal_priority=False), **kw)
    return mock.Mock(slots=2, jobs=4, workers=2, readable_workers=4, rd_chunk=2, draft="off", **kw)


class Pool(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(W, "STOP_FILE", TMP / "STOP_pool"))
        self.enterContext(mock.patch.object(W, "ram", lambda: 10.0))
        self.enterContext(mock.patch.object(wave_game, "POLL_S", 0.05))
        self.r = wave_game.Run(workspace("pool"), opts())

    def test_codes_in_order_and_width(self):
        cmds = [[PY, "-c", f"import sys, time; time.sleep(0.2); sys.exit({i})"] for i in range(4)]
        t0 = time.monotonic()
        self.assertEqual(self.r.pool(cmds, 30, 2), [0, 1, 2, 3])
        self.assertGreater(time.monotonic() - t0, 0.39)  # two at a time: two rounds of 0.2 s
        self.assertEqual(len(list((self.r.ws / "state" / "wave_game_logs").glob("*.log"))), 4)  # one log per child

    def test_limit_kills_its_own_child(self):
        self.assertEqual(self.r.pool([[PY, "-c", "import time; time.sleep(30)"]], 0.3, 1), [124])

    def test_stop_exits_3_with_state_saved(self):
        W.STOP_FILE.write_text("", encoding="utf-8")
        try:
            with self.assertRaises(SystemExit) as e:
                self.r.pool([[PY, "-c", "import time; time.sleep(30)"]], 60, 1)
            self.assertEqual(e.exception.code, 3)
            self.assertTrue(self.r.state_file.is_file())
        finally:
            W.STOP_FILE.unlink(missing_ok=True)

    def test_stop_from_before_the_start_is_ignored(self):  # another wave's STOP: kept for it, not obeyed here
        ctl = wave_game.wave_ctl
        try:
            ctl.write_stop(W.STOP_FILE)
            ctl.started(W.STOP_FILE)
            self.assertEqual(self.r.pool([[PY, "-c", "pass"]], 30, 1), [0])
            self.assertTrue(W.STOP_FILE.exists())
            ctl.write_stop(W.STOP_FILE)  # `wave.py stop` now: this wave stops
            with self.assertRaises(SystemExit) as e:
                self.r.pool([[PY, "-c", "import time; time.sleep(30)"]], 60, 1)
            self.assertEqual(e.exception.code, 3)
        finally:
            W.STOP_FILE.unlink(missing_ok=True)
            ctl._seen.pop(str(W.STOP_FILE), None)


class Route(unittest.TestCase):
    """The steps with fake children: what each step runs, and what it leaves to the next."""
    SCOPE = ["82000000", "82000100", "82000200", "82000300", "82000400"]

    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(W, "STOP_FILE", TMP / "STOP_route"))
        self.ws = workspace(f"route{time.time_ns()}{self._testMethodName}")  # monotonic_ns ticks 15 ms on Windows
        self.cur, self.ran = set(), []

    def ledger(self, rows):
        (self.ws / "state" / "ledger.json").write_text(json.dumps({"functions": rows}), encoding="utf-8")

    def fake(self, r, readable_rounds):
        def ask(what, *args):
            return {"reuse": True, "scope": self.SCOPE, "current": sorted(self.cur)}[what]

        def one(cmd, limit):
            tool = pathlib.Path(str(cmd[2])).name
            self.ran.append((tool, [str(c) for c in cmd[3:]]))
            if tool == "prepare_list.py":  # all but the last: its prepare fails
                self.cur |= set(self.SCOPE[:-1])
                return 1
            if tool == "translate_backlog.py":  # two translator PASSes
                self.ledger({a: {"status": "matched", "source": "translated"} for a in self.SCOPE[:2]})
                return 0
            rd = r.readable()
            if "verify" in cmd:  # refine.py verify: every listed file passes again
                for a in pathlib.Path(str(cmd[-3])[1:]).read_text(encoding="utf-8").split():
                    rd[a]["verified"] = 1
                (self.ws / "state" / "readable.json").write_text(json.dumps(rd), encoding="utf-8")
                return 0
            done = readable_rounds.pop(0) if readable_rounds else []
            rd.update({a: {"status": s} for a, s in done})
            (self.ws / "state" / "readable.json").write_text(json.dumps(rd), encoding="utf-8")
            return 0

        def pool(cmds, limit, width):
            led = r.ledger()
            for c in cmds:
                self.ran.append(("investigate", [str(c[-1])]))
                led[str(c[-1])] = {"status": "matched", "source": "agent"} if c[-1] == self.SCOPE[2] else {
                    "status": "failed"}
            self.ledger(led)
            return [0] * len(cmds)
        return mock.patch.multiple(r, ask=ask, one=one, pool=pool)

    def test_whole_route(self):
        r = wave_game.Run(self.ws, opts())
        rounds = [[("82000000", "readable"), ("82000100", "failed")], [("82000200", "unproven")]]  # unproven: done
        with self.fake(r, rounds):
            self.assertEqual(r.go(), 0)
        tools = [t for t, _ in self.ran]
        self.assertEqual(tools, ["prepare_list.py", "translate_backlog.py", "investigate", "investigate",
                                 "refine.py", "refine.py", "refine.py"])
        self.assertIn("verify", self.ran[6][1])  # the verify step, over the two passed files
        self.assertEqual(sorted(pathlib.Path(self.ran[6][1][-3][1:]).read_text(encoding="utf-8").split()),
                         ["82000000", "82000200"])
        self.assertIn("--serve", self.ran[0][1])
        self.assertIn("--record", self.ran[1][1])
        self.assertEqual({a for t, (a, *_) in self.ran if t == "investigate"}, {"82000200", "82000300"})
        self.assertIn("--draft", self.ran[4][1])
        s = json.loads((self.ws / "state" / "wave_game_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(s["route"], {"matched (translated)": 2, "matched (agent)": 1, "failed": 1, "no row": 1})
        self.assertEqual(s["no_harness"], ["82000400"])  # its prepare failed: never prepared again by the agent step
        self.assertEqual(s["readable"], {"readable": 1, "unproven": 1, "failed": 1})
        self.assertEqual(s["verified"], 1)  # gate 2's count: readable and verified (unproven is not)
        self.assertIsNone(s["stuck"])

        self.ran.clear()  # every step recorded: a second run does nothing but the summary
        r2 = wave_game.Run(self.ws, opts())
        with self.fake(r2, []):
            self.assertEqual(r2.go(), 0)
        self.assertEqual(self.ran, [])

    def test_only_and_rd_max(self):
        lst = TMP / f"only{time.monotonic_ns()}.txt"
        lst.write_text("82000100 # one\n0x82000200\n82000300\n99999999\n", encoding="utf-8")
        r = wave_game.Run(self.ws, opts(only=f"@{lst}", rd_max=1))
        with self.fake(r, [[(a, "readable") for a in self.SCOPE]]):  # one refine finishes all
            self.assertEqual(r.go(), 0)
        self.assertEqual(r.st["scope"], self.SCOPE[1:4])  # the listed ones in scope; 99999999 is not
        self.assertEqual({a for t, (a, *_) in self.ran if t == "investigate"}, {"82000200", "82000300"})
        self.assertEqual(len(r.st["rd_list"]), 1)  # matched: 82000100 (translated), 82000200 (agent); one drawn
        self.assertEqual([t for t, _ in self.ran].count("refine.py"), 2)  # the readable run and its verify
        self.assertEqual(wave_game.only_list("8200a0,0x82000100"), {"008200a0", "82000100"})

    def test_readable_stuck(self):
        r = wave_game.Run(self.ws, opts())
        with self.fake(r, []):  # refine finishes nothing, twice: stuck, exit 1
            self.assertEqual(r.go(), 1)
        self.assertEqual([t for t, _ in self.ran].count("refine.py"), 2)
        self.ran.clear()  # the next start (the cause fixed) tries the readable step again, and only it
        st = json.loads((self.ws / "state" / "wave_game.json").read_text(encoding="utf-8"))
        st["steps"]["readable"] = 1.0  # also when an older run saved the stuck step as done
        (self.ws / "state" / "wave_game.json").write_text(json.dumps(st), encoding="utf-8")
        r2 = wave_game.Run(self.ws, opts())
        with self.fake(r2, [[("82000000", "readable"), ("82000100", "readable"), ("82000200", "readable")]]):
            self.assertEqual(r2.go(), 0)
        self.assertEqual([t for t, _ in self.ran], ["refine.py", "refine.py"])  # readable, then verify
        self.assertIsNone(r2.st.get("stuck"))

    def test_stop_after_translate(self):  # the translator-only first pass (tools/AutoOnboard/autoonboard.py): no agent, no readable
        r = wave_game.Run(self.ws, opts(stop_after="translate"))
        with self.fake(r, []):
            self.assertEqual(r.go(), 0)
        self.assertEqual([t for t, _ in self.ran], ["prepare_list.py", "translate_backlog.py"])
        self.assertEqual(sorted(r.st["steps"]), ["prepare", "scope", "translate"])
        s = json.loads((self.ws / "state" / "wave_game_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(s["route"], {"matched (translated)": 2, "no row": 3})
        self.ran.clear()  # a run without the flag goes on at the agent step
        r2 = wave_game.Run(self.ws, opts())
        with self.fake(r2, [[(a, "readable") for a in self.SCOPE]]):
            self.assertEqual(r2.go(), 0)
        self.assertEqual([t for t, _ in self.ran][0], "investigate")

    def test_tx_chunk_resumes(self):  # a chunk recorded when it ends; a killed run translates only what is left
        lists, rcs = [], [0, 1]

        def ask(what, *a):
            return {"reuse": True, "scope": self.SCOPE, "current": sorted(self.cur)}[what]

        def one(cmd, limit):
            if pathlib.Path(str(cmd[2])).name == "translate_backlog.py":
                lists.append(pathlib.Path(str(cmd[cmd.index("--only") + 1])[1:]).read_text(encoding="utf-8").split())
                self.assertIn("--rows", [str(c) for c in cmd])
                return rcs.pop(0) if rcs else 0
            self.cur |= set(self.SCOPE)
            return 0
        r = wave_game.Run(self.ws, opts(tx_chunk=2, stop_after="translate"))
        with mock.patch.multiple(r, ask=ask, one=one), self.assertRaises(SystemExit):  # chunk 2 fails
            r.go()
        self.assertEqual(r.st["translated"], self.SCOPE[:2])  # chunk 1 stays recorded
        r2 = wave_game.Run(self.ws, opts(tx_chunk=2, stop_after="translate"))
        with mock.patch.multiple(r2, ask=ask, one=one):
            self.assertEqual(r2.go(), 0)
        self.assertEqual(lists, [self.SCOPE[:2], self.SCOPE[2:4], self.SCOPE[2:4], self.SCOPE[4:]])
        self.assertEqual(r2.st["translated"], self.SCOPE)

    def test_tx_chunk_prepares_each_chunk(self):  # a stopped first pass has every finished chunk judged, not just prepared
        seen = []

        def ask(what, *a):
            return {"reuse": True, "scope": self.SCOPE, "current": sorted(self.cur)}[what]

        def one(cmd, limit):
            tool = pathlib.Path(str(cmd[2])).name
            arg = next(str(c) for c in cmd[3:] if str(c).startswith("@"))
            listed = pathlib.Path(arg[1:]).read_text(encoding="utf-8").split()
            seen.append((tool, listed))
            if tool == "prepare_list.py":
                self.cur |= set(listed) - {self.SCOPE[3]}  # one prepare fails
                return 1
            return 0
        r = wave_game.Run(self.ws, opts(tx_chunk=2, stop_after="translate"))
        with mock.patch.multiple(r, ask=ask, one=one):
            self.assertEqual(r.go(), 0)
        P, T = "prepare_list.py", "translate_backlog.py"
        self.assertEqual(seen, [(P, self.SCOPE[:2]), (T, self.SCOPE[:2]), (P, self.SCOPE[2:4]), (T, self.SCOPE[2:4]),
                                (P, self.SCOPE[4:]), (T, self.SCOPE[4:])])
        self.assertEqual(r.st["prepare_failed"], 1)

    def test_tx_chunk_with_no_prepare_stops(self):  # Ghidra's project refused: the chunk is not recorded as done
        def ask(what, *a):
            return {"reuse": True, "scope": self.SCOPE, "current": []}[what]
        ran = []
        r = wave_game.Run(self.ws, opts(tx_chunk=len(self.SCOPE), stop_after="translate"))
        with mock.patch.multiple(r, ask=ask, one=lambda cmd, limit: ran.append(cmd) or 1), \
                mock.patch.object(wave_game, "WHOLE_CHUNK", 2), self.assertRaises(SystemExit) as e:
            r.go()
        self.assertIn("none of its", str(e.exception))
        self.assertEqual(r.st.get("translated", []), [])
        self.assertFalse(any("translate_backlog.py" in str(c) for cmd in ran for c in cmd))

    def test_stop_after_translate_needs_no_key(self):  # recording refuses a stale harness; no agent step is reached
        r = wave_game.Run(self.ws, opts(stop_after="translate"))
        with self.fake(r, []), mock.patch.object(r, "ask", lambda what, *a: False if what == "reuse" else
                                                 {"scope": self.SCOPE, "current": sorted(self.cur)}[what]):
            self.assertEqual(r.go(), 0)
        self.assertEqual([t for t, _ in self.ran], ["prepare_list.py", "translate_backlog.py"])

    def test_reuse_off_refused(self):  # a person's decision (no check proves it): the message names the file to set
        r = wave_game.Run(self.ws, opts())
        with mock.patch.object(r, "ask", lambda what, *a: False), self.assertRaises(SystemExit) as e:
            r.go()
        self.assertIn(f'"harness_reuse": true in {r.ws / "project.json"}', str(e.exception.code))

    def test_go_keeps_an_older_stop(self):  # a start no longer deletes the STOP another running wave may still read
        W.STOP_FILE.write_text("", encoding="utf-8")
        try:
            r = wave_game.Run(self.ws, opts())
            with self.fake(r, [[(a, "readable") for a in self.SCOPE]]):
                self.assertEqual(r.go(), 0)
            self.assertTrue(W.STOP_FILE.exists())
            self.assertFalse(r.stopped())  # written before this start
            W.STOP_FILE.write_text("again", encoding="utf-8")
            self.assertTrue(r.stopped())  # written since: obeyed
        finally:
            W.STOP_FILE.unlink(missing_ok=True)

    def test_unreadable_state_stops(self):  # present but unreadable: never a silent restart from step 1
        f = self.ws / "state" / "wave_game.json"
        for bad in ("{", "[]", '{"hours": {}}'):
            f.write_text(bad, encoding="utf-8")
            with self.assertRaises(SystemExit) as e:
                wave_game.Run(self.ws, opts())
            self.assertIn(str(f), str(e.exception.code))
        f.unlink()
        self.assertEqual(wave_game.Run(self.ws, opts()).st["steps"], {})  # missing: a first start


class PerFunction(unittest.TestCase):
    """Saturn Bomberman, 2026-11-15: three 45-minute runs each ended inside their first chunk with nothing recorded,
    and the functions whose prepare hangs to Ghidra's 900 s limit were tried again on every run."""
    SCOPE = ["06000000", "06000100", "06000200", "06000300", "06000400"]

    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(W, "STOP_FILE", TMP / "STOP_perfn"))
        self.ws = workspace(f"perfn{time.time_ns()}{self._testMethodName}")  # monotonic_ns ticks 15 ms on Windows
        self.cur, self.seen, self.preps = set(), [], 0

    def ask(self, what, *a):
        return {"reuse": True, "scope": self.SCOPE, "current": sorted(self.cur)}[what]

    def test_killed_mid_chunk_keeps_finished_functions(self):
        s = self.SCOPE

        class Killed(Exception):  # tools/bounded.py killing the whole tree mid-chunk
            pass

        def one(cmd, limit):
            cmd = [str(c) for c in cmd]
            tool = pathlib.Path(cmd[2]).name
            listed = pathlib.Path(next(c for c in cmd if c.startswith("@"))[1:]).read_text(encoding="utf-8").split()
            self.seen.append((tool, listed, cmd))
            rows = pathlib.Path(cmd[cmd.index("--rows") + 1])
            if tool == "prepare_list.py":
                self.preps += 1
                with open(rows, "a", encoding="utf-8") as f:
                    for a in listed:
                        if a == s[3]:  # KRNL's endless loop: killed at the limit
                            f.write(f"{a}\ttimeout\t300\tRuntimeError: Ghidra timeout\n")
                        elif a == s[4] and self.preps == 1:  # the server failed to start: not the function's fault
                            f.write(f"{a}\terror\t300\tStartError: no JVM\n")
                        else:
                            self.cur.add(a)
                            f.write(f"{a}\tprepared\t300\t\n")
                return 1
            with open(rows, "a", encoding="utf-8") as f:  # the first function judged, then the run is killed
                f.write(f"{listed[0]}\tPASS\tcompare\tcurrent\t\n")
            if self.preps == 1:  # the first run only
                raise Killed
            return 0

        r = wave_game.Run(self.ws, opts(tx_chunk=5, fn_seconds=300, stop_after="translate"))
        with mock.patch.multiple(r, ask=self.ask, one=one), self.assertRaises(Killed):
            r.go()
        self.assertEqual(r.st.get("translated", []), [])  # the chunk never ended ...
        self.assertEqual(wave_game.recorded(self.ws, r.st, 300), {s[0], s[3]})  # ... but its finished functions count
        self.assertIn("--kill-s", self.seen[0][2])
        self.assertEqual(self.seen[0][2][self.seen[0][2].index("--kill-s") + 1], "300")
        self.seen.clear()
        r2 = wave_game.Run(self.ws, opts(tx_chunk=5, fn_seconds=300, stop_after="translate"))
        with mock.patch.multiple(r2, ask=self.ask, one=one):
            self.assertEqual(r2.go(), 0)
        P, T_ = "prepare_list.py", "translate_backlog.py"
        # only the function the failed server start left is prepared again; the timeout is not, at the same limit
        self.assertEqual([(t, lst) for t, lst, _ in self.seen], [(P, [s[4]]), (T_, [s[1], s[2], s[4]])])
        self.assertEqual(wave_game.recorded(self.ws, r2.st, 300), set(s))
        self.assertEqual(r2.summary()["prepare_given_up"], {"timeout": 1})

    def test_ram_guard_waits_then_goes_on(self):  # Build box A shared, 2026-11-15: one guard kill ended the whole run
        s, lists, waits = self.SCOPE, [], []

        def one(cmd, limit):
            cmd = [str(c) for c in cmd]
            listed = pathlib.Path(next(c for c in cmd if c.startswith("@"))[1:]).read_text(encoding="utf-8").split()
            if pathlib.Path(cmd[2]).name == "prepare_list.py":
                self.preps += 1
                if self.preps == 1:
                    return "guard"
                self.cur |= set(listed)
                return 0
            lists.append(listed)
            with open(cmd[cmd.index("--rows") + 1], "a", encoding="utf-8") as f:
                f.write(f"{listed[0]}\tPASS\tcompare\tcurrent\t\n")
            return "guard" if len(lists) == 1 else 0
        r = wave_game.Run(self.ws, opts(tx_chunk=5, fn_seconds=300, stop_after="translate"))
        with mock.patch.multiple(r, ask=self.ask, one=one, wait_ram=lambda: waits.append(1)):
            self.assertEqual(r.go(), 0)
        self.assertEqual(len(waits), 2)  # once after the prepare kill, once after the translate kill
        self.assertEqual(lists, [s, s[1:]])  # the function judged before the kill is not judged again
        self.assertEqual(r.st["translated"], s)

    def test_wait_ram_needs_readings_in_a_row(self):
        r = wave_game.Run(self.ws, opts())
        readings = iter([95.0, 80.0, 90.0] + [80.0] * W.RESUME_READINGS + [99.0])
        with mock.patch.object(W, "ram", lambda: next(readings)), mock.patch.object(wave_game, "RAM_WAIT_S", 0),                 mock.patch.object(r, "stopped", lambda: False):
            r.wait_ram()
        self.assertEqual(next(readings), 99.0)  # it stopped after the readings in a row, not before

    def test_two_pass_prepare_limits(self):  # Saturn Bomberman, 2026-11-16: 300 s timeouts took ~70% of a chunk
        s, took = self.SCOPE, {self.SCOPE[1]: 100, self.SCOPE[3]: 400}  # fake prepare seconds: slow, hopeless
        first = {}

        def one(cmd, limit):
            cmd = [str(c) for c in cmd]
            tool = pathlib.Path(cmd[2]).name
            listed = pathlib.Path(next(c for c in cmd if c.startswith("@"))[1:]).read_text(encoding="utf-8").split()
            rows = pathlib.Path(cmd[cmd.index("--rows") + 1])
            if tool == "translate_backlog.py":
                with open(rows, "a", encoding="utf-8") as f:
                    f.writelines(f"{a}\tPASS\tcompare\tcurrent\t\n" for a in listed if a in self.cur)
                return 0
            lim = int(cmd[cmd.index("--kill-s") + 1])
            self.seen.append((lim, listed))
            with open(rows, "a", encoding="utf-8") as f:
                for a in listed:
                    if took.get(a, 1) > lim:
                        f.write(f"{a}\ttimeout\t{lim}\tRuntimeError: Ghidra timeout\n")
                    else:
                        self.cur.add(a)
                        f.write(f"{a}\tprepared\t{lim}\t\n")
            if len(self.seen) == 1:
                first.update(wave_game.prepare_rows(self.ws))
            return 1
        r = wave_game.Run(self.ws, opts(tx_chunk=5, fn_seconds=60, fn_seconds_late=300, stop_after="translate"))
        with mock.patch.multiple(r, ask=self.ask, one=one):
            self.assertEqual(r.go(), 0)
        self.assertEqual(self.seen, [(60, s), (300, [s[1], s[3]])])  # all at 60 s, then only the two that timed out
        self.assertEqual({a for a, v in first.items() if v[:2] == ("timeout", 60)}, {s[1], s[3]})
        rows = wave_game.prepare_rows(self.ws)
        self.assertEqual(rows[s[1]][:2], ("prepared", 300))  # rescued
        self.assertEqual(rows[s[3]][:2], ("timeout", 300))  # final: not tried again at 300
        self.assertEqual(set(wave_game.given_up(self.ws, 300)), {s[3]})
        self.assertEqual(r.summary()["prepare_given_up"], {"timeout": 1})
        self.seen.clear()
        r2 = wave_game.Run(self.ws, opts(tx_chunk=5, fn_seconds=60, fn_seconds_late=300, stop_after="translate"))
        with mock.patch.multiple(r2, ask=self.ask, one=one):
            r2.go()
        self.assertEqual(self.seen, [])  # a resumed run prepares nothing again

    def test_given_up_per_limit(self):
        (self.ws / "state" / wave_game.PREPARE_ROWS).write_text(
            "06000000\ttimeout\t300\tRuntimeError: Ghidra timeout\n"
            "06000100\terror\t300\tRuntimeError: Ghidra script error\n"
            "06000200\terror\t\tnot tried: StartError: x\n"
            "06000300\ttimeout\t\tRuntimeError: Ghidra timeout\n"
            "06000400\ttimeout\t300\tRuntimeError: Ghidra timeout\n06000400\tprepared\t600\t\n", encoding="utf-8")
        self.assertEqual(set(wave_game.given_up(self.ws, 300)), {"06000000", "06000100", "06000300"})
        self.assertEqual(set(wave_game.given_up(self.ws, 600)), {"06000100", "06000300"})  # a longer limit tries again
        self.assertEqual(set(wave_game.given_up(self.ws, 0)), {"06000100", "06000300"})  # the config's limit is longer
        self.assertEqual(set(wave_game.given_up(self.ws, None)), {"06000000", "06000100", "06000300"})  # for reports
        self.assertIn("killed at its limit (300 s)", wave_game.given_up(self.ws, 300)["06000000"][1])

    def test_prepare_list_rows_stream_as_each_ends(self):
        import prepare_list
        f = TMP / f"prep{time.monotonic_ns()}.tsv"
        got = []

        def prepare(a):
            if a == "06000000":
                time.sleep(0.4)
                raise RuntimeError("Ghidra timeout")
            return {"signature": "void f(void)"}, {}

        def on_row(rw):
            got.append(rw["addr"])
            prepare_list.append_row(f, rw, 300)
        rows = prepare_list.run({}, ["06000000", "06000100"], prepare, lambda a: None, 2, log=lambda m: None,
                                on_row=on_row)
        self.assertEqual(got, ["06000100", "06000000"])  # the fast one is written first, not after the slow one
        self.assertEqual([r["state"] for r in rows], ["timeout", "prepared"])
        self.assertEqual([ln.split("\t")[:3] for ln in f.read_text(encoding="utf-8").splitlines()],
                         [["06000100", "prepared", "300"], ["06000000", "timeout", "300"]])


class RetryPass(unittest.TestCase):
    """The retry pass under the coordinator's limits: --late-max, --retry-late, below-normal children, NOVECTORS."""
    SCOPE = ["06000000", "06000100", "06000200", "06000300", "06000400"]

    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(W, "STOP_FILE", TMP / "STOP_retry"))
        self.ws = workspace(f"retry{time.time_ns()}{self._testMethodName}")
        self.cur, self.seen = set(), []

    def ask(self, what, *a):
        if what == "widen":
            return {x: 192 for x in pathlib.Path(str(a[0])).read_text(encoding="utf-8").split()}
        return {"reuse": True, "scope": self.SCOPE, "current": sorted(self.cur)}[what]

    def rows(self, name, lines):
        with open(self.ws / "state" / name, "a", encoding="utf-8", newline="\n") as f:
            f.writelines(ln + "\n" for ln in lines)

    def run_one(self, r, translate=None):
        def one(cmd, limit):
            cmd = [str(c) for c in cmd]
            tool = pathlib.Path(cmd[2]).name
            listed = pathlib.Path(next(c for c in cmd if c.startswith("@"))[1:]).read_text(encoding="utf-8").split()
            self.seen.append((tool, listed, cmd[cmd.index("--kill-s") + 1] if "--kill-s" in cmd else None,
                              r.env().get("DP_SEED_OFFSET")))
            if tool == "prepare_list.py":
                self.cur |= set(listed)
                self.rows("wave_game_prepare.tsv", [f"{a}\tprepared\t300\t" for a in listed])
            elif translate:
                self.rows("wave_game_translate.tsv", translate(listed))
            return 0
        return mock.patch.multiple(r, ask=self.ask, one=one)

    def test_late_max_and_retry(self):
        s = self.SCOPE
        self.rows("wave_game_prepare.tsv", [f"{a}\ttimeout\t60\tkilled" for a in s[1:]])
        self.rows("wave_game_translate.tsv", [f"{s[0]}\tPASS\t\tcurrent\t"])
        done = {"steps": {"scope": 1, "prepare": 1, "translate": 1}, "hours": {}, "scope": s}
        (self.ws / "state" / "wave_game.json").write_text(json.dumps(done), encoding="utf-8")
        kw = dict(tx_chunk=10, stop_after="translate", fn_seconds=60, fn_seconds_late=300, late_max=2)
        r = wave_game.Run(self.ws, opts(**kw))
        with self.run_one(r):
            r.go()
        self.assertEqual(self.seen, [])  # steps.translate is set: nothing runs without --retry-late
        r = wave_game.Run(self.ws, opts(retry_late=True, **kw))
        with self.run_one(r):
            r.go()
        prep = [x for x in self.seen if x[0] == "prepare_list.py"]
        self.assertEqual([(x[1], x[2]) for x in prep], [(s[1:3], "300")])  # two of the four timeouts

    def test_below_normal_children(self):
        r = wave_game.Run(self.ws, opts())
        with mock.patch.object(wave_game.subprocess, "Popen") as pop:
            outs = [r.start([PY, "-c", "pass"])[1]]
            r.o.normal_priority = True
            outs.append(r.start([PY, "-c", "pass"])[1])
        for out in outs:  # start() hands the child's log back: the real caller closes it
            out.close()
        flags = [c.kwargs["creationflags"] for c in pop.call_args_list]
        self.assertEqual(flags, [wave_game.BELOW_NORMAL, 0] if sys.platform == "win32" else [0, 0])

    def test_novectors_reprepared_once_then_needs_inputs(self):
        s = self.SCOPE
        self.cur |= set(s[2:])  # the widened count changes the two stamps: no current harness
        self.rows("wave_game_translate.tsv", [f"{a}\tPASS\t\tcurrent\t" for a in s[2:]] + [
            f"{s[0]}\tNOVECTORS\t\tcurrent\t0/64 dev valid", f"{s[1]}\tNOVECTORS\t\tcurrent\t0/64 dev valid"])

        def translate(listed):  # the wider harness gives the first function vectors, not the second
            return [f"{a}\t{'PASS' if a == s[0] else 'NOVECTORS'}\t\tcurrent\t0/192 dev valid" for a in listed]
        kw = dict(tx_chunk=10, stop_after="translate", fn_seconds=60, fn_seconds_late=300)
        r = wave_game.Run(self.ws, opts(**kw))
        with self.run_one(r, translate):
            r.go()
        prep = [x for x in self.seen if x[0] == "prepare_list.py"]
        self.assertEqual([(x[3], x[2]) for x in prep], [("1", "300")], (self.seen, (self.ws / "state" / "wave_game.log").read_text()))
        last = wave_game.translate_rows(self.ws)
        self.assertEqual(last[s[0]][0], "PASS")
        self.assertEqual(last[s[1]][0], "NEEDS_INPUTS")
        self.assertIn("needs a human input set", last[s[1]][1])
        self.assertIn("0/192 dev valid", last[s[1]][1])
        n = len(self.seen)
        r2 = wave_game.Run(self.ws, opts(retry_late=True, **kw))
        with self.run_one(r2, translate):
            r2.go()
        self.assertEqual(len(self.seen), n)  # tried once: no second re-prepare

    def test_novectors_why_names_counts_and_reason(self):
        import translate_backlog
        why = translate_backlog.novectors_why({"dev_valid": 0, "dev_tried": 64, "hidden_valid": 3, "hidden_tried": 64,
                                               "invalid": {"loop": 60, "decode": 4}}, 16)
        self.assertIn("0/64 dev", why)
        self.assertIn("3/64 hidden", why)
        self.assertIn("loop", why)
        self.assertNotIn("re-prepare first", why)


class Rows(unittest.TestCase):
    def test_short_rows(self):  # translate_backlog --rows: one line per function, the reason the gaps writer groups
        import translate_backlog
        f = TMP / f"rows{time.monotonic_ns()}.tsv"
        translate_backlog.short_rows(f, [
            {"addr": "8c010000", "verdict": "NOTRANSLATE", "harness": "current", "why": "no translation: ftrv\nxx"},
            {"addr": "8c010010", "verdict": "FAIL", "stage": "compare", "harness": "current", "summary": "dev 3/48",
             "lint": [], "build": [], "diff": ["r0 0x1 != 0x2"]}])
        lines = f.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0].split("\t"), ["8c010000", "NOTRANSLATE", "", "current", "no translation: ftrv xx"])
        self.assertEqual(lines[1].split("\t")[4], "dev 3/48; r0 0x1 != 0x2")


class Entry(unittest.TestCase):
    def test_wave_run_game_dispatch(self):
        with mock.patch.object(sys, "argv", ["wave.py", "run", "--game", "WS", "--workers", "5"]), \
                mock.patch.object(wave_game, "main", return_value=7) as m:
            self.assertEqual(W.main(), 7)
        m.assert_called_once_with("run", "WS", ["--workers", "5"])

    def test_options_without_game_refused(self):
        with mock.patch.object(sys, "argv", ["wave.py", "status", "--workers", "5"]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            W.main()

    def test_no_catalog_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            wave_game.main("run", str(TMP / "nowhere"), [])


class X360Reference(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc" / "tools"))

    def test_sample(self):
        import proof_pick

        def f(a, size, callees=()):
            return {"addr": a, "size": size, "source": "pdata", "kind": "function", "refuse": [], "callees":
                    list(callees), "tail_calls": []}
        fs = [f(f"8200{i:04x}", 8 + i) for i in range(40)]
        fs.append(f("82100000", 500, [x["addr"] for x in fs[:30]]))  # a big tree: its closure is over the cap
        cat = {"functions": fs}
        roots, chosen = proof_pick.sample(cat, 25, seed=3, max_closure=10)
        self.assertEqual(len(chosen), 25)
        self.assertNotIn("82100000", chosen)
        self.assertEqual((roots, chosen), proof_pick.sample(cat, 25, seed=3, max_closure=10))  # seeded
        self.assertNotEqual(roots, proof_pick.sample(cat, 25, seed=4, max_closure=10)[0])

    def test_setup_from_a_workspace(self):
        from test_xbox360_ppc import TEXT, synthetic_image, synthetic_xex
        import x360setup
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        d = TMP / f"x360ref{time.monotonic_ns()}"
        d.mkdir()
        (d / "default.xex").write_bytes(synthetic_xex(synthetic_image(), encrypt=False))
        (d / "roots.txt").write_text(f"{TEXT:08x}\n", encoding="utf-8")
        first = x360setup.setup(d / "default.xex", d / "ws1", "T", roots_file=d / "roots.txt")
        with mock.patch.object(x360setup.catalog_inputs, "build", side_effect=AssertionError("no decrypt")):
            again = x360setup.setup(d / "ws1", d / "ws2", "T", roots_file=d / "roots.txt")
        self.assertEqual(again, first)
        for n in x360setup.INPUTS + ("ghidra_setup.txt",):
            self.assertEqual((d / "ws1" / n).read_bytes(), (d / "ws2" / n).read_bytes(), n)


class FreshReadable(unittest.TestCase):
    def test_run_makes_an_empty_registry(self):  # was: no readable/names, "0 function(s) to rewrite", silently
        import os
        import subprocess
        import test_batch21_runs as b21
        ws = b21.workspace()
        (ws / "readable/names").rmdir()
        (ws / "events.txt.ready0").write_text("x")  # the driver waits for two processes; this run is one
        env = dict(os.environ, DECOMP_PROJECT=str(ws), PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([sys.executable, "-c", b21.DRIVER, str(b21.TOOL), str(ws), str(ws / "events.txt"),
                              "claim", *b21.FNS[:2], "--workers", "1", "--attempts", "1"], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180).stdout
        self.assertIn("fresh readable stage: made", out)
        self.assertIn("2 function(s) to rewrite", out)
        self.assertTrue((ws / "readable/names").is_dir())
        self.assertIn('#include "rd_rt.h"', (ws / "readable/include/types.h").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
