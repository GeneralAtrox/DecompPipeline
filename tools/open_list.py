"""Write a Markdown list of every function to decompile not matched yet (queued, running, failed, skipped, error, not
started), with its tier, size, attempts, blocker kind (decomp.py audit) and reason, from the ledger and catalog.
Functions to decompile: the game ranges plus project.json "extra_functions" (core/scope.py), marked "(extra)".

usage: python tools/open_list.py --project DIR [--out FILE.md]   (default: DIR/OPEN_FUNCTIONS.md)
Read-only on the project except for the output file.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import use_project  # noqa: E402

ORDER = ["running", "preparing", "waiting", "queued", "stopped", "not started", "failed", "skipped", "error"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import audit  # the server's own game-range and tier rules
    proj, kinds, ledger, game = audit.load(ws)
    out = pathlib.Path(a.out) if a.out else ws / "OPEN_FUNCTIONS.md"
    groups = {}
    for addr, f in sorted(game.items()):
        r = ledger.get(addr) or {}
        st = r.get("status") or "not started"
        if st == "matched" or (st == "not started" and f["tier"] == "excluded"):
            continue
        groups.setdefault(st, []).append((addr, f, r))
    port = proj.get("port")
    lines = [f"# {proj.get('name')}: functions not matched yet", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')} from state/ledger.json by tools/open_list.py.",
             f"To run one by hand: open http://localhost:{port}, type the address in the box next to "
             "**Investigate address**, and click the button. That starts an extra worker on that function.", ""]
    for st in sorted(groups, key=lambda s: ORDER.index(s) if s in ORDER else 99):
        rows = groups[st]
        lines += [f"## {st} ({len(rows)})", "", "| Address | Tier | Insns | Attempts | Kind | Reason |",
                  "|---|---|---|---|---|---|"]
        for addr, f, r in rows:
            kind = audit.classify(r, kinds) or ("UNDECLARED" if st in audit.FINISHED else "")
            why = audit.reason(r).replace("|", "/")[:160] if st in audit.FINISHED else \
                (r.get("verdict_detail") or "").replace("|", "/").replace("\n", " ")[:160]
            tier = f.get("tier", "") + (" (extra)" if f.get("extra") else "")
            lines.append(f"| `{addr}` | {tier} | {f.get('insns', '')} | {len(r.get('attempts') or [])} "
                         f"| {kind} | {why} |")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out, {st: len(v) for st, v in groups.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
