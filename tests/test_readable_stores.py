"""A readable PASS counts as verified only when every store of the original runs in a valid vector, and only when its
agent does not admit a gap (core/proof.readable_state, recorded by core/refine.pass_status; 2026-11-06).

Power Stone 2's fn_8c0e9b5e passed the readable judge 110/110 with its obj->update store missing: no vector reached the
block after Obj_Create's NULL test, so the store was never compared, and the game called address 0 at frame ~8,580.
Planted: the same shape (a store removed in a block no vector reaches, under the coverage gate's one-block allowance),
recorded through refine.record_pass and readable_verify.record; it must be `unproven`, not `readable`. Temp folders;
no Ghidra, compiler or agent.

usage: python -m unittest tests.test_readable_stores
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
TMP = TOOL / "tests" / ".work"
for p in (str(TOOL / "tools"), str(TOOL)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import proof  # noqa: E402  (pure: no core.paths)

A = "8c000100"
# The original, modelled on PS2's spawn_stage_marker: Obj_Create(5, 0, 1); on a non-NULL object it stores the update
# callback at +4 and two flag bytes. The store block 8c000110 is the one no vector reaches.
ASM = """; part 1 of 1 of asm.txt
; fn_8c000100  entry 8c000100  34 bytes
; SH-4 little endian. A mnemonic starting with '_' executes in the delay slot of the branch above it.
  8c000100  mov.l r14,@-r15
  8c000102  sts.l PR,@-r15
  8c000104  mov.l 0x8c000124,r3                  ; = 0x0c03f448  (function 8c03f448)
  8c000106  jsr @r3                              ; call 8c03f448
  8c000108  _mov #0x5,r4
  8c00010a  tst r0,r0
  8c00010c  bt/s 0x8c00011c
  8c00010e  _mov r0,r14
  8c000110  mov.l 0x8c000128,r3                  ; = 0x0c000180  (function 8c000180)
  8c000112  mov.b r0,@(0x1,r14)
  8c000114  mov.l r3,@(0x4,r14)
  8c000116  mov #0x1,r0
  8c000118  mov.b r0,@r14
  8c00011a  nop
LAB_8c00011c:
  8c00011c  lds.l @r15+,PR
  8c00011e  rts
  8c000120  _mov.l @r15+,r14
"""
# The planted readable file: the judge passes it (no vector reaches the block), the update store is gone.
PLANTED = """void SpawnMarker(void)
{
    Obj *obj = Obj_Create(5, 0, 1);
    if (obj != NULL) {
        obj->state = (s8)-1;
        obj->flags = 1;  /* obj->update = Marker_Update; missing */
    }
}
"""
HAD = '8c000114 mov.l r3,@(0x4,r14)'
ADMIT = ("RESULT: PASS (dev only; the obj->update store of 0x0c0ea222 is missing). I also dropped two stack words "
         "the check never compared.")
SCRIPT = """
import json, sys
sys.path.insert(0, sys.argv[1])
from core import readable_verify, refine
a, how = sys.argv[2], sys.argv[3]
if how == "pass":
    print(refine.record_pass(a, "h"))
else:
    readable_verify.record({}, a, {"verdict": "PASS"}, {}, "h", print)
    print("verify")
print(json.dumps(refine.load_ledger()[a]))
"""


def summary(hit_all):
    """3 blocks; hit_all: every block reached, else the store block 8c000110 unreached (the gate allows one)."""
    s = {"dev_valid": 55, "dev_tried": 60, "hidden_valid": 55, "hidden_tried": 60, "blocks_total": 3,
         "blocks_hit_dev": 3 if hit_all else 2, "blocks_hit_all": 3 if hit_all else 2}
    return s if hit_all else dict(s, blocks_unhit=["8c000110"])


def workspace(summ, message="RESULT: PASS", status="running", **row):
    ws = pathlib.Path(tempfile.mkdtemp(prefix="stores_", dir=TMP))
    files = {"project.json": {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin"},
             f"harness/{A}/oracle_summary.json": summ, f"packets/{A}/asm.txt": ASM, f"readable/src/{A}.c": PLANTED,
             "state/readable.json": {A: dict(row, status=status, attempts=[
                 {"n": 1, "verdict": "PASS", "final_message": message}])}}
    for rel, data in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return ws


def record(ws, how="pass"):
    env = {**__import__("os").environ, "DECOMP_PROJECT": str(ws)}
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(TOOL), A, how], env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    return lines[-2], json.loads(lines[-1])


class Planted(unittest.TestCase):
    """The planted wrong function through the two places a readable PASS is recorded."""

    def test_gate_allows_it(self):  # the existing coverage gate passes the planted harness: the gap this closes
        gate = proof.coverage_settings({"coverage_gate": {"enforce": True}})
        self.assertIs(proof.coverage_verdict(summary(False), gate)["ok"], True)

    def test_unreached_store_is_unproven(self):
        out, row = record(workspace(summary(False)))
        self.assertEqual((out, row["status"]), ("unproven", "unproven"))
        self.assertIn(HAD, row["unproven"]["stores"])
        self.assertEqual((row["unproven"]["blocks"], row["unproven"]["oracle"]), (["8c000110"], "untried"))
        self.assertIn("store(s) no vector runs", row["note"])

    def test_verify_turns_an_old_pass_unproven(self):
        out, row = record(workspace(summary(False), status="readable"), "verify")
        self.assertEqual(row["status"], "unproven")
        self.assertIn(HAD, row["unproven"]["stores"])

    def test_every_store_reached_stays_readable(self):  # the control: a reached store block changes nothing
        out, row = record(workspace(summary(True)))
        self.assertEqual((out, row["status"]), ("readable", "readable"))
        self.assertNotIn("unproven", row)
        self.assertNotIn("note", row)

    def test_admitted_gap_needs_review(self):
        out, row = record(workspace(summary(True), ADMIT))
        self.assertEqual(row["status"], "needs_review")
        self.assertIn("dev only", row["review"]["admits"])
        _, row = record(workspace(summary(False), ADMIT))  # both: review first, the stores listed too
        self.assertEqual((row["status"], HAD in row["unproven"]["stores"]), ("needs_review", True))
        _, row = record(workspace(summary(True), ADMIT, reviewed="a reviewer: read raw.c, the store is there"))
        self.assertEqual(row["status"], "readable")
        _, row = record(workspace(summary(True), "The store to obj->update is skipped when Obj_Create returns NULL, "
                                                  "as raw.c does. RESULT: PASS"))
        self.assertEqual(row["status"], "readable")  # the original's behaviour, not an admission


class Stores(unittest.TestCase):
    def test_blocks(self):
        b = proof.block_stores(ASM, ["8c000100", "8c000110", "8c00011c", "8c000200"])
        self.assertEqual(b["8c000100"], ["8c000100 mov.l r14,@-r15", "8c000102 sts.l PR,@-r15"])  # to the bt/s slot
        self.assertEqual(b["8c000110"], ["8c000112 mov.b r0,@(0x1,r14)", HAD, "8c000118 mov.b r0,@r14"])
        self.assertEqual((b["8c00011c"], b["8c000200"]), ([], None))  # loads only; not in the listing
        call = ("  8c000200  jsr @r3                              ; call 8c03749c\n  8c000202  _mov r13,r4\n"
                "  8c000204  mov.b r12,@r13\n  8c000206  rts\n  8c000208  _nop\n")  # a call does not end a block
        self.assertEqual(proof.block_stores(call, ["8c000200"])["8c000200"], ["8c000204 mov.b r12,@r13"])
        slot = ASM.replace("_mov.l @r15+,r14", "_mov.l r1,@r2")  # a delay slot runs with its branch
        self.assertEqual(proof.block_stores(slot, ["8c00011c"])["8c00011c"], ["8c000120 mov.l r1,@r2"])
        ppc = "  80001000  stw r3,0x4(r31)\n  80001004  addi r3,r3,1\n  80001008  beq 0x80001010\n" \
              "  8000100c  stfs f1,0x8(r31)\nLAB_80001010:\n  80001010  psq_st f1,0x0(r3),0,0\n  80001014  blr\n"
        self.assertEqual(proof.block_stores(ppc, ["80001000", "8000100c"]),
                         {"80001000": ["80001000 stw r3,0x4(r31)"], "8000100c": ["8000100c stfs f1,0x8(r31)"]})

    def test_coverage(self):
        self.assertEqual(proof.store_coverage(summary(True), None)["ok"], True)  # every block reached: every store
        full = dict(summary(True), branch_dirs_total=2, branch_dirs_hit_all=2)  # "fully proven" implies it too
        self.assertEqual((proof.grade(full)["grade"], proof.store_coverage(full, ASM)["ok"]), (proof.FULL, True))
        self.assertEqual(proof.store_coverage(summary(False), ASM)["stores"], [b for b in proof.block_stores(
            ASM, ["8c000110"])["8c000110"]])
        self.assertIs(proof.store_coverage(None, ASM)["ok"], None)  # no data: counted apart, as the gate's
        self.assertIs(proof.store_coverage(summary(False), None)["ok"], None)
        old = {k: v for k, v in summary(False).items() if k != "blocks_unhit"}  # unhit blocks not listed
        self.assertIs(proof.store_coverage(old, ASM)["ok"], False)
        loads = dict(summary(False), blocks_unhit=["8c00011c"])  # an unreached block without a store
        self.assertIs(proof.store_coverage(loads, ASM)["ok"], True)
        self.assertEqual(proof.oracle_tried(dict(summary(False), bound={"open": 1})), True)

    def test_admits(self):
        said = ["RESULT: PASS (dev only; the two global stores are not reproduced, see above)",
                "the stores to guest addresses 0x8cffdff4 and 0x8cffdff8 are missing from work.c",
                "RESULT: PASS (unverified: player-3 literal-pool constants are not in the provided files)",
                "One deviation from raw.c: I dropped the store of the g_charOrder pointer into the frame.",
                "raw.c's stack scratch writes (fr15 save, fr6/fr7 spill) are dropped; the check passed without them.",
                "I did not write the raw `0x8cffdffc` stack store."]
        clean = ["RESULT: PASS", "The store is skipped when the flag is clear, as in raw.c.", "PASS 110/110",
                 "I removed the unused local and kept the stores in raw.c's order.", "", None]
        self.assertEqual([bool(proof.admits_gap(m)) for m in said], [True] * len(said))
        self.assertEqual([proof.admits_gap(m) for m in clean], [[]] * len(clean))

    def test_passing_message(self):
        row = {"attempts": [{"n": 1, "verdict": "PASS", "final_message": "one"},
                            {"n": 2, "verdict": "FAIL", "final_message": "two"}, {"n": 3, "kind": "sync"}]}
        self.assertEqual(proof.passing_message(row), "one")  # a failed redo's words are not the file's
        row["attempts"].append({"n": 4, "kind": "readopt", "from_attempt": 2})
        self.assertEqual(proof.passing_message(row), "two")
        self.assertIsNone(proof.passing_message({"attempts": [{"n": 1, "kind": "draft", "verdict": "PASS"}]}))

    def test_backlog_queue(self):
        import proof_backlog
        ws = workspace(summary(False))
        rows = {A: {"status": "unproven", "unproven": {"ok": False, "stores": [HAD], "oracle": "tried"}},
                "8c000200": {"status": "unproven", "unproven": {"ok": False, "stores": [HAD], "oracle": "untried"}},
                "8c000300": {"status": "readable"}}
        (ws / "state" / "readable.json").write_text(json.dumps(rows), encoding="utf-8")
        out = proof_backlog.unproven_readable(ws)
        self.assertEqual([a for a, _ in out], ["8c000200", A])  # the oracle's untried ones first
        self.assertIn("oracle untried: 1 store(s) no vector runs: " + HAD, out[0][1])

    def test_restatus(self):  # an existing workspace's rows take the rule without a rejudge
        import proof_backlog
        ws = workspace(summary(False), status="readable", verified="2026-11-01")
        led = ws / "state" / "readable.json"
        self.assertEqual(proof_backlog.restatus(ws), {("readable", "unproven"): 1})
        self.assertEqual(json.loads(led.read_text(encoding="utf-8"))[A]["status"], "readable")  # dry: unchanged
        self.assertEqual(proof_backlog.restatus(ws, apply=True), {("readable", "unproven"): 1})
        row = json.loads(led.read_text(encoding="utf-8"))[A]
        self.assertEqual((row["status"], HAD in row["unproven"]["stores"], row["verified"]), ("unproven", True, "2026-11-01"))
        self.assertIn("store(s) no vector runs", row["note"])
        (ws / "harness" / A / "oracle_summary.json").write_text(json.dumps(summary(True)), encoding="utf-8")
        self.assertEqual(proof_backlog.restatus(ws, apply=True), {("unproven", "readable"): 1})  # reached later
        row = json.loads(led.read_text(encoding="utf-8"))[A]
        self.assertEqual(row["status"], "readable")
        self.assertFalse({"unproven", "note"} & set(row))

    def test_store_waive(self):  # project.json coverage_gate.store_waive: a store gap judged by hand
        ws = workspace(summary(False), status="readable")
        pj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        (ws / "project.json").write_text(json.dumps(dict(pj, coverage_gate={"store_waive": {A: "own frame only"}})),
                                         encoding="utf-8")
        self.assertEqual(proof.readable_state(ws, A, {}), ("readable", {}))


if __name__ == "__main__":
    unittest.main()
