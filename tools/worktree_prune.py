"""List (and with --apply remove) DP worktrees whose work is done: the standing rule for worktree build-up.

usage: python tools/bounded.py 600 python tools/worktree_prune.py [--repo DIR] [--base REF ...] [--idle-h H]
           [--sizes] [--apply] [--json FILE]

A worktree is removable when all of these hold:
  - it is not the main checkout, not the one this runs from, and not locked (git worktree lock);
  - it is clean: no changed or untracked files (git status; ignored files such as build/ do not count);
  - its HEAD is landed: reachable from one of --base (default master; each REF repeatable), so no commit is lost;
  - nothing changed in it for --idle-h hours (default 24: its index and HEAD files), so no session is still using it.
--apply removes exactly those with `git worktree remove` (never --force: git itself refuses a dirty one) and then
`git worktree prune`; the branch refs stay. Without --apply nothing is changed. --sizes adds each folder's size
(slow on large build folders).
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

GIT_S = 60


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=GIT_S)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


def worktrees(repo):
    """[{path, head, branch, locked, bare}] from git worktree list --porcelain; the first is the main checkout."""
    out, cur = [], None
    for line in git(repo, "worktree", "list", "--porcelain").stdout.splitlines():
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "head": None, "branch": None, "locked": False, "bare": False}
            out.append(cur)
        elif cur is not None and key == "HEAD":
            cur["head"] = val
        elif cur is not None and key == "branch":
            cur["branch"] = val.removeprefix("refs/heads/")
        elif cur is not None and key in ("locked", "bare"):
            cur[key] = True
    return out


def git_dir(path):
    """The worktree's own git folder (its .git file names it), else None."""
    g = pathlib.Path(path) / ".git"
    if g.is_file():
        text = g.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            return pathlib.Path(text[7:].strip())
    return g if g.is_dir() else None


def idle_h(path, now):
    """Hours since its index or HEAD last changed (a commit, a checkout or a staged file all touch one)."""
    d = git_dir(path)
    times = [p.stat().st_mtime for p in ((d / "index"), (d / "HEAD")) if d and p.exists()]
    return (now - max(times)) / 3600 if times else None


def size_mb(path):
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return round(total / 1e6)


def judge(repo, wt, bases, idle_min, now, here):
    """wt with "dirty", "landed_in", "idle_h", "removable" and, when not removable, "why"."""
    p = pathlib.Path(wt["path"])
    if not p.is_dir():
        return dict(wt, removable=False, why="folder gone (git worktree prune clears it)")
    h = idle_h(p, now)  # before status, and status with no optional locks: it would refresh (rewrite) the index
    st = git(p, "--no-optional-locks", "status", "--porcelain", check=False)
    wt = dict(wt, dirty=len(st.stdout.splitlines()) if st.returncode == 0 else None, idle_h=None, landed_in=None)
    wt["landed_in"] = next((b for b in bases if wt["head"] and git(repo, "merge-base", "--is-ancestor", wt["head"], b,
                                                                      check=False).returncode == 0), None)
    wt["idle_h"] = None if h is None else round(h, 1)
    why = ("the main checkout" if wt.get("main") else "this checkout" if p.resolve() == here
           else "locked" if wt["locked"] else "git status failed" if wt["dirty"] is None
           else f"{wt['dirty']} changed or untracked file(s)" if wt["dirty"]
           else f"not landed in {', '.join(bases)}" if not wt["landed_in"]
           else f"changed {wt['idle_h']} h ago (under {idle_min} h)" if wt["idle_h"] is None or wt["idle_h"] < idle_min
           else None)
    return dict(wt, removable=why is None, why=why)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument("--base", action="append")
    ap.add_argument("--idle-h", type=float, default=24.0)
    ap.add_argument("--sizes", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    bases = a.base or ["master"]
    for b in bases:
        git(a.repo, "rev-parse", "--verify", "--quiet", b)
    here, now = pathlib.Path(a.repo).resolve(), time.time()
    rows = worktrees(a.repo)
    if rows:
        rows[0]["main"] = True
    rows = [judge(a.repo, wt, bases, a.idle_h, now, here) for wt in rows if not wt["bare"]]
    for r in rows:
        if a.sizes and r["removable"]:
            r["size_mb"] = size_mb(r["path"])
        if a.apply and r["removable"]:
            res = git(a.repo, "worktree", "remove", r["path"], check=False)
            r["removed"] = res.returncode == 0
            if res.returncode:
                r["why"] = f"git worktree remove: {res.stderr.strip()[:200]}"
    if a.apply:
        git(a.repo, "worktree", "prune", check=False)
    keep = [r for r in rows if not r["removable"]]
    gone = [r for r in rows if r["removable"]]
    verb = "removed" if a.apply else "removable"
    print(f"{len(rows)} worktrees: {len(gone)} {verb}, {len(keep)} kept (base {', '.join(bases)}, idle {a.idle_h} h)")
    for r in gone:
        state = "" if not a.apply else " removed" if r.get("removed") else f" NOT removed: {r['why']}"
        size = f" {r['size_mb']} MB" if "size_mb" in r else ""
        print(f"  {verb}: {r['path']} [{r['branch'] or 'detached'}] landed in {r['landed_in']}{size}{state}")
    for r in keep:
        print(f"  kept: {r['path']} [{r['branch'] or 'detached'}] {r['why']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
