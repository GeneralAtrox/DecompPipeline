"""The target's fused multiply-add (rtx SPEC.fused: its opcode and the one-rounding runtime function), and the other
instructions it rounds once (SPEC.once: the SH-4's fipr and ftrv, one inner product each), for the lint rules and
`refine.py draft --fmac` (core/draft_run).

C that writes the CPU's fmac as `a*b + c` rounds twice. It passes the judge whenever the vectors never hit a case
where the two roundings differ, yet drifts the game (TC h5n1 camera, Spline_MeasureLength: fmac-probe 2026-10-26).
So a file whose function's code has the fused opcode and which never calls the one-rounding function fails lint:
R9/fmac (raw C, core/check.run_check) and RR8 (readable C, core/readable_check). The rules only add a failure: the
fix they ask for (sh4_fmac) is always the CPU's own result. A file that fuses some and not others is left to the
judge (a helper or a loop changes the count). A target without SPEC.fused is unaffected.
fipr and ftrv the same way (fipr-once 2026-10-27: TC's Vec3_LengthSq written `x*x + y*y + z*z` rounds seven times
where the SH-4 and Flycast round once, and drifted mission1): code with fipr must call sh4_fipr (or sh4_fipr_fv), code
with ftrv sh4_ftrv (or sh4_ftrv_fv). Same rule names: they are the same rule, one rounding where the CPU has one.
Nearest existing: core/draft_run (fused_ops, fused_gap) - moved here, not copied: the lint rules need the count
without draft_run's readable machinery (it imports refine and readable_check, which runs the readable lint).
"""
import re

from .compare import IMAGE_BASE, WORD

RAW = "R9/fmac"  # the raw rule's prefix: refine.harness_stale tells it from a stale harness
READABLE = "RR8"
_IMAGES = {}
ADVICE = {"fmac": "The original's fmac rounds once; `a*b + c` rounds twice and drifts the game where the vectors "
                  "cannot tell. Write each fmac as {0}(fr0, frm, frn) (= fr0 * frm + frn, rounded once)",
          "fipr": "The original's fipr is one inner product rounded once; float multiplies and adds round seven "
                  "times and drift the game where the vectors cannot tell. Write each fipr as {0}(a, b) "
                  "(= a[0]*b[0] + .. + a[3]*b[3], rounded once; {1}(m, n) when the whole FPU is state)",
          "ftrv": "The original's ftrv is four inner products, each rounded once; float multiplies and adds round "
                  "seven times per element and drift the game where the vectors cannot tell. Write each ftrv as "
                  "{0}(v) (v = XMTRX * v; {1}(n) when the whole FPU is state)"}


def _spec():
    from .rtx import source
    sp = source.spec()
    return sp.fused if sp is not None and getattr(sp, "fused", None) else None


def _entries():
    """((opcode mask, value), (runtime calls), name) of every instruction the target rounds once, fmac first."""
    from .rtx import source
    sp, fused = source.spec(), _spec()
    return ([(fused[0], (fused[1],), "fmac")] if fused else []) + list(getattr(sp, "once", None) or ())


def _counts(cfg, meta, entries):
    """How many of each entry's instructions the function's code has: its meta blocks' words."""
    path = cfg["binary_path"]
    if path not in _IMAGES:
        _IMAGES[path] = path.read_bytes()
    image, order = _IMAGES[path], "big" if WORD == ">I" else "little"
    n = [0] * len(entries)
    for lo, hi in (meta or {}).get("blocks") or []:
        a, b = int(lo, 16) - IMAGE_BASE, int(hi, 16) + 1 - IMAGE_BASE
        for off in range(max(a, 0) & ~1, min(b, len(image) - 1), 2):
            w = int.from_bytes(image[off:off + 2], order)
            for i, ((m, v), _, _) in enumerate(entries):
                n[i] += w & m == v
    return n


def ops(cfg, meta):
    """How many of the target's fused multiply-adds the function's code has: its meta blocks' words. 0 for a target
    without one."""
    fused = _spec()
    return _counts(cfg, meta, [(fused[0], (fused[1],), "fmac")])[0] if fused else 0


def gaps(cfg, meta, text):
    """[(entry, "N fipr in the code, no sh4_fipr or sh4_fipr_fv in the file"), ...]: each instruction rounded once
    that the code has while text (comments removed) never calls one of its runtime functions."""
    from .lint import strip_comments
    entries = _entries()
    if not entries:
        return []
    out, src = [], None
    for e, n in zip(entries, _counts(cfg, meta, entries)):
        if not n:
            continue
        src = strip_comments(text) if src is None else src
        if not any(re.search(rf"\b{c}\s*\(", src) for c in e[1]):
            out.append((e, f"{n} {e[2]} in the code, no {' or '.join(e[1])} in the file"))
    return out


def gap(cfg, meta, text):
    """"N fmac in the code, no sh4_fmac in the file" (fipr and ftrv the same, joined by "; ") when the code has an
    instruction rounded once and text (comments removed) never calls its runtime function; else None."""
    return "; ".join(why for _, why in gaps(cfg, meta, text)) or None


def lint(cfg, meta, src, rule):
    """The rule's errors for src (rule: RAW or READABLE), one per instruction, as a list."""
    return [f"{rule}: {why}. " + ADVICE[e[2]].format(*e[1], *e[1]) for e, why in gaps(cfg, meta, src)]
