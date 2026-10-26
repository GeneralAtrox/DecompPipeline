"""Batch 21: two `refine.py run` processes at once over overlapping target lists (TC prod4: 48 addresses listed twice,
two workers per function, sandbox and build folders deleted under each other, `error`/`blocked` rows).

Each process runs the real refine.py CLI (target dedupe, run_all, core/fn_claim, refine.refine, the ledger) with the
agent, sandbox build, preflight and judge faked: the fake sandbox build wipes and recreates sandbox/rd_ADDR as the
real one does, and the fake agent logs when it enters and leaves a function and writes work.c there. With claims,
each function is attempted by one worker at a time and exactly once in all (the second process finds it busy or
finished), and every row ends readable. The control (claims switched off) shows the overlap the test detects.
Run: python -W error tests/test_batch21_runs.py (also from tests/test_tooling.py).
"""
import collections
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP, TOOL  # noqa: E402

FNS = [f"8c0{n}000" for n in range(10, 16)]  # six functions; S01 holds the first three
DRIVER = r"""
import contextlib, os, pathlib, runpy, shutil, sys, threading, time
tool, ws, log_path, mode = sys.argv[1:5]
sys.path.insert(0, tool)
from core import agent, draft_run, fn_claim, header_snapshot, paths, readable, refine
from core import readable_check as rcheck


class Snap:
    hash, dir, include, names = "h", pathlib.Path(ws), readable.INCLUDE, readable.NAMES

    def release(self):
        pass


def build_sandbox(cfg, reg, addr, meta, prev_work, previous):  # what the real one does to the folder
    box = paths.SANDBOX / f"rd_{addr}"
    shutil.rmtree(box, ignore_errors=True)
    box.mkdir(parents=True, exist_ok=True)
    (box / "work.c").write_text("/* stub */")
    return box, "task"


lock = threading.Lock()


def note(*words):  # one log per process: two processes appending to one file tear lines on Windows
    with lock, open(f"{log_path}.{os.getpid()}", "a") as f:
        f.write(" ".join(map(str, words)) + "\n")


def run_attempt(cfg, box, task, transcript, log, stop):
    addr = box.name[3:]
    note("enter", addr, os.getpid(), threading.get_ident(), time.time())
    time.sleep(0.5)
    (box / "work.c").write_text(f"/* {addr} {os.getpid()} */")  # FileNotFoundError when a rival wiped the box
    note("exit", addr, os.getpid(), threading.get_ident(), time.time())
    return {}


header_snapshot.acquire = lambda cfg, log=None, **k: Snap()
refine.harness_stale = lambda cfg, a, **k: None
refine.preflight = lambda cfg, reg, a, meta: None
readable.Registry = lambda: None
refine.tree_hashes = lambda box: {}
refine.build_sandbox = build_sandbox
draft_run.first = lambda *a, **k: None  # no machine draft here (batch 23): the agents are what this test runs
agent.run_attempt = run_attempt
rcheck.check_readable = lambda cfg, a, work, bdir, **k: {"verdict": "PASS", "stage": "compare", "failing": 0,
                                                          "total": 1}
if mode == "noclaim":  # the control: batch 20's behaviour
    fn_claim.claim = lambda *a, **k: contextlib.nullcontext()
    fn_claim.skip_reason = lambda *a, **k: None
pathlib.Path(f"{log_path}.ready{os.getpid()}").write_text("x")  # start together
deadline = time.time() + 60
while len(list(pathlib.Path(log_path).parent.glob(pathlib.Path(log_path).name + ".ready*"))) < 2:
    if time.time() > deadline:
        sys.exit("the other process never started")
    time.sleep(0.01)
sys.argv = ["refine.py", "--project", ws, "run", *sys.argv[5:]]
runpy.run_path(os.path.join(tool, "refine.py"), run_name="__main__")
"""


def put(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def workspace():
    ws = pathlib.Path(tempfile.mkdtemp(prefix="runs", dir=TMP))
    put(ws / "project.json", {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin",
                              "function_lock_wait_s": 30})
    put(ws / "state/ledger.json", {"functions": {a: {"addr": a, "status": "matched"} for a in FNS}})
    put(ws / "state/systems.json", {"systems": [{"id": "S01", "hint": "", "functions": FNS[:3]}]})
    (ws / "readable/names").mkdir(parents=True)
    (ws / "readable/src").mkdir(parents=True)
    for a in FNS:
        put(ws / f"matched/{a}.c", "/* raw */")
        put(ws / f"harness/{a}/meta.json", {"callees": [], "signature": f"void fn_{a}(void)"})
    # list 1: CRLF, a BOM, upper case, 0x and repeats (prod4's merged list); list 2: a system id and the rest reversed
    (ws / "one.txt").write_bytes(("﻿" + "\r\n".join([FNS[0].upper(), "0x" + FNS[1], *FNS, FNS[2], FNS[0]])
                                  + "\r\n").encode("utf-8"))
    (ws / "two.txt").write_text("\n".join(["s01", *reversed(FNS)]) + "\n", encoding="utf-8")
    return ws


def run_pair(ws, mode):
    log = ws / "events.txt"
    env = dict(os.environ, DECOMP_PROJECT=str(ws), PYTHONDONTWRITEBYTECODE="1")
    outs = []
    procs = [subprocess.Popen([sys.executable, "-c", DRIVER, str(TOOL), str(ws), str(log), mode, f"@{ws / lst}",
                               "--workers", "3", "--attempts", "1"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True) for lst in ("one.txt", "two.txt")]
    try:
        for p in procs:
            outs.append(p.communicate(timeout=180)[0])
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.communicate(timeout=30)
    lines = [ln for f in sorted(log.parent.glob(log.name + ".*")) if f.suffix[1:].isdigit()
             for ln in f.read_text().splitlines()]  # each driver's own log (DRIVER note)
    return [p.returncode for p in procs], outs, lines


def intervals(lines):
    """{addr: [(start, end or None, pid)]} from the event log (an attempt that crashed has no exit)."""
    out, open_ = collections.defaultdict(list), {}
    for line in lines:
        kind, addr, pid, tid, t = line.split()
        if kind == "enter":
            open_[(addr, pid, tid)] = float(t)
        else:
            out[addr].append((open_.pop((addr, pid, tid)), float(t), pid))
    for (addr, pid, _), start in open_.items():
        out[addr].append((start, None, pid))
    return out


def overlaps(spans):
    s = sorted(spans)
    return any(b[0] < (a[1] if a[1] is not None else float("inf")) for a, b in zip(s, s[1:]))


class ConcurrentRuns(unittest.TestCase):
    def test_one_worker_per_function(self):
        ws = workspace()
        codes, outs, lines = run_pair(ws, "claim")
        self.assertEqual(codes, [0, 0], outs)
        spans = intervals(lines)
        self.assertEqual(sorted(spans), sorted(FNS), outs)
        for a in FNS:
            self.assertEqual(len(spans[a]), 1, (a, spans[a], outs))  # attempted once in all, never twice at once
            self.assertIsNotNone(spans[a][0][1])
        led = json.loads((ws / "state/readable.json").read_text(encoding="utf-8"))
        self.assertEqual({a: led[a]["status"] for a in FNS}, {a: "readable" for a in FNS}, outs)
        self.assertIn("10 item(s) listed, 10 function(s) after systems, 4 duplicate(s) dropped, 6 unique", outs[0])
        self.assertIn("7 item(s) listed, 9 function(s) after systems, 3 duplicate(s) dropped, 6 unique", outs[1])
        for out in outs:
            for a in FNS:
                self.assertLessEqual(out.count(f"== {a}:"), 1, out)
        both = outs[0] + outs[1]
        self.assertEqual(sum(both.count(f"== {a}: readable") for a in FNS), 6, both)
        self.assertNotIn("error", both.lower())

    def test_control_without_claims_overlaps(self):
        ws = workspace()
        _, outs, lines = run_pair(ws, "noclaim")
        spans = intervals(lines)
        self.assertTrue(any(overlaps(spans[a]) for a in FNS), (spans, outs))


if __name__ == "__main__":
    unittest.main(verbosity=1)
