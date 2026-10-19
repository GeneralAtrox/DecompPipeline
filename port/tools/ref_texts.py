"""The text outputs of a reference recorded in segments (tools/reference.py record), joined into one file each.

Each segment after the first is a new Flycast run from the save state at the previous segment's last recorded frame,
and the previous run goes on a few frames past that state, so the texts overlap there. The join keeps the later run's
lines from where they begin:
- emu.txt ("label vblank words"): the earlier run's lines before the later run's first vertical blank;
- emu_draws.txt ("label frames .."): the earlier run's lines before its last one for the later run's first draw (the
  same frame counter and game time; the label may be a timer tick or so apart);
- hw.txt: the C and R records (an R with its L and T lines) by emulated tick, the earlier run's before the later run's
  first tick; S lines (queue statistics) from both.
"""
import pathlib

TEXTS = ("emu.txt", "emu_draws.txt", "hw.txt")


def _emu(prev, cur):
    first = int(cur[0].split()[1])
    return [line for line in prev if int(line.split()[1]) < first] + cur


def _same_draw(a, b):
    """The same frame's draw in two runs: the frame counter and game time equal, the label a few timer ticks apart
    (a run loaded from a save state reads the timer an instruction apart: reference.py)."""
    return a[1] == b[1] and a[5] == b[5] and abs(int(a[0], 16) - int(b[0], 16)) < 16


def _draws(prev, cur):
    first = cur[0].split()
    at = next((i for i in range(len(prev) - 1, -1, -1) if _same_draw(prev[i].split(), first)), None)
    if at is None:
        print(f"  emu_draws.txt: the next run's first draw {' '.join(first[:2])} is not in the one before; appended")
        at = len(prev)
    return prev[:at] + cur


def _records(lines):
    """(tick, lines): a C or R line starts a record and L and T lines join the one before; S lines stand alone."""
    out = []
    for line in lines:
        if line[:1] in ("C", "R"):
            out.append((int(line.split()[2]), [line]))
        elif line[:1] == "S" or not out:
            out.append((None, [line]))
        else:
            out[-1][1].append(line)
    return out


def _hw(prev, cur):
    ticks = [t for t, _ in _records(cur) if t is not None]
    if not ticks:
        return prev + cur
    first = min(ticks)
    return [line for t, lines in _records(prev) if t is None or t < first for line in lines] + cur


JOIN = {"emu.txt": _emu, "emu_draws.txt": _draws, "hw.txt": _hw}


def join(segments, dest):
    """Each text of the segment folders, in order, joined into dest/NAME; returns the lines dropped per join."""
    dropped = {}
    for name in TEXTS:
        parts = [pathlib.Path(s) / name for s in segments]
        if not all(p.exists() for p in parts):
            continue
        lines = parts[0].read_text(encoding="ascii").splitlines()
        dropped[name] = []
        for p in parts[1:]:
            cur = p.read_text(encoding="ascii").splitlines()
            if cur:
                n = len(lines) + len(cur)
                lines = JOIN[name](lines, cur)
                dropped[name].append(n - len(lines))
        (pathlib.Path(dest) / name).write_text("".join(f"{line}\n" for line in lines), encoding="ascii",
                                              newline="\n")
    return dropped
