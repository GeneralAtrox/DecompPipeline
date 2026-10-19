"""Recorded reference sessions: one recording per scenario, stored once and read by every later check.

    python tools/reference.py record --game DIR --name intro --input START@7000,.. --frame-pc ADDR
        --vblanks 6000 [--every 300] [--every-vb 600] [--keyframe 300] [--hw] [--seconds 1800] [--out DIR]
    python tools/reference.py replay --ref DIR --frame F [--frames N] [--out DIR]
    python tools/reference.py port --ref DIR [--build-dir x86-agent] [--range A:B] [--frame N] [--from N] [--ignore 30]
    python tools/reference.py ram --ref DIR --port-ram FILE [--frame N] [--from N] [--ignore 30]
    python tools/reference.py frame --ref DIR --frame F [--words ADDR,..]
    python tools/reference.py info --ref DIR | unpack --ref DIR

A reference (default GAME/build/reference/NAME: game data, never in git) holds, for one scripted input:
- manifest.json: the input, the frame PC, the spec, the save states and the recording's cost;
- emu.txt.zst, emu_draws.txt.zst (lockstep_emu.lua's frames and draws) and, with --hw, hw.txt.zst (hw_emu.lua's display
  lists and texture hashes); `unpack` writes them out to DIR/work for tools/lockstep.py --emu and tools/hw_check.py;
- ram.zram/ram.zidx (ram_delta.py): main RAM at each game frame, as deltas with a full keyframe every --keyframe;
- states/fNNNNNN.state: a Flycast save state every --every frames or --every-vb vertical blanks, whichever comes
  first (Flycast's own compressed format).

A game frame is caught where the instruction at --frame-pc completes (the game's once-per-frame clock function):
Flycast pauses there (control-socket checkpoint), the RAM is dumped, and Flycast resumes.
Pausing is invisible to the guest (emulated time stands still), but the checkpoint needs research.DynarecObservation,
which charges each SH-4 instruction 8x its cycles (FlycastRedux research/sh4_observation_timing.cpp, warmupCycles):
the game runs fewer instructions per vertical blank than in an unobserved run, so its frames are not that run's. That
counter's memory-operation count is an int never reset in a run; past 2^31 (about 150 emulated seconds) every memory
operation pays 16 cycles more, and a run loaded from a save state (count 0) no longer matches the one that saved it.
Even before that, the counter is not in the state: a loaded run starts a few cycles off the run that saved it, and
interrupts land an instruction apart (the frame label one timer tick off, Kamui's counters one step apart).
So each save state starts a new Flycast run (the run before goes on a few frames past it, for the texts' join in
ref_texts.py): every recorded frame comes from a run loaded from its nearest save state.

`replay` does the same from the save state at or before frame F (Dreamcast.AutoLoadState; lockstep_emu.lua resumes
its vertical blank count and held input there), a new run at each later state, up to frame F + N; it reports the
RAM words that differ from the reference at each frame (none: the replay is the recording again) and leaves the
texts in its folder. `frame` rebuilds one frame's RAM.
"""
import argparse
import json
import os
import pathlib
import shutil
import sys
import time

import zstandard

import emu_capture as ec
import lockstep
import ram_compare
import ram_delta
import ref_texts
from game_config import load as load_game

RAM_BASE = 0x8C000000
SLOT = 9                    # the save state slot the recorder uses (its own folder: Dreamcast.SavestatePath)
TEXTS = ref_texts.TEXTS


def label_offset(spec):
    item = next(i for i in lockstep.parse_spec(spec) if "label" in i["flags"])
    return int(item["addr"].split("|")[0], 16) & (ram_delta.RAM_SIZE - 1)


def wait_paused(ctl, proc, deadline):
    while proc.poll() is None and time.monotonic() < deadline:
        s = ctl.request("status")
        if s["checkpoint"]["triggered"] and s["paused"]:
            return True
        time.sleep(0.001)
    return False


class Session:
    """Catches each game frame of a Flycast run: RAM deltas, and a save state every `every` frames."""

    def __init__(self, ref, out, pc, label_off, until_vb, keyframe, buffer_mb, every=0, first=0, every_vb=0):
        self.ref, self.out, self.pc, self.label_off = ref, out, pc, label_off
        self.until_vb, self.every, self.every_vb, self.first, self.state_vb = until_vb, every, every_vb, first, None
        self.tmp = out / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.vbfile, self.dump = self.tmp / "vb.txt", self.tmp / "ram.bin"
        self.writer = ram_delta.Writer(out / "ram", keyframe, buffer_mb)
        self.states, self.frames, self.stats, self.overlap, self.cut, self.vb = [], 0, None, 0, None, 0
        self.cuts = {}  # replay: the reference's save states, where its runs began
        self.t = {"wait": 0.0, "dump": 0.0, "delta": 0.0, "state": 0.0}
        self.t0 = time.monotonic()

    def drive(self, ctl, proc, deadline):
        """The frames up to until_vb. With `overlap`, the run ends at its first save state, made or in `cuts`
        (self.cut: the next run starts from it), after `overlap` more frames run but not recorded, for the texts'
        join (ref_texts.py)."""
        self.cut, extra = None, 0
        ctl.request("checkpoint_set", {"pc": self.pc})
        while proc.poll() is None and time.monotonic() < deadline:
            t = time.perf_counter()
            if not wait_paused(ctl, proc, deadline):
                break
            t1 = time.perf_counter()
            vb = self.vb = int(self.vbfile.read_text(encoding="ascii").split()[0])
            if self.cut is not None:
                extra += 1
                if extra >= self.overlap:
                    break
            else:
                ram = self.dump_ram(ctl)
                t2 = time.perf_counter()
                label = int.from_bytes(ram[self.label_off:self.label_off + 4], "little")
                frame = self.first + self.frames
                self.writer.add(ram, f"{frame} {vb} {label:08x}")
                t3 = time.perf_counter()
                end = vb >= self.until_vb
                save = self.every and not end and (self.state_vb is None or self.frames % self.every == 0
                                                   or (self.every_vb and vb - self.state_vb >= self.every_vb))
                if save:
                    self.save_state(ctl, frame, vb, label)
                    self.state_vb = vb
                self.t["wait"] += t1 - t
                self.t["dump"] += t2 - t1
                self.t["delta"] += t3 - t2
                self.t["state"] += time.perf_counter() - t3
                self.frames += 1
                if end:
                    break
                cut = self.states[-1] if save else self.cuts.get(frame)
                if cut and self.overlap:
                    self.cut = cut
            ctl.request("checkpoint_set", {"pc": self.pc})  # armed before the run goes on: no frame is missed
            ctl.request("resume")

    def close(self):
        self.stats = self.writer.close()
        return self.stats

    def dump_ram(self, ctl):
        """16 MB of RAM through a file; two files in turn, and a retry while another process (a scanner) holds one."""
        path = self.dump.with_suffix(f".{self.frames % 2}.bin")
        for attempt in range(40):
            try:
                ctl.request("mem_dump", {"addr": RAM_BASE, "len": ram_delta.RAM_SIZE, "path": str(path)})
                return path.read_bytes()
            except (RuntimeError, OSError):
                if attempt == 39:
                    raise
                time.sleep(0.05)
        return None

    def save_state(self, ctl, frame, vb, label):
        ctl.request("savestate", {"slot": SLOT}, timeout=120.0)
        made = max(self.tmp.glob("*.state"), key=lambda p: p.stat().st_mtime)
        dest = self.ref / "states" / f"f{frame:06d}.state"
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(made, dest)
        self.states.append({"frame": frame, "vb": vb, "label": f"{label:08x}", "file": dest.name,
                            "bytes": dest.stat().st_size, "wall_s": round(time.monotonic() - self.t0, 1)})


def state_entries(folder, load):
    entries = ["research:DynarecObservation=yes", f"config:Dreamcast.SavestatePath={folder.as_posix()}"]
    if load:
        entries += ["config:Dreamcast.AutoLoadState=yes", f"config:Dreamcast.SavestateSlot={SLOT}"]
    return entries


def pack(src, dest):
    if src.exists():
        with open(src, "rb") as f, open(dest, "wb") as g:
            zstandard.ZstdCompressor(level=9).copy_stream(f, g)
        src.unlink()


def unpack(ref):
    work = ref / "work"
    work.mkdir(exist_ok=True)
    for name in TEXTS:
        src, dest = ref / f"{name}.zst", work / name
        if src.exists() and (not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime):
            with open(src, "rb") as f, open(dest, "wb") as g:
                zstandard.ZstdDecompressor().copy_stream(f, g)
    return work


def summary(stats, minutes):
    ch, bl = stats["changed_bytes"] or [0], stats["block_bytes"] or [0]
    return {"frames": stats["frames"], "keyframes": stats["keyframes"],
            "changed_bytes_avg": sum(ch) / len(ch), "changed_bytes_peak": max(ch),
            "block_bytes_avg": sum(bl) / len(bl), "block_bytes_peak": max(bl),
            "stored_mb": stats["stored_bytes"] / 1e6,
            "stored_mb_per_min": stats["stored_bytes"] / 1e6 / minutes if minutes else 0.0,
            "compress_s": stats["seconds"]}


def record(a):
    load_game(game := pathlib.Path(a.game))
    ref = pathlib.Path(a.out or game / "build" / "reference" / a.name).resolve()
    ref.mkdir(parents=True, exist_ok=True)
    spec = pathlib.Path(a.spec or game / "lockstep.spec")
    shutil.copyfile(spec, ref / "lockstep.spec")
    s = Session(ref, ref, int(a.frame_pc, 16), label_offset(spec), a.vblanks, a.keyframe, a.buffer_mb, a.every,
                every_vb=a.every_vb)
    s.overlap = 3
    segments, wall = run(s, game, spec, lockstep.parse_input(a.input), a.flycast, a.hw, a.seconds, None)
    s.close()
    dropped = ref_texts.join(segments, ref)
    shutil.rmtree(segments[0].parent, ignore_errors=True)
    for name in TEXTS:
        pack(ref / name, ref / f"{name}.zst")
    shutil.rmtree(s.tmp, ignore_errors=True)
    minutes = a.vblanks / 60.0 / 60.0
    m = {"name": a.name, "game": str(game.resolve()), "input": a.input, "frame_pc": a.frame_pc, "vblanks": a.vblanks,
         "segments": len(segments), "every": a.every, "every_vb": a.every_vb, "keyframe": a.keyframe, "hw": a.hw,
         "rtc_seed": ec.RTC_SEED, "flycast": str(lockstep.flycast_exe(a.flycast)), "wall_s": wall, "times_s": s.t,
         "joins": dropped,
         "states": s.states, "ram": summary(s.stats, minutes), "disk_mb": disk_mb(ref)}
    (ref / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(f"reference {ref}: {s.frames} frames in {wall:.0f} s wall, {len(segments)} segments, "
          f"{len(s.states)} save states, {m['disk_mb']:.1f} MB")
    print(json.dumps(m["ram"], indent=1))
    return 0 if s.frames and s.vb >= a.vblanks else 1


def run(s, game, spec, inputs, exe, hw, seconds, start):
    """Session s's Flycast runs: the first from save state `start` (from boot when None), each next one from the state
    the one before ended on (s.cut). Each run's texts go to s.out/segments/NNN; returns those folders and the wall."""
    disc = pathlib.Path(load_game(game)["disc"])
    segments, wall = [], 0.0
    while True:
        seg = s.out / "segments" / f"{len(segments):03d}"
        lua = {"LS_VBFILE": f'"{s.vbfile.as_posix()}"'}
        if start:
            shutil.copyfile(s.ref / "states" / start["file"], s.tmp / f"{disc.stem}_{SLOT}.state")
            lua["LS_VB0"] = start["vb"]
        wall += lockstep.emu_run(game, spec, seg / "emu.txt", seconds - wall, inputs, exe,
                                 hw=seg / "hw.txt" if hw else None, lua_vars=lua,
                                 entries=state_entries(s.tmp, start is not None), drive=s.drive)
        segments.append(seg)
        print(f"  run {len(segments) - 1}: to frame {s.first + s.frames - 1}, {wall:.0f} s wall")
        if s.cut is None:
            return segments, wall
        start = s.cut


def disk_mb(ref):
    return sum(p.stat().st_size for p in ref.rglob("*") if p.is_file() and "work" not in p.parts) / 1e6


def manifest(ref):
    return json.loads((ref / "manifest.json").read_text(encoding="utf-8"))


def replay(a):
    ref = pathlib.Path(a.ref).resolve()
    m = manifest(ref)
    st = max((x for x in m["states"] if x["frame"] <= a.frame), key=lambda x: x["frame"])
    out = pathlib.Path(a.out or ref / "replay").resolve()
    shutil.rmtree(out, ignore_errors=True)
    s = Session(ref, out, int(m["frame_pc"], 16), label_offset(ref / "lockstep.spec"), 1 << 30, m["keyframe"],
                a.buffer_mb, 0, st["frame"] + 1)  # the state was saved paused at its frame's checkpoint
    s.overlap, s.cuts = 3, {x["frame"]: x for x in m["states"] if x["frame"] > st["frame"]}
    reader = ram_delta.Reader(ref / "ram")
    last = min(a.frame + a.frames, len(reader) - 1)
    s.until_vb = int(reader.tag(last)[1])
    segments, wall = run(s, m["game"], ref / "lockstep.spec", lockstep.parse_input(m["input"]), m["flycast"],
                         m["hw"], a.seconds, st)
    s.close()
    ref_texts.join(segments, out)
    shutil.rmtree(segments[0].parent, ignore_errors=True)
    shutil.rmtree(s.tmp, ignore_errors=True)
    print(f"replay from frame {st['frame']} (vblank {st['vb']}): {s.frames} frames in {wall:.1f} s wall")
    got = ram_delta.Reader(out / "ram")
    same = differ = 0
    for (i, ram), (j, want_ram) in zip(got.frames(), reader.frames(st["frame"] + 1)):
        f, vb, label = got.tag(i)
        want = reader.tag(j)
        if want != [f, vb, label]:
            print(f"  frame {f}: replay vblank {vb} label {label}, reference vblank {want[1]} label {want[2]}")
            differ += 1
            continue
        words = ram_delta.differing_words(ram, want_ram)
        if words:
            differ += 1
            print(f"  frame {f}: {len(words)} words differ, first {', '.join('%08x' % w for w in words[:6])}")
        else:
            same += 1
    print(f"replayed frames equal to the reference: {same}; different: {differ}")
    return 0 if differ == 0 and same else 1


def frame(a):
    ref = pathlib.Path(a.ref).resolve()
    r = ram_delta.Reader(ref / "ram")
    t0 = time.perf_counter()
    ram = r.frame(a.frame)
    dt = time.perf_counter() - t0
    print(f"frame {a.frame} (vblank {r.tag(a.frame)[1]}, label {r.tag(a.frame)[2]}): rebuilt in {dt * 1000:.0f} ms")
    for w in filter(None, (a.words or "").split(",")):
        addr = int(w, 16)
        off = addr & (ram_delta.RAM_SIZE - 1)
        print(f"  {addr:08x} {int.from_bytes(ram[off:off + 4], 'little'):08x}")
    return 0


def info(a):
    ref = pathlib.Path(a.ref).resolve()
    print(json.dumps(manifest(ref), indent=1))
    return 0


def port(a):
    """The port driven by the reference's frames (tools/lockstep.py port), recording its RAM, then the RAM compare."""
    ref = pathlib.Path(a.ref).resolve()
    m = manifest(ref)
    work = unpack(ref)
    out = pathlib.Path(a.out or ref / f"port_{a.build_dir}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "ram.raw"
    seconds = a.seconds or int(m["vblanks"] / 60) + 10
    extra = ["--ram-frames", str(raw)] + (["--ram-frames-range", a.range] if a.range else [])
    t0 = time.monotonic()
    lockstep.main(["port", "--game", m["game"], "--spec", str(ref / "lockstep.spec"), "--emu", str(work / "emu.txt"),
                   "--out", str(out), "--build-dir", a.build_dir, "--seconds", str(seconds)] + extra)
    t1 = time.monotonic()
    raw_mb = raw.stat().st_size / 1e6
    stats = ram_delta.pack_raw(raw, out / "ram", m["keyframe"])
    print(f"port RAM: {stats['frames']} frames, {raw_mb:.0f} MB raw -> {stats['stored_bytes'] / 1e6:.1f} MB "
          f"stored; port {t1 - t0:.0f} s, packing {time.monotonic() - t1:.0f} s")
    names = ram_compare.global_names(m["game"], a.build_dir)
    return ram_compare.compare(ref / "ram", out / "ram", a.frame, a.ignore, names,
                               ram_compare.drive_labels(out / "drive.txt"), ram_compare.region(a.region, names),
                               a.start)


def ram(a):
    ref = pathlib.Path(a.ref).resolve()
    names = ram_compare.global_names(manifest(ref)["game"], a.build_dir)
    port_ram = pathlib.Path(a.port_ram).resolve()
    drive = ram_compare.drive_labels(a.drive or port_ram.parent / "drive.txt")
    return ram_compare.compare(ref / "ram", port_ram, a.frame, a.ignore, names, drive,
                               ram_compare.region(a.region, names), a.start)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--game", required=True)
    r.add_argument("--name", required=True)
    r.add_argument("--input", required=True)
    r.add_argument("--frame-pc", required=True)
    r.add_argument("--vblanks", type=int, required=True, help="stop at the first frame at or past this vertical blank")
    r.add_argument("--every", type=int, default=300, help="a save state every N frames")
    r.add_argument("--every-vb", type=int, default=600, help="and at least every N vertical blanks (movies, loads)")
    r.add_argument("--keyframe", type=int, default=300, help="a full RAM keyframe every N frames")
    r.add_argument("--buffer-mb", type=int, default=1024, help="compressed RAM held in memory before it is written")
    r.add_argument("--hw", action="store_true", help="also record the hardware check's lists (hw_emu.lua)")
    r.add_argument("--seconds", type=float, default=1800)
    r.add_argument("--spec")
    r.add_argument("--out")
    r.add_argument("--flycast")
    p = sub.add_parser("replay")
    p.add_argument("--ref", required=True)
    p.add_argument("--frame", type=int, required=True)
    p.add_argument("--frames", type=int, default=60)
    p.add_argument("--buffer-mb", type=int, default=256)
    p.add_argument("--seconds", type=float, default=600)
    p.add_argument("--out")
    f = sub.add_parser("frame")
    f.add_argument("--ref", required=True)
    f.add_argument("--frame", type=int, required=True)
    f.add_argument("--words")
    i = sub.add_parser("info")
    i.add_argument("--ref", required=True)
    u = sub.add_parser("unpack")
    u.add_argument("--ref", required=True)
    q = sub.add_parser("port")
    q.add_argument("--ref", required=True)
    q.add_argument("--build-dir", default="x86-agent")
    q.add_argument("--range", help="A:B: only port frames A to B")
    q.add_argument("--frame", type=int, help="list the words that differ at this port frame")
    q.add_argument("--ignore", type=int, default=30, help="paired frames that set the always-different words")
    q.add_argument("--region", help="all, or LO-HI (hex); default: the game's globals")
    q.add_argument("--from", dest="start", type=int, default=0, help="skip port frames before this")
    q.add_argument("--seconds", type=int, help="game seconds the port runs (default: the reference's length + 10)")
    q.add_argument("--out")
    c = sub.add_parser("ram")
    c.add_argument("--ref", required=True)
    c.add_argument("--port-ram", required=True, help="a port RAM recording (port's OUT/ram)")
    c.add_argument("--build-dir", default="x86-agent", help="for the globals' names")
    c.add_argument("--drive", help="the port run's drive file (default: next to --port-ram)")
    c.add_argument("--frame", type=int)
    c.add_argument("--ignore", type=int, default=30)
    c.add_argument("--region", help="all, or LO-HI (hex); default: the game's globals")
    c.add_argument("--from", dest="start", type=int, default=0, help="skip port frames before this")
    a = ap.parse_args(argv)
    if a.cmd == "unpack":
        print(unpack(pathlib.Path(a.ref).resolve()))
        return 0
    return {"record": record, "replay": replay, "frame": frame, "info": info, "port": port, "ram": ram}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
