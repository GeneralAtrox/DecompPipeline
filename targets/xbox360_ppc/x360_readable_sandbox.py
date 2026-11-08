"""The readable stage's agent sandbox on the Xbox 360: core/readable_sandbox builds it (lean headers, raw.c, TASK.md
from readable_task.md, check.py); these two hooks give it the Xenon's parts, as targets/gamecube_ppc's
ppc_readable_sandbox does for the GameCube. game.h declares the callees with their raw signatures, as
x360_readable.check_readable builds them; context.md maps the parameters by the Xenon placement
(x360_readable_abi.place).
"""
from core import paths, raw_consts, readable
from core.readable_context import global_hint

import x360_compare as cmp
import x360_readable
import x360_readable_abi as abi


def sandbox_decls(reg, addr, meta):
    """({addr: raw signature}, data function addrs): the function, its callees (64-bit integers:
    x360_readable_abi.register_sig) and the functions raw.c uses as data (one with no harness: a placeholder
    signature, x360_readable.data_decls)."""
    data, placeholders = x360_readable.data_decls(addr, meta)
    return placeholders | x360_readable.callee_sigs(addr, meta) | {addr: meta["signature"]}, data


def value_lines(reg, addr, cfg, raw_text, decls, ext):
    """context.md's addresses raw.c passes or stores as values (core/raw_consts: a sign-extended `0xffffffff...ull`
    too), each with the name readable C writes. Before, the Xbox 360 context listed only the globals raw.c reads or
    writes, and agents wrote these as numbers (RR3: 98 of the x360-ref sample's 119 lint lines, .rdata addresses)."""
    if not cfg or not raw_text:
        return []
    out = []
    for v in raw_consts.values(raw_text, len(cfg["binary_path"].read_bytes()), int(addr, 16)):
        a, res = f"{v:08x}", raw_consts.resolve(reg, ext, v)
        if res:
            out.append(f"- 0x{a}: {raw_consts.describe(res)}")
        elif a in decls or (paths.HARNESS / a / "meta.json").exists():
            name = readable.parse_proto(reg.proto(a, raw_sig=decls.get(a)))[1]
            out.append(f"- 0x{a}: the function `{name}` (use its name as the pointer: `(u32)(uintptr_t){name}`)")
        else:
            out.append(f"- 0x{a}: no name yet: say so in your final message (MISSING GLOBAL 0x{a})")
    return ["", "## Addresses raw.c passes or stores as values (use the name, never the number)", ""] + out if out else []


def context_md(reg, addr, meta, cfg=None, raw_text="", types_body=None, decls=None, ext=None, views=None):
    """context.md of one Xbox 360 function (core/readable_context.context_md's sections, Xenon placement)."""
    proto = reg.proto(addr, meta)
    ret, name, params = readable.parse_proto(proto)
    st = abi.place(reg, proto, meta)
    raw = {abi._meta_storage(p): p["name"] for p in meta["params"]}
    lines = [f"# {name} (0x{addr})", "", f"Prototype: `{proto}`", "", "## Parameters (raw.c name -> yours)", "",
             "Placed as the Xenon places your prototype: integers and pointers r3..r10 (64-bit), f32/f64 f1..f13 "
             "(counted apart), then the stack doublewords sN (r1+0x50+8N). A parameter keeps raw.c's register while "
             "it keeps raw.c's name (param_rN / param_fN name the register).", ""]
    for (t, n), s in zip(params, st):
        lines.append(f"- {raw.get(s, '(not used by raw.c)')} ({s}) -> `{t} {n}`")
    unused = [f"{n} ({s})" for s, n in raw.items() if s not in st]
    if unused:
        lines.append(f"- not placed by your prototype: {', '.join(unused)} (the check fails while raw.c reads them)")
    lines += ["", f"Return: raw `{meta['ret']}` -> `{ret}` (r3 is compared in all 64 bits: keep raw.c's return type)",
              "", f"r1 is 0x{cmp.SP:08x} at entry: raw.c's numbers just below it are this function's own frame (its "
              "locals), never globals. r13 is 0x7fff0000 (the kernel's per-thread page).", "", "## Calls in raw.c", ""]
    calls = x360_readable.callee_sigs(addr, meta)
    for c in meta["callees"]:
        lines.append(f"- tc_call(0x{c['addr']}) -> `{reg.proto(c['addr'], raw_sig=calls[c['addr']])}`"
                     f" (registers compared at the call: {', '.join(c['regs']) or 'none'})")
    for a in [a for a in calls if a not in {c["addr"] for c in meta["callees"]}]:
        lines.append(f"- tc_call(0x{a}) -> `{reg.proto(a, raw_sig=calls[a])}` (outside the prepared functions: its "
                     "arguments are compared as the original set them)")
    if not calls:
        lines.append("No direct calls (a call through a pointer is a call through a typed function pointer).")
    lines += ["", "## Globals raw.c uses", ""]
    lines += [f"- 0x{g}: {global_hint(reg, g, ext)}" for g in meta["globals"]] or ["None."]
    lines += value_lines(reg, addr, cfg, raw_text, decls or {}, ext)
    if meta.get("ppc_special"):
        lines += ["", "## Instructions with a ppc_ helper", "",
                  ", ".join(meta["ppc_special"]) + ": keep raw.c's ppc_ helper calls (rd_rt.h)."]
    if types_body is not None:
        lines += ["", "## Types and constants", ""]
        lines += ["```c", types_body, "```"] if types_body.strip() else ["None beyond rd_rt.h's basic types."]
    return "\n".join(lines) + "\n"
