"""The game route of `tools/wave.py run --game WS`: one hands-off run of a workspace from its catalog to readable C,
translator first (docs/DEFINITION_OF_DONE.md gate 2; its ledger and readable.json are gate 3's figures).

Nearest existing: tools/wave.py - its run is the Dreamcast re-stamp wave over a plan's workspaces (hold, broker live
test, landing, recatalog, re-prepare of matched harnesses); it never prepares a new function, translates, runs an
agent or the readable stage. This route chains the stage tools that exist (prepare_list.py, translate_backlog.py,
decomp.py investigate, refine.py run) under one start, with wave.py's STOP file and RAM guard.

usage: python tools/wave.py run --game WS [--slots 2] [--jobs 4] [--workers 3] [--readable-workers 4]
           [--draft off|adopt|start] [--rd-chunk 60] [--only a,b|@FILE] [--rd-max N] [--stop-after STEP]
           [--tx-chunk N] [--fn-seconds S] [--fn-seconds-late L] [--late-max N] [--retry-late]
           [--novectors-max N] [--novectors-give-up] [--normal-priority]
  --only     the run's list is these functions of the scope, not the whole scope (a sample workspace's first run)
  --rd-max   the readable step takes at most N of the matched functions (a seeded draw, kept in the state file)
  --stop-after STEP  the run ends after that step (and the summary): `translate` is the translator-only first pass
             (tools/AutoOnboard/autoonboard.py). A later run without it goes on at the next step. A run that stops at or before
             translate needs no harness_reuse: recording refuses a stale harness, and the agent step is never reached
  --tx-chunk N  the translate step takes N functions at a time: it prepares the chunk's functions that have no current
             harness, then translates them (one translate_backlog call), and records the chunk when it ends; the
             prepare step leaves its work to these chunks. A run stopped or killed part way (an outer tools/bounded.py)
             has every finished chunk judged, and the next run translates only the chunks not done. Inside a chunk
             each function is recorded as it ends (prepare_list --rows: state/wave_game_prepare.tsv; translate_backlog
             --rows: state/wave_game_translate.tsv), so a run killed mid-chunk keeps those too (recorded())
  --fn-seconds-late L  a second pass: once no unrecorded function is left, the functions whose prepare was killed at a
             limit under L are prepared once more with --kill-s L (translated if that works); one killed at L is
             final. The first pass runs at a short --fn-seconds so a hung function costs little (FN_FIRST_S, FN_LATE_S)
  --late-max N  the late pass tries at most N of those functions (the first N of the scope; a later run, with the
             tried ones now prepared or killed at L, takes the next N)
  --retry-late  run the translate step again although wave_game.json says it is done: its late pass and the
             NOVECTORS pass (below) take what is left. Nothing is edited by hand in the state file
  NOVECTORS  a function whose harness has fewer than min_valid_vectors valid vectors per set is not left with a message
             nobody acts on: after the late pass the step re-prepares it ONCE with a fresh harness (state/vectors.json
             raises its dev vector count to 4x config vectors_dev, so its stamp changes, and DP_SEED_OFFSET=1 draws
             other vectors; the late limit applies), then judges it again. One still short is recorded as NEEDS_INPUTS
             (a line in wave_game_translate.tsv: "needs a human input set" with the valid counts and why vectors were
             invalid). Saturn's oracle has no per-function limits (oracle_limits is SH-4 only), so count and seed are
             the knobs. --novectors-max N takes at most N; --novectors-give-up marks the ones not tried NEEDS_INPUTS
  --normal-priority  the children start at normal priority; the default on Windows is below normal, so a build
             or a player on the same machine is not starved by the Ghidra slots
  --fn-seconds S  one function's prepare is killed after S s (prepare_list --kill-s). A function killed so, or whose
             prepare failed, is recorded with its reason and not prepared again at that limit (given_up()): a later
             run with a larger S tries the timeouts again. Saturn Bomberman, 2026-11-15: a few functions (KRNL's
             endless main loop) each held a slot about 900 s, then failed, on every resumed run
Steps, each recorded in WS/state/wave_game.json when done, so a run after a stop goes on where it ended:
  1 scope      the in-scope catalog functions (core/scope) not tiered "excluded" (core/orch.tier): the run's list
  2 prepare    tools/prepare_list.py --serve over those without a current harness, in chunks bounded by CHUNK_S,
               until a chunk exits 0 or two chunks running prepare nothing new
  3 translate  tools/translate_backlog.py --record over the list: a PASS is matched (ledger source "translated", no
               agent); a FAIL is kept, so the agent starts from it and the same C is not judged again. One short row
               per function (verdict, why) goes to state/wave_game_translate.tsv (translate_backlog --rows)
  4 agent      decomp.py investigate ADDR, --workers at a time, for each function with a current harness and no final
               ledger status (config harness_reuse must be on: no prepare repeats; a run that never reaches this step
               does not need it). A function whose prepare failed is
               listed in the summary, never prepared again with the same limits
  5 readable   refine.py run over the matched functions without a final readable status, in chunks of --rd-chunk
               bounded by RD_CHUNK_S, until none is left or two chunks running finish none
  6 verify     refine.py verify over the readable step's files not yet verified (a passing file is gate 2's
               `verified`; the readable step's own PASS is not)
  7 summary    state/wave_game_summary.json: functions by route, readable by status, verified, hours per step
Every child inherits the run id (core/oplog), so the run is one start in state/operator_steps.jsonl. Exit 0 when every
step ended (failed functions are results, not stops), 1 when a step was stuck, 3 on `wave.py stop` (one written after
this start: an older STOP is another wave's, core/wave_ctl). A state file that is there but unreadable stops the start.
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
sys.path.insert(0, str(TOOL))
from core import filelock, proof, wave_ctl  # noqa: E402  (none reads core.paths: safe before DECOMP_PROJECT is set)

PY = [sys.executable, "-B"]
BELOW_NORMAL = 0x00004000  # Windows BELOW_NORMAL_PRIORITY_CLASS (subprocess has it on Windows only)
NOVEC_DEV_X, NOVEC_SEED = 4, 1  # the NOVECTORS re-prepare: dev vectors x4, seed offset (core/ghidra.vector_seed)
CHUNK_S, TRANSLATE_S, AGENT_S, RD_CHUNK_S, POLL_S = 7200, 7200, 10800, 14400, 2.0
RAM_WAIT_S = 10  # between RAM readings while waiting out a guard kill (wave.py's levels)
WHOLE_CHUNK = 5  # --tx-chunk: a chunk this size or more with no prepare at all stops the run instead of being recorded as done
LEDGER_DONE = ("matched", "failed", "skipped")  # the main ledger's final statuses (core/orch), none of core/proof's
RD_PASSED = proof.PASSED  # an attempt passed (core/proof.readable_state): the file the verify step judges again
RD_DONE = RD_PASSED + ("failed", "blocked", "harness_stale", "raw_not_matched")  # and the ends a refine run records


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
            self.st = json.loads(filelock.read_text(self.state_file))
        except FileNotFoundError:  # a first start: step 1
            self.st = {"steps": {}, "hours": {}}
        except (OSError, ValueError) as e:
            self.st = f"{type(e).__name__}: {e}"
        if not isinstance(self.st, dict) or not isinstance(self.st.get("steps"), dict):  # never a silent step 1
            raise SystemExit(f"{self.state_file} is unreadable or not a wave_game state ({str(self.st)[:200]}): a run "
                             "would start again from step 1. Restore or fix it, or delete it to start from step 1")
        self.st.setdefault("hours", {})

    def log(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save(self):
        filelock.write_json(self.state_file, self.st, indent=1)  # atomic: a reader never sees half a state

    def env(self):
        env = dict(os.environ, DECOMP_PROJECT=str(self.ws), PYTHONUNBUFFERED="1")
        if getattr(self, "seed_offset", 0):  # the NOVECTORS re-prepare only (core/ghidra.SEED_OFFSET_ENV)
            env["DP_SEED_OFFSET"] = str(self.seed_offset)
        return env

    def stopped(self):
        return wave_ctl.stop_asked(wave().STOP_FILE)

    # --- children ---
    def start(self, cmd):
        logs = self.ws / "state" / "wave_game_logs"  # one per child: a pool's children run at once
        logs.mkdir(exist_ok=True)
        self.n = getattr(self, "n", 0) + 1
        out = open(logs / f"{time.strftime('%m%d-%H%M%S')}-{self.n}.log", "a", encoding="utf-8")
        out.write(f"$ {' '.join(map(str, cmd))}\n")
        out.flush()
        flags = BELOW_NORMAL if sys.platform == "win32" and not getattr(self.o, "normal_priority", False) else 0
        return subprocess.Popen([str(c) for c in cmd], cwd=TOOL, env=self.env(), stdout=out,
                                stderr=subprocess.STDOUT, creationflags=flags), out

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

    def wait_ram(self):
        """After a RAM guard kill: wave.py's RESUME_READINGS readings in a row under RESUME_UNDER (another job's
        memory on a shared box), so the run goes on instead of ending. Bounded by the run's outer bound; STOP exits 3."""
        w, ok = wave(), 0
        while ok < w.RESUME_READINGS:
            if self.stopped():
                self.save()
                self.log("STOP while waiting for RAM: state saved; run again to resume")
                sys.exit(3)
            ok = ok + 1 if w.ram() < w.RESUME_UNDER else 0
            if ok < w.RESUME_READINGS:
                time.sleep(RAM_WAIT_S)

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

    def current(self, funcs=None):
        return set(self.ask("current", self.listfile("current", self.st["scope"] if funcs is None else funcs)))

    def prepare_step(self):
        if getattr(self.o, "tx_chunk", 0):
            self.log("prepare: per translate chunk (--tx-chunk), each chunk's functions just before it is translated")
            return
        self.st["prepare_failed"] = self.prepare(self.st["scope"])

    def fn_seconds(self):
        return int(getattr(self.o, "fn_seconds", 0) or 0)

    def prepare(self, funcs, fn_s=None):
        """prepare_list.py --serve over those of funcs without a current harness, in CHUNK_S-bounded runs, until one
        exits 0 or two running prepare nothing new. Returns how many of funcs are left without one. Each function's
        result goes to state/wave_game_prepare.tsv as it ends; one given up (given_up) is not tried again."""
        idle = 0
        fn_s = self.fn_seconds() if fn_s is None else fn_s
        while True:
            cur, gone = self.current(funcs), given_up(self.ws, fn_s)
            todo = [a for a in funcs if a not in cur and a not in gone]
            if not todo:
                return sum(1 for a in funcs if a not in cur)
            rc = self.one(PY + ["tools/prepare_list.py", "--project", self.ws, f"@{self.listfile('prepare', todo)}",
                                "--serve", "--slots", self.o.slots, "--rows", self.ws / "state" / PREPARE_ROWS]
                          + (["--kill-s", fn_s] if fn_s else []), CHUNK_S)
            if rc == "guard" and getattr(self.o, "tx_chunk", 0):  # another job's memory: wait, then the rest
                self.log("prepare: killed by the RAM guard; waiting for RAM, then the functions not yet done")
                self.wait_ram()
                continue
            now = self.current(funcs)
            left = sum(1 for a in funcs if a not in now)
            self.log(f"prepare chunk: rc {rc}, {len(todo) - left} prepared, {left} without a current harness")
            idle = idle + 1 if left == len(todo) else 0
            if rc == 0 or idle >= 2 or rc not in (124, "guard"):
                return left

    def translate(self):
        per = getattr(self.o, "tx_chunk", 0)
        done = recorded(self.ws, self.st, self.fn_seconds()) if per else set(self.st.get("translated", []))
        left = [a for a in self.st["scope"] if a not in done]
        self.chunks(left, per, self.fn_seconds(), done)
        late = int(getattr(self.o, "fn_seconds_late", 0) or 0)
        if per and late > self.fn_seconds():  # nothing unrecorded is left: the 60 s timeouts get one longer try
            rows = prepare_rows(self.ws)
            slow = [a for a in self.st["scope"] if a in rows and rows[a][0] == "timeout" and rows[a][1] is not None
                    and rows[a][1] < late]
            self.log(f"translate: late pass, {len(slow)} function(s) killed at under {late} s are prepared again at {late} s")
            cap = int(getattr(self.o, "late_max", 0) or 0)
            if cap and len(slow) > cap:
                self.log(f"translate: --late-max {cap}: the first {cap} of {len(slow)} are tried; a later run takes the rest")
                slow = slow[:cap]
            self.chunks(slow, per, late, set())
        if per:
            self.novectors(per, late or self.fn_seconds())

    def novectors(self, per, fn_s):
        """Each function the translator reported NOVECTORS (too few valid vectors) is re-prepared once, wider (state/
        vectors.json, a seed offset), and judged again; one still short is recorded NEEDS_INPUTS with the reason."""
        tried = self.st.setdefault("novectors_tried", [])
        rows = translate_rows(self.ws)
        todo = [a for a in self.st["scope"] if (rows.get(a) or ("",))[0] == "NOVECTORS" and a not in tried]
        if not todo:
            return
        cap = int(getattr(self.o, "novectors_max", 0) or 0)
        take, rest = (todo[:cap], todo[cap:]) if cap else (todo, [])
        self.log(f"novectors: {len(todo)} function(s) have too few valid vectors; {len(take)} re-prepared once, wider")
        if take:
            widened = self.ask("widen", self.listfile("widen", take), NOVEC_DEV_X)
            self.seed_offset = NOVEC_SEED
            try:
                self.chunks(take, per, fn_s, set())
            finally:
                self.seed_offset = 0
            now = translate_rows(self.ws)
            short = [a for a in take if (now.get(a) or ("NOVECTORS",))[0] == "NOVECTORS"]
            note = f"re-prepared once with {NOVEC_DEV_X}x dev vectors and seed offset {NOVEC_SEED}"
            short_rows(self.ws, [(a, f"needs a human input set: {(now.get(a) or ('', ''))[1] or 'no row'}; {note}"
                                  f" (dev vectors {widened.get(a)})") for a in short])
            tried.extend(take)
            self.log(f"novectors: {len(take) - len(short)} of {len(take)} now have enough valid vectors, "
                     f"{len(short)} are NEEDS_INPUTS")
            self.save()
        if rest and getattr(self.o, "novectors_give_up", False):
            short_rows(self.ws, [(a, "needs a human input set: " + (rows.get(a) or ("", ""))[1]
                                  + "; not re-prepared (--novectors-give-up after a stop rule)") for a in rest])
            tried.extend(rest)
            self.log(f"novectors: {len(rest)} not re-prepared, recorded NEEDS_INPUTS")
            self.save()

    def chunks(self, left, per, fn_s, done):
        n = per or max(len(left), 1)
        self.log(f"translate: {len(done & set(self.st['scope']))} of {len(self.st['scope'])} recorded by an earlier "
                 f"start, {len(left)} left" + (f", in chunks of {n}, each prepared then translated" if per else ""))
        for i in range(0, len(left), n):
            chunk = left[i:i + n]
            failed = self.prepare(chunk, fn_s) if per else 0
            gone = set(given_up(self.ws, fn_s)) if per else set()
            if per and len(chunk) >= WHOLE_CHUNK and failed == len(chunk) and not gone & set(chunk):
                # not the functions (none was recorded as failing on its own): Ghidra, a lock, a disk
                raise SystemExit(f"prepare chunk: none of its {len(chunk)} function(s) prepared, so it is not recorded "
                                 f"(a fault around them, e.g. Ghidra's project refused): see {self.log_file}")
            todo = chunk
            while True:
                rc = self.one(PY + ["tools/translate_backlog.py", "--project", self.ws, "--only",
                                    f"@{self.listfile('translate', todo)}", "--jobs", self.o.jobs, "--record", "--rows",
                                    self.ws / "state" / TRANSLATE_ROWS], TRANSLATE_S)
                if rc != "guard" or not per:
                    break
                got = recorded(self.ws, {}, fn_s)  # the rows written before the kill are kept
                todo = [a for a in chunk if a not in got]
                self.log(f"translate: killed by the RAM guard; waiting for RAM, then {len(todo)} not yet judged")
                self.wait_ram()
                if not todo:
                    rc = 0
                    break
            self.log(f"translate: rc {rc}, {i + len(chunk)} of {len(left)} function(s)")
            if rc != 0:
                raise SystemExit(f"translate_backlog.py ended rc {rc}: see {self.log_file}")
            self.st.setdefault("translated", []).extend(chunk)
            if per:
                self.st["prepare_failed"] = self.st.get("prepare_failed", 0) + failed
            self.save()

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
               "prepare_given_up": dict(collections.Counter(s for s, _ in given_up(self.ws, None).values())),
               "hours": self.st["hours"], "stuck": self.st.get("stuck"), "run": os.environ.get("DP_RUN")}
        filelock.write_json(self.ws / "state" / "wave_game_summary.json", out, indent=1)
        self.log("summary: " + json.dumps({k: v for k, v in out.items() if k != "no_harness"}))
        return out

    def go(self):
        early = getattr(self.o, "stop_after", None) in ("scope", "prepare", "translate")
        if not early and not self.ask("reuse"):  # else every agent job would prepare its function again
            raise SystemExit(
                f"{self.ws}: config harness_reuse is off. No check proves a workspace's harnesses reusable: the oracle "
                "stamp does not cover the Ghidra project's own state, so turning it on is a person's decision (proven "
                "only on gamecube_ppc, whose target.json turns it on; AGENTS.md, harness_reuse). Once decided, set "
                f"\"harness_reuse\": true in {self.ws / 'project.json'} and run again (a run with --stop-after translate or "
                "earlier needs no key: recording refuses a stale harness, and it never reaches the agent step)")
        self.st.setdefault("started", time.time())
        self.st["steps"].pop(str(self.st.pop("stuck", None)).split(":")[0], None)  # a stuck step saved as done (before 64cbabe7)
        wave_ctl.started(wave().STOP_FILE)  # a STOP there now was for the waves before this start: ignored, kept
        if getattr(self.o, "retry_late", False):  # the translate step runs again: its late and NOVECTORS passes
            self.st["steps"].pop("translate", None)
        for name, fn in (("scope", self.scope), ("prepare", self.prepare_step), ("translate", self.translate),
                         ("agent", self.agent), ("readable", self.readable_step), ("verify", self.verify)):
            self.step(name, fn)
            if self.st.get("stuck"):  # a later step must not clear it (step pops it): the start ends stuck
                break
            if name == getattr(self.o, "stop_after", None):
                self.log(f"--stop-after {name}: the run ends here; a run without it goes on at the next step")
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
    if cmd == "_widen":  # {addr: dev vectors} after raising each listed function's count to factor x config vectors_dev
        from core import ghidra
        cfg, f = paths.config(), paths.STATE / "vectors.json"
        over = paths.read_json(f) or {}
        addrs = pathlib.Path(extra[0]).read_text(encoding="utf-8").split()
        for a in addrs:
            over[a] = max(ghidra.dev_vectors(cfg, a), int(extra[1]) * int(cfg["vectors_dev"]))
        paths.write_json(f, over)
        return {a: over[a] for a in addrs}
    if cmd == "_current":
        from core import job
        cfg = paths.config()
        want = pathlib.Path(extra[0]).read_text(encoding="utf-8").split()
        return [a for a in want if job.reusable_harness(cfg, a)]
    raise SystemExit(f"unknown internal command {cmd}")


PREPARE_ROWS, TRANSLATE_ROWS = "wave_game_prepare.tsv", "wave_game_translate.tsv"


def prepare_rows(ws):
    """{addr: (state, kill limit or None, detail)}: each function's latest line of state/wave_game_prepare.tsv."""
    out, f = {}, pathlib.Path(ws) / "state" / PREPARE_ROWS
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        p = ln.split("\t")
        if len(p) >= 4 and p[1] in ("prepared", "error", "timeout"):
            out[p[0]] = (p[1], int(p[2]) if p[2].isdigit() else None, p[3])
    return out


def translate_rows(ws):
    """{addr: (verdict, why)}: each function's latest line of state/wave_game_translate.tsv."""
    out = {}
    try:
        lines = (pathlib.Path(ws) / "state" / TRANSLATE_ROWS).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        p = ln.split("\t")
        if len(p) >= 2:
            out[p[0]] = (p[1], p[4] if len(p) > 4 else "")
    return out


def short_rows(ws, items):
    """Append NEEDS_INPUTS lines (addr, reason) to the translate rows, in translate_backlog's columns."""
    with open(pathlib.Path(ws) / "state" / TRANSLATE_ROWS, "a", encoding="utf-8", newline="\n") as f:
        for a, why in items:
            f.write("\t".join([a, "NEEDS_INPUTS", "", "current", " ".join(why.split())[:300]]) + "\n")


def given_up(ws, fn_s):
    """{addr: (state, reason)} of the functions not prepared again at kill limit fn_s (0: the config's): a prepare
    that failed on its own, or was killed at a limit at least fn_s. fn_s None: every function whose latest prepare
    failed or was killed, at any limit (the summary and GAPS.md). A Ghidra server that failed to start (StartError)
    and the functions not tried after it are not the functions' own failure: never given up."""
    out = {}
    for a, (state, lim, detail) in prepare_rows(ws).items():
        if state == "error" and not detail.startswith(("StartError", "not tried")):
            out[a] = ("error", f"prepare failed: {detail[:200]}")
        elif state == "timeout" and (fn_s is None or lim is None or (fn_s and fn_s <= lim)):
            out[a] = ("timeout", f"prepare killed at its limit ({lim or 'config'} s): not tried again at that limit")
    return out


def recorded(ws, st, fn_s=None):
    """The functions the translator pass has finished with, per function: those in recorded chunks, those with a
    translate_backlog --rows line (judged, or refused by it), and those whose prepare was given up (given_up)."""
    out = set(st.get("translated") or [])
    try:
        out |= {ln.split("\t", 1)[0] for ln in (pathlib.Path(ws) / "state" / TRANSLATE_ROWS).read_text(
            encoding="utf-8", errors="replace").splitlines() if "\t" in ln}
    except OSError:
        pass
    return out | set(given_up(ws, fn_s))


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
    ap.add_argument("--stop-after", choices=("scope", "prepare", "translate", "agent", "readable"))
    ap.add_argument("--tx-chunk", type=int, default=0)
    ap.add_argument("--fn-seconds", type=int, default=0)
    ap.add_argument("--fn-seconds-late", type=int, default=0)
    ap.add_argument("--late-max", type=int, default=0)
    ap.add_argument("--retry-late", action="store_true")
    ap.add_argument("--novectors-max", type=int, default=0)
    ap.add_argument("--novectors-give-up", action="store_true")
    ap.add_argument("--normal-priority", action="store_true")
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
        print(json.dumps(internal(sys.argv[1], sys.argv[2], sys.argv[3:])))
        sys.exit(0)
    sys.exit("run it as: python tools/wave.py run --game WS")
