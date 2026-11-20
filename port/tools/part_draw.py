"""The game's per-part 3D draw, wrapped (game.json part_draw, gen_port.py via wide_cull.write): PartDraw_ADDR.

In-between frames built from game state need to know which object part wrote which vertices, and with which world
matrix. The game names its per-part draw, whose first two arguments are the object and the part, and where in them the
object's stamp (an s16 renewed when its pool slot is taken or released) and the part's world matrix (16 floats) are:

    "part_draw": {"function": "<its 8-digit address>", "stamp": "0x24", "world": "0x28"}

"function" may be a list of addresses (a game with more than one per-part draw, such as separate lit and prelit mesh draws).
A game whose draw takes the part alone (its first argument, no object around it) says "part": "object": the part is its
own object then, the stamp is read from it too, and the second argument may be anything.

The TA lists' write positions before and after each call come from the SDK (pvr_parts_lists), unless the game keeps its
own live ones and hands them to the SDK only at the end of a pass (the active list's in r14, the others
in a table of five words, and a word pointing at the active list's entry). Such a game names them:

    "lists": {"table": "<8-digit address>", "current": "<8-digit address>", "register": "r14", "offsets": ["0x0", "0xc", "0x4", "0x10", "0x8"]}

"offsets" are each TA list's entry in the table, in the TA's order (opaque, opaque modifier, translucent, translucent
modifier, punch-through). The wrapper reads them; nothing is written.

The wrapper calls port_part_begin (pvr/pvr_parts.c) before the function and port_part_end after, and gen_port wraps the
address as game.json wrap would. It reads guest RAM only; the call itself is unchanged.

A function the game also names in game.json wrap keeps the game's wrap: the part wrapper takes the address and calls
the game's wrap between begin and end, so the part holds whatever the wrap draws (a boss copy that relights
MeshNode_Draw). One in hardware or wide_cull is refused: neither can be put inside the part without changing what it
does.

Nearest existing: wide_cull.py - the same kind of generated wrapper, for the viewport; one more key would pass its job.
"""
import re

KEYS = {"function", "stamp", "world"}
OPTIONAL = {"part", "lists"}
LISTS = 5  # the TA lists, PVRX order


def config(raw, wrap, hardware, wide_cull, error):
    """game.json part_draw checked: {"function": addr or [addr, ...], "stamp": offset, "world": offset[, "part": "object"]},
    or None."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not KEYS <= set(raw) <= KEYS | OPTIONAL or not all(isinstance(raw[k], str) for k in KEYS - {"function"}):
        raise error('game.json part_draw: {"function": "8-digit address" or a list of them, "stamp": "0x..", "world": "0x.."'
                    '[, "part": "object"]}')
    fns = raw["function"] if isinstance(raw["function"], list) else [raw["function"]]
    if not fns or not all(isinstance(f, str) for f in fns):
        raise error("game.json part_draw.function: an 8-digit hex address or a list of them")
    fns = [f.lower() for f in fns]
    for fn in fns:
        if not re.fullmatch(r"[0-9a-f]{8}", fn):
            raise error(f"game.json part_draw.function: an 8-digit hex address, not {fn!r}")
        if fn in hardware or fn in ((wide_cull or {}).get("functions") or []):  # in wrap: composed (c_text)
            raise error(f"game.json part_draw {fn}: also in hardware or wide_cull")
    if len(set(fns)) != len(fns):
        raise error("game.json part_draw.function: an address named twice")
    if "part" in raw and raw["part"] != "object":
        raise error(f'game.json part_draw.part: "object" (the draw takes the part alone), not {raw["part"]!r}')
    lists = lists_config(raw["lists"], error) if "lists" in raw else None
    offsets = {}
    for k in ("stamp", "world"):
        if not re.fullmatch(r"0x[0-9a-fA-F]{1,4}", raw[k]):
            raise error(f"game.json part_draw.{k}: an offset like 0x28, not {raw[k]!r}")
        offsets[k] = int(raw[k], 16)
    out = {"function": fns if isinstance(raw["function"], list) else fns[0], **offsets}
    out = {**out, "part": "object"} if "part" in raw else out
    return {**out, "lists": lists} if lists else out


def lists_config(raw, error):
    """part_draw.lists checked: {"table": addr, "current": addr, "register": int 8..14, "offsets": [5 ints]}."""
    bad = error('game.json part_draw.lists: {"table": "8-digit address", "current": "8-digit address", "register": '
                '"r8".."r14", "offsets": [five "0x.." offsets, in the TA lists\' order]}')
    if not isinstance(raw, dict) or set(raw) != {"table", "current", "register", "offsets"}:
        raise bad
    if not all(isinstance(raw[k], str) and re.fullmatch(r"[0-9a-fA-F]{8}", raw[k]) for k in ("table", "current")):
        raise bad
    reg = re.fullmatch(r"r(8|9|1[0-4])", str(raw["register"]))
    offs = raw["offsets"]
    if not reg or not isinstance(offs, list) or len(offs) != LISTS or not all(
            isinstance(o, str) and re.fullmatch(r"0x[0-9a-fA-F]{1,3}", o) for o in offs):
        raise bad
    return {"table": int(raw["table"], 16), "current": int(raw["current"], 16), "register": int(reg.group(1)),
            "offsets": [int(o, 16) for o in offs]}


def lists_c(lists):
    """The C of part_lists(at): each TA list's live write position, read from the game's table and register."""
    offs = ", ".join(f"0x{o:x}u" for o in lists["offsets"])
    return ["", f"extern u32 port_sh4_gs[7]; /* r8..r14 (runtime/sh4_rt.c) */", "",
            "/* game.json part_draw.lists: the active list's position is in the register, the others in the table */",
            "static const u32 *part_lists(u32 *at)",
            "{", f"    static const u32 off[{LISTS}] = {{{offs}}};",
            f"    u32 cur = *(const u32 *)(uintptr_t)guest_fold(0x{lists['current']:08x}u) & 0x1fffffffu;",
            f"    for (u32 l = 0; l < {LISTS}u; l++) {{",
            f"        u32 entry = 0x{lists['table']:08x}u + off[l];",
            f"        at[l] = cur == (entry & 0x1fffffffu) ? port_sh4_gs[{lists['register'] - 8}] "
            "        : *(const u32 *)(uintptr_t)guest_fold(entry);".replace("        :", ":"),
            "    }", "    return at;", "}"]


def c_text(readable, part_draw, protos, wrapper_proto, wrap=None):
    """(the C of the wrappers, {addr: wrapper}) for game.json part_draw, or None. wrap: game.json wrap; a function in
    it is called through the game's wrap."""
    if not part_draw:
        return None
    alone = part_draw.get("part") == "object"
    fns = part_draw["function"] if isinstance(part_draw["function"], list) else [part_draw["function"]]
    lists = part_draw.get("lists")
    text = ["/* Generated by port/tools/gen_port.py from game.json part_draw: do not edit. */", '#include "game.h"',
            '#include "platform.h"'] + (['#include "port.h"'] + lists_c(lists) if lists else [])
    live = "part_lists(part_at)" if lists else "NULL"
    wraps = {}
    for x in fns:
        if x not in protos:
            raise SystemExit(f"game.json part_draw {x}: no function the port declares there")
        ret, name, params = readable.parse_proto(protos[x])
        pointers = 1 if alone else 2
        if readable.is_variadic(protos[x]) or len(params) < pointers or not all("*" in t for t, _ in params[:pointers]):
            raise SystemExit(f"game.json part_draw {x}: its first " + ("argument must be a pointer (the part)" if alone else
                             "two arguments must be pointers (the object and the part)"))
        wrapper = f"PartDraw_{x}"
        inner = (wrap or {}).get(x, name)  # the game's own wrap of this function, inside the part
        call = f"{inner}({', '.join(n for _, n in params)});"
        obj, part = params[0][1], params[0 if alone else 1][1]
        text += ["", wrapper_proto(protos[x], name, inner)] if inner != name else []
        text += ["", wrapper_proto(protos[x], name, wrapper)[:-1], "{"] + ([f"    u32 part_at[{LISTS}];"] if lists else [])
        text += [f"    port_part_begin({obj}, {part}, 0x{part_draw['stamp']:x}u, 0x{part_draw['world']:x}u, {live});"]
        text += [f"    {call}", f"    port_part_end({live});"] if ret.strip() == "void" else [
            f"    {ret} part_result = {call}", f"    port_part_end({live});", "    return part_result;"]
        text += ["}"]
        wraps[x] = wrapper
    return "\n".join(text + [""]), wraps


def write(out, readable, cfg, protos, wrapper_proto, write_if_changed, wrap, sources):
    """wrap and sources with the wrapper added: out/part_draw.c is written, or removed when none is asked."""
    got = c_text(readable, cfg.get("part_draw"), protos, wrapper_proto, wrap)
    if got:
        write_if_changed(out / "part_draw.c", got[0])
        return {**wrap, **got[1]}, sources + [out / "part_draw.c"]
    if (out / "part_draw.c").exists():
        (out / "part_draw.c").unlink()
    return wrap, sources
