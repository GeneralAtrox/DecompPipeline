"""Cross-check the oracle with a second, independent SH-4 emulator (xemu, targets/dreamcast_sh4/xemu).

The judge compares the C with expected.txt, which Ghidra's EmulatorHelper made (TcOracle): an emulation bug in
Ghidra would be truth. xemu is an SH-4 interpreter of our own, written from the hardware manual; it runs a
function's original machine code on its stamped vectors, linked with the unchanged C runtime (the vectors, the
mocked callees, the memory model and the report are the judge's own), and core/compare.compare_vector compares its
results with expected.txt as the judge does: the return registers (fr0 too), memory writes, the calls in order with
their registers, and the runtime state at exit. A function where the two emulators disagree is written to
OUT/<addr>.json for triage (a Ghidra bug, an xemu bug, or a modelling difference); OUT/summary.json has them all.

Run it at stamp or gate time on a sample only (never in the match loop): --sample 0.07 plus --special, every
function with a float, store-queue (line S), matrix (ftrv, fipr) or fschg/frchg instruction.

usage: python tools/xemu_check.py --project DIR [ADDR... | @FILE] [--sample FRAC] [--special] [--seed N] [--max N]
       [--harness-root DIR] [--out DIR] [--timeout S] [--model LIST]
--model: triage switches (xe.h), a comma list; each puts one oracle rule in place of the hardware's, so a
       disagreement it removes has that rule as its cause: ftrc (Ghidra's truncating ftrc), ftrcnan (the same for a
       NaN operand only), ghmem (Ghidra's emulator zeroing initialized bytes a read runs into, xe_ghmem.c), sqwt
       (TcSq's write-through store queues). With ftrc,ghmem,sqwt xemu reproduces the oracle as it was before r15
       (the r15 sweep); the r15 oracle saturates ftrc (TcFpu) and keeps the bytes a read runs into (TcOracle), so
       harnesses it stamped need sqwt alone.
--harness-root: the harnesses to check (default the workspace's harness/; e.g. a staging trial's output).
--out: where the build and the results go (default the workspace's state/xemu/); a run there resumes, skipping
       the functions OUT/records.jsonl already has (delete it for a fresh run).
A harness with the stricter judge's mock_junk or exit_frame (vectors.txt line J) is skipped and listed: xemu keeps
its own r1..r7 and r15. Exit 0 when every checked function agrees or is skipped, 1 when one disagrees, 2 on a usage
or build error.
"""
import argparse
import json
import os
import pathlib
import random
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import expand_addrs, use_project  # noqa: E402

IMAGE_BASE = 0x8c010000
MODEL = ""  # --model: xemu's triage switches (xe.h)


def build(cfg, paths, check, out):
    """runner.exe with xemu as tc_thunk, once per workspace (core/junk_ram.xemu_cmd). Returns its path; exits 2 on a
    failed build."""
    from core import junk_ram
    bdir = out.resolve() / "build"  # the compiler runs in it: a relative --out would double
    bdir.mkdir(parents=True, exist_ok=True)
    env = check.msvc_env(cfg)
    env_path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    import shutil
    cl = shutil.which("cl.exe", path=env_path)
    if not cl:
        sys.exit("cl.exe not found in the Visual Studio environment")
    exe = bdir / "runner.exe"
    rc, log, reason = paths.run_bounded(junk_ram.xemu_cmd(cl, bdir, exe), 180, cwd=bdir, env=env)
    if rc != 0 or reason:
        print(log[-3000:])
        sys.exit(2)
    return exe


def words(image, meta):
    for lo, hi in meta.get("blocks") or []:
        a, b = int(lo, 16) - IMAGE_BASE, int(hi, 16) + 1 - IMAGE_BASE
        for off in range(max(a, 0) & ~1, min(b, len(image) - 1), 2):
            yield image[off] | image[off + 1] << 8


def flags(image, meta, harness):
    """What makes a function special for the sample: its FPU instructions and store queues."""
    f = set()
    for w in words(image, meta):
        if w >> 12 == 0xF:
            f.add("float")
            if (w & 0xF3FF) == 0xF1FD or (w & 0xF0FF) == 0xF0ED:
                f.add("matrix")
            if w in (0xF3FD, 0xFBFD):
                f.add("fschg")
            if (w & 0xF1FF) == 0xF0FD:
                f.add("fsca")
            if (w & 0xF0FF) == 0xF07D:
                f.add("fsrra")
            if (w & 0xF00F) == 0xF00E:
                f.add("fmac")
            if (w & 0xF0FF) == 0xF03D:
                f.add("ftrc")
    with open(harness / "vectors.txt", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.startswith("V"):
                break
            if ln.startswith("S"):
                f.add("sq")
    return sorted(f)


def strict_flags(harness):
    """vectors.txt header line J (core/strict_judge's flags; 0 without one)."""
    with open(harness / "vectors.txt", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.startswith("V"):
                break
            if ln.startswith("J "):
                return int(ln.split()[1])
    return 0


def bodies(path):
    """core/junk_ram.bodies (one copy: the judge's junk pass runs xemu too)."""
    from core import junk_ram
    return junk_ram.bodies(path)


def config_text(meta, fpscr, body=()):
    """core/junk_ram.config_text: xemu's function config."""
    from core import junk_ram
    return junk_ram.config_text(meta, fpscr, body)


def run(exe, args, env, timeout):
    """Run the runner bounded; returns (rc, log, reason, peak working set in MB)."""
    import psutil
    from core import paths
    p = subprocess.Popen([str(exe), *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    peak, end, reason = 0, time.monotonic() + timeout, None
    try:
        ps = psutil.Process(p.pid)
    except psutil.Error:
        ps = None
    while p.poll() is None:
        try:
            peak = max(peak, ps.memory_info().peak_wset) if ps else peak
        except psutil.Error:
            pass
        if time.monotonic() > end:
            reason = "timeout"
            paths.kill_tree(p)
            break
        time.sleep(0.02)
    log = p.communicate()[0].decode("utf-8", "replace")
    return p.returncode, log, reason, peak / 2 ** 20


def check_one(addr, harness, exe, cfg, image, fpscr, out, timeout):
    from core import compare, paths, readable_stack
    meta = paths.read_json(harness / "meta.json")
    rec = {"addr": addr, "flags": flags(image, meta, harness), "ret": meta.get("ret")}
    if strict_flags(harness) & 9:  # mock_junk, exit_frame: the oracle junks r1..r7 and reports r15, xemu keeps its own
        rec.update(skipped="strict judge (mock_junk or exit_frame): xemu does not model it", vectors=0, disagree=0,
                   first=[], disagree_vectors=[], seconds=0, peak_mb=0)
        return rec
    fn = out / "fn" / f"{addr}.txt"
    fn.parent.mkdir(parents=True, exist_ok=True)
    fn.write_text(config_text(meta, fpscr, bodies(paths.FUNCTION_BODIES).get(addr, ())), encoding="utf-8")
    act_path = out / "fn" / f"{addr}.actual.txt"
    env = dict(os.environ, XEMU_FN=str(fn), XEMU_MODEL=MODEL)
    t = time.monotonic()
    rc, log, reason, peak = run(exe, [str(cfg["binary_path"]), str(paths.CODE_RANGES),
                                      str(paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE),
                                      str(harness / "vectors.txt"), "all", str(act_path)], env, timeout)
    rec.update(seconds=round(time.monotonic() - t, 3), peak_mb=round(peak, 1))
    if reason or rc:
        rec["run_error"] = reason or f"exit {rc}"
    xemu = sorted({ln.strip() for ln in log.splitlines() if ln.startswith("xemu:")})
    if xemu:
        rec["xemu"] = xemu[:10]
    vecs = compare.parse_vectors(harness / "vectors.txt")
    exp = compare.parse_results(harness / "expected.txt")
    act = compare.parse_results(act_path) if act_path.exists() else {}
    bad, lo = [], readable_stack.frame_lo()
    for i, v in vecs.items():
        if i not in exp:
            continue
        d = compare.compare_vector(meta, image, v, exp[i], act.get(i), lo)
        if d:
            bad.append({"vector": i, "set": v["set"], "diffs": d[:8]})
    rec.update(vectors=sum(1 for i in vecs if i in exp), disagree=len(bad), first=bad[:5],
               disagree_vectors=[b["vector"] for b in bad])
    act_path.unlink(missing_ok=True)
    fn.unlink(missing_ok=True)
    return rec


def pick(a, paths, image, root):
    have = sorted(d.name for d in root.iterdir()
                  if (d / "vectors.txt").exists() and (d / "expected.txt").exists() and (d / "meta.json").exists())
    if a.addrs:
        want = set(expand_addrs(a.addrs))
        return [x for x in have if x in want]
    rng = random.Random(a.seed)
    chosen = set(rng.sample(have, round(len(have) * a.sample))) if a.sample else set()
    if a.special:
        for x in have:
            meta = paths.read_json(root / x / "meta.json") or {}
            if set(flags(image, meta, root / x)) & {"float", "sq", "matrix", "fschg"}:
                chosen.add(x)
    out = sorted(chosen)
    if a.max and len(out) > a.max:
        out = sorted(rng.sample(out, a.max))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--sample", type=float, default=0.0)
    ap.add_argument("--special", action="store_true")
    ap.add_argument("--seed", type=int, default=15)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--harness-root")
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--model", default="", help="triage switches: ftrc, ftrcnan, ghmem, sqwt (xe.h)")
    a = ap.parse_args()
    global MODEL
    MODEL = a.model
    use_project(a.project)
    from core import check, paths
    cfg = paths.config()
    out = pathlib.Path(a.out) if a.out else paths.STATE / "xemu"
    root = pathlib.Path(a.harness_root) if a.harness_root else paths.HARNESS
    out.mkdir(parents=True, exist_ok=True)
    image = cfg["binary_path"].read_bytes()
    target = paths.read_json(paths.TARGET / "target.json") or {}
    fpscr = int(str(paths.PROJECT.get("fpscr_entry") or target.get("fpscr_entry") or "0x00040001"), 16)
    fns = pick(a, paths, image, root)
    if not fns:
        sys.exit("no function to check")
    exe = build(cfg, paths, check, out)
    log = out / "records.jsonl"  # one record per function checked: a stopped run resumes where it was
    done = {}
    if log.exists():
        for ln in log.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                done[r["addr"]] = r
    print(f"xemu: {len(fns)} functions from {root} ({len([x for x in fns if x in done])} done before)", flush=True)
    recs, t0 = [done[x] for x in fns if x in done], time.monotonic()
    todo = [x for x in fns if x not in done]
    for k, addr in enumerate(todo, 1):
        rec = check_one(addr, root / addr, exe, cfg, image, fpscr, out, a.timeout)
        recs.append(rec)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        if rec.get("skipped"):
            print(f"[{k}/{len(todo)}] {addr} SKIPPED {rec['skipped']}", flush=True)
        elif rec["disagree"] or rec.get("run_error"):
            paths.write_json(out / f"{addr}.json", rec)
            print(f"[{k}/{len(todo)}] {addr} DISAGREE {rec['disagree']}/{rec['vectors']} {rec['flags']} "
                  f"{(rec['first'][0]['diffs'][:2] if rec['first'] else rec.get('run_error'))}", flush=True)
        else:
            (out / f"{addr}.json").unlink(missing_ok=True)
    skipped = [r for r in recs if r.get("skipped")]
    agree = [r for r in recs if not r["disagree"] and not r.get("run_error") and not r.get("skipped")]
    summary = {"project": str(paths.ROOT), "harness_root": str(root), "functions": len(recs), "agree": len(agree),
               "vectors": sum(r["vectors"] for r in recs), "vectors_disagree": sum(r["disagree"] for r in recs),
               "seconds": round(time.monotonic() - t0, 1),
               "seconds_per_function": round(sum(r["seconds"] for r in recs) / len(recs), 3),
               "peak_mb_max": max(r["peak_mb"] for r in recs),
               "skipped": [r["addr"] for r in skipped],
               "disagree": [r["addr"] for r in recs if r not in agree and r not in skipped], "records": recs}
    paths.write_json(out / "summary.json", summary)
    print(f"xemu: {len(agree)}/{len(recs)} functions agree ({len(skipped)} skipped: strict judge); "
          f"{summary['vectors_disagree']}/{summary['vectors']} "
          f"vectors disagree; {summary['seconds_per_function']} s per function, peak {summary['peak_mb_max']} MB",
          flush=True)
    return 0 if len(agree) + len(skipped) == len(recs) else 1


if __name__ == "__main__":
    sys.exit(main())
