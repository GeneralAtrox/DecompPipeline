"""Readable stage: where each argument of a readable prototype lives (its SH-4 storage), for the function's own entry
thunk (thunk_c) and for its callees (callee_regs: the call trap's RdProto.regs).

Placement goes by storage, not by position:
- A raw-style prototype (every parameter keeps Ghidra's name: param_1, param_r4, param_fr5, param_s2) is placed as
  the raw signature is: parameter i where the original's parameter i is.
- A names entry may give "storage": one of r0..r7, fr4..fr11, stack+N or none (a placeholder the original does not
  read: passed nowhere) per parameter, for what the convention cannot express. Its storages must cover the
  original's, or be a part of them (as below).
- Otherwise the SH-4/SHC convention places the parameters (storages): ints and pointers r4..r7 in order, f32
  fr4..fr11 in order (independently), then the stack; in a variadic prototype the last named parameter and the
  variable ones go on the stack. The placement is accepted when it covers the original's storages as a set (the
  order is free; extra parameters where the original reads nothing are placeholders), or when it is a part of them
  (a prototype leaving out an input the original reads by accident: that storage is 0 at the call and in the thunk,
  and the judge still compares it). Each argument then goes where its storage says:
  `Vec3 *CamVec_Set(Vec3 *out, f32 x, f32 y, f32 z)` fits an original taking fr4, fr5, fr6, r4.
- Else parameter i goes where the original's parameter i is, when the counts agree and no pointer would come from an
  FPU register (an int Ghidra keeps in an FPU register; the rule before batch 14). Anything else is refused, and the
  message says which storage each parameter needs.
The judge compares the original's registers either way, so a wrong placement gives a FAIL, never a false PASS.
"""
import re

from .readable import RegistryError, is_variadic, parse_proto, raw_proto

GHIDRA_NAME = re.compile(r"param_(\d+|f?r\d+|s\d+)")
STORAGE = re.compile(r"r[0-7]|fr([4-9]|1[01])|stack\+(\d+)|none")
NONE = "none"  # a placeholder parameter: passed nowhere (0 in the thunk), never compared
CONVENTION = "ints and pointers r4..r7 in order, f32 fr4..fr11 in order, then the stack"


def order(s):
    """Ghidra's parameter order (its cspec's): fr4.., then r0.., then the stack by offset."""
    if s.startswith("fr"):
        return 0, int(s[2:])
    if s.startswith("r"):
        return 1, int(s[1:])
    return (2, int(s.split("+")[1])) if s.startswith("stack+") else (3, 0)


def arg_storage(reg_name):
    """A callee record's register as a parameter storage: r0..r7, fr4..fr11, sN -> stack+4N; else None (state)."""
    if re.fullmatch(r"r[0-7]|fr([4-9]|1[01])", reg_name):
        return reg_name
    m = re.fullmatch(r"s(\d+)", reg_name)
    return f"stack+{4 * int(m.group(1))}" if m else None


def storages(reg, proto):
    """The SH-4/SHC convention's storage of each parameter: rN, frN, or stack+offset."""
    params = parse_proto(proto)[2]
    last = len(params) - 1 if is_variadic(proto) else -1  # SHC: the last named parameter goes on the stack
    out, ni, nf, stack = [], 0, 0, 0
    for i, (t, _) in enumerate(params):
        k = reg.kind(t)
        if i != last and k == "f" and nf < 8:
            out.append(f"fr{4 + nf}")
            nf += 1
        elif i != last and k != "f" and ni < 4:
            out.append(f"r{4 + ni}")
            ni += 1
        else:
            out.append(f"stack+{stack}")
            stack += 4
    return out


def override(reg, addr, proto):
    """The names entry's "storage" for addr, when proto is that entry's prototype; else None."""
    e = reg.functions.get(addr) or {}
    st = e.get("storage")
    if st is None or e.get("proto") != proto:
        return None
    _, name, params = parse_proto(proto)
    real = [s for s in st if s != NONE] if isinstance(st, list) else []
    if (not isinstance(st, list) or len(st) != len(params) or len(set(real)) != len(real)
            or not all(isinstance(s, str) and STORAGE.fullmatch(s) for s in st)):
        raise RegistryError(f"{name}: \"storage\" must give one storage per parameter ({len(params)}), each once: "
                            "r0..r7, fr4..fr11, stack+N (a byte offset, a multiple of 4) or none (a placeholder "
                            "the original does not read)")
    return list(st)


def _need(reg, proto, raw, raw_names, conv):
    _, name, params = parse_proto(proto)
    have = ", ".join(f"{s} ({n})" for s, n in sorted(zip(raw, raw_names), key=lambda x: order(x[0])))
    got = ", ".join(f"`{t} {n}` -> {s}" for (t, n), s in zip(params, conv)) or "nothing"
    missing = ", ".join(sorted(set(raw) - set(conv), key=order))
    extra = ", ".join(sorted(set(conv) - set(raw), key=order))
    return (f"{name}: the original takes its arguments in {have}. By the SH-4 convention ({CONVENTION}) the "
            f"parameters go to {got}, which leaves out {missing} and puts arguments in {extra}, where the original "
            "takes none. Give each parameter the storage the original uses "
            "(order and type them so; a value in an fN register is an f32), or list each parameter's storage in its "
            "names entry as \"storage\": [...]")


def placement(reg, proto, raw, raw_names, fixed=None):
    """Storage of each parameter of proto, for an original taking its arguments in raw (its parameters' storages, in
    the raw signature's order; raw_names: those parameters' names). fixed: the names entry's "storage"."""
    _, name, params = parse_proto(proto)
    if fixed is not None:
        missing, extra = set(raw) - set(fixed), set(fixed) - set(raw) - {NONE}
        if missing and extra:
            raise RegistryError(f"{name}: its \"storage\" leaves out {', '.join(sorted(missing, key=order))}, where "
                                f"the original takes an argument, and names {', '.join(sorted(extra, key=order))}, "
                                "where it takes none")
        return fixed
    if len(params) == len(raw) and all(GHIDRA_NAME.fullmatch(n) for _, n in params) and not is_variadic(proto):
        return list(raw)  # raw-style: the names say where each parameter is
    conv = storages(reg, proto)
    if set(raw) <= set(conv) or set(conv) < set(raw):
        return conv
    if len(params) == len(raw) and not is_variadic(proto) and all(
            not s.startswith("fr") or reg.kind(t) == "f" or ("*" not in t and reg.kind(t) == "i")
            for (t, _), s in zip(params, raw)):
        return list(raw)  # by position: an int Ghidra keeps in an FPU register
    raise RegistryError(_need(reg, proto, raw, raw_names, conv))


def own_storages(reg, addr, proto, meta):
    """Storage of each parameter of the function's own prototype (its entry thunk, context.md)."""
    raw = [p["storage"] if p["storage"] != "stack" else f"stack+{p['stack_offset']}" for p in meta["params"]]
    return placement(reg, proto, raw, [p["name"] for p in meta["params"]], override(reg, addr, proto))


def param_storage(meta):
    """{parameter name: storage} of a function's own meta.json: rN, frN or stack+OFFSET."""
    return {p["name"]: p["storage"] if p["storage"] != "stack" else f"stack+{p['stack_offset']}"
            for p in meta["params"] if p.get("name")}


def name_storage(reg, params, regs=()):
    """Storage of each parameter going by Ghidra's names alone: param_r5 / param_fr6 / param_s2 (@(8,r15)) name
    theirs; the others take the convention's registers in order, skipping the named ones (fn(param_1, param_r4):
    param_1 is in r5). regs: the registers a caller's harness compares at the call (the callee record). When the
    unnamed parameters do not fit them but are exactly as many as the compared storages no name takes, they get those,
    in Ghidra's order (fr, r, stack): Lib_SpriteDrawAt's `u32 param_1..param_3` are fr4..fr6, sprintf's param_2 is
    the stack word s0."""
    named = {m.group(1) for _, n in params if (m := re.fullmatch(r"param_(f?r\d+)", n))}
    out, ni, nf, stack = [], 4, 4, 0
    for t, n in params:
        m = re.fullmatch(r"param_(f?r\d+)", n)
        if m:
            out.append(m.group(1))
            continue
        if m := re.fullmatch(r"param_s(\d+)", n):  # the stack word @(4N,r15)
            out.append(f"stack+{4 * int(m.group(1))}")
            continue
        if reg.kind(t) == "f":
            while f"fr{nf}" in named:
                nf += 1
            if nf < 12:
                out.append(f"fr{nf}")
                nf += 1
                continue
        else:
            while f"r{ni}" in named:
                ni += 1
            if ni < 8:
                out.append(f"r{ni}")
                ni += 1
                continue
        out.append(f"stack+{stack}")
        stack += 4
    args = {s for s in map(arg_storage, regs) if s}
    fixed = [i for i, (_, n) in enumerate(params) if re.fullmatch(r"param_(f?r\d+|s\d+)", n)]
    free = sorted(args - {out[i] for i in fixed}, key=order)
    rest = [i for i in range(len(params)) if i not in fixed]
    if args and len(free) == len(rest) and {out[i] for i in rest} != set(free):
        for i, s in zip(rest, free):
            out[i] = s
    return out


def callee_where(reg, callee, storage=None):
    """The callee's parameter storages in its raw signature's order: its own meta.json's (storage, by name), else
    Ghidra's names and the compared registers (name_storage)."""
    raw = parse_proto(raw_proto(callee["signature"]))[2]
    guess = name_storage(reg, raw, callee["regs"])
    return [(storage or {}).get(n) or guess[i] for i, (_, n) in enumerate(raw)], [n for _, n in raw]


def storage_named(reg, callee, storage=None):
    """The callee's raw signature for game.h with each numbered parameter (param_1) renamed by its storage (param_fr4,
    param_r4, param_s2), so readable C sees where a raw-named callee's arguments go: Ghidra lists fr parameters first
    (`fn_8c0622f6(u32 param_1, u32 param_2)` takes fr4, r4). Only names change; the placement is callee_where's."""
    sig = callee["signature"]
    where, names = callee_where(reg, callee, storage)
    new = {n: "param_" + (s if not s.startswith("stack+") else f"s{int(s[6:]) // 4}")
           for n, s in zip(names, where) if re.fullmatch(r"param_\d+", n) and s and s != NONE}
    if len(set(new.values()) | (set(names) - set(new))) != len(names):
        return sig  # a clash with a name already in the signature: keep Ghidra's
    head, _, params = sig.partition("(")
    return head + "(" + re.sub(r"\bparam_\d+\b", lambda m: new.get(m.group(0), m.group(0)), params)


def _code(s):
    if s == NONE:
        return "x"
    if re.fullmatch(r"r[0-7]", s):
        return s[1]
    if s.startswith("fr"):
        return chr(ord("a") + int(s[2:]) - 4)
    k = int(s.split("+")[1]) // 4
    return chr(ord("S") + k) if k < 8 else "x"  # the call record holds s0..s7; a deeper word is not compared


def conv_regs(reg, proto):
    """RdProto.regs placing proto's parameters by the convention, spelled out (a variadic function's)."""
    return "".join(map(_code, storages(reg, proto)))


def callee_place(reg, callee, proto, storage=None):
    """Storage of each parameter of the callee's prototype proto (registry or raw), checked against the registers
    the caller's harness compares at the call."""
    where, names = callee_where(reg, callee, storage)
    place = placement(reg, proto, where, names, override(reg, callee["addr"], proto))
    # r0..r3 have no C-convention default: one the judge compares must be a parameter (another register a prototype
    # leaves out is 0 at the call, as the oracle may have it too)
    missing = [r for r in callee["regs"] if re.fullmatch(r"r[0-3]", r) and r not in place]
    if missing:
        name = parse_proto(proto)[1]
        raise RegistryError(f"callee {callee['addr']} ({name}): the judge compares {', '.join(missing)} at each call, "
                            f"which no parameter of `{proto}` takes (they go to {', '.join(place) or 'nothing'}); "
                            "add the parameter, or give \"storage\" in its names entry")
    return place


def callee_regs(reg, callee, proto, storage=None):
    """RdProto.regs for a callee (a meta "callees" entry): "" when the trap's convention places proto, else one
    character per parameter: '0'..'7' (r0..r7), 'a'..'h' (fr4..fr11, an int there too), 'S'..'Z' (the stack words
    s0..s7) or 'x' (a deeper stack word, not compared). storage: {parameter name: storage} from the callee's own
    meta.json (param_storage); without it Ghidra's names and the compared registers decide (name_storage)."""
    place = callee_place(reg, callee, proto, storage)
    if place == storages(reg, proto) and not is_variadic(proto):
        return ""
    return "".join(map(_code, place))


def raw_alias_ok(reg, callee, storage=None):
    """Whether readable C may still call a named callee by its raw name with the raw signature's arguments: its
    registry placement starts with the raw parameters' storages, in order (the trap places by the registry's). Never
    for a variadic prototype: the raw signature cannot pass its variable arguments."""
    e = reg.functions.get(callee["addr"])
    if not e or is_variadic(e["proto"]):
        return False
    try:
        place = callee_place(reg, callee, e["proto"], storage)
    except RegistryError:
        return False
    where, _ = callee_where(reg, callee, storage)
    rk, raw_rk = reg.kind(parse_proto(e["proto"])[0]), reg.kind(parse_proto(raw_proto(callee["signature"]))[0])
    same_result = raw_rk == "v" or (rk == "f") == (raw_rk == "f")  # the trap returns in st(0) for an f32 only
    return same_result and len(where) <= len(place) and place[:len(where)] == where


def variadic_words(proto, st, sp):
    """A variadic function's own thunk passes, after its named parameters, the entry stack words after the named stack
    words up to s7 (the original reads its variable arguments there, its va_list points at the first), one u32 each:
    a fixed number of arguments, so va_start in the readable C gives the first of them and va_arg reads the vector's
    stack words. The raw harness tested the same function on the same words; a word past s7 is not in the vector.
    Returns (the words, the guest address of the first)."""
    if not is_variadic(proto):
        return [], None
    first = max([int(s.split("+")[1]) + 4 for s in st if s.startswith("stack+")] or [0])
    return [f"rd32(0x{sp + off:08x}u)" for off in range(first, 32, 4)], sp + first


def va_probe(params, args, guest):
    """(definition, statement) of the anchor probe: a variadic function with the same named parameters, called with
    the same arguments from the same thunk frame just before the readable function, so its va_start gives the host
    address of the first variable word the readable function's va_start will give. The runner reports it with the
    guest address (VAANCHOR); the judge pairs the two, so a va_list the function passes on must point exactly at the
    first variable word (a wrong anchor can only make a correct function fail, never a wrong one pass)."""
    names = [f"a{i}" for i in range(len(params))]
    decl = ", ".join(f"{t} {n}" for (t, _), n in zip(params, names))
    voids = " ".join(f"(void){n};" for n in names)
    probe = (f"static void rd_va_probe({decl}, ...)\n{{\n    va_list v;\n    {voids}\n"
             f"    va_start(v, {names[-1]});\n    rd_set_va_anchor((u32)(uintptr_t)v, 0x{guest:08x}u);\n"
             "    va_end(v);\n}\n")
    return probe, f"rd_va_probe({', '.join(args)});\n    "


def thunk_c(reg, addr, meta):
    """The runner's entry thunk for the readable function at addr, checked against the original's parameters.

    Each argument comes from where the original takes it, as check.gen_thunk does for raw C: r4..r7, fr4..fr11 (an
    int there too), the stack, r0..r3 (tcrt_low: an SHC runtime helper); a nonstandard result register (meta ret_reg)
    and a 64-bit result's high word (r1) are reported as RETX (tcrt_set_retx). r8..r14 state is sh4_r/sh4_set_r."""
    from .readable import SIZES
    proto = reg.proto(addr, meta)
    ret, name, params = parse_proto(proto)
    reg.kinds(proto)  # no 64-bit parameter
    st = own_storages(reg, addr, proto, meta)
    sp = 0x8CFFE000
    args = []
    for (t, _), s in zip(params, st):
        if s == NONE:  # a placeholder: the original reads nothing there
            args.append(f"({t})0")
        elif s.startswith("fr") and reg.kind(t) != "f":  # a 32-bit int in an FPU register: its bits
            args.append(f"({t})(uintptr_t)rd_f2u(fr[{int(s[2:]) - 4}])")
        elif s.startswith("fr"):
            args.append(f"fr[{int(s[2:]) - 4}]")
        elif s.startswith("r"):
            word = f"r[{int(s[1:]) - 4}]" if int(s[1:]) >= 4 else f"tcrt_low({s[1:]})"
            args.append(f"rd_u2f({word})" if reg.kind(t) == "f" else f"({t})(uintptr_t){word}")
        else:
            off = int(s.split("+")[1])
            args.append(f"({t})(uintptr_t)rd32(0x{sp + off:08x}u)" if reg.kind(t) != "f" else f"rdf32(0x{sp + off:08x}u)")
    words, guest = variadic_words(proto, st, sp)
    args += words
    probe, anchor = va_probe(params, args, guest) if guest is not None else ("", "")
    call = f"{name}({', '.join(args)})"
    rk = reg.kind(ret)
    if meta.get("ret_reg"):  # a nonstandard result register: reported as RETX
        body = f"tcrt_set_retx({'rd_f2u' if rk == 'f' else '(u32)(uintptr_t)'}({call}));"
    elif rk == "L":  # r0 (low word) and r1 (high word, reported as RETX)
        body = f"u64 v = (u64){call}; *out_r0 = (u32)v; tcrt_set_retx((u32)(v >> 32));"
    else:
        body = {"v": f"{call};", "f": f"*out_fr0 = {call};"}.get(rk, f"*out_r0 = (u32)(uintptr_t){call};")
    checks = [f"_Static_assert(sizeof({t}) == {SIZES[reg.kind(t)]}, \"size of {t}\");" for t, _ in params]
    return f"""#include "game.h"
u32 rd32(u32 addr);
f32 rdf32(u32 addr);
u32 tcrt_low(int index);
void tcrt_set_retx(u32 value);
void rd_set_va_anchor(u32 host, u32 guest);
void rd_set_self(u32 host, u32 guest);
static f32 rd_u2f(u32 u) {{ union {{ u32 u; f32 f; }} x; x.u = u; return x.f; }}
static u32 rd_f2u(f32 f) {{ union {{ u32 u; f32 f; }} x; x.f = f; return x.u; }}
{chr(10).join(checks)}
{probe}void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{{
    (void)r; (void)fr; (void)out_r0; (void)out_fr0; (void)rd_u2f; (void)rd_f2u;
    rd_set_self((u32)(uintptr_t)&{name}, 0x{int(addr, 16):08x}u);
    {anchor}{body}
}}
"""
