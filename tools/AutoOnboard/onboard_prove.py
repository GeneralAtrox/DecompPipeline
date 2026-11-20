"""`onboard.py prove`: the three proofs an onboarded game's build gets before it goes live, in one command.

    py -3.12 tools/onboard.py prove --project GAME_DIR [--title VB:SHA[:INPUT]] [--replay REC] [--live DIR] [--stop-at N]
                                    [--here | --host build box B|Build box A --remote-project DIR --remote-dp DIR]
                                    [--skip replay,title,launcher] [--seconds N]

  replay    a recording played on the live build and on this one (port/tools/replay_compare.py, every frame and the
            sound): 0 differing frames. The live build is the project the game folder's root Start Game.bat calls; the
            recording is --replay, else the newest .ptrec in the live project's data/ (crash- and killed- ones last).
  title     one headless fixed-clock run of this build to vertical blank VB (pressing INPUT, the kit's --input, when
            given): the frame's sha256 starts with SHA. --title is kept in .work/onboard/reference.json for later runs.
  launcher  the launcher's path: the root Start Game.bat (this project's own when the root one calls another project),
            hidden and headless, its game.exe found by parent PID, bounded, killed by PID tree on timeout; the title
            frame again. Main laptop only, and only with MAIN_LAPTOP_OK=launcher-check set (the global rule's mark).

--host runs replay and title on that machine, in one ssh call after one scp of the live build and the recording: the
remote project is where the onboarding's --host run built (its DecompPipeline at --remote-dp, synced by that run). The
launcher check always runs here. Writes .work/onboard/prove.json; exit 0 when every check run passed, 2 otherwise.

Nearest existing: port/tools/replay_compare.py - reused here for the replay check, but it knows nothing of a project's
live build, its title reference or the launcher; the per-card scripts this replaces (Saturn Bomberman's j1.py/b3.py,
Dragon Force's p3x.py/send3.sh and lcheck.ps1) were each one game's paths.
"""
import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import posixpath
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import onboard_remote as R  # noqa: E402
import onboard_sat as SAT  # noqa: E402
import onboard_stages as S  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[2]
MARK = "PROVE-RESULT "
REF = "reference.json"
OUT = "prove.json"
CHECKS = ("replay", "title", "launcher")
TITLE_TAIL = 100  # vertical blanks run past the title frame
LAUNCH_WAIT_S = 150
CHILD_WAIT_S = 20
REMOTE_DIR = ".work/onboard/prove"


def state(project):
    return pathlib.Path(project) / S.STATE_DIR


def parse_title(text):
    """'VB:SHA[:INPUT]' -> {"vb", "sha", "input"}."""
    vb, sha, *rest = text.split(":", 2)
    return {"vb": int(vb), "sha": sha.lower(), "input": rest[0] if rest else None}


def reference(project, title=None):
    """The title reference: --title (then kept in reference.json), else the kept one, else None."""
    f = state(project) / REF
    if title:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"title": title}, indent=1) + "\n", encoding="utf-8", newline="\n")
        return title
    try:
        return json.loads(f.read_text(encoding="utf-8"))["title"]
    except (OSError, ValueError, KeyError):
        return None


def build_of(project):
    """(game.exe, its launch arguments, image.bin) of the build the project's boot proved."""
    launch = SAT.launch_of(project)
    exe = pathlib.Path(project) / launch["dir"] / "game.exe"
    return exe, [str(x) for x in launch.get("args", ())], exe.parent / "image.bin", launch


def live_project(project):
    """The project the game folder's root Start Game.bat calls when it is not this one (the live build), else None."""
    root = SAT.game_root(project)
    bat = root / "Start Game.bat" if root else None
    rel = SAT.root_target(bat.read_bytes()) if bat and bat.is_file() else None
    if not rel:
        return None
    live = (root / rel).resolve()
    return None if live == pathlib.Path(project).resolve() else live


def disc_of(project):
    """The .cue the builds run: the one onboarding kept (disc.txt), one at the project root (a remote copy), else the
    disc stage's."""
    got = SAT.local_disc(project)
    if got:
        return got
    cues = sorted(pathlib.Path(project).glob("*.cue"))
    if cues:
        return cues[0]
    try:
        img = json.loads((state(project) / "disc.json").read_text(encoding="utf-8"))["paths"]["image"]
        return pathlib.Path(img) if pathlib.Path(img).is_file() else None
    except (OSError, ValueError, KeyError):
        return None


def recording(live, given=None):
    """--replay, else the newest .ptrec in the live project's data/, those a crash or a kill cut short last."""
    if given:
        return pathlib.Path(given)
    recs = list((pathlib.Path(live) / "data").rglob("*.ptrec")) if live else []
    recs.sort(key=lambda f: (not f.name.startswith(("crash-", "killed-")), f.stat().st_mtime), reverse=True)
    return recs[0] if recs else None


def sha_of(f):
    return hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest()


def bounded(argv, seconds, why, changed, log):
    """argv under tools/bounded.py inside tools/jobreg.py; output appended to log. The exit code."""
    argv = ([sys.executable] if str(argv[0]).endswith(".py") else []) + list(argv)  # a script runs under Python
    cmd = [sys.executable, str(TOOL / "tools" / "jobreg.py"), "run", "--why", why, "--changed", changed, "--",
           sys.executable, str(TOOL / "tools" / "bounded.py"), str(seconds), "--", *map(str, argv)]
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n$ {subprocess.list2cmdline(cmd)}\n")
        f.flush()
        return subprocess.run(cmd, cwd=TOOL, stdout=f, stderr=subprocess.STDOUT, timeout=seconds + 300).returncode


# The Saturn kit dumps chosen frames (--dump-frames N,N,..), not every one (replay_compare's --dump-every): its replay check
# compares a frame every SAMPLE_EVERY vertical blanks over the whole recording (Dragon Force, 2026-11-19: replay_compare
# saw 0 frames on both sides and called the sound alone a difference).
SAMPLE_EVERY = 30
KEEP_DIFFERING = 5


def sampled_replay(project, live_exe, live_args, live_img, rec, stop_at, seconds, out, disc):
    sys.path.insert(0, str(TOOL / "port" / "tools"))
    import replay_compare as RC
    exe, args, img, _ = build_of(project)
    stop = stop_at or RC.last_vblank(pathlib.Path(rec).read_bytes())
    frames = list(range(SAMPLE_EVERY, stop, SAMPLE_EVERY))
    top = out / f"replay_{time.strftime('%Y%m%d-%H%M%S')}"
    got, rcs = {}, {}
    for side, x, xa, xi in (("old", live_exe, live_args, live_img), ("new", exe, args, img)):
        d = top / side
        d.mkdir(parents=True)
        rcs[side] = bounded([x, "--image", xi, "--disc", disc, *xa, "--headless", "--fixed-clock", "1", "--present", "tick",
                             "--record", "off", "--replay", rec, "--stop-at", stop, "--seconds", max(60, seconds - 120),
                             "--log", d / "port.log", "--data-directory", d / "data", "--dump-dir", d,
                             "--dump-frames", ",".join(map(str, frames)), "--audio-dump", d / "a.wav"], seconds,
                            f"onboard prove: replay {pathlib.Path(rec).name} on the {side} build, a frame every "
                            f"{SAMPLE_EVERY} vertical blanks", f"exe {sha_of(x)[:12]}", out / "replay.log")
        got[side] = {f.name: sha_of(f) for f in d.glob("frame_*.png")}
    names = sorted(set(got["old"]) | set(got["new"]))
    differ = [n for n in names if got["old"].get(n) != got["new"].get(n)]
    for side in ("old", "new"):  # keep only the first differing frames to look at
        for f in (top / side).glob("frame_*.png"):
            if f.name not in differ[:KEEP_DIFFERING]:
                f.unlink()
    wav = [top / s / "a.wav" for s in ("old", "new")]
    wav_same = all(w.is_file() for w in wav) and sha_of(wav[0]) == sha_of(wav[1])
    for w in wav:
        w.unlink(missing_ok=True)
    ok = bool(names) and not differ and len(got["old"]) == len(got["new"])
    return {"ok": ok, "recording": str(rec), "folder": str(top), "stop": stop, "frames compared": len(names),
            "frames differing": len(differ), "first differing": differ[:KEEP_DIFFERING], "sound same": wav_same,
            "exit": rcs, "why": (f"{len(names)} frames (every {SAMPLE_EVERY} vertical blanks to {stop}), 0 differ"
                                 + ("" if wav_same else "; the sound differs") if ok else
                                 f"{len(differ)} of {len(names)} sampled frames differ, first {differ[:3]}" if names
                                 else "no frames written on either side")}


def check_replay(project, live_exe, live_args, live_img, rec, stop_at, seconds, out):
    exe, args, img, _ = build_of(project)
    disc = disc_of(project)
    missing = [n for n, p in (("this build", exe), ("the live build", live_exe), ("a recording", rec), ("a disc", disc))
               if not p or not pathlib.Path(p).is_file()]
    if missing:
        return {"ok": False, "why": "no " + ", ".join(missing)}
    if img.is_file():  # a Saturn build (image.bin beside its exe): sampled frames, its kit has no --dump-every
        return sampled_replay(project, live_exe, live_args, live_img, rec, stop_at, seconds, out, disc)
    folder = out / f"replay_{time.strftime('%Y%m%d-%H%M%S')}"
    argv = [TOOL / "port" / "tools" / "replay_compare.py", "--disc", disc, "--out", folder, "--old", live_exe,
            "--new", exe, "--replay", rec, "--run-seconds", max(60, seconds - 300), "--budget", max(60, seconds - 200)]
    argv += [f"--old-arg={x}" for x in ["--image", live_img, *live_args]]
    argv += [f"--new-arg={x}" for x in ["--image", img, *args]]
    argv += ["--stop-at", stop_at] if stop_at else []
    rc = bounded(argv, seconds, f"onboard prove: replay {rec.name} on the live build and this one",
                 f"old {sha_of(live_exe)[:12]} new {sha_of(exe)[:12]}", out / "replay.log")
    tail = (out / "replay.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
    return {"ok": rc == 0, "exit": rc, "recording": str(rec), "folder": str(folder),
            "why": {0: "ALL SAME: 0 differing frames", 4: "frames differ", 5: "budget reached: run again to go on",
                    6: "a run hit its bound before the recording's end", 7: "a dump was not written: run again"}.get(
                        rc, f"replay_compare exit {rc}: {' '.join(tail)}")}


def title_args(ref, folder):
    vb = ref["vb"]
    return (["--headless", "--fixed-clock", "1", "--present", "tick", "--stop-at", str(vb + TITLE_TAIL),
             "--dump-frames", str(vb), "--dump-dir", str(folder), "--log", str(folder / "port.log")]
            + (["--input", ref["input"]] if ref.get("input") else []))


def frame_verdict(ref, folder):
    pngs = sorted(pathlib.Path(folder).glob("*.png"))
    if not pngs:
        return {"ok": False, "why": f"no frame at vertical blank {ref['vb']} in {folder}"}
    got = sha_of(pngs[0])
    ok = got.startswith(ref["sha"])
    return {"ok": ok, "frame": str(pngs[0]), "sha256": got[:12], "want": ref["sha"],
            "why": f"vb {ref['vb']} = {got[:10]}" + (" (the reference)" if ok else f", not {ref['sha']}")}


def check_title(project, ref, seconds, out):
    if not ref:
        return {"ok": False, "why": "no title reference: pass --title VB:SHA[:INPUT] once"}
    exe, args, img, _ = build_of(project)
    disc = disc_of(project)
    if not exe.is_file():
        return {"ok": False, "why": f"no build at {exe}"}
    folder = out / "title"
    if folder.exists():
        for f in folder.iterdir():
            f.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    rc = bounded([exe, "--image", img, *(["--disc", disc] if disc else []), *args, *title_args(ref, folder)], seconds,
                 f"onboard prove: the title frame at vertical blank {ref['vb']}",
                 f"exe {sha_of(exe)[:12]} at {time.strftime('%Y%m%d-%H%M%S')}", out / "title.log")
    got = frame_verdict(ref, folder)
    got["exit"] = rc
    return got


# ------------------------------------------------------------------------------------------------ the launcher check
def child_pid(parent, image="game.exe"):
    """The PID of `image` started by `parent`, or None (one bounded PowerShell query)."""
    q = (f"(Get-CimInstance Win32_Process -Filter \"ParentProcessId={int(parent)} and Name='{image}'\")"
         ".ProcessId")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", q], capture_output=True,
                           text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    got = [int(x) for x in r.stdout.split() if x.isdigit()]
    return got[0] if got else None


def wait_pid(pid, seconds):
    """True when the process ended within `seconds` (WaitForSingleObject on its handle)."""
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x00100000 | 0x1000, False, int(pid))  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return True  # already gone
    try:
        return k.WaitForSingleObject(h, int(seconds * 1000)) == 0
    finally:
        k.CloseHandle(h)


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, timeout=60)


def launcher_bat(project):
    """The root Start Game.bat when it calls this project, else the project's own (a second onboarding of a game: the
    root one stays on the live build)."""
    root = SAT.game_root(project)
    top = root / "Start Game.bat" if root else None
    if top and top.is_file() and live_project(project) is None and SAT.root_target(top.read_bytes()):
        return top
    return pathlib.Path(project) / "Start Game.bat"


def check_launcher(project, ref, out):
    if os.environ.get("MAIN_LAPTOP_OK") != "launcher-check":
        return {"ok": False, "why": "not run: set MAIN_LAPTOP_OK=launcher-check (main laptop only, the global rule)"}
    if not ref:
        return {"ok": False, "why": "no title reference: pass --title VB:SHA[:INPUT] once"}
    bat = launcher_bat(project)
    if not bat.is_file():
        return {"ok": False, "why": f"no {bat}"}
    folder = out / "launcher"
    if folder.exists():
        for f in folder.iterdir():
            f.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    args = subprocess.list2cmdline(title_args(ref, folder))
    t0 = time.time()
    cmd = subprocess.Popen(f'cmd.exe /c ""{bat}" {args}"', cwd=bat.parent, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    pid = None
    while pid is None and time.time() - t0 < CHILD_WAIT_S and cmd.poll() is None:
        pid = child_pid(cmd.pid)
    if pid is None:
        kill_tree(cmd.pid)
        return {"ok": False, "why": f"{bat} started no game.exe within {CHILD_WAIT_S} s", "bat": str(bat)}
    ended = wait_pid(pid, LAUNCH_WAIT_S)
    if not ended:
        kill_tree(pid)
    try:
        cmd.wait(15)
    except subprocess.TimeoutExpired:
        kill_tree(cmd.pid)
    got = frame_verdict(ref, folder)
    got.update(bat=str(bat), seconds=int(time.time() - t0))
    if not ended:
        got.update(ok=False, why=f"game.exe ran past {LAUNCH_WAIT_S} s (killed by PID); " + got["why"])
    return got


# ------------------------------------------------------------------------------------------------------- remote
REMOTE_BODY = r"""
import json, pathlib, subprocess, sys
P = json.loads(PARAMS)
proj = pathlib.Path(P["project"])
if P.get("mkdir"):
    (proj / P["mkdir"]).mkdir(parents=True, exist_ok=True)
    print("PROVE-RESULT " + json.dumps({"made": True}))
    sys.exit(0)
try:
    r = subprocess.run([sys.executable, str(pathlib.Path(P["dp"]) / "tools" / "onboard.py"), "prove", *P["args"]],
                       capture_output=True, text=True, timeout=P["seconds"] + 120)
    tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
except subprocess.TimeoutExpired:
    tail = ["no end within the bound"]
try:
    got = json.loads((proj / ".work" / "onboard" / "prove.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    got = {"checks": {}}
got["tail"] = tail
print("PROVE-RESULT " + json.dumps(got))
"""


def remote(a, ref, live_exe, live_args, live_img, rec):
    """replay and title on a.host: one ssh call to make the folder, one scp, one ssh call to run. The checks' dict."""
    rp = a.remote_project.replace("\\", "/")
    dest = posixpath.join(rp, REMOTE_DIR, "live")
    fail = {}
    got = R._ssh(a.host, REMOTE_BODY, {"project": rp, "mkdir": f"{REMOTE_DIR}/live"}, MARK, 120)
    if got.get("_fail"):
        return {c: {"ok": False, "why": got["_fail"]} for c in ("replay", "title")}
    files = [p for p in [live_exe, live_img, *sorted(live_exe.parent.glob("*.dll"))] if p and p.is_file()] if live_exe else []
    if files or rec:
        err = R._scp(a.host, files + ([rec] if rec else []), dest, 1800)
        if err:
            fail = {"replay": {"ok": False, "why": err}}
    args = ["--here", "--project", rp, "--seconds", str(a.seconds), "--skip", ",".join(sorted(set(a.skip) | {"launcher"}))]
    if live_exe:
        args += ["--live-exe", posixpath.join(dest, "game.exe"), "--live-args", json.dumps(live_args)]
    if rec:
        args += ["--replay", posixpath.join(dest, rec.name)]
    if ref:
        args += ["--title", f"{ref['vb']}:{ref['sha']}" + (f":{ref['input']}" if ref.get("input") else "")]
    if a.stop_at:
        args += ["--stop-at", str(a.stop_at)]
    got = R._ssh(a.host, REMOTE_BODY, {"project": rp, "dp": a.remote_dp, "args": args, "seconds": a.seconds}, MARK,
                 a.seconds + 300)
    if got.get("_fail"):
        return {c: {"ok": False, "why": got["_fail"]} for c in ("replay", "title")}
    checks = {c: v for c, v in got.get("checks", {}).items() if c in ("replay", "title")}
    for c in ("replay", "title"):
        if c not in checks and c not in a.skip:
            checks[c] = {"ok": False, "why": f"no result from {a.host}: {' | '.join(got.get('tail', []))}"}
    checks.update(fail)
    for v in checks.values():
        v["host"] = a.host
    return checks


def main(argv=None):
    ap = argparse.ArgumentParser(prog="onboard.py prove", description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--title", help="VB:SHA[:INPUT], kept in .work/onboard/reference.json")
    ap.add_argument("--replay", help="a .ptrec (default: the newest in the live project's data/)")
    ap.add_argument("--live", help="the live project (default: the one the root Start Game.bat calls)")
    ap.add_argument("--live-exe", help=argparse.SUPPRESS)  # a remote run: the live build copied there
    ap.add_argument("--live-args", help=argparse.SUPPRESS)
    ap.add_argument("--stop-at", type=int)
    ap.add_argument("--skip", default="", type=lambda s: {x for x in s.split(",") if x})
    ap.add_argument("--seconds", type=int, default=2700, help="each check's bound")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--here", action="store_true")
    g.add_argument("--host", choices=("Build box B", "Build box A"))
    ap.add_argument("--remote-project")
    ap.add_argument("--remote-dp")
    a = ap.parse_args(argv)
    bad = a.skip - set(CHECKS)
    if bad:
        ap.error(f"--skip takes {', '.join(CHECKS)}, not {', '.join(sorted(bad))}")
    if a.host and not (a.remote_project and a.remote_dp):
        ap.error("--host needs --remote-project and --remote-dp")
    project = pathlib.Path(a.project).resolve()
    out = state(project) / "prove"
    out.mkdir(parents=True, exist_ok=True)
    ref = reference(project, parse_title(a.title) if a.title else None)
    if a.live_exe:
        live_exe = pathlib.Path(a.live_exe)
        live_args, live_img = json.loads(a.live_args or "[]"), live_exe.parent / "image.bin"
        live = None
    else:
        live = pathlib.Path(a.live).resolve() if a.live else live_project(project)
        live_exe, live_args, live_img, _ = build_of(live) if live else (None, [], None, None)
    rec = recording(live, a.replay) if "replay" not in a.skip else None
    checks = {}
    if a.host and ({"replay", "title"} - a.skip):
        checks.update(remote(a, ref, live_exe, live_args, live_img, rec))
    else:
        if "replay" not in a.skip:
            checks["replay"] = (check_replay(project, live_exe, live_args, live_img, rec, a.stop_at, a.seconds, out)
                                if live_exe else {"ok": False, "why": "no live build: the root Start Game.bat calls this "
                                                  "project (pass --live DIR) or none"})
        if "title" not in a.skip:
            checks["title"] = check_title(project, ref, a.seconds, out)
    if "launcher" not in a.skip:
        checks["launcher"] = check_launcher(project, ref, out)
    result = {"stamp": S.stamp(), "project": str(project), "live": str(live) if live else None, "reference": ref,
              "checks": checks, "ok": bool(checks) and all(v.get("ok") for v in checks.values())}
    (state(project) / OUT).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    for c, v in checks.items():
        print(f"prove: {c}: {'PASS' if v.get('ok') else 'FAIL'}: {v.get('why', '')}"
              + (f" (on {v['host']})" if v.get("host") else ""), flush=True)
    print(f"prove: {'ALL PASS' if result['ok'] else 'NOT PROVEN'}; {state(project) / OUT}", flush=True)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
