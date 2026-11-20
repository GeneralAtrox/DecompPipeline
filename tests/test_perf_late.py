"""port/tools/perf_late.py (perf.py segment) on a made-up run: each late frame is put down to what took the game's
thread most time in its gap, named by the game's symbols, the groups come worst first, the pacing wait is never a
cause, a held thread that had not run is the system's, and a long tick with no samples in it falls back to its largest
part. Also the sample file's busy bit (meta bit 7) reads apart from the thread."""
import array
import json
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "port" / "tools"))
import perf_late as pl  # noqa: E402
import perf_report as pr  # noqa: E402

SECTIONS = ["game: translated C", "game: SH-2 interpreter", "wait: game polls a flag (translated)",
            "wait: game polls a flag (interpreter)", "wait: pacing to the blank", "kit: VDP2 + compose", "kit: VDP1 draw",
            "kit: audio (68000, SCSP)", "kit: show (copy, post)", "kit: CD drive, disc read", "kit: in-between frame"]
SLOT = 6944
LATE = {100: 4, 200: 3, 250: 2}  # frame index: slots its gap spans
AI = 0x06012345


def build(d):
    frames, t = [], 0
    for i in range(300):
        gap = SLOT * LATE.get(i, 1)
        t += gap
        frames.append({"t_us": t, "tick": i // 2, "vblank": 1000 + i // 2, "gap_us": gap, "make_us": 3000, "tick_us": 2000,
                       "flags": 0, "draw_us": 500, "present_us": 300, "show_wait_us": 100})
    with open(d / "run.frames.csv", "w") as f:
        f.write(",".join(frames[0]) + "\n")
        f.writelines(",".join(str(v) for v in r.values()) + "\n" for r in frames)
    w = array.array("I")
    k = 0
    for ts in range(250, t, 500):
        while frames[k]["t_us"] < ts:
            k += 1
        sec, busy, stack = 4, 1, ()
        if k == 100:
            sec, stack = 1, (0x06001000, AI)
        elif k == 200:
            sec = 9
        elif k == 250:
            sec, busy = 0, 0
        w.extend([ts, k // 2, 1000 + k // 2, 0 | busy << 7 | sec << 8 | len(stack) << 16, 0x1000] + list(stack))
    with open(d / "run.samples.bin", "wb") as f:
        f.write(struct.pack("<5I", 0x31465250, 0x100000, 500, len(SECTIONS), len(w)))
        for s in SECTIONS:
            f.write(s.encode()[:31].ljust(32, b"\0"))  # the kit keeps 31 characters
        f.write(w.tobytes())
    names = ["work", "game", "wait_pace", "wait_poll", "vdp2", "vdp1", "audio", "show"]
    with open(d / "run.ticks.csv", "w") as f:
        f.write("t_us,tick,vblank," + ",".join(names) + "\n")
        for i in range(0, 300, 2):
            work, game, audio = (9000, 1500, 7000) if i == 150 else (3000, 2000, 500)
            f.write(f"{frames[i]['t_us']},{i // 2},{1000 + i // 2},{work},{game},1000,0,300,200,{audio},0\n")
    (d / "syms.txt").write_text(f"{AI:08x} BattleAI\n06001000 MainLoop\n")
    return frames


class PerfLate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pathlib.Path(tempfile.mkdtemp())
        cls.frames = build(cls.d)
        cls.samples = pr.read_samples(cls.d / "run.samples.bin")
        nm = pl.Namer(None, pl.read_symbols(cls.d / "syms.txt"))
        cls.an = pl.analyse(cls.frames, pl.read_ticks(cls.d / "run.ticks.csv"), cls.samples, nm)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_busy_bit_apart_from_thread(self):
        self.assertEqual({r[3] for r in self.samples["rows"]}, {0})
        self.assertIn(False, self.samples["busy"])
        self.assertIn(True, self.samples["busy"])

    def test_groups_worst_first_with_names(self):
        g = [(x["cause"], x["function"], x["late_frames"], x["late_slots"]) for x in self.an["groups"] if x["late_frames"]]
        self.assertEqual(g[0], ("game: SH-2 interpreter", "BattleAI", 1, 3))
        self.assertEqual((g[1][0], g[1][2], g[1][3]), ("kit: CD drive, disc read", 1, 2))
        self.assertEqual(g[2][0], "OS: game thread not running (Windows ran something else)")
        self.assertEqual(len(g), 3)

    def test_pacing_never_a_cause(self):
        self.assertFalse([e for e in self.an["events"] if e["cause"] == pl.PACING])

    def test_long_tick_falls_back_to_its_part(self):
        t = [e for e in self.an["events"] if e["kind"] == "long tick"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["cause"], "tick part: audio")  # its samples are all pacing: the largest part, not the wait
        self.assertEqual(self.an["stats"]["late"], 6)

    def test_report_files(self):
        self.assertEqual(pl.main(["--report", str(self.d), "--symbols", str(self.d / "syms.txt"), "--name", "made-up"]), 0)
        md = next(self.d.glob("late-*.md")).read_text(encoding="utf-8")
        self.assertIn("**Verdict:** fail", md)
        self.assertIn("| game: SH-2 interpreter | BattleAI | 1 | 3 |", md)
        self.assertIn("<svg", next(self.d.glob("late-*.html")).read_text(encoding="utf-8"))
        self.assertTrue(json.loads(next(self.d.glob("late-*.json")).read_text())["analysis"]["groups"])
        self.assertTrue((self.d / "run.samples.bin").exists())  # a re-report keeps the samples


if __name__ == "__main__":
    unittest.main()
