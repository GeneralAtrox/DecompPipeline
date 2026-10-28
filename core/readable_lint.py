"""Rules for readable C (the raw rules of lint.py where they still apply, plus readability rules).

Readable code may call other original functions by name (fn_8c012345 or their registry name), so lint.py's R5
does not apply; everything else from lint.py does (one exported function, static helpers only, no globals,
no inline assembly, no lookup tables, ...). Added:
- RR1: the only include is "game.h".
- RR2: no raw guest-memory or call API (rd32, wr32, tc_call, ...): use pointers, structs and function calls.
- RR3: no guest RAM addresses as numbers (0x8c000000..0x8cffffff): use the named globals and functions. Nor a mirror
  form (target.json address_mirrors, 0x0c206130 for 0x8c206130) of a named function or global: the judge treats both
  forms as one address, so the name is safe (PS2 S20 stored `(T)(uintptr_t)0x0c16ca90u` callbacks and passed).
- RR4 (raw_name_lint, needs the registry): a named function called by its raw name fn_ADDR where the raw arguments do
  not map onto its prototype.
- RR5 (pointer_bytes_lint, needs raw.c and the image): the bytes stored at a pointer raw.c passes, written as a number
  in place of the pointer (batch 20).
- RR7 (void_result_lint, needs the registry): a function declared void, cast to a value-returning function type whose
  result is used. The original read a register the callee never wrote (what the caller left there, e.g. the callee's
  own address reused for a second `jsr`); the judge's mocked callee returns a random r0 unless an oracle hint pins it
  (`genret N 0xADDR`), and a native build gets garbage.
- RR8 (fused_lint, needs the image): the function's code has the target's fused multiply-add (fmac) and the file never
  calls the one-rounding function (sh4_fmac): `a*b + c` rounds twice, passes when the vectors cannot tell, and drifts
  the game (core/fused; `refine.py draft --fmac --adopt` replaces such a file with a machine file that fuses).
"""
import re

from . import lint
from .compare import RAM

RAW_API = ("rd8", "rd16", "rd32", "rdf32", "wr8", "wr16", "wr32", "wrf32", "tc_call", "tc_callf", "TcArgs",
           "RdProto", "rd_protos", "rd_nprotos")


def readable_lint(src, name, proto, named=None, mirror=None):
    """named(v): the core/raw_consts.resolve answer for a canonical address (int), else None; mirror: target.json
    address_mirrors. Both given: RR3 also refuses a mirror-form number that names a symbol."""
    code = lint.strip_comments(src)
    errors = []
    includes = [ln.strip() for ln in code.splitlines() if ln.strip().startswith("#")]
    if includes != ['#include "game.h"']:
        errors.append('RR1: the only preprocessor line must be #include "game.h"')
    # The raw rules, checked on the same text with the raw include, without R5 (calls by name are the point).
    raw = re.sub(r'#\s*include\s+"game\.h"', '#include "tc_rt.h"', src, count=1)
    meta = {"name": name, "signature": proto}
    errors += [e for e in lint.lint(raw, meta) if not e.startswith(("R5:", "R2:"))]
    for ident in set(re.findall(r"[A-Za-z_]\w*", code)):
        if ident in RAW_API or ident.startswith(("rd_", "rdg_", "tcrt_")):
            errors.append(f"RR2: '{ident}' is the raw runtime API; use pointers, structs and calls by name")
    for lit in re.findall(r"\b0[xX]([0-9a-fA-F]+)", code):
        v = int(lit, 16)
        if RAM[0] <= v < RAM[1]:
            errors.append(f"RR3: guest address 0x{v:08x} written as a number; name it in the registry")
        elif named and mirror and f"{v >> 24:02x}" in mirror:
            from .raw_consts import describe, fold
            res = named(fold(v, mirror))
            if res:
                errors.append(f"RR3: 0x{v:08x} is the mirror form of 0x{fold(v, mirror):08x}, {describe(res)}")
    return sorted(set(errors))


def pointer_bytes_lint(src, raw_text, image):
    """RR5: a number that is the bytes stored at an address raw.c passes or stores as a value (core/raw_consts
    pointer_bytes): the pointer is meant, by its name."""
    from .raw_consts import pointer_bytes
    by_word = {}
    for a, w in pointer_bytes(raw_text, lint.strip_comments(src), image):
        by_word.setdefault(w, []).append(f"0x{a:08x}")
    return [f"RR5: 0x{w:08x} is the 4 bytes stored at {' / '.join(at[:3])}{' / ...' if len(at) > 3 else ''}, an "
            "address raw.c passes as a value (a pointer): use the name context.md gives for that address, not the "
            "bytes at it" for w, at in sorted(by_word.items())]


# `(RET (*)(PARAMS)) NAME`: a function name cast to a function pointer type (RR7)
FN_CAST = re.compile(r"\(\s*(?P<ret>[A-Za-z_][\w\s]*?\**)\s*\(\s*\*\s*\)\s*\([^()]*\)\s*\)\s*(?P<name>[A-Za-z_]\w*)\b")


def _result_discarded(code, start, end):
    """Whether the cast at code[start:end] is called as a statement of its own (`((T (*)(..))F)(..);` or
    `(void)((T (*)(..))F)(..);`), so nothing reads what it returns."""
    i = start - 1
    while i >= 0 and code[i].isspace():
        i -= 1
    j = end
    while j < len(code) and code[j].isspace():
        j += 1
    if i < 0 or code[i] != "(" or code[j:j + 1] != ")":
        return False  # not wrapped and called: the cast value itself is used (stored, passed, returned)
    k = j + 1
    while k < len(code) and code[k].isspace():
        k += 1
    if code[k:k + 1] != "(":
        return False
    before = code[:i].rstrip()
    return not before or before[-1] in ";{}" or re.search(r"(\(\s*void\s*\)|\belse|\bdo)$", before) is not None


def void_result_lint(src, reg):
    """RR7: a function the registry declares void, cast to a value-returning function type whose result is used (or
    the cast pointer kept). The original then reads a register the callee did not set: what is left there is the
    caller's own value (an address it loaded, an argument); the judge's mock returns a recorded word instead. Use what
    the register really holds: call that function by its name, or pass the caller's own value; when the callee
    really returns a value, its names entry says so (designer: prototype, raw_ret)."""
    from .readable import parse_proto
    void = set()
    for e in reg.functions.values():
        try:
            ret, fname, _ = parse_proto(e["proto"])
        except Exception:  # a bad prototype is the registry's error, reported elsewhere
            continue
        if ret == "void":
            void.add(fname)
    code = lint.strip_comments(src)
    errors = []
    for m in FN_CAST.finditer(code):
        ret = re.sub(r"\b(const|volatile)\b", "", m.group("ret")).strip()
        if m.group("name") in void and ret != "void" and not _result_discarded(code, m.start(), m.end()):
            errors.append(f"RR7: {m.group('name')} is declared void, yet its call is cast to return `{ret}` and that "
                          "value is used: a void callee leaves nothing in the result register, only what the caller "
                          "loaded there before the call (often the callee's own address). Call the function that "
                          "register really holds by its name (or use the caller's own value) instead of a value "
                          "carried through the call")
    return sorted(set(errors))


def fused_lint(cfg, meta, src):
    """RR8: the code's fmac written as `a*b + c` (core/fused)."""
    from . import fused
    return fused.lint(cfg, meta, src, fused.READABLE)


def raw_name_lint(src, reg, allowed):
    """RR4: a raw name fn_ADDR of a function the registry has named, where game.h cannot keep that name (allowed: the
    addresses whose raw name still fits, readable_abi.raw_alias_ok): call it by its name and prototype."""
    from .readable import parse_proto
    errors = []
    for raw in sorted(set(re.findall(r"\bfn_([0-9a-f]{8})\b", lint.strip_comments(src)))):
        e = reg.functions.get(raw)
        if e and raw not in allowed and parse_proto(e["proto"])[1] != f"fn_{raw}":
            errors.append(f"RR4: fn_{raw} is named now; call it by its name with its prototype's arguments: "
                          f"`{e['proto']}` (its raw signature's arguments do not map onto that prototype)")
    return errors
