"""The proof gates' third reason (core/unchecked_args): matched functions with a call whose arguments no judge
compares, marked "unlisted call args unchecked" at raw and readable, and the marks lifted once a re-prepared harness
has the CA lines or a re-judge compared the callee's proven signature (core/unchecked_args.gate).

usage:
  python tools/args_marks.py --project DIR [--project DIR ...] [--scope | --addrs A,B|@FILE | --from FILE]
                             [--lift A,B|@FILE] [--apply --backup DIR]

Per workspace: re-reads the harnesses (every matched function's, or --addrs, or with --scope only those the stamp
scope unlisted_call_args names from the image's words, plus the marked ones; a full scan reads every expected.txt) or
takes --from FILE's "addr target ..." lines (a workspace without harnesses); --lift drops the marks of functions
re-judged elsewhere on a harness with the CA lines (a workspace without harnesses: the copies of a reference run's
state), or on their callees' proven signatures (the "rejudge" targets, kept in the file's "rejudged" for later
runs). Prints the moves:
  marked / lifted      functions that gain or lose a mark (state/unchecked_args.json)
  raw                  matched rows that turn "unproven" {reason, targets, since, source} in the ledger (the
                       ledger-truth gate's shape; a row another gate marked keeps its mark), and so leave verified
                       (core/proof.scan's state unchecked_args)
  readable             readable rows that turn `unproven` ("args" in their unproven; core/proof.readable_state
                       keeps them so at the next verify)
  raw_lifted, readable_lifted   the same undone for a function no longer marked
--apply copies the ledger, readable.json and unchecked_args.json to --backup DIR/<workspace label>/ and writes all
three under their locks (filelock.update_json: a locked read-modify-write of the rows, so other writers' rows and
marks stay). Exit 0.

Nearest existing: tools/proof_backlog.py (restatus, truth) - the same kind of marks, but its file is being changed by
the ledger-truth session (2026-11-07) and restatus skips rows without a readable file; this command can move there.
"""
import argparse
import datetime
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402
from core import filelock, proof, unchecked_args as ua  # noqa: E402  (pure: no core.paths)


def label(ws):
    """The workspace's backup folder name: its parent's and its own (Spawn's decomp/main, decomp/ovl ...)."""
    parts = [x for x in ws.parts[-3:] if x != "decomp"][-2:]
    return "_".join(parts).replace(" ", "_").replace("'", "")


def mark(ws, apply=False, addrs=None, from_file=None, scope=False, backup=None, lift=()):
    """{"marked", "lifted", "raw", "readable", "raw_lifted", "readable_lifted": [addr]}; apply: written."""
    st = ws / "state"
    old, new = ua.marks(ws, addrs, from_file, scope)
    rj, now = ua.rejudged(ws), datetime.datetime.now().isoformat(timespec="seconds")
    for a in lift:
        for t in (new.pop(a, None) or {}).get("rejudge") or []:
            rj.setdefault(a, {})[t] = now  # re-judged elsewhere on t's proven signature: kept across runs
    moves = {"marked": sorted(set(new) - set(old)), "lifted": sorted(set(old) - set(new)), "raw": [], "readable": [],
             "raw_lifted": [], "readable_lifted": [],
             "kinds": {k: sum(bool(x.get(k)) for x in new.values()) for k in ("none", "unsigned", "rejudge")}}
    led, rd, mk = st / "ledger.json", st / "readable.json", st / ua.FILE
    since = datetime.date.today().isoformat()
    if not apply:
        ua.mark_ledger(json_rows(led).get("functions") or {}, new, moves, since)
        ua.mark_readable(json_rows(rd), new, moves, proof.state_note)
        return moves
    backup.mkdir(parents=True, exist_ok=True)
    for f in (led, rd, mk):
        if f.exists():
            shutil.copy2(f, backup / f.name)
    filelock.update_json(mk, lambda d: (d.clear(), d.update(functions=new, rejudged=rj)), {"functions": {}})
    filelock.update_json(led, lambda d: ua.mark_ledger(d.get("functions") or {}, new, moves, since),
                         dump=filelock.dumps_rows)
    if rd.exists():
        filelock.update_json(rd, lambda d: ua.mark_readable(d, new, moves, proof.state_note),
                             dump=lambda d: filelock.dumps_rows(d, None))
    return moves


def listed(arg):
    """A,B or @FILE (whitespace-separated) as a list of lower-case addresses; None without one."""
    if not arg:
        return None
    return [x.lower() for x in (pathlib.Path(arg[1:]).read_text(encoding="utf-8").split() if arg.startswith("@")
                                else arg.split(","))]


def json_rows(path):
    return json.loads(filelock.read_text(path)) if path.exists() else {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--addrs")
    ap.add_argument("--from", dest="from_file")
    ap.add_argument("--scope", action="store_true")
    ap.add_argument("--lift")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup")
    a = ap.parse_args()
    if a.apply and not a.backup:
        sys.exit("--apply needs --backup DIR (the game's .work): the ledger and readable.json are copied there first")
    addrs, lift = listed(a.addrs), listed(a.lift) or ()
    for p in a.project:
        ws = workspace(p)
        if not (ws / "state" / "ledger.json").exists():
            print(f"{label(ws)}: no state/ledger.json, skipped")
            continue
        m = mark(ws, a.apply, addrs, a.from_file, a.scope, pathlib.Path(a.backup) / label(ws) if a.apply else None,
                 lift)
        print(f"{label(ws)}: {ua.REASON}: marked {len(m['marked'])}, lifted {len(m['lifted'])}; raw verified -> "
              f"unproven {len(m['raw'])}, back {len(m['raw_lifted'])}; readable -> unproven {len(m['readable'])}, "
              f"back {len(m['readable_lifted'])}"
              + (f"; marks now {len(ua.read(ws))}" if a.apply else "  (dry run: --apply --backup DIR writes them)"))
        print("  marks by kind: " + ", ".join(f"{k} {n}" for k, n in m["kinds"].items()))
        for k in ("marked", "lifted", "raw", "readable", "raw_lifted", "readable_lifted"):
            if m[k]:
                print(f"  {k}: {' '.join(m[k])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
