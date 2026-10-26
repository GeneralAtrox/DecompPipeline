"""List readable C that reads a host stack local past its end, or copies one out half written (core/stack_overread).

usage: python tools/stack_overread.py --project DIR [--src DIR] [--only ADDR ...] [--workers N] [--json FILE] [--write]

Every readable/src/ADDR.c is preprocessed by cl /EP against the library's game.h and parsed; the analysis is whole
program (a callee's extent and definite writes come from its own readable C). Static, no Ghidra, no runner.
- --src DIR: ADDR.c files there replace the workspace's (to check an older or a candidate version).
- --only: report the hits in these files only (the analysis still reads every file).
- --write: state/stack_overread/summary.json and requeue.txt (`addr rule line why`); it requeues nothing.
Exit 0 with no hits, 1 with hits, 2 when a file could not be read (preprocessor or parser).
"""
import argparse
import json
import pathlib
import re
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--src", help="a folder of ADDR.c files that replace the workspace's")
    ap.add_argument("--only", nargs="*", default=[], help="report hits in these files only")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", help="write the hits and counts here")
    ap.add_argument("--write", action="store_true", help="state/stack_overread/summary.json and requeue.txt")
    a = ap.parse_args(argv)
    use_project(a.project)
    from core import library, paths, readable, readable_check as rcheck, stack_overread as so

    t0 = time.time()
    cfg = paths.config()
    out = paths.STATE / "stack_overread"
    (out / "include").mkdir(parents=True, exist_ok=True)
    (out / "src").mkdir(parents=True, exist_ok=True)
    files = sorted(p.stem for p in readable.SRC.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem))
    sources = {f: (readable.SRC / f"{f}.c").read_text(encoding="utf-8") for f in files}
    replaced = []
    if a.src:
        for p in sorted(pathlib.Path(a.src).glob("*.c")):
            if re.fullmatch(r"[0-9a-f]{8}", p.stem):
                sources[p.stem] = p.read_text(encoding="utf-8")
                replaced.append(p.stem)
    reg = readable.Registry()
    sigs, _ = library.raw_declarations(reg, sorted(sources))
    (out / "include" / "game.h").write_text(readable.game_h(reg, addrs=sorted(sigs), raw_sigs=sigs), encoding="utf-8")
    env, exe = library.tools(cfg)
    inc = [out / "include", readable.INCLUDE, rcheck.RUNTIME_RD, paths.RUNTIME]
    res, errors = so.run(sources, exe["cl"], env, inc, out / "src", workers=a.workers)
    hits = res["hits"]
    only = {x.lower() for x in a.only}
    if only:
        hits = [h for h in hits if h["fn"].split(":")[0] in only or file_of(res, h) in only]
    for h in hits:
        h["file"] = file_of(res, h)
    hits.sort(key=lambda h: (h["file"], h["line"], h["rule"]))
    for h in hits:
        print(f"{h['file']}.c:{h['line']}: {h['rule']} {h['fn'].split(':')[-1]}: {h['why']}")
    for f, e in sorted(errors.items()):
        print(f"{f}.c: not read: {e}")
    counts = {"files": len(sources), "functions": res["functions"], "replaced": replaced, "errors": len(errors),
              "hits": len(hits), "R1": sum(h["rule"] == "R1" for h in hits),
              "R2": sum(h["rule"] == "R2" for h in hits), "files_hit": len({h["file"] for h in hits}),
              "seconds": round(time.time() - t0, 1)}
    print(json.dumps(counts))
    data = {"counts": counts, "hits": hits, "errors": errors}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(data, indent=1), encoding="utf-8")
    if a.write:
        paths.write_json(out / "summary.json", data)
        (out / "requeue.txt").write_text("".join(f"{h['file']} {h['rule']} {h['line']} {h['why']}\n" for h in hits),
                                         encoding="utf-8")
    return 2 if errors else 1 if hits else 0


def file_of(res, h):
    return res["files"].get(h["fn"], h["fn"].split(":")[0])


if __name__ == "__main__":
    sys.exit(main())
