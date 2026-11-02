"""A function that calls itself, in the readable pass (core/rtx/lift).

The translator writes it as a helper and a wrapper: `static u32 body(u32 sp, u32 ret_addr, s32 param_1) { ...; r4 = r8;
r0 = (u32)body(r15, 0x8c06667eu, (s32)r4); ... }` and `u32 fn_ADDR(s32 param_1) { return body(0x8cffe000u, 0x8cfff000u,
param_1); }`. The readable function has no guest stack or return address to hand on: this makes the helper the function
and each self-call an ordinary call of the function (tc_call), so the passes name it like any other callee. The
judge runs the readable recursion as C and compares the calls and memory of the whole run. A form this does not know (a
computed return address or stack, register state as extra arguments) stays as it was, and the target refuses `body`.

Nearest existing: targets/dreamcast_sh4/rtx.prepare - it runs on the parsed body, and this has to choose which function
is parsed."""
import re

HELPER = re.compile(r"^static\s+([\w ]+?\**)\s*\bbody\s*\((.*?)\)\s*\{", re.M | re.S)
EXPORTED = re.compile(r"^([\w ]+?\**)\s*\b(fn_([0-9a-f]{8}))\s*\((.*?)\)\s*\{", re.M | re.S)
ARGS = ("TcArgs a = {{0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0}};")
SELF = re.compile(r"^(\s*)(?:(\w+)\s*=\s*)?(?:\(\s*\w+\s*\)\s*)?body\(r15,\s*0x[0-9a-f]+u,\s*([^;]*)\);[ \t]*$", re.M)


def close_of(text, brace):
    depth = 0
    for i in range(brace, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return i
    return -1


def split_args(text):
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return out + [cur.strip()] if cur.strip() else out


def slot(storage):
    """The call block's field of a parameter storage (spec.arg_var's names in the raw C's form)."""
    if storage.startswith("fr"):
        return f"a.fr[{int(storage[2:]) - 4}]"
    if storage.startswith("stack+"):
        return f"a.s[{int(storage[6:]) // 4}]"
    return f"a.r[{int(storage[1:]) - 4}]" if re.fullmatch(r"r[4-7]", storage) else None


def unwrap(text, job):
    """The raw C with the helper as the exported function, or the text as it was."""
    h, x = HELPER.search(text), EXPORTED.search(text)
    if not h or not x or h.start() > x.start():
        return text
    close = close_of(text, x.end() - 1)
    call = re.search(r"\bbody\(\s*(0x[0-9a-f]+u),\s*(0x[0-9a-f]+u)\s*(?:,|\))", text[x.end():close])
    params = [p.strip() for p in h.group(2).split(",")]
    if not call or params[:2] != ["u32 sp", "u32 ret_addr"] or params[2:] != [
            p.strip() for p in x.group(4).split(",") if p.strip() not in ("", "void")]:
        return text
    places = [slot(st) for _, st, _ in job.raw_params]
    if None in places or len(places) != len(params) - 2:
        return text
    fails = []

    def self_call(m):
        args = split_args(m.group(3))
        if len(args) != len(places):
            fails.append(m.group(0))
            return m.group(0)
        sets = "".join(f"{m.group(1)}{p} = {a};\n" for p, a in zip(places, args))
        fn = "tc_callf" if x.group(1).strip() == "f32" else "tc_call"
        lhs = f"{m.group(2)} = " if m.group(2) else ""
        return f"{sets}{m.group(1)}{lhs}{fn}(0x{x.group(3)}u, &a);"
    end = close_of(text, h.end() - 1)
    inner = SELF.sub(self_call, text[h.end():end])
    if fails or re.search(r"\bbody\s*\(", inner):
        return text
    inner = re.sub(r"\bsp\b", call.group(1), inner)
    inner = re.sub(r"\bret_addr\b", call.group(2), inner)
    decl = "" if re.search(r"\bTcArgs a\b", inner) else f"\n    {ARGS}"
    return (text[:h.start()] + f"{x.group(1)} {x.group(2)}({x.group(4)})\n{{{decl}" + inner + "}\n" + text[end + 1:x.start()])
