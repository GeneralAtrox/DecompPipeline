"""refine.py requests: the design gaps readable agents reported, grouped for the designers (batch 20).

A readable agent that cannot express something says so in its final message (core/refine.py TASK: `MISSING FIELDS:
...`; context.md: `MISSING GLOBAL 0x..`), or explains a FAIL in prose ("registry name for 0x8c041014 missing", "the
one-parameter prototype Entity_ResetRot(s32 entity_id) cannot carry them"). Those requests were scattered over
transcripts. This collects them from the functions that did not become readable: status failed or stale, and the
attempts of a redo that failed (`redo_failed`, the old file kept). Each attempt's whole final message comes from its
transcript (runs/ADDR/readable/attempt-NN/transcript.jsonl); the ledger keeps only its last 400 characters, the
fallback. A request is one of
  field      a struct field to add: "MISSING FIELDS: RigidBody +0x124 (f32), Viewport +0x1b4" (by type),
  name       an address without a registry name (a global or a function used as a value),
  prototype  a prototype that cannot carry what raw.c passes or returns (by function name),
grouped by system (state/systems.json) and, within it, by the type, address or function they need.
"""
import json
import re

from . import paths

ADDR = r"0x(8c[0-9a-fA-F]{6})"
FIELDS = re.compile(r"MISSING FIELDS?\s*:\s*(.+?)(?=\s*(?:MISSING \w+\s*:|RESULT:|\n|$))")
NAME = [re.compile(rf"MISSING GLOBALS?\s*:?\s*{ADDR}"),
        re.compile(rf"(?:registry )?name for {ADDR}[^.;]{{0,30}}?missing", re.I),
        re.compile(rf"no (?:registry )?name (?:for|yet)[^.;]{{0,40}}?{ADDR}", re.I),
        re.compile(rf"{ADDR}[^.;]{{0,60}}?\b(?:unnamed|has no (?:registry )?name|is not named|without a name)", re.I),
        re.compile(rf"\b(?:unnamed|no registry name)\b[^.;]{{0,60}}?{ADDR}", re.I)]
CLAUSE = r"(?:[^.;\n]|\.(?=\S))"  # a "." inside a word (game.h, raw.c) does not end the clause
PROTO = re.compile(rf"{CLAUSE}*\bprototype\b{CLAUSE}*?\b(?:cannot carry|can't carry|has no|lacks|does not take|"
                   rf"doesn't take|cannot (?:take|express|pass|return)){CLAUSE}*", re.I)
FUNC = re.compile(r"\b((?:fn_8c[0-9a-f]{6})|(?:[A-Z][A-Za-z0-9]*_[A-Za-z0-9_]+)|(?:[a-z]+[A-Z]\w+))\b")
TYPE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")
NONE = re.compile(r"^\s*(?:none|n/?a|-)\b", re.I)
STACK_LO = 0x8CFF0000  # a guest stack address is never a missing name


def final_messages(addr, row):
    """[(attempt n, whole final message)] of the attempts that count: a failed or stale row's own attempts, and the
    attempts of a failed redo."""
    want = []
    if row.get("status") in ("failed", "stale"):
        want += [a.get("n") for a in row.get("attempts") or []]
    want += list((row.get("redo_failed") or {}).get("attempts") or [])
    by_n = {a.get("n"): a for a in row.get("attempts") or []}
    out = []
    for n in sorted({n for n in want if isinstance(n, int)}):
        text = transcript_result(paths.RUNS / addr / "readable" / f"attempt-{n:02d}" / "transcript.jsonl")
        if text is None:
            text = (by_n.get(n) or {}).get("final_message") or ""
        out.append((n, text))
    return out


def transcript_result(path):
    """The "result" text of a transcript's result event, or None."""
    if not path.exists():
        return None
    text = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"type"' in line and '"result"' in line:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "result" and isinstance(e.get("result"), str):
                text = e["result"]
    return text


def clause(text, start, end, width=160):
    """The sentence around text[start:end], at most width characters."""
    lo = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start), text.rfind("\n", 0, start)) + 1
    hi = min([i for i in (text.find(". ", end), text.find("; ", end), text.find("\n", end)) if i >= 0] or [len(text)])
    return " ".join(text[lo:hi].split())[:width]


def parse(text, own=""):
    """[(kind, key, detail)] of one final message: ("field", type, item), ("name", addr, clause), ("prototype",
    function, clause). own: the function's address (a prototype request that names no function is about its own,
    or about the address it names)."""
    out = []
    for m in FIELDS.finditer(text):
        body = m.group(1).strip().rstrip(".")
        if NONE.match(body):
            continue
        for item in re.split(r";\s*|,\s*(?=[A-Z][A-Za-z0-9_]*\b)", body):
            item = " ".join(item.split())
            t = TYPE.search(item)
            if item:
                out.append(("field", t.group(1) if t else "(other)", item[:120]))
    for rx in NAME:
        for m in rx.finditer(text):
            if int(m.group(1), 16) < STACK_LO:
                out.append(("name", m.group(1).lower(), clause(text, m.start(), m.end())))
    for m in PROTO.finditer(text):
        s = m.group(0)
        k = s.lower().find("prototype")
        names = [f.group(1) for f in FUNC.finditer(s)]
        before = [f.group(1) for f in FUNC.finditer(s[:k])]
        addr = re.search(ADDR, s)
        name = ((before[-1] if before else names[0]) if names else
                f"fn_{addr.group(1).lower()}" if addr else f"(its own: {own})")
        out.append(("prototype", name, " ".join(s.split())[:160]))
    return out


def collect(sys_list, targets=()):
    """{system id: {kind: {key: [{"addr", "attempt", "detail"}]}}} for the functions of the targets (system ids,
    addresses or @file entries already expanded; none: every function with a request)."""
    from . import refine
    led = refine.load_ledger()
    sys_of = {a: s["id"] for s in sys_list for a in s["functions"]}
    by_id = {s["id"]: s["functions"] for s in sys_list}
    want = {paths.norm_addr(x) for t in targets for x in by_id.get(t, [t])} if targets else None
    groups = {}
    for addr, row in sorted(led.items()):
        if want is not None and addr not in want:
            continue
        seen = set()
        for n, text in final_messages(addr, row):
            for kind, key, detail in parse(text, addr):
                if (kind, key, detail) in seen:
                    continue
                seen.add((kind, key, detail))
                sysg = groups.setdefault(sys_of.get(addr, "(no system)"), {})
                sysg.setdefault(kind, {}).setdefault(key, []).append({"addr": addr, "attempt": n, "detail": detail})
    return groups


def report(groups):
    """Text lines: per system, per kind, per key (with the functions asking), then totals by kind."""
    lines, totals = [], {}
    for sid in sorted(groups):
        g = groups[sid]
        fns = sorted({r["addr"] for k in g.values() for rs in k.values() for r in rs})
        lines.append(f"{sid}: {len(fns)} function(s) with requests")
        for kind in ("field", "name", "prototype"):
            for key, rows in sorted(g.get(kind, {}).items()):
                totals.setdefault(kind, set()).add(key)
                users = sorted({r["addr"] for r in rows})
                details = sorted({r["detail"] for r in rows})
                lines.append(f"  {kind:<9} {key}: {' | '.join(details[:4])}{' | ...' if len(details) > 4 else ''} "
                             f"({' '.join(users)})")
    lines.append("requests: " + ", ".join(f"{len(totals.get(k, ()))} {k} key(s)" for k in ("field", "name",
                                                                                         "prototype")))
    return lines
