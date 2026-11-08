"""Which functions' C a translator change moves: translate every harnessed function of a workspace with two copies of
sh2translate (before and after the change) and compare the C by sha256. Nothing is built or judged, so it is quick;
the functions it lists are the ones to judge again (translate_backlog --only @FILE), and every other function keeps
its verdict, its C being byte for byte the same.

Nearest existing: tools/dp_judge.py and DP's tools/translate_backlog.py - both translate and judge every function;
this only translates (in process, as targets/saturn_sh2/translate.py maps a meta.json) so a fix re-judges just the
functions whose C changed.

usage: python tools/cdiff.py --ws <workspace> --old <translator dir> --new <translator dir> --target-json FILE
           --out <changed.txt> [--json <rows.json>] [--jobs N]
  A translator dir is the folder holding sh2translate/ (targets/saturn_sh2/translator, or an export of another commit).
  --out: one address per line, the functions whose C differs (or that translate on one side only).
  --json: {addr: {"old": sha256 | why, "new": sha256 | why}} for every function, the record of the unchanged ones.
  --jobs: worker processes (default 4); a function taking over 20 s to translate is named on stderr.
"""
import argparse
import concurrent.futures as cf
import hashlib
import importlib
import importlib.util
import json
import pathlib
import sys
import time

TARGET = pathlib.Path(__file__).resolve().parents[2]  # targets/saturn_sh2


def load(repo):
    """The translate module of the sh2translate under repo (a fresh import: two copies in one process)."""
    for k in [k for k in sys.modules if k == "sh2translate" or k.startswith("sh2translate.")]:
        del sys.modules[k]
    sys.path.insert(0, str(repo))
    try:
        return importlib.import_module("sh2translate.translate")
    finally:
        sys.path.remove(str(repo))


def entries_of(bodies):
    p = pathlib.Path(bodies)
    if not p.is_file():
        return set()
    return {int(ln.split()[0], 16) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def shas(mod, ws, addrs, tj, entries):
    """{addr: sha256 of the C, or "none: <why>" when there is no translation} with one translator copy."""
    facts = mod.TargetFacts.from_target_json(tj)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    img = mod.Image((ws / proj["binary"]).read_bytes(), facts.image_base)
    out = {}
    spec = importlib.util.spec_from_file_location("sat_translate", TARGET / "translate.py")
    tx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tx)
    ranges = tx.body_ranges(ws / "state" / "function_bodies.txt")
    for x in addrs:
        t0 = time.time()
        meta = json.loads((ws / "harness" / x / "meta.json").read_text(encoding="utf-8"))
        body = tx.body_of(meta, int(x, 16), ranges)
        try:
            src = mod.translate_function(img, int(x, 16), mod.Signature.from_meta(meta), facts, body or None,
                                         entries=entries)
            out[x] = hashlib.sha256(src.encode("utf-8")).hexdigest()
        except mod.TranslateError as e:
            out[x] = f"none: {e}"
        except Exception as e:  # noqa: BLE001  (a crash is a result too: the stage reports it as no C)
            out[x] = f"crash: {type(e).__name__}: {e}"
        if time.time() - t0 > 20:
            print(f"{x}: {time.time() - t0:.0f} s to translate", file=sys.stderr, flush=True)
    return out


def both(old, new, ws, addrs, tj, entries):
    """(old shas, new shas) of addrs: one worker's share."""
    return shas(load(old), ws, addrs, tj, entries), shas(load(new), ws, addrs, tj, entries)


def main():
    ap = argparse.ArgumentParser()
    for k in ("--ws", "--old", "--new", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--target-json", default=str(TARGET / "target.json"))
    ap.add_argument("--json", default="")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    ws = pathlib.Path(a.ws)
    tj = json.loads(pathlib.Path(a.target_json).read_text(encoding="utf-8"))
    addrs = sorted(p.parent.name for p in (ws / "harness").glob("*/meta.json"))
    entries = entries_of(ws / "state" / "function_bodies.txt")
    old, new = {}, {}
    parts = [addrs[k::a.jobs * 4] for k in range(a.jobs * 4)]
    with cf.ProcessPoolExecutor(a.jobs) as ex:
        for o, n in ex.map(both, *zip(*[(str(pathlib.Path(a.old).resolve()), str(pathlib.Path(a.new).resolve()), ws,
                                         part, tj, entries) for part in parts])):
            old.update(o)
            new.update(n)
    changed = [x for x in addrs if old[x] != new[x]]
    pathlib.Path(a.out).write_text("".join(x + "\n" for x in changed), encoding="utf-8")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({x: {"old": old[x], "new": new[x]} for x in addrs}, indent=1)
                                        + "\n", encoding="utf-8")
    print(f"{ws.name}: {len(addrs)} function(s), {len(changed)} with changed C, {len(addrs) - len(changed)} the same")
    return 0


if __name__ == "__main__":
    sys.exit(main())
