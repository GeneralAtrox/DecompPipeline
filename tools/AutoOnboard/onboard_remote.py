"""Runs tools/AutoOnboard/autoonboard.py's build-box stages on build box B or build box A in one ssh call: one script over stdin, a check that
no -benchmark process runs there and 4 GB are free, `autoonboard.py --here` in the remote project (its heavy steps under
the remote tools/jobreg.py and tools/bounded.py), one bounded wait, and one line back: the remote verdict files.
The log stays on the remote machine; only its last lines come back.

The remote DecompPipeline checkout: --remote-dp, else machines.local.json "onboard_remote_dp" {host: path}; neither is
a hard error. ONBOARD_SSH (a JSON list) replaces the whole remote command ["ssh", host, "py -3.12 -I -"] (tests). ONBOARD_SCP (a JSON
list) replaces ["scp", "-q"]; the destination is then a plain path, not host:path (tests). With ONBOARD_SSH set and
ONBOARD_SCP not, sync() does nothing (a test must never reach a real machine).

sync() runs before the run call: the remote DecompPipeline checkout is brought to the local commit (a git bundle over
scp, because the build boxes have no GitHub credentials) and the disc image files (.cue/.gdi and the tracks they name)
are copied to the remote project root when missing or of another size. At most two ssh calls (one check, one git
update) and two scp calls (bundle, discs). Files land only under the remote project and beside the remote checkout.

Nearest existing: tools/jobreg.py - keys and refuses a job on the machine it runs on but never sends one anywhere;
tools/bounded.py bounds a local command. No ssh runner existed in DP (the only one found is build box B's live_push.py, for
live-line pushes); the per-card job scripts per machine are one-off.
"""
import inspect
import json
import os
import pathlib
import posixpath
import re
import subprocess

MARK = "ONBOARD-RESULT "
FREE_GB = 4



def busy():
    """Process ids of any -benchmark process here (not this check's own shell): heavy jobs wait for them."""
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-benchmark*' -and "
              "$_.ProcessId -ne $PID } | ForEach-Object { $_.ProcessId }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
    else:
        r = subprocess.run(["sh", "-c", "ps -eo pid,args | grep -e '-benchmark' | grep -v grep"],
                           capture_output=True, text=True, timeout=60)
    return r.stdout.split()


PREFLIGHT = r'''
import json, os, shutil, subprocess, sys, pathlib
P = json.loads(sys.argv[1] if len(sys.argv) > 1 else PARAMS)
def out(v):
    print("ONBOARD-RESULT " + json.dumps(v))
    sys.exit(0)
proj, dp = pathlib.Path(P["project"]), pathlib.Path(P["dp"])
ENTRY = next((e for e in (dp / "tools" / "AutoOnboard" / "autoonboard.py", dp / "tools" / "autoonboard.py") if e.is_file()), None)
if ENTRY is None:  # an older build box has only tools/AutoOnboard/autoonboard.py (the whole tool then); a newer one both (a forwarder)
    out({"error": f"no tools/AutoOnboard/autoonboard.py in {dp} on {P['host']}", "missing": [f"{dp}/tools/AutoOnboard/autoonboard.py"],
         "supply": "a DecompPipeline checkout there with this branch (git fetch the private remote)"})
pids = busy()
if pids:
    out({"error": f"a -benchmark process runs on {P['host']} (pid {', '.join(pids[:5])}): heavy jobs wait",
         "missing": ["a free build box"], "supply": "wait for it to end, or use the other build box"})
proj.mkdir(parents=True, exist_ok=True)
free = shutil.disk_usage(proj).free / 2**30
if free < P["free_gb"]:
    out({"error": f"{free:.1f} GB free under {proj}, under {P['free_gb']} GB", "missing": [f"{P['free_gb']} GB free"],
         "supply": "free space there (delete spent .work output)"})
state = proj / ".work" / "onboard"
state.mkdir(parents=True, exist_ok=True)
log = state / "remote.log"
cmd = [sys.executable, str(dp / "tools" / "bounded.py"), str(P["seconds"]), "--", sys.executable,
       str(ENTRY), "--project", str(proj), "--here", *P["args"]]
with open(log, "a", encoding="utf-8", errors="replace") as f:
    f.write("\n$ " + subprocess.list2cmdline(cmd) + "\n")
    f.flush()
    rc = subprocess.run(cmd, cwd=dp, stdout=f, stderr=subprocess.STDOUT, timeout=P["seconds"] + 300).returncode
verdicts = {}
for v in sorted(state.glob("*.json")):
    try:
        verdicts[v.stem] = json.loads(v.read_text(encoding="utf-8"))
    except ValueError as e:
        verdicts[v.stem] = {"stage": v.stem, "status": "error", "message": f"unreadable verdict file: {e}"}
out({"rc": rc, "verdicts": verdicts, "log": str(log),
     "tail": log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]})
'''


def remote_dp(host, given, tool):
    if given:
        return given
    try:
        got = json.loads((pathlib.Path(tool) / "machines.local.json").read_text(encoding="utf-8")).get(
            "onboard_remote_dp", {})
    except (OSError, ValueError):
        got = {}
    if not isinstance(got, dict) or not got.get(host):
        raise LookupError(f"where DecompPipeline is on {host}: pass --remote-dp DIR, or set \"onboard_remote_dp\": "
                          f"{{\"{host}\": \"D:/Projects/...\"}} in {pathlib.Path(tool) / 'machines.local.json'}")
    return got[host]


def script(host, dp, project, args, seconds, free_gb=FREE_GB):
    params = {"host": host, "dp": dp, "project": project, "args": list(args), "seconds": seconds, "free_gb": free_gb}
    body = PREFLIGHT.replace("pids = busy()", inspect.getsource(busy) + "pids = busy()")
    return f"PARAMS = {json.dumps(json.dumps(params))}\n" + body


def command(host):
    over = os.environ.get("ONBOARD_SSH")
    return json.loads(over) if over else ["ssh", host, "py -3.12 -I -"]


def run(host, dp, project, args, seconds):
    """The remote result: {"rc", "verdicts", "log", "tail"} or {"error", "missing", "supply"}. One ssh call."""
    try:
        r = subprocess.run(command(host), input=script(host, dp, project, args, seconds), capture_output=True,
                           text=True, timeout=seconds + 600)
    except subprocess.TimeoutExpired:
        return {"error": f"ssh {host}: no answer within {seconds + 600} s", "missing": [f"an answer from {host}"],
                "supply": f"look at {project}/.work/onboard/remote.log on {host}"}
    except OSError as e:
        return {"error": f"cannot start {command(host)[0]}: {e}", "missing": ["ssh"], "supply": "an ssh client"}
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(MARK)]
    if not lines:
        return {"error": f"ssh {host} exit {r.returncode}, no result line: {(r.stderr or r.stdout).strip()[-400:]}",
                "missing": [f"a run on {host}"], "supply": f"ssh {host} working (aliases are lower case)"}
    return json.loads(lines[-1][len(MARK):])


# ------------------------------------------------------------------------------------------------------- sync
CHECK = r"""
import json, os, subprocess, pathlib, shutil
P = json.loads(PARAMS)
# the copy cannot make folders: the project and the checkout's parent exist before anything is copied (2026-11-16,
# Dragon Force: "dest open C:/dpx/DragonForce/: Failure" on a first run)
pathlib.Path(P["project"]).mkdir(parents=True, exist_ok=True)
pathlib.Path(P["dp"]).parent.mkdir(parents=True, exist_ok=True)
if P.get("game_src"):  # the game's own code: a fresh folder, so a file deleted here does not survive there
    shutil.rmtree(pathlib.Path(P["project"]) / "game_src", ignore_errors=True)
    (pathlib.Path(P["project"]) / "game_src").mkdir()
if any(n.startswith("reference/") for n in P["files"]):
    (pathlib.Path(P["project"]) / "reference").mkdir(exist_ok=True)
def git(*a):
    try:
        return subprocess.run(["git", "-C", P["dp"], *a], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
r = git("rev-parse", "HEAD") if os.path.isdir(P["dp"]) else None
head = r.stdout.strip() if r is not None and r.returncode == 0 else None
dirty = False
if head:
    d = git("status", "--porcelain", "--untracked-files=no")
    dirty = d is None or d.returncode != 0 or bool(d.stdout.strip())
sizes = {}
for n in P["files"]:
    f = pathlib.Path(P["project"]) / n
    sizes[n] = f.stat().st_size if f.is_file() else None
print("ONBOARD-CHECK " + json.dumps({"head": head, "dirty": dirty, "sizes": sizes}))
"""

UPDATE = r"""
import json, os, subprocess
P = json.loads(PARAMS)
def git(*a):
    r = subprocess.run(["git", *a], capture_output=True, text=True, timeout=900)
    if r.returncode:
        raise RuntimeError("git " + " ".join(a[:2]) + ": " + (r.stderr or r.stdout).strip()[-300:])
    return r.stdout.strip()
res = {}
try:
    if P["clone"]:
        git("clone", "--no-checkout", P["bundle"], P["dp"])
    else:
        git("-C", P["dp"], "fetch", P["bundle"], P["ref"])
    git("-C", P["dp"], "checkout", "--detach", P["sha"])
    res["head"] = git("-C", P["dp"], "rev-parse", "HEAD")
    # a new checkout lacks this machine's paths (ghidra_home, vcvars, vcpkg: git-ignored config.local.json). Take the
    # newest one with ghidra_home from a sibling checkout on the same build box (2026-11-16, Dragon Force: census
    # stopped on "config key 'ghidra_home' is not set" in C:/dpx/DragonForce/dp; C:/dpx/SaturnBomberman/dp had one)
    import glob, shutil
    own = os.path.join(P["dp"], "config.local.json")
    if not os.path.isfile(own):
        root = os.path.dirname(os.path.dirname(os.path.abspath(P["dp"])))
        seeds = []
        for pat in ("*/config.local.json", "*/dp/config.local.json", "*/*/dp/config.local.json"):
            for f in glob.glob(os.path.join(root, pat)):
                if os.path.normcase(os.path.abspath(f)) == os.path.normcase(os.path.abspath(own)):
                    continue
                try:
                    if json.load(open(f, encoding="utf-8")).get("ghidra_home"):
                        seeds.append(f)
                except (OSError, ValueError):
                    pass
        if seeds:
            src = max(seeds, key=os.path.getmtime)
            shutil.copyfile(src, own)
            res["config_from"] = src
except Exception as e:
    res["error"] = str(e)
finally:
    try:
        os.remove(P["bundle"])
    except OSError:
        pass
print("ONBOARD-SYNC " + json.dumps(res))
"""


def scp_command():
    over = os.environ.get("ONBOARD_SCP")
    return json.loads(over) if over else ["scp", "-q"]


def _ssh(host, body, params, mark, seconds=300):
    """One ssh call: body over stdin with PARAMS; the JSON after `mark` comes back, or {"_fail": text}."""
    src = f"PARAMS = {json.dumps(json.dumps(params))}\n" + body
    try:
        r = subprocess.run(command(host), input=src, capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return {"_fail": f"ssh {host}: no answer within {seconds} s"}
    except OSError as e:
        return {"_fail": f"cannot start {command(host)[0]}: {e}"}
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(mark)]
    if not lines:
        return {"_fail": f"ssh {host} exit {r.returncode}, no {mark.strip()} line: "
                         f"{(r.stderr or r.stdout).strip()[-300:]}"}
    return json.loads(lines[-1][len(mark):])


def _scp(host, sources, dest, seconds):
    """One scp call of every source into the remote folder `dest` (a plain path under ONBOARD_SCP)."""
    target = dest if os.environ.get("ONBOARD_SCP") else f"{host}:{dest}"
    try:
        r = subprocess.run([*scp_command(), *map(str, sources), target], capture_output=True, text=True,
                           timeout=seconds)
    except subprocess.TimeoutExpired:
        return f"scp to {host}: no end within {seconds} s"
    except OSError as e:
        return f"cannot start scp: {e}"
    return None if r.returncode == 0 else f"scp to {host} exit {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}"


def fetch(host, remote_dir, names, local_dir, seconds=900):
    """One scp call of the files `names` in the remote folder `remote_dir` into `local_dir` (made). None, or what went
    wrong. With ONBOARD_SSH set and ONBOARD_SCP not, nothing is copied (a test never reaches a real machine)."""
    if os.environ.get("ONBOARD_SSH") and not os.environ.get("ONBOARD_SCP"):
        return "no copy: ONBOARD_SSH is set without ONBOARD_SCP"
    pathlib.Path(local_dir).mkdir(parents=True, exist_ok=True)
    pre = "" if os.environ.get("ONBOARD_SCP") else f"{host}:"
    src = [pre + posixpath.join(str(remote_dir).replace("\\", "/"), n) for n in names]
    try:
        r = subprocess.run([*scp_command(), *src, str(local_dir)], capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return f"scp from {host}: no end within {seconds} s"
    except OSError as e:
        return f"cannot start scp: {e}"
    if r.returncode:
        return f"scp from {host} exit {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}"
    gone = [n for n in names if not (pathlib.Path(local_dir) / n).is_file()]
    return f"scp from {host} left out {', '.join(gone)}" if gone else None


def disc_files(project, disc, suffixes):
    """The local disc image's files: the image (--disc, else the one .cue in the project root, else the one file with a
    suffix of the console) plus every file a .cue (FILE) or .gdi (quoted name) lists beside it. [] = none found."""
    project = pathlib.Path(project)
    if disc:
        image = pathlib.Path(disc).resolve()
        if not image.is_file():
            raise LookupError(f"--disc {image}: no such file")
    else:
        try:
            cues = sorted(p for p in project.iterdir() if p.is_file() and p.suffix.lower() == ".cue")
            found = cues or sorted(p for p in project.iterdir() if p.is_file() and p.suffix.lower() in suffixes)
        except OSError:
            return []
        if len(found) != 1:
            return []  # none or several: the remote stage names the problem itself
        image = found[0]
    files = [image]
    if image.suffix.lower() in (".cue", ".gdi"):
        text = image.read_text(encoding="utf-8", errors="replace")
        if image.suffix.lower() == ".cue":
            names = [q or bare for q, bare in re.findall(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', text, re.I | re.M)]
        else:
            names = re.findall(r'"([^"]+)"', text)
        for n in names:
            f = image.parent / n
            if not f.is_file():
                raise LookupError(f"{image.name} lists {n}, which is not beside it ({image.parent})")
            if f not in files:
                files.append(f)
    return files


def _local_git(dp, *a):
    r = subprocess.run(["git", "-C", str(dp), *a], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"git {a[0]}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip()


def sync(host, local_dp, remote_dp_dir, local_project, remote_project, disc, suffixes, seconds=1800, refs=(), game_src=None):
    """Bring the remote DecompPipeline to the local commit and the disc files to the remote project root.
    refs: local symbol-set files (the symbols stage's folders here) that go to the remote project's reference/, which
    the symbols stage reads first: the build box has no Integrations console folder (Saturn Bomberman's were
    copied there by hand).
    game_src: the local project's game_src/*.c (the game's own native code), or None to leave the remote folder alone; a
    list empties the remote project's game_src/ and sends these.
    {"disc": remote path of the image or None, "notes": [...]} or {"error", "missing", "supply"}."""
    def fail(msg, missing, supply):
        return {"error": msg, "missing": missing, "supply": supply}
    try:
        files = disc_files(local_project, disc, suffixes)
        sha = _local_git(local_dp, "rev-parse", "HEAD")
        branch = _local_git(local_dp, "rev-parse", "--abbrev-ref", "HEAD")
    except (LookupError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return fail(f"sync to {host}: {e}", ["the local disc files or git state"], "fix that, or pass --no-sync")
    if os.environ.get("ONBOARD_SSH") and not os.environ.get("ONBOARD_SCP"):
        # a test with the ssh stand-in but no scp stand-in: never reach a real machine
        return {"disc": None, "notes": ["ONBOARD_SSH without ONBOARD_SCP: nothing synced"]}
    rproj = remote_project.replace("\\", "/").rstrip("/")
    dp = remote_dp_dir.replace("\\", "/").rstrip("/")
    refs = [pathlib.Path(r) for r in refs]
    chk = _ssh(host, CHECK, {"dp": dp, "project": rproj,
                             "files": [f.name for f in files] + ["reference/" + r.name for r in refs],
                             "game_src": game_src is not None}, "ONBOARD-CHECK ")
    if "_fail" in chk:
        return fail(chk["_fail"], [f"an answer from {host}"], f"ssh {host} working (aliases are lower case)")
    notes = []
    if chk["head"] != sha:
        if chk["dirty"]:
            return fail(f"{dp} on {host} has uncommitted tracked changes; not overwritten",
                        [f"a clean checkout at {dp}"],
                        f"commit or discard them there (git -C {dp} status), then re-run")
        bundle_dir = pathlib.Path(local_project) / ".work" / "onboard"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        local_bundle = bundle_dir / f"onboard-sync-{sha[:12]}.bundle"
        rbundle = f"{posixpath.dirname(dp)}/onboard-sync-{sha[:12]}.bundle"
        ref = branch if branch != "HEAD" else "HEAD"
        try:
            _local_git(local_dp, "bundle", "create", str(local_bundle), ref)
            err = _scp(host, [local_bundle], posixpath.dirname(dp) + "/", seconds)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            err = str(e)
        finally:
            local_bundle.unlink(missing_ok=True)
        if err:
            return fail(f"DecompPipeline bundle to {host}: {err}", ["the bundle on the build box"], "scp working")
        up = _ssh(host, UPDATE, {"dp": dp, "bundle": rbundle, "ref": ref, "sha": sha, "clone": chk["head"] is None},
                  "ONBOARD-SYNC ", 1000)
        if "_fail" in up or "error" in up:
            return fail(f"updating {dp} on {host}: {up.get('_fail') or up['error']}", [f"{dp} at {sha[:12]}"],
                        "fix that checkout by hand once, or pass --no-sync")
        notes.append(f"DecompPipeline on {host}: {str(chk['head'] or 'none')[:12]} -> {sha[:12]}")
    need = [f for f in files if chk["sizes"].get(f.name) != f.stat().st_size]
    if need:
        err = _scp(host, need, rproj + "/", seconds)
        if err:
            return fail(f"disc files to {host}: {err}", [f"{n.name} in {rproj}" for n in need], "scp working")
        notes.append("disc files copied: " + ", ".join(f.name for f in need))
    need = [r for r in refs if chk["sizes"].get("reference/" + r.name) != r.stat().st_size]
    if need:
        err = _scp(host, need, rproj + "/reference/", seconds)
        if err:
            return fail(f"symbol sets to {host}: {err}", [f"reference/{n.name} in {rproj}" for n in need], "scp working")
        notes.append("symbol sets copied: " + ", ".join(f.name for f in need))
    if game_src:
        err = _scp(host, game_src, rproj + "/game_src/", seconds)
        if err:
            return fail(f"game source to {host}: {err}", [f"game_src/ in {rproj}"], "scp working")
        notes.append("game source copied: " + ", ".join(pathlib.Path(f).name for f in game_src))
    return {"disc": f"{rproj}/{files[0].name}" if files else None, "notes": notes}
