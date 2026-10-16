"""refine.py varargs: the variadic call convention, audited at every call site the harnesses record (batch 20).

The readable stage places a variadic callee's parameters by SHC's convention (readable_abi.storages: the last named
parameter and the variable arguments on the stack) and judges the stack words its names entry's "varargs" rule says
a call passes (readable_varargs). If that convention were wrong for a callee, every readable caller of it would fail
for reasons no agent can fix. The audit checks it against the original's own calls: for every function whose
harness calls a variadic callee, at each recorded call of each vector,
  - the registers the original's call record compares are the prototype's register parameters (no register argument
    the prototype leaves out, none it adds; core/readable_abi.callee_place);
  - "printf": the format word is a NUL-terminated string in the initial memory, and each %s / %c / %d word after it
    has a plausible value (%s: 0 or an address in RAM), so the words line up with the conversions; at most 8 words
    (the call record holds s0..s7);
  - "null": a zero word ends the list within s0..s7.
A format built at run time (in the stack, or written by the function) cannot be read: reported as "unread", not as
a mismatch. Findings are grouped per (caller, callee) with how many calls show each.
"""
from . import paths, readable, readable_abi
from .compare import IMAGE_BASE, STACK_LO, Memory, parse_results, parse_vectors
from .readable_varargs import PRINTF_SPEC, STACK_WORD, _read_cstr, variadic_info

RAM = (0x8C000000, 0x8D000000)


def is_ptr(v):
    return v == 0 or RAM[0] <= v < RAM[1] or RAM[0] & 0x1FFFFFFF <= v < RAM[1] & 0x1FFFFFFF


def check_printf(fmt, words, first):
    """(problems, %s words that are addresses, %s words that are not) of one printf call: fmt (bytes), words (s0..s7
    as passed), first: the first variable word. A %s word that is not an address is counted, not a problem: it is
    often a mocked call's result or an input word, whatever the vector gave (TC 8c0265a0 passes its param_1)."""
    out, j, yes, no = [], first, 0, 0
    for m in PRINTF_SPEC.finditer(fmt):
        conv = m.group(5)
        if conv == b"%":
            continue
        j += (m.group(2) == b"*") + (m.group(3) == b"*")
        if j >= 8:
            return out + ["more than 8 stack words (the call record holds s0..s7): the rest is not judged"], yes, no
        if conv == b"s" and words[j]:
            yes, no = (yes + 1, no) if is_ptr(words[j]) else (yes, no + 1)
        j += 2 if m.group(4) == b"ll" else 1
    return out, yes, no


def is_text(s):
    return not any(b < 0x20 and b not in (9, 10, 13) for b in s)  # SJIS text is fine; control bytes are not


def read_format(mems, exp, ptr):
    """The format at ptr: the image's own bytes first (a vector's random fill of the rodata around a string is not
    the program's format), then the vector's initial memory."""
    got = [_read_cstr(m, exp, ptr) for m in mems]
    return next((s for s in got if s is not None and is_text(s)), got[-1])


def check_call(info, mem, exp, rec, image_mem=None):
    """(problems, unread, %s words that are addresses, %s words that are not) of one recorded call of a variadic
    callee (info: readable_varargs.variadic_info; mem: the vector's initial memory; image_mem: the image alone)."""
    fmt_word, first, rule = info
    words = [rec[STACK_WORD + j] for j in range(8)]
    if rule == "null":
        if not any(words[j] == 0 for j in range(first, 8)):
            return [f"no zero word ends the list in s{first}..s7"], 0, 0, 0
        return [], 0, 0, 0
    if rule != "printf":
        return [], 0, 0, 0
    ptr = words[fmt_word]
    if ptr == 0 or not is_ptr(ptr):  # read first, unmapped memory reads as zeros: an empty "format"
        return [f"format word s{fmt_word} is 0x{ptr:08x}, not an address"], 0, 0, 0
    fmt = read_format([m for m in (image_mem, mem) if m is not None], exp, ptr)
    if fmt is None:
        if ptr >= STACK_LO or any(ptr <= a < ptr + 512 for a in exp["writes"]):
            return [], 1, 0, 0  # built at run time: not readable from the initial memory
        return [f"format word s{fmt_word} 0x{ptr:08x} is not a NUL-terminated string"], 0, 0, 0
    if not is_text(fmt):
        return [f"format word s{fmt_word} 0x{ptr:08x} does not read as text: {fmt[:24]!r}"], 0, 0, 0
    problems, yes, no = check_printf(fmt, words, first)
    return problems, 0, yes, no


def audit_function(reg, image, addr, var):
    """{callee addr: {"calls": n, "unread": n, "problems": {text: count}, "regs": [register problems]}} for one caller.
    var: {callee addr: (proto, rule)} of the variadic registry functions."""
    harness = paths.HARNESS / addr
    meta = paths.read_json(harness / "meta.json")
    if not meta:
        return {}
    from .readable_check import callee_storage
    out, infos = {}, {}
    for c in meta["callees"]:
        if c["addr"] not in var or c["addr"] == addr:
            continue
        row = out.setdefault(c["addr"], {"calls": 0, "unread": 0, "str_ok": 0, "str_other": 0, "problems": {},
                                         "regs": []})
        proto = var[c["addr"]][0]
        storage = callee_storage(harness, c, ())
        try:
            place = readable_abi.callee_place(reg, c, proto, storage)
            infos[int(c["addr"], 16)] = variadic_info(reg, c["addr"], place)
        except readable.RegistryError as e:
            row["regs"].append(str(e))
            continue
        regs = {r for r in c["regs"] if readable_abi.arg_storage(r) and not r.startswith("s")}
        named = {s for s in place if not s.startswith("stack+")}
        if regs - named:
            row["regs"].append(f"the original passes {', '.join(sorted(regs - named))}, which `{proto}` leaves out")
        if named - regs - {readable_abi.NONE}:
            row["regs"].append(f"`{proto}` takes {', '.join(sorted(named - regs))}, which the original's call record "
                               "does not compare")
    if not infos:
        return out
    vecs, exp = parse_vectors(harness / "vectors.txt"), parse_results(harness / "expected.txt")
    image_mem = Memory(image, {"patches": []})
    for vid, res in exp.items():
        if vid not in vecs:
            continue
        mem = Memory(image, vecs[vid])
        for rec in res.get("calls") or []:
            info = infos.get(rec[0])
            if info is None:
                continue
            row = out[f"{rec[0]:08x}"]
            row["calls"] += 1
            problems, unread, yes, no = check_call(info, mem, res, rec, image_mem)
            row["unread"] += unread
            row["str_ok"] += yes
            row["str_other"] += no
            for p in problems:
                row["problems"][p] = row["problems"].get(p, 0) + 1
    return out


def variadic_functions(reg):
    return {a: (e["proto"], e.get("varargs")) for a, e in reg.functions.items() if readable.is_variadic(e["proto"])}


def audit(reg=None, cfg=None):
    """{caller addr: audit_function(...)} over every harness that calls a variadic registry function."""
    reg = reg or readable.Registry()
    cfg = cfg or paths.config()
    image = cfg["binary_path"].read_bytes()
    var = variadic_functions(reg)
    out = {}
    for d in sorted(paths.HARNESS.iterdir()) if paths.HARNESS.exists() else ():
        meta = paths.read_json(d / "meta.json", {}) if (d / "meta.json").exists() else {}
        if any(c["addr"] in var for c in meta.get("callees") or ()):
            out[d.name] = audit_function(reg, image, d.name, var)
    return out


def report(result, var):
    """(text lines, number of call sites with a problem)."""
    lines, bad, calls = [], 0, 0
    for callee, (proto, rule) in sorted(var.items()):
        sites = {a: r[callee] for a, r in result.items() if callee in r}
        n = sum(s["calls"] for s in sites.values())
        calls += n
        lines.append(f"{callee} `{proto}` (varargs: {rule}): {len(sites)} caller(s), {n} recorded call(s), "
                     f"{sum(s['unread'] for s in sites.values())} with a run-time format; %s words: "
                     f"{sum(s['str_ok'] for s in sites.values())} addresses, "
                     f"{sum(s['str_other'] for s in sites.values())} other values (an input or a mocked result)")
        for a, s in sorted(sites.items()):
            if s["problems"] or s["regs"]:
                bad += 1
                lines.append(f"  {a}: " + "; ".join(s["regs"] + [f"{p} ({k}x)" for p, k in s["problems"].items()]))
    lines.append(f"variadic call sites with a problem: {bad} (of {sum(len(r) for r in result.values())}; "
                 f"{calls} recorded calls; image base 0x{IMAGE_BASE:08x})")
    return lines, bad
