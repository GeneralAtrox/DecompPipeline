"""Where each argument of a readable Xbox 360 function lives: the Xenon placement of a readable prototype, the call
trap's prototype letters (runtime_rd/rd_internal.h RdProto) and the readable judge's entry thunk. The Xbox 360
counterpart of targets/gamecube_ppc/ppc_readable_abi.py, for x360_readable.check_readable.

The Xenon passes integers and pointers in r3..r10 (64-bit registers) and floats in f1..f13, the two counted apart,
then the stack doublewords at r1+0x50+8N (s0, s1, ...). A raw signature can leave gaps (`void fn(f64 param_f1,
u64 param_r3, u64 param_r6)`): a parameter named param_rN or param_fN is in that register, and the parameters of the
function under test keep the storage its harness records (meta.json params) while they keep raw.c's names. Any other
parameter takes the next register of its class. The call trap places a mocked callee's arguments the same way
(rd_veh.c call_trap, by the regs codes built here), so a readable prototype that names the original's parameters gets
the original's inputs, and one that does not gets other values and fails the comparison.
"""
import re

from core.readable import BASE_KINDS, RegistryError, is_variadic, parse_proto, raw_proto

import x360_compare as cmp

F64 = {"f64", "double"}
I64 = {"u64", "s64", "uint64_t", "int64_t", "long long", "unsigned long long", "uintptr_t", "intptr_t"}
U32 = {"u32", "unsigned", "unsigned int", "unsigned long", "uint32_t"}
SIZES = {"f": 4, "d": 8, "c": 1, "C": 1, "h": 2, "H": 2, "i": 4, "I": 4, "L": 8, "P": 8}
# How each kind's value fills a 64-bit register (the entry thunk's result to r3).
RET_R3 = {"c": "(u64)(s64)(s8)", "C": "(u64)(u8)", "h": "(u64)(s64)(s16)", "H": "(u64)(u16)", "i": "(u64)(s64)(s32)",
          "I": "(u64)(u32)", "L": "(u64)", "P": "(u64)(uintptr_t)"}
# How the entry thunk takes each kind from a 64-bit register.
FROM_R = {"c": "(s8)", "C": "(u8)", "h": "(s16)", "H": "(u16)", "i": "(s32)", "I": "(u32)", "L": ""}
# Integer types narrower than a register: a callee's raw signature takes the register's 64 bits for them (register_sig).
NARROW = {"u8", "s8", "u16", "s16", "u32", "s32", "char", "signed char", "unsigned char", "short", "unsigned short",
          "int", "unsigned", "unsigned int", "signed int", "long", "unsigned long", "uint32_t", "int32_t"}
BITS_F64 = "static f64 rd_bits_f64(u64 x)\n{\n    union { f64 d; u64 b; } v;\n    v.b = x;\n    return v.d;\n}\n"


def register_sig(sig):
    """A callee's raw signature as readable C calls it: each integer parameter or result narrower than a register is
    the register's 64 bits (u64). Ghidra's s32 or u32 is a guess from the instructions; the original passes and the raw
    judge compares all 64 bits of r3..r10 (82549490 passes `param_1 - 0x40` in r3 to fn_825494c0(s32, u64): its upper
    word is not the sign extension, and a readable s32 argument could never match), and raw C takes tc_call's whole
    r3. Pointers, floats and void stay. A registry prototype (a named function) is the designer's and is not
    changed."""
    ret, name, params = parse_proto(raw_proto(sig))

    def w(t):
        return "u64" if re.sub(r"\s+", " ", re.sub(r"\b(const|volatile)\b", "", t)).strip() in NARROW else t
    return f"{w(ret)} {name}({', '.join(f'{w(t)} {n}' for t, n in params) or 'void'})"


def kind(reg, ctype):
    """Argument class of a C type: f (f32), d (f64), L (64-bit integer), P (pointer), c, C, h, H, i (s32), I (u32)
    or v."""
    t = re.sub(r"\s+", " ", re.sub(r"\b(const|volatile)\b", "", ctype)).strip()
    if "*" in t:
        return "P"
    if t.startswith("enum "):
        return "i"
    if t in F64:
        return "d"
    if t in I64:
        return "L"
    if t in U32:
        return "I"
    if t in BASE_KINDS:
        return BASE_KINDS[t]
    if t in reg.typedefs:
        target = reg.typedefs[t]
        if target in ("struct", "union"):
            raise RegistryError(f"{ctype} is passed by value (a struct/union): pass a pointer to it")
        return "i" if target == "enum" else kind(reg, target)
    raise RegistryError(f"unknown type {ctype!r} (not a basic type or a typedef in types.h)")


def _meta_storage(p):
    """A meta.json parameter's storage as place() spells it: r3..r10, f1..f13 or sN (the doubleword at r1+0x50+8N)."""
    return p["storage"] if p["storage"] != "stack" else f"s{(p['stack_offset'] - cmp.STACK_ARGS) // 8}"


def place(reg, proto, meta=None):
    """The storage of each parameter of proto: 'r3'..'r10', 'f1'..'f13' or 'sN'. meta: the harness of the function
    (its parameters keep their recorded storage while the prototype keeps their names)."""
    _, name, params = parse_proto(proto)
    own = {p["name"]: _meta_storage(p) for p in (meta or {}).get("params", [])}
    out, g, f, s = [], 0, 0, 0
    for t, n in params:
        k = kind(reg, t)
        m = re.fullmatch(r"param_([rf])(\d+)", n)
        if n in own:
            st = own[n]
        elif m and m.group(1) == "r" and 3 <= int(m.group(2)) <= 10:
            st = f"r{m.group(2)}"
        elif m and m.group(1) == "f" and 1 <= int(m.group(2)) <= cmp.CALL_FLOATS:
            st = f"f{m.group(2)}"
        elif k in "fd" and f < cmp.CALL_FLOATS:
            st = f"f{f + 1}"
        elif k not in "fd" and g < 8:
            st = f"r{g + 3}"
        else:
            st = f"s{s}"
        if st[0] == "r":
            g = max(g, int(st[1:]) - 2)
        elif st[0] == "f":
            f = max(f, int(st[1:]))
        else:
            s = max(s, int(st[1:]) + 1)
        out.append(st)
    if len(set(out)) != len(out):
        raise RegistryError(f"{name}: two parameters in the same register ({', '.join(out)})")
    return out


def regs_code(storages):
    """RdProto.regs for these storages (rd_internal.h): '0'..'7' r3..r10, 'a'..'m' f1..f13, 'S'..'Z' s0..s7, 'x'."""
    def one(st):
        n = int(st[1:])
        if st[0] == "r":
            return chr(ord("0") + n - 3)
        if st[0] == "f":
            return chr(ord("a") + n - 1)
        return chr(ord("S") + n) if n < 8 else "x"
    return "".join(one(s) for s in storages)


def kinds(reg, proto, meta=None):
    """(parameter letters, result letter, regs) for the call trap; '*' after the letters marks a variadic
    prototype."""
    ret, _, params = parse_proto(proto)
    letters = "".join(kind(reg, t) for t, _ in params)
    return letters + ("*" if is_variadic(proto) else ""), kind(reg, ret), regs_code(place(reg, proto, meta))


def pointer_protos(exp):
    """The call trap's prototypes for the targets of calls through function pointers (expected.txt CA: the argument
    registers the original sets): the readable call passes them as its parameters, the integer ones first in r3..r10
    order, then the float ones in f1..f13 order; its result is guessed at the call ('?'). A target called with several
    CA sets takes the longest (core/readable_check.pointer_protos, for this record)."""
    out = {}
    for run in exp.values():
        for k, regs in run.get("call_args", {}).items():
            ints = sorted((r for r in regs if r[0] == "r"), key=lambda r: int(r[1:]))
            floats = sorted((r for r in regs if r[0] == "f"), key=lambda r: int(r[1:]))
            letters = "L" * len(ints) + "d" * len(floats)
            t = run["calls"][k][0]
            if letters and len(letters) > len(out.get(t, ("",))[0]):
                out[t] = (letters, "?", regs_code(ints + floats))
    return out


def _arg(t, k, st, meta_off):
    """The thunk's expression for one parameter from the vector's registers (r, f) or the guest stack."""
    if st[0] == "f":
        return f"({t})f[{int(st[1:]) - 1}]"
    if st[0] == "r":
        v = f"r[{int(st[1:]) - 3}]"
        if k == "P":
            return f"({t})(uintptr_t)(u32){v}"
        return f"({t})rd_bits_f64({v})" if k in "fd" else f"({t}){FROM_R[k]}{v}"
    a = f"0x{cmp.SP + (meta_off if meta_off is not None else cmp.STACK_ARGS + 8 * int(st[1:])):08x}u"
    if k == "d":
        return f"({t})rdf64({a})"
    if k == "f":
        return f"({t})rdf32({a})" if meta_off is not None else f"({t})rdf32({a} + 4u)"
    if k == "P":
        return f"({t})(uintptr_t)(u32)rd64({a})"
    return f"({t}){FROM_R[k]}rd64({a})"  # a stack argument occupies a doubleword; an int its low (higher) word


def thunk_c(reg, addr, proto, meta=None):
    """The runner's entry thunk for the readable function at addr: each parameter from where the Xenon puts it for the
    readable prototype, the result to r3 (extended to 64 bits as its type says) or f1, as x360_check.gen_thunk for
    raw C."""
    ret, name, params = parse_proto(proto)
    letters, rk, _ = kinds(reg, proto, meta)
    if letters.endswith("*"):
        raise RegistryError(f"{name}: a variadic function under test (not supported on this target yet)")
    st = place(reg, proto, meta)
    offs = {p["name"]: p["stack_offset"] for p in (meta or {}).get("params", []) if p["storage"] == "stack"}
    args = [_arg(t, k, s, offs.get(n)) for (t, n), k, s in zip(params, letters, st)]
    call = f"{name}({', '.join(args)})"
    if rk == "v":
        body = f"{call};"
    elif rk in "fd":
        body = f"*out_f1 = (f64){call};"
    else:
        body = f"*out_r3 = {RET_R3[rk]}{call};"
    checks = [f'_Static_assert(sizeof({t}) == {SIZES[k]}, "size of {t}");' for (t, _), k in zip(params, letters)]
    helper = BITS_F64 if "rd_bits_f64" in call else ""
    return f"""#include "game.h"
u64 rd64(u32 addr);
f32 rdf32(u32 addr);
f64 rdf64(u32 addr);
void rd_set_self(u64 host, u32 guest);
{helper}{chr(10).join(checks)}
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1)
{{
    (void)r; (void)f; (void)out_r3; (void)out_f1;
    rd_set_self((u64)(uintptr_t)&{name}, 0x{int(addr, 16):08x}u);
    {body}
}}
"""
