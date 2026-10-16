"""Re-adopt earlier attempts that pass the current judge (batch 23): `refine.py verify --adopt` and `refine.py adopt`.

After batch 22's judge change the coordinator adopted 5 functions by hand: their old Haiku attempts passed the new
judge (`refine.py check ADDR --work runs/ADDR/readable/attempt-NN/work.c`, then a copy). Here that is a command:
- try_attempts: the function's attempts (runs/ADDR/readable/attempt-NN/work.c), newest first, each judged by the full
  readable judge (dev and hidden vectors, every lint) against the current headers; the first strict PASS wins. An
  attempt identical to the file being replaced is not judged again.
- adopt: the old readable/src file (if any) is backed up to state/readopt_backup/ADDR-TIME.c, the attempt copied over
  it (atomically), and the row becomes readable with `source: readopt attempt-NN` and an attempt entry
  {"kind": "readopt"} (no tokens).
- a STALE file none of whose attempts passes goes on the redo list state/redo.txt (`refine.py run @state/redo.txt
  --redo`).
Never adopted: an attempt without a PASS against the current judge (a FAIL, an infrastructure error, no harness).
The caller holds the function's claim (core/fn_claim).
"""
import datetime
import os
import re
import shutil

from . import filelock, header_snapshot, infra, paths, readable, refine
from . import readable_check as rcheck

BACKUP = paths.STATE / "readopt_backup"
REDO_LIST = paths.STATE / "redo.txt"


def attempt_files(addr):
    """[(n, path)] of the function's readable attempts' work.c, newest (highest n) first."""
    runs = paths.RUNS / addr / "readable"
    out = []
    for d in runs.glob("attempt-*") if runs.exists() else []:
        m = re.fullmatch(r"attempt-(\d+)", d.name)
        if m and (d / "work.c").exists():
            out.append((int(m.group(1)), d / "work.c"))
    return sorted(out, reverse=True)


def try_attempts(cfg, reg, addr, log=print, skip_text=None):
    """(n, path, result) of the newest attempt that strictly PASSes the current judge, or (None, None, tried):
    tried is [(n, why)] of the attempts that did not."""
    tried, seen = [], set()
    for n, path in attempt_files(addr):
        text = path.read_text(encoding="utf-8", errors="replace")
        if text == skip_text or text in seen:
            tried.append((n, "same text as a file already judged"))
            continue
        seen.add(text)
        try:
            res = rcheck.check_readable(cfg, addr, str(path), paths.BUILD / addr / "rd_readopt", reg=reg)
        except Exception as e:  # noqa: BLE001  (one attempt that cannot be judged is not adopted)
            tried.append((n, f"{type(e).__name__}: {e}"[:200]))
            continue
        if res["verdict"] == "PASS" and res.get("stage") == "compare" and not res.get("run_error"):
            return n, path, res
        why = "; ".join((res.get("lint") or []) + (res.get("build") or [])[:2])[:200] or \
            f"{res.get('failing')}/{res.get('total')} vectors differ"
        if infra.is_infra(res.get("build"), res.get("run_error")):
            why = f"infrastructure error, not judged: {why}"
        tried.append((n, f"{res['stage']}: {why}"))
    return None, None, tried


def adopt(addr, n, path, res):
    """Back up readable/src/ADDR.c (when there is one), copy attempt n over it, and record the row readable."""
    src = readable.SRC / f"{addr}.c"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = None
    if src.exists():
        BACKUP.mkdir(parents=True, exist_ok=True)
        backup = BACKUP / f"{addr}-{stamp}.c"
        shutil.copyfile(src, backup)
    src.parent.mkdir(parents=True, exist_ok=True)
    tmp = src.with_suffix(f".c.readopt{os.getpid()}")
    shutil.copyfile(path, tmp)
    os.replace(tmp, src)
    cur = header_snapshot.active_hash()
    source = f"readopt attempt-{n:02d}"

    def merge(led):
        row = led.setdefault(addr, {})
        atts = row.setdefault("attempts", [])
        atts.append({"n": len(atts) + 1, "kind": "readopt", "at": refine.now(), "from_attempt": n, "verdict": "PASS",
                     "stage": res["stage"], "failing": res.get("failing"), "total": res.get("total"), "headers": cur,
                     "backup": backup and paths.show(backup)})
        row.update(status="readable", verified=refine.now(), finished=refine.now(), headers=cur, source=source)
        for k in ("stale_headers", "note", "redo_failed", "redo_of", "sync_refused"):
            row.pop(k, None)
        return row

    with refine._LOCK:
        filelock.update_json(refine.LEDGER, merge, {})
    return source


def add_redo(addrs):
    """Add addresses to the redo list (state/redo.txt), each once; returns the list's length."""
    from .targets import read_list
    with filelock.locked(REDO_LIST):
        have = read_list(REDO_LIST) if REDO_LIST.exists() else []
        for a in addrs:
            if a not in have:
                have.append(a)
        filelock.write_atomic(REDO_LIST, "# refine.py run @state/redo.txt --redo: STALE files no earlier attempt "
                                         "re-adopts (refine.py verify --adopt)\n" + "\n".join(have) + "\n")
    return len(have)


def readopt(cfg, reg, addr, log=print):
    """Try the attempts of addr (the caller holds its claim); adopt the first strict PASS. Returns the source text
    (`readopt attempt-NN`) or None, with the log lines saying why each attempt was refused."""
    src = readable.SRC / f"{addr}.c"
    cur = src.read_text(encoding="utf-8", errors="replace") if src.exists() else None
    n, path, res = try_attempts(cfg, reg, addr, log, skip_text=cur)
    if n is None:
        for k, why in res:
            log(f"  {addr} attempt-{k:02d}: {why}")
        return None
    return adopt(addr, n, path, res)


def adopt_command(cfg, addrs, log=print):
    """`refine.py adopt ADDR... | @FILE`: for each function without a passing readable file (failed, stale, blocked,
    error ... rows), adopt its newest attempt that strictly passes. Returns {addr: outcome}."""
    from . import fn_claim
    reg = readable.Registry()
    out = {}
    for addr in addrs:
        row = refine.load_ledger().get(addr, {})
        if (readable.SRC / f"{addr}.c").exists() and row.get("status") == "readable":
            out[addr] = "skipped: already readable"
        elif refine.raw_missing(addr):
            out[addr] = f"skipped: {refine.raw_missing(addr)}"
        elif not attempt_files(addr):
            out[addr] = "no attempts"
        else:
            try:
                with fn_claim.claim(addr, "adopt", fn_claim.wait_s(cfg)):
                    if refine.load_ledger().get(addr, {}).get("status") == "running":
                        out[addr] = "skipped: a run is rewriting it"
                    else:
                        got = readopt(cfg, reg, addr, log)
                        out[addr] = f"ADOPTED ({got})" if got else "no attempt passes"
            except fn_claim.Busy as e:
                out[addr] = f"busy: {e}"
        log(f"{addr}: {out[addr]}")
    return out
