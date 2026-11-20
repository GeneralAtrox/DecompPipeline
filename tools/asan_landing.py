"""Every kit landing that changes host C carries an AddressSanitizer result (AGENTS.md "Test rules", rule 3; the user,
2026-11-09: "Yes, every landing").

Nearest existing: tools/fails_before.py - it checks the Fails-before trailer of bug-fix commits; this checks another trailer, on
the commits that change the port kit's host C, and turns a run's results into it.

    py -3.12 tools/asan_landing.py trailer --console dreamcast RESULTS [--tree REV]
    py -3.12 tools/asan_landing.py trailer --console dreamcast --game spawn --on REV RESULTS
    py -3.12 tools/asan_landing.py check A..B

A change to the kit's host C (port/runtime, pvr, sdk, base, saturn: .c .cpp .h .hpp .inc) gets, before it lands, one ASan
build per console it touches (pvr and sdk: Dreamcast; saturn: Saturn; runtime and base: both), each of which first reports the
planted overflow (DP_ASAN_PLANT=1, port/base/host_main.cpp), then one short replay of each of that console's core games with no
ASan error. The runner writes a line per run (the format of the first runs, 2026-11-09):
    run plant_<game>: exit E in S s; ASan errors N; ...
    run <game>_<what>: exit E in S s; ASan errors N; ...
trailer reads them and prints the console's line for the landing's last commit, or refuses (exit 1) if a build did not report
its plant, a replay had an ASan error or did not end, or a core game has no replay:
    ASan: dreamcast tree 1a2b3c4d5e6f: plant caught; tc_frontend 0 errors; ps2_111340 0 errors; spawn_attract 0 errors
The tree is the one the builds were made from (--tree, default HEAD), so the line holds only on a commit of that tree: amend the
message, not the code. A change ASan can't see (a comment, a log text) says so: `ASan: n/a <why>`.
A core game that can't build on the landing's tree (its kit line is on another contract) is run on its kit line's merge of the
landing instead (the user, 2026-11-10: "Count kit-line merge runs"). --game and --on name the game and that commit, and the line
holds for every commit that commit contains:
    ASan: dreamcast spawn on 9f8e7d6c5b4a: plant caught; spawn_attract 0 errors
A commit can't carry a line naming a merge of itself, so these lines go on a later commit (the landing's own last commit when
the kit lines merge the landing before it lands), and the named commit must be in the checkout: push the kit line.
check fails on a commit in the range that changes kit host C and is not covered: for every console it touches, it or a later
commit of the range carries that console's line on that later commit's own tree, or a line per core game on a commit that
contains it. An n/a line covers its own commit only.
"""
import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRAILER = "ASan:"
HOST = re.compile(r"^port/(runtime|pvr|sdk|base|saturn)/.*\.(c|cpp|h|hpp|inc)$")
CONSOLES = {"pvr": ("dreamcast",), "sdk": ("dreamcast",), "saturn": ("saturn",), "runtime": ("dreamcast", "saturn"),
            "base": ("dreamcast", "saturn")}
CORE = {"dreamcast": ("tc", "ps2", "spawn"), "saturn": ("robopit",)}  # the core games (CLAUDE.md), by their run names
RUN = re.compile(r"^run (\S+): exit (\S+) in \d+ s; ASan errors (\d+)")
LINE = re.compile(r"^(\w+) tree ([0-9a-f]{12}): plant caught; (.+)$")
ON = re.compile(r"^(\w+) (\w+) on ([0-9a-f]{12}): plant caught; (.+)$")


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def touched(files):
    """The consoles a commit's files touch through the kit's host C."""
    out = set()
    for f in files:
        m = HOST.match(f)
        if m:
            out.update(CONSOLES[m.group(1)])
    return out


def trailer(console, results, tree, game=None, on=None):
    """(line, None) or (None, why it is refused). With game and on: that one core game's run on commit on."""
    runs = [m.groups() for m in map(RUN.match, results.splitlines()) if m]
    if game and game not in CORE[console]:
        return None, f"{game} is not a core game of {console} ({', '.join(CORE[console])})"
    games = (game,) if game else CORE[console]
    plants = {name[len("plant_"):]: int(n) for name, _, n in runs if name.startswith("plant_")}
    replays = [(name, code, int(n)) for name, code, n in runs if not name.startswith("plant_")]
    for gm in games:
        if plants.get(gm, 0) < 1:
            return None, f"{gm}'s build did not report its planted overflow" + (" (no plant run)" if gm not in plants else "")
    done = []
    for gm in games:
        mine = [r for r in replays if r[0].split("_")[0] == gm]
        if not mine:
            return None, f"{gm} has no replay"
        for name, code, n in mine:
            if n or code != "0":
                return None, f"{name}: {n} ASan error(s), exit {code}"
            done.append(f"{name} 0 errors")
    where = f"{game} on {on[:12]}" if game else f"tree {tree[:12]}"
    return f"{TRAILER} {console} {where}: plant caught; " + "; ".join(done), None


def lines_of(msg):
    return [ln[len(TRAILER):].strip() for ln in msg.splitlines() if ln.startswith(TRAILER)]


def covers(lines, tree, cwd=ROOT):
    """The consoles a commit's lines cover on its tree, the (console, game, commit) runs on kit-line merges, and whether it
    says n/a (with a reason); bad lines are problems."""
    got, merged, na, bad = set(), [], False, []
    for body in lines:
        if body.startswith("n/a"):
            na = na or bool(body[3:].strip())
            if not body[3:].strip():
                bad.append("n/a with no reason")
            continue
        o = ON.match(body)
        if o:
            console, game, rev, runs = o.groups()
            full = git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", cwd=cwd).stdout.strip()
            if console not in CORE or game not in CORE[console]:
                bad.append(f"{game} is not a core game of {console}")
            elif f"{game}_" not in runs:
                bad.append(f"{console} {game}'s line names no {game} replay")
            elif not full:
                bad.append(f"{console} {game}'s run was on {rev}, which is not in this checkout (push and fetch its kit line)")
            else:
                merged.append((console, game, full))
            continue
        m = LINE.match(body)
        if not m or m.group(1) not in CORE:
            bad.append(f"not an ASan line: {body[:60]}")
        elif not tree.startswith(m.group(2)):
            bad.append(f"{m.group(1)}'s run was on tree {m.group(2)}, not this commit's {tree[:12]}")
        elif any(f"{gm}_" not in m.group(3) for gm in CORE[m.group(1)]):
            bad.append(f"{m.group(1)}'s line leaves out a core game")
        else:
            got.add(m.group(1))
    return got, merged, na, bad


def contains(rev, sha, cwd=ROOT):
    """Whether commit rev has sha in its history (or is sha)."""
    return git("merge-base", "--is-ancestor", sha, rev, cwd=cwd).returncode == 0


def problems(rng, cwd=ROOT):
    bad, pending = [], []  # pending: (sha, subject, consoles) not yet covered
    for sha in git("rev-list", "--reverse", "--no-merges", rng, cwd=cwd).stdout.split():
        msg = git("log", "-1", "--format=%B", sha, cwd=cwd).stdout
        files = git("show", "--name-only", "--format=", sha, cwd=cwd).stdout.split("\n")
        tree = git("rev-parse", f"{sha}^{{tree}}", cwd=cwd).stdout.strip()
        need = touched(files)
        if need:
            pending.append((sha, msg.split("\n")[0], need, need))  # (sha, subject, consoles it needs, not yet covered)
        lines = lines_of(msg)
        if not lines:
            continue
        got, merged, na, why = covers(lines, tree, cwd)
        bad += [f"{sha[:12]} {msg.splitlines()[0][:60]}: {w}" for w in why]
        if na and need:
            pending = [p for p in pending if p[0] != sha]
        left = []
        for p in pending:
            def done(c, p=p):  # this commit's tree line, or a run per core game on a commit containing p
                return c in got or all(any(mc == c and mg == g and contains(rev, p[0], cwd) for mc, mg, rev in merged)
                                       for g in CORE[c])
            still = {c for c in p[3] if not done(c)}
            if still:
                left.append((*p[:3], still))
        pending = left
    for sha, subject, need, still in pending:
        what = "it" if still == need else ", ".join(sorted(still))
        bad.append(f"{sha[:12]} {subject[:60]}: changes the kit's host C ({', '.join(sorted(need))}) and no {TRAILER} line covers {what}")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="what", required=True)
    t = sub.add_parser("trailer")
    t.add_argument("--console", required=True, choices=sorted(CORE))
    t.add_argument("--tree", default="HEAD")
    t.add_argument("--game", help="one core game, run on --on (its kit line's merge of the landing)")
    t.add_argument("--on", help="the commit the --game run was built from")
    t.add_argument("results")
    c = sub.add_parser("check")
    c.add_argument("range")
    a = ap.parse_args(argv)
    if a.what == "trailer":
        if bool(a.game) != bool(a.on):
            ap.error("--game and --on go together")
        tree = git("rev-parse", f"{a.tree}^{{tree}}").stdout.strip()
        on = a.on and git("rev-parse", "--verify", "--quiet", f"{a.on}^{{commit}}").stdout.strip()
        if a.on and not on:
            ap.error(f"--on {a.on} is not a commit in this checkout")
        line, why = trailer(a.console, pathlib.Path(a.results).read_text(encoding="utf-8", errors="replace"), tree, a.game, on)
        print(line or f"refused: {why}")
        return 0 if line else 1
    bad = problems(a.range)
    print("\n".join(bad) or "every kit host-C change is covered")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
