"""The game frames of a lockstep recording (tools/lockstep.py): the emulator's vertical blank samples and its draws
(lockstep_emu.lua) made into the frames the port runs, each with the timer value its first read takes."""
import bisect
import pathlib
import re
import struct


def iter_frames(path):
    """(label, n, words) per line: n is the emulator's vertical blank, or the port's paired emulator frame; words are
    one string, split where used (a trace's split words take about seven times its size)."""
    with open(path, encoding="ascii", errors="replace") as f:
        for p in (line.split(None, 2) for line in f):
            if len(p) >= 2:
                yield int(p[0], 16), int(p[1]), p[2] if len(p) > 2 else ""


def check_gates(items, path):
    """The spec's items; an error unless each flag if=ITEM:VALUE (if=ITEM[J]:VALUE, word J; VALUE hex) names an
    earlier item's word. Such an item is read only while that word holds VALUE, else "-" on both sides
    (runtime/lockstep_spec.c, lockstep_emu.lua): the compare needs nothing more."""
    for n, it in enumerate(items):
        for g in (x[3:] for x in it["flags"] if x.startswith("if=")):
            m = re.fullmatch(r"([^:\[]+)(?:\[(\d+)\])?:([0-9a-fA-F]+)", g)
            if not m or not any(e["name"] == m[1] and int(m[2] or 0) < e["words"] for e in items[:n]):
                raise SystemExit(f"{path}: {it['name']} if={g} names no earlier item's word")
    return items


def read_frames(path):
    return list(iter_frames(path))


def draws_path(emu_path):
    return pathlib.Path(emu_path).with_name(pathlib.Path(emu_path).stem + "_draws.txt")


def steps_path(emu_path):
    return pathlib.Path(emu_path).with_name(pathlib.Path(emu_path).stem + "_steps.txt")


def read_marks(emu_path):
    """The marks build_frames takes: the steps (lockstep_emu.lua: each frame at its sample PC, under its own label,
    also several frames within one vertical blank) when the recording has them, else the draws."""
    return read_draws(steps_path(emu_path)) or read_draws(draws_path(emu_path))


def read_draws(path):
    """[(label, frames, ms, msrem, step, gametime, paused[, scene[, unsampled]])]: lockstep_emu.lua's draws (or steps:
    scene; unsampled: a frame that began a scene but reached no sample PC, its words read at the scene), [] if none."""
    path = pathlib.Path(path)
    if not path.exists():
        return []
    rows = [line.split() for line in path.read_text(encoding="ascii", errors="replace").splitlines()]
    return [tuple(int(x, 16) for x in p) for p in rows if len(p) in (7, 8, 9)]


def scene_frames(emu, steps):
    """build_frames over steps that know each frame's scene: the frames the port counts (runtime/lockstep.c
    frame_read). A frame ends where the game's frame counter has moved since the last end, by more than one or with a
    scene begun: a frame that counted itself and began no scene (a mission start's fade-out) runs on into the next,
    whose label it takes (the port's reads within a frame step the timer by one: the run-on read is the one that
    counts, as the draws showed it), and so do the labels before a step that reached no sample PC (a load): such a
    frame starts at its first label and pairs with its last step's sample (jump). A load can come after a run-on
    step too (the counter reset a step earlier): the frame then starts at the load's label. A load that began a scene
    (an unsampled step: a movie's first scene as it starts) ends its frame where that scene does (frame_read: the
    counter moved by one with a scene begun); within the frame it stays a load's label."""
    labels, stepped = [r[0] for r in emu], {d[0] for d in steps if len(d) < 9}
    undrawn = sorted({lab for lab in labels if lab not in stepped})
    frames = [{"label": lab, "jump": None, "draw": None} for lab in undrawn if lab < steps[0][0]]
    load, running, seen, scenes, end = None, False, None, 0, steps[0][0] - 1
    for d in steps:
        if not running:
            load, running = None, True
        if load is None:  # at a label no sample PC saw (a load) if one comes before this step
            i = bisect.bisect_right(undrawn, end)
            load = undrawn[i] if i < len(undrawn) and undrawn[i] < d[0] else None
        scenes += d[7]
        if seen is not None and (d[1] == seen or (d[1] == (seen + 1) & 0xffffffff and not scenes)):
            if len(d) < 9:
                end = d[0]
            continue  # the port's next read is still this frame's
        if frames and frames[-1]["draw"] and frames[-1]["draw"][0] == d[0]:
            frames[-1]["draw"] = d  # the same label counted again: one frame
        else:
            frames.append({"label": d[0] if load is None else load, "jump": None if load is None else d[0], "draw": d})
        running, seen, scenes, end = False, d[1], 0, d[0]
    return frames + [{"label": lab, "jump": None, "draw": None} for lab in undrawn if lab > steps[-1][0]]


def fill(prev, d, n):
    """Start labels for n frames that drew nothing between draws prev and d: whole milliseconds shared out up to the
    start of d's frame (its ms less its step), mid-millisecond, from prev's clock (label, ms, msrem)."""
    l0, m0, r0 = prev[0], prev[2], prev[3]
    end = d[2] - d[4] if 0 < d[4] <= d[2] - m0 else d[2]
    span = max(0, end - m0)
    marks = [m0 + span * (j + 1) // (n + (end == d[2])) for j in range(n)]
    at = lambda m: l0 + max(1, -((-(m - m0) * 781250 - 390625 + r0) // 1000))
    return [at(m) + j for j, m in enumerate(marks)]


def build_frames(emu, draws):
    """[{label, jump, draw}]: the game frames in order, each with the label its first timer read takes. With draws
    (lockstep_emu.lua's draws or steps: read_marks), each draw is a frame (one that presented twice, once), and the
    frame counter between two draws counts the frames that drew nothing: they start at the vertical blank labels
    between that no draw has, or failing those at milliseconds shared out (fill). A vertical blank label just before
    a draw, with no frame between, is the start of a frame that loaded: its draw shows the clock after the load, and
    its first read within the frame jumps there (jump). Without draws, every vertical blank sample is a frame. Steps
    that know each frame's scene: scene_frames."""
    labels = [r[0] for r in emu]
    if not draws:
        return [{"label": lab, "jump": None, "draw": None} for lab in labels]
    if len(draws[0]) >= 8:
        return scene_frames(emu, draws)
    merged = []  # [(first draw, last draw)] per label: a frame that presented twice counted itself twice
    for d in draws:
        if merged and merged[-1][1][0] == d[0]:
            merged[-1] = (merged[-1][0], d)
        else:
            merged.append((d, d))
    drawn = {d[0] for d in draws}
    undrawn = sorted({lab for lab in labels if lab not in drawn})
    frames = [{"label": lab, "jump": None, "draw": None} for lab in undrawn if lab < draws[0][0]]
    prev = None
    for first, d in merged:
        between = [lab for lab in undrawn if prev and prev[0] < lab < d[0]]
        n = 0
        if prev:
            n = first[1] - prev[1] - 1 if first[1] >= prev[1] else max(first[1], 1) - 1
            if n > 0 and not prev[6] and not d[6] and d[2] - prev[2] == d[4] > 0:
                n = 0  # one frame took all the time: it counted twice
            if n > len(between) and d[4] == 0:
                n = len(between)  # d's frame stepped no time, so none to share out: it presented before it drew
        starts = between[:n]
        starts += fill(prev, d, n - len(starts)) if n > len(starts) else []
        frames += [{"label": lab & 0xffffffff, "jump": None, "draw": None} for lab in sorted(starts)]
        rest = between[n:]
        frames.append({"label": rest[0] if rest else d[0], "jump": d[0] if rest else None, "draw": d})
        prev = d
    frames += [{"label": lab, "jump": None, "draw": None} for lab in undrawn if lab > draws[-1][0]]
    return frames


def sample_of(emu, frames):
    """{frame index: emulator sample index}: the sample whose label is the frame's (its start or its jump)."""
    at = {}
    for n, fr in enumerate(frames):
        at[fr["label"]] = n
        if fr["jump"] is not None:
            at[fr["jump"]] = n
    return {at[r[0]]: i for i, r in enumerate(emu) if r[0] in at}


def layout(items, port):
    """[(item, first word index)] of the items one side writes."""
    out, at = [], 0
    for it in items:
        if port and "emu" in it["flags"]:
            continue
        out.append((it, at))
        at += it["words"]
    return out


def word(w):
    return None if w == "-" else int(w, 16)


def drive_file(items, emu, frames, out):
    """The port's --lockstep file: "label on r l x y rtc jump key..", one game frame per line (build_frames; jump 0:
    none). Pad, RTC and key: from the frame's sample (a loading frame's jump's, as compare pairs it), or the next."""
    lay = layout(items, False)
    pad = next(((it, at) for it, at in lay if "pad" in it["flags"]), None)
    rtc = next(((it, at) for it, at in lay if "rtc" in it["flags"]), None)
    keys = [(it, at) for it, at in lay if "key" in it["flags"]]
    if pad is None or rtc is None:
        raise SystemExit("the spec needs a pad item and an rtc item")
    labels = [r[0] for r in emu]
    sampled = set(labels)
    with open(out, "w", encoding="ascii") as f:
        for fr in frames:
            label, jump = fr["label"], fr["jump"]
            end = jump if jump in sampled else label
            w = emu[min(bisect.bisect_left(labels, end), len(emu) - 1)][2].split()
            rec = [word(x) or 0 for x in w[pad[1]:pad[1] + pad[0]["words"]]]
            on, rl, xy = rec[2], rec[6], rec[7]
            x, y = struct.unpack("<hh", struct.pack("<I", xy))
            hi, lo = (word(w[rtc[1] + j]) or 0 for j in range(2))
            key = [w[at + j] if w[at + j] != "-" else "ffffffff" for it, at in keys for j in range(it["words"])]
            clamp = lambda v: max(-128, min(127, v)) & 0xff
            rtc_word = (hi & 0xffff) << 16 | (lo & 0xffff)
            f.write("%08x %x %x %x %x %x %x %x %s\n" % (label, on, rl & 0xff, (rl >> 16) & 0xff, clamp(x), clamp(y),
                                                          rtc_word, jump or 0, " ".join(key)))
