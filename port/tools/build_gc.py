"""Removes a game's old agent builds from <game>/build, so that `build.py --build-dir NAME` stops piling up folders.

    python port/tools/build_gc.py --game DIR [--days 5] [--dry-run]       (build.py runs it by itself)

Every `build.py --build-dir NAME` makes build/NAME (a configured CMake + Ninja build, the exe, the object files),
build/NAME_gen, and later NAME_run, NAME_regress, NAME_lockstep and NAME.log; nothing removes them. A build is
removed, with those companions, when all of these hold:
- it is a build folder of this kit: build/NAME has CMakeCache.txt and build.ninja, and the cache's PORT_GEN is a
  folder called NAME_gen (what build.py configures); any other folder (run output, references, pictures, notes)
  is left alone, and so is a build this cannot confirm;
- NAME is not x86, x86chk, gen, run, regress or reference, and is not the build build.py is running now;
- nothing in the build or its companions was written for DAYS days (default 5): the newest file inside, the
  `.used` stamp build.py touches when it builds there (folder dates are not trusted), or the log;
- no running process uses it: its executable, working folder or command line (a path inside it, or
  `--build-dir NAME`) is not in the build; and the folder can be renamed (Windows refuses while a file in it is open).
Only <game>/build is ever touched. build.py prints one line when it removed something. Off: `build.py
--no-build-gc`, or DP_BUILD_GC=0 in the environment. Without psutil (requirements.txt) nothing is removed.

The sweep runs when build.py exits, at most every INTERVAL_H hours per game (the stamp build/.build_gc), and not in
the launcher's `--progress` view: a player's start never waits for it. A folder that is still fresh stops its own
check at the first new file, so a sweep over a busy build folder costs a few thousand stats.

Nearest existing: core/build_prune.py - it prunes the compiler outputs of the pipeline's judge folders build/ADDR
by function, with its claims and ledger; this removes whole port build folders by name and age, game-free.
"""
import argparse
import os
import pathlib
import re
import shutil
import stat
import sys
import time

DAYS = 5.0
INTERVAL_H = 6.0
PROTECTED = frozenset({"x86", "x86chk", "gen", "run", "regress", "reference"})
COMPANIONS = ("_gen", "_run", "_regress", "_lockstep")
NAME_RE = re.compile(r"[\w.-]+")
STAMP, USED = ".build_gc", ".used"
REPARSE = 0x400  # a junction or symlink: never walked into


def enabled():
    return os.environ.get("DP_BUILD_GC", "1").strip().lower() not in ("0", "off", "no", "false")


def touch_used(bdir):
    """The stamp build.py leaves in the build it works in."""
    try:
        (pathlib.Path(bdir) / USED).write_text(str(int(time.time())), encoding="ascii")
    except OSError:
        pass


def walk(path, cutoff):
    """(newest mtime, bytes, fresh) under path; stops at the first file newer than cutoff (fresh), then the
    totals are partial. Junctions and symlinks are skipped."""
    newest, size, stack = 0.0, 0, [str(path)]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if getattr(st, "st_file_attributes", 0) & REPARSE or stat.S_ISLNK(st.st_mode):
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(e.path)
                continue
            size += st.st_size
            newest = max(newest, st.st_mtime)
            if newest > cutoff:
                return newest, size, True
    return newest, size, False


def confirmed(d, name):
    """True when d is a configured build of this kit written for build/NAME."""
    cache = d / "CMakeCache.txt"
    if not cache.is_file() or not (d / "build.ninja").is_file():
        return False
    try:
        text = cache.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    m = re.search(r"^PORT_GEN:\w+=(.*)$", text, re.M)
    return bool(m) and m.group(1).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower() == f"{name}_gen".lower()


def group(build, name):
    """The paths of one build: its folder and companions that exist."""
    out = [build / name] + [build / (name + s) for s in COMPANIONS] + [build / (name + ".log")]
    return [p for p in out if p.exists()]


def users(psutil, paths):
    """{path: reason} for the paths a running process uses."""
    roots = {str(p).replace("\\", "/").lower().rstrip("/"): p for p in paths}
    busy = {}
    for pr in psutil.process_iter(["exe", "cwd", "cmdline"]):
        try:
            info = pr.info
            texts = [info.get("exe") or "", info.get("cwd") or ""] + list(info.get("cmdline") or [])
        except (psutil.Error, OSError):
            continue
        low = [t.replace("\\", "/").lower() for t in texts if t]
        args = [t.replace("\\", "/").lower() for t in (info.get("cmdline") or [])]
        for root, p in roots.items():
            if root in busy:
                continue
            name = p.name.lower()
            if any(t == root or t.startswith(root + "/") or (root + "/") in t for t in low) or \
                    any(a == "--build-dir" and i + 1 < len(args) and args[i + 1] == name for i, a in enumerate(args)) or \
                    any(a == f"--build-dir={name}" for a in args):
                busy[root] = f"pid {pr.pid}"
    return {roots[r]: why for r, why in busy.items()}


def remove(p):
    """Renames p aside (refused while a file in it is open), then deletes it. False when it could not be renamed."""
    aside = p.with_name(".gc-" + p.name)
    try:
        if aside.exists():
            _delete(aside)
        os.replace(p, aside)
    except OSError:
        return False
    _delete(aside)
    return True


def _delete(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, onerror=lambda f, path, _e: (os.chmod(path, stat.S_IWRITE), f(path)))
    else:
        p.unlink(missing_ok=True)


def sweep(build, keep=(), days=DAYS, now=None, dry_run=False):
    """Removes the old builds under build. Returns [(name, bytes)] of what was (or with dry_run would be) removed."""
    build = pathlib.Path(build)
    if not build.is_dir():
        return []
    now = time.time() if now is None else now
    cutoff = now - days * 86400.0
    keep = {k.lower() for k in keep} | PROTECTED
    stale = []
    for e in sorted(build.iterdir(), key=lambda x: x.name):
        name = e.name
        if name.lower() in keep or name.startswith(".") or not NAME_RE.fullmatch(name) or not e.is_dir() \
                or e.is_symlink() or not confirmed(e, name):
            continue
        used = build / name / USED
        if used.is_file() and used.stat().st_mtime > cutoff:
            continue
        total, fresh = 0, False
        members = group(build, name)
        for p in members:
            if p.is_dir():
                newest, size, hot = walk(p, cutoff)
            else:
                st = p.stat()
                newest, size, hot = st.st_mtime, st.st_size, st.st_mtime > cutoff
            total += size
            if hot:
                fresh = True
                break
        if not fresh:
            stale.append((name, members, total))
    if not stale:
        return []
    import psutil  # a requirement of the pipeline; without it nothing is removed
    busy = users(psutil, [p for _n, members, _t in stale for p in members])
    done = []
    for name, members, total in stale:
        if any(p in busy for p in members):
            continue
        if dry_run:
            done.append((name, total))
        elif all(remove(p) for p in members):
            done.append((name, total))
    return done


def maybe_sweep(build, keep=(), days=DAYS, interval_h=INTERVAL_H, say=print):
    """build.py's call: at most every interval_h hours; prints one line when it removed something. Never raises."""
    try:
        build = pathlib.Path(build)
        stamp = build / STAMP
        if not enabled() or not build.is_dir():
            return
        try:
            import psutil  # noqa: F401
        except ImportError:
            return
        if stamp.is_file() and time.time() - stamp.stat().st_mtime < interval_h * 3600.0:
            return
        stamp.write_text(str(int(time.time())), encoding="ascii")
        done = sweep(build, keep, days)
        if done:
            say(f"build gc: removed {len(done)} agent build folder(s) unused for {days:g}+ days, freed "
                f"{sum(s for _n, s in done) / 2**30:.1f} GB (off: --no-build-gc or DP_BUILD_GC=0)")
    except Exception as e:  # a clean-up must never fail a build
        say(f"build gc: skipped ({type(e).__name__}: {e})")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, help="the folder with game.json")
    ap.add_argument("--days", type=float, default=DAYS)
    ap.add_argument("--keep", action="append", default=[], help="a build name to leave (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="list what would go")
    a = ap.parse_args(argv)
    done = sweep(pathlib.Path(a.game) / "build", a.keep, a.days, dry_run=a.dry_run)
    for name, size in done:
        print(f"{'would remove' if a.dry_run else 'removed'} {name}: {size / 2**20:.0f} MB")
    print(f"{len(done)} build(s), {sum(s for _n, s in done) / 2**30:.2f} GB")


if __name__ == "__main__":
    sys.exit(main())
