"""Readable C proven in the running game: decomp.py divergence records it, tools/scorecard.py counts it.

The per-function judge proves a readable file against its oracle vectors, yet the running game can still break: on
2026-11-06 PS2's readable C hung at boot on a spin wait (fixed in 31dcb0d2) and PS2 still differs from frame 0, Spawn in
68 of 2,866 frames, all with every function passing the judge. So a readable function counts as done only once a
divergence scenario ran it in the port and came out clean.

stamp(), after decomp.py divergence: state/in_game.json gets {ADDR: {"sha", "scenario", "when", "build_dir"}} for each
function that
  (a) the build compiled as readable C: PORT_READABLE in GAME/build/<BD>_gen/port_files.cmake (gen_port.py) names
      this workspace's readable/src/ADDR.c;
  (b) Flycast entered (state/coverage.json, core/div_coverage) in a scenario whose status is clean;
  (c) whose readable file is no newer than the newest port.exe under GAME/build/<BD>, so that build compiled this content.
Earlier entries are kept (proofs accumulate across runs), but the entry of every function a diverged scenario names
as its writer is dropped, whatever else ran clean.

readable_proven(ws): the functions whose state/in_game.json sha equals readable/src/ADDR.c's sha now and whose
state/readable.json row is status readable; an edited file loses its proof until a clean scenario runs it again. The
one rule: the scorecard's "readable C proven in the running game" row counts it, and a build that should take only
proven readable C filters its passing set with it (in gen_port.py: `passing &= in_game.readable_proven(paths.ROOT)`).
That switch is each game card's decision and is not made here; gen_port's choice of readable files is unchanged.
Only the standard library: the scorecard imports this without a workspace set.

Nearest existing: core/lookalike.readable_sources - it lists the passing readable files but imports the workspace's
paths (a project must be set, so the scorecard can't use it) and knows nothing of the running game; core/divergence.py
runs the scenarios, this records what a clean one proves.
"""
import hashlib
import json
import pathlib
import re
import time


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load(ws):
    """state/in_game.json ({} when absent or unreadable)."""
    d = _json(pathlib.Path(ws) / "state" / "in_game.json")
    return d if isinstance(d, dict) else {}


def build_files(game, build_dir):
    """({addr: path} of the readable files the build compiled, the newest port.exe under GAME/build/<BD> or None)."""
    build = pathlib.Path(game) / "build"
    own = build_dir if build_dir not in (None, "", "x86", "x86chk") else None  # build.py's rule (ram_compare.gen_dir)
    cm = build / (f"{own}_gen" if own else "gen") / "port_files.cmake"
    files = {}
    if cm.is_file():
        m = re.search(r"^set\(PORT_READABLE\n(.*?)^\)", cm.read_text(encoding="utf-8"), re.M | re.S)
        for q in re.findall(r'"([^"]+)"', m.group(1) if m else ""):
            p = pathlib.Path(q)
            files[p.stem.lower()] = p
    exes = list((build / (build_dir or "x86")).rglob("port.exe")) if (build / (build_dir or "x86")).is_dir() else []
    return files, max(exes, key=lambda p: p.stat().st_mtime, default=None)


def stamp(ws, game, build_dir, results, coverage, when=None, log=print):
    """Records the proofs of one divergence run (module notes) in WS/state/in_game.json; the counts."""
    ws = pathlib.Path(ws)
    src = (ws / "readable" / "src").resolve()
    when = when or time.strftime("%Y-%m-%d %H:%M:%S")
    files, exe = build_files(game, build_dir)
    exe_t = exe.stat().st_mtime if exe else None
    per = (coverage or {}).get("scenarios") or {}
    led = load(ws)
    added, newer = set(), set()
    for name, res in results.items():
        if res.get("status") != "clean" or exe_t is None:
            continue
        for addr in (per.get(name) or {}).get("entered") or []:
            addr = addr.lower()
            f = src / f"{addr}.c"
            if addr in added or addr not in files or files[addr].resolve() != f or not f.is_file():
                continue
            if f.stat().st_mtime > exe_t:
                newer.add(addr)
                continue
            led[addr] = {"sha": sha(f), "scenario": name, "when": when, "build_dir": build_dir}
            added.add(addr)
    dropped = []
    for res in results.values():
        a = ((res.get("writer") or {}).get("addr") or "").lower() if res.get("status") == "diverged" else ""
        if a and led.pop(a, None) is not None:
            dropped.append(a)
    (ws / "state").mkdir(parents=True, exist_ok=True)
    (ws / "state" / "in_game.json").write_text(json.dumps(dict(sorted(led.items())), indent=1), encoding="utf-8")
    out = {"proven": len(added - set(dropped)), "newer_than_exe": len(newer), "dropped": len(dropped),
           "entries": len(led), "exe": str(exe) if exe else None, "in_build": len(files)}
    log(f"in game: {out['proven']} readable functions proven by a clean scenario ({len(files)} readable in the build, "
        f"{len(newer)} newer than port.exe left out, {len(dropped)} dropped as a diverged scenario's writer); "
        f"{len(led)} in state/in_game.json" + ("" if exe else f"; no port.exe under {pathlib.Path(game) / 'build'}"))
    return out


def readable_proven(ws):
    """The readable functions proven in the running game now (module notes): in_game.json's sha equals the file's,
    and readable.json says readable."""
    ws = pathlib.Path(ws)
    rd = _json(ws / "state" / "readable.json")
    rd = {k.lower(): v for k, v in rd.items()} if isinstance(rd, dict) else {}
    out = set()
    for a, e in load(ws).items():
        f = ws / "readable" / "src" / f"{a}.c"
        row = rd.get(a.lower())
        if (isinstance(e, dict) and isinstance(row, dict) and row.get("status") == "readable" and f.is_file()
                and e.get("sha") == sha(f)):
            out.add(a)
    return out
