"""Where each argument of a readable GameCube function lives: the PowerPC EABI placement of a readable prototype, the
call trap's prototype letters (runtime_rd/rd_internal.h RdProto) and the readable judge's entry thunk. The PowerPC
counterpart of core/readable_abi, for ppc_readable.check_readable.

The placement is the EABI's, by the readable prototype's types: integers and pointers in r3..r10, f32 and f64 in
f1..f8 (counted apart from the integers), a 64-bit integer in an odd-even register pair (r3:r4, r5:r6, ...), then the
stack words at r1+8 (s0, s1, ...; a 64-bit value 8-aligned). The call trap places a mocked callee's arguments the same
way (rd_veh.c call_trap), so a readable prototype that names the original's parameters gets the original's inputs,
and one that does not gets other values and fails the comparison.
"""
import re

from core.readable import BASE_KINDS, RegistryError, is_variadic, parse_proto

import ppc_compare as cmp

F64 = {"f64", "double"}
I64 = {"u64", "s64", "uint64_t", "int64_t", "long long", "unsigned long long"}
SIZES = {"f": 4, "d": 8, "c": 1, "C": 1, "h": 2, "H": 2, "i": 4, "L": 8}


def kind(reg, ctype):
    """Argument class of a C type: f (f32), d (f64), L (64-bit integer), c, C, h, H, i (32-bit integer or pointer)
    or v."""
    t = re.sub(r"\s+", " ", re.sub(r"\b(const|volatile)\b", "", ctype)).strip()
    if "*" in t or t.startswith("enum "):
        return "i"
    if t in F64:
        return "d"
    if t in I64:
        return "L"
    if t in BASE_KINDS:
        return BASE_KINDS[t]
    if t in reg.typedefs:
        target = reg.typedefs[t]
        if target in ("struct", "union"):
            raise RegistryError(f"{ctype} is passed by value (a struct/union): pass a pointer to it")
        return "i" if target == "enum" else kind(reg, target)
    raise RegistryError(f"unknown type {ctype!r} (not a basic type or a typedef in types.h)")


def kinds(reg, proto):
    """(parameter letters, result letter) for the call trap; '*' last marks a variadic prototype."""
    ret, _, params = parse_proto(proto)
    return "".join(kind(reg, t) for t, _ in params) + ("*" if is_variadic(proto) else ""), kind(reg, ret)


def place(letters, name="function"):
    """The EABI storage of each parameter: 'r3'..'r10', 'f1'..'f8', 'sN' (the stack word at r1+8+4N), or a pair
    'r3:r4' / 'sN:sN+1' for a 64-bit integer (a double past f8: refused, as more than 8 float parameters)."""
    out, g, f, s = [], 0, 0, 0
    for k in letters.rstrip("*"):
        if k in "fd":
            if f >= 8:
                raise RegistryError(f"{name}: more than 8 float parameters (the stack form is not supported)")
            out.append(f"f{f + 1}")
            f += 1
        elif k == "L":
            g += g & 1
            if g + 1 < 8:
                out.append(f"r{g + 3}:r{g + 4}")
                g += 2
            else:
                g, s = 8, s + (s & 1)
                out.append(f"s{s}:s{s + 1}")
                s += 2
        elif g < 8:
            out.append(f"r{g + 3}")
            g += 1
        else:
            out.append(f"s{s}")
            s += 1
    return out


def _arg(t, k, storage):
    """The thunk's expression for one parameter from the vector's registers (r, f) or the guest stack."""
    stack = lambda n: f"0x{cmp.SP + 8 + 4 * n:08x}u"  # noqa: E731
    if storage.startswith("f"):
        return f"({t})f[{int(storage[1:]) - 1}]"
    if ":" in storage:
        hi = storage.split(":")[0]
        if hi.startswith("r"):
            i = int(hi[1:]) - 3
            return f"({t})(((u64)r[{i}] << 32) | r[{i + 1}])"
        return f"({t})rd64({stack(int(hi[1:]))})"
    if storage.startswith("r"):
        return f"({t})(uintptr_t)r[{int(storage[1:]) - 3}]"
    return f"({t})(uintptr_t)rd32({stack(int(storage[1:]))})"


def thunk_c(reg, addr, proto):
    """The runner's entry thunk for the readable function at addr: each parameter from where the EABI puts it for the
    readable prototype, the result to r3 (r3:r4 for a 64-bit integer) or f1, as ppc_check.gen_thunk for raw C."""
    ret, name, params = parse_proto(proto)
    letters, rk = kinds(reg, proto)
    if letters.endswith("*"):
        raise RegistryError(f"{name}: a variadic function under test (not supported on this target yet)")
    st = place(letters, name)
    call = f"{name}({', '.join(_arg(t, k, s) for (t, _), k, s in zip(params, letters, st))})"
    if rk == "v":
        body = f"{call};"
    elif rk in "fd":
        body = f"*out_f1 = (f64){call};"
    elif rk == "L":
        body = f"u64 v = (u64){call}; *out_r3 = (u32)(v >> 32); *out_r4 = (u32)v;"
    else:
        body = f"*out_r3 = (u32)(uintptr_t){call};"
    checks = [f'_Static_assert(sizeof({t}) == {SIZES[k]}, "size of {t}");' for (t, _), k in zip(params, letters)]
    return f"""#include "game.h"
u32 rd32(u32 addr);
u64 rd64(u32 addr);
void rd_set_self(u32 host, u32 guest);
{chr(10).join(checks)}
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1);
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1)
{{
    (void)r; (void)f; (void)out_r3; (void)out_r4; (void)out_f1;
    rd_set_self((u32)(uintptr_t)&{name}, 0x{int(addr, 16):08x}u);
    {body}
}}
"""
