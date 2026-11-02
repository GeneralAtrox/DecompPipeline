"""A bug fix's check must fail on the unfixed build (AGENTS.md "Test rules"). A check that passes there didn't reproduce the bug.

Nearest existing: tools/mutation_check.py - it seeds its own faults into matched C; this runs the author's check on the real old code.

    py -3.12 tools/fails_before.py prove [--base REF] [--overlay PATH].. [--seconds N] -- CMD ARG..
    py -3.12 tools/fails_before.py prove --unfixed-cmd "CMD ON THE UNFIXED BUILD" -- CMD ARG..
    py -3.12 tools/fails_before.py check A..B [--rerun]

prove checks out --base (default master) in a throwaway worktree, about 2 s, and copies in the test files that differ from it,
so the new check meets the old code. CMD must fail there on what it checks and pass here. A port's check takes --unfixed-cmd
instead, so nothing is built twice: for example regress.py --build-dir x86-unfixed.
It prints the trailer for the commit message, which is the whole record:
    Fails-before: BASE CMD => the failing line
A change no check can reproduce says so: `Fails-before: n/a <why>`.
check fails on a commit in the range whose subject says it fixes something and has no trailer; --rerun runs each trailer's
command again on the commit's parent. A subject that only describes the behaviour isn't seen: the rule still applies to it.
"""
import argparse
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRAILER = "Fails-before:"
FIX = re.compile(r"\b(fix|fixes|fixed|bug|regression|crash|crashes|hang|hangs)\b", re.I)
LANDING = re.compile(r"^(land |merge )|\blanding fix", re.I)  # repairs a landing: the bug never reached master
TESTS = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*$")
# a check that dies on a name the old code lacks has not met the bug
BROKEN = re.compile(r"\b(ModuleNotFoundError|ImportError|AttributeError|NameError|SyntaxError|FileNotFoundError)\b")


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", timeout=120)


def run(cmd, cwd, seconds):
    """(exit code, output), or (None, why) when it ran out of time."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=seconds,
                           shell=isinstance(cmd, str))
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return None, f"no result in {seconds} s"


def outcome(rc, out):
    """passed, failed (on what the check says) or broken (it never met the bug), and the line that says so."""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if rc is None:
        return "broken", out
    if rc == 0:
        return "passed", ""
    said = [ln for ln in lines if ln.startswith("E ") or "AssertionError" in ln or ln.startswith(("FAIL", "ERROR"))]
    if re.search(r"\berrors? during collection\b|\bno tests ran\b|\bNo such file\b", out) or rc in (4, 5, 127):
        return "broken", (said or lines or [f"exit {rc}"])[0]
    if BROKEN.search("\n".join(said or lines)) and "AssertionError" not in out:
        return "broken", next(ln for ln in said or lines if BROKEN.search(ln))
    return "failed", (said or lines or [f"exit {rc}"])[0]


def changed_tests(base, cwd):
    names = git("diff", "--name-only", base, "--", cwd=cwd).stdout.splitlines()
    names += git("ls-files", "--others", "--exclude-standard", cwd=cwd).stdout.splitlines()
    return [n for n in names if TESTS.search(n) and (pathlib.Path(cwd) / n).is_file()]


def on_base(base, overlay, cmd, seconds, cwd):
    """Run cmd on base's tree with the test files overlaid: {path: bytes}."""
    with tempfile.TemporaryDirectory(prefix="fb") as t:
        wt = pathlib.Path(t) / "base"
        r = git("worktree", "add", "-q", "--detach", str(wt), base, cwd=cwd)
        if r.returncode:
            return None, f"no worktree of {base}: {r.stderr.strip()}"
        try:
            for n, data in overlay.items():
                (wt / n).parent.mkdir(parents=True, exist_ok=True)
                (wt / n).write_bytes(data)
            return run(cmd, wt, seconds)
        finally:
            git("worktree", "remove", "--force", str(wt), cwd=cwd)


def prove(a, cwd=ROOT):
    """0 and the trailer, or 1 and why this check proves nothing."""
    base = git("rev-parse", "--short=12", a.base, cwd=cwd).stdout.strip()
    if a.unfixed_cmd:
        rc, out = run(a.unfixed_cmd, cwd, a.seconds)
    else:
        names = set(changed_tests(base, cwd)) | set(a.overlay)
        rc, out = on_base(base, {n: (pathlib.Path(cwd) / n).read_bytes() for n in names}, a.cmd, a.seconds, cwd)
    what, line = outcome(rc, out)
    if what == "passed":
        print(f"refused: it passes on the unfixed build ({base or a.unfixed_cmd}), so it did not reproduce the bug")
        return 1
    if what == "broken":
        print(f"refused: it never met the bug on {base or a.unfixed_cmd}: {line[:200]}")
        return 1
    rc, out = run(a.cmd, cwd, a.seconds)
    if rc != 0:
        print(f"refused: it fails on the fixed build too: {outcome(rc, out)[1][:200]}")
        return 1
    print(f"{TRAILER} {base} {shlex.join(a.cmd)} => {line[:100]}")
    return 0


def commits(rng, cwd):
    """[(sha, subject, trailer lines)] of the range, without merges and commits that touch only docs."""
    out = []
    for sha in git("log", "--no-merges", "--format=%H", rng, cwd=cwd).stdout.split():
        msg = git("log", "-1", "--format=%B", sha, cwd=cwd).stdout
        files = git("show", "--name-only", "--format=", sha, cwd=cwd).stdout.split("\n")
        if all(f.endswith((".md", ".txt")) or not f for f in files):
            continue
        out.append((sha, msg.split("\n")[0], [ln for ln in msg.splitlines() if ln.startswith(TRAILER)]))
    return out


def rerun_trailer(sha, body, seconds, cwd):
    """Its command on the commit's parent, with the commit's test files: outcome()."""
    cmd = shlex.split(body.split(" => ")[0])[1:]
    changed = git("show", "--name-only", "--format=", sha, cwd=cwd).stdout.split("\n")
    tests = {n: subprocess.run(["git", "show", f"{sha}:{n}"], cwd=cwd, capture_output=True, timeout=60)
             for n in changed if TESTS.search(n)}
    rc, out = on_base(f"{sha}~1", {n: r.stdout for n, r in tests.items() if r.returncode == 0}, cmd, seconds, cwd)
    return outcome(rc, out)[0]


def problems(rng, cwd=ROOT, rerun=False, seconds=300):
    bad = []
    for sha, subject, trailers in commits(rng, cwd):
        for t in trailers:
            body = t[len(TRAILER):].strip()
            if body.startswith("n/a"):
                if not body[3:].strip():
                    bad.append(f"{sha[:12]} {subject[:60]}: n/a with no reason")
            elif rerun:
                what = rerun_trailer(sha, body, seconds, cwd)
                if what != "failed":
                    bad.append(f"{sha[:12]} {subject[:60]}: its check does not fail on the parent ({what})")
        if not trailers and FIX.search(subject) and not LANDING.search(subject):
            bad.append(f"{sha[:12]} {subject[:60]}: says it fixes something, and has no {TRAILER} trailer")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="what", required=True)
    p = sub.add_parser("prove")
    p.add_argument("--base", default="master")
    p.add_argument("--overlay", action="append", default=[])
    p.add_argument("--unfixed-cmd")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("cmd", nargs="+")
    c = sub.add_parser("check")
    c.add_argument("range")
    c.add_argument("--rerun", action="store_true")
    c.add_argument("--seconds", type=int, default=300)
    a = ap.parse_args(argv)
    if a.what == "prove":
        return prove(a, pathlib.Path.cwd())
    bad = problems(a.range, pathlib.Path.cwd(), a.rerun, a.seconds)
    print("\n".join(bad) or f"{a.range}: every fix commit says how it failed before")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
