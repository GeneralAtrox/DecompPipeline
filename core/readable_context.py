"""context.md of a readable sandbox: everything the agent needs to rewrite one function, in one file.

The parameter mapping, the calls and globals raw.c uses (each with its declaration), the literal-pool constants,
the registers outside the C convention, and the definitions of the types and constants those declarations use
(readable_lean), so an agent needs to open nothing else.
"""
import re
import struct

from . import check, lean_cap, raw_consts, readable, readable_stack
from .compare import RAM, WORD
from .readable_lean import own_pool

PART_NOTE = ("A big struct shows only the members raw.c reaches; `u8 _pad_0xOFF[0xLEN];` stands for the members not "
             "shown, so every offset is exact (never use a `_pad_` name). A struct marked \"members not shown\" is "
             "used here only through pointers. The check builds work.c with the full definitions.")


def f32_literal(bits):
    """The shortest decimal float literal MSVC reads back as exactly these bits."""
    v = struct.unpack("<f", struct.pack("<I", bits))[0]
    if v != v or v in (float("inf"), float("-inf")):
        return f"(not a finite float: bits 0x{bits:08x})"
    for digits in range(1, 18):
        text = repr(float(f"{v:.{digits}g}"))
        try:  # FLT_MAX at 8 digits (3.4028235e+38) is above FLT_MAX: struct refuses it, MSVC would round it
            back = struct.unpack("<I", struct.pack("<f", float(text)))[0]
        except OverflowError:
            continue
        if back == bits:
            return text + "f"
    return repr(v) + "f"


def mirror_map(mirror=None):
    """target.json address_mirrors of the workspace ({"0c": "8c"}), unless given."""
    if mirror is not None:
        return mirror
    from . import paths
    from .retire import mirrors
    return mirrors(paths.ROOT)


def pool_constants(cfg, meta, raw_text, mirror=None):
    """Addresses raw.c reads that are the function's literal pool (constants the compiler placed next to the code):
    not globals or callees, or a "global" between the function's start and the next function. Returns
    [(addr, u32 bits)]. A mirror-form address (address_mirrors: PS2 raw.c writes 0x0c206130) is its 0x8c address:
    before, only 0x8c words were read, so a mirror-form pointer got no name and agents wrote it as a number."""
    from .retire import canonical
    mirror = mirror_map(mirror)
    image = cfg["binary_path"].read_bytes()
    known = {canonical(a, mirror) for a in (set(meta["globals"]) - own_pool(meta))
             | {c["addr"] for c in meta["callees"]} | {meta["addr"]}}
    found = []
    for a in sorted({canonical(m.lower(), mirror) for m in re.findall(r"0x([0-9a-fA-F]{8})u?", raw_text)}):
        if not RAM[0] <= int(a, 16) < RAM[1]:
            continue
        off = int(a, 16) - check.IMAGE_BASE
        if a in known or not 0 <= off <= len(image) - 4:
            continue
        found.append((a, struct.unpack_from(WORD, image, off)[0]))
    return found


def global_hint(reg, addr, ext=None):
    """How context.md names an address raw.c uses. ext (core/global_extents): a named global's declared extent holds
    an address however far into it (PS2 8c16d9da: 0x8c535ea4 is g_fighters + 0x380c, inside the Fighter array);
    without ext, the nearest global below within 0x1000."""
    if addr in reg.functions:  # a function the oracle lists as data (PS2 8c17e960 stores 8c189540, an SDK entry)
        return raw_consts.describe(("function", addr, reg.functions[addr]["proto"]))
    a = int(addr, 16)
    for ga, e in sorted(reg.globals.items()):
        if int(ga, 16) == a:
            return f"`{e['decl']}`"
    from .global_extents import containing
    hit = containing(ext, a) if ext else None
    if hit and f"{hit[0]:08x}" in reg.globals:
        return f"inside `{reg.globals[f'{hit[0]:08x}']['decl']}` (at 0x{hit[0]:08x}, offset 0x{a - hit[0]:x})"
    near = [(int(ga, 16), e) for ga, e in reg.globals.items() if 0 < a - int(ga, 16) < 0x1000]
    if near:
        base, e = max(near, key=lambda x: x[0])
        return f"inside or after `{e['decl']}` (at 0x{base:08x}, offset 0x{a - base:x})"
    return "no name yet: say so in your final message (MISSING GLOBAL 0x" + addr + ")"


def split_pointers(reg, cfg, raw_text, consts, ext, mirror=None):
    """([(addr, how to name it)], the remaining pool constants). An address raw.c uses as a value (core/raw_consts:
    passed, stored, compared) is a pointer, not a literal-pool word to copy: before batch 20 context.md listed the
    bytes at a string's or a function's address as a constant to write (TC 8c0539bc stores Render_BeginFrame's
    address, 8c041146 passes World_GetSoundSample's). It names the function or global it resolves to; an unnamed
    data address is a missing name; an unnamed code-region address stays a constant (a pool word raw.c reads through
    a register). mirror: target.json address_mirrors, a mirror-form value or pool word is its canonical address."""
    mirror = mirror_map(mirror)
    size = len(cfg["binary_path"].read_bytes())
    use = raw_consts.uses(raw_text, size, mirror)
    bodies = None
    pointers, rest = [], []
    for a, bits in consts:
        v, b = int(a, 16), raw_consts.fold(bits, mirror)
        if "value" not in use.get(v, ()):
            res = check.IMAGE_BASE <= b < check.IMAGE_BASE + size and raw_consts.resolve(reg, ext, b)
            if res:  # batch 26: a pool word holding a named address (a machine-named string): its name
                pointers.append((a, "a literal-pool word holding the address of " + raw_consts.describe(res)[4:]))
                continue
            rest.append((a, bits))
            continue
        res = raw_consts.resolve(reg, ext, v)
        if res:
            pointers.append((a, raw_consts.describe(res)))
            continue
        if bodies is None:
            from . import paths
            bodies = raw_consts.body_ranges(paths.read_json(paths.CATALOG) or {}, paths.FUNCTION_BODIES)
        if raw_consts.region(v, bodies) == "code":
            rest.append((a, bits))
        else:
            pointers.append((a, f"no name yet: say so in your final message (MISSING GLOBAL 0x{a})"))
    return pointers, rest


def context_md(reg, addr, meta, cfg=None, raw_text="", types_body=None, decls=None, ext=None, views=None):
    """types_body: the lean types.h text (readable_lean.headers_for), shown as the section "Types and constants";
    views: its core/lean_select.Views (what the cap cut: core/lean_cap);
    decls: {callee addr: the signature game.h declares it with} (raw-named callees: parameters named by storage);
    ext: the named globals' extents (core/global_extents.cached) that name pointer constants (split_pointers)."""
    proto = reg.proto(addr, meta)
    _, name, params = readable.parse_proto(proto)
    st = readable.own_storages(reg, addr, proto, meta)
    lines = [f"# {name} (0x{addr})", "", f"Prototype: `{proto}`", "", "## Parameters (raw.c name -> yours)", ""]
    by_reg = {p["storage"] if p["storage"] != "stack" else f"stack+{p['stack_offset']}": p["name"]
              for p in meta["params"]}
    for (t, n), s in zip(params, st):
        lines.append(f"- {by_reg.get(s, '(not used by raw.c)')} ({s}) -> `{t} {n}`")
    from . import arg_passthrough, paths
    lines += arg_passthrough.context_lines(reg, addr, params, st, set(by_reg), paths.PACKETS)
    lines += ["", f"Return: raw `{meta['ret']}` -> `{readable.parse_proto(proto)[0]}`"]
    from . import paths, stack_uninit
    hints = paths.HINTS / f"{addr}.txt"
    lines += stack_uninit.context_lines(meta, hints.read_text(encoding="utf-8") if hints.exists() else "")
    lines += ["", "## Calls in raw.c", ""]
    sigs = {c["addr"]: c["signature"] for c in meta["callees"]}
    for a in sigs:
        cproto = reg.proto(a, raw_sig=(decls or {}).get(a) or sigs[a])
        lines.append(f"- tc_call(0x{a}) -> `{cproto}`")
        if readable.is_variadic(cproto):
            lines.append(variadic_note(reg, a, cproto))
    if not sigs:
        lines.append("No direct calls (a call through a pointer is a call through a typed function pointer).")
    if re.search(r"\btc_callf?\s*\(\s*(?!0x)", raw_text or ""):  # a call through a pointer (core/readable_check)
        lines.append("A call through a function pointer is judged on the argument registers raw.c fills: give its "
                     "type those parameters, the integer ones (r4..r7) first, then the float ones (fr4..fr11).")
    lines += state_notes(meta)
    from . import sh4_asm
    lines += readable_stack.context_lines(reg, addr, meta, raw_text,  # guest stack addresses are no globals
                                          asm_text=sh4_asm.read_packet(paths.PACKETS / addr))
    lines += ["", "## Globals raw.c uses", ""]
    pool = own_pool(meta)
    data = [g for g in meta["globals"] if g not in pool]
    for g in data:
        lines.append(f"- 0x{g}: {global_hint(reg, g, ext)}")
    if not data:
        lines.append("None.")
    mirror = mirror_map()
    consts = pool_constants(cfg, meta, raw_text, mirror) if cfg else []
    pointers, consts = split_pointers(reg, cfg, raw_text, consts, ext, mirror) if consts else ([], [])
    if pointers:
        forms = {raw_consts.fold(int(m, 16), mirror): m.lower() for m in re.findall(r"0x([0-9a-fA-F]{8})", raw_text)
                 if m[:2].lower() in mirror}  # raw.c's mirror form of an address (0x0c206130): named the same
        lines += ["", "## Addresses raw.c passes or stores as values (pointers: use the name, never the number)", ""]
        lines += [f"- 0x{a}" + (f" (raw.c: 0x{forms[int(a, 16)]})" if int(a, 16) in forms else "") + f": {text}"
                  for a, text in pointers]
    if consts:
        lines += ["", "## Constants (the original's literal pool: write them as literals, never as addresses)", ""]
        for a, bits in consts:
            lines.append(f"- 0x{a}: u32 0x{bits:08x} = s32 {struct.unpack('<i', struct.pack('<I', bits))[0]}"
                         f" = f32 {f32_literal(bits)}")
    if types_body is not None:
        lines += ["", "## Types and constants", ""]
        if "u8 _pad_0x" in types_body or "members not shown" in types_body:  # batch 25: core/lean_select
            lines += [PART_NOTE, ""]
        lines += ["```c", types_body, "```"] if types_body.strip() else ["None beyond rd_rt.h's basic types."]
        cut = lean_cap.summary(getattr(views, "cut", None) or {}, lean_cap.budget())
        lines += ["", cut] if cut else []
    return "\n".join(lines) + "\n"


def raw_word(storage):
    """raw.c's TcArgs word of a parameter storage: r4..r7 args.r[0..3], fr4..fr11 args.fr[0..7], stack+K args.s[K/4]."""
    if storage.startswith("stack+"):
        return f"args.s[{int(storage[6:]) // 4}]"
    if storage.startswith("fr"):
        return f"args.fr[{int(storage[2:]) - 4}]"
    return f"args.r[{int(storage[1:]) - 4}]" if storage[1:].isdigit() and int(storage[1:]) >= 4 else storage


def variadic_note(reg, addr, proto):
    """Which of raw.c's args words a variadic callee's parameters are (SHC: the last named parameter and the variable
    ones on the stack; refine.py varargs audits it at every call). Agents read raw.c's leftover args.r words at such a
    call (TC sprintf: args.r[1] holds whatever r5 held) as the format, which shifted every word after it."""
    from .readable_abi import NONE, override, storages
    st = [s if s != NONE else "(none)" for s in (override(reg, addr, proto) or storages(reg, proto))]
    named = ", ".join(f"{n} = {raw_word(s)}" for (_, n), s in zip(readable.parse_proto(proto)[2], st))
    nxt = max((int(s[6:]) // 4 for s in st if s.startswith("stack+")), default=-1) + 1
    return (f"  - variadic: {named}; then the variable arguments are args.s[{nxt}], args.s[{nxt + 1}], ... in order. "
            "raw.c's other args.r / args.fr words at this call are leftovers, not arguments.")


def low_regs():
    """The registers before the C convention's arguments a callee may read (target.json abi extra_inputs_allowed: the
    SH's r0..r3; none on the PowerPC, whose r3 is the first argument)."""
    import json
    from . import paths
    try:
        abi = json.loads((paths.TARGET / "target.json").read_text(encoding="utf-8")).get("abi") or {}
    except (OSError, ValueError):
        abi = {}
    return set(abi.get("extra_inputs_allowed", ("r0", "r1", "r2", "r3")))


def out_notes(meta):
    """context.md notes on r4..r7 outputs (TcOut): a callee handing a result back there, read after the call (meta
    r_out), or the function's own, read by its callers (meta r_exports). Shared with core/agent.abi_notes."""
    lines = []
    if meta.get("r_out"):
        lines += ["", "## Callees that hand results back in r4..r7", "",
                  "These callees leave an argument register advanced, and this function reads it after the call "
                  "(meta r_out: " + ", ".join(f"fn_{t} {r}" for t, r in meta["r_out"].items()) + "):",
                  "after each such call, read the register with sh4_r(N) (N = 4..7), as raw.c does; never keep the",
                  "value it had before the call."]
    if meta.get("r_exports"):
        lines += ["", "## Results handed back in " + ", ".join(meta["r_exports"]), "",
                  "Its callers read these registers after calling it, and they are compared at exit: before",
                  "returning, set each with sh4_set_r(N, value) to the value the original leaves there, as raw.c does."]
    if meta.get("t_out"):  # T after a call (TcTflag)
        lines += ["", "## Callees that hand T back", "",
                  "This function reads the T flag after calling these callees, before setting it (meta t_out: "
                  + ", ".join(f"fn_{t}" for t in meta["t_out"]) + "): T is what the callee left. Before each call",
                  "hand T over with sh4_set_t(t) and after it read it with t = sh4_t(), as raw.c does; never keep the",
                  "T it had before the call."]
    if meta.get("t_export"):
        lines += ["", "## T handed back", "",
                  "Its callers read the T flag after calling it, and T is compared at exit: before returning, set it",
                  "with sh4_set_t(t) to the T the original leaves (a path that never sets T hands back the T it got:",
                  "t = sh4_t() at entry), as raw.c does."]
    return lines


def state_notes(meta):
    """context.md notes on registers outside the C convention: runtime state, and callees reading r0..r3 or r8..r14."""
    lines = []
    state = (meta.get("r_state") or []) + (meta.get("fr_state") or [])
    if state:
        lines += ["", "## Runtime state: " + ", ".join(state), "",
                  "Values its callers keep in these registers: set at entry, compared at exit and at the calls",
                  "that read them. Keep raw.c's sh4_r/sh4_set_r (r8..r14) and sh4_fr/sh4_set_fr (fr12..fr15)",
                  "calls at the same points."]
    if meta.get("r_live"):
        lines += ["", "## Callees that change runtime state", "",
                  "These callees advance r8..r14 themselves (meta r_live: " + ", ".join(
                      f"fn_{t} {r} {d}" for t, regs in meta["r_live"].items() for r, d in regs.items()) + "): the value",
                  "changes at each such call. Read it with sh4_r(N) after the call, as raw.c does; never keep a copy",
                  "across one."]
    lines += out_notes(meta)
    if meta.get("sq_window"):
        lines += ["", "## The store-queue window", "",
                  f"{meta['sq_window']} may point into the store-queue window (0xe0000000..), mapped onto RAM as in",
                  "the port. Keep raw.c's 32-byte copies at the slots its pref sends unwritten: they are the queue's",
                  "old contents, which no store in C puts there."]
        from core.agent import sq_callee_bytes
        if sq_callee_bytes(meta):
            lines += [f"Raw.c's sq_mark lines name the bytes a callee stores into the window ({sq_callee_bytes(meta)}):",
                      "the pref sends them as the callee left them, so no copy may overwrite them."]
    if meta.get("fpu_state"):
        lines += ["", "## The whole FPU is state", "",
                  "raw.c switches FPU modes: fr0..fr15, xf0..xf15 and the mode bits are compared at exit and at",
                  "every call. Keep its sh4_fr/sh4_set_fr/sh4_xf/sh4_set_xf, sh4_frchg/sh4_fschg/sh4_fpchg and",
                  "sh4_ftrv_fv/sh4_fipr_fv calls, in the same order relative to the calls."]
    regs = {r for c in meta["callees"] for r in c["regs"]}
    low = sorted(regs & low_regs())
    if low:
        lines += ["", f"A callee reads {', '.join(low)} (raw.c's args.rlo): call it by name with its arguments",
                  "in the order of its prototype above (param_r0 is args.rlo[0], ...): the check puts each in the",
                  "register it names."]
    high = sorted(regs & {f"r{n}" for n in range(8, 15)}, key=lambda r: int(r[1:]))
    if high:
        lines += ["", f"A callee reads {', '.join(high)} as runtime state: before that call, keep raw.c's",
                  "sh4_set_r(N, value) calls (a register raw.c leaves alone keeps its entry value)."]
    fhigh = sorted(regs & {f"fr{n}" for n in range(12, 16)}, key=lambda r: int(r[2:]))
    if fhigh:
        lines += ["", f"A callee reads {', '.join(fhigh)} (beyond the float parameters fr4..fr11): before that",
                  "call, keep raw.c's sh4_set_fr(N, value) calls (a register raw.c leaves alone keeps its entry",
                  "value)."]
    return lines
