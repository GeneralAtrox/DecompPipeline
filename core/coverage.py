"""refine.py coverage: functions, callees and globals of some functions that the names registry has not named yet,
and named globals whose declared extents overlap.

A global address raw code references (harness meta "globals") counts as named only when it lies inside a named
global's declared extent, [base, base + sizeof(declaration)) (core/global_extents; before batch 18 anything within
0x1000 bytes after a named global counted, which hid real, separate globals). Every other address is listed as
without a name, with the named global it follows and how far past that global's end it lies. The overlap check runs
over the whole registry: a named global that starts inside another's extent is listed with both declarations (one
of them is wrong; the designers fix the data). The command exits 0 as before; the count of problems is returned.

Pointer constants (batch 20, core/raw_consts): an address the matched raw C passes, stores or compares as a value
(a string argument, a stored function address) needs a name too, and the harness "globals" never list it. A data
address no named global's extent holds and a catalog function used as data without a registry entry are counted;
code-region addresses used as values (literal-pool words or tables a register then reads) are only noted.
"""
from . import global_extents, paths, raw_consts, readable


def _fmt(reg, base):
    a = f"{base:08x}"
    return f"`{reg.globals[a]['decl']}` (0x{a})"


def excluded_functions():
    """Catalog functions of tier "excluded" (hardware registers or privileged CPU state: core/orch.tier). They are
    never queued, prepared or rewritten; the project keeps an intent note for each (tools/final_audit.py checks it).
    A function matched before the catalog flagged it is not one: a verified match is never dropped because the catalog
    learns more (Spawn 8c059c10 matched, then TcCatalog's flow walk found its AICA read; core/orch.keep_matched)."""
    from . import hw_ops
    cat = paths.read_json(paths.CATALOG) or {}
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    listed = hw_ops.listed(paths.config())  # project.json "hw_ops": a listed function is judged on the rest
    return {e["addr"] for e in (cat.get("functions", []) if isinstance(cat, dict) else cat) if (e.get("hw_refs") or
            e.get("sys_ops")) and (led.get(e["addr"]) or {}).get("status") != "matched"
            and not hw_ops.covered(listed.get(e["addr"]), e)}


def report(cfg, targets, log=print, facts=None):
    """targets: function addresses. Returns the number of problems (0: everything named, no overlap). facts: a dict
    to fill with the count of each kind (functions, callees, globals, overlaps, unsized, excluded, no_harness)."""
    reg = readable.Registry()
    hw = excluded_functions() & set(targets)
    no_meta = [a for a in targets if a not in hw and not (paths.HARNESS / a / "meta.json").exists()]
    log(f"targets: {len(targets)} function(s); {len(hw)} excluded hardware/system function(s) not counted (intent notes "
        "only, never prepared or rewritten)" + (f"; {len(no_meta)} without harness meta, not counted: "
                                                 + " ".join(no_meta[:20]) if no_meta else ""))
    targets = [a for a in targets if a not in hw]
    ext_by, unknown = global_extents.extents(cfg, reg, paths.BUILD / "rd_extents")
    ext = sorted((int(a, 16), s) for a, s in ext_by.items())
    ext += sorted((int(a, 16), 1) for a in unknown)  # a declaration the compiler refused covers its address only
    ext.sort()
    own, calls, globs = [], {}, {}
    for addr in targets:
        meta = paths.read_json(paths.HARNESS / addr / "meta.json")
        if meta is None:
            continue
        if addr not in reg.functions:
            own.append(addr)
        for c in meta["callees"]:
            if c["addr"] not in reg.functions:
                calls.setdefault(c["addr"], set()).add(addr)
        for g in meta["globals"]:
            if not global_extents.containing(ext, int(g, 16)):
                globs.setdefault(g, set()).add(addr)
    log(f"functions without a prototype: {len(own)}  " + " ".join(own))
    log(f"callees without a prototype: {len(calls)}")
    for c, users in sorted(calls.items()):
        log(f"  {c}  used by {' '.join(sorted(users))}")
    log(f"globals without a name: {len(globs)} (an address counts as named only inside a named global's extent)")
    for g, users in sorted(globs.items()):
        prev = global_extents.preceding(ext, int(g, 16))
        where = (f"; follows {_fmt(reg, prev[0])}, {prev[1]} bytes, ending 0x{prev[0] + prev[1]:08x} "
                 f"(+0x{int(g, 16) - prev[0] - prev[1]:x} past it)" if prev else "")
        log(f"  {g}  used by {' '.join(sorted(users))}{where}")
    vals = raw_consts.unnamed_values(cfg, reg, ext, targets, paths.MATCHED, paths.read_json(paths.CATALOG) or {},
                                     paths.FUNCTION_BODIES)
    log(f"pointer constants without a name: {len(vals['data'])} (data addresses the matched raw C passes, stores or "
        "compares as values: readable C needs a named global whose extent holds them)")
    for g, users in sorted(vals["data"].items()):
        prev = global_extents.preceding(ext, int(g, 16))
        where = (f"; follows {_fmt(reg, prev[0])}, {prev[1]} bytes (+0x{int(g, 16) - prev[0] - prev[1]:x} past "
                 "its end)" if prev else "")
        log(f"  {g}  used by {' '.join(sorted(users))}{where}")
    log(f"functions used as data without a name: {len(vals['function'])}")
    for g, users in sorted(vals["function"].items()):
        log(f"  {g}  used by {' '.join(sorted(users))}")
    code_users = {u for us in vals["code"].values() for u in us}
    log(f"note: {len(vals['code'])} code-region address(es) in {len(code_users)} function(s) are used as values "
        "(literal-pool words or tables a register then reads: readable C writes the loaded value, no name needed)")
    over = global_extents.overlaps(ext)
    log(f"overlapping named globals: {len(over)}")
    for a, sa, b in over:
        log(f"  {_fmt(reg, b)} starts inside {_fmt(reg, a)}, {sa} bytes (0x{a:08x}..0x{a + sa:08x})")
    if unknown:
        log(f"named globals whose size the headers do not give: {len(unknown)} (they cover their own address only)")
        for a, why in sorted(unknown.items()):
            log(f"  {a} `{reg.globals[a]['decl']}`: {why[:160]}")
    if facts is not None:
        facts.update(functions=len(own), callees=len(calls), globals=len(globs), overlaps=len(over),
                     unsized=len(unknown), excluded=len(hw), no_harness=len(no_meta), pointers=len(vals["data"]),
                     data_functions=len(vals["function"]), code_values=len(vals["code"]))
    return (len(own) + len(calls) + len(globs) + len(over) + len(unknown) + len(vals["data"])
            + len(vals["function"]))
