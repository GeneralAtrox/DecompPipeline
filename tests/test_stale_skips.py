"""Stale skips (core/stale_skips.py): finished rows whose blocker kind a landing fixed after they were judged.
1. stale: a row of a kind with "fixed_at" is stale when it was judged before it (its "blocker" record, else its
   "finished" time); a kind with no fixed_at, a matched or queued row, and a row judged after the fix are left alone;
2. job.finish records the kind and its fixed_at on an unmatched row (none on a matched one), so the same fix never
   requeues a row twice;
3. tools/requeue.py --stale-skips (offline) and Orchestrator.requeue_stale_skips (decomp.py serve) requeue exactly
   the stale rows of takeable functions, one note per kind: never one in the excluded tier (Spawn main 8c024cbe, the
   CCR cache routine: sys_ops) or out of the catalog's scope.
The count over the three projects' ledgers is in the r15 notes (Lane 2 item 8).
Run: python -W error tests/test_stale_skips.py (also from test_tooling.py).
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import PROJECT, TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import job, orch, stale_skips  # noqa: E402

FIX = "2026-10-18T18:59:38"
KINDS = [{"id": "entry_regs", "status": ["skipped"], "detail": "^unsupported: reads (f?r\\d+(, )?)+ at entry",
          "fixed_at": FIX},
         {"id": "oracle_bad_pointers", "status": ["skipped"], "detail": "^oracle could not produce .*write outside RAM"}]
REGS = "unsupported: reads r0, r1, r3 at entry (not argument registers)"


def row(a, status="skipped", detail=REGS, finished="2026-10-11T08:14:13", **kw):
    return dict({"addr": a, "status": status, "attempts": [], "verdict_detail": detail, "finished": finished}, **kw)


# Spawn gend 8c563e40 (skipped 10-11, before the fix), ovl 8c5f6ef0 (10-17, fr2, before), a row skipped after the fix,
# rows recorded under the old fix (none) and under this one, an unfixed kind, a matched and a queued row.
ROWS = {"8c563e40": row("8c563e40"),
        "8c5f6ef0": row("8c5f6ef0", detail="unsupported: reads fr2 at entry (not argument registers)",
                        finished="2026-10-17T15:39:43"),
        "8c600000": row("8c600000", finished="2026-10-19T10:00:00"),
        "8c600100": row("8c600100", finished="2026-10-20T10:00:00", blocker={"kind": "entry_regs", "fixed_at": None}),
        "8c600200": row("8c600200", finished="2026-10-01T10:00:00", blocker={"kind": "entry_regs", "fixed_at": FIX}),
        "8c600300": row("8c600300", detail="oracle could not produce 48+ valid vectors per set (write outside RAM)"),
        "8c600400": row("8c600400", status="matched", detail="PASS"),
        "8c600500": row("8c600500", status="queued"),
        "8c5f0000": row("8c5f0000", detail="unsupported: reads r2, r3 at entry (not argument registers)"),
        "8c024cbe": row("8c024cbe", detail="unsupported: reads r2, r3 at entry (not argument registers)")}
STALE = ["8c563e40", "8c5f6ef0", "8c600100"]
UNTAKEABLE = ["8c024cbe", "8c5f0000"]  # out of scope; sys_ops (the excluded tier)
TAKEABLE = set(ROWS) - set(UNTAKEABLE)


def entry(a, sys_ops=0):
    """A catalog entry (core/orch.score and tier read these)."""
    return {"addr": a, "insns": 15, "cyclo": 1, "fpu_special": 0, "fp_mode": 0, "jump_tables": 0, "indirect_calls": 0,
            "callees": 0, "globals": 0, "hw_refs": 0, "sys_ops": sys_ops}


class Rule(unittest.TestCase):
    def test_stale(self):
        self.assertEqual(sorted(stale_skips.stale(ROWS, KINDS)), sorted(STALE + UNTAKEABLE))
        out = stale_skips.stale(ROWS, KINDS, TAKEABLE)
        self.assertEqual(sorted(out), STALE)
        self.assertEqual(set(out.values()),
                         {f"blocker entry_regs fixed at {FIX} (target.json blocker_kinds), after this row was judged"})
        self.assertEqual(stale_skips.stale(ROWS, [dict(KINDS[0], fixed_at=None), KINDS[1]]), {})

    def test_finish_records_the_fix(self):
        class O:
            cfg = {"blocker_kinds": KINDS}
            rows = {"8c563e40": {"addr": "8c563e40", "status": "running", "attempts": []}}
            rec = rows.get
            save = log = staticmethod(lambda *a: None)
        job.finish(O, "8c563e40", "skipped", REGS)
        r = O.rows["8c563e40"]
        self.assertEqual(r["blocker"], {"kind": "entry_regs", "fixed_at": FIX})
        self.assertEqual(stale_skips.stale(O.rows, KINDS), {})  # judged under this fix: not requeued again
        self.assertEqual(list(stale_skips.stale(O.rows, [dict(KINDS[0], fixed_at="2026-11-01T00:00:00")])),
                         ["8c563e40"])  # a later fix of the same kind
        r["status"] = "matched"
        stale_skips.record(r, KINDS)
        self.assertNotIn("blocker", r)


class Requeue(unittest.TestCase):
    def ws(self, d):
        ws = pathlib.Path(d)
        (ws / "project.json").write_text(json.dumps(dict(PROJECT, name="staleskip",
                                                         game_ranges=[["8c500000", "8c700000"]])), encoding="utf-8")
        (ws / "state").mkdir()
        cat = [entry(a, sys_ops=int(a == "8c5f0000")) for a in sorted(ROWS) if a != "8c024cbe"]
        (ws / "state" / "catalog.json").write_text(json.dumps({"functions": cat}), encoding="utf-8")
        (ws / "state" / "ledger.json").write_text(json.dumps({"loop": False, "functions": ROWS}), encoding="utf-8")
        return ws

    def check(self, rows):
        for a, r in ROWS.items():
            if a in STALE:  # requeued; the untakeable ones stay skipped
                self.assertEqual(rows[a]["status"], "queued", a)
                self.assertIn("blocker entry_regs fixed at", rows[a]["requeued"])
            else:
                self.assertEqual(rows[a], r, a)

    def test_tool_offline(self):  # the real target.json: entry_regs fixed_at is this FIX
        with tempfile.TemporaryDirectory(prefix="staleskip_", dir=TOOL / "tests" / ".work") as d:
            ws = self.ws(d)
            p = subprocess.run([sys.executable, str(TOOL / "tools" / "requeue.py"), "--project", str(ws),
                                "--stale-skips"], capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("3 function(s) to requeue (offline)", p.stdout)
            self.check(json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"])

    def test_serve_hook(self):
        o = object.__new__(orch.Orchestrator)
        o.cfg, o.lock = {"blocker_kinds": KINDS}, threading.RLock()
        o.ledger = {"functions": json.loads(json.dumps(ROWS))}
        o.functions = {a: dict(entry(a), tier="excluded" if a == "8c5f0000" else "easy")
                       for a in ROWS if a != "8c024cbe"}
        o.save = o.log = lambda *a: None
        self.assertEqual(sorted(o.requeue_stale_skips()), STALE)
        self.check(o.ledger["functions"])
        self.assertEqual(o.requeue_stale_skips(), [])  # queued now: nothing twice


if __name__ == "__main__":
    unittest.main()
