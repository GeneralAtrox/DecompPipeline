"""The port's RAM against a reference's, frame by frame (tools/reference.py port and ram), and the words that differ
named by the game's globals (the build's generated game.h, where each global is defined at its guest address).

Pairing: the port records, with each frame, the emulator frame it was driven by (its line in the --lockstep drive
file, tools/lockstep.py); that line's label is the label the reference's RAM holds as the next frame starts (its tag).
Without the drive file, the port's own label is used (an undriven port's).
"""
import bisect
import pathlib
import re

import ram_delta


def global_names(game, build_dir):
    """[(guest address, name)] of the game's globals, from the build's generated game.h."""
    h = pathlib.Path(game) / "build" / f"{build_dir}_gen" / "include" / "game.h"
    if not h.exists():
        return []
    pat = re.compile(r"#define (\w+) \(\*\(rdg_\w+_t \*\)0x([0-9a-f]{8})u\)")
    return sorted((int(m.group(2), 16), m.group(1)) for m in pat.finditer(h.read_text(encoding="utf-8")))


def name_of(names, addr, reach=0x4000):
    i = bisect.bisect_right(names, (addr + 1, "")) - 1
    if i < 0 or addr - names[i][0] >= reach:
        return ""
    return names[i][1] + (f"+{addr - names[i][0]:x}" if addr != names[i][0] else "")


def drive_labels(path):
    path = pathlib.Path(path)
    if not path.exists():
        return None
    return [line.split()[0] for line in path.read_text(encoding="ascii").splitlines() if line.strip()]


def paired_frames(ref_ram, port_ram, drive=None):
    """(port tag, reference tag, port RAM, reference RAM) for each port frame the reference has."""
    emu, prt = ram_delta.Reader(ref_ram), ram_delta.Reader(port_ram)
    where = {}  # label -> the reference frames with it (the game's timer restarts, so labels repeat)
    for i in range(len(emu)):
        where.setdefault(emu.tag(i)[2], []).append(i)
    eit = emu.frames()
    ei, eram = next(eit, (None, None))
    for pi, pram in prt.frames():
        _, at, label = prt.tag(pi)
        if drive and 0 <= int(at) < len(drive):
            label = drive[int(at)]
        j = next((i for i in where.get(label, ()) if i >= ei), None)
        if j is None:
            continue
        while ei < j:
            ei, eram = next(eit)
        yield prt.tag(pi), emu.tag(ei), pram, eram


def region(text, names):
    """(lo, hi): "all", "LO-HI" (hex), or by default the span of the game's globals (its static data)."""
    if text == "all" or (not text and not names):
        return 0x8C000000, 0x8D000000
    if text:
        lo, hi = (int(x, 16) for x in text.split("-"))
        return lo & ~3, hi & ~3
    return names[0][0] & ~0xFFF, min(0x8D000000, (names[-1][0] + 0x10000) & ~0xFFF)


def compare(ref_ram, port_ram, at=None, ignore=None, names=(), drive=None, span=None, start=0, limit=24):
    """Names the words that differ between the port's RAM and the reference's at paired frames (by frame label).
    Words that differ in every one of the first --ignore paired frames (memory the two sides use differently: the
    stack, the SDK's own state) are left out; frames before port frame `start` (--from: after a load, say) are
    skipped. Without --frame, the first frame where another word differs is reported; with it, that frame. Words
    marked * did not differ at the paired frame before."""
    noise, paired, first, before = None, 0, None, set()
    lo, hi = span or region(None, names)
    print(f"comparing {lo:08x}..{hi:08x}")
    for ptag, etag, pram, eram in paired_frames(ref_ram, port_ram, drive):
        if int(ptag[0]) < start:
            continue
        words = set(ram_delta.differing_words(pram, eram, lo, hi))
        paired += 1
        if paired <= (ignore or 0):
            noise = words if noise is None else noise & words
        elif (at is None and words - noise) or (at is not None and int(ptag[0]) == at):
            first = (ptag, etag, sorted(words - (noise or set())))
            break
        before = words
    print(f"{paired} port frames paired with the reference{' (stopped there)' if first else ''}; "
          f"{len(noise or ())} words left out as always different in the first {ignore or 0}")
    if first is None:
        print("no RAM difference" if at is None else f"port frame {at} not paired")
        return 0
    (pf, _, label), (ef, vb, _), words = first
    print(f"{'FIRST RAM DIFFERENCE' if at is None else 'RAM'}: port frame {pf}, reference frame {ef} (vblank {vb}, "
          f"label {label}): {len(words)} words, {len(set(words) - before)} new (*)")
    runs = []
    for w in words:
        if runs and w == runs[-1][1] + 4:
            runs[-1][1] = w
        else:
            runs.append([w, w])
    for lo, hi in runs[:limit]:
        new = "*" if any(w not in before for w in range(lo, hi + 4, 4)) else " "
        span = f"..{hi + 3:08x} ({(hi - lo) // 4 + 1} words)" if hi != lo else ""
        print(f"   {new}{lo:08x}{span} {name_of(names, lo)}")
    if len(runs) > limit:
        print(f"    ... {len(runs) - limit} more runs")
    return 1 if words else 0
