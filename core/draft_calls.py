"""The tc_call pass of a machine draft (core/draft): tc_call(0xT, &args) -> Name(arguments), by the callee's
placement (core/readable_abi: the storage of each parameter of its prototype).

An argument's value is the expression of the last `args.r[i] = E;` (fr, s) among the statements right before the
call (only such assignments, back to the previous other statement), when E has no side effects; or the TcArgs
block's initializer (0 when the block is initialized but the field not written) when no statement of the function
ever writes that field. Anything else leaves the call as it is. When no tc_call uses a block any more, its field
assignments and its declaration go (all side-effect free, or nothing goes).
"""
import re

from .draft import close_paren, side_effect_free, split_args
from .raw_names import mask

DECL = re.compile(r"\bTcArgs\s+([A-Za-z_]\w*)\s*(=\s*\{)?")
FIELD = r"(r|fr|s|rlo)\s*\[\s*(\d+)\s*\]"
STORE = {"r": lambda i: f"r{4 + i}", "fr": lambda i: f"fr{4 + i}", "s": lambda i: f"stack+{4 * i}",
         "rlo": lambda i: f"r{i}"}


def _init_values(text, brace):
    """{storage: expression} of a TcArgs initializer starting at the '{' at text[brace] (missing fields are 0)."""
    code = mask(text)[0]
    end = code.find("}", brace)
    depth, j = 0, brace
    for j in range(brace, len(code)):
        depth += {"{": 1, "}": -1}.get(code[j], 0)
        if depth == 0:
            end = j
            break
    out = {f"r{4 + i}": "0" for i in range(4)} | {f"fr{4 + i}": "0.0f" for i in range(8)}
    out |= {f"stack+{4 * i}": "0" for i in range(8)} | {f"r{i}": "0" for i in range(4)}
    inner = text[brace + 1:end].strip()
    groups = split_args(inner) if inner else []
    for gi, (kind, n) in enumerate((("r", 4), ("fr", 8), ("s", 8), ("rlo", 4))):
        if gi >= len(groups):
            break
        g = groups[gi].strip()
        if not (g.startswith("{") and g.endswith("}")):
            return None, end  # a flat initializer list: not modelled
        for i, e in enumerate(split_args(g[1:-1]) if g[1:-1].strip() else []):
            if i < n and e:
                out[STORE[kind](i)] = e
    return out, end


def _statement_start(code, i):
    j = i - 1
    while j >= 0 and code[j] not in ";{}":
        j -= 1
    return j + 1


def _run_before(text, code, start, block):
    """{storage: expression} of the consecutive `block.F[i] = E;` statements ending right before start (the latest
    wins), or None when one of them has a side effect."""
    got = {}
    pat = re.compile(rf"\s*{re.escape(block)}\s*\.\s*{FIELD}\s*=\s*(.+?)\s*;\s*$", re.S)
    end = start
    while end > 0 and code[end - 1] in " \t\r\n":
        end -= 1
    while end > 0 and code[end - 1] == ";":
        s = _statement_start(code, end - 1)
        m = pat.fullmatch(code[s:end])
        if not m:
            break
        expr = text[s:end][m.start(3):m.end(3)]
        if not side_effect_free(expr):
            return None
        got.setdefault(STORE[m.group(1)](int(m.group(2))), expr)
        end = s
        while end > 0 and code[end - 1] in " \t\r\n":
            end -= 1
    return got


def _written(code, block):
    """Storages some statement writes in the block."""
    return {STORE[k](int(i)) for k, i in re.findall(rf"\b{re.escape(block)}\s*\.\s*{FIELD}\s*=(?!=)", code)}


def rewrite_tc_calls(text, callees, own_void=False):
    """text with each convertible tc_call / tc_callf by name; callees: {hex addr: (name, [storage], result kind,
    variadic)}. own_void: the function returns void (a tail call `return tc_call(...)` becomes `Name(...); return`)."""
    code = mask(text)[0]
    blocks = {}
    for m in DECL.finditer(code):
        init = None
        if m.group(2):
            init, _ = _init_values(text, m.end() - 1)
        blocks[m.group(1)] = {"init": init, "written": _written(code, m.group(1))}
    edits, left = [], 0
    call = re.compile(r"\b(tc_callf?)\s*\(")
    for m in call.finditer(code):
        close = close_paren(code, m.end() - 1)
        args = split_args(text[m.end():close])
        rep = _convert(text, code, m, close, args, blocks, callees, own_void)
        if rep is None:
            left += 1
            continue
        edits.append(rep)
    for a, b, new in sorted(edits, reverse=True):
        text = text[:a] + new + text[b:]
    if not left:
        text = _drop_blocks(text, blocks)
    return text


def _convert(text, code, m, close, args, blocks, callees, own_void):
    """(start, end, replacement) for one tc_call, else None."""
    if len(args) != 2:
        return None
    t = re.fullmatch(r"\(?\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*\)?", args[0])
    b = re.fullmatch(r"&\s*([A-Za-z_]\w*)", args[1])
    if not t or not b or b.group(1) not in blocks or t.group(1).lower() not in callees:
        return None
    name, place, rk, variadic = callees[t.group(1).lower()]
    blk = blocks[b.group(1)]
    if variadic or (m.group(1) == "tc_callf") != (rk == "f") and rk != "v":
        return None
    start = _statement_start(code, m.start())
    run = _run_before(text, code, start, b.group(1))
    if run is None:
        return None
    vals = []
    for st in place:
        if st == "none":
            vals.append("0")
        elif st in run:
            vals.append(run[st])
        elif st not in blk["written"] and blk["init"] is not None and st in blk["init"]:
            vals.append(blk["init"][st])
        else:
            return None
    new = f"{name}({', '.join(vals)})"
    head = code[start:m.start()].strip()
    if head == "return" and own_void:  # a tail call from a void function
        if code[close + 1:].lstrip()[:1] != ";":
            return None
        return start + code[start:].index("return"), close + 1, f"{new};\n    return"
    if rk == "v" and head not in ("", "(void)"):
        return None  # a void callee's result is used
    if rk == "v" and head == "(void)":
        return start + code[start:].index("("), close + 1, new
    return m.start(), close + 1, new


def _drop_blocks(text, blocks):
    """Remove the field assignments and declarations of the TcArgs blocks no call uses any more."""
    for block in blocks:
        code = mask(text)[0]
        if re.search(rf"&\s*{re.escape(block)}\b", code):
            continue
        stmts = list(re.finditer(rf"[ \t]*\b{re.escape(block)}\s*\.\s*{FIELD}\s*=\s*([^;]*);[ \t]*\n?", code))
        if any(not side_effect_free(text[s.start(3):s.end(3)]) for s in stmts):
            continue
        for s in reversed(stmts):
            text = text[:s.start()] + text[s.end():]
        code = mask(text)[0]
        d = re.search(rf"[ \t]*\bTcArgs\s+{re.escape(block)}\b", code)
        if d:
            semi = code.find(";", d.end())
            brace = code.find("{", d.end())
            if 0 <= brace < semi:
                semi = code.find(";", close_brace(code, brace))
            end = semi + 1
            if text[end:end + 1] == "\n":
                end += 1
            text = text[:d.start()] + text[end:]
            text = re.sub(r"\{\n[ \t]*\n", "{\n", text)
        if re.search(rf"\b{re.escape(block)}\b", mask(text)[0]):
            return text  # still used somewhere (a copy, an address): the lint refuses what is left
    return text


def close_brace(code, i):
    depth = 0
    for j in range(i, len(code)):
        depth += {"{": 1, "}": -1}.get(code[j], 0)
        if depth == 0:
            return j
    return len(code) - 1
