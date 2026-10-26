"""Finished rows whose blocker kind a later landing fixed: requeue them instead of letting them sit forever.

A blocker kind (target.json "blocker_kinds", core/audit.py) may carry "fixed_at": the local time
("YYYY-MM-DDTHH:MM:SS") its latest fix landed on master. The landing commit that fixes (part of) a kind sets it.
core/job.finish records on every unmatched row the kind it falls under and that kind's fixed_at at the time:
"blocker": {"kind", "fixed_at"}. A row is stale when its kind's fixed_at is newer than the one it was judged under;
for a row from before this record existed, newer than its "finished" time. Requeuing re-prepares and re-judges it
under today's checker. A row that is still blocked finishes again with the new record, so the same fix never
requeues it twice. Nothing about the verdict changes: a requeued row is judged exactly like a new one.
Only takeable functions are requeued: one in the excluded tier (hardware or system ops, core/orch.tier) or out of the
catalog's scope never runs, so requeuing it only parks it in the queue (Spawn main 8c024cbe, the CCR cache routine).
Spawn's 22 entry_regs rows (skipped 2026-10-11..17, before r0..r3 inputs and entry fr0..fr3 as FPU state) sat
skipped until this: 13 of them already had matched C staged in import/.
"""
from . import audit


def record(rec, kinds):
    """Stamp an unmatched row with the kind it falls under and that kind's fixed_at now (a matched row: none)."""
    k = audit.classify(rec, kinds) if rec.get("status") in audit.FINISHED else None
    if k:
        rec["blocker"] = {"kind": k, "fixed_at": next(x for x in kinds if x["id"] == k).get("fixed_at")}
    else:
        rec.pop("blocker", None)


def takeable():
    """The project's functions the queue can take: in the catalog's scope and not in the excluded tier."""
    from . import hw_ops, orch, paths
    sc, listed = paths.scope(), hw_ops.listed(paths.config())
    return {f["addr"] for f in (paths.read_json(paths.CATALOG) or {}).get("functions", [])
            if f["addr"] in sc and orch.tier(f, listed=listed) != "excluded"}


def stale(functions, kinds, takeable=None):
    """{addr: note} for each finished row whose kind was fixed after it was judged (one note per kind), of the
    takeable functions (a set; None: every row)."""
    fixed = {k["id"]: k.get("fixed_at") for k in kinds}
    out = {}
    for a, r in sorted(functions.items()):
        if r.get("status") not in audit.FINISHED or (takeable is not None and a not in takeable):
            continue
        k = audit.classify(r, kinds)
        if not k or not fixed.get(k):
            continue
        b = r.get("blocker") or {}
        under = (b.get("fixed_at") or "") if b.get("kind") == k and "fixed_at" in b else None
        if (under if under is not None else r.get("finished") or "") < fixed[k]:
            out[a] = f"blocker {k} fixed at {fixed[k]} (target.json blocker_kinds), after this row was judged"
    return out


def by_note(notes):
    """{note: [addr, ...]} of stale()'s result: one requeue per kind."""
    out = {}
    for a, n in notes.items():
        out.setdefault(n, []).append(a)
    return out
