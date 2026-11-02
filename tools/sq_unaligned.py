"""Make the answers of the unaligned store-queue judge pass (core/sq_unaligned): state/sq_unaligned/<addr>/ for every
function with a window register. See that module for which vectors are kept and why. Nothing here reads a C file the
game's agents wrote; a function whose key still matches is left alone.

usage: python tools/bounded.py 3000 python tools/sq_unaligned.py --project DIR [ADDR...] [--budget SECONDS]

Nearest existing: tools/xemu_check.py (xemu against expected.txt on the harness's own vectors: it cannot move the window
pointer) and tools/junk_ram.py (the junk pass's report-only tool: same runner, other inputs).
"""
import argparse
import json
import os
import pathlib
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import use_project  # noqa: E402


def translator_c(cfg, a, meta, w):
    """The translator's C of a function, as tools/dp_judge.py makes it."""
    body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks", [])]
    return w["translate_function"](w["img"], int(a, 16), w["Signature"].from_meta(meta), w["facts"], body or None,
                                   entries=w["entries"], ranges=w["ranges"].get(int(a, 16)), spin=w["spins"].get(int(a, 16)),
                                   written=w["written"], sz_from_caller=int(a, 16) in w["sz1"])


def one(cfg, a, w, work):
    from core import check, compare, junk_ram, paths, readable_stack, ret_reg, sq_unaligned
    h = paths.HARNESS / a
    raw_meta = paths.read_json(h / "meta.json")
    idx = sq_unaligned.window_index(raw_meta)
    out = sq_unaligned.answers_dir(a)
    key = sq_unaligned.key_of(cfg, h)
    if (paths.read_json(out / "info.json") or {}).get("key") == key:
        return None
    meta = ret_reg.judged(raw_meta, ret_reg.listed(paths.STATE), a)
    d = work / a
    shutil.rmtree(d, ignore_errors=True)
    (d / "h").mkdir(parents=True)
    for f in h.iterdir():
        if f.is_file():
            shutil.copy(f, d / "h" / f.name)
    image, lo = w["image"], readable_stack.frame_lo()

    def xemu(vectors, res):
        fn = d / "fn.txt"
        fn.write_text(junk_ram.config_text(raw_meta, w["fpscr"], w["bodies"].get(raw_meta["addr"], ())), encoding="utf-8")
        env = dict(os.environ, XEMU_FN=str(fn), XEMU_MODEL=sq_unaligned.XEMU_MODEL)
        check.run_runner([str(w["exe"]), str(cfg["binary_path"]), str(paths.CODE_RANGES), w["table"], str(vectors), "all", str(res)],
                         cfg["check_timeout_s"], res, 1 << 30, env=env)
        return compare.parse_results(res)

    vecs0, exp0 = compare.parse_vectors(h / "vectors.txt"), compare.parse_results(h / "expected.txt")
    z = xemu(h / "vectors.txt", d / "zero.txt")
    fit = {i for i, v in vecs0.items() if i in exp0 and i in z and not compare.compare_vector(meta, image, v, exp0[i], z[i], lo)}
    moved, ids = sq_unaligned.move_vectors((h / "vectors.txt").read_text(encoding="utf-8"), idx)
    (d / "h" / "vectors.txt").write_text(moved, encoding="utf-8", newline="\n")
    ex = xemu(d / "h" / "vectors.txt", d / "h" / "expected.txt")
    clean = [i for i in ids if i in fit and i in ex and ex[i].get("done") and not ex[i].get("faults") and "crash" not in ex[i]]
    (d / "tx.c").write_text(translator_c(cfg, a, meta, w), encoding="utf-8")
    r = check.run_check(cfg, a, str(d / "tx.c"), build_dir=d / "b", harness_dir=d / "h")
    act = compare.parse_results(d / "b" / "actual.txt") if (d / "b" / "actual.txt").exists() else {}
    vecs = compare.parse_vectors(d / "h" / "vectors.txt")
    kept = [i for i in clean if i in act and not compare.compare_vector(meta, image, vecs[i], ex[i], act[i], lo)]
    out.mkdir(parents=True, exist_ok=True)
    for f in ("vectors.txt", "expected.txt"):
        (out / f).write_text(sq_unaligned.keep_blocks((d / "h" / f).read_text(encoding="utf-8"), kept), encoding="utf-8", newline="\n")
    info = {"key": key, "moved": len(ids), "zero_disagree": len(vecs0) - len(fit), "clean": len(clean), "kept": len(kept),
            "dropped_translator": len(clean) - len(kept), "translator": r["verdict"], "translator_stage": r.get("stage")}
    (out / "info.json").write_text(json.dumps(info, indent=1), encoding="utf-8", newline="\n")
    shutil.rmtree(d, ignore_errors=True)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--budget", type=float, default=2400)
    args = ap.parse_args()
    use_project(args.project)
    from core import junk_ram, paths, ret_reg, sq_unaligned  # noqa: F401
    sys.path.insert(0, str(paths.TARGET / "translator"))
    from sh4translate.translate import (Image, Signature, TargetFacts, spin_ranges, sz_from_caller, translate_function,
                                        written_code)
    cfg = dict(paths.config())
    cfg["junk_ram"] = False
    facts = TargetFacts.from_target_json(paths.read_json(paths.TARGET / "target.json"))
    image = cfg["binary_path"].read_bytes()
    lines = (paths.STATE / "function_bodies.txt").read_text().splitlines()
    ranges = {}
    for ln in lines:
        if len(ln.split()) == 3:
            e, lo, hi = (int(x, 16) for x in ln.split())
            ranges.setdefault(e, []).append((lo, hi))
    w = {"translate_function": translate_function, "Signature": Signature, "facts": facts, "image": image,
         "img": Image(image, facts.image_base, facts.little), "entries": {int(ln.split()[0], 16) for ln in lines if ln.strip()},
         "ranges": ranges, "spins": spin_ranges(cfg), "written": written_code(cfg), "sz1": sz_from_caller(cfg),
         "exe": junk_ram.xemu_runner(cfg)[0], "bodies": junk_ram.bodies(paths.FUNCTION_BODIES),
         "fpscr": int(str(cfg.get("fpscr_entry") or "0x00040001"), 16),
         "table": str(paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE)}
    addrs = args.addrs or sorted(a.parent.name for a in paths.HARNESS.glob("*/meta.json")
                                 if sq_unaligned.window_index(paths.read_json(a)) is not None)
    work, t0, left = paths.BUILD / "sq_unaligned", time.time(), 0
    for a in addrs:
        if time.time() - t0 > args.budget:
            left += 1
            continue
        info = one(cfg, a, w, work)
        print(a, "current" if info is None else info, flush=True)
    print(f"{len(addrs) - left} of {len(addrs)} functions done" + (f"; {left} left: run it again" if left else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
