"""A port build's first divergence from a recorded reference, and the port function that wrote it.

    python tools/divergence.py --ref DIR [--build-dir x86-agent] [--out DIR] [--from F] [--ignore 30] [--word ADDR,..]
        [--float-ulps N] [--baseline RAM] [--seconds N] [--no-window] [--json FILE]

--ref: a reference folder (tools/ref_fast.py or tools/reference.py record). Three steps, each bounded:
1. The port, driven by the reference's frames (tools/lockstep.py: timer, pads and RTC from the paired emulator frame;
   headless, fixed clock), records its RAM as each frame starts (--ram-frames).
2. Paired frames (by frame label, ram_compare.paired_frames) are compared word by word over the game's globals (the
   span of the build's game.h globals, ram_compare.region: the heap and the stack are the port's own). Left out: the
   port frames before --from, the words that differ in each of the first --ignore paired frames after it (memory the
   two sides use differently: the stack, the SDK work areas the port replaces) and the game's documented ranges
   (GAME/divergence_ignore.txt, one "LO HI reason" per line, hex, HI exclusive). The first frame with another
   differing word is the divergence. The noise words that are game globals (named from game.h) are listed in the
   result's noise_globals, and a run with any is never "no divergence" (exit 1): a word wrong from the first frame
   on is noise too, so it could hide a real divergence; document a range that really is the port's own instead.
   --float-ulps N (a TEMPORARY diagnostic, until the kit's SH4 float approximations
   are bit-exact) also leaves out normal floats of the same sign and exponent at most N ulps apart; they are counted
   in the result's own float_close section, and a run with any is never zero divergence. --baseline RAM (also
   TEMPORARY, for proofs while kit-side classes remain) leaves out each word another build's port RAM on the same
   reference (its divergence folder's ram) also holds at that frame: what diverges is what this build does
   differently from that one. Never zero divergence against the original.
3. The port runs again to that frame with --watch-ram-frame on up to four of those words (watch_words: one per differing
   global, in address order; or --word): each write it makes to them between the frame before and that frame is named
   from port.map (the function, then its callers), in the order they ran. The writer is the earliest of the words'
   last writes (the values the frame shows): a wrong value is usually copied on into the others later in the frame
   (a camera's matrix into the draw's matrix stack, say), so the first store of one is nearest the cause. Its guest
   address and readable file come from the build's dispatch table. No write: the port left the words alone where the
   original wrote them (or the values came from an earlier frame).
4. The original's side (not with --no-window): Flycast replays from the reference's nearest save state with the
   memory writes to the writer's word recorded (tools/ref_fast.py window), and each writing PC is named by the decomp
   catalog's function that holds it.
Writes the result (`result` below) to --json (default OUT/divergence.json); exit 0 when nothing diverges, 1 when it
does (or only float-close words do), 2 when a step failed.

Nearest existing: port/tools/lockstep.py and ram_compare.py - lockstep pairs frames and compares samples, ram_compare
compares two RAM dumps; neither names the first differing global nor the port function that wrote it. This uses both.
"""
import argparse
import bisect
import contextlib
import json
import pathlib
import re
import sys
import time

import emu_card
import lockstep
import ram_compare
import ram_delta
import ref_fast
import reference
from game_config import load as load_game
from lockstep_watch import FUNC_REACH, exit_line, read_map, watch_hits

WORDS_KEPT = 64  # differing words listed in the result
WATCHED = 4  # words the port's watch can take at once (runtime/lockstep_watch.c: one x86 debug register each)


def ignore_ranges(game):
    """[(lo, hi, reason)] from GAME/divergence_ignore.txt."""
    path = pathlib.Path(game) / "divergence_ignore.txt"
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            p = line.split(None, 2)
            if len(p) >= 2 and not line.lstrip().startswith("#"):
                out.append((int(p[0], 16), int(p[1], 16), p[2] if len(p) > 2 else ""))
    return out


def dispatch_names(game, build_dir):
    """{host function name: guest address} from the build's generated dispatch table (tools/gen_port.py)."""
    rows = re.compile(r"\{0x([0-9a-f]{8})u, \(PortFn\)(\w+),")
    out = {}
    for p in sorted(ram_compare.gen_dir(game, build_dir).glob("dispatch_*.c")):
        for m in rows.finditer(p.read_text(encoding="utf-8")):
            out.setdefault(m.group(2), m.group(1))
    return out


def word(ram, addr):
    off = addr - 0x8C000000
    return int.from_bytes(ram[off:off + 4], "little")


def float_close(a, b, ulps):
    """Two normal floats with the same sign and exponent, at most ulps apart (the TEMPORARY --float-ulps diagnostic)."""
    e = a >> 23 & 0xFF
    return (a ^ b) >> 23 == 0 and 0 < e < 0xFF and abs(a - b) <= ulps


def symbolizer(map_path):
    """A host address -> "symbol+offset" lookup from a build's port.map (None outside its symbols)."""
    _, syms = read_map(map_path)
    keys = [a for a, _ in syms]

    def name(a):
        i = bisect.bisect_right(keys, a) - 1
        return f"{syms[i][1]}+{a - syms[i][0]:x}" if i >= 0 and a - syms[i][0] < FUNC_REACH else None
    return name


def same_symbol(maps, base_value, value):
    """Both builds' host pointers name the same symbol and offset (each from its own port.map)."""
    name = maps[0](base_value) if maps else None
    return name is not None and name == maps[1](value)


def baseline_frames(base_ram, port_ram):
    """A lookup of another port build's RAM by port frame (the TEMPORARY --baseline diagnostic): frames are asked for
    in order; None for a frame it does not have. Also the words the two builds already hold differently in their
    first frame, before game code runs: the build's own layout (the kit's host jump stubs in guest RAM move with the
    executable), left out like noise."""
    reader = ram_delta.Reader(base_ram)
    other = ram_delta.Reader(port_ram)
    try:
        layout = set(ram_delta.differing_words(reader.frame(0), other.frame(0), 0x8C000000, 0x8D000000))
    finally:
        other.close()
    it = reader.frames()
    cur = [next(it, (None, None))]

    def at(frame):
        while cur[0][0] is not None and int(reader.tag(cur[0][0])[0]) < frame:
            cur[0] = next(it, (None, None))
        i, ram = cur[0]
        return ram if i is not None and int(reader.tag(i)[0]) == frame else None
    return at, reader, layout


def first_divergence(ref_ram, port_ram, drive, ignore, ranges, names, span=None, start=0, ulps=0, close=None,
                     base=None, maps=None, noise_out=None):
    """The first paired frame where a word of span (by default the game's globals, as ram_compare) outside the noise
    and the ranges differs, or None; and the counts. Port frames before start (past a load, say) are skipped. With
    ulps, float_close words are left out and counted in close (a dict): never zero divergence, only a diagnostic.
    base (the TEMPORARY --baseline diagnostic): another build's port RAM on the same reference; a word that build
    also holds at that frame is left out, so only what this build does differently from it can diverge. maps:
    (the baseline's, this build's) host address -> symbol lookups; a host pointer both builds hold to the same
    symbol and offset (a function pointer the kit stores in a guest global) is the same value. noise_out (a dict):
    gets "words", the noise words, sorted."""
    noise, paired = None, 0
    lo, hi = span or ram_compare.region(None, names)
    base_at, base_reader, layout = baseline_frames(base, port_ram) if base else (None, None, set())
    if close is not None and base:
        close["baseline_layout_words"] = len(layout)
    with contextlib.closing(ram_compare.paired_frames(ref_ram, port_ram, drive)) as frames:
        for ptag, etag, pram, eram in frames:
            if int(ptag[0]) < start:
                continue
            words = {w for w in ram_delta.differing_words(pram, eram, lo, hi)
                     if not any(lo <= w < hi for lo, hi, _ in ranges)}
            bram = base_at(int(ptag[0])) if base_at else None
            if bram is not None:
                words = {w for w in words if word(bram, w) != word(pram, w) and w not in layout
                         and not same_symbol(maps, word(bram, w), word(pram, w))}
            paired += 1
            if paired <= ignore:
                noise = words if noise is None else noise & words
                if noise_out is not None:
                    noise_out["words"] = sorted(noise)
                continue
            new = sorted(words - (noise or set()))
            near = [w for w in new if float_close(word(eram, w), word(pram, w), ulps)] if ulps else []
            if near:
                new = sorted(set(new) - set(near))
                close["frames"] = close.get("frames", 0) + 1
                close["words"] = close.get("words", 0) + len(near)
                close["max_ulps"] = max([close.get("max_ulps", 0)] + [abs(word(eram, w) - word(pram, w)) for w in near])
                close.setdefault("first", {"port_frame": int(ptag[0]), "vblank": int(etag[1]), "words": [
                    {"addr": f"{w:08x}", "name": ram_compare.name_of(names, w), "expected": f"{word(eram, w):08x}",
                     "actual": f"{word(pram, w):08x}"} for w in near[:8]]})
            if new:
                listed = [{"addr": f"{w:08x}", "name": ram_compare.name_of(names, w),
                           "expected": f"{word(eram, w):08x}", "actual": f"{word(pram, w):08x}"}
                          for w in new[:WORDS_KEPT]]
                if base_reader:
                    base_reader.close()
                return {"port_frame": int(ptag[0]), "ref_frame": int(etag[0]), "vblank": int(etag[1]),
                        "label": etag[2], "count": len(new), "words": listed}, paired, len(noise or ())
    if base_reader:
        base_reader.close()
    return None, paired, len(noise or ())


def noise_globals(noise, names, kept=WORDS_KEPT):
    """The noise words that are game globals (a game.h name): {"count", "words": [{"addr", "name"}]}, or None."""
    rows = [{"addr": f"{w:08x}", "name": ram_compare.name_of(names, w)} for w in noise]
    rows = [r for r in rows if r["name"]]
    return {"count": len(rows), "words": rows[:kept]} if rows else None


def run_port(game, ref, build_dir, out, seconds, extra):
    """The port driven by the reference's frames, in folder out (from a fresh copy of the reference's memory card, if
    it has one); (exit code, wall s, log, drive file)."""
    card = reference.manifest(ref).get("card")
    extra = list(extra) + (emu_card.port_args(card, out) if card else [])
    work = reference.unpack(ref)
    spec = ref / "lockstep.spec"
    emu = lockstep.read_frames(work / "emu.txt")
    frames = lockstep.build_frames(emu, lockstep.read_draws(lockstep.draws_path(work / "emu.txt")))
    drive = out / "drive.txt"
    lockstep.drive_file(lockstep.parse_spec(spec), emu, frames, drive)
    rc, wall, log = lockstep.port_run(game, build_dir, spec, drive, out / "trace.txt", seconds, extra, rdir=out)
    return rc, wall, log, drive


def watch_words(first, n=WATCHED):
    """The differing words to watch: the first of each differing global in address order (a wrong value spreads
    through neighbouring words of one global, so several of them name one writer), then the next words, up to n."""
    rows = first["words"]
    heads, seen = [], set()
    for w in rows:
        g = (w["name"] or w["addr"]).split("+")[0]
        if g not in seen:
            seen.add(g)
            heads.append(w["addr"])
    rest = [w["addr"] for w in rows if w["addr"] not in heads]
    return [int(x, 16) for x in (heads + rest)[:n]]


WATCH_FLAG = "--watch-ram-frame"  # runtime/ram_frames.c; the base's scene_dump.c owns --watch-ram (A@V1:V2)


def watch_args(targets, frame):
    """The port's arguments that watch the words in targets (up to four) through port frame `frame`."""
    return [WATCH_FLAG, ",".join(f"{t:08x}" for t in targets) + f"@{frame}"]


def armed(log, frame):
    """The port's log has the watch's arming line for that frame (lockstep_watch.c)."""
    log = pathlib.Path(log)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return f"lockstep watch: frame {frame} word" in text


def writer_of(hits, dispatch, decomp):
    """The writer behind the watched words: each word's last write in the frame is the one whose value the frame
    shows (an earlier one is overwritten), and the earliest of those to run is nearest the cause. Its function as a
    guest function (name, address, readable file), the word, the callers' too, and every write in the frame, in the
    order they ran."""
    def guest(fn):
        name = fn.split("+")[0]
        addr = dispatch.get(name)
        src = decomp / "readable" / "src" / f"{addr}.c" if addr and decomp else None
        return {"function": name, "addr": addr, "file": str(src) if src and src.exists() else None}
    if not hits:
        return None
    last = {x.get("word"): i for i, x in enumerate(hits)}
    h = hits[min(last.values())]
    return {**guest(h["in"]), "value": h["value"], "word": h.get("word"), "chain": [guest(c) for c in h["callers"]],
            "writes": [{"function": x["in"].split("+")[0], "value": x["value"], "word": x.get("word")} for x in hits]}


def catalog_functions(decomp):
    """[(start, end, address)] of the decomp catalog's functions, sorted."""
    path = pathlib.Path(decomp) / "state" / "catalog.json" if decomp else None
    if not path or not path.exists():
        return []
    funcs = json.loads(path.read_text(encoding="utf-8"))["functions"]
    return sorted((int(f["addr"], 16), int(f["addr"], 16) + int(f.get("bytes") or 2), f["addr"]) for f in funcs)


def function_at(funcs, pc):
    """The catalog address of the function holding guest pc (any SH-4 mirror of it), or None."""
    pc = pc & 0x1FFFFFFF | 0x80000000
    i = bisect.bisect_right(funcs, (pc, 1 << 32, "")) - 1
    return funcs[i][2] if i >= 0 and pc < funcs[i][1] else None


def baseline_maps(base_ram, game, build_dir):
    """The symbol lookups for --baseline: the baseline run's build (its divergence.json) and this build."""
    if not base_ram:
        return None
    run = json.loads((pathlib.Path(base_ram).resolve().parent / "divergence.json").read_text(encoding="utf-8"))
    build = pathlib.Path(game) / "build"
    return symbolizer(build / run["build_dir"] / "port.map"), symbolizer(build / build_dir / "port.map")


def result(a):
    ref = pathlib.Path(a.ref).resolve()
    m = reference.manifest(ref)
    game = pathlib.Path(m["game"])
    cfg = load_game(game)
    out = pathlib.Path(a.out or ref / f"divergence_{a.build_dir}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    seconds = a.seconds or int(m["vblanks"] / 60) + 10
    res = {"ref": str(ref), "scenario": m["name"], "build_dir": a.build_dir, "first": None, "watch": None,
           "writer": None, "times_s": {}}
    t0 = time.monotonic()
    raw = out / "ram.raw"
    rc, wall, log, drive = run_port(game, ref, a.build_dir, out, seconds, ["--ram-frames", str(raw)])
    res["port_exit"] = exit_line(log)
    if not raw.exists():
        res["error"] = f"the port wrote no RAM frames (exit {rc}: {res['port_exit']})"
        return res
    ram_delta.pack_raw(raw, out / "ram", m["keyframe"])
    t1 = time.monotonic()
    res["times_s"]["port"] = round(t1 - t0, 1)
    names = ram_compare.global_names(game, a.build_dir)
    ranges = ignore_ranges(game)
    close, noise_out = {}, {}
    first, paired, noise = first_divergence(ref / "ram", out / "ram", ram_compare.drive_labels(drive), a.ignore,
                                            ranges, names, start=a.start, ulps=a.float_ulps, close=close,
                                            base=pathlib.Path(a.baseline).resolve() if a.baseline else None,
                                            maps=baseline_maps(a.baseline, game, a.build_dir), noise_out=noise_out)
    res["noise_globals"] = noise_globals(noise_out.get("words", ()), names)
    span = ram_compare.region(None, names)
    res.update(first=first, paired=paired, noise_words=noise, region="{:08x}-{:08x}".format(*span), start=a.start,
               ignored=[f"{lo:08x}-{hi:08x} {why}" for lo, hi, why in ranges],
               float_close=dict(close, ulps=a.float_ulps) if a.float_ulps else None, baseline=a.baseline,
               baseline_layout_words=close.pop("baseline_layout_words", None))
    t2 = time.monotonic()
    res["times_s"]["compare"] = round(t2 - t1, 1)
    if first is None:
        return res
    targets = [int(x, 16) for x in a.word.split(",")][:WATCHED] if a.word else watch_words(first)
    wdir = out / "watch"
    wdir.mkdir(exist_ok=True)
    rc, wall, log, _ = run_port(game, ref, a.build_dir, wdir, seconds, watch_args(targets, first["port_frame"]))
    notes, hits = watch_hits(log, game / "build" / a.build_dir / "port.map", callers=6)
    res["watch"] = {"words": [f"{t:08x}" for t in targets], "frame": first["port_frame"], "writes": hits,
                    "notes": notes, "port_exit": exit_line(log)}
    if not armed(log, first["port_frame"]):
        res["watch"]["error"] = (f"the port never armed the watch ({WATCH_FLAG} unknown to this build? rebuild it); "
                                 "no writer named")
    decomp = pathlib.Path(cfg["decomp"]) if cfg.get("decomp") else None
    res["writer"] = writer_of(hits, dispatch_names(game, a.build_dir), decomp)
    target = int(res["writer"]["word"], 16) if res["writer"] and res["writer"]["word"] else targets[0]
    t3 = time.monotonic()
    res["times_s"]["watch"] = round(t3 - t2, 1)
    if not a.no_window:
        win = ref_fast.window(ref, first["vblank"], target, seconds=a.seconds or 900, out=out / "window")
        funcs = catalog_functions(decomp)
        win["functions"] = {pc: function_at(funcs, int(pc, 16)) for pc in win["writers"]}
        res["original"] = win
        res["times_s"]["window"] = round(time.monotonic() - t3, 1)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--build-dir", default="x86-agent")
    ap.add_argument("--out")
    ap.add_argument("--ignore", type=int, default=30, help="paired frames that set the always-different words")
    ap.add_argument("--from", dest="start", type=int, default=0, help="the first port frame compared (past a load)")
    ap.add_argument("--float-ulps", type=int, default=0, help="TEMPORARY diagnostic: leave out float words this close "
                    "(same sign and exponent), reported in their own section; never zero divergence")
    ap.add_argument("--baseline", help="TEMPORARY diagnostic: another build's port RAM (DIR/ram) on this reference; "
                    "the words it also holds are left out")
    ap.add_argument("--word", help="the words to watch (hex, comma-separated, up to 4; default: watch_words)")
    ap.add_argument("--seconds", type=int, help="game seconds the port runs (default: the reference's + 10)")
    ap.add_argument("--no-window", action="store_true", help="skip the original's side (an observed Flycast replay)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = result(a)
    path = pathlib.Path(a.json or pathlib.Path(a.out or pathlib.Path(a.ref) / f"divergence_{a.build_dir}")
                        / "divergence.json")
    path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    f = res["first"]
    if res.get("error"):
        print(f"divergence: {res['error']}")
        return 2
    near = (res.get("float_close") or {}).get("words")
    if f is None and near:
        c = res["float_close"]
        print(f"no divergence beyond the TEMPORARY float tolerance ({c['ulps']} ulps; not zero divergence): {near} "
              f"float-close words in {c['frames']} frames, up to {c['max_ulps']} ulps, first at port frame "
              f"{c['first']['port_frame']}")
        return 1
    hidden = res.get("noise_globals")
    if f is None and hidden:
        print(f"no divergence outside the noise, but {hidden['count']} noise words are game globals (they may hide "
              f"one; not zero divergence): " + ", ".join(f"{w['addr']} {w['name']}" for w in hidden["words"][:8]))
        return 1
    if f is None:
        print(f"no divergence: {res['paired']} paired frames ({res['noise_words']} words always different)")
        return 0
    w = res["writer"]
    print(f"FIRST DIVERGENCE: port frame {f['port_frame']}, reference frame {f['ref_frame']} (vblank {f['vblank']}, "
          f"label {f['label']}): {f['count']} words; {f['words'][0]['addr']} {f['words'][0]['name']} expected "
          f"{f['words'][0]['expected']} got {f['words'][0]['actual']}")
    print(f"  port writer: {w['function']} ({w['addr'] or 'not a guest function'}) to {w['word']} {w['file'] or ''}"
          if w else "  port writer: none in that frame (the port left the words alone, or they came from earlier)")
    o = res.get("original")
    if o:
        print(f"  original writers (observed replay from vblank {o['state_vb']}): " + (", ".join(
            f"{pc} in {o['functions'].get(pc) or '?'} x{n}" for pc, n in o["writers"].items()) or "none"))
    print(f"  {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
