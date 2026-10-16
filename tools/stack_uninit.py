"""Frame words functions read before anything writes them (core/stack_uninit.py), and pinning them to 0.

usage: python tools/stack_uninit.py --project DIR [ADDR|@FILE ...] [--pin] [--json FILE]
  (no --pin)  scan the packets' asm.txt (every packet, or the targets) and list each function that reads an
              uninitialized frame word, with the words and the instructions; counts the listings it cannot follow.
              Writes nothing.
  --pin       (targets required) add `genrange 0xADDR 0 0` lines (one per word, after a comment) to
              oracle/hints/ADDR.txt: the harness then holds the word at 0 in every vector (the rule of batch 27).
              The hints file is an oracle input, so the function's harness is no longer current: re-prepare it
              (tools/reprepare_stale.py --stamps, or the server's investigate) and judge its matched C again.
Needs a project TcInputs with genrange (TC and Spawn have it). Servers stopped for --pin.
"""
import argparse
import collections
import json
import pathlib
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import expand_addrs, use_project  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--pin", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import paths, stack_uninit
    targets = expand_addrs(a.targets) if a.targets else None
    if a.pin and not targets:
        ap.error("--pin needs targets (pinning is decided per function)")
    if a.pin and '"genrange"' not in (paths.INPUTS.read_text(encoding="utf-8") if paths.INPUTS.exists() else ""):
        ap.error(f"--pin: {paths.show(paths.INPUTS)} has no genrange hint (the target template's has none yet): its "
                 "prepare would fail on `bad hint line`; add genrange through the watcher gate first")
    dirs = [paths.PACKETS / t for t in targets] if targets else sorted(d for d in paths.PACKETS.iterdir() if d.is_dir())
    status, rows = collections.Counter(), []
    for d in dirs:
        res = stack_uninit.scan_packet(d)
        status["ok" if res["status"] == "ok" else "not followed"] += 1
        if res["status"] != "ok" and targets:
            print(f"{d.name}: {res['status']}")
        if res["reads"]:
            ws_ = stack_uninit.words(res)
            hp = paths.HINTS / f"{d.name}.txt"
            pins = stack_uninit.pinned(hp.read_text(encoding="utf-8") if hp.exists() else "")
            rows.append({"addr": d.name, "words": [f"{w:08x}" for w in ws_], "reads": res["reads"],
                         "pinned": [f"{w:08x}" for w in ws_ if w in pins]})
    print(f"{len(dirs)} listings: {status['ok']} followed, {status['not followed']} not followed "
          f"(computed jumps, fschg, r15 from a register)")
    print(f"{len(rows)} function(s) read an uninitialized frame word:")
    for r in rows:
        at = ", ".join(sorted({x["at"] for x in r["reads"]}))
        print(f"  {r['addr']}: words {' '.join(r['words'])} (read at {at})"
              + (f"; pinned {' '.join(r['pinned'])}" if r["pinned"] else ""))
    if a.pin:
        paths.HINTS.mkdir(parents=True, exist_ok=True)
        for r in rows:
            hp = paths.HINTS / f"{r['addr']}.txt"
            old = hp.read_text(encoding="utf-8") if hp.exists() else ""
            new = stack_uninit.pin_text(old, [int(w, 16) for w in r["words"]])
            if new:
                hp.write_text(new, encoding="utf-8", newline="\n")
                print(f"pinned in {paths.show(hp)}: re-prepare {r['addr']} and judge it again")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
