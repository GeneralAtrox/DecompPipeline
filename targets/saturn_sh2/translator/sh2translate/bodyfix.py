"""The passes over a function's rendered lines (translate.render calls finish_body): the TcArgs fields every call
writes (moved here from translate.py, which may not grow), and the dead epilogue after an endless loop: a body that
ends in a loop nothing leaves, followed by the original's own epilogue, builds with C4702 under MSVC /W4 /WX (Dragon
Force 060872c8 is such a function).

Nearest existing: cfgstruct.ends / structure.ends - they decide whether control runs off a sequence while the loops
are still a tree; this works on the rendered lines, where the epilogue (`return`, register copy-back, `pr` dispatch)
has been put in.
"""
import re

FIELD_LINE = re.compile(r"^\s*a\.(r|s|rlo)\[(\d)\] = (.*);$")
FIELD_ORDER = [("r", k) for k in range(4)] + [("s", k) for k in range(8)] + [("rlo", k) for k in range(4)]
LOOP_HEAD = re.compile(r"^(for|while|do|switch)\b")
TAIL_OK = re.compile(r"^(/\*.*\*/|return\b.*;|sh4_set_r\(.*|if \(pr == \w+\) goto L_\w+;)$")


def fill_call_args(body):
    """Every call writes the same TcArgs fields: those any call of the function writes, 0 where its callee takes
    nothing there. (DP's draft step takes a field's value from the assignments right before the call, or from the
    block's initializer only when no statement ever writes that field.)"""
    used = {(m.group(1), int(m.group(2))) for x in body for m in [FIELD_LINE.match(x)] if m}
    if not used:
        return body
    want = [f for f in FIELD_ORDER if f in used]
    out = []
    for x in body:
        if "tc_call(" not in x:
            out.append(x)
            continue
        got = {}
        while out and FIELD_LINE.match(out[-1]):
            m = FIELD_LINE.match(out.pop())
            got.setdefault((m.group(1), int(m.group(2))), m.group(3))
        ind = x[:len(x) - len(x.lstrip())]
        out += [f"{ind}a.{f}[{k}] = {got.get((f, k), '0')};" for f, k in want]
        out.append(x)
    return out


def leaves_loop(lines):
    """A `break;` in lines (a loop body, without its braces) that ends that loop, not an inner loop or switch."""
    kinds = []
    for x in lines:
        y = x.strip()
        if y.startswith("}") and kinds:
            kinds.pop()
            y = y.lstrip("} ")
        if re.search(r"\bbreak;", y) and not any(kinds):
            return True
        if y.endswith("{"):
            kinds.append(bool(LOOP_HEAD.match(y)))
    return False


def drop_dead_tail(body):
    """body: the rendered lines of the function (no braces of its own). When it ends in an endless `for (;;) { }` at
    its top level that has no break, and only epilogue lines follow with no label among them, those lines go."""
    ind = " " * 4
    end = next((k for k in range(len(body) - 1, -1, -1) if body[k] == ind + "}"), None)
    if end is None or any(not TAIL_OK.match(x.strip()) for x in body[end + 1:] if x.strip()):
        return body
    if not any(x.strip().startswith("return") for x in body[end + 1:]):
        return body
    head = next((k for k in range(end - 1, -1, -1) if body[k].startswith(ind) and not body[k].startswith(ind + " ")
                 and body[k].strip()), None)
    if head is None or body[head] != ind + "for (;;) {" or leaves_loop(body[head + 1:end]):
        return body
    return body[:end + 1] + [x for x in body[end + 1:] if x.strip().startswith("/*")]


def finish_body(body):
    return drop_dead_tail(fill_call_args(body))


FRAME_SET = re.compile(r"^\s*(r[4-7]) = r15(?: \+ (?:0x[0-9a-f]+|\d+)u?)?;$")
REG_SET = re.compile(r"^\s*(r[4-7]) (?:[-+*/%&|^]|<<|>>)?= ")
BLOCK_EDGE = re.compile(r"^\s*(?:\}|if\b|else\b|for\b|while\b|do\b|switch\b|goto\b|case\b|L_\w+:)|\{\s*$|tc_call\(")


def pass_frame_args(body):
    """R12 for a callee DP's meta lists with fewer registers: the original sets rN (4..7) to an address in its own
    stack frame (`mov r15,r4`, maybe `add #imm,r4`) right before the call, the C wrote nothing (0) in a.r[N-4] (the
    callee's meta takes nothing there, so the judge compares nothing), and the lint refuses the 0 (core/frame_args).
    The register goes on instead, when the straight-line statements before the call leave it as `rN = r15 [+ k];`
    (Robo Pit 06005498). Run before fill_call_args, which gives the function's other calls their 0 for the field."""
    out = list(body)
    i = 0
    while i < len(out):
        if "tc_call(" not in out[i]:
            i += 1
            continue
        j = i - 1
        while j >= 0 and out[j].lstrip().startswith("a."):
            j -= 1
        have = {m.group(1) for x in out[j + 1:i] for m in [re.match(r"^\s*a\.r\[([0-3])\] =", x)] if m}
        seen, k = {}, j
        while k >= 0 and not BLOCK_EDGE.search(out[k]):
            m = REG_SET.match(out[k])
            if m and m.group(1) not in seen:
                seen[m.group(1)] = bool(FRAME_SET.match(out[k]))
            k -= 1
        ind = out[i][:len(out[i]) - len(out[i].lstrip())]
        add = [f"{ind}a.r[{n}] = r{4 + n};" for n in range(4) if str(n) not in have and seen.get(f"r{4 + n}")]
        out[i:i] = add
        i += len(add) + 1
    return out
