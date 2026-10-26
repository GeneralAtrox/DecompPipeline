"""The readable stage's agent sandbox on the GameCube: core/readable_sandbox builds it (lean headers, raw.c, TASK.md
from readable_task.md, check.py); these two hooks give it the PowerPC's parts. game.h declares the callees with their
raw signatures, as ppc_readable.check_readable builds them; context.md maps the parameters by the EABI
(ppc_readable_abi.place) instead of the SH-4 convention.
"""
from core import paths, readable
from core.readable_context import global_hint

import ppc_compare as cmp
import ppc_readable
import ppc_readable_abi as abi


def sandbox_decls(reg, addr, meta):
    """({addr: raw signature}, data function addrs): the function, its callees and the functions raw.c uses as data."""
    sigs = {c["addr"]: c["signature"] for c in meta["callees"]} | {addr: meta["signature"]}
    return sigs, ppc_readable.data_functions(addr, meta)


def _storage(p):
    """A meta.json parameter's storage as place() spells it: r3..r10, f1..f8 or sN (the word at r1+8+4N)."""
    return p["storage"] if p["storage"] != "stack" else f"s{(p['stack_offset'] - 8) // 4}"


def context_md(reg, addr, meta, cfg=None, raw_text="", types_body=None, decls=None, ext=None, views=None):
    """context.md of one GameCube function (core/readable_context.context_md's sections, PowerPC placement)."""
    proto = reg.proto(addr, meta)
    _, name, params = readable.parse_proto(proto)
    st = abi.place(abi.kinds(reg, proto)[0], name)
    raw = {_storage(p): p["name"] for p in meta["params"]}
    lines = [f"# {name} (0x{addr})", "", f"Prototype: `{proto}`", "", "## Parameters (raw.c name -> yours)", "",
             "Placed as the PowerPC EABI places your prototype: integers and pointers r3..r10, f32/f64 f1..f8 "
             "(counted apart), a 64-bit integer in a register pair, then the stack words sN (r1+8+4N).", ""]
    for (t, n), s in zip(params, st):
        lines.append(f"- {' / '.join(raw.get(x, '(not used by raw.c)') for x in s.split(':'))} ({s}) -> `{t} {n}`")
    unused = [f"{n} ({s})" for s, n in raw.items() if s not in {x for y in st for x in y.split(":")}]
    if unused:
        lines.append(f"- not placed by your prototype: {', '.join(unused)} (the check fails while raw.c reads them)")
    lines += ["", f"Return: raw `{meta['ret']}` -> `{readable.parse_proto(proto)[0]}`",
              "", f"r1 is 0x{cmp.SP:08x} at entry: raw.c's numbers just below it are this function's own frame (its "
              "locals), never globals.", "", "## Calls in raw.c", ""]
    for c in meta["callees"]:
        lines.append(f"- tc_call(0x{c['addr']}) -> `{reg.proto(c['addr'], raw_sig=c['signature'])}`"
                     f" (registers compared at the call: {', '.join(c['regs']) or 'none'})")
    if not meta["callees"]:
        lines.append("No direct calls (a call through a pointer is a call through a typed function pointer).")
    lines += ["", "## Globals raw.c uses", ""]
    lines += [f"- 0x{g}: {global_hint(reg, g, ext)}" for g in meta["globals"]] or ["None."]
    if meta.get("ppc_special"):
        lines += ["", "## Instructions with a ppc_ helper", "",
                  ", ".join(meta["ppc_special"]) + ": keep raw.c's ppc_ helper calls (rd_rt.h)."]
    if types_body is not None:
        lines += ["", "## Types and constants", ""]
        lines += ["```c", types_body, "```"] if types_body.strip() else ["None beyond rd_rt.h's basic types."]
    return "\n".join(lines) + "\n"
