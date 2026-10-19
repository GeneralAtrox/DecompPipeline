"""List the matched functions whose C writes a RAM pointer in the other view than the original's pool (the mirror
rule, docs/PROOF_GATES.md).

usage: python tools/ptr_views.py --project DIR [--write] [--json FILE]

The judge takes 0x0c...... and 0x8c...... as one main-RAM address (core/compare.same_word), so a function whose C
writes a pool's 0x0c...... pointer as 0x8c...... passes. Its stored pointers then differ from the ones the rest of the
game writes, and code that compares two pointers (a list walk's `p == self`, tools/mirror_ptrs.py) takes a different
branch: PS2 8c07bbf4's cmp/eq on +0x377c, 8c0bbb9a's self-skip. This check finds the C that mixes the views.
Static and text only (packets, matched/ C, code_ranges.txt); no Ghidra, no runner. For every matched function, its
pool data words in either RAM view (core/sh4_asm pool_value, not function addresses) against the integer literals of
its C with the comments taken out:
- view: the C writes a pool word's other view (0x8c532698 for the pool's 0x0c532698);
- offset: the C writes, in the other view, an address up to 64 KB above a pool word that the original reaches by
  adding to it (0x8c532698 + 0xe4e0 written as 0x8c540b78), and the pool has no word in that view.
  Literals inside the game's code ranges (a pool's own address in rd32(0x8c02ea08u)) or the harness stack
  (sp = 0x8cffe000u - 52u) are not pointers here; nor, for either kind, is a pool word inside the code ranges.
Only value sites requeue (literals()): a literal that is just the address of an rd32/wr8... access reaches the same
RAM in either view (access_only in summary.json). PS2 8c07cedc stores `0x8c532698u + i * size` at +0x377c, where
the original stores the pool's 0x0c532698 + i * size; 8c07bbf4 then compares it with the 0x0c pointer.
--write puts the list in the workspace's state/ptr_views/: requeue.txt (`addr reason`, value sites) and
summary.json. It requeues nothing: each game's session runs its own list.
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

HEX = re.compile(r"(?<![\w.])0[xX]([0-9a-fA-F]{1,8})[uUlL]*(?![\w.])")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"", re.S)
VIEW = 0x80000000
SPAN = 0x10000
STACK = (0x8CFEE000, 0x8D000000)  # the harness's own frames under entry r15 (core/compare.SP, TCRT_SP), as `sp`


def ram(v):
    """True for a main-RAM address in either view: 0x0c...... or 0x8c......"""
    return (v >> 24) & 0x7F == 0x0C


def pool_words(ins):
    """{word} of the RAM data words the listing loads from its pool (not function addresses)."""
    return {pv[0] for x in ins if x.mn == "mov.l" for pv in [x.pool_value()]
            if pv and pv[1] != "function" and ram(pv[0])}


def enclosing(text, pos):
    """(name, argument index) of the innermost call around pos; grouping and cast parentheses are looked through;
    name is a keyword (if, return ...) for a condition, None outside any call."""
    depth, commas, i = 0, 0, pos - 1
    while i >= 0:
        ch = text[i]
        if ch in ")]":
            depth += 1
        elif ch in "([":
            if depth:
                depth -= 1
            elif ch == "[":
                return "[", 0  # an index: a value
            else:
                m = re.search(r"(\w+)\s*$", text[max(0, i - 40):i])
                if m:
                    return m.group(1), commas
                commas = 0  # (a + b), (u32)x: look further out
        elif ch == "," and not depth:
            commas += 1
        elif ch in ";{}" and not depth:
            return None, None
        i -= 1
    return None, None


def statement(text, pos):
    """The text of the statement around pos, up to pos."""
    return text[max(text.rfind(c, 0, pos) for c in ";{}") + 1:pos]


ACCESS = {("rd8", 0), ("rd16", 0), ("rd32", 0), ("rdf32", 0), ("wr8", 0), ("wr16", 0), ("wr32", 0), ("wrf32", 0)}
TYPE = r"(?:u8|u16|u32|s8|s16|s32|int|char|short|long)"
TYPES = r"(?:(?:const|static|unsigned|signed|volatile)\s+)*%s\s*\**\s*" % TYPE
DECL = re.compile(r"\s*%s[\w\s,*]*" % TYPES)  # u32 r0, r1, r2 (a declaration so far)
LOST = re.compile(r"\(\s*(?:u8|s8|u16|s16|f32|f64|float|double)\s*\)\s*$")  # the view bit doesn't survive
STEP = re.compile(r"\s*(?:[-+|&^]=|\+\+|--)")


def accessors(text):
    """{(call, argument)}: the tc_rt.h accesses' address argument, and the u32 parameters of the file's own helpers
    that are only ever access addresses there (static void inc16(u32 addr) { wr16(addr, ...); })."""
    names = set(ACCESS)
    for m in re.finditer(r"static\s+[\w\s\*]+?\b(\w+)\s*\(([^)]*)\)\s*\{", text):
        depth, j = 1, m.end()
        while depth and j < len(text):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        body = text[m.end():j]
        for i, param in enumerate(m.group(2).split(",")):
            p = re.fullmatch(r"\s*u32\s+(\w+)\s*", param)
            if p and all(use(body, at, names)[0] == "access" for at in uses(body, p.group(1), 0)):
                names.add((m.group(1), i))
    return names


def uses(text, var, start):
    """[position] of the occurrences of var after start that read it, up to where it is next assigned anew (a plain
    `var = ...` without var on the right). Linear in the text: a loop's way back to the top is not followed."""
    for u in re.finditer(r"(?<![\w.>])%s\b" % re.escape(var), text[start:]):
        at = start + u.start()
        rest = text[start + u.end():]
        if re.match(r"\s*=(?!=)", rest):
            rhs = rest[:rest.find(";")] if ";" in rest else rest
            if not re.search(r"(?<![\w.>])%s\b" % re.escape(var), rhs):
                return  # killed
            continue
        if STEP.match(rest) or DECL.fullmatch(statement(text, at)):
            continue  # stepped or declared here: not a read that keeps it
        yield at


def use(text, pos, names):
    """How a pointer at pos is used: ("access",) for an access address or a cast that drops the view bit, ("value",)
    when kept (passed, stored, returned or compared), or ("local", var, end) when assigned to var, whose uses from
    end are then followed."""
    if LOST.search(text, max(0, pos - 20), pos):
        return ("access",)
    name, arg = enclosing(text, pos)
    if (name, arg) in names:
        return ("access",)
    if name is not None:
        return ("value",)
    m = re.fullmatch(r"\s*(?:%s)?(\w+)\s*(?:[-+|]?=)(?!=)[^=]*" % TYPES, statement(text, pos))
    if not m:
        return ("value",)
    end = text.find(";", pos)
    return ("local", m.group(1), len(text) if end < 0 else end + 1)


def reaches_value(text, var, start, names, seen):
    """True when var, assigned a pointer at start, is used as a value before it is assigned anew."""
    if (var, start) in seen or len(seen) > 200:
        return False
    seen.add((var, start))
    for at in uses(text, var, start):
        how = use(text, at, names)
        if how[0] == "value" or (how[0] == "local" and reaches_value(text, how[1], how[2], names, seen)):
            return True
    return False


def literals(text):
    """{value: [sites, value sites]} of the hex integer literals in C text, comments and strings left out. A value
    site is one whose pointer the C keeps as a value: passes it to a call, stores it, returns or compares it, there
    or through the locals it is assigned to. The runtime folds an access, so a literal that only ever becomes an
    access address (rd32(0x8c540f94u + i), T = 0x8c2a26e0u; rd32(T + 4u)) reaches the same RAM in either view; so
    does one stored through a (u8) or (u16) cast."""
    out, text = {}, COMMENT.sub(" ", text)
    names = accessors(text)
    for m in HEX.finditer(text):
        n = out.setdefault(int(m.group(1), 16), [0, 0])
        n[0] += 1
        if ram(int(m.group(1), 16)):
            how = use(text, m.start(), names)
            n[1] += how[0] == "value" or (how[0] == "local" and reaches_value(text, how[1], how[2], names, set()))
    return out


def check(words, lits, ranges=()):
    """-> [{"kind", "pool", "c", "sites", "values"}]: the C literals (literals()) that write a pool word, or an address
    just above one, in the other view. Pool words in the code ranges (a call target) are left out."""
    rows = []
    for p in sorted(words):
        if any(lo <= p | VIEW < hi or lo <= p & ~VIEW < hi for lo, hi in ranges):
            continue
        if p ^ VIEW in lits:
            n = lits[p ^ VIEW]
            rows.append({"kind": "view", "pool": f"{p:08x}", "c": f"{p ^ VIEW:08x}", "sites": n[0], "values": n[1]})
    for v, n in sorted(lits.items()):
        if not ram(v) or v in words or (v ^ VIEW) in words or any(lo <= v < hi for lo, hi in [*ranges, STACK]):
            continue
        if any((v >> 31) == (p >> 31) for p in words):
            continue  # the pool has a word in this view itself: an offset of that one
        base = [p for p in words if 0 < (v ^ VIEW) - p < SPAN]
        if base:
            p = max(base)
            rows.append({"kind": "offset", "pool": f"{p:08x}", "c": f"{v:08x}", "sites": n[0], "values": n[1],
                         "delta": f"0x{(v ^ VIEW) - p:x}"})
    return rows


def scan(ws):
    from core import asm_pool, paths, ret_use
    rp = ret_use.Project(ws)
    ranges = asm_pool.code_ranges(paths.CODE_RANGES)
    rows, counts = [], {"functions": 0, "with_pool_ram": 0}
    for matched in sorted(paths.MATCHED.glob("*.c")):
        a = matched.stem
        ins = rp.insns(a)
        if not ins:
            continue
        counts["functions"] += 1
        words = pool_words(ins)
        if not words:
            continue
        counts["with_pool_ram"] += 1
        hits = check(words, literals(matched.read_text(encoding="utf-8", errors="replace")), ranges)
        if hits:
            rows.append({"addr": a, "hits": hits})
    return rows, counts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    ws = use_project(a.project)
    rows, counts = scan(ws)
    req = {r["addr"]: [f"{h['kind']}: pool {h['pool']}, C {h['c']}" + (f" (+{h['delta']})" if "delta" in h else "")
                       + f" x{h['values']}" for h in r["hits"] if h["values"]]
           for r in rows if any(h["values"] for h in r["hits"])}
    summary = {"project": str(ws), **counts, "other_view": len(rows), "access_only": len(rows) - len(req),
               "requeue": len(req), "requeue_offset_only": sum(
                   all(h["kind"] == "offset" for h in r["hits"] if h["values"]) for r in rows if r["addr"] in req),
               "sites": sum(h["sites"] for r in rows for h in r["hits"]),
               "value_sites": sum(h["values"] for r in rows for h in r["hits"])}
    print(json.dumps(summary))
    for addr, why in sorted(req.items()):
        print(f"  {addr}  " + "; ".join(why))
    if a.write:
        out = ws / "state" / "ptr_views"
        out.mkdir(parents=True, exist_ok=True)
        (out / "requeue.txt").write_text("".join(f"{addr} {'; '.join(why)}\n" for addr, why in sorted(req.items())),
                                         encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
        print(f"wrote {out}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
