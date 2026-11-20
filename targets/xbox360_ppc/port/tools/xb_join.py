"""Catalogue functions cut in two, joined again for the port. The catalogue (pdata and Ghidra) sometimes ends a function in the middle of
its own loop and starts the next entry there: Too Human's 82cb0098 ends at 82cb0260, inside the vector loop 82cb0230..82cb027c, which
runs on into "82cb0260" and branches back. Translated apart, the first piece's fall into the second became a tail call and the second's
branch back another, so the loop's condition field, its vector registers (v0, v12, v13) and r11 were lost at each cut: no hand-over
channel carries them (the hand-over check's fwd-nochannel and vec-unloaded rows on 82cb0260 and 82cb2d60).

A function F takes in the entry G that starts exactly at its end when F's last word falls into G (not a return, an unconditional branch
or a call: a call there is one that does not return), or when G branches back into F past F's entry and nothing calls G with bl. Never
when either piece calls into the other with bl (the translator refuses a call into its own body). Repeated, so a chain joins whole. G
keeps its own translation as well, for a pointer that may name it.

Nearest existing: gen_xbox.Kit.inside - it takes a catalogue body from a call target inside it; this lengthens one.
"""
import collections


def _word(image, base, a):
    o = a - base
    return int.from_bytes(image[o:o + 4], "big") if 0 <= o and o + 4 <= len(image) else 0


def _branches(image, base, lo, hi):
    """(at, target, is_call) of every relative b / bc / bl in [lo, hi)."""
    for a in range(lo, hi, 4):
        w = _word(image, base, a)
        op = w >> 26
        if w & 2 or op not in (16, 18):
            continue
        d = (w & 0x03FFFFFC) - ((w & 0x02000000) << 1) if op == 18 else (w & 0xFFFC) - ((w & 0x8000) << 1)
        yield a, (a + d) & 0xFFFFFFFF, bool(w & 1)


def falls_through(image, base, end):
    """True when the word before end lets control run on into end."""
    w = _word(image, base, end - 4)
    op = w >> 26
    if w == 0 or op == 18:                                           # padding; b / bl (a call there does not return)
        return False
    if op == 16 and (w >> 21) & 0x14 == 0x14 and not w & 1:          # bc always (BO 1z1zz)
        return False
    if op == 19 and ((w >> 1) & 0x3FF) in (16, 528) and (w >> 21) & 0x14 == 0x14 and not w & 1:   # blr / bctr always
        return False
    return True


def join(bodies, image, base):
    """{F: (lo, new_end)} for each catalogue function to lengthen; bodies is {addr: [(lo, hi)]} (only single-range bodies are joined)."""
    span = {a: b[0] for a, b in bodies.items() if len(b) == 1}
    starts = sorted(span)
    bl_in = collections.Counter()
    for a in starts:
        for _, t, call in _branches(image, base, *span[a]):
            if call:
                bl_in[t] += 1
    out = {}
    done = set()
    for f in starts:
        if f in done:
            continue
        lo, hi = span[f]
        while hi in span and hi != lo:
            g0, g1 = span[hi]
            if g1 <= g0:
                break
            g_br = list(_branches(image, base, g0, g1))
            f_br = list(_branches(image, base, lo, hi))
            if any(c and g0 <= t < g1 for _, t, c in f_br) or any(c and lo <= t < hi for _, t, c in g_br):
                break
            back = any(not c and lo < t < hi for _, t, c in g_br)
            if not (falls_through(image, base, hi) or (back and not bl_in[g0])):
                break
            done.add(g0)
            hi = g1
        if hi != span[f][1]:
            out[f] = (lo, hi)
    return out
