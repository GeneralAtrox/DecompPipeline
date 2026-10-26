"""Fixtures for cross-process state writes (core/filelock.py, core/refine.update) and system assignment
(core/sysassign.py, tools/assign_systems.py, the doctor's unassigned check). Temp folders; no Ghidra or agent.

usage: python tests/test_locks_systems.py   (python tests/test_tooling.py runs these too)
"""
import atexit
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="locks_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
for p in (str(TOOL / "tools"), str(TOOL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import doctor, filelock, sysassign  # noqa: E402  (pure: no core.paths)
import assign_systems  # noqa: E402  (tools/assign_systems.py)

atexit.register(_TMP.cleanup)  # not tearDownModule: test_tooling.py runs these classes between its own

HAMMER = """
import sys
sys.path.insert(0, sys.argv[1])
from core import refine
for n in range(int(sys.argv[3])):
    refine.update(f"{sys.argv[2]}{n:06x}", status="readable", n=n)
    refine.update("shared", **{"p" + sys.argv[2]: n})
"""
HOLD = """
import sys, time
sys.path.insert(0, sys.argv[1])
from core import filelock
with filelock.locked(sys.argv[2]):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""
LEDGER_FAIL = """
import sys
sys.path.insert(0, sys.argv[1])
from core import filelock, readable, refine
A, B, C = "8c010000", "8c010010", "8c010020"
readable.SRC.mkdir(parents=True, exist_ok=True)
for a, row in ((A, {}), (B, {"redo_of": "stale"}), (C, {})):
    refine.update(a, status="running", **row)  # a fresh run of A and C, a redo of B (its old file was stale)
for a in (A, B):  # the attempt passed and its file was copied to readable/src
    (readable.SRC / f"{a}.c").write_text("/* passed */")
real = filelock.update_json
def refuse(path, fn, *args, **kw):
    raise PermissionError(13, "Permission denied", str(path))
filelock.update_json, refine.time.sleep = refuse, lambda s: None
try:
    refine.record_pass(A, "h")
except refine.PassNotRecorded as e:
    print("raised", e)
print(refine.record_error(A, "x", passed=True))
print(refine.load_ledger()[A]["status"])
filelock.update_json = real
print(refine.record_error(A, "passed, ledger failed", passed=True))
print(refine.record_error(B, "passed, ledger failed", passed=True))
print(refine.load_ledger()[A]["status"], refine.load_ledger()[B]["status"], sep="\\n")
print(refine.record_error(C, "an agent crash, no file").split(":")[0])
"""


def put(ws, rel, data):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


class Locks(unittest.TestCase):
    def test_two_processes_lose_no_entries(self):
        ws = pathlib.Path(tempfile.mkdtemp(prefix="hammer", dir=TMP))
        put(ws, "project.json", {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin"})
        env = dict(os.environ, DECOMP_PROJECT=str(ws))
        n = 150
        procs = [subprocess.Popen([sys.executable, "-c", HAMMER, str(TOOL), tag, str(n)], env=env)
                 for tag in ("aa", "bb")]
        try:
            for p in procs:
                self.assertEqual(p.wait(timeout=300), 0)
        finally:
            for p in procs:
                if p.poll() is None:
                    p.kill()
        led = json.loads((ws / "state" / "readable.json").read_text(encoding="utf-8"))
        for tag in ("aa", "bb"):
            self.assertEqual(sum(1 for a in led if a.startswith(tag)), n, tag)
            self.assertEqual(led["shared"]["p" + tag], n - 1)
        self.assertEqual(list(ws.glob("state/*.tmp")), [])

    def test_bounded_wait_names_the_lock(self):
        target = TMP / "held.json"
        p = subprocess.Popen([sys.executable, "-c", HOLD, str(TOOL), str(target), "6"], stdout=subprocess.PIPE,
                             text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            t0 = time.monotonic()
            with self.assertRaises(filelock.LockTimeout) as cm:
                with filelock.locked(target, wait=0.3):
                    pass
            self.assertLess(time.monotonic() - t0, 3)
            self.assertIn("held.json.lock", str(cm.exception))
        finally:
            p.kill()
            p.wait(timeout=30)
            p.stdout.close()
        with filelock.locked(target, wait=5):  # released when its process ended
            with filelock.locked(target, wait=0.1):  # and reentrant in one thread
                pass

    def test_update_json(self):
        target = pathlib.Path(tempfile.mkdtemp(prefix="update", dir=TMP)) / "u.json"  # Locks runs twice (test_tooling)
        filelock.update_json(target, lambda d: d.update(a=1), {})
        data, ret = filelock.update_json(target, lambda d: d.setdefault("b", []).append(2) or "done", {})
        self.assertEqual((data, ret), ({"a": 1, "b": [2]}, "done"))

    def test_write_while_a_reader_holds_the_file(self):
        """Windows refuses os.replace while another handle has the target open without delete sharing (Python's
        open() does not share delete): write_atomic retries until the reader closes, within RETRY_S."""
        target = TMP / "held_open.json"
        filelock.write_json(target, {"v": 0})
        opened, release = threading.Event(), threading.Event()

        def reader():
            with open(target, encoding="utf-8") as f:
                f.read()
                opened.set()
                release.wait(10)

        t = threading.Thread(target=reader)
        t.start()
        try:
            self.assertTrue(opened.wait(10))
            threading.Timer(0.6, release.set).start()
            t0 = time.monotonic()
            filelock.write_json(target, {"v": 1})  # blocked on Windows until the reader closes, then replaced
            if os.name == "nt":
                self.assertGreater(time.monotonic() - t0, 0.4)
        finally:
            release.set()
            t.join(10)
        self.assertEqual(filelock.read_json(target), {"v": 1})
        self.assertEqual(list(TMP.glob("held_open.json.*.tmp")), [])

    def test_refused_past_the_limit_names_the_file(self):
        if os.name != "nt":
            self.skipTest("only Windows refuses a replace over an open file")
        target = TMP / "never_closed.json"
        filelock.write_json(target, {"v": 0})
        old = filelock.RETRY_S
        filelock.RETRY_S = 0.3
        try:
            with open(target, encoding="utf-8"):
                with self.assertRaises(PermissionError) as cm:
                    filelock.write_json(target, {"v": 1})
        finally:
            filelock.RETRY_S = old
        self.assertIn("never_closed.json: still refused after 0.3 s", str(cm.exception))
        self.assertEqual(filelock.read_json(target), {"v": 0})
        self.assertEqual(list(TMP.glob("never_closed.json.*.tmp")), [])

    def test_readers_and_a_writer_hammer_one_file(self):
        """A reader that opens the file while another thread replaces it ([Errno 13] on Windows) retries too."""
        target = TMP / "hammered.json"
        filelock.write_json(target, {"n": 0})
        errors = []

        def write():
            try:
                for n in range(1, 151):
                    filelock.write_json(target, {"n": n})
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        def read():
            try:
                for _ in range(300):
                    self.assertIn("n", filelock.read_json(target))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=write)] + [threading.Thread(target=read) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        self.assertEqual(errors, [])
        self.assertEqual(filelock.read_json(target), {"n": 150})

    def test_a_failed_ledger_write_never_leaves_a_wrong_status(self):
        ws = pathlib.Path(tempfile.mkdtemp(prefix="ledger", dir=TMP))
        put(ws, "project.json", {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin"})
        out = subprocess.run([sys.executable, "-c", LEDGER_FAIL, str(TOOL)], env=dict(os.environ, DECOMP_PROJECT=str(ws)),
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertTrue(lines[0].startswith("raised passed, readable/src/8c010000.c written, but the ledger write "
                                            "failed: PermissionError"), lines)
        self.assertIn("readable on disk, ledger NOT written", lines[1])
        self.assertEqual(lines[2], "running")  # unwritable ledger: the row is untouched (verify repairs it)
        self.assertTrue(lines[3].startswith("readable (ledger written on the second try"), lines)
        self.assertTrue(lines[4].startswith("readable (ledger written on the second try"), lines)  # B: not stale
        self.assertEqual(lines[5:], ["readable", "readable", "error"])  # C: no file, a real error


A = [f"8c0100{i:x}0" for i in range(10)]  # a0..a9 game functions; SDK outside the game range


def make_sys_ws(name):
    ws = pathlib.Path(tempfile.mkdtemp(prefix=name, dir=TMP))
    put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000"})
    put(ws, "state/catalog.json", {"functions": [{"addr": a} for a in A + ["8c068000"]]})
    put(ws, "state/systems.json", {"resolution": 2.0, "systems": [
        {"id": "S01", "hint": "", "functions": [A[0], A[1], A[4]]},
        {"id": "S02", "hint": "", "functions": [A[5]]},
        {"id": "S03", "hint": "", "functions": [A[8]]}]})
    put(ws, f"harness/{A[2]}/meta.json", {"callees": [{"addr": A[0]}], "globals": []})  # call, from meta
    put(ws, "state/graph.tsv", "\n".join([
        f"{A[6]}\t\t\t8c200000\t",  # shares a global with a5
        f"{A[5]}\t\t\t8c200000\t",
        f"{A[9]}\t{A[8]},{A[5]}\t\t\t",  # one call into S03, one into S02: tie, S03's member is nearer
    ]) + "\n")
    return ws  # a3: enclosed by a2 (S01, placed by call) and a4; a7: between a6 (S02) and a8 (S03), no affinity


class Assign(unittest.TestCase):
    def test_plan_rules_and_new_system(self):
        ws = make_sys_ws("asg")
        p = sysassign.plan(ws)
        got = {a: (x["system"], x["how"]) for a, x in p["assign"].items()}
        self.assertEqual(got, {A[2]: ("S01", "call"), A[3]: ("S01", "enclosed"), A[6]: ("S02", "global"),
                               A[9]: ("S03", "call"), A[7]: ("S04", "new")})
        self.assertEqual(p["new"], [{"id": "S04", "hint": "", "functions": [A[7]]}])
        self.assertEqual(sysassign.plan(ws, attach=True)["assign"][A[7]], {"system": "S02", "how": "adjacent"})

    def test_apply_keeps_members_and_is_idempotent(self):
        ws = make_sys_ws("asg2")
        before = json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))["systems"]
        ok, lines, facts = doctor.check(ws)
        self.assertFalse(ok)
        self.assertEqual(facts["unassigned"], 5)
        self.assertTrue(any("in no system" in x and "assign_systems.py" in x for x in lines))
        out = []
        assign_systems.run(ws, dry=True, log=out.append)
        self.assertEqual(sysassign.unassigned(ws)[0], [A[2], A[3], A[6], A[7], A[9]])  # dry: nothing written
        self.assertTrue(any("S01+2" in x for x in out), out)
        assign_systems.run(ws, log=out.append)
        after = {s["id"]: s for s in json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))["systems"]}
        for s in before:  # every old id and member stays
            self.assertTrue(set(s["functions"]) <= set(after[s["id"]]["functions"]))
        self.assertEqual(after["S04"]["functions"], [A[7]])
        rec = json.loads((ws / sysassign.ASSIGNED).read_text(encoding="utf-8"))
        self.assertEqual(rec[A[3]]["how"], "enclosed")
        self.assertEqual(sysassign.unassigned(ws)[0], [])
        self.assertEqual(doctor.check(ws)[2]["unassigned"], 0)
        again = sysassign.apply(ws)
        self.assertEqual((again["before"], again["assign"]), (0, {}))
        self.assertNotIn("8c068000", {a for s in after.values() for a in s["functions"]})  # SDK: not a game function

    def test_no_systems_yet(self):
        ws = pathlib.Path(tempfile.mkdtemp(prefix="nosys", dir=TMP))
        put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000"})
        put(ws, "state/catalog.json", {"functions": [{"addr": A[0]}]})
        self.assertIsNone(sysassign.plan(ws))
        ok, lines, _ = doctor.check(ws)
        self.assertTrue(ok, lines)
        self.assertTrue(any("no state/systems.json" in x for x in lines))

    def test_override_moves_a_member(self):  # project.json system_overrides: the one move (PS2's VMU drive masks)
        ws = make_sys_ws("ovr")
        put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000",
                                 "system_overrides": {A[0]: "S02", A[7]: "S03"}})
        out = []
        p = assign_systems.run(ws, log=out.append)
        self.assertEqual(p["move"], {A[0]: {"system": "S02", "from": "S01"}, A[7]: {"system": "S03", "from": None}})
        self.assertTrue(any(f"{A[0]} S01->S02" in x and f"{A[7]} none->S03" in x for x in out), out)
        after = {s["id"]: s["functions"] for s in
                 json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))["systems"]}
        self.assertEqual(after["S01"], [A[1], A[4]])  # out of its old system, ids unchanged
        self.assertEqual(after["S02"][:2], [A[0], A[2]])  # a2 calls a0: it follows a0 to its new system
        self.assertIn(A[7], after["S03"])
        rec = json.loads((ws / sysassign.ASSIGNED).read_text(encoding="utf-8"))
        self.assertEqual((rec[A[0]]["how"], rec[A[0]]["from"]), ("override", "S01"))
        again = sysassign.apply(ws)
        self.assertEqual((again["move"], again["assign"]), ({}, {}))  # idempotent

    def test_override_refuses_an_unknown_system_or_function(self):
        for want, err in (({A[0]: "S99"}, "S99 is not a system"), ({"8c0aaaa0": "S02"}, "not a function")):
            ws = make_sys_ws("ovrx")
            put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000", "system_overrides": want})
            before = (ws / "state/systems.json").read_bytes()
            with self.assertRaisesRegex(ValueError, err):
                sysassign.apply(ws)
            self.assertEqual((ws / "state/systems.json").read_bytes(), before)  # nothing written
            self.assertFalse((ws / sysassign.ASSIGNED).exists())

    def test_no_override_moves_nothing(self):  # control: no key, or an empty one, is the plain assignment
        plain = sysassign.plan(make_sys_ws("ovr0"))
        ws = make_sys_ws("ovr1")
        put(ws, "project.json", {"target": "dreamcast_sh4", "game_end": "8c068000", "system_overrides": {}})
        self.assertEqual(plain["move"], {})
        self.assertEqual(sysassign.plan(ws), plain)
        sysassign.apply(ws)
        after = {s["id"]: s["functions"] for s in
                 json.loads((ws / "state/systems.json").read_text(encoding="utf-8"))["systems"]}
        self.assertEqual(after["S01"][:2], [A[0], A[1]])


if __name__ == "__main__":
    unittest.main(warnings="error")
