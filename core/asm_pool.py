"""Sandbox asm.txt: the words a `mova` points at (batch 29).

The packet's listing annotates the literal-pool word of every `mov.l`/`mov.w @(disp,PC)` load, but not `mova`: that
puts the pool address in r0 and the next instructions read the words there (`fmov.s @r0,frN` reads an f32 constant).
The agent saw only the address and guessed the constant: TC 8c07f4b8 compares against the f32 at 0x8c07f658
(0.0001); all three Haiku attempts wrote 1e-5 and failed one hidden vector. TC packets have 1086 `mova` lines in 287
functions.

annotate() appends the first words at the target (hex, and the f32 reading where it is a plain number) to each
`mova` line of the sandbox copy whose target lies in the image outside the code ranges (a literal pool; a `mova` into
code, such as a jump table's base, is left alone). The packet itself is unchanged: it is an oracle input.
"""
import re
import struct

MOVA = re.compile(r"^(\s+[0-9a-f]{8}\s+_?mova\s+0x([0-9a-f]{8}),r0)\s*$")
WORDS = 2


def code_ranges(path):
    """[(lo, hi)] from a code_ranges.txt (one `lo hi` hex pair per line, hi exclusive); [] when missing."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [(int(t[0], 16), int(t[1], 16)) for t in (ln.split() for ln in text.splitlines()) if len(t) >= 2]


def _f32(word):
    """The f32 reading of a word when it is a plain number (+-0, or a finite magnitude of at least 1e-30), else None
    (a pointer such as 0x8c0635b8 reads as -1e-31)."""
    v = struct.unpack("<f", struct.pack("<I", word))[0]
    if word & 0x7FFFFFFF == 0:
        return "-0.0" if word else "0.0"
    if v != v or abs(v) == float("inf") or abs(v) < 1e-30:
        return None
    for digits in range(1, 10):  # the shortest decimal that gives the same f32 (0.0001, not 9.99999975e-05)
        text = f"{v:.{digits}g}"
        if abs(float(text)) <= 3.4028234663852886e38 and struct.unpack("<I", struct.pack("<f", float(text)))[0] == word:
            return text
    return f"{v:.9g}"


def describe(target, image, base, ranges):
    """The annotation for a mova to target, or None (in code, or outside the image)."""
    if any(lo <= target < hi for lo, hi in ranges):
        return None
    off = target - base
    if off < 0 or off + 4 > len(image):
        return None
    words = []
    for k in range(WORDS):
        if off + 4 * k + 4 > len(image):
            break
        w = struct.unpack_from("<I", image, off + 4 * k)[0]
        f = _f32(w)
        words.append(f"0x{w:08x}" + (f" (f32 {f})" if f else ""))
    return f"; r0 = 0x{target:08x} (literal pool): words " + ", ".join(words)


def annotate(text, image, base, ranges):
    """asm.txt text with each pool `mova` annotated (lines that already carry a comment are kept as they are)."""
    width = next((ln.index(";") for ln in text.splitlines() if re.match(r"^\s+[0-9a-f]{8}\s+\S.*;", ln)), 49)
    out = []
    for line in text.splitlines(keepends=True):
        m = MOVA.match(line.rstrip("\r\n"))
        note = describe(int(m.group(2), 16), image, base, ranges) if m else None
        if note:
            line = m.group(1).ljust(width) + note + "\n"
        out.append(line)
    return "".join(out)
