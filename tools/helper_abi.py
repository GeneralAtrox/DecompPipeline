"""List the matched functions whose C calls a runtime helper without the registers the helper takes (the helper ABI
rule, docs/PROOF_GATES.md).

usage: python tools/helper_abi.py --project DIR [--write] [--json FILE]

The SHC runtime helpers (division, shifts, block moves) take their arguments in r0..r3, not r4..r7. The C passes
them in TcArgs.rlo[0..3]; a call that leaves rlo empty runs the helper on whatever r0..r3 hold. The judge compares a
call only where a vector reaches it, so a wrong call on an unreached path passes: Toy Commander's 8c03a4b0
(Unit_UpdateBeams, 15 of 31 blocks hit) passed r4/r5 to the division 8c067c64, which takes r0 (divisor) and r1
(dividend), and the port crashed in two missions; 8c0248e0 (the menu viewer list, 7 of 224 blocks) did the same.
Static and text only (packets, harness meta, matched/ C); no Ghidra, no runner.
- The helpers: every callee that the game's harness metas measure reading r0..r3 (meta.json callees' regs).
- The asm: at each call of a helper, the instruction that last sets each of those registers in the block before the
  jsr (its delay slot first): a constant (`mov #3,r0`, a pool word) or something else.
- The C: each call whose arguments hold the helper's address. Its rlo values come from tc_call's TcArgs (its
  initializer or builder, then `x.rlo[i] = ...` up to the call) or, through the file's own wrappers, from the
  wrapper's parameters (call_rr(.., 7, r5) sets rlo[0] = 7). A `{0}` group sets nothing.
Kinds: missing (a call that sets no value for a register the helper takes: the class of 8c03a4b0 and 8c0248e0),
value (the asm sets a register to a constant that no call's rlo value for it holds, and those values hold literals).
Calls it cannot follow (a macro, a TcArgs passed in) are counted, not listed.
--write puts the list in the workspace's state/helper_abi/: requeue.txt (`addr reason`) and summary.json. It
requeues nothing: each game's session runs its own list.
"""
import argparse
import json
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402
from core import sh4_asm  # noqa: E402
from ptr_views import COMMENT, HEX  # noqa: E402

LOW = ("r0", "r1", "r2", "r3")
VIEW = 0x80000000
KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof"}
FUNC = re.compile(r"^[A-Za-z_][\w \t\*]*?\b(\w+)\s*\(([^;{}]*)\)\s*\{", re.M)
NUM = re.compile(r"(?<![\w.])(-?\s*(?:0[xX][0-9a-fA-F]+|\d+))[uUlL]*(?![\w.])")


def helpers(metas):
    """{callee: [r0..r3 it reads]} over the harness metas."""
    out = {}
    for m in metas:
        for c in m.get("callees", []):
            lo = [r for r in c.get("regs", []) if r in LOW]
            if lo and re.fullmatch(r"[0-9a-f]{8}", c.get("addr", "")):
                out.setdefault(c["addr"], set()).update(lo)
    return {a: sorted(r) for a, r in out.items()}


def asm_sites(ins, abi):
    """[(call address, helper, {reg: constant or None})]: each call of a helper, with the constant each register it
    takes holds at the jsr when the block before it sets one (None: set otherwise, or before the block)."""
    targets = {x.branch_target() for x in ins if x.mn in sh4_asm.COND or x.mn == "bra"}
    out = []
    for i, x in enumerate(ins):
        h = x.call_target()
        if x.mn not in sh4_asm.CALLS or h not in abi:
            continue
        before = [ins[i + 1]] if i + 1 < len(ins) and ins[i + 1].slot else []
        j = i - 1
        while j >= 0 and not ins[j].slot and ins[j].mn not in sh4_asm.DELAYED | sh4_asm.COND:
            before.append(ins[j])
            if ins[j].addr in targets:
                break
            j -= 1
        regs = {}
        for r in abi[h]:
            d = next((y for y in before if sh4_asm.uses(y, r)[1]), None)
            regs[r] = constant(d) if d else None
        out.append((x.addr, h, regs))
    return out


def constant(x):
    """(value, pool address or None) that x loads, or None when it loads no constant."""
    if x.mn == "mov" and x.ops and x.ops[0].startswith("#"):
        return int(x.ops[0][1:], 0) & 0xFFFFFFFF, None
    pv = x.pool_value() if x.mn in ("mov.w", "mov.l") else None
    return (pv[0], int(x.ops[0], 16)) if pv and re.fullmatch(r"0x[0-9a-f]{8}", x.ops[0]) else None


def functions(text):
    """{name: (parameter names, body start, body end)} of the file's function definitions."""
    out = {}
    for m in FUNC.finditer(text):
        if m.group(1) in KEYWORDS:
            continue
        depth, j = 1, m.end()
        while depth and j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        names = [re.findall(r"\w+", p)[-1] if re.findall(r"\w+", p) else "" for p in m.group(2).split(",")]
        out[m.group(1)] = (names, m.end(), j)
    return out


def split_top(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and not depth:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch in "({["
        depth -= ch in ")}]"
        cur += ch
    return out + [cur.strip()] if cur.strip() else out


def close(text, i):
    """The index just after the bracket that closes the one at i."""
    depth = 0
    for j in range(i, len(text)):
        depth += text[j] in "({["
        depth -= text[j] in ")}]"
        if not depth:
            return j + 1
    return len(text)


def enclosing_call(text, pos):
    """(name, argument index, index of its '(') of the innermost call around pos, or None outside any call."""
    depth, commas, i = 0, 0, pos - 1
    while i >= 0:
        ch = text[i]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth:
                depth -= 1
            elif ch == "(":
                m = re.search(r"(\w+)\s*$", text[max(0, i - 40):i])
                if m and m.group(1) not in KEYWORDS:
                    return m.group(1), commas, i
                if m:
                    return None
                commas = 0
            else:
                return None
        elif ch == "," and not depth:
            commas += 1
        elif ch == ";" and not depth:
            return None
        i -= 1
    return None


def init_rlo(s):
    """{index: expression} that a TcArgs initializer sets in rlo, or None when it is no initializer."""
    s = re.sub(r"^\(\s*TcArgs\s*\)\s*", "", s.strip())
    if not s.startswith("{"):
        return None
    el = split_top(s[1:close(s, 0) - 1])
    des = [e for e in el if e.startswith(".rlo")]
    if des:
        m = re.match(r"\.rlo\s*(?:\[\s*(\d)\s*\])?\s*=\s*(.*)$", des[0], re.S)
        if m.group(1):
            return {int(m.group(1)): m.group(2).strip()}
        el = [None, None, None, m.group(2)]
    elif any(e.startswith(".") for e in el):
        return {}
    if len(el) < 4 or not el[3].startswith("{"):
        return {}
    g = split_top(el[3][1:-1])
    if len(g) == 1 and re.fullmatch(r"0[uU]?", g[0]):
        return {}  # {0}: zero fill, nothing chosen
    return {i: e for i, e in enumerate(g) if e}


def subst(expr, params, args):
    for p, a in zip(params, args):
        if p:
            expr = re.sub(r"(?<![\w.>])%s\b" % re.escape(p), lambda _: "(%s)" % a, expr)
    return expr


def args_rlo(text, var, pos, start, funcs, depth):
    """{index: expression} that TcArgs var holds in rlo at pos, from its last reset after start; None: unknown."""
    seg = text[start:pos]
    resets = [(m.start(), m.end()) for m in re.finditer(r"\bTcArgs\b[^;]*?(?<![\w.>])%s\b" % re.escape(var), seg)]
    resets += [(m.start(), m.end()) for m in re.finditer(r"(?<![\w.>*&])%s\s*=(?!=)" % re.escape(var), seg)]
    resets += [(m.start(), m.end()) for m in re.finditer(r"\bmemset\s*\(\s*&\s*%s\b[^;]*;" % re.escape(var), seg)]
    if not resets:
        return None
    r0, r1 = max(resets)
    rest = seg[r1:]
    out = {}
    if seg[r0:].startswith("memset"):
        pass
    elif re.match(r"\s*=(?!=)", rest) or seg[r1 - 1] == "=":
        rhs = rest[re.match(r"\s*=?\s*", rest).end():]
        rhs = rhs[:close(rhs, 0)] if rhs[:1] == "{" or rhs.startswith("(TcArgs)") else rhs.split(";")[0]
        if rhs.startswith("(TcArgs)"):
            rhs = rhs + seg[r1:][len(rest[:rest.find(rhs)]) + len(rhs):].split(";")[0]
        got = init_rlo(rhs)
        if got is None:
            m = re.match(r"(\w+)\s*\((.*)\)\s*$", rhs.strip(), re.S)
            if not m or m.group(1) not in funcs or depth > 2:
                return None
            params, b0, b1 = funcs[m.group(1)]
            body = text[b0:b1]
            ret = re.search(r"\breturn\s+(\w+)\s*;", body)
            got = args_rlo(text, ret.group(1), b0 + ret.start(), b0, funcs, depth + 1) if ret else None
            if got is None:
                return None
            got = {i: subst(e, params, split_top(m.group(2))) for i, e in got.items()}
        out.update(got)
    v = re.escape(var)
    for m in re.finditer(r"(?<![\w.>])%s\s*(?:\.|->)\s*rlo\s*\[\s*(\d)\s*\]\s*=(?!=)\s*([^;]+);|"
                         r"\b(\w+)\s*\(\s*&?\s*%s\s*[,)]" % (v, v), seg[r1:]):
        if m.group(1):
            out[int(m.group(1))] = m.group(2).strip()
        elif m.group(3) not in ("tc_call", "tc_callf"):  # set_rlo(&a, 216000u, g, ..): a setter of the file
            if m.group(3) not in funcs or depth > 2:
                return None
            params, b0, b1 = funcs[m.group(3)]
            body = text[b0:b1]
            if "rlo" not in body:
                continue
            o = seg.index("(", r1 + m.start())
            sets = {int(i): e.strip() for i, e in re.findall(
                r"(?<![\w.>])%s\s*->\s*rlo\s*\[\s*(\d)\s*\]\s*=(?!=)\s*([^;]+);" % re.escape(params[0]), body)}
            if re.search(r"rlo\s*\[\s*[^\d\s\]][^\]]*\]\s*=(?!=)(?!\s*0[uU]?\s*;)", body):
                return None  # rlo[i] = something other than 0 in a loop
            if re.search(r"rlo\s*\[\s*[^\d\s\]][^\]]*\]\s*=\s*0[uU]?\s*;|\bmemset\s*\(\s*%s\b" % re.escape(params[0]),
                         body):
                out = {}  # clear_args(&a): every rlo word zeroed, so nothing chosen
            call_args = split_top(seg[o + 1:close(seg, o) - 1])
            out.update({i: subst(e, params, call_args) for i, e in sets.items()})
    return out


def call_rlo(text, name, args, pos, funcs, depth=0):
    """{index: expression} of rlo at a call `name(args)` at pos, or None when it cannot be followed."""
    start = max([b0 for _, b0, b1 in funcs.values() if b0 <= pos < b1] or [0])
    if name in ("tc_call", "tc_callf"):
        m = re.fullmatch(r"&\s*(\w+)", args[1]) if len(args) > 1 else None
        return args_rlo(text, m.group(1), pos, start, funcs, depth) if m else None
    if name not in funcs or depth > 2:
        return None
    params, b0, b1 = funcs[name]
    body = text[b0:b1]
    for m in re.finditer(r"\b(\w+)\s*\(", body):
        if m.group(1) in KEYWORDS or m.group(1) not in funcs and m.group(1) not in ("tc_call", "tc_callf"):
            continue
        inner = split_top(body[m.end():close(body, m.end() - 1) - 1])
        if inner and re.fullmatch(r"\(?\s*(?:\(\s*u32\s*\)\s*)?(\w+)\s*\)?", inner[0]):
            got = call_rlo(text, m.group(1), inner, b0 + m.start(), funcs, depth + 1)
            if got is not None:
                return {i: subst(e, params, args) for i, e in got.items()}
    return None


def pools(ins, abi):
    """{pool address: helper} of the pool words the listing loads a helper's address from."""
    out = {}
    for x in ins:
        pv = x.pool_value() if x.mn == "mov.l" else None
        if pv and f"{max(views(pv[0])):08x}" in abi and re.fullmatch(r"0x[0-9a-f]{8}", x.ops[0]):
            out.update({v: f"{max(views(pv[0])):08x}" for v in views(int(x.ops[0], 16))})
    return out


def c_sites(text, abi, pool=None):
    """[(helper, {index: expression} or None)] for each C call whose arguments hold a helper's address, or load it
    from its pool word (tc_call(rd32(0x8c0186a8u), &a); pool: pools())."""
    text = COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    funcs = functions(text)
    out = []
    for m in HEX.finditer(text):
        v = int(m.group(1), 16)
        h = f"{max(views(v)):08x}"  # PS2 calls 0x0c187670u
        at = m.start()
        if h not in abi:
            r = re.search(r"\brd32\s*\(\s*$", text[max(0, at - 20):at])
            if not pool or v not in pool or not r:
                continue
            h, at = pool[v], at - len(r.group(0))
        enc = enclosing_call(text, at)
        encs = [enc] if enc else []
        if not enc:  # FA = 0x8c5f62d4u; ... tc_call(FA, &a): the calls whose target is that local, up to its next set
            v = re.search(r"(?<![\w.>\]])(\w+)\s*=\s*(?:\(\s*u32\s*\)\s*)?$", text[max(0, m.start() - 60):m.start()])
            end = min([b1 for _, b0, b1 in funcs.values() if b0 <= m.start() < b1] or [len(text)])
            if v:
                again = re.compile(r"(?<![\w.>])%s\s*=(?!=)" % re.escape(v.group(1))).search(text, m.end(), end)
                for c in re.finditer(r"\b(\w+)\s*\(\s*%s\s*[,)]" % re.escape(v.group(1)), text[:again.start()
                                     if again else end]):
                    if c.start() > m.end() and c.group(1) not in KEYWORDS:
                        encs.append((c.group(1), 0, text.index("(", c.start())))
        for name, _, op in encs:
            if name in funcs and "tc_call" not in text[funcs[name][1]:funcs[name][2]]:
                continue  # mk_args(.., 0x8c187958u): the address as an rlo value of a builder, not a call
            if name not in ("rd8", "rd16", "rd32", "rdf32", "wr8", "wr16", "wr32", "wrf32"):
                out.append((h, call_rlo(text, name, split_top(text[op + 1:close(text, op) - 1]), op, funcs)))
    return out


def views(v):
    return {v, v ^ VIEW} if (v >> 24) & 0x7F == 0x0C else {v}


PURE = re.compile(r"[\s()0-9a-fA-FxXuUlL+\-*/&|^~<>]*")


def holds(expr, const):
    """True/False: expr, a constant expression, is the asm's constant (in either RAM view), or loads its pool word;
    None when expr is not constant (a variable, a macro, a load of something else)."""
    k, pool = const
    e = re.sub(r"\(\s*(?:u32|s32|u16|s16|u8|s8|int)\s*\)", "", expr).strip()
    m = re.fullmatch(r"\(?\s*rd32\s*\(\s*0[xX]([0-9a-fA-F]+)[uUlL]*\s*\)\s*\)?", e)
    if m:
        return pool is not None and int(m.group(1), 16) in views(pool) or None
    if not PURE.fullmatch(e) or not re.search(r"\d", e):
        return None
    try:
        v = eval(re.sub(r"(?<=[0-9a-fA-F])[uUlL]+\b", "", e).replace("/", "//"), {"__builtins__": {}}) & 0xFFFFFFFF
    except Exception:
        return None
    return v in views(k)
def check(asm, csites, abi):
    """-> [reason] for one function: its asm call sites (asm_sites) against its C calls (c_sites). A register that
    holds the helper's own address at every call is the jsr's target register, not an argument."""
    out = []
    for h in sorted({s[1] for s in asm}):
        calls = [r for g, r in csites if g == h]
        known = [r for r in calls if r is not None]
        sites = [s for s in asm if s[1] == h]
        for r in abi[h]:
            i = LOW.index(r)
            if all(s[2].get(r) and s[2][r][0] in views(int(h, 16)) for s in sites):
                continue
            if any(i not in rl for rl in known):
                out.append(f"missing: {h} {r} not passed ({sum(i not in rl for rl in known)} of {len(calls)} calls)")
                continue
            for addr, _, regs in sites:
                said = [holds(rl[i], regs[r]) for rl in known] if regs.get(r) else []
                if said and all(x is False for x in said):
                    out.append(f"value: {h} {r} = 0x{regs[r][0]:x} at {addr:08x}, C passes "
                               + ", ".join(sorted({rl[i] for rl in known})))
                    break
    return out


def scan(ws):
    from core import paths, ret_use
    rp = ret_use.Project(ws)
    abi = helpers(rp.meta.values())
    rows, counts = [], {"helpers": len(abi), "functions": 0, "callers": 0, "asm_calls": 0, "c_calls": 0,
                        "c_unfollowed": 0}
    for matched in sorted(paths.MATCHED.glob("*.c")):
        a = matched.stem
        ins = rp.insns(a)
        if not ins:
            continue
        counts["functions"] += 1
        asm = asm_sites(ins, abi)
        if not asm:
            continue
        cs = c_sites(matched.read_text(encoding="utf-8", errors="replace"), abi, pools(ins, abi))
        counts["callers"] += 1
        counts["asm_calls"] += len(asm)
        counts["c_calls"] += len(cs)
        counts["c_unfollowed"] += sum(r is None for _, r in cs)
        why = check(asm, cs, abi)
        if why:
            rows.append({"addr": a, "why": why})
    return rows, counts, abi


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    ws = use_project(a.project)
    rows, counts, abi = scan(ws)
    summary = {"project": str(ws), **counts, "requeue": len(rows),
               "missing": sum(any(w.startswith("missing") for w in r["why"]) for r in rows),
               "value_only": sum(all(w.startswith("value") for w in r["why"]) for r in rows),
               "abi": abi}
    print(json.dumps({k: v for k, v in summary.items() if k != "abi"}))
    for r in rows:
        print(f"  {r['addr']}  " + "; ".join(r["why"]))
    if a.write:
        out = ws / "state" / "helper_abi"
        out.mkdir(parents=True, exist_ok=True)
        (out / "requeue.txt").write_text("".join(f"{r['addr']} {'; '.join(r['why'])}\n" for r in rows),
                                         encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
        print(f"wrote {out}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
