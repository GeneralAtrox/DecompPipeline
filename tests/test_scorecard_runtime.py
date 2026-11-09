"""Gate 6 of the scorecard (tools/scorecard_runtime.py): 144 fps at 2560x1600, no emulated layers.
1. the session summary line parses (last one in a log; times as median/p99/worst);
2. with no log, or only logs at another size or too short, the fps and p99 checks are unmeasured and the gate is never
   green; a log at the target size is judged: the worst session counts, a make p99 of 6.67 ms and 1 late frame per
   1,000 shown pass, up to twice that is partial, beyond fails, and a log without the figures (older than the bar) is
   unmeasured;
3. the layers check reads docs/RUNTIME_TARGET.md's table: open layers fail, a row with no port kit is unmeasured, a console
   with no row is unmeasured, "Replaced ..." rows are closed;
4. the aspect-ratio check reads the proof table of RUNTIME_TARGET.md: no table or no row for the reference game is
   unmeasured, a row short of any shape, default on, identical gameplay or existing evidence fails, and only a full
   evidenced row passes, so the gate is never green without one;
5. the laptop's computer name is not in the spec (a label): DP_TARGET_MACHINE, else machines.local.json
   "target_machine", names it; with neither only the label counts;
6. the real spec and the real RUNTIME_TARGET.md give a dreamcast gate that is red (layers open) and a gate-6 entry per console.
Run: python -W error tests/test_scorecard_runtime.py
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import scorecard as sc  # noqa: E402
import scorecard_runtime as sr  # noqa: E402

SPEC = json.loads((ROOT / "tools" / "scorecard" / "spec.json").read_text(encoding="utf-8"))
OK = ("play summary: shown_fps 144.6 output 2560x1600 frames 8640 tick_ms 1.10/2.30/9.10 draw_ms 2.00/3.10/12.00 "
      "frame_ms 6.50/6.80/14.00 make_ms 3.00/5.10/12.00\n"
      "play verdict: pass make_ms p99 5.10 (bar 6.67) late 4 of 8600 shown (0.47 per 1000, bar 1) loading 40 shown 9 late machine target_laptop\n")
OLD = OK.splitlines()[0].replace(" make_ms 3.00/5.10/12.00", "") + "\n"  # a log from before the bar
DOC = """# x
## Emulated layers in the play path

| Layer | Where | Cost measured | Status |
|---|---|---|---|
| Sound CPU: ARM7 | `runtime/arm7.c` | 2 ms | Open |
| Guest memory accessors | `tx_rt.c` | 30% | Replaced by inline accessors |
| Saturn VDP | `sat_vdp.c` | Not measured | Open |
| GameCube and Xbox 360 hardware | No port kit yet | None | Design first |

## Next
| Layer | Where | Cost | Status |
|---|---|---|---|
| Saturn other | x | y | Open |
"""


def con(logs):
    return {"runtime": {"reference": "Ref", "logs": logs, "layers": ["Sound CPU", "Guest memory"]}}


class Summary(unittest.TestCase):
    def test_parse_takes_the_last_line(self):
        s = sr.parse_summary("junk\n" + OK.replace("144.6", "100.0") + "more\n" + OK)
        self.assertEqual((s["fps"], s["w"], s["h"], s["frames"]), (144.6, 2560, 1600, 8640))
        self.assertEqual(s["frame_ms"], (6.5, 6.8, 14.0))
        self.assertEqual(s["tick_ms"][2], 9.1)
        self.assertEqual(s["make_ms"], (3.0, 5.1, 12.0))
        self.assertEqual((s["verdict"], s["late"], s["judged"], s["shown_loading"], s["late_loading"]), ("pass", 4, 8600, 40, 9))
        self.assertNotIn("late", sr.parse_summary(OLD))
        self.assertEqual(s["machine"], "target_laptop")
        self.assertIsNone(sr.parse_summary("play summary: nothing useful"))

    def test_layer_table_stops_at_the_next_heading(self):
        rows = sr.layer_rows(DOC)
        self.assertEqual([r[0] for r in rows], ["Sound CPU: ARM7", "Guest memory accessors", "Saturn VDP",
                                                "GameCube and Xbox 360 hardware"])


class Gate(unittest.TestCase):
    def setUp(self):  # this machine's own DP_TARGET_MACHINE must not decide the cases
        saved = os.environ.pop(sr.MACHINE_ENV, None)
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, sr.MACHINE_ENV, saved)

    def run_gate(self, logs, doc=DOC, c=None, local=None):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "docs").mkdir()
            if local is not None:
                (d / "machines.local.json").write_text(json.dumps(local), encoding="utf-8")
            if doc is not None:
                (d / "docs" / "RUNTIME_TARGET.md").write_text(doc, encoding="utf-8")
            for name, text in logs.items():
                (d / name).parent.mkdir(parents=True, exist_ok=True)
                (d / name).write_text(text, encoding="utf-8")
            return sr.gate_runtime(SPEC, c or con(["logs/port*.log"]), d, d)

    def test_no_log_is_unmeasured_and_never_green(self):
        out = self.run_gate({}, doc=DOC.replace("| Open |", "| Replaced |", 1))
        self.assertEqual([c["status"] for c in out[:2]], ["unmeasured", "unmeasured"])
        self.assertIn("play summary:", out[0]["missing"])
        self.assertNotEqual(sc.gate_status(out), "green")

    def test_a_stale_low_resolution_or_short_log_does_not_count(self):
        low = OK.replace("2560x1600", "1920x1080")
        short = OK.replace("frames 8640", "frames 100")
        out = self.run_gate({"logs/port.log": low, "logs/port.1.log": short})
        self.assertEqual([c["status"] for c in out[:2]], ["unmeasured", "unmeasured"])
        self.assertIn("1920x1080", out[0]["missing"])

    def test_the_worst_session_counts(self):
        slow = OK.replace("3.00/5.10/12.00", "4.00/9.50/20.00").replace("late 4 of", "late 40 of")
        out = self.run_gate({"logs/port.log": OK, "logs/port.1.log": slow})
        self.assertEqual((out[0]["status"], out[0]["value"]), ("partial", 9.5))  # at_most: up to twice the target is partial
        self.assertEqual((out[1]["status"], out[1]["value"]), ("fail", 4.6512))  # 40 of 8600 shown
        worse = self.run_gate({"logs/port.log": slow.replace("4.00/9.50/20.00", "4.00/15.00/20.00")})
        self.assertEqual((worse[0]["status"], worse[0]["value"]), ("fail", 15.0))
        good = self.run_gate({"logs/port.log": OK})
        self.assertEqual([c["status"] for c in good[:2]], ["pass", "pass"])
        # the bar's edges: 6.67 ms and 1 per 1,000 pass, just over is partial
        edge = OK.replace("3.00/5.10/12.00", "3.00/6.67/12.00").replace("late 4 of 8600", "late 8 of 8000")
        self.assertEqual([c["status"] for c in self.run_gate({"logs/port.log": edge})[:2]], ["pass", "pass"])
        over = OK.replace("3.00/5.10/12.00", "3.00/6.68/12.00").replace("late 4 of 8600", "late 9 of 8000")
        self.assertEqual([c["status"] for c in self.run_gate({"logs/port.log": over})[:2]], ["partial", "partial"])
        # shown-frame spacing is not a check: a session at 6.94 ms frames passes on its figures
        self.assertEqual([c["status"] for c in self.run_gate({"logs/port.log": OK.replace("6.50/6.80/14.00", "6.94/7.40/14.00")})[:2]],
                         ["pass", "pass"])
        # a log from before the bar has no figures: unmeasured, never green
        old = self.run_gate({"logs/port.log": OLD})
        self.assertEqual([c["status"] for c in old[:2]], ["unmeasured", "unmeasured"])

    def test_only_the_users_laptop_passes_or_fails(self):
        slow = OK.replace("3.00/5.10/12.00", "4.00/15.00/20.00").replace("target_laptop", "BUILD_BOX_A")
        out = self.run_gate({"logs/port.log": slow})
        self.assertEqual([c["status"] for c in out[:2]], ["unmeasured", "unmeasured"])  # BUILD_BOX_A: never a fail
        self.assertIn("BUILD_BOX_A", out[0]["missing"])
        both = self.run_gate({"logs/port.log": slow, "logs/port.1.log": OK.replace("target_laptop", "TARGET_LAPTOP")})
        self.assertEqual([c["status"] for c in both[:2]], ["pass", "pass"])  # the laptop's counts, BUILD_BOX_A's does not
        unnamed = self.run_gate({"logs/port.log": OK.replace(" machine target_laptop", "")})
        self.assertEqual([c["status"] for c in unnamed[:2]], ["unmeasured", "unmeasured"])

    def test_the_laptops_name_comes_from_local_config(self):
        self.assertEqual(SPEC["thresholds"]["runtime"]["machine"], "target_laptop")  # a label, no computer name
        named = OK.replace("target_laptop", "Example_Laptop")
        statuses = lambda out: [c["status"] for c in out[:2]]  # noqa: E731
        self.assertEqual(statuses(self.run_gate({"logs/port.log": named})), ["unmeasured", "unmeasured"])  # not configured
        local = {"target_machine": "Example_Laptop"}
        self.assertEqual(statuses(self.run_gate({"logs/port.log": named}, local=local)), ["pass", "pass"])
        self.assertEqual(statuses(self.run_gate({"logs/port.log": named.replace("Example_Laptop", "EXAMPLE_LAPTOP")},
                                                local=local)), ["pass", "pass"])  # computer names ignore case
        for other in (OK, OK.replace("target_laptop", "BUILD_BOX_A"), OK.replace(" machine target_laptop", "")):
            out = self.run_gate({"logs/port.log": other}, local=local)  # the label, a build box or no name: refused
            self.assertEqual(statuses(out), ["unmeasured", "unmeasured"])
            self.assertNotIn("Example_Laptop", out[0]["missing"])  # the messages name the label only
        failing = named.replace("3.00/5.10/12.00", "4.00/15.00/20.00")
        self.assertEqual(statuses(self.run_gate({"logs/port.log": failing}, local=local))[0], "fail")  # it is judged
        os.environ[sr.MACHINE_ENV] = "Other_Laptop"  # the environment wins over the file (setUp restores it)
        self.addCleanup(os.environ.pop, sr.MACHINE_ENV, None)
        self.assertEqual(statuses(self.run_gate({"logs/port.log": named}, local=local)), ["unmeasured", "unmeasured"])
        self.assertEqual(statuses(self.run_gate({"logs/port.log": named.replace("Example_Laptop", "Other_Laptop")},
                                                local=local)), ["pass", "pass"])

    def test_layers(self):
        out = self.run_gate({"logs/port.log": OK})
        self.assertEqual((out[2]["status"], out[2]["value"]), ("fail", 1))  # Sound CPU open, accessors replaced
        self.assertIn("Sound CPU", out[2]["source"])
        closed = self.run_gate({"logs/port.log": OK}, doc=DOC.replace("| Open |", "| Replaced by native |", 1))
        self.assertEqual(closed[2]["status"], "pass")
        self.assertEqual(sc.gate_status(closed[:3]), "green")  # the first three measured and passing
        self.assertEqual(sc.gate_status(closed), "amber")  # the aspect-ratio check has no proof, so the gate is not green
        none = self.run_gate({"logs/port.log": OK}, c={"runtime": {"logs": ["logs/port*.log"], "layers": ["Nothing"]}})
        self.assertEqual(none[2]["status"], "unmeasured")
        nokit = self.run_gate({"logs/port.log": OK}, c={"runtime": {"logs": ["logs/port*.log"], "layers": ["GameCube"]}})
        self.assertEqual(nokit[2]["status"], "unmeasured")
        self.assertEqual(self.run_gate({}, doc=None)[2]["status"], "unmeasured")

    def test_aspect_ratio_is_never_green_without_a_proof(self):
        shapes = "4:3, 16:10, 16:9, 21:9, 32:9, narrower"
        head = "\n### Aspect-ratio proof per game\n\n| Game | Shapes | On | Same | Evidence |\n|---|---|---|---|---|\n"

        def aspect(row, files=("proof.md",)):
            with tempfile.TemporaryDirectory() as d:
                d = pathlib.Path(d)
                (d / "docs").mkdir()
                (d / "docs" / "RUNTIME_TARGET.md").write_text(DOC + (head + row if row is not None else ""), encoding="utf-8")
                for f in files:
                    (d / f).write_text("x", encoding="utf-8")
                return sr.gate_runtime(SPEC, con(["logs/port*.log"]), d, d)

        # con() names the reference "Ref"; the 3 earlier checks are green-capable, so only this one decides the gate
        self.assertEqual(aspect(None)[3]["status"], "unmeasured")
        self.assertEqual(aspect("| Other game | " + shapes + " | Yes | Yes | proof.md |\n")[3]["status"], "unmeasured")
        for row, why in [(f"| Ref | {shapes} | Yes | Yes | proof.md |\n", None),
                         (f"| Ref | 4:3, 16:9 | Yes | Yes | proof.md |\n", "shape 21:9"),
                         (f"| Ref | {shapes} | No, off | Yes | proof.md |\n", "not on by default"),
                         (f"| Ref | {shapes} | Yes | Not yet | proof.md |\n", "gameplay not shown identical"),
                         (f"| Ref | {shapes} | Yes | Yes | |\n", "no evidence path"),
                         (f"| Ref | {shapes} | Yes | Yes | `gone.md` |\n", "evidence missing: gone.md")]:
            c = aspect(row)[3]
            self.assertEqual(c["status"], "pass" if why is None else "fail", row)
            if why:
                self.assertIn(why, c["source"])
        full = aspect(f"| Ref | {shapes} | Yes | Yes | proof.md |\n")
        full = [dict(c, status="pass") for c in full[:3]] + [full[3]]
        self.assertEqual(sc.gate_status(full), "green")  # a full, evidenced row is the only way to pass
        self.assertNotEqual(sc.gate_status(full[:3] + [aspect(None)[3]]), "green")

    def test_the_real_spec_and_doc(self):
        for key, con_ in SPEC["consoles"].items():
            out = sr.gate_runtime(SPEC, con_, ROOT.parent, ROOT)
            self.assertEqual(len(out), 4, key)
            self.assertEqual(out[3]["status"], "unmeasured", key)  # no proof row yet
            self.assertNotEqual(sc.gate_status(out), "green", key)  # nothing is at the target yet
        dc = sr.gate_runtime(SPEC, SPEC["consoles"]["dreamcast"], ROOT.parent, ROOT)
        self.assertEqual(dc[2]["status"], "fail")
        self.assertEqual(sc.GATES[-1], "6 Runtime (144 fps)")


if __name__ == "__main__":
    unittest.main()
