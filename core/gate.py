"""The watcher gate: staged oracle trials, and the strict checks a change must pass before it is promoted.

The watcher agent may only change the project's oracle/TcInputs.java (how test inputs are generated) and
oracle/hints/<addr>.txt. It never decides anything: this module runs every check itself.

A change is promoted only if ALL of these hold:
  1. TcInputs.java and the hints pass javalint (allow-lists: inputs only, no I/O, no reaching the oracle).
  2. The target function now gets enough valid vectors in both sets, covers its blocks (at least 70%, or all
     but one), its dev results are not all identical, and no hidden vector repeats a dev vector.
  3. Regression: the project's reference functions and a sample of matched functions are regenerated with the new
     generator; their accepted C must still PASS, and none may lose block coverage.
  4. The deliberately broken control must still FAIL against the regenerated tests.
"""
import difflib
import json
import os
import pathlib
import random
import shutil
import threading
import time

from . import check, ghidra, javalint, paths

WATCH = paths.ROOT / "watcher"
def locked():
    """The target's oracle scripts that TcInputs builds on (target.json "locked_scripts"): compiled with it, never edited."""
    return tuple(paths.config()["locked_scripts"])


def references(cfg):
    """Functions with hand-written accepted C in reference/ (project.json "references")."""
    return tuple(cfg.get("references") or ())


def staging_scripts(ws):
    """The target's locked scripts plus the workspace's TcInputs.java (compiled as a separate bundle)."""
    d = WATCH / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    for n in locked():
        paths.copy_if_changed(paths.TARGET_SCRIPTS / n, d / n)
    paths.copy_if_changed(ws / "TcInputs.java", d / "TcInputs.java")
    return d


def lint_workspace(ws, addr):
    errs = [f"TcInputs.java: {e}" for e in javalint.lint_inputs((ws / "TcInputs.java").read_text(encoding="utf-8"))]
    for h in sorted((ws / "hints").glob("*")):
        if h.name != f"{addr}.txt":
            errs.append(f"hints/{h.name}: only hints/{addr}.txt may be written")
        else:
            errs += [f"hints/{h.name}: {e}" for e in javalint.lint_hints(h.read_text(encoding="utf-8", errors="replace"))]
    return errs


def trial(cfg, addr, ws, out, stop_flag=None):
    """Prepare `addr` with the workspace generator into `out`. Returns (meta, summary)."""
    own = ws / "hints" / f"{addr}.txt"
    st = {"scripts": staging_scripts(ws), "packet": out / "packet", "harness": out / "harness",
          "hints": own if own.exists() else paths.HINTS / f"{addr}.txt"}
    with ghidra.slot(cfg, stop_flag) as gs:
        return ghidra.prepare(cfg, addr, slot_index=gs, staging=st, stop_flag=stop_flag)


def _blocks(path, drop=("V ", "COV")):
    """vectors.txt / expected.txt -> list of (set, body without the id line and coverage)."""
    out, cur, cur_set = [], [], None
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("V "):
            cur, cur_set = [], ln.split()[2]
        elif ln == "E":
            out.append((cur_set, "\n".join(cur)))
        elif not ln.startswith(drop):
            cur.append(ln)
    return out


def target_checks(cfg, summary, harness):
    checks = []
    mv = cfg["min_valid_vectors"]
    ok = summary and summary["dev_valid"] >= mv and summary["hidden_valid"] >= mv
    checks.append(("enough valid vectors", bool(ok),
                   f"dev {summary and summary['dev_valid']}, hidden {summary and summary['hidden_valid']} (need {mv})"))
    if not ok:
        return checks
    tot, hit = summary["blocks_total"], summary["blocks_hit_all"]
    checks.append(("block coverage", hit >= -(-7 * tot // 10) or tot - hit <= 1, f"{hit}/{tot} blocks"))
    vec = _blocks(harness / "vectors.txt")
    dev = {b for s, b in vec if s == "dev"}
    dup = sum(1 for s, b in vec if s == "hidden" and b in dev)
    checks.append(("hidden vectors differ from dev vectors", dup == 0, f"{dup} hidden vector(s) repeat a dev vector"))
    outcomes = {b for s, b in _blocks(harness / "expected.txt") if s == "dev"}
    checks.append(("dev results are not all identical", tot <= 1 or len(outcomes) >= 2,
                   f"{len(outcomes)} distinct dev result(s), {tot} block(s)"))
    return checks


def regression_set(o, target, sample):
    refs = references(o.cfg)
    matched = sorted(a for a, r in o.ledger["functions"].items()
                     if r.get("status") == "matched" and (paths.MATCHED / f"{a}.c").exists()
                     and a not in refs and a != target)
    rng = random.Random(int(target, 16))
    return list(refs) + rng.sample(matched, min(sample, len(matched)))


def regression(cfg, o, target, ws, tmp, stop_flag, log):
    """Regenerate tests for the regression set in parallel (Ghidra slots bound it) and re-check accepted C."""
    addrs = regression_set(o, target, int(cfg.get("watcher_regress_sample", 5)))
    refs = references(cfg)
    results = {}

    def one(a):
        try:
            _meta, summ = trial(cfg, a, ws, tmp / a, stop_flag)
            c_file = paths.REFERENCE / f"{a}.c" if a in refs else paths.MATCHED / f"{a}.c"
            res = check.run_check(cfg, a, str(c_file), build_dir=tmp / a / "build", harness_dir=tmp / a / "harness")
            old = paths.read_json(paths.HARNESS / a / "oracle_summary.json") or {}
            results[a] = (res["verdict"], res["failing"], res["total"], summ["blocks_hit_all"],
                          old.get("blocks_hit_all", 0))
        except Exception as e:  # noqa: BLE001 - a crash is a failed check
            results[a] = ("ERROR", str(e).splitlines()[0][:200])

    threads = [threading.Thread(target=one, args=(a,), daemon=True) for a in addrs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    checks = []
    for a in addrs:
        r = results[a]
        if r[0] == "ERROR":
            checks.append((f"regression {a}", False, "trial failed: " + r[1]))
            continue
        verdict, failing, total, cov_new, cov_old = r
        checks.append((f"regression {a}: accepted C still passes", verdict == "PASS", f"{failing}/{total} differ"))
        checks.append((f"regression {a}: coverage kept", cov_new >= cov_old, f"{cov_new} blocks (was {cov_old})"))
    log(f"[WATCH] regression: {sum(1 for c in checks if c[1])}/{len(checks)} checks passed")
    b_addr, b_file = cfg["broken_control"]
    if (tmp / b_addr / "harness" / "vectors.txt").exists():
        res = check.run_check(cfg, b_addr, str(paths.REFERENCE / b_file), build_dir=tmp / "broken",
                              harness_dir=tmp / b_addr / "harness")
        checks.append(("broken control still fails", res["verdict"] == "FAIL", f"{res['failing']}/{res['total']} differ"))
    return checks


def _snapshot(ws, addr, dst):
    """Freeze the workspace: everything after this reads the copy, so what is promoted is what was tested."""
    (dst / "hints").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ws / "TcInputs.java", dst / "TcInputs.java")
    for h in (ws / "hints").glob("*"):
        if h.is_file():
            shutil.copy2(h, dst / "hints" / h.name)
    return dst


def run_gate(cfg, o, addr, ws, stop_flag=None, log=print, full=True):
    """All checks. Returns {"ok", "checks": [[name, ok, detail], ...], "summary", "snapshot"}; on success the
    snapshot folder is kept for promote(), otherwise it is removed. full=False: lint and target only (a try)."""
    tmp = WATCH / "gate" / f"{addr}-{int(time.time())}"
    shutil.rmtree(tmp, ignore_errors=True)
    snap = _snapshot(ws, addr, tmp / "snap")
    errs = lint_workspace(snap, addr)
    checks = [("javalint (TcInputs.java and hints)", not errs, "; ".join(errs[:8]) or "clean")]
    summary = None
    if not errs:
        try:
            _meta, summary = trial(cfg, addr, snap, tmp / "target", stop_flag)
            checks += target_checks(cfg, summary, tmp / "target" / "harness")
        except Exception as e:  # noqa: BLE001
            checks.append(("target trial", False, str(e).splitlines()[0][:300]))
    if full and all(c[1] for c in checks):
        checks += regression(cfg, o, addr, snap, tmp, stop_flag, log)
    ok = all(c[1] for c in checks)
    keep = ok and full
    for d in tmp.iterdir():
        if d.name != "snap" or not keep:
            shutil.rmtree(d, ignore_errors=True)
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": ok, "checks": [list(c) for c in checks], "summary": summary, "snapshot": str(snap) if keep else None}


def promote(cfg, addr, snap, report, log):
    """Install a gated snapshot's TcInputs.java and hints. Holds every Ghidra slot so no preparation is mid-compile."""
    ws = pathlib.Path(snap)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    arch = WATCH / "history" / f"{stamp}_{addr}"
    arch.mkdir(parents=True, exist_ok=True)
    before = paths.INPUTS.read_text(encoding="utf-8")
    after = (ws / "TcInputs.java").read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        "TcInputs.java (before)", "TcInputs.java (after)"))
    hints = ws / "hints" / f"{addr}.txt"
    hint_text = hints.read_text(encoding="utf-8") if hints.exists() else ""
    (arch / "TcInputs.before.java").write_text(before, encoding="utf-8")
    (arch / "TcInputs.after.java").write_text(after, encoding="utf-8")
    (arch / "diff.txt").write_text(diff, encoding="utf-8")
    (arch / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    with ghidra.all_slots(cfg):
        if diff:
            tmp = paths.INPUTS.with_suffix(".java.new")
            tmp.write_text(after, encoding="utf-8")
            os.replace(tmp, paths.INPUTS)
        if hint_text.strip():
            paths.HINTS.mkdir(parents=True, exist_ok=True)
            (paths.HINTS / f"{addr}.txt").write_text(hint_text, encoding="utf-8")
            (arch / f"{addr}.hints.txt").write_text(hint_text, encoding="utf-8")
        warm = WATCH / "gate" / "warm"
        st = {"scripts": paths.sync_scripts(), "packet": warm / "packet", "harness": warm / "harness",
              "hints": paths.HINTS / f"{addr}.txt"}
        try:
            ghidra.prepare(cfg, addr, slot_index=0, staging=st)  # compiles the new bundle once, before workers do
        except Exception:
            paths.INPUTS.write_text(before, encoding="utf-8")  # roll back
            paths.sync_scripts()
            raise
        finally:
            shutil.rmtree(warm, ignore_errors=True)
    shutil.rmtree(ws.parent, ignore_errors=True)
    log(f"[WATCH] {addr}: PROMOTED new input generator (archive {paths.show(arch)})")
    return {"archive": paths.show(arch), "diff": diff[:20000], "hints": hint_text}
