"""Fast reference recordings: tools/reference.py's folder layout, recorded by Flycast itself at full dynarec speed.

    python tools/ref_fast.py record --game DIR --name NAME --input START@7000,.. --frame-pc ADDR --vblanks N
        [--every-vb 600] [--keyframe 300] [--card IMAGE] [--seconds 1800] [--max-gb 5] [--min-vbps 8] [--out DIR]
        [--flycast EXE] [--key JSON]
    python tools/ref_fast.py window --ref DIR --vb V --word ADDR [--budget 8] [--seconds 900] [--out DIR]

reference.py record catches each game frame at a PC checkpoint: Flycast pauses there, the control socket dumps the
16 MB of RAM to a file, and Flycast resumes. The checkpoint needs research.DynarecObservation, which charges each
instruction 8x its cycles (about 2 game frames per wall second). Here a FlycastRedux build with research.RamFrames*
(the divergence branch: core/research/ram_frames_runtime.cpp) writes the frames itself: the dynarec starts a block at
--frame-pc and the block's entry writes the RAM blocks that changed since the last frame, in the port's --ram-frames
format (runtime/ram_frames.c), with the vertical blank count where the port writes its emulator frame. No
instruction is observed, so the run's timing is a plain run's and nothing pauses per frame.

The frames are taken as the block at --frame-pc is entered (reference.py: after its first instruction completes);
the frame label and vertical blank are the same. Save states: every --every-vb vertical blanks Flycast pauses, the
count is read, the state saved (states/vNNNNNN.state) and the run resumed. A state's frames are found by vertical
blank (manifest "states": vb, file); a run from one counts on from there (lockstep_emu.lua LS_VB0, Flycast
research.RamFramesVblankBase). tools/reference.py port, ram, frame, info and unpack read the folder as they read
their own. coverage.txt: the start PC of each block the dynarec compiled (research.BlockCoveragePath; the code that
ran, for decomp.py divergence's coverage report). --card: Flycast starts from a copy of that memory card (tools/emu_card.py); the manifest keeps its path,
so the port's runs get one too (tools/divergence.py).

Bounded (tools/ref_bound.py): Flycast writes the frames into a pipe and they are packed as they arrive, so the raw
stream never reaches the disk; the run stops early, with the reason, over --max-gb (also when projected over it) or
when its speed so far cannot reach --vblanks in its time limit, which follows the vertical blanks (120 s + vblanks /
--min-vbps, at most --seconds); it does not start without twice its estimated size free. The recording goes to
OUT.new and replaces OUT only once complete and verified: a failed or stopped run leaves the old reference as it was
and its partial folder is removed. OUT.record.json (beside it) says how every run went: speed, size, the delta's
make-up by region of RAM, and why it stopped. --key: JSON stored as the manifest's divergence_key before the swap.

`window`: the original's writes to one word (ADDR, a guest RAM address) before vertical blank V, from the
reference's last save state at or before V (from boot without one): an observed replay (research.DynarecObservation)
with the workbench recording the SH-4's memory writes to that word (PC, PR, value; research.Workbench*, its
sh4_events rows). Observed instructions cost more cycles, so the same frames take more vertical blanks: the replay
runs --budget times the reference's vertical blanks from the state to V. Its frames are not the reference's (the
timing differs), so the writes are evidence of which code writes the word, not of the values at frame V.

Nearest existing: port/tools/reference.py - its PC checkpoint pauses Flycast every frame (slow, interpreter-bound);
this records the same folder layout at dynarec speed.
"""
import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import time

import emu_card
import lockstep
import ref_bound
import reference
from game_config import load as load_game

SLOT = reference.SLOT


def flycast(given=None):
    """The FlycastRedux build with research.RamFrames*: --flycast, else config "flycast_divergence"."""
    if given:
        return pathlib.Path(given)
    sys.path.insert(0, str(lockstep.PIPELINE))
    from core import toolcfg  # noqa: E402
    return pathlib.Path(toolcfg.need(toolcfg.load(lockstep.PIPELINE), "flycast_divergence"))


def label_address(spec):
    return 0x8C000000 | reference.label_offset(spec)


def entries(raw, pc, label, states, vb0=0, load=False, coverage=None):
    """raw: a file, or a pipe's name (a str, passed as it is)."""
    raw = raw if isinstance(raw, str) else raw.as_posix()
    out = [f"research:RamFramesPath={raw}", f"research:RamFramesPc={pc}",
           f"research:RamFramesLabel={label}", f"research:RamFramesVblankBase={vb0}",
           f"config:Dreamcast.SavestatePath={states.as_posix()}"]
    if load:
        out += ["config:Dreamcast.AutoLoadState=yes", f"config:Dreamcast.SavestateSlot={SLOT}"]
    if coverage:
        out.append(f"research:BlockCoveragePath={coverage.as_posix()}")
    return out


class Drive:
    """Waits for vertical blank `until`, saving a state every `every_vb` (paused: the count read is the state's).
    guard(vb, now): a reason to stop early (ref_bound.Bound), kept in self.stop."""

    def __init__(self, vbfile, until, every_vb, tmp, dest, guard=None):
        self.vbfile, self.until, self.every_vb, self.tmp, self.dest = vbfile, until, every_vb, tmp, dest
        self.states, self.vb, self.t0, self.guard, self.stop = [], 0, time.monotonic(), guard, None

    def read_vb(self):
        try:
            return int(self.vbfile.read_text(encoding="ascii").split()[0])
        except (OSError, ValueError, IndexError):
            return self.vb

    def __call__(self, ctl, proc, deadline):
        due = self.every_vb
        while proc.poll() is None and time.monotonic() < deadline:
            self.vb = self.read_vb()
            if self.vb >= self.until:
                return
            self.stop = self.guard(self.vb, time.monotonic()) if self.guard else None
            if self.stop:
                return
            if self.every_vb and self.vb >= due:
                ctl.request("pause", timeout=30.0)
                vb = self.read_vb()
                ctl.request("savestate", {"slot": SLOT}, timeout=120.0)
                made = max(self.tmp.glob("*.state"), key=lambda p: p.stat().st_mtime)
                dest = self.dest / f"v{vb:06d}.state"
                os.replace(made, dest)
                self.states.append({"vb": vb, "file": dest.name, "bytes": dest.stat().st_size,
                                    "wall_s": round(time.monotonic() - self.t0, 1)})
                ctl.request("resume", timeout=30.0)
                due = vb + self.every_vb
            time.sleep(0.05)


def record(game, name, inputs, frame_pc, vblanks, every_vb=600, keyframe=300, seconds=1800, out=None, exe=None,
           spec=None, card=None, max_gb=ref_bound.MAX_GB, min_vbps=ref_bound.MIN_VBPS, key=None):
    """Records the reference (bounded: see the module's notes); returns its manifest, or SystemExit with the reason
    (the old reference kept)."""
    game = pathlib.Path(game).resolve()
    load_game(game)
    ref = pathlib.Path(out or game / "build" / "reference" / name).resolve()
    ref_bound.restore(ref)
    new = ref.with_name(ref.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    ref.parent.mkdir(parents=True, exist_ok=True)
    report = ref.with_name(ref.name + ".record.json")
    report.unlink(missing_ok=True)
    try:
        est = ref_bound.check_disk(ref.parent, vblanks, max_gb)
    except SystemExit as e:
        report.write_text(json.dumps({"name": name, "ok": False, "reason": str(e)}, indent=1), encoding="utf-8")
        raise
    limit = ref_bound.time_limit(vblanks, seconds, min_vbps)
    tmp = new / "tmp"
    tmp.mkdir(parents=True)
    (new / "states").mkdir()
    spec_path = pathlib.Path(spec or game / "lockstep.spec")
    shutil.copyfile(spec_path, new / "lockstep.spec")
    vbfile = tmp / "vb.txt"
    exe = flycast(exe)
    stream = ref_bound.Stream(new / "ram", keyframe)
    t0 = time.monotonic()
    bound = ref_bound.Bound(vblanks, limit, lambda: ref_bound.folder_bytes(new) + stream.held(),
                            lambda: shutil.disk_usage(ref.parent).free, max_gb, t0)

    def guard(vb, now):
        stream.stop = stream.stop or bound(vb, now)
        return stream.stop
    d = Drive(vbfile, vblanks, every_vb, tmp, new / "states", guard)
    more = emu_card.emu_entries(load_game(game)["disc"], card, tmp) if card else []
    try:
        wall = lockstep.emu_run(game, new / "lockstep.spec", tmp / "emu.txt", limit, lockstep.parse_input(inputs), exe,
                                lua_vars={"LS_VBFILE": f'"{vbfile.as_posix()}"'},
                                entries=entries(stream.name, int(frame_pc, 16), label_address(spec_path), tmp,
                                                coverage=tmp / "coverage.txt") + more, drive=d)
    except BaseException:
        stream.stop = "the run failed"
        stream.finish()
        shutil.rmtree(new, ignore_errors=True)
        raise
    stats = stream.finish()
    pack_s = round(max(0.0, time.monotonic() - t0 - wall), 1)
    why = (d.stop or stream.error or (None if stats and stats["frames"] else
                                     f"{exe}: no RAM frames written (a build without research.RamFrames*?)")
           or (None if d.vb >= vblanks else f"vertical blank {d.vb} of {vblanks} in the {limit:.0f} s limit"))
    why = why or ref_bound.verify(new, stats["frames"], vblanks)
    run = {"name": name, "ok": not why, "reason": why, "vblanks": vblanks, "reached_vb": d.vb, "limit_s": round(limit),
           "wall_s": round(wall, 1), "pack_after_s": pack_s, "estimated_gb": round(est / 1e9, 2),
           "vbps": round(d.vb / wall, 2) if wall else None, "bound": bound.seen,
           "frames": stats and stats["frames"], "stored_mb": stats and round(stats["stored_bytes"] / 1e6, 1),
           "per_frame": stats and reference.summary(stats, 0), "stream": stats and stats.get("stream"),
           "states_mb": round(sum(x["bytes"] for x in d.states) / 1e6, 1)}
    if why:
        run["disk_mb"] = round(ref_bound.folder_bytes(new) / 1e6, 1) if new.exists() else 0
        shutil.rmtree(new, ignore_errors=True)
        report.write_text(json.dumps(run, indent=1), encoding="utf-8")
        raise SystemExit(f"record {name}: {why} (nothing replaced; {ref.name}.record.json)")
    for text in ("emu.txt", "emu_draws.txt"):
        reference.pack(tmp / text, new / f"{text}.zst")
    blocks = 0
    if (tmp / "coverage.txt").exists():
        os.replace(tmp / "coverage.txt", new / "coverage.txt")
        blocks = len((new / "coverage.txt").read_text(encoding="ascii").split())
    shutil.rmtree(tmp, ignore_errors=True)
    m = {"name": name, "game": str(game), "input": inputs, "frame_pc": frame_pc, "vblanks": vblanks, "fast": True,
         "card": str(pathlib.Path(card).resolve()) if card else None,
         "segments": 1, "every": 0, "every_vb": every_vb, "keyframe": keyframe, "hw": False,
         "rtc_seed": reference.ec.RTC_SEED, "flycast": str(exe), "wall_s": wall, "pack_s": pack_s,
         "reached_vb": d.vb, "complete": True, "states": d.states, "coverage_blocks": blocks,
         "ram": reference.summary(stats, vblanks / 3600.0), "stream": stats.get("stream"),
         "disk_mb": reference.disk_mb(new)}
    if key is not None:
        m["divergence_key"] = key
    (new / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    ref_bound.swap_in(new, ref)
    run["disk_mb"] = m["disk_mb"]
    report.write_text(json.dumps(run, indent=1), encoding="utf-8")
    print(f"reference {ref}: {stats['frames']} frames to vblank {d.vb} in {wall:.0f} s wall ({run['vbps']} vb/s, "
          f"+{pack_s:.0f} s packing after), {len(d.states)} save states, {m['disk_mb']:.1f} MB")
    return m


def window(ref, vb, word, budget=8, seconds=900, out=None, exe=None, keep=64):
    """The original's writes to `word` (see the module's notes): a dict with the writes in order (at most `keep`)."""
    ref = pathlib.Path(ref).resolve()
    m = reference.manifest(ref)
    disc = pathlib.Path(load_game(m["game"])["disc"])
    st = max((s for s in m["states"] if s["vb"] <= vb), key=lambda s: s["vb"], default=None)
    out = pathlib.Path(out or ref / "window").resolve()
    shutil.rmtree(out, ignore_errors=True)
    tmp = out / "tmp"
    tmp.mkdir(parents=True)
    db, vbfile, start = out / "writes.db", tmp / "vb.txt", st["vb"] if st else 0
    lua = {"LS_VBFILE": f'"{vbfile.as_posix()}"'}
    more = ["research:DynarecObservation=yes", f"research:WorkbenchRecord={db.as_posix()}",
            "research:WorkbenchBuses=sh4", "research:WorkbenchSh4Types=memory-write",
            f"research:WorkbenchSh4MemStart={word}", f"research:WorkbenchSh4MemEnd={word + 3}",
            f"config:Dreamcast.SavestatePath={tmp.as_posix()}"]
    if st:
        shutil.copyfile(ref / "states" / st["file"], tmp / f"{disc.stem}_{SLOT}.state")
        lua["LS_VB0"] = st["vb"]
        more += ["config:Dreamcast.AutoLoadState=yes", f"config:Dreamcast.SavestateSlot={SLOT}"]
    if m.get("card"):
        more += emu_card.emu_entries(disc, m["card"], tmp)
    d = Drive(vbfile, start + budget * (vb - start) + 60, 0, tmp, tmp)
    wall = lockstep.emu_run(m["game"], ref / "lockstep.spec", tmp / "emu.txt", seconds, lockstep.parse_input(m["input"]),
                            flycast(exe or m.get("flycast")), lua_vars=lua, entries=more, drive=d)
    rows = []
    if db.exists():
        with sqlite3.connect(db) as c:
            rows = c.execute("SELECT pc, pr, mem_addr, mem_width, mem_value FROM sh4_events WHERE type = 5 "
                             "ORDER BY ordinal").fetchall()
    writers = {}
    for pc, *_ in rows:
        writers[f"{pc:08x}"] = writers.get(f"{pc:08x}", 0) + 1
    shutil.rmtree(tmp, ignore_errors=True)
    return {"state_vb": start, "until_vb": d.until, "reached_vb": d.vb, "wall_s": round(wall, 1), "count": len(rows),
            "writers": writers, "writes": [{"pc": f"{pc:08x}", "pr": f"{pr:08x}" if pr is not None else None,
                                            "addr": f"{a:08x}", "width": w, "value": f"{v:08x}"}
                                           for pc, pr, a, w, v in rows[-keep:]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--game", required=True)
    r.add_argument("--name", required=True)
    r.add_argument("--input", default="")
    r.add_argument("--frame-pc", required=True)
    r.add_argument("--vblanks", type=int, required=True)
    r.add_argument("--every-vb", type=int, default=600)
    r.add_argument("--keyframe", type=int, default=300)
    r.add_argument("--seconds", type=float, default=1800)
    r.add_argument("--card", help="a memory card image both sides start from")
    r.add_argument("--spec")
    r.add_argument("--out")
    r.add_argument("--flycast")
    r.add_argument("--max-gb", type=float, default=ref_bound.MAX_GB, help="the reference's size cap")
    r.add_argument("--min-vbps", type=float, default=ref_bound.MIN_VBPS, help="the time limit: 120 s + vblanks / this")
    r.add_argument("--key", help="JSON stored as the manifest's divergence_key (core/divergence.py)")
    w = sub.add_parser("window")
    w.add_argument("--ref", required=True)
    w.add_argument("--vb", type=int, required=True)
    w.add_argument("--word", required=True)
    w.add_argument("--budget", type=int, default=8)
    w.add_argument("--seconds", type=float, default=900)
    w.add_argument("--out")
    w.add_argument("--flycast")
    a = ap.parse_args(argv)
    if a.cmd == "window":
        res = window(a.ref, a.vb, int(a.word, 16), a.budget, a.seconds, a.out, a.flycast)
        print(json.dumps(res, indent=1))
        return 0
    m = record(a.game, a.name, a.input, a.frame_pc, a.vblanks, a.every_vb, a.keyframe, a.seconds, a.out, a.flycast,
               a.spec, a.card, a.max_gb, a.min_vbps, json.loads(a.key) if a.key else None)
    return 0 if m["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
