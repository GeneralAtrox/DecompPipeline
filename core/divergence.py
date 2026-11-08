"""decomp.py divergence: the port in check mode against Flycast, scenario by scenario, down to the function that wrote
the first word that differs (port/tools/ref_fast.py records the reference, port/tools/divergence.py finds the word and
its writer).

The game's scenarios: GAME/divergence_scenarios.json (GAME: the port folder with game.json, --game, default the
workspace's sibling port/):
    {"frame_pc": "<the game's once-per-frame function>", "every_vb": 600,
     "scenarios": {"boot": {"input": "", "vblanks": 3000}, "level1": {"input": "START@900,A@1500,..", "vblanks": 9000,
                   "card": "tests/cards/unlocked.bin"}, ..}}
Input times are the emulator's vertical blanks (lockstep: the port takes its pads and timer from the paired emulator
frame). A reference is recorded once per scenario (GAME/build/reference/div_NAME) and recorded again only when the
scenario, the frame PC or the Flycast build (path, size, time) changes. Optional per scenario: "from" (the first port
frame compared, past a load) and "ignore" (the paired frames after it that set the always-different words), passed to
port/tools/divergence.py as --from and --ignore.

Each scenario's result goes to its reference folder (divergence_BUILD/divergence.json) and, summed up, to
state/divergence.json: status clean | diverged | error, the first differing frame and words, and the port's writer
(function, guest address, readable file, callers). --requeue: each diverged scenario whose writer is a readable
function gets the evidence appended to agent_notes/ADDR.md (a reviewer's note every agent of that function reads) and
the functions are redone by the readable route (refine.py run --redo); no file is edited by hand.
Coverage (core/div_coverage.py): from the references' coverage.txt, state/coverage.json lists the functions and branch
sides no scenario reaches, with the figure that says how much of the game a zero-divergence result proves.
In game (core/in_game.py): state/in_game.json records each readable function a clean scenario entered in a build that
compiled its current file (proofs accumulate across runs); a diverged scenario's writer loses its entry.
Runs are bounded (--timeout per step, the process tree killed) and wait while RAM is at 85% or more, until five
readings in a row, a minute apart, are under 85%. A recording is bounded by port/tools/ref_bound.py (disk cap, time
limit from the scenario's vertical blanks, free disk checked first) and replaces the old reference only once complete:
a failed one leaves the old reference (and its key) as they were, and the error names the reason.

Nearest existing: port/tools/lockstep.py - it compares one port run with one emulator trace and stops at the first
different sample; this drives every scenario, keeps the references, and goes on to the word and the function that wrote it.
"""
import concurrent.futures
import ctypes
import json
import pathlib
import shutil
import subprocess
import sys
import time

from . import div_coverage, in_game, paths

KIT = paths.TOOL / "port" / "tools"
RAM_HIGH, RAM_OK, RAM_READINGS = 85, 85, 5


def ram_percent():
    class Mem(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                                                                          ("tp", "ap", "tf", "af", "tv", "av", "ae")]
    m = Mem()
    m.len = ctypes.sizeof(Mem)
    return m.load if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else 0


def wait_ram(log=print, poll=60.0):
    """Returns at once below RAM_HIGH; else after RAM_READINGS readings in a row under RAM_OK."""
    if ram_percent() < RAM_HIGH:
        return
    log(f"RAM at {ram_percent()}%: waiting for {RAM_READINGS} readings under {RAM_OK}%")
    good = 0
    while good < RAM_READINGS:
        time.sleep(poll)
        good = good + 1 if ram_percent() < RAM_OK else 0


def game_dir(given=None):
    game = pathlib.Path(given) if given else paths.ROOT.parent / "port"
    if not (game / "game.json").is_file():
        raise SystemExit(f"{game}: no game.json (pass --game)")
    return game.resolve()


def scenarios(game):
    path = game / "divergence_scenarios.json"
    if not path.is_file():
        raise SystemExit(f"{path}: no scenarios")
    return json.loads(path.read_text(encoding="utf-8"))


def flycast_exe():
    sys.path.insert(0, str(paths.TOOL))
    from core import toolcfg
    return pathlib.Path(toolcfg.need(toolcfg.load(paths.TOOL), "flycast_divergence"))


def build_key(exe):
    st = exe.stat()
    return f"{exe.resolve()}|{st.st_size}|{int(st.st_mtime)}"


def bounded(cmd, timeout, log_path):
    """Runs cmd with output to log_path; the whole process tree is killed (by the PID started here) on timeout."""
    with open(log_path, "w", encoding="utf-8", errors="replace") as out:
        p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(KIT))
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)
            return -1


def reference(game, name, sc, top, exe, timeout, log):
    """The scenario's reference folder, recorded if missing or stale; (folder, seconds spent recording). Only a
    complete reference is used; the recorder replaces the old one only with a complete one (ref_fast.py)."""
    ref = game / "build" / "reference" / f"div_{name}"
    card = str((game / sc["card"]).resolve()) if sc.get("card") else None
    want = {"input": sc.get("input", ""), "vblanks": sc["vblanks"], "frame_pc": top["frame_pc"], "card": card,
            "build": build_key(exe)}
    m = ref / "manifest.json"
    if m.is_file():
        have = json.loads(m.read_text(encoding="utf-8"))
        if have.get("divergence_key") == want and have.get("reached_vb", 0) >= sc["vblanks"]:
            return ref, 0.0
    wait_ram(log)
    t0 = time.monotonic()
    cmd = [sys.executable, str(KIT / "ref_fast.py"), "record", "--game", str(game), "--name", f"div_{name}",
           "--input", want["input"], "--frame-pc", top["frame_pc"], "--vblanks", str(sc["vblanks"]),
           "--every-vb", str(top.get("every_vb", 600)), "--flycast", str(exe),
           "--seconds", str(timeout), "--key", json.dumps(want)] + (["--card", card] if card else [])
    ref.parent.mkdir(parents=True, exist_ok=True)
    log_path = ref.parent / f"div_{name}.record.log"
    rc = bounded(cmd, timeout + REC_GRACE_S, log_path)
    have = json.loads(m.read_text(encoding="utf-8")) if m.is_file() else {}
    if rc != 0 or have.get("divergence_key") != want:
        shutil.rmtree(ref.with_name(ref.name + ".new"), ignore_errors=True)  # a recorder killed on the outer bound
        raise RuntimeError(f"record {name}: {record_reason(ref, rc, timeout)} (log {log_path})")
    return ref, time.monotonic() - t0


REC_GRACE_S = 300  # past ref_fast's own limit: Flycast's exit, the packer's end (ref_bound.PACK_S), the texts


def record_reason(ref, rc, timeout):
    """Why a recording failed, from its record.json when this run wrote one."""
    run = ref.with_name(ref.name + ".record.json")
    why = json.loads(run.read_text(encoding="utf-8")).get("reason") if run.is_file() else None
    if rc == -1:
        return f"killed past {timeout + REC_GRACE_S} s; the old reference kept"
    return f"exit {rc}: {why or 'see the log'}; the old reference kept"


def run_one(game, name, sc, top, exe, build_dir, timeout, log, float_ulps=0):
    t0 = time.monotonic()
    try:
        ref, rec_s = reference(game, name, sc, top, exe, timeout, log)
        wait_ram(log)
        out = ref / f"divergence_{build_dir}"
        out.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(KIT / "divergence.py"), "--ref", str(ref), "--build-dir", build_dir,
               "--out", str(out), "--json", str(out / "divergence.json")]
        cmd += [x for k in ("from", "ignore") if k in sc for x in (f"--{k}", str(sc[k]))]  # past a load, say
        cmd += ["--float-ulps", str(float_ulps)] if float_ulps else []
        rc = bounded(cmd, timeout * 2 + 300, out / "divergence.log")
        res = json.loads((out / "divergence.json").read_text(encoding="utf-8")) if rc in (0, 1) else {}
        near = (res.get("float_close") or {}).get("words")
        status = ("error" if rc not in (0, 1) or res.get("error") else "diverged" if res.get("first") else
                  "float-close" if near else "noise" if res.get("noise_globals") else "clean")
        res.update(status=status, exit=rc, record_s=round(rec_s, 1), wall_s=round(time.monotonic() - t0, 1))
        if status == "error" and not res.get("error"):
            res["error"] = f"divergence.py exit {rc} (see {out / 'divergence.log'})"
    except (RuntimeError, OSError, ValueError) as e:
        res = {"status": "error", "error": str(e), "wall_s": round(time.monotonic() - t0, 1)}
    log(f"{name}: {summary_line(res)}")
    return name, res


def summary_line(res):
    if res["status"] == "float-close":
        c = res["float_close"]
        return (f"float-close only (TEMPORARY {c['ulps']}-ulp tolerance, not zero divergence): {c['words']} words in "
                f"{c['frames']} frames, up to {c['max_ulps']} ulps")
    if res["status"] == "noise":
        n = res["noise_globals"]
        return (f"noise only: {n['count']} game globals differ from the first frames on (--ignore), so not zero "
                f"divergence: " + ", ".join(f"{w['addr']} {w['name']}" for w in n["words"][:4]))
    if res["status"] != "diverged":
        return res["status"] + (f": {res.get('error')}" if res["status"] == "error" else "")
    f, w = res["first"], res.get("writer") or {}
    word = written_word(f, w)
    return (f"diverged at port frame {f['port_frame']} (vblank {f['vblank']}): {word['addr']} {word['name']} expected "
            f"{word['expected']} got {word['actual']}; writer {w.get('function', 'none')} {w.get('addr') or ''}")


def written_word(first, writer):
    """The differing word the writer wrote (tools/divergence.py watches several and names the earliest writer),
    else the first."""
    return next((x for x in first["words"] if x["addr"] == (writer or {}).get("word")), first["words"][0])


NOTE_HEAD = "Divergence against the original (decomp.py divergence, the port in check mode against Flycast):"


def note_text(name, res):
    f, w = res["first"], res["writer"]
    word = written_word(f, w)
    chain = ", ".join(c["function"] + (f" ({c['addr']})" if c.get("addr") else "") for c in w.get("chain", []))
    return (f"{NOTE_HEAD} in scenario {name}, at the start of port frame {f['port_frame']} (the emulator's vertical "
            f"blank {f['vblank']}) the word {word['addr']}{' (' + word['name'] + ')' if word['name'] else ''} holds "
            f"{word['actual']} in the port where the original holds {word['expected']}. The port's last write to it in "
            f"the frame before was {w['value']}, by this function (called from {chain or 'unknown'}). This function "
            f"computes that value differently from the original: compare each store to that word, and the values it "
            f"is built from, with the asm, and make them match. Keep everything else as the original has it.")


def requeue(results, log):
    """Appends the evidence to agent_notes/ADDR.md for each readable writer; the addresses."""
    addrs = []
    for name, res in results.items():
        w = res.get("writer") or {}
        if res.get("status") != "diverged" or not w.get("file") or not w.get("addr"):
            continue
        note = paths.AGENT_NOTES / f"{w['addr']}.md"
        text = note_text(name, res)
        old = note.read_text(encoding="utf-8") if note.exists() else ""
        if text not in old:
            paths.AGENT_NOTES.mkdir(exist_ok=True)
            note.write_text((old.rstrip() + "\n\n" if old.strip() else "") + text + "\n", encoding="utf-8")
        if w["addr"] not in addrs:
            addrs.append(w["addr"])
    if addrs:
        log(f"requeue (readable route, reviewer's notes in agent_notes/): {' '.join(addrs)}")
    return addrs


def main(a, log=print):
    game = game_dir(a.game)
    top = scenarios(game)
    names = list(top["scenarios"]) if a.all or not a.scenario else a.scenario
    missing = [n for n in names if n not in top["scenarios"]]
    if missing:
        raise SystemExit(f"no such scenario: {' '.join(missing)}")
    exe = flycast_exe()
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        jobs = [pool.submit(run_one, game, n, top["scenarios"][n], top, exe, a.build_dir, a.timeout, log,
                            getattr(a, "float_ulps", 0)) for n in names]
        for job in concurrent.futures.as_completed(jobs):
            name, res = job.result()
            results[name] = res
    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "game": str(game), "build_dir": a.build_dir,
           "counts": {s: sum(r["status"] == s for r in results.values())
                      for s in ("clean", "diverged", "float-close", "noise", "error")},
           "float_ulps": getattr(a, "float_ulps", 0),
           "scenarios": {n: results[n] for n in names}}
    refs = {n: game / "build" / "reference" / f"div_{n}" for n in top["scenarios"]}
    cov = div_coverage.from_workspace(paths, refs)
    div_coverage.write(paths.STATE / "coverage.json", cov)
    out["coverage"] = cov["figure"]
    log(div_coverage.summary(cov) + f" ({len(cov['scenarios'])} scenarios with coverage; state/coverage.json)")
    out["in_game"] = in_game.stamp(paths.ROOT, game, a.build_dir, results, cov, out["when"], log)
    if a.requeue:
        out["requeued"] = requeue(results, log)
    path = paths.STATE / "divergence.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    log(f"{out['counts']}; {path}")
    if out.get("requeued"):
        cmd = [sys.executable, str(paths.TOOL / "refine.py"), "--project", str(paths.ROOT), "run", "--redo",
               "--workers", str(max(1, a.workers))] + out["requeued"]
        rc = bounded(cmd, a.timeout * 4, paths.STATE / "divergence_requeue.log")
        log(f"refine.py run --redo: exit {rc} (state/divergence_requeue.log)")
    return 0 if out["counts"]["clean"] == len(names) else 1
