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
    kw = dict(dict(only=None, rd_max=None), **kw)
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


class Route(unittest.TestCase):
    """The steps with fake children: what each step runs, and what it leaves to the next."""
    SCOPE = ["82000000", "82000100", "82000200", "82000300", "82000400"]

    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(W, "STOP_FILE", TMP / "STOP_route"))
        self.ws = workspace(f"route{time.monotonic_ns()}")
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

    def test_reuse_off_refused(self):
        r = wave_game.Run(self.ws, opts())
        with mock.patch.object(r, "ask", lambda what, *a: False), self.assertRaises(SystemExit):
            r.go()


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
