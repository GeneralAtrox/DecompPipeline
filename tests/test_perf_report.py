"""port/tools/perf_report.py on made-up runs: a planted slow function must come first in the swap list, a function
that costs the same in every frame must not be blamed for the frames over the bar, and a benchmark difference inside
the run-to-run spread must not be called real. The check is fed wrong answers too: without the plant the planted
function is absent, and the tier of each object comes out as the map says."""
import array
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "port" / "tools"))
import perf_report as pr  # noqa: E402
from perf_map import KIT, RAW, READABLE, Map  # noqa: E402

MAP = """ port
 Preferred load address is 5a000000
  Address         Publics by Value              Rva+Base       Lib:Object
 0001:00000000       _fn_8c010080               5a001000 f   txc:tx_8c010080.c.obj
 0001:00000100       _Game_Draw                 5a001100 f   readable:8c015d60.c.obj
 0001:00000200       _arm7_run                  5a001200 f   arm7.c.obj
 0001:00000300       _perf_planted_slow_function 5a001300 f   perf.c.obj
 0001:00000400       _pace_next_tick            5a001400 f   pace.c.obj
 0001:00000500       _A_fold                    5a001500 f   txc:tx_8c020000.c.obj
 0001:00000500       _B_fold                    5a001500 f   txc:tx_8c020010.c.obj
"""
SECTIONS = ["wait for blank", "ARM7 sound driver", "game code"]


def samples_file(path, rows):
    w = array.array("I")
    for t, tick, thread, section, off, stack in rows:
        w.extend([t, tick, tick, thread | section << 8 | len(stack) << 16, off] + list(stack))
    with open(path, "wb") as f:
        f.write(struct.pack("<5I", 0x31465250, 0x100000, 500, len(SECTIONS), len(w)))
        for s in SECTIONS:
            f.write(s.encode().ljust(32, b"\0"))
        f.write(w.tobytes())


def make_run(tmp, plant):
    """200 ticks; every 10th is over the bar. Per tick: 4 samples in fn_8c010080, 2 in the ARM7, 2 waiting; a tick
    over the bar has 6 more, in the planted function (plant) or spread like the rest (no plant)."""
    rows, frames, t = [], [], 0
    for tick in range(200):
        over = tick % 10 == 0
        what = [(2, 0x1010, (0x8c010080,))] * 4 + [(1, 0x1210, ())] * 2 + [(0, 0x1410, ())] * 2
        if over:
            what += [(2, 0x1310, ())] * 6 if plant else [(2, 0x1010, (0x8c010080,))] * 4 + [(1, 0x1210, ())] * 2
        for section, off, stack in what:
            rows.append((t, tick, 0, section, off, stack))
            t += 500
        frames.append({"t_us": t, "tick": tick, "vblank": tick, "gap_us": 6944, "make_us": 9000 if over else 4000,
                       "tick_us": 9000 if over else 4000, "flags": 2})
    path = tmp / f"s{int(plant)}.bin"
    samples_file(path, rows)
    return {"samples": pr.read_samples(path), "frames": frames, "log": {"stamped": [(10, "menu: open")]}}


def test_map_tiers(tmp_path):
    (tmp_path / "port.map").write_text(MAP)
    mp = Map(tmp_path / "port.map")
    assert mp.at(0x1010)["tier"] == RAW and mp.at(0x1010)["addr"] == 0x8c010080
    assert mp.at(0x1110)["tier"] == READABLE and mp.at(0x1110)["addr"] == 0x8c015d60
    assert mp.at(0x1210)["tier"] == KIT
    assert mp.at(0x1310)["layer"] == "Core"
    assert mp.at(0x1510)["folded"] == 1


def test_map_of_a_64_bit_kit_with_one_object_per_function(tmp_path):
    (tmp_path / "x.map").write_text(""" xbox_run
 Preferred load address is 0000000140000000
 0001:0585f8b4       fn_82a4fcc0                00000001458608b4 f   fn_82a4fcc0.obj
 0001:0585fe18       xg_82a4fcc0                0000000145860e18 f   fn_82a4fcc0.obj
 0001:05860000       xb_d3d_swap                0000000145861000 f   xb_d3d.obj
""")
    mp = Map(tmp_path / "x.map")
    assert mp.at(0x58608c0)["tier"] == RAW and mp.at(0x58608c0)["addr"] == 0x82a4fcc0
    assert mp.at(0x5860e20)["addr"] == 0x82a4fcc0 and mp.at(0x5861004)["tier"] == KIT
    assert "graphics" in mp.at(0x5861004)["kind"]


def test_planted_function_ranks_first(tmp_path):
    (tmp_path / "port.map").write_text(MAP)
    mp = Map(tmp_path / "port.map")
    rep = pr.analyse([make_run(tmp_path, True)], mp, calls={0x8c010080: 400}, call_blanks=200)
    top = max(rep["functions"], key=lambda f: f["excess_ms"])
    assert top["name"] == "perf_planted_slow_function"
    assert abs(top["self_ms_over"] - 3.0) < 0.2 and top["self_ms_normal"] == 0  # 6 samples of 0.5 ms
    raw = next(f for f in rep["functions"] if f["address"] == "8c010080")
    assert abs(raw["excess_ms"]) < 0.05 and raw["calls_per_tick"] == 2.0       # same cost in every frame: not blamed
    assert abs(raw["inclusive_ms_over"] - raw["self_ms_over"]) < 1e-9
    assert rep["ticks"] == {"normal": 180, "band": 0, "far": 20, "over": 20}
    waits = [f for f in rep["functions"] if f["bound"] == pr.WAITING]
    assert waits and all(s["name"] != waits[0]["name"] for s in rep["swaps"])  # waiting is never a swap
    assert rep["worst_frames"][0]["make_ms"] == 9.0
    assert rep["worst_frames"][0]["bound"][pr.CPU] > 0.8                       # the planted frame is the processor's
    assert "native rewrite" in raw["fix"] and "answer key" in raw["fix"]
    assert any(r[1] == "frame pacing" for r in rep["waits"])                   # waiting is named, not lumped
    from perf_text import render
    page = render({"bar_ms": 6.67, "analysis": rep, "notes": [], "meta": {
        "name": "t", "machine": "m", "when": "", "size": "s", "replay": "r", "stop_at": 1, "builds": {}}})
    assert "Which rule applies" in page and "What the waiting waits on" in page


def test_band_just_over_the_bar_is_reported_apart(tmp_path):
    (tmp_path / "port.map").write_text(MAP)
    run = make_run(tmp_path, True)
    for r in run["frames"]:
        r["make_us"] = 7200 if r["make_us"] > 6670 else r["make_us"]  # every frame over is just over
    rep = pr.analyse([run], Map(tmp_path / "port.map"))
    assert rep["ticks"]["band"] == 20 and rep["ticks"]["far"] == 0 and rep["band"]["frames"] == 20
    assert rep["band"]["functions_extra"][0][0] == "perf_planted_slow_function"


def test_without_plant_it_is_absent(tmp_path):
    (tmp_path / "port.map").write_text(MAP)
    rep = pr.analyse([make_run(tmp_path, False)], Map(tmp_path / "port.map"))
    assert all("planted" not in f["name"] for f in rep["functions"])
    assert rep["swaps"][0]["address"] == "8c010080"


def stats(p99):
    return {"make_ms": {"median": p99 - 2, "p99": p99, "worst": p99 + 5}, "late_per_1000": 0.0}


def test_difference_inside_the_noise_is_not_real():
    runs = [{"side": s, "stats": stats(v)} for s, v in (("A", 8.0), ("B", 8.3), ("B", 9.6), ("A", 9.5), ("A", 8.8), ("B", 8.1))]
    assert not pr.bench(runs)["difference"]["p99"]["real"]
    runs = [{"side": s, "stats": stats(v)} for s, v in (("A", 9.0), ("B", 6.1), ("B", 6.3), ("A", 9.4), ("A", 9.2), ("B", 6.0))]
    d = pr.bench(runs)["difference"]["p99"]
    assert d["real"] and d["b_minus_a_ms"] < -2.5


def test_one_off_work_is_counted_and_the_numbers_given_without_it(tmp_path):
    log = tmp_path / "port.log"
    log.write_text("[21:54:01 +1.0s v50] once: shader compile 41.5 ms water\n[21:54:02 +2.0s v120] d3d: shader 7 compiled in 30.0 ms\n"
                   "xb: opened movie.wmv\n[21:54:03 +3.0s v130] menu: main menu\n")
    events = pr.log_lines(log)["once"]
    assert [(e["vblank"], e["kind"], e["ms"]) for e in events] == [(50, "shader compile", 41.5), (120, "shader compile", 30.0), (None, "file open", 0.0)]
    frames = [{"tick": v, "vblank": v, "gap_us": 6944, "make_us": 45000 if v in (50, 120) else 4000, "tick_us": 0, "flags": 2} for v in range(200)]
    kinds, marked, unplaced = pr.once_summary(events, frames, start=10)
    assert kinds["shader compile"] == {"count": 2, "ms": 71.5} and unplaced == 1 and marked == {50, 51, 120, 121}
    assert pr.frame_stats(frames)["make_ms"]["worst"] == 45.0 and pr.frame_stats(frames, skip=marked)["make_ms"]["worst"] == 4.0
    run = {"side": "A", "stats": pr.frame_stats(frames), "stats_without": pr.frame_stats(frames, skip=marked), "once": kinds,
           "once_frames": 4, "once_unplaced": unplaced, "cpu": 5}
    from perf_text import render
    page = render({"bar_ms": 6.67, "bench": pr.bench([run, run]), "notes": [], "meta": {
        "name": "t", "machine": "m", "when": "", "size": "s", "replay": "r", "stop_at": 1, "builds": {"A": "x"}}})
    assert "Fault of the build" in page and "compiled 2 shaders during play (72 ms)" in page and "Without them" in page
    assert not pr.once_summary([], frames)[0]  # a clean run reports none


def test_kernel_time_without_a_caller_is_not_given_to_a_function(tmp_path):
    (tmp_path / "port.map").write_text(MAP)
    rows = [(i * 500, i // 4, 0, 2, 0xfffffffe if i % 4 == 0 else 0x1010, (0x8c010080,)) for i in range(800)]
    samples_file(tmp_path / "k.bin", rows)
    frames = [{"t_us": 0, "tick": t, "vblank": t, "gap_us": 6944, "make_us": 4000, "tick_us": 4000, "flags": 2} for t in range(200)]
    run = {"samples": pr.read_samples(tmp_path / "k.bin"), "frames": frames, "log": {"stamped": []}}
    rep = pr.analyse([run], Map(tmp_path / "port.map"))
    kernel = [f for f in rep["functions"] if "caller not known" in f["name"]]
    assert len(kernel) == 1 and "fn_8c010080" in kernel[0]["name"] and abs(kernel[0]["self_ms_normal"] - 0.5) < 0.01
    assert kernel[0]["bound"] == pr.CPU
    assert all("_fold" not in f["name"] for f in rep["functions"])  # not handed to the map's last symbol


def test_busy_system_rows_are_marked_and_kept_out_of_the_swap_list(tmp_path):
    (tmp_path / "port.map").write_text(MAP)
    rows = [(i * 500, i // 4, 0, 2, 0x80001010 if i % 2 else 0x1210, (0x8c010080,)) for i in range(800)]
    samples_file(tmp_path / "u.bin", rows)
    frames = [{"t_us": 0, "tick": t, "vblank": t, "gap_us": 6944, "make_us": 4000, "tick_us": 4000, "flags": 2} for t in range(200)]
    rep = pr.analyse([{"samples": pr.read_samples(tmp_path / "u.bin"), "frames": frames, "log": {"stamped": []}}], Map(tmp_path / "port.map"))
    unsure = [f for f in rep["functions"] if f["caller_uncertain"]]
    assert len(unsure) == 1 and unsure[0]["name"].endswith("[caller uncertain]") and unsure[0]["self_ms_normal"] > 0.9
    assert rep["swaps"] and all("uncertain" not in w["name"] for w in rep["swaps"])
    (tmp_path / "g.log").write_text("[x +1.0s v9] perf: timer game pacing sleep: 1234.5 ms in 600 calls, longest 9.87 ms\n")
    assert pr.log_lines(tmp_path / "g.log")["timers"] == [["game", "pacing sleep", 1234.5, 600, 9.87]]


def test_timer_lines_split_by_frames_within_and_over_the_bar(tmp_path):
    (tmp_path / "g.log").write_text("[x +1.0s v9] perf: timer frames: 900 within the bar, 100 over it (made in over 6670 us)\n"
                                    "[x +1.0s v9] perf: timer show pvr draw calls: 50.0 ms in 4000 calls, longest 0.40 ms; "
                                    "30.0 ms in frames within the bar, 20.0 ms in frames over it\n")
    log = pr.log_lines(tmp_path / "g.log")
    assert log["timer_frames"] == [900, 100]
    assert log["timers"] == [["show", "pvr draw calls", 50.0, 4000, 0.40, 30.0, 20.0]]
