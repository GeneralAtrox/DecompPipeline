"""The game route of `tools/wave.py run --game WS`: one hands-off run of a workspace from its catalog to readable C,
translator first (docs/DEFINITION_OF_DONE.md gate 2; its ledger and readable.json are gate 3's figures).

Nearest existing: tools/wave.py - its run is the Dreamcast re-stamp wave over a plan's workspaces (hold, broker live
test, landing, recatalog, re-prepare of matched harnesses); it never prepares a new function, translates, runs an
agent or the readable stage. This route chains the stage tools that exist (prepare_list.py, translate_backlog.py,
decomp.py investigate, refine.py run) under one start, with wave.py's STOP file and RAM guard.

usage: python tools/wave.py run --game WS [--slots 2] [--jobs 4] [--workers 3] [--readable-workers 4]
           [--draft off|adopt|start] [--rd-chunk 60] [--only a,b|@FILE] [--rd-max N]
  --only     the run's list is these functions of the scope, not the whole scope (a sample workspace's first run)
  --rd-max   the readable step takes at most N of the matched functions (a seeded draw, kept in the state file)
Steps, each recorded in WS/state/wave_game.json when done, so a run after a stop goes on where it ended:
  1 scope      the in-scope catalog functions (core/scope) not tiered "excluded" (core/orch.tier): the run's list
  2 prepare    tools/prepare_list.py --serve over those without a current harness, in chunks bounded by CHUNK_S,
               until a chunk exits 0 or two chunks running prepare nothing new
  3 translate  tools/translate_backlog.py --record over the list: a PASS is matched (ledger source "translated", no
               agent); a FAIL is kept, so the agent starts from it and the same C is not judged again
  4 agent      decomp.py investigate ADDR, --workers at a time, for each function with a current harness and no final
               ledger status (config harness_reuse must be on: no prepare repeats). A function whose prepare failed is
               listed in the summary, never prepared again with the same limits
  5 readable   refine.py run over the matched functions without a final readable status, in chunks of --rd-chunk
               bounded by RD_CHUNK_S, until none is left or two chunks running finish none
  6 verify     refine.py verify over the readable step's files not yet verified (a passing file is gate 2's
               `verified`; the readable step's own PASS is not)
  7 summary    state/wave_game_summary.json: functions by route, readable by status, verified, hours per step
Every child inherits the run id (core/oplog), so the run is one start in state/operator_steps.jsonl. Exit 0 when every
step ended (failed functions are results, not stops), 1 when a step was stuck, 3 on `wave.py stop`.
"""
import argparse
import collections
import importlib.util
import json
import os
import pathlib
import random
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
PY = [sys.executable, "-B"]
CHUNK_S, TRANSLATE_S, AGENT_S, RD_CHUNK_S, POLL_S = 7200, 7200, 10800, 14400, 2.0
LEDGER_DONE = ("matched", "failed", "skipped")
RD_DONE = ("readable", "unproven", "needs_review", "failed", "blocked", "harness_stale", "raw_not_matched")  # core/proof
RD_PASSED = ("readable", "unproven", "needs_review")  # an attempt passed: the file the verify step judges again


def wave():
    """tools/wave.py by its path (Python has a wave module of its own): its STOP file, RAM reading, tree kill, tiers."""
    if "dp_wave" not in sys.modules:
        spec = importlib.util.spec_from_file_location("dp_wave", TOOL / "tools" / "wave.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["dp_wave"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["dp_wave"]


class Run:
    def __init__(self, ws, opts):
        self.ws, self.o = pathlib.Path(ws).resolve(), opts
        self.state_file = self.ws / "state" / "wave_game.json"
        self.log_file = self.ws / "state" / "wave_game.log"
        try:
            self.st = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.st = {"steps": {}, "hours": {}}

    def log(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)

    def env(self):
        return dict(os.environ, DECOMP_PROJECT=str(self.ws), PYTHONUNBUFFERED="1")

    def stopped(self):
        return wave().STOP_FILE.exists()

    # --- children ---
    def start(self, cmd):
        logs = self.ws / "state" / "wave_game_logs"  # one per child: a pool's children run at once
        logs.mkdir(exist_ok=True)
        self.n = getattr(self, "n", 0) + 1
        out = open(logs / f"{time.strftime('%m%d-%H%M%S')}-{self.n}.log", "a", encoding="utf-8")
        out.write(f"$ {' '.join(map(str, cmd))}\n")
        out.flush()
        return subprocess.Popen([str(c) for c in cmd], cwd=TOOL, env=self.env(), stdout=out,
                                stderr=subprocess.STDOUT), out

    def pool(self, cmds, limit, width):
        """Run cmds, `width` at a time, each bounded by `limit` s. A STOP kills them all (exit 3); RAM at wave.py's
        KILL_AT kills the newest (its function stays to do: the next run takes it). [rc] in cmds' order."""
        w, todo, live, rcs = wave(), list(enumerate(cmds)), {}, [None] * len(cmds)
        while todo or live:
            while todo and len(live) < width:
                i, cmd = todo.pop(0)
                p, out = self.start(cmd)
                live[i] = (p, out, time.monotonic())
            time.sleep(POLL_S)
            stop, ram = self.stopped(), w.ram()
            for i, (p, out, t0) in sorted(live.items(), key=lambda x: -x[1][2]):
                why = "stop" if stop else 124 if time.monotonic() - t0 > limit else (
                    "guard" if ram >= w.KILL_AT and p.poll() is None else None)
                if why is not None and p.poll() is None:
                    self.log(f"killing child {p.pid} ({why}, RAM {ram:.1f}%)")
                    w.kill_tree(p.pid)
                    p.wait(60)
                    ram = 0.0 if why == "guard" else ram  # one kill per reading
                if p.poll() is not None:
                    out.close()
                    rcs[i] = why if why is not None else p.returncode
                    del live[i]
            if stop:
                self.save()
                self.log("STOP: state saved; run again to resume")
                sys.exit(3)
        return rcs

    def one(self, cmd, limit):
        return self.pool([cmd], limit, 1)[0]

    def ask(self, what, *args):
        """This module's internal command `what` in a child (core reads DECOMP_PROJECT at import): its JSON answer."""
        r = subprocess.run(PY + [__file__, "_" + what, str(self.ws), *map(str, args)], capture_output=True, text=True,
                           encoding="utf-8", timeout=1800, env=self.env(), cwd=TOOL)
        if r.returncode != 0:
            raise RuntimeError(f"_{what}: {r.stderr[-1500:]}")
        return json.loads(r.stdout.strip().splitlines()[-1])

    def ledger(self):
        try:
            return json.loads((self.ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
        except (OSError, ValueError, KeyError):
            return {}

    def readable(self):
        try:
            rd = json.loads((self.ws / "state" / "readable.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {a: r for a, r in rd.items() if isinstance(r, dict)}

    def listfile(self, name, addrs):
        p = self.ws / "state" / f"wave_game_{name}.txt"
        p.write_text("".join(a + "\n" for a in addrs), encoding="utf-8")
        return p

    # --- steps ---
    def step(self, name, fn):
        if self.st["steps"].get(name):
            return
        t0 = time.time()
        self.log(f"step {name}")
        self.st.pop("stuck", None)
        fn()
        self.st["hours"][name] = round(self.st["hours"].get(name, 0) + (time.time() - t0) / 3600, 3)
        if not self.st.get("stuck"):  # a stuck step is not done: the next start tries it again
            self.st["steps"][name] = time.time()
        self.save()

    def scope(self):
        self.st["scope"] = self.ask("scope")
        if getattr(self.o, "only", None):
            want = only_list(self.o.only)
            out = sorted(want - set(self.st["scope"]))
            self.st["scope"] = [a for a in self.st["scope"] if a in want]
            self.log(f"--only: {len(self.st['scope'])} of {len(want)} listed functions are in scope"
                     + (f"; not in scope: {', '.join(out[:10])}{' ...' if len(out) > 10 else ''}" if out else ""))
        self.log(f"scope: {len(self.st['scope'])} functions")

    def current(self):
        return set(self.ask("current", self.listfile("scope", self.st["scope"])))

    def prepare(self):
        idle = 0
        while True:
            cur = self.current()
            todo = [a for a in self.st["scope"] if a not in cur]
            if not todo:
                return
            rc = self.one(PY + ["tools/prepare_list.py", "--project", self.ws, f"@{self.listfile('prepare', todo)}",
                                "--serve", "--slots", self.o.slots], CHUNK_S)
            left = len(self.st["scope"]) - len(self.current())
            self.log(f"prepare chunk: rc {rc}, {len(todo) - left} prepared, {left} without a current harness")
            idle = idle + 1 if left == len(todo) else 0
            if rc == 0 or idle >= 2 or rc not in (124, "guard"):
                self.st["prepare_failed"] = left
                return

    def translate(self):
        rc = self.one(PY + ["tools/translate_backlog.py", "--project", self.ws, "--only",
                            f"@{self.listfile('scope', self.st['scope'])}", "--jobs", self.o.jobs, "--record"],
                      TRANSLATE_S)
        self.log(f"translate: rc {rc}")
        if rc != 0:
            raise SystemExit(f"translate_backlog.py ended rc {rc}: see {self.log_file}")

    def agent(self):
        cur = self.current()
        led = self.ledger()
        todo = [a for a in self.st["scope"] if a in cur and (led.get(a) or {}).get("status") not in LEDGER_DONE]
        self.st["no_harness"] = sorted(a for a in self.st["scope"] if a not in cur
                                       and (led.get(a) or {}).get("status") not in LEDGER_DONE)
        self.log(f"agent: {len(todo)} function(s) without a final status after the translator; "
                 f"{len(self.st['no_harness'])} without a current harness left alone")
        rcs = self.pool([PY + ["decomp.py", "--project", self.ws, "investigate", a] for a in todo], AGENT_S,
                        self.o.workers)
        self.st["agent_rc"] = dict(collections.Counter(str(rc) for rc in rcs))

    def readable_step(self):
        idle = 0
        while True:
            rd, led = self.readable(), self.ledger()
            todo = [a for a in self.rd_list(led) if (rd.get(a) or {}).get("status") not in RD_DONE]
            if not todo:
                return
            chunk = todo[:self.o.rd_chunk]
            cmd = PY + ["refine.py", "--project", self.ws, "run", f"@{self.listfile('readable', chunk)}",
                        "--workers", self.o.readable_workers] + (["--draft", self.o.draft] if self.o.draft else [])
            rc = self.one(cmd, RD_CHUNK_S)
            rd = self.readable()
            done = sum(1 for a in chunk if (rd.get(a) or {}).get("status") in RD_DONE)
            self.log(f"readable chunk: rc {rc}, {done}/{len(chunk)} finished, {len(todo) - done} left")
            idle = idle + 1 if done == 0 else 0
            if idle >= 2:
                self.st["stuck"] = f"readable: two chunks running finished none ({len(todo)} left)"
                return

    def verify(self):
        rd = self.readable()
        todo = [a for a in self.rd_list(self.ledger()) if (rd.get(a) or {}).get("status") in RD_PASSED
                and not (rd.get(a) or {}).get("verified")]
        if not todo:
            return
        rc = self.one(PY + ["refine.py", "--project", self.ws, "verify", f"@{self.listfile('verify', todo)}",
                            "--workers", self.o.readable_workers], RD_CHUNK_S)
        rd = self.readable()
        self.log(f"verify: rc {rc}, {sum(1 for a in todo if (rd.get(a) or {}).get('verified'))}/{len(todo)} verified")

    def rd_list(self, led):
        """The readable step's functions: the matched ones in the scope, or with --rd-max a seeded draw of N of them,
        drawn once and kept (st["rd_list"]) so a resumed run takes the same ones."""
        m = [a for a in self.st["scope"] if (led.get(a) or {}).get("status") == "matched"]
        n = getattr(self.o, "rd_max", None)
        if "rd_list" not in self.st and n and n < len(m):
            self.st["rd_list"] = sorted(random.Random(1).sample(m, n))
            self.log(f"--rd-max: the readable step takes {n} of {len(m)} matched functions")
            self.save()
        ms = set(m)
        return [a for a in self.st.get("rd_list", m) if a in ms]

    def summary(self):
        led, rd = self.ledger(), self.readable()
        route = collections.Counter()
        for a in self.st["scope"]:
            r = led.get(a) or {}
            src = r.get("source") if r.get("status") == "matched" else None
            route[f"matched ({src})" if src else (r.get("status") or "no row")] += 1
        out = {"scope": len(self.st["scope"]), "route": dict(route),
               "readable": dict(collections.Counter((rd.get(a) or {}).get("status") or "no row"
                                                    for a in self.st["scope"]
                                                    if (led.get(a) or {}).get("status") == "matched")),
               "verified": sum(1 for a in self.st["scope"] if (rd.get(a) or {}).get("status") == "readable"
                               and (rd.get(a) or {}).get("verified")),
               "no_harness": self.st.get("no_harness", []), "agent_rc": self.st.get("agent_rc"),
               "hours": self.st["hours"], "stuck": self.st.get("stuck"), "run": os.environ.get("DP_RUN")}
        (self.ws / "state" / "wave_game_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        self.log("summary: " + json.dumps({k: v for k, v in out.items() if k != "no_harness"}))
        return out

    def go(self):
        if not self.ask("reuse"):  # else every agent job would prepare its function again
            raise SystemExit(f"{self.ws}: config harness_reuse is off (project.json \"harness_reuse\": true turns "
                             "it on for this workspace once its harnesses are proven reusable)")
        self.st.setdefault("started", time.time())
        self.st["steps"].pop(str(self.st.pop("stuck", None)).split(":")[0], None)  # a stuck step saved as done (before 64cbabe7)
        wave().STOP_FILE.unlink(missing_ok=True)
        for name, fn in (("scope", self.scope), ("prepare", self.prepare), ("translate", self.translate),
                         ("agent", self.agent), ("readable", self.readable_step), ("verify", self.verify)):
            self.step(name, fn)
            if self.st.get("stuck"):  # a later step must not clear it (step pops it): the start ends stuck
                break
        return 1 if self.summary()["stuck"] else 0


def internal(cmd, ws, extra):
    """Child-process answers (DECOMP_PROJECT is ws): `_scope` the run's list, `_current` which of a list's functions
    have a current harness (core/job.reusable_harness, no writes)."""
    from core import paths
    if cmd == "_scope":
        t = wave().tiers(paths.read_json(paths.CATALOG), pathlib.Path(ws))
        return sorted(a for a, tier in t.items() if tier != "excluded")
    if cmd == "_reuse":
        return bool(paths.config().get("harness_reuse"))
    if cmd == "_current":
        from core import job
        cfg = paths.config()
        want = pathlib.Path(extra[0]).read_text(encoding="utf-8").split()
        return [a for a in want if job.reusable_harness(cfg, a)]
    raise SystemExit(f"unknown internal command {cmd}")


def only_list(arg):
    """--only's addresses: 'a,b' or @FILE (whitespace-separated, '#' comments), lower-case 8-digit hex."""
    text = pathlib.Path(arg[1:]).read_text(encoding="utf-8") if arg.startswith("@") else arg.replace(",", " ")
    return {w.lower().removeprefix("0x").zfill(8) for line in text.splitlines()
            for w in line.split("#")[0].split()}


def main(cmd, game, rest):
    """wave.py's `run --game WS [options]`."""
    ap = argparse.ArgumentParser(prog="wave.py run --game WS", description=__doc__.splitlines()[0])
    for k, v in (("--slots", 2), ("--jobs", 4), ("--workers", 3), ("--readable-workers", 4), ("--rd-chunk", 60)):
        ap.add_argument(k, type=int, default=v)
    ap.add_argument("--draft", choices=("off", "adopt", "start"))
    ap.add_argument("--only")
    ap.add_argument("--rd-max", type=int)
    o = ap.parse_args(rest)
    if cmd != "run" or not game:
        ap.error("the game route is `wave.py run --game WS`")
    ws = pathlib.Path(game).resolve()
    if not (ws / "project.json").is_file() or not (ws / "state" / "catalog.json").is_file():
        ap.error(f"{ws}: no project.json or state/catalog.json (set the workspace up first)")
    from core import oplog
    oplog.begin("wave.py run --game", [ws])
    return Run(ws, o).go()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1].startswith("_"):
        os.environ["DECOMP_PROJECT"] = sys.argv[2]
        sys.path.insert(0, str(TOOL))
        print(json.dumps(internal(sys.argv[1], sys.argv[2], sys.argv[3:])))
        sys.exit(0)
    sys.exit("run it as: python tools/wave.py run --game WS")
