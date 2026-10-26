"""The game frames of a lockstep recording (tools/lockstep.py): the emulator's vertical blank samples and its draws
(lockstep_emu.lua) made into the frames the port runs, each with the timer value its first read takes."""
import pathlib


def iter_frames(path):
    """(label, n, words) per line: n is the emulator's vertical blank, or the port's paired emulator frame; words are
    one string, split where used (a trace's split words take about seven times its size)."""
    with open(path, encoding="ascii", errors="replace") as f:
        for p in (line.split(None, 2) for line in f):
            if len(p) >= 2:
                yield int(p[0], 16), int(p[1]), p[2] if len(p) > 2 else ""


def read_frames(path):
    return list(iter_frames(path))


def draws_path(emu_path):
    return pathlib.Path(emu_path).with_name(pathlib.Path(emu_path).stem + "_draws.txt")


def read_draws(path):
    """[(label, frames, ms, msrem, step, gametime, paused)]: lockstep_emu.lua's draws, [] if there are none."""
    path = pathlib.Path(path)
    if not path.exists():
        return []
    rows = [line.split() for line in path.read_text(encoding="ascii", errors="replace").splitlines()]
    return [tuple(int(x, 16) for x in p) for p in rows if len(p) == 7]


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
    (lockstep_emu.lua), each draw is a frame (one that presented twice, once), and the frame counter between two
    draws counts the frames that drew nothing: they start at the vertical blank labels between that no draw has, or
    failing those at milliseconds shared out (fill). A vertical blank label just before a draw, with no frame between,
    is the start of a frame that loaded: its draw shows the clock after the load, and its first read within the frame
    jumps there (jump). Without draws, every vertical blank sample is a frame."""
    labels = [r[0] for r in emu]
    if not draws:
        return [{"label": lab, "jump": None, "draw": None} for lab in labels]
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
