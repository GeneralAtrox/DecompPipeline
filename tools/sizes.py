"""sizes.py: the files over the 400-line cap (README "Layout"), and the ratchet that keeps it from drifting again.

The cap counts the kinds an agent reads whole (.py .java .c .h .html .md); data and proof json are exempt. DP had 65
such files over the cap when this landed (the four translators' translate.py the largest), so the cap is a ratchet:
tests/sizes_over_400.txt lists each file over it with its line count, and tests/test_sizes.py fails when a file not in
the list goes over the cap or a listed file grows. A split shrinks the list: run --ratchet to record it.

    python tools/sizes.py              the files of this DP checkout over the cap, and any ratchet breach
    python tools/sizes.py --ratchet    rewrite the list after files shrank or left it (refused on a breach)
decomp.py sizes runs this without --project; with --project it lists that project's own files with over_cap.

Nearest existing: decomp.py sizes - it listed a project's files over the cap but needed --project, read DP's files only
through paths.TOOL, and was in no test, so the cap drifted; it now calls over_cap here.
"""
import argparse
import pathlib
import subprocess
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
CAP = 400
EXTS = {".py", ".java", ".c", ".h", ".html", ".md"}
BASELINE = TOOL / "tests" / "sizes_over_400.txt"


def lines(p):
    return len(pathlib.Path(p).read_text(encoding="utf-8", errors="replace").splitlines())


def over_cap(files, cap=CAP):
    """[(line count, path)] of the files over the cap, largest first."""
    return sorted(((n, p) for p in files if pathlib.Path(p).suffix in EXTS and (n := lines(p)) > cap), reverse=True)


def tracked(root=TOOL):
    """The files git tracks in this checkout (scratch and build output are never tracked), or None without git."""
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return [root / f for f in r.stdout.decode("utf-8", "replace").split("\0") if f and (root / f).is_file()]


def current(root=TOOL):
    """{posix path relative to root: line count} for the tracked files over the cap, or None without git."""
    files = tracked(root)
    return None if files is None else {p.relative_to(root).as_posix(): n for n, p in over_cap(files)}


def read_baseline(path=BASELINE):
    base = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.startswith("#"):
            n, f = ln.split(None, 1)
            base[f.strip()] = int(n)
    return base


def write_baseline(over, path=BASELINE):
    head = ("# tools/sizes.py: the files over the 400-line cap and their line counts. A file may only leave this list or\n"
            "# shrink (tests/test_sizes.py); after a split run  python tools/sizes.py --ratchet\n")
    body = "".join(f"{n:5d}  {f}\n" for f, n in sorted(over.items(), key=lambda kv: (-kv[1], kv[0])))
    path.write_text(head + body, encoding="utf-8", newline="\n")


def breaches(over, base):
    """What breaks the ratchet: a file over the cap that is not listed, or a listed file that grew."""
    out = []
    for f, n in sorted(over.items()):
        if f not in base:
            out.append(f"{f}: {n} lines, over the {CAP}-line cap (split it, or keep it at {CAP})")
        elif n > base[f]:
            out.append(f"{f}: grew from {base[f]} to {n} lines (it is over the cap: it may only shrink)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ratchet", action="store_true", help="rewrite tests/sizes_over_400.txt (shrinks only)")
    a = ap.parse_args(argv)
    over = current()
    if over is None:
        print("git not available: the cap is checked over the files git tracks")
        return 2
    for f, n in sorted(over.items(), key=lambda kv: -kv[1]):
        print(f"{n:5d}  {f}")
    base = read_baseline() if BASELINE.exists() else {}
    bad = breaches(over, base)
    for b in bad:
        print("RATCHET:", b)
    print(f"{len(over)} file(s) over {CAP} lines, {sum(n - CAP for n in over.values())} lines beyond the cap")
    if a.ratchet and not bad:
        write_baseline(over)
        print(f"wrote {BASELINE.relative_to(TOOL).as_posix()} ({len(base) - len(over)} file(s) fewer)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
