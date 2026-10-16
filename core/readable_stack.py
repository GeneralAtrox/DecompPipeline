"""Readable stage: the original's guest stack in what an agent sees and in what the judge compares (batch 22).

raw.c runs the original on its guest stack: r15 is 0x8cffe000 at entry (compare.SP). A raw constant at or above it
is a stack argument (0x8cffe000 + 4N: stack argument N, a parameter of the prototype); one below it is the
function's own frame (a local). Haiku read `rd32(0x8cffe000)` in 8c058402 (Pvr_EmitListClip's y1, stack argument 0)
as an unnamed global, and 8c049bfc's TOC buffer 0x8cffde58 likewise. So:
- annotate_raw marks every such constant in the sandbox's raw.c with what it is (line numbers unchanged);
- context_lines lists them in context.md;
- RR6 (stack_arg_lint) refuses a readable file that leaves out a parameter raw.c reads from its stack slot;
- pair_stores: a guest stack address the original stores to memory (8c027610 stores &tex into
  g_pvrHeaders[2].texture) pairs with the host stack address the readable code stores, one to one with the pointer
  arguments (readable_check.map_stack_args), so the store compares equal exactly when it points at the local the
  original's address stands for.
"""
import re

from . import lint
from .compare import SP, STACK_LO

STACK_HI = 0x8D000000
CONST = re.compile(r"\b0[xX]([0-9a-fA-F]{8})[uU]?\b")
READ = re.compile(r"\brd(?:8|16|32|f32)\s*\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*\)")


def code_spans(line, in_comment):
    """([(start, end)] of line's code outside comments and literals, in_comment at the end of the line)."""
    spans, i, start, n = [], 0, None if in_comment else 0, len(line)
    while i < n:
        if in_comment:
            j = line.find("*/", i)
            if j < 0:
                return spans, True
            in_comment, i, start = False, j + 2, j + 2
            continue
        two = line[i:i + 2]
        if two == "/*" or two == "//":
            spans.append((start, i))
            if two == "//":
                return spans, False
            in_comment, i = True, i + 2
            continue
        if line[i] in "\"'":
            q, j = line[i], i + 1
            while j < n and line[j] != q:
                j += 2 if line[j] == "\\" else 1
            spans.append((start, i))
            i = start = j + 1
            continue
        i += 1
    if start is not None and start < n:
        spans.append((start, n))
    return spans, False


def constants(raw_text):
    """The guest stack addresses raw.c writes as numbers (outside comments), sorted."""
    return sorted({int(m.group(1), 16) for m in CONST.finditer(lint.strip_comments(raw_text))
                   if STACK_LO <= int(m.group(1), 16) < STACK_HI})


def stack_params(reg, addr, meta):
    """{byte offset above the entry r15: parameter name} of the function's own prototype (stack+N storages)."""
    from . import readable
    from .readable_abi import own_storages
    try:
        proto = reg.proto(addr, meta)
        params = readable.parse_proto(proto)[2]
        st = own_storages(reg, addr, proto, meta)
    except readable.RegistryError:
        return {}
    return {int(s[6:]): n for (_, n), s in zip(params, st) if s.startswith("stack+")}


def describe(a, params, short=False):
    """What the guest stack address a is: a stack argument (the prototype's parameter there) or the own frame."""
    if a >= SP:
        off = a - SP
        name = params.get(off - off % 4)
        part = f"byte {off % 4} of " if off % 4 else ""
        if short:
            return (f"0x{a:08x} = {part}stack argument {off // 4} = `{name}`" if name else
                    f"0x{a:08x} = entry r15" + (f" + 0x{off:x}" if off else "")) + ", not a global"
        if name:
            return (f"{part}stack argument {off // 4} (entry r15 + 0x{off:x}): your parameter `{name}`, not a global "
                    "(read the parameter)")
        if not off:
            return ("the stack pointer r15 at entry, not a global (raw.c's addresses computed from it are stack "
                    "arguments at or above it and the function's own locals below it)")
        return (f"entry r15 + 0x{off:x}: stack argument word {off // 4}, which no parameter of the prototype takes; "
                "not a global")
    if short:
        return f"0x{a:08x} = own frame, entry r15 - 0x{SP - a:x} (a local), not a global"
    return (f"this function's own stack frame (entry r15 - 0x{SP - a:x}): a local of yours, never a global. Declare "
            "the local and use or pass its address: the judge pairs it with the original's")


def annotate_raw(raw_text, reg, addr, meta):
    """raw.c with a comment after each line that writes a guest stack address as a number, saying what it is (a line
    ending inside a block comment or with a backslash is left alone; context.md lists every address)."""
    if not constants(raw_text):
        return raw_text
    params = stack_params(reg, addr, meta)
    out, in_comment = [], False
    for line in raw_text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        spans, in_comment = code_spans(body, in_comment)
        found = []
        for s, e in spans:
            for m in CONST.finditer(body[s:e]):
                v = int(m.group(1), 16)
                if STACK_LO <= v < STACK_HI and v not in found:
                    found.append(v)
        if found and not in_comment and not body.endswith("\\"):
            note = "; ".join(describe(v, params, short=True) for v in found)
            line = f"{body}  /* {note} */{line[len(body):]}"
        out.append(line)
    return "".join(out)


def prologue_pushes(asm_text):
    """Bytes the prologue pushes (`mov.l rN,@-r15`, `sts.l pr,@-r15`, fmov pushes) before the first call, branch or
    other change of r15: the saved registers just below the entry r15. None without a listing."""
    from . import sh4_asm
    if not asm_text:
        return None
    total = 0
    for x in sh4_asm.parse(asm_text)[:32]:
        if x.call_target() or x.mn.startswith(("b", "j", "rts")):
            break
        if len(x.ops) == 2 and x.ops[1].lower() == "@-r15":
            m = re.search(r"\.([bwl])$", x.mn)
            total += {"b": 1, "w": 2, "l": 4}[m.group(1)] if m else (8 if x.ops[0].lower().startswith(("dr", "xd"))
                                                                     else 4)
        elif x.ops and x.ops[-1].lower() == "r15":
            break
    return total


def frame_spans(found, pushes):
    """{own-frame address: bytes up to the next higher stack address raw.c names, or to the saved registers}. A
    local passed by address in the original reaches that far: the callee may fill all of it (8c06fc14 passed an
    8-byte AnimFrame where Anim_ReadFrame fills 0x2f0 bytes, and the port's stack broke)."""
    top = SP - (pushes or 0)
    own = [a for a in found if a < top]
    return {a: (own[i + 1] if i + 1 < len(own) else top) - a for i, a in enumerate(own)}


def context_lines(reg, addr, meta, raw_text, limit=24, asm_text=None):
    """context.md's section on the guest stack addresses raw.c writes as numbers (none: no section)."""
    found = constants(raw_text)
    if not found:
        return []
    params = stack_params(reg, addr, meta)
    spans = frame_spans(found, prologue_pushes(asm_text))
    lines = ["", "## Stack addresses in raw.c (never globals)", "",
             f"raw.c runs the original on its guest stack: r15 is 0x{SP:08x} at entry. An address at or above it is a "
             "stack argument (0x8cffe000 + 4N: stack argument N, a parameter); one below it is this function's own "
             "frame (a local). Readable C uses the parameter or the local; it never names these numbers.", ""]
    lines += [f"- 0x{a:08x}: {describe(a, params)}" + (
              f". The original's frame has 0x{spans[a]:x} bytes from here up to the next stack address raw.c names "
              "(or its saved registers): a local whose address you pass to a callee needs that many bytes, since the "
              "callee may fill all of them" if spans.get(a, 0) > 4 else "") for a in found[:limit]]
    if len(found) > limit:
        lines.append(f"- ... and {len(found) - limit} more addresses in the own frame (locals)")
    return lines


def _body(code, name):
    """The text of the definition of name in code (between its braces), or None."""
    m = re.search(rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", code)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(code) and depth:
        depth += {"{": 1, "}": -1}.get(code[i], 0)
        i += 1
    return code[m.end():i - 1]


def stack_arg_lint(src, raw_text, reg, addr, meta, name):
    """RR6: raw.c reads a stack argument by its address (rd32(0x8cffe000)), and the readable definition never uses
    the parameter there (or only discards it: `(void)y1;`): the value is the caller's argument, not a global."""
    params = stack_params(reg, addr, meta)
    if not params:
        return []
    body = _body(lint.strip_comments(src), name)
    if body is None:
        return []
    body = re.sub(r"\(\s*void\s*\)\s*[A-Za-z_]\w*\s*;", "", body)
    errors = []
    for a in sorted({int(m.group(1), 16) for m in READ.finditer(lint.strip_comments(raw_text))}):
        off = a - SP
        p = params.get(off - off % 4) if a >= SP else None
        if p and not re.search(rf"\b{re.escape(p)}\b", body):
            errors.append(f"RR6: raw.c reads 0x{a:08x}, stack argument {off // 4}: that is your parameter `{p}`, not "
                          "a global; use it")
    return errors


def pair_stores(exp, act, stack, g2h, h2g, initial):
    """Memory words outside the guest stack where the original stores a guest stack address and the readable code a
    host stack address: when the two pair one to one with every pointer argument paired so far (map_stack_args'
    g2h/h2g, extended here), the readable word reads as the original's. A pointer to another local (paired with
    another guest slot), or a value that is no host stack address, keeps its bytes and differs. initial(address):
    the byte there before the run (a written byte equal to it is not in the writes)."""
    if not act or not stack:
        return
    lo, hi = stack[0], stack[1]
    ew, aw = exp["writes"], act["writes"]
    for w in sorted({a & ~3 for a in ew if a < STACK_LO}):
        if not any(w + i in aw for i in range(4)):
            continue
        g = int.from_bytes(bytes(ew.get(w + i, initial(w + i)) for i in range(4)), "little")
        h = int.from_bytes(bytes(aw.get(w + i, initial(w + i)) for i in range(4)), "little")
        if not (STACK_LO <= g < STACK_HI and lo <= h < hi) or g2h.get(g, h) != h or h2g.get(h, g) != g:
            continue
        g2h[g], h2g[h] = h, g
        for i in range(4):
            aw[w + i] = (g >> (8 * i)) & 0xFF
