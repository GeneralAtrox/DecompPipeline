"""Readable runs build against a validated, run-local snapshot of the headers, never the live files.

Designers edit readable/include and readable/names while readable runs use them. A half-written header used to
block every function of a run at preflight. Now `refine.py run`, `verify` and `check` start by copying
readable/include (*.h) and readable/names (*.json) into a run-local directory under state/readable_snapshots/, writing
the snapshot's own game.h from its names, and validating it: the header lint (core/header_lint) and a game.h build at
/W4 /WX. The run then points the readable stage at the snapshot (use()): every sandbox, preflight, check and the
agents' check.py (`refine.py check --headers DIR`) read only it, so a designer's edit during the run changes nothing
the run compiles against. The directory is removed when the run ends.

A snapshot that validates is recorded as the last good one (state/readable_snapshots/good-HASH/, last_good.json).
When the live headers do not validate, `run` checks again every readable_header_retry_s (default 60) for up to
readable_header_wait_s (default 900), printing why; then it uses the last good snapshot, else it exits with the
problems. `verify` and `check` neither wait nor fall back: a verdict is against the current headers, so STALE
still means stale.

HASH (16 hex digits of the snapshot's files) is stamped in every readable attempt and ledger row (item 3).
"""
import datetime
import hashlib
import itertools
import os
import pathlib
import shutil
import time

from . import filelock, header_lint, paths, readable

SNAPS = paths.STATE / "readable_snapshots"
GOOD = SNAPS / "last_good.json"
RUN_KEEP_S = 2 * 86400  # a run directory left by a killed run is removed after this
GOOD_KEEP = 3  # good-* directories kept besides the recorded one
_SEQ = itertools.count(1)
ACTIVE = None  # the Snapshot this process's readable stage reads (use())


class HeaderError(Exception):
    pass


class Snapshot:
    def __init__(self, d, source):
        self.dir, self.source = pathlib.Path(d), source
        self.include, self.names = self.dir / "include", self.dir / "names"
        h = self.dir / "HASH"
        self.hash = h.read_text(encoding="utf-8").strip() if h.exists() else None

    def release(self):
        """Remove a run-local snapshot (never a good-* one)."""
        if self.dir.name.startswith("run-"):
            shutil.rmtree(self.dir, ignore_errors=True)


def live_dirs():
    """(readable/include, readable/names) of the workspace: the live files, whatever the readable stage uses now."""
    return readable.READABLE / "include", readable.READABLE / "names"


def tree_hash(d):
    h = hashlib.sha256()
    for sub in ("include", "names"):
        for f in sorted(p for p in (d / sub).rglob("*") if p.is_file()):
            h.update(f"{sub}/{f.relative_to(d / sub).as_posix()}\0".encode())
            h.update(f.read_bytes())
            h.update(b"\0")
    return h.hexdigest()[:16]


def _copy(include, names, dest):
    (dest / "include").mkdir(parents=True, exist_ok=True)
    (dest / "names").mkdir(parents=True, exist_ok=True)
    for f in sorted(include.rglob("*.h")) if include.exists() else []:
        out = dest / "include" / f.relative_to(include)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, out)
    for f in sorted(names.glob("*.json")) if names.exists() else []:
        shutil.copyfile(f, dest / "names" / f.name)


def validate(cfg, d, compile_fn=None):
    """Problems of the header tree in d (include/, names/), [] when it is good. Writes d/include/game.h from d's names
    first (the generated header the port and every build use), then lints, then builds game.h (lint clean only)."""
    problems = []
    try:
        reg = readable.Registry(names_dir=d / "names", include_dir=d / "include")
        (d / "include" / "game.h").write_text(readable.game_h(reg), encoding="utf-8")
    except readable.RegistryError as e:
        problems.append(f"names registry: {e}")
    problems += header_lint.lint(d / "include")
    if not problems:
        problems += (compile_fn or header_lint.compile_game_h)(cfg, d / "include", d / "probe")
    shutil.rmtree(d / "probe", ignore_errors=True)
    (d / "HASH").write_text(tree_hash(d) + "\n", encoding="utf-8")
    return problems


def _new_dir(prefix):
    SNAPS.mkdir(parents=True, exist_ok=True)
    d = SNAPS / f"{prefix}-{datetime.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{next(_SEQ)}"
    d.mkdir()
    return d


def _prune():
    """Run directories of killed runs and copies left half-made, older than RUN_KEEP_S."""
    if not SNAPS.exists():
        return
    old = time.time() - RUN_KEEP_S
    for d in SNAPS.iterdir():
        if d.is_dir() and d.name.startswith(("run-", "tmp-")) and d.stat().st_mtime < old:
            shutil.rmtree(d, ignore_errors=True)


def take(cfg, compile_fn=None):
    """(Snapshot of the live headers in a new run-local directory, its problems)."""
    _prune()
    d = _new_dir("run")
    _copy(*live_dirs(), d)
    problems = validate(cfg, d, compile_fn)
    return Snapshot(d, "live"), problems


def record_good(snap):
    """Keep a copy of a validated snapshot as good-HASH and record it in last_good.json."""
    gd = SNAPS / f"good-{snap.hash}"
    if not (gd / "HASH").exists():
        tmp = _new_dir("tmp")
        _copy(snap.include, snap.names, tmp)
        shutil.copyfile(snap.dir / "HASH", tmp / "HASH")
        try:
            os.replace(tmp, gd)
        except OSError:  # another run recorded the same snapshot first
            shutil.rmtree(tmp, ignore_errors=True)
    rec = {"hash": snap.hash, "dir": gd.name, "at": datetime.datetime.now().isoformat(timespec="seconds")}
    filelock.update_json(GOOD, lambda data: (data.clear(), data.update(rec)), {})
    goods = sorted((d for d in SNAPS.glob("good-*") if d.name != gd.name), key=lambda d: d.stat().st_mtime)
    for d in goods[:-GOOD_KEEP] if len(goods) > GOOD_KEEP else []:
        shutil.rmtree(d, ignore_errors=True)


def last_good():
    """(directory, record) of the last good snapshot, or (None, None)."""
    rec = paths.read_json(GOOD) if GOOD.exists() else None
    if rec and (SNAPS / rec.get("dir", "-") / "HASH").exists():
        return SNAPS / rec["dir"], rec
    return None, None


def acquire(cfg, log=print, wait_s=None, retry_s=None, fallback=True, compile_fn=None):
    """A validated snapshot for one run: the live headers when they validate; else, after waiting up to wait_s
    (checking every retry_s), the last good snapshot when fallback; else HeaderError with the problems."""
    wait_s = cfg.get("readable_header_wait_s", 900) if wait_s is None else wait_s
    retry_s = max(0.05, cfg.get("readable_header_retry_s", 60) if retry_s is None else retry_s)
    deadline = time.monotonic() + wait_s
    while True:
        snap, problems = take(cfg, compile_fn)
        if not problems:
            record_good(snap)
            log(f"headers: snapshot {snap.hash} of the live headers (lint clean, game.h builds at /W4 /WX)")
            return snap
        snap.release()
        why = f"the live readable headers do not validate ({len(problems)} problem(s)): " + "; ".join(problems[:4])
        left = deadline - time.monotonic()
        if left <= 0:
            break
        pause = min(retry_s, left)
        log(f"headers: {why}\nheaders: waiting {pause:.0f} s, then checking again (at most {wait_s} s in all, "
            "readable_header_wait_s); `refine.py header` names every problem")
        time.sleep(pause)
    good, rec = last_good() if fallback else (None, None)
    if good is None:
        raise HeaderError(why + ("" if not fallback else "; no last good snapshot is recorded") +
                          ". Fix the headers (`refine.py header` names every problem) and start again.")
    snap = Snapshot(_new_dir("run"), "last good")
    _copy(good / "include", good / "names", snap.dir)
    problems = validate(cfg, snap.dir, compile_fn)
    snap.hash = (snap.dir / "HASH").read_text(encoding="utf-8").strip()
    if problems:
        snap.release()
        raise HeaderError(f"{why}; and the last good snapshot {rec['hash']} no longer validates: "
                          + "; ".join(problems[:4]))
    log(f"headers: {why}\nheaders: this run uses the last good snapshot {snap.hash} (recorded {rec['at']}); live "
        "header and names changes since then are not in it")
    return snap


def use(snap):
    """Point this process's readable stage (registry, lean headers, judge) at the snapshot."""
    global ACTIVE
    readable.INCLUDE, readable.NAMES, ACTIVE = snap.include, snap.names, snap


def use_dir(d):
    """`refine.py check --headers DIR`: the run's snapshot, already validated by the run."""
    snap = Snapshot(d, "run")
    if not (snap.include / "game.h").exists():
        raise HeaderError(f"{d} is not a header snapshot (no include/game.h)")
    use(snap)
    return snap


def active_hash():
    return ACTIVE.hash if ACTIVE else None


def stamp_note(was, now):
    """What a failing file's recorded snapshot says (verify, report only)."""
    if not now:
        return "headers not snapshotted"
    if not was:
        return f"no snapshot recorded for its last pass (now {now})"
    if was == now:
        return f"it passed against these same headers ({now}): not a header change"
    return f"written against older headers ({was}, now {now})"
