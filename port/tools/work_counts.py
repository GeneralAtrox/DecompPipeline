"""Compares a port's work counters (base/work_count.c, --work-counts) with its tracked baseline, per scene.

    py -3.12 port/tools/work_counts.py check  --spec docs/proofs/work_counts/GAME/work_counts.json --exe PORT.EXE
    py -3.12 port/tools/work_counts.py accept --spec ... --exe PORT.EXE --why "what changed the counts, and why that is right"

The spec names the game's run and its scenes, each a fixed-clock headless replay of a stretch of the game. A scene counts
from its "from" blank to its "to" blank. check runs every scene and fails on any row that differs from the baseline, up or
down, printing the differences; a run that cannot be compared fails too. A difference passes only once accept has written
it in, with its reason, in the spec's ledger: accept runs each scene twice and refuses counts that do not repeat, and check
refuses a baseline the ledger's last digest does not match, so a hand edit is caught. The diff of the spec in the commit is
the explanation of the change. Timing runs stay the judge of the 144 fps target (docs/RUNTIME_TARGET.md "Work counters").

Each run has its own empty folder and save folder, so one run's save can't change the next. In args, {here} is the spec's
folder, {exe_dir} the exe's and {work} the run's own folder.

Nearest existing: tools/scorecard_runtime.py - reads the times a play session logged, which only mean something on a quiet
machine; this runs the game and compares exact counts. base/play_stats.c and runtime/prof.c measure time the same way.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import subprocess
import tempfile

FAIL, CANNOT_RUN = 1, 2
MIN_WHY = 15


def digest(scenes):
    """The baselines' digest: counts only, so a scene's run arguments may be tidied without a ledger entry."""
    kept = {name: {k: s.get(k) for k in ("blanks", "counts", "peak")} for name, s in scenes.items()}
    return hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest()


def rows(result):
    """A result as flat rows: {"blanks": n, "draws": total, "peak draws": highest in one blank, ...}."""
    out = {"blanks": result.get("blanks")}
    out.update(result.get("counts") or {})
    out.update({"peak " + k: v for k, v in (result.get("peak") or {}).items()})
    return out


def differences(base, now):
    """[(row, baseline, now)] for every row that isn't equal, a row only one side has included."""
    a, b = rows(base), rows(now)
    return [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def ledger_ok(spec):
    """None if the baselines are the ones the ledger's last entry wrote, else what is wrong."""
    ledger = spec.get("ledger") or []
    if not ledger:
        return "the ledger is empty: no baseline was accepted"
    if ledger[-1].get("digest") != digest(spec.get("scenes") or {}):
        return "the baselines don't match the ledger's last digest: they were edited without accept"
    return None


def stop_tree(pid):
    """Only the process this tool started, by its PID, and its children."""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60, check=False)


def run_scene(exe, spec_path, spec, scene, seconds):
    """Runs one scene; returns the counters' result, or raises RuntimeError saying why there is none."""
    with tempfile.TemporaryDirectory(prefix="work_counts_") as work:
        out = pathlib.Path(work) / "counts.json"
        fill = {"{here}": str(spec_path.parent), "{exe_dir}": str(exe.parent), "{work}": work}

        def filled(arg):
            for key, value in fill.items():
                arg = arg.replace(key, value)
            return arg

        cmd = [str(exe)] + [filled(a) for a in spec["args"] + scene["args"]]
        cmd += ["--work-counts", str(out), "--work-from", str(scene["from"])]
        with subprocess.Popen(cmd, cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
            try:
                code = proc.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                stop_tree(proc.pid)
                proc.wait(timeout=60)
                raise RuntimeError(f"no result in {seconds} s") from None
        if code:
            raise RuntimeError(f"the run ended with exit code {code:#x}")
        if not out.exists():
            raise RuntimeError("the run wrote no counts (an exe from before --work-counts?)")
        result = json.loads(out.read_text(encoding="utf-8"))
    if not result.get("fixed_clock"):
        raise RuntimeError("not a fixed-clock run: its counts would not repeat")
    if result.get("last") != scene["to"]:
        raise RuntimeError(f"stopped at blank {result.get('last')}, not {scene['to']}")
    return result


def run_all(exe, spec_path, spec, names, jobs, seconds, times=1):
    """{(scene, n): result or RuntimeError}, each scene `times` times."""
    def one(key):
        try:
            return key, run_scene(exe, spec_path, spec, spec["scenes"][key[0]], seconds)
        except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as e:
            return key, RuntimeError(str(e))

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        return dict(pool.map(one, [(name, n) for name in names for n in range(times)]))


def check(exe, spec_path, spec, names, jobs, seconds):
    """Prints each scene's verdict; returns the exit code."""
    code = 0
    wrong = ledger_ok(spec)
    if wrong:
        print(f"FAIL {spec_path}: {wrong}")
        code = FAIL
    results = run_all(exe, spec_path, spec, names, jobs, seconds)
    for name in names:
        result = results[(name, 0)]
        if isinstance(result, RuntimeError):
            print(f"FAIL {name}: could not compare: {result}")
            code = code or CANNOT_RUN
            continue
        found = differences(spec["scenes"][name], result)
        print(f"{'FAIL' if found else 'ok  '} {name}: {result['blanks']} blanks, {len(found)} row(s) differ")
        for row, old, new in found:
            change = f" ({new - old:+d})" if old is not None and new is not None else ""
            print(f"       {row}: {old} -> {new}{change}")
        if found:
            code = FAIL
    if code == FAIL:
        print("A changed count needs a reason: if the change is right, run accept --why and commit the spec.")
    return code


def accept(exe, spec_path, spec, names, jobs, seconds, why):
    """Writes new baselines and their ledger entry, if each scene repeats exactly; returns the exit code."""
    if len(why.strip()) < MIN_WHY:
        print("accept needs --why: what changed these counts, and why that is right")
        return FAIL
    results = run_all(exe, spec_path, spec, names, jobs, seconds, times=2)
    changes = {}
    for name in names:
        first, second = results[(name, 0)], results[(name, 1)]
        for result in (first, second):
            if isinstance(result, RuntimeError):
                print(f"FAIL {name}: {result}")
                return CANNOT_RUN
        found = differences(first, second)
        if found:
            print(f"FAIL {name}: two runs differ, so these rows can't be a baseline: {found}")
            return FAIL
        changed = differences(spec["scenes"][name], first)
        if changed:
            changes[name] = {row: [old, new] for row, old, new in changed}
        spec["scenes"][name].update({k: first[k] for k in ("blanks", "counts", "peak")})
    spec.setdefault("ledger", []).append(
        {"date": datetime.date.today().isoformat(), "why": why.strip(), "changes": changes, "digest": digest(spec["scenes"])})
    spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"accepted {len(names)} scene(s), {len(changes)} changed: {spec_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    ap.add_argument("action", choices=("check", "accept"))
    ap.add_argument("--spec", required=True, type=pathlib.Path)
    ap.add_argument("--exe", required=True, type=pathlib.Path)
    ap.add_argument("--scene", action="append", help="only this scene (again for more)")
    ap.add_argument("--why", default="")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=500, help="the limit for one run")
    args = ap.parse_args()
    spec_path, exe = args.spec.resolve(), args.exe.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    names = args.scene or sorted(spec["scenes"])
    if args.action == "accept":
        return accept(exe, spec_path, spec, names, args.jobs, args.seconds, args.why)
    return check(exe, spec_path, spec, names, args.jobs, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
