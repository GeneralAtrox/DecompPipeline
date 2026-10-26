"""Per-step timing of ghidra.prepare (r15): where a prepare's seconds go.

A short prepare takes about 14 s, of which TcPrepare itself is a median 1 s; the rest is the JVM, project and
program opening, exit, the slot-0 save and the Python steps after it. Clock marks each step on the monotonic clock:
the Ghidra milestones come from its output lines (watch(), chained in front of the caller's on_line), the Python
steps from mark() calls in ghidra.prepare. Nothing here changes what a prepare writes into the packet or harness.

Each prepare appends one JSON line to state/prepare_timing.jsonl (rotated to .1 past ROTATE bytes):
  {"addr", "slot", "staging", "at" (epoch end), "ok", "marks": {step: seconds since launch},
   "phases": {jvm, startup, open, script, exit, post, total}}
The caller may pass a dict to ghidra.prepare(timing=...) to receive the same record (core/job puts it in the ledger
row, with the slot wait, and in events.log).
"""
import json
import os
import time

from . import paths

ROTATE = 32 * 2 ** 20
# Ghidra's output line -> mark, in the order a prepare prints them (the first matching line sets the mark)
LINES = (("Headless startup complete", "startup"),
         ("REPORT: Processing ", "project_open"),  # "project file" or, -readOnly, "read-only project file"
         ("REPORT: Execute script: TcPrepare.java", "script_start"),
         ("TcServe: begin ", "script_start"),  # core/ghidra_server: a request on a running server
         ("TcPrepare.java> TcPrepare:", "script_done"),
         ("REPORT: Save succeeded", "saved"))
# phase: (from mark, to mark); a phase whose marks are missing is left out
PHASES = (("jvm", "launch", "first_line"),  # analyzeHeadless.bat, its LaunchSupport JVMs, the JVM up to logging
          ("startup", "first_line", "startup"),  # class search, preferences, headless init
          ("open", "startup", "script_start"),  # project and program open, script compile; of which:
          ("project", "startup", "project_open"),  # the project (its lock) and the program's database
          ("program", "project_open", "script_start"),  # the program's open, the script bundle's compile
          ("script", "script_start", "script_done"),  # TcPrepare itself
          ("exit", "script_done", "exit"),  # the slot-0 save, JVM shutdown, the pipe drained
          ("post", "exit", "done"),  # split_packet, callee_drift.adopt, sig_override, stack_uninit, oracle_stamp
          ("total", "launch", "done"))


class Clock:
    def __init__(self, addr, slot, staging=False):
        self.addr, self.slot, self.staging = addr, slot, bool(staging)
        self.t0 = time.monotonic()
        self.marks = {"launch": 0.0}

    def mark(self, name):
        self.marks.setdefault(name, round(time.monotonic() - self.t0, 3))

    def watch(self, on_line=None):
        """An on_line callback that marks the milestones, then calls on_line."""
        def seen(line):
            self.mark("first_line")
            for text, name in LINES:
                if name not in self.marks and text in line:
                    self.mark(name)
            if on_line:
                on_line(line)
        return seen

    def record(self, ok=True):
        phases = {name: round(self.marks[b] - self.marks[a], 3) for name, a, b in PHASES
                  if a in self.marks and b in self.marks}
        return {"addr": self.addr, "slot": self.slot, "staging": self.staging, "at": round(time.time(), 1),
                "ok": ok, "marks": dict(self.marks), "phases": phases}

    def finish(self, timing=None, ok=True):
        """The record, appended to state/prepare_timing.jsonl and copied into `timing` (a dict) when given.
        Never raises: timing is a report, a failure to write it must not fail the prepare."""
        self.mark("done")
        rec = self.record(ok)
        if timing is not None:
            timing.clear()
            timing.update(rec)
        try:
            out = paths.STATE / "prepare_timing.jsonl"
            paths.STATE.mkdir(parents=True, exist_ok=True)
            if out.exists() and out.stat().st_size > ROTATE:
                os.replace(out, out.with_suffix(".jsonl.1"))
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError:
            pass
        return rec


def brief(rec):
    """'14.2 s (jvm 2.4, startup 3.5, open 2.8, script 3.4, exit 1.2, post 0.9)' for a log line."""
    ph = rec.get("phases") or {}
    parts = ", ".join(f"{k} {ph[k]:.1f}" for k, _, _ in PHASES
                      if k not in ("total", "project", "program") and k in ph)
    return f"{ph.get('total', 0):.1f} s ({parts})"
