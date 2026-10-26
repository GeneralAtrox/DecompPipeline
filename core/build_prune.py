"""Build-cache pruning (r15): the compiler outputs of build/ADDR/ once nothing builds that function.

Each judgement builds in its own folder under build/ADDR/ (official, agent, rd_agent, rd_preflight, rd_rawcheck,
rd_verify, readable, ... and their _inc/_proof/_orig/_lean companions), and every build starts by deleting its
folder (check.build, readable_check.build: shutil.rmtree, then a full compile; the precompiled header of
core/pch_warnings and the generated game.h are written afresh too). So no build reuses a file another build left (but
the readable runners kept in build/ADDR/rd_runners by the key of every input, core/readable_check.build), and
nothing reads a build folder after its judgement returns: the judge reads actual.txt during the check, regress builds
in tests/.work. The outputs only pile up: PS2 main had 6555 folders of about 5 MB (a sample of 150: .pch 31%, .asm
18%, .exe 17%, .obj 7%), TC and Spawn more; Bitdefender also flags fresh runner executables (PS2's rd_runner.exe was
quarantined on 2026-10-20).

What goes: the files with an ARTEFACT suffix (executables, objects, precompiled headers, compiler listings, linker
by-products), then any folder left empty. What stays: everything else (the C copied in or generated, game.h, the
runner's actual.txt, JSON): small text a person can read back, and none of it is needed to build again.

A function is never pruned while a job holds it:
- its claim (state/fnlocks/ADDR.lock, core/fn_claim: refine runs, verify, sync-names, drafts, adoptions) is taken
  here without waiting, and a busy one is skipped;
- the orchestrator reserves functions in state/ledger.json (waiting, preparing, running, queued), not by the claim:
  those are skipped;
- a file written in the last `min_age_min` minutes is left alone (a build that started between the check and the
  deletion writes only new files).

When it runs (config.json "build_prune", over DEFAULTS; "enabled": false turns it off):
- after_job: when a job finishes a function (an orchestrator investigation, a refine cycle, a verify judgement),
  its folder's outputs, at once;
- maybe_sweep: at the start of an orchestrator or a refine run, at most every `interval_h` hours (state stamp
  build_prune.json): every folder whose newest output is older than `age_h` hours, then, while the outputs left
  exceed `cap_gb`, the oldest of the rest, down to `min_age_min`.
tools/build_prune.py runs a sweep by hand (--dry-run measures it).
"""
import json
import os
import re
import threading
import time

from . import filelock, paths

ARTEFACT = frozenset({".exe", ".obj", ".pch", ".asm", ".pdb", ".ilk", ".exp", ".lib", ".idb", ".ipch"})
DEFAULTS = {"enabled": True, "on_finish": True, "age_h": 24.0, "cap_gb": 5.0, "min_age_min": 30.0,
            "interval_h": 6.0}
ORCH_ACTIVE = ("waiting", "preparing", "running", "queued")
FN = re.compile(r"[0-9a-f]{8}")


def settings(cfg):
    return {**DEFAULTS, **((cfg or {}).get("build_prune") or {})}


def stamp_file():
    return paths.STATE / "build_prune.json"


def _outputs(d):
    """[(path, size, mtime)] of the ARTEFACT files under folder d."""
    out = []
    for p in d.rglob("*"):
        if p.suffix.lower() in ARTEFACT:
            try:
                st = p.stat()
            except OSError:
                continue
            if not p.is_dir():
                out.append((p, st.st_size, st.st_mtime))
    return out


def prune_dir(d, before):
    """Delete the ARTEFACT files under d last written before `before` (epoch seconds), then the folders left empty.
    Returns (bytes freed, files deleted). A file another process has open stays (its build is not over)."""
    freed = n = 0
    for p, size, mtime in _outputs(d):
        if mtime >= before:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        freed, n = freed + size, n + 1
    for sub in sorted((x for x in d.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            sub.rmdir()  # only when empty
        except OSError:
            pass
    return freed, n


def orch_held(raw_rows, addr):
    return (raw_rows.get(addr) or {}).get("status") in ORCH_ACTIVE


def prune_function(addr, min_age_s=0.0, raw_rows=None, now=None):
    """Prune build/ADDR under its claim (taken without waiting; a claim this thread already holds is re-entered).
    Returns (bytes freed, files deleted, why skipped or None)."""
    d = paths.BUILD / addr
    if not d.is_dir():
        return 0, 0, None
    if raw_rows is None:
        raw_rows = (paths.read_json(paths.LEDGER, {}) or {}).get("functions") or {}
    if orch_held(raw_rows, addr):
        return 0, 0, f"orchestrator: {raw_rows[addr]['status']}"
    try:
        with filelock.claim(paths.STATE / "fnlocks" / f"{addr}.lock", 0, {"purpose": "prune"}):
            return (*prune_dir(d, (now or time.time()) - min_age_s), None)
    except filelock.Busy as e:
        return 0, 0, str(e)


def after_job(cfg, addr, log=None, raw_rows=None):
    """The outputs of a function a job just finished (the caller may hold its claim; raw_rows: the orchestrator's
    own ledger rows, fresher than the file). Never raises: pruning is housekeeping, a failure must not change the
    job's result."""
    s = settings(cfg)
    if not (s["enabled"] and s["on_finish"]):
        return None
    try:
        freed, n, busy = prune_function(addr, raw_rows=raw_rows)
    except Exception as e:  # noqa: BLE001
        busy, freed, n = f"{type(e).__name__}: {e}", 0, 0
    if log and (n or busy):
        log(f"build prune {addr}: " + (f"skipped ({busy})" if busy else f"{n} files, {freed / 2 ** 20:.1f} MB"))
    return freed, n, busy


def scan():
    """[(newest output mtime, addr, output bytes)] of the function folders under build/ that have outputs."""
    rows = []
    for d in paths.BUILD.iterdir() if paths.BUILD.is_dir() else []:
        if d.is_dir() and FN.fullmatch(d.name):
            outs = _outputs(d)
            if outs:
                rows.append((max(m for _, _, m in outs), d.name, sum(s for _, s, _ in outs)))
    return sorted(rows)


def sweep(cfg, dry_run=False, now=None, log=None):
    """Prune by age, then by size (settings). Returns a report dict (bytes before, freed, pruned, busy, kept)."""
    s, now = settings(cfg), now or time.time()
    rows = scan()
    raw_rows = (paths.read_json(paths.LEDGER, {}) or {}).get("functions") or {}
    total = sum(b for _, _, b in rows)
    old, floor, cap = now - s["age_h"] * 3600, now - s["min_age_min"] * 60, s["cap_gb"] * 2 ** 30
    rep = {"folders": len(rows), "bytes": total, "freed": 0, "pruned": 0, "busy": [], "dry_run": dry_run}
    for newest, addr, size in rows:  # oldest first
        if newest >= floor or (newest >= old and total - rep["freed"] <= cap):
            break
        if dry_run:
            if not orch_held(raw_rows, addr):
                rep["freed"], rep["pruned"] = rep["freed"] + size, rep["pruned"] + 1
            continue
        freed, n, busy = prune_function(addr, s["min_age_min"] * 60, raw_rows, now)
        if busy:
            rep["busy"].append(addr)
        rep["freed"] += freed
        rep["pruned"] += bool(n)
    rep["kept"] = total - rep["freed"]
    if log:
        log(f"build prune sweep: {rep['pruned']} of {rep['folders']} folders, {rep['freed'] / 2 ** 30:.2f} of "
            f"{total / 2 ** 30:.2f} GB freed" + (" (dry run)" if dry_run else "")
            + (f", {len(rep['busy'])} busy" if rep["busy"] else ""))
    return rep


def maybe_sweep(cfg, log=None, background=True):
    """A sweep when the last one is `interval_h` old (state/build_prune.json), in a daemon thread unless
    background=False. Returns the thread, the report (foreground), or None when not due."""
    s = settings(cfg)
    if not s["enabled"]:
        return None
    now = time.time()

    def take(cur):  # under the stamp's lock: one process takes a due sweep
        if now - float(cur.get("last", 0)) < s["interval_h"] * 3600:
            return False
        cur.update(last=now, pid=os.getpid())
        return True

    try:
        if not filelock.update_json(stamp_file(), take, {})[1]:
            return None
    except Exception:  # noqa: BLE001  (the stamp is busy or unreadable: no sweep this time)
        return None

    def run():
        try:
            rep = sweep(cfg, log=log)
            filelock.update_json(stamp_file(), lambda c: c.update(report={k: v for k, v in rep.items()
                                                                           if k != "busy"}), {})
            return rep
        except Exception as e:  # noqa: BLE001  (housekeeping: never stops the run that started it)
            if log:
                log(f"build prune sweep failed: {type(e).__name__}: {e}")
            return None

    if not background:
        return run()
    t = threading.Thread(target=run, daemon=True, name="build-prune")
    t.start()
    return t


def report_text(rep):
    return json.dumps({k: v for k, v in rep.items() if k != "busy"} | {"busy": len(rep.get("busy") or [])})
