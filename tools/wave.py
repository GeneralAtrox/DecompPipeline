"""The re-stamp wave as one resumable driver: hold, broker live test and landing, recatalog, re-prepare every stale
matched harness of every workspace through the served Ghidra slots, until each workspace's stale count is 0.

usage: python tools/wave.py dry  [--ws KEY ...]                     counts and a time estimate; no Ghidra, no HOLD
       python tools/wave.py run  --head SHA --live-test CMD [--release-at-end] [--requeue]
       python tools/wave.py stop | release | status
PLAN (STATE_DIR/plan.json, or env WAVE_PLAN; machine paths stay out of the repo): {"dp": the live checkout the wave
prepares with, "land_script": land_dp.sh, "tc": workspace, "spawn": the folder of main, ovl, gend and sst*, "ps2": ws,
"fmac_list": the fmac requeue candidates note: "## Game (WORKSPACE)" headers, each followed by lines of addresses}.
Steps of `run`, each recorded in STATE (state.json) when done, so a run after a stop goes on where it ended:
  1 hold      HOLD in the broker folder (core/ghidra_broker) with a pass key only this driver's children carry
              (DP_BROKER_PASS); waits (at most HOLD_WAIT_S) until no Ghidra JVM runs, no broker token is held and no
              wave workspace's server answers. The sessions themselves are held by the monitor.
  2 live      the broker live test (CMD, a shell command; exit 0 = proven), then land_dp.sh SHA (LAND OK or NOOP),
              then the live checkout must have the HOLD pass (DP). A failed live test releases the HOLD and stops (4).
  3 catalog   decomp.py catalog on TC and PS2 (state files backed up to STATE_DIR/catalog/KEY first); refused unless
              only hw_refs/sys_ops/sr_ops/sq_refs changed, no function came or went, and no in-scope function newly
              tiers "excluded" (core/orch.tier, the hw_ops listing applied).
  4 restamp   per workspace in ORDER: tools/reprepare_stale.py --stamps --serve --max MAX in chunks of at most
              CHUNK_S (tools/bounded.py), slots 3 when RAM is under 82% at the chunk's start, else 2 (the broker
              still decides each JVM), and --brake 85: inside a chunk, at 85% or more a prepare starts only while no
              other runs, so the chunk slows before the guard below kills it; until a chunk exits 0 and a --dry
              check finds 0 stale. A chunk with nothing
              re-prepared twice running ends the workspace as stuck (listed in the summary).
  5 fmac      right after a workspace's re-stamp, if the fmac list names it: exactly its listed matched functions
              rejudged on their new harnesses (core/oracle_fresh.recount, the raw judge); every FAIL is a flip, written
              to STATE_DIR/fmac-flips-KEY.txt for requeueing (details in fmac-flips-KEY.json).
  6 requeue   only with --requeue, after the summary (and the release, with --release-at-end): each workspace's
              flips go to tools/requeue.py --project WS @fmac-flips-KEY.txt (what an operator ran by hand; the
              requeued functions are judged and re-matched by the usual jobs), once per workspace.
The guard: every GUARD_POLL_S, RAM >= 88% kills the running child's own tree (taskkill /T) and waits for 5 readings
under 85% before the next chunk; STOP_FILE (`wave.py stop`) kills it and exits 3 with the HOLD kept. Progress lines
and an ETA go to wave.log; the end writes summary.json (time, prepares, served vs fresh, failures, stale per ws).
"""
import argparse
import json
import os
import pathlib
import re
import secrets
import statistics
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
STATE_DIR = pathlib.Path(os.environ.get("LOCALAPPDATA", ".")) / "DecompPipeline" / "wave"
STATE, LOG, STOP_FILE = STATE_DIR / "state.json", STATE_DIR / "wave.log", STATE_DIR / "STOP"
PLAN = pathlib.Path(os.environ.get("WAVE_PLAN") or STATE_DIR / "plan.json")
DP, WS = TOOL, {}  # load_plan(): the live checkout, the workspaces
RECATALOG = ("tc", "ps2")
HOLD_WAIT_S, LIVE_S, LAND_S, CATALOG_S, CHUNK_S, MAX = 2700, 5400, 1500, 1800, 7200, 300
REJUDGE_S, JUDGE_S = 3600, 0.6  # a rejudge's bound; s per function at 4 judge workers (8 TC fmac fns, 4.3 s)
GUARD_POLL_S, KILL_AT, RESUME_UNDER, RESUME_READINGS, THIRD_UNDER = 1.0, 88.0, 85.0, 5, 82.0
OVERHEAD_S, FLOOR_S, LONG_S = 7.1, 1.0, 300  # a served prepare saves the JVM start + project open (r15 serve notes)
WORK_RATES = (77000, 112000)  # oracle steps/s measured under load (0d1c014); the budget is 65000 x deadline


def load_plan():
    global DP, WS
    WS = {k: pathlib.Path(v) for k, v in json.loads(PLAN.read_text(encoding="utf-8")).items()}
    DP = WS["dp"]


def order():
    """[(key, workspace)] in wave order: TC, Spawn main, ovl, gend, the sst* overlays, PS2."""
    spawn = [("spawn-" + n, WS["spawn"] / n) for n in ("main", "ovl", "gend")]
    spawn += [("spawn-" + p.name, p) for p in sorted(WS["spawn"].glob("sst*"))
              if (p / "state" / "ledger.json").exists()]
    return [("tc", WS["tc"]), *spawn, ("ps2", WS["ps2"])]


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"steps": {}, "ws": {}}


def save(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def broker_dir():
    from core import ghidra_broker
    cfg = json.loads((DP / "config.json").read_text(encoding="utf-8"))
    return ghidra_broker.settings(cfg)[1]


def ram():
    from core import ghidra_broker
    return ghidra_broker.ram_pct() or 0.0


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)


def guarded(cmd, limit, st, cwd=None):
    """Run cmd (its output to wave.log and out), bounded by limit seconds; RAM >= KILL_AT or STOP_FILE kills its own
    tree. Returns (rc, text): rc 124 timed out, "guard" RAM kill; exits 3 on STOP_FILE."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    if st.get("pass"):
        env["DP_BROKER_PASS"] = st["pass"]
    lines, t0 = [], time.monotonic()
    p = subprocess.Popen(cmd, cwd=cwd or DP, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", shell=isinstance(cmd, str))
    import threading
    reader = threading.Thread(target=lambda: [lines.append(ln.rstrip("\n")) or log("  | " + ln.rstrip("\n"))
                                              for ln in p.stdout], daemon=True)
    reader.start()
    why = None
    while p.poll() is None:
        r = ram()
        if r >= KILL_AT:
            why = "guard"
        elif STOP_FILE.exists():
            why = "stop"
        elif time.monotonic() - t0 > limit:
            why = 124
        if why:
            log(f"killing the child tree (pid {p.pid}): {why}, RAM {r:.1f}%")
            kill_tree(p.pid)
            p.wait(60)
            break
        time.sleep(GUARD_POLL_S)
    reader.join(30)
    p.stdout.close()
    if why == "stop":
        save(st)
        log("STOP: state saved; the HOLD stays (wave.py release removes it); run again to resume")
        sys.exit(3)
    return (why if why else p.returncode), "\n".join(lines)


def wait_ram(after_kill):
    """Before a chunk: under KILL_AT, and after a guard kill RESUME_READINGS readings in a row under RESUME_UNDER."""
    need, ok = (RESUME_READINGS if after_kill else 1), 0
    while ok < need:
        if STOP_FILE.exists():
            log("STOP while waiting for RAM")
            sys.exit(3)
        r = ram()
        ok = ok + 1 if r < (RESUME_UNDER if after_kill else KILL_AT) else 0
        if ok < need:
            time.sleep(10)
    return ram()


# --- step 1: hold ---

def ghidra_jvms():
    from core import ghidra_broker
    return ghidra_broker.jvms(ghidra_broker.processes())


def servers_up():
    sys.path.insert(0, str(TOOL / "tools"))
    from common import server_state
    return [k for k, ws in order() if server_state(ws, timeout=2) is not None]


def hold(st):
    d = broker_dir()
    d.mkdir(parents=True, exist_ok=True)
    st["pass"] = st.get("pass") or secrets.token_hex(8)
    (d / "HOLD").write_text(json.dumps({"pass": st["pass"], "by": "tools/wave.py", "since": time.time()}),
                            encoding="utf-8")
    save(st)
    log(f"HOLD written in {d}; waiting for running Ghidra work to end (at most {HOLD_WAIT_S} s)")
    t0 = time.monotonic()
    while True:
        jv, holds, up = ghidra_jvms(), sorted(p.name for p in d.glob("h_*.json")), servers_up()
        if not jv and not holds and not up:
            log("hold: no Ghidra JVM, no broker token, no wave workspace server")
            return
        if time.monotonic() - t0 > HOLD_WAIT_S:
            sys.exit(f"hold: still busy after {HOLD_WAIT_S} s: {len(jv)} JVM(s), tokens {holds}, servers {up}")
        log(f"hold: waiting: {len(jv)} Ghidra JVM(s), {len(holds)} token(s), servers up: {up or 'none'}")
        time.sleep(60)


def release():
    p = broker_dir() / "HOLD"
    if p.exists():
        p.unlink()
        log(f"HOLD removed ({p})")


# --- step 2: live test, landing ---

def live_and_land(st, a):
    if not st["steps"].get("live"):
        rc, _ = guarded(a.live_test, LIVE_S, st, cwd=TOOL)
        if rc != 0:
            release()
            log(f"broker live test failed (rc {rc}): HOLD released; resume everyone. Stopping.")
            sys.exit(4)
        st["steps"]["live"] = time.time()
        save(st)
    rc, out = guarded(["bash", str(WS["land_script"]), st["head"]], LAND_S, st, cwd=TOOL)
    if rc != 0 or not re.search(r"^LAND (OK|NOOP)", out, re.M):
        sys.exit(f"landing {st['head']} failed (rc {rc}); the HOLD stays")
    if "PASS_ENV" not in (DP / "core" / "ghidra_broker.py").read_text(encoding="utf-8"):
        sys.exit(f"{DP} has no HOLD pass after landing: its prepares would wait on the HOLD")


# --- step 3: recatalog ---

COUNTS = ("hw_refs", "sys_ops", "sr_ops", "sq_refs")


def catalog_check(old, new, tier_old, tier_new):
    """Problems of a recatalog: functions that came or went, fields other than COUNTS changed, in-scope functions
    newly excluded or gone from the tiers. tier_*: {addr: tier} of the in-scope functions."""
    o, n = {f["addr"]: f for f in old["functions"]}, {f["addr"]: f for f in new["functions"]}
    bad = [f"functions gone: {sorted(set(o) - set(n))[:10]}"] if set(o) - set(n) else []
    bad += [f"functions new: {sorted(set(n) - set(o))[:10]}"] if set(n) - set(o) else []
    other = [a for a in set(o) & set(n) if {k: v for k, v in o[a].items() if k not in COUNTS}
             != {k: v for k, v in n[a].items() if k not in COUNTS}]
    bad += [f"fields other than {COUNTS} changed: {sorted(other)[:10]}"] if other else []
    gone = sorted(set(tier_old) - set(tier_new))
    bad += [f"in-scope functions gone: {gone[:10]}"] if gone else []
    excl = sorted(a for a in tier_new if tier_new[a] == "excluded" and tier_old.get(a) not in (None, "excluded"))
    bad += [f"in-scope functions newly excluded: {excl[:10]}"] if excl else []
    return bad


def tiers(cat, ws):
    """{addr: tier} of the workspace's in-scope catalog functions (a child process: core reads DECOMP_PROJECT)."""
    from core import hw_ops, orch, paths, scope
    cfg = paths.config()
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    sc, listed, out = scope.Scope(cfg, ws), hw_ops.listed(cfg), {}
    for f in cat["functions"]:
        if f["addr"] in sc:
            t = orch.tier(dict(f), listed=listed)
            if t == "excluded" and (led.get(f["addr"]) or {}).get("status") == "matched":
                t = orch.tier(dict(f), matched=True, listed=listed)
            out[f["addr"]] = t
    return out


def me():
    """The live checkout's wave.py once landed (its scripts made the wave's stamps), else this one."""
    p = DP / "tools" / "wave.py"
    return p if p.exists() else pathlib.Path(__file__)


def child(fn, ws, *args):
    """Run wave.py's internal command fn for workspace ws in a fresh process; its JSON answer."""
    r = subprocess.run([sys.executable, "-B", str(me()), "_" + fn, str(ws), *map(str, args)], capture_output=True,
                       text=True, encoding="utf-8", timeout=1800, env=dict(os.environ, DECOMP_PROJECT=str(ws)))
    if r.returncode != 0:
        raise RuntimeError(f"{fn} {ws}: {r.stderr[-1500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def recatalog(st, key, ws):
    back = STATE_DIR / "catalog" / key
    back.mkdir(parents=True, exist_ok=True)
    names = ("catalog.json", "code_ranges.txt", "function_bodies.txt")
    if not (back / "catalog.json").exists():
        for n in names:
            if (ws / "state" / n).exists():
                (back / n).write_bytes((ws / "state" / n).read_bytes())
    rc, out = guarded([sys.executable, "-B", "decomp.py", "catalog", "--project", str(ws)], CATALOG_S, st)
    if rc != 0:
        sys.exit(f"recatalog {key} failed (rc {rc}); backups in {back}")
    bad = child("check", ws, back / "catalog.json")
    if bad["problems"]:
        sys.exit(f"recatalog {key} refused: {bad['problems']}; restore {back} into {ws / 'state'} before resuming")
    log(f"recatalog {key}: {bad['moves']}")


# --- step 4: re-stamp ---

def parse(out):
    """Counts from a reprepare_stale run's output."""
    g = lambda pat, d=0: int(m[1]) if (m := re.search(pat, out)) else d  # noqa: E731
    return {"stale": g(r"stamps: (\d+) matched harness\(es\) not current", -1),
            "stamped": g(r"stamps: (\d+) re-prepared"),
            "errors": g(r"(\d+) prepare error\(s\)") + g(r"prepare errors: (\d+)"),
            "left": g(r"; (\d+) left for the next run", -1), "cascade": g(r"(\d+) prepare\(s\) of \d+ function"),
            "still": g(r"still stale: (\d+)", -1),
            "failed": sorted(set(re.findall(r"([0-9a-f]{8}): re-prepared; its matched raw C FAILS", out)))}


def eta(st, keys):
    """Seconds left: each workspace's stale count at the wave's measured seconds per prepare."""
    done = [w for w in st["ws"].values() if w.get("prepared")]
    rate = (sum(w["seconds"] for w in done) / max(1, sum(w["prepared"] for w in done))) if done else 14.0
    left = [st["ws"][k]["left"] if "left" in st["ws"].get(k, {}) else st.get("est", {}).get(k, 0) for k in keys]
    return sum(max(0, n) for n in left) * rate


def restamp(st, key, ws, keys, fmac=None):
    """Step 4 for ws. fmac: its fmac list (step 5 judges those): their PASSes are recorded for step 5 (verdicts)."""
    w = st["ws"].setdefault(key, {"chunks": 0, "prepared": 0, "served": 0, "fresh": 0, "seconds": 0.0,
                                  "errors": 0, "failed": [], "guard_kills": 0, "idle": 0})
    after_kill = False
    while not w.get("done"):
        r = wait_ram(after_kill)
        slots = 3 if r < THIRD_UNDER else 2
        cmd = [sys.executable, "-B", "tools/bounded.py", str(CHUNK_S), sys.executable, "-B", "tools/reprepare_stale.py",
               "--project", str(ws), "--stamps", "--serve", "--slots", str(slots), "--max", str(MAX),
               "--brake", f"{RESUME_UNDER:g}"]
        if fmac:
            cmd += ["--verdicts", str(verdicts(key)), "--verdicts-for", str(fmac_in(key, fmac))]
        log(f"{key}: chunk {w['chunks'] + 1}, {slots} slots, RAM {r:.1f}%")
        t0 = time.monotonic()
        rc, out = guarded(cmd, CHUNK_S + 120, st)
        c = parse(out)
        w["chunks"] += 1
        w["seconds"] += time.monotonic() - t0
        w["served"] += c["stamped"]
        w["fresh"] += c["cascade"]
        w["prepared"] = w["served"] + w["fresh"]
        w["errors"] += c["errors"]
        w["failed"] = sorted(set(w["failed"]) | set(c["failed"]))
        w["left"] = c["left"] if c["left"] >= 0 else w.get("left", 0)
        after_kill = rc == "guard"
        w["guard_kills"] += after_kill
        w["idle"] = w["idle"] + 1 if c["stamped"] == 0 and c["cascade"] == 0 and not after_kill else 0
        save(st)
        log(f"{key}: chunk rc {rc}: {c}; ETA {eta(st, keys) / 3600:.1f} h for the rest of the wave")
        if rc == 0 or (w["left"] == 0 and c["still"] == 0):
            w["stale_after"] = child("count", ws)["stale"]
            w["done"] = "ok" if w["stale_after"] == 0 else "stale"
        elif w["idle"] >= 2:
            w["stale_after"] = child("count", ws)["stale"]
            w["done"] = "stuck"
        save(st)
    log(f"{key}: {w['done']}, stale after {w.get('stale_after')}, {w['prepared']} prepares ({w['served']} served, "
        f"{w['fresh']} fresh) in {w['seconds'] / 3600:.2f} h")


# --- step 5: the fmac rejudge ---

def fmac_in(key, addrs):
    """The fmac list of workspace key as a file (one address per line): step 4 records their verdicts, step 5
    judges them."""
    lst = STATE_DIR / "fmac" / f"{key}.in"
    lst.parent.mkdir(parents=True, exist_ok=True)
    lst.write_text("\n".join(addrs) + "\n", encoding="utf-8")
    return lst


def verdicts(key):
    """Step 4's PASS verdicts for workspace key (core/verdict_memo): step 5 takes those still on the same inputs."""
    return STATE_DIR / "verdicts" / f"{key}.jsonl"


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def fmac_lists():
    """{workspace (norm): [addr]} from the plan's fmac list; {} without one. A "## Game (WORKSPACE)" header starts a
    workspace; only lines of addresses alone are its candidates (a header's "unmatched:" list is not)."""
    out, cur = {}, None
    if "fmac_list" not in WS:
        return out
    for line in WS["fmac_list"].read_text(encoding="utf-8").splitlines():
        m = re.match(r"##\s.*\((.+)\)\s*$", line)
        if m:
            cur = out.setdefault(norm(m.group(1)), [])
        elif cur is not None and re.fullmatch(r"(8c[0-9a-f]{6}\s*)+", line.strip()):
            cur += line.split()
    return out


def rejudge(st, key, ws, addrs):
    """The listed matched fmac functions of ws judged on their new harnesses; FAILs (flips) to fmac-flips-KEY.txt."""
    w = st["ws"][key]
    if "fmac" in w:
        return
    lst = fmac_in(key, addrs)
    after_kill = True
    while after_kill:
        wait_ram(after_kill=False)
        rc, out = guarded([sys.executable, "-B", str(me()), "_rejudge", str(ws), str(lst), str(verdicts(key))],
                          REJUDGE_S, st)
        after_kill = rc == "guard"
    try:
        res = json.loads(out.strip().splitlines()[-1]) if rc == 0 else None
    except (ValueError, IndexError):
        res = None
    if res is None:
        w["fmac"] = {"error": f"rc {rc}"}
        log(f"{key}: fmac rejudge failed (rc {rc}); rerun it with: python tools/wave.py _rejudge {ws} {lst} "
            f"{verdicts(key)}")
    else:
        (STATE_DIR / f"fmac-flips-{key}.txt").write_text("".join(x + "\n" for x in res["flips"]), encoding="utf-8")
        (STATE_DIR / f"fmac-flips-{key}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        w["fmac"] = {k: (v if isinstance(v, int) else len(v)) for k, v in res.items()}
        log(f"{key}: fmac rejudge: {w['fmac']}; flips in {STATE_DIR / f'fmac-flips-{key}.txt'}")
    save(st)


# --- dry run ---

def matched_count(ws):
    try:
        led = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
    except (OSError, ValueError, KeyError):
        return 0
    return sum(1 for r in led.values() if r.get("status") == "matched")


def estimate(hist, matched, overhead=OVERHEAD_S):
    """(fresh s, served s, tail served s) for the matched functions from their prepare history: a function without
    history takes the workspace's median; served = fresh - overhead (at least FLOOR_S); tail: history >= LONG_S."""
    have = [hist[a] for a in matched if a in hist]
    med = statistics.median(have) if have else 26.0
    fresh = [hist.get(a, med) for a in matched]
    served = [max(s - overhead, FLOOR_S) for s in fresh]
    tail = sum(s for f, s in zip(fresh, served) if f >= LONG_S)
    return sum(fresh), sum(served), tail


def dry(a):
    keys = [k for k, _ in order() if not a.ws or k in a.ws]
    tot = {"n": 0, "stale": 0, "served": 0.0, "fresh": 0.0, "tail": 0.0}
    fm = fmac_lists()
    print(f"{'workspace':16s} {'matched':>7s} {'stale now':>9s} {'fresh h':>8s} {'served h':>8s} {'tail h':>7s} "
          f"{'fmac':>5s}")
    for k, ws in order():
        if k not in keys:
            continue
        c = child("count", ws)
        n = len(fm.get(norm(ws), []))
        tot["fmac"] = tot.get("fmac", 0) + n
        print(f"{k:16s} {c['matched']:7d} {c['stale']:9d} {c['fresh'] / 3600:8.2f} {c['served'] / 3600:8.2f} "
              f"{c['tail'] / 3600:7.2f} {n:5d}", flush=True)
        for x in ("stale", "served", "fresh", "tail"):
            tot[x] += c[x]
        tot["n"] += c["matched"]
    lo, hi = (65000 / WORK_RATES[1], 65000 / WORK_RATES[0])
    body, h = tot["served"] - tot["tail"], lambda s: f"{s / 3600:.1f}"
    budget = (body + tot["tail"] * lo, body + tot["tail"] * hi)
    print(f"total: {tot['n']} matched, {tot['stale']} stale now (every one once the next oracle change lands)")
    print(f"fmac rejudge: {tot.get('fmac', 0)} listed functions, about {tot.get('fmac', 0) * JUDGE_S / 60:.0f} min "
          f"of judging (no Ghidra)")
    print(f"Ghidra-hours: fresh {h(tot['fresh'])}, served {h(tot['served'])} (tail {h(tot['tail'])}); work budgets: "
          f"tail x{lo:.2f}-{hi:.2f} -> served {h(budget[0])}-{h(budget[1])}")
    for n in (2, 3):
        print(f"wall at {n} slots: {h(budget[0] / n)}-{h(budget[1] / n)} h (+ hold drain <= {HOLD_WAIT_S // 60} min,"
              f" live test <= {LIVE_S // 60} min, landing, 2 catalogs)")


def internal(cmd, ws, extra):
    from core import oracle_fresh, paths, verdict_memo
    if cmd == "_count":
        cfg = paths.config()
        matched = [x for x in oracle_fresh.matched_functions() if (paths.HARNESS / x).is_dir()]
        stale = [x for x, (k, _) in oracle_fresh.scan(cfg, matched).items() if k in ("no stamp", "old stamp")]
        from core import longtail
        fresh, served, tail = estimate(longtail.history(paths.STATE), matched)
        return {"matched": len(matched), "stale": len(stale), "fresh": fresh, "served": served, "tail": tail}
    if cmd == "_check":
        old = json.loads(pathlib.Path(extra[0]).read_text(encoding="utf-8"))
        new = paths.read_json(paths.CATALOG)
        t_old, t_new = tiers(old, pathlib.Path(ws)), tiers(new, pathlib.Path(ws))
        moves = {}
        for x in sorted(set(t_old) & set(t_new)):
            if t_old[x] != t_new[x]:
                moves[f"{t_old[x]}->{t_new[x]}"] = moves.get(f"{t_old[x]}->{t_new[x]}", 0) + 1
        return {"problems": catalog_check(old, new, t_old, t_new), "moves": moves}
    if cmd == "_rejudge":  # flips: matched and FAIL; other: MISSING/ERROR; not_current: judged on a harness not current
        cfg = paths.config()
        want = pathlib.Path(extra[0]).read_text(encoding="utf-8").split()
        matched = set(oracle_fresh.matched_functions())
        todo = [x for x in want if x in matched]
        memo = verdict_memo.Memo(extra[1]) if len(extra) > 1 else None  # step 4's PASSes on these very inputs
        res = oracle_fresh.recount(cfg, todo, workers=4, log=lambda m: print(m, flush=True), memo=memo)
        return {"judged": len(todo), "pass": sum(v[0] == "PASS" for v in res.values()),
                "reused": memo.hits if memo else 0,
                "flips": sorted(x for x, v in res.items() if v[0] == "FAIL"),
                "detail": {x: v[1] for x, v in sorted(res.items()) if v[0] != "PASS"},
                "other": sorted(x for x, v in res.items() if v[0] not in ("PASS", "FAIL")),
                "not_matched": sorted(set(want) - matched), "not_current": sorted(oracle_fresh.scan(cfg, todo))}
    raise SystemExit(f"unknown internal command {cmd}")


def run(a):
    st = load()
    STOP_FILE.unlink(missing_ok=True)
    st.setdefault("started", time.time())
    st["head"] = st.get("head") or a.head
    keys = [k for k, _ in order()]
    st.setdefault("est", {k: matched_count(ws) for k, ws in order()})  # every matched harness, for the ETA
    log(f"wave run: head {st['head']}, steps done {sorted(st['steps'])}")
    if not st["steps"].get("hold"):
        hold(st)
        st["steps"]["hold"] = time.time()
        save(st)
    if not st["steps"].get("land"):
        live_and_land(st, a)
        st["steps"]["land"] = time.time()
        save(st)
    for k, ws in order():
        if k in RECATALOG and not st["steps"].get("catalog_" + k):
            recatalog(st, k, ws)
            st["steps"]["catalog_" + k] = time.time()
            save(st)
    fm = fmac_lists()
    log(f"fmac lists: {sum(map(len, fm.values()))} functions in {len(fm)} workspace(s)" if fm else
        "fmac lists: none in the plan, no rejudge")
    for i, (k, ws) in enumerate(order()):
        restamp(st, k, ws, keys[i:], fm.get(norm(ws)))
        if norm(ws) in fm:
            rejudge(st, k, ws, fm[norm(ws)])
    ws = st["ws"]
    summary = {"hours": round((time.time() - st["started"]) / 3600, 2),
               "prepares": sum(w["prepared"] for w in ws.values()), "served": sum(w["served"] for w in ws.values()),
               "fresh": sum(w["fresh"] for w in ws.values()), "prepare_errors": sum(w["errors"] for w in ws.values()),
               "failing_raw_c": {k: w["failed"] for k, w in ws.items() if w["failed"]},
               "guard_kills": sum(w["guard_kills"] for w in ws.values()),
               "stale_after": {k: w.get("stale_after") for k, w in ws.items()},
               "not_ok": [k for k, w in ws.items() if w.get("done") != "ok"],
               "fmac": {k: w["fmac"] for k, w in ws.items() if "fmac" in w}}
    (STATE_DIR / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    log("summary: " + json.dumps(summary))
    if a.release_at_end:
        release()
    if a.requeue:
        requeue(st)
    return 0 if not summary["not_ok"] else 1


def requeue(st):
    """Step 6: each workspace's fmac flips to tools/requeue.py (as by hand), once per workspace (state steps)."""
    for k, ws in order():
        flips = STATE_DIR / f"fmac-flips-{k}.txt"
        if st["steps"].get("requeue_" + k) or not flips.exists() or not flips.read_text(encoding="utf-8").split():
            continue
        cmd = [sys.executable, "-B", str(me().parent / "requeue.py"), "--project", str(ws),
               "--note", "fused fmac: the matched C fails its re-stamped harness (wave fmac rejudge)", f"@{flips}"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
            rc, out = r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            rc, out = 124, "timed out after 600 s"
        log(f"{k}: requeue of the fmac flips: rc {rc}; {out[-400:]}")
        if rc == 0:
            st["steps"]["requeue_" + k] = time.time()
            save(st)


def main():
    if len(sys.argv) > 2 and sys.argv[1].startswith("_"):
        os.environ["DECOMP_PROJECT"] = sys.argv[2]  # before core.paths is imported
        print(json.dumps(internal(sys.argv[1], sys.argv[2], sys.argv[3:])))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("dry", "run", "stop", "release", "status"))
    ap.add_argument("--ws", nargs="*")
    ap.add_argument("--head")
    ap.add_argument("--live-test")
    ap.add_argument("--release-at-end", action="store_true")
    ap.add_argument("--requeue", action="store_true", help="step 6: requeue the fmac flips at the end")
    a = ap.parse_args()
    if a.cmd != "status":
        load_plan()
    from core import oplog  # the manual operator steps record (docs/DEFINITION_OF_DONE.md gate 2)
    try:
        wss = [ws for _, ws in order()] if a.cmd in ("run", "stop", "release") else []
    except (KeyError, OSError):  # a plan without the workspaces: the wave's own checks say so
        wss = []
    if a.cmd == "run":
        oplog.begin("wave.py run", wss)
    elif a.cmd in ("stop", "release"):  # a person's hand on the wave
        oplog.record(wss, "wave.py", "act", f"wave.py {a.cmd}")
    if a.cmd == "dry":
        dry(a)
    elif a.cmd == "run":
        if not (a.head or load().get("head")) or not (a.live_test or load()["steps"].get("land")):
            ap.error("run needs --head and --live-test (until step 2 is done)")
        return run(a)
    elif a.cmd == "stop":
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text("", encoding="utf-8")
        print(f"{STOP_FILE} written: the driver kills its child tree and exits 3 within {GUARD_POLL_S:g} s")
    elif a.cmd == "release":
        release()
    else:
        print(json.dumps(load(), indent=1))
    return 0


if __name__ == "__main__":
    from core import oplog
    sys.exit(oplog.main(main))
