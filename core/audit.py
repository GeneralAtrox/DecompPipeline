"""decomp.py audit: group each project's unmatched functions by blocker kind.

The kinds are data: target.json "blocker_kinds", a list in priority order (the first kind that matches wins) of
  {"id", "status": [ledger statuses it applies to], "fix": what unblocks it, and one or more conditions:
   "detail": regex searched in verdict_detail,
   "diff": regex searched in the last attempt's first-diff lines ("diff_all": true = every line must match),
   "stage": regex on the last attempt's stage (lint, build, run, compare),
   and optionally "fixed_at": when its latest fix landed (core/stale_skips.py requeues the rows judged before)}.
A kind matches when the status is listed and any of its conditions holds. A finished function (failed, skipped,
error) that no kind matches is UNDECLARED: the exit code is then 2, so a new kind of blocker gets noticed and
declared (or fixed) instead of hiding in a long tail. Functions still on their way (queued, running, stopped,
not started) are counted as pending, not as blockers.
This is separate from target.json "known_blockers" (core/job.py parks functions with those while they run).
"""
import collections
import json
import pathlib
import re

TOOL = pathlib.Path(__file__).resolve().parents[1]
FINISHED = ("failed", "skipped", "error")


def norm(s):
    """A reason with addresses and numbers folded, for grouping undeclared ones."""
    return re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{6,8}\b|\d+(\.\d+)?", "#", s)


def load(ws, ledger=None):
    """(project.json, blocker kinds, ledger functions, functions to decompile {addr: catalog entry + tier, "extra"}):
    the game ranges plus project.json "extra_functions" (core/scope.py; "extra" marks those). ledger: the caller's
    already-read ledger functions (the scorecard's), not a second parse of the same file."""
    from . import hw_ops, scope
    from .orch import tier
    ws = pathlib.Path(ws)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    target = json.loads((TOOL / "targets" / proj["target"] / "target.json").read_text(encoding="utf-8"))
    if ledger is None:
        ledger = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
    cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
    sc = scope.Scope(proj, ws)
    listed = hw_ops.listed(dict(target, **proj))  # project.json "hw_ops" (target.json "hw_reads")
    game = {f["addr"]: dict(f, tier=tier(f, listed=listed), extra=sc.is_extra(f["addr"]))
            for f in cat if f["addr"] in sc}
    return proj, target.get("blocker_kinds") or [], ledger, game


def last_attempt(rec):
    atts = rec.get("attempts") or []
    own = [a for a in atts if a.get("model") not in ("import", "previous", "translated")]  # attempt 0: no agent
    return (own or atts or [{}])[-1]


def diff_lines(att):
    return [d for fd in att.get("first_diffs") or [] for d in fd.get("diffs") or []]


def classify(rec, kinds):
    """The id of the first declared kind matching this ledger record, or None."""
    st, det = rec.get("status"), rec.get("verdict_detail") or ""
    att = last_attempt(rec)
    lines = diff_lines(att)
    for k in kinds:
        sts = k.get("status") or []
        sts = [sts] if isinstance(sts, str) else sts
        if sts and st not in sts:
            continue
        if k.get("detail") and re.search(k["detail"], det):
            return k["id"]
        if k.get("diff") and lines:
            hit = [bool(re.search(k["diff"], x)) for x in lines]
            if all(hit) if k.get("diff_all") else any(hit):
                return k["id"]
        if k.get("stage") and re.search(k["stage"], att.get("stage") or ""):
            return k["id"]
    return None


def reason(rec):
    """verdict_detail plus the last attempt's first diff line: what an undeclared kind is shown as."""
    first = (diff_lines(last_attempt(rec)) or [""])[0]
    return ((rec.get("verdict_detail") or "") + (f" | {first}" if first else "")).replace("\n", " ")


def audit(ws):
    proj, kinds, ledger, game = load(ws)
    out = {"name": proj.get("name"), "kinds": collections.defaultdict(list), "pending": collections.Counter(),
           "undeclared": [], "matched": 0}
    pri = set(proj.get("priority") or [])
    for a, f in sorted(game.items()):
        r = ledger.get(a) or {}
        st = r.get("status") or "not started"
        hw = st == "not started" and f["tier"] == "excluded" and a not in pri
        if f["extra"]:  # counted with the rest, and apart (project.json extra_functions)
            out.setdefault("extra", collections.Counter())["matched" if st == "matched" else
                                                           "excluded" if hw else "open"] += 1
        if st == "matched":
            out["matched"] += 1
        elif st not in FINISHED:
            out["pending"]["excluded" if hw else st] += 1
        else:
            k = classify(r, kinds)
            if k:
                out["kinds"][k].append(a)
            else:
                out["undeclared"].append({"addr": a, "status": st, "reason": reason(r)[:240]})
    return out, kinds


def label(ws):
    """Short column name: 'Spawn:ovl' for .../Spawn In the Demon's Hand/decomp/ovl (or .../decomp_ovl),
    'ToyCo:main' for .../ToyCommander/decomp."""
    p = pathlib.Path(ws)
    if p.parent.name == "decomp":  # a game folder holding several workspaces under decomp/
        return f"{p.parent.parent.name.split()[0][:5]}:{p.name}"
    return f"{p.parent.name.split()[0][:5]}:{p.name.removeprefix('decomp').lstrip('_') or 'main'}"


def table(results, kinds):
    """Combined text table: one column per project."""
    names = [label(ws) for ws, _ in results]
    w = max([22] + [len(k["id"]) for k in kinds])
    head = f"{'kind':{w}} " + " ".join(f"{n[:13]:>13}" for n in names)
    lines = [head, "-" * len(head)]

    def row(name, vals):
        lines.append(f"{name:{w}} " + " ".join(f"{v if v else '.':>13}" for v in vals))

    row("matched", [r["matched"] for _, r in results])
    if any(r.get("extra") for _, r in results):  # extras are in every row; these two count them apart
        row("extra: matched", [(r.get("extra") or {}).get("matched", 0) for _, r in results])
        row("extra: open", [(r.get("extra") or {}).get("open", 0) for _, r in results])
    for k in kinds:
        vals = [len(r["kinds"].get(k["id"], [])) for _, r in results]
        if any(vals):
            row(k["id"], vals)
    row("UNDECLARED", [len(r["undeclared"]) for _, r in results])
    for st in sorted({s for _, r in results for s in r["pending"]}):
        row(f"pending: {st}", [r["pending"].get(st, 0) for _, r in results])
    return lines


def main(projects, as_json=False, verbose=False):
    """Print the audit of one or more workspaces; returns 2 if any finished function is UNDECLARED, else 0."""
    results, kinds = [], []
    for ws in projects:
        r, ks = audit(ws)
        results.append((ws, r))
        kinds += [k for k in ks if k["id"] not in {x["id"] for x in kinds}]  # projects may use different targets
    if as_json:
        print(json.dumps({str(ws): r for ws, r in results}, indent=1))
    else:
        print("\n".join(table(results, kinds)))
        fixes = {k["id"]: k["fix"] for k in kinds}
        used = [k for k in fixes if any(r["kinds"].get(k) for _, r in results)]
        if used:
            print("\nfixes:")
            for k in used:
                print(f"  {k}: {fixes[k]}")
        if verbose:
            for ws, r in results:
                for k, addrs in r["kinds"].items():
                    print(f"\n{label(ws)} {k} ({len(addrs)}): {' '.join(addrs)}")
        und = [(ws, u) for ws, r in results for u in r["undeclared"]]
        if und:
            print(f"\nUNDECLARED ({len(und)}): declare a kind in target.json blocker_kinds, or fix the cause")
            groups = collections.defaultdict(list)
            for ws, u in und:
                groups[(u["status"], norm(u["reason"])[:110])].append(f"{label(ws)}:{u['addr']}")
            for (st, why), items in sorted(groups.items(), key=lambda x: -len(x[1])):
                print(f"  {len(items):4}  {st:8} {why}")
                print(f"        {' '.join(items[:8])}{' ...' if len(items) > 8 else ''}")
    return 2 if any(r["undeclared"] for _, r in results) else 0
