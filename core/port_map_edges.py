"""The call edges a workspace records for one function, from every source it has, and where those sources disagree.

No source is trusted alone: asm.txt's `; call` notes, meta.json `callees`, state/graph.tsv calls. A pointer reference
(an asm `(function X)` pool note, graph.tsv fptrs) is kept apart from a call. A function named by the raw or readable
text and by no call source is kept apart again: the walk follows it, and it is reported as an edge the sources lack.
A pointer held only in a data table is in none of them, and an indirect call site has no target here.

Pure (no core.paths). Reads only.

Nearest existing: core/sysassign.py and tools/sdk_classify.read_graph (read graph.tsv and meta.json into one set for
their own purposes, and never compare the sources) - the comparison is what catches a missing edge.
"""
import collections
import re

from .raw_names import mask

ASM_CALL = re.compile(r";\s*call\s+([0-9a-f]{8})\b")
ASM_FUNC = re.compile(r"\(function\s+([0-9a-f]{8})\)")
ASM_INDIRECT = re.compile(r"^\s*([0-9a-f]{8})\s+_?(?:jsr\s+@r\d+|jmp\s+@r\d+|bctrl|bcctrl|bctr|bcctr|blrl)\b")
HEX_WORD = re.compile(r"\b0x([0-9a-fA-F]{8})")
RAW_FN = re.compile(r"\bfn_([0-9a-f]{8})\b")
WORD = re.compile(r"\b[A-Za-z_]\w*\b")


def _text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def edges(ws, addr):
    """Every edge the workspace knows out of addr: {"calls": {callee: sources}, "refs": {target: sources}, "text":
    {target: sources}, "indirect": [site], "have": the call sources this function has}."""
    calls, refs, text = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    have, indirect = set(), []
    asm = _text(ws.path / "packets" / addr / "asm.txt")
    if asm is not None:
        have.add("asm")
        for ln in asm.splitlines():
            m = ASM_CALL.search(ln)
            if m:
                calls[m.group(1)].add("asm")
            elif ASM_INDIRECT.match(ln):
                indirect.append(ASM_INDIRECT.match(ln).group(1))
            for t in ASM_FUNC.findall(ln):
                refs[t].add("asm")
    meta = ws.meta(addr)
    if meta is not None and "callees" in meta:
        have.add("meta")
        for c in meta["callees"] or []:
            calls[c["addr"].lower()].add("meta")
    if addr in ws.graph:
        have.add("graph")
        direct, pointers = ws.graph[addr]
        for t in direct:
            calls[t].add("graph")
        for t in pointers:
            refs[t].add("graph")
    for source, path, named in (("raw", ws.raw_file(addr), False), ("readable", ws.readable_file(addr), True)):
        body = _text(path)
        if body is None:
            continue
        code = mask(body)[0]
        found = set(RAW_FN.findall(code)) | {x.lower() for x in HEX_WORD.findall(code)}
        if named:
            found |= {ws.by_name[w] for w in set(WORD.findall(code)) if w in ws.by_name}
        for t in found:
            hit = ws.function(t)
            if hit and hit != addr:
                text[hit].add(source)
    calls.pop(addr, None)  # a recursive call adds nothing to a tree
    refs = {t: s for t, s in refs.items() if t not in calls and t != addr}
    text = {t: s for t, s in text.items() if t not in calls and t not in refs}
    return {"calls": dict(calls), "refs": refs, "text": text, "indirect": indirect, "have": have}


def edge_gaps(ws, addr, found):
    """The ways this function's edges fail to agree. Sources that do not exist for it are not held against it."""
    gaps = []
    for callee, sources in sorted(found["calls"].items()):
        missing = found["have"] - sources
        if missing:
            gaps.append({"kind": "source_disagrees", "from": addr, "to": callee, "in": sorted(sources),
                         "missing_from": sorted(missing)})
    for target, sources in sorted(found["text"].items()):
        gaps.append({"kind": "text_only", "from": addr, "to": target, "in": sorted(sources)})
    counted = (ws.catalog.get(addr) or {}).get("callees")
    if isinstance(counted, int) and counted > len(found["calls"]):
        gaps.append({"kind": "catalog_counts_more", "from": addr, "catalog": counted, "found": len(found["calls"])})
    if not found["have"]:
        gaps.append({"kind": "no_edge_data", "from": addr})
    return gaps
