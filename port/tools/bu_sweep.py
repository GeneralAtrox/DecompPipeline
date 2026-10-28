"""Bring-up sweeps: many blockers per run of a port, not one per round. Diagnostic only; nothing here goes into a play
build (the runtime side is runtime/diag.c, off unless --diag-skip or --diag-cover names a file).

    python port/tools/bu_sweep.py sweep --game DIR --build B --script S.json --out DIR [--steps 20] [--minutes 60] [--resume]
    python port/tools/bu_sweep.py weak  --game DIR --build B --sweep DIR [--ab --script S.json [--group A,B,..]]

sweep: runs the built port (DIR/build/B/port.exe) headless with the script's input. At a blocker it records a row
(class, guest function, guest call chain, vertical blank, last screen, skips before it) and steps past it for the next
run: a fault makes its innermost guest function a logging stub that returns 0 (runtime --diag-skip: the host
function's start jumps to the stub, no rebuild); a must-work stub reached leaves the must-work list. A hang, the
entry returning or the script's goal ends the sweep. Each run boots from the start with one more skip, so a row
after k skips is a map for planning, not proof: the skips changed what followed.

weak: from the first sweep run's coverage (runtime --diag-cover: a one-shot breakpoint at every guest function's
host start; the run with no skips), each executed function with its port C (readable, translator, stub; the build's
port.map names the object), its judge verdict and "weak" where the ledger's verdict_detail says call sites were
never reached. --ab: every weak readable function with a PASS translator C goes into tx_first of a copy of the game
folder, built (B + "-ab") and run with the same script; frames and log are compared with the first run, and on a
difference the set is halved (up to --ab-runs runs). An A/B result decides nothing on its own.

The script (JSON): {"seconds": N, "input": "START@300,...", "args": [extra port args], "dump_every": N,
"milestones": [{"name": "...", "log": regex}, ...] (in order; all met = the goal), "card": a vmu_save_A1.bin or
absent, "skip": [guest addresses skipped from the first run], "hang_frames": N (identical last frames = a hang),
"stop_at": a vertical blank to end each run at}.
"""
import argparse
import bisect
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import game_config  # noqa: E402
import symbolize  # noqa: E402

ROW = re.compile(r'\{0x([0-9a-f]{8})u,\s*(?:\(PortFn\))?(\w+),\s*"(\w+)"\}')
FLOAT = re.compile(r"^\s*(?:extern\s+)?(?:f32|f64|float|double)\s+\**\s*(\w+)\s*\(", re.M)


class Build:
    """A built port: its executable, the guest functions' host symbols (port.map and the generated dispatch rows)."""

    def __init__(self, game, name):
        self.game = pathlib.Path(game).resolve()
        self.dir = self.game / "build" / name
        self.exe = self.dir / "port.exe"
        self.gen = self.game / "build" / f"{name}_gen"
        self.syms = syms = symbolize.load_map(self.dir / "port.map", objects=True)
        self.keys = [s[0] for s in syms]
        guest = {}  # host symbol -> (guest address, guest name)
        for f in self.gen.rglob("*.c"):
            if f.name.startswith(("dispatch_", "overlay_table")):
                for a, sym, nm in ROW.findall(f.read_text(encoding="utf-8", errors="replace")):
                    guest.setdefault(sym, (a, nm))
        self.floats = set()
        for f in self.gen.rglob("*.h"):
            self.floats.update(FLOAT.findall(f.read_text(encoding="utf-8", errors="replace")))
        self.fn = {}  # rva -> [(guest address, guest name, host symbol, kind)]
        for rva, sym, obj in syms:
            s = sym[1:] if sym.startswith("_") else sym
            if s in guest:
                self.fn.setdefault(rva, []).append((*guest[s], s, kind_of(obj)))

    def at(self, rva):
        """(host symbol, offset, object) of the function holding rva, or None."""
        i = bisect.bisect_right(self.keys, rva) - 1
        return (self.syms[i][1], rva - self.syms[i][0], self.syms[i][2]) if i >= 0 else None

    def guest_at(self, rva):
        """The guest functions whose host body holds rva (more than one: identical bodies folded), or []."""
        i = bisect.bisect_right(self.keys, rva) - 1
        return self.fn.get(self.syms[i][0], []) if i >= 0 else []

    def rva_of(self, sym):
        return next((r for r, rows in self.fn.items() for row in rows if row[2] == sym), None)

    def cover_file(self, path):
        """Every guest function's host start that is not a logging stub."""
        rows = [f"{r:08x} {g[0]} {g[1]}" for r, gs in sorted(self.fn.items()) for g in gs[:1] if g[3] != "stub"]
        pathlib.Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")
        return len(rows)


def kind_of(obj):
    if re.search(r"(^|:)tx_[0-9a-f]{8}\.c\.obj$", obj):
        return "translator"
    if re.search(r"stubs(_\d+)?\.c\.obj$", obj) or obj.startswith("dispatch_"):
        return "stub"
    return "readable"


def run_port(b, cfg, script, rd, skip=(), must=None, cover=False):
    """One bounded headless run into folder rd; returns (exit code, seconds)."""
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "data" / "saves").mkdir(parents=True, exist_ok=True)
    if script.get("card"):
        shutil.copy(script["card"], rd / "data" / "saves" / "vmu_save_A1.bin")
    (rd / "skip.txt").write_text("".join(f"{r:08x} {a} {'f' if s in b.floats else 'i'} {n} {v}\n"
                                         for r, a, n, s, v in skip), encoding="utf-8")
    must_path = rd / "must_work.txt"
    must_path.write_text("".join(f"{m}\n" for m in (must or [])), encoding="utf-8")
    secs = int(script.get("seconds", 60))
    cmd = [str(b.exe), "--disc", str(cfg["disc"]), "--headless", "--fixed-clock", "1", "--present", "tick",
           "--seconds", str(secs), "--no-dialog", "--data-directory", str(rd / "data"), "--log", str(rd / "port.log"),
           "--must-work", str(must_path), "--diag-skip", str(rd / "skip.txt"),
           "--dump-every", str(script.get("dump_every", 300)), "--dump-dir", str(rd / "frames")]
    if cfg.get("fsca_table"):
        cmd += ["--check-fsca", str(cfg["fsca_table"])]
    if script.get("input"):
        cmd += ["--input", script["input"]]
    if cover:
        cmd += ["--diag-cover", str(b.dir / "diag_cover.txt")]
    if script.get("stop_at"):  # a vertical blank limit: a frozen game counts blanks fast
        cmd += ["--stop-at", str(script["stop_at"])]
    cmd += script.get("args", [])
    t = time.time()
    with open(rd / "run.out", "wb") as out:
        p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(b.dir))
        try:
            code = p.wait(timeout=secs + 60)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            p.wait(timeout=30)
            code = "killed"
    return code, round(time.time() - t, 1)


def frames(rd):
    return sorted((rd / "frames").glob("frame_*.png"))


def frame_vblanks(log):
    return {int(f): int(v) for f, v in re.findall(r"frame (\d+) dumped \(vblank (\d+)", log)}


def classify(b, rd, code, script, skipped):
    """The run's end as a row: {class, function, chain, vblank, screen, detail}."""
    log = (rd / "port.log").read_text(encoding="utf-8", errors="replace")
    fr = frames(rd)
    vbs = frame_vblanks(log)
    m = re.search(r"last stub called: ([0-9a-f]{8})", log)
    row = {"exit": code, "screen": str(fr[-1]) if fr else None, "last_stub": m and m.group(1), "function": None,
           "chain": []}
    vb = re.findall(r"after (\d+) vertical blanks|fault at vblank (\d+)", log)
    row["vblank"] = int(next((a or c for a, c in reversed(vb)), 0) or 0) or (max(vbs.values()) if vbs else 0)
    row["milestones"] = [m["name"] for m in script.get("milestones", []) if re.search(m["log"], log)]
    row["misses"] = sorted(set(re.findall(r"(?:CALL|OVERLAY MISS: call to) ([0-9a-f]{8})", log)))[:12]
    fault = re.search(r"^FAULT .*$", log, re.M)
    must = re.search(r"first must-work stub reached: ([0-9a-f]{8}) (\w+)", log)
    if fault:
        rvas = [int(x, 16) for x in re.findall(r"eip: port\.exe\+([0-9a-f]{8})", log)[:1]]
        rvas += [int(x, 16) - 1 for x in re.findall(r"chain: port\.exe\+([0-9a-f]{8})", log)]
        chain, pick = [], None
        for i, r in enumerate(rvas):
            gs = b.guest_at(r)
            if not gs:
                continue
            g = gs[0]
            name = f"{g[0]} {g[1]} ({g[3]}{', folded x%d' % len(gs) if len(gs) > 1 else ''})"
            if not chain or chain[-1] != name:
                chain.append(name)
            if pick is None and len(gs) == 1 and g[3] != "stub" and g[2] not in skipped:
                pick = (b.rva_of(g[2]), g[0], g[1], g[2])
        row.update({"class": "fault", "detail": fault.group(0), "chain": chain[:16], "pick": pick,
                    "function": pick and f"{pick[1]} {pick[2]}"})
        if "CALL" in log[max(0, fault.start() - 200):fault.start()]:
            row["detail"] = log[log.rfind("CALL", 0, fault.start()):fault.end()].replace("\n", " ")
    elif must:
        row.update({"class": "stub", "detail": must.group(0), "function": f"{must.group(1)} {must.group(2)}",
                    "must": must.group(2)})
    elif "entry function returned" in log:
        row.update({"class": "exit", "detail": "the entry function returned"})
    else:
        n = int(script.get("hang_frames", 4))
        tail = [hashlib.md5(p.read_bytes()).hexdigest() for p in fr[-n:]]
        goal = len(row["milestones"]) == len(script.get("milestones", [])) and (
            script.get("milestones") or "--stop-at reached" in log)
        where = [x for x in re.findall(r"stack\+[0-9a-f]+: port\.exe\+([0-9a-f]{8})|eip port\.exe\+([0-9a-f]{8})",
                                        log) for x in x if x]
        names = list(dict.fromkeys(f"{g[0]} {g[1]}" for x in where for g in b.guest_at(int(x, 16))[:1]))
        frozen = len(tail) == n and len(set(tail)) == 1
        cls = "goal" if goal else "hang" if frozen else "wrong mode"
        detail = f"{'frozen screen' if frozen else 'time limit'} (exit {code}); milestones met: {row['milestones']}"
        nomiss = re.search(r"overlays: \d+ units, \d+ switches, (\d+) calls with no loaded function", log)
        lost = re.findall(r"^(?:OVERLAY MISS: call to|CALL) ([0-9a-f]{8})[: ]", log, re.M)
        if not goal and nomiss and int(nomiss.group(1)) > 1000 and lost:  # a code address the port has no function at
            names = [f"{lost[-1]} (no host function: not a catalog function start)"] + names
            detail = f"{int(nomiss.group(1))} calls into overlay code with no function; " + detail
        if hot := re.findall(r"^\s+(\d+) calls\s+([0-9a-f]{8}) (\w+) \(first", log, re.M):
            detail += f"; hottest stub {hot[0][1]} {hot[0][2]} ({hot[0][0]} calls)"
        row.update({"class": cls, "detail": detail, "chain": names[:12],
                    "function": names[0] if names and not goal else None})
    return row


def must_list(cfg):
    return [m for m in (ln.split("#")[0].strip() for ln in open(cfg["must_work"], encoding="utf-8")) if m] \
        if cfg["must_work"] else []


def initial_skip(b, script):
    """The script's "skip" addresses as skip rows of build b (rva, address, name, host symbol)."""
    skip = []
    for x in script.get("skip", []):
        rva = next((r for r, gs in b.fn.items() for g in gs if g[0] == x and g[3] != "stub"), None)
        if rva is None:
            raise SystemExit(f"skip {x}: no host function of it in {b.dir}")
        g = next(g for g in b.fn[rva] if g[0] == x)
        skip.append((rva, x, g[1], g[2], 0))
    return skip


def sweep(a):
    cfg = game_config.load(a.game)
    b = Build(a.game, a.build)
    script = json.loads(pathlib.Path(a.script).read_text(encoding="utf-8"))
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ncov = b.cover_file(b.dir / "diag_cover.txt")
    must, skip = must_list(cfg), initial_skip(b, script)
    rows, t0 = [], time.time()
    if a.resume and (out / "blockers.json").is_file():  # go on from the rows so far (bounded chunks of a sweep)
        rows = json.loads((out / "blockers.json").read_text(encoding="utf-8"))
        for r in rows:
            if r.get("skip_row"):
                skip.append(tuple(r["skip_row"]))
            elif r["class"] == "stub" and r.get("must") in must:
                must.remove(r["must"])
        if rows and not (rows[-1].get("skip_row") or rows[-1]["class"] == "stub"):
            print("sweep: already ended at", rows[-1]["class"])
            return
    print(f"sweep: {b.exe}; {ncov} guest function starts for coverage; must-work {len(must)}; "
          f"{len(skip)} skips", flush=True)
    for step in range(len(rows), a.steps):
        if time.time() - t0 > a.minutes * 60:
            break
        rd = out / f"step_{step:02d}"
        if rd.exists():
            raise SystemExit(f"exists: {rd}")
        code, secs = run_port(b, cfg, script, rd, skip, must, cover=step == 0)
        row = classify(b, rd, code, script, {s[3] for s in skip})
        row.update({"step": step, "skips_before": len(skip), "seconds": secs})
        pick = row.pop("pick", None)
        row["skip_row"] = [*pick, max(0, row["vblank"] - 1)] if pick and pick[0] is not None else None
        rows.append(row)
        print(f"step {step}: {row['class']} {row.get('function')} at vblank {row['vblank']} ({secs} s, "
              f"{len(skip)} skips before)", flush=True)
        (out / "blockers.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        if row["skip_row"]:
            skip.append(tuple(row["skip_row"]))
        elif row["class"] == "stub" and row["must"] in must:
            must.remove(row["must"])
        else:
            break
    (out / "blockers.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    (out / "blockers.md").write_text(table(rows), encoding="utf-8")
    print(table(rows))


def table(rows):
    t = ["| step | skips before | class | guest function | vblank | chain (innermost first) | last stub | detail |",
         "|---:|---:|---|---|---:|---|---|---|"]
    for r in rows:
        t.append(f"| {r['step']} | {r['skips_before']} | {r['class']} | {r.get('function') or ''} | {r['vblank']} | "
                 f"{'; '.join(r['chain'][:6])} | {r.get('last_stub') or ''} | {(r.get('detail') or '')[:120]} |")
    return "\n".join(t) + "\n"


def coverage(log):
    return re.findall(r"^COVER ([0-9a-f]{8}) (\w+) at vblank (\d+)", log, re.M)


def ledgers(cfg):
    """{guest address: (status, verdict_detail)} from the main workspace's ledger and every overlay unit's."""
    out = {}
    roots = [cfg["decomp"]]
    ov = cfg["dir"] / "overlays.json"
    if ov.is_file():
        for u in json.loads(ov.read_text(encoding="utf-8")).get("overlays", []):
            if u.get("decomp"):
                roots.append((cfg["dir"] / u["decomp"]).resolve())
    for r in roots:
        p = pathlib.Path(r) / "state" / "ledger.json"
        if p.is_file():
            for x, rec in json.loads(p.read_text(encoding="utf-8")).get("functions", {}).items():
                out.setdefault(x, (rec.get("status"), rec.get("verdict_detail") or ""))
    return out


def tx_passing(cfg):
    import tx_port  # noqa: E402
    tc = cfg.get("translator_c")
    return set(tx_port.judged(pathlib.Path(tc["dir"]))[0]) if tc else set()


def compare(base, other):
    """Differences between two runs: frames by frame number (md5) and the log's lines (host offsets masked)."""
    fa = {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in frames(base)}
    fb = {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in frames(other)}
    diff = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))

    def lines(rd):
        t = (rd / "port.log").read_text(encoding="utf-8", errors="replace")
        t = t.replace(str(rd), "RUN").replace(str(rd).replace("\\", "/"), "RUN")
        t = re.sub(r"COVER [0-9a-f]{8} \w+ at vblank \d+\n", "", t)  # also after a line with no newline of its own
        t = re.sub(r"port\.exe\+[0-9a-f]{8}", "port.exe+X", t)  # the two builds' host code differs
        t = re.sub(r"^(FAULT \w+ at )\w+", r"\1X", t, flags=re.M)
        return [ln for ln in t.splitlines() if not ln.startswith(("COVER ", "diag:", "  eax=", "tx: ", "dispatched guest calls:"))]  # tx: glue notes
    la, lb = lines(base), lines(other)
    first = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), None if len(la) == len(lb) else min(len(la), len(lb)))
    return {"frames_differ": len(diff), "first_frame": diff[0] if diff else None,
            "log_first_diff": None if first is None else {"line": first, "base": la[first] if first < len(la) else None,
                                                           "ab": lb[first] if first < len(lb) else None}}


def weak(a):
    cfg = game_config.load(a.game)
    b = Build(a.game, a.build)
    sw = pathlib.Path(a.sweep)
    base = sw / "step_00"
    cov = coverage((base / "port.log").read_text(encoding="utf-8", errors="replace"))
    led, txok = ledgers(cfg), tx_passing(cfg)
    kinds = {g[0]: g[3] for gs in b.fn.values() for g in gs}
    rows = []
    for x, name, vb in cov:
        st, det = led.get(x, (None, ""))
        m = re.search(r"weak pass: (\d+) of (\d+) call sites never reached: ([^;)]*)", det)
        rows.append({"addr": x, "name": name, "first_vblank": int(vb), "port_c": kinds.get(x, "?"), "ledger": st,
                     "weak": m.group(0) if m else "", "tx_pass": x in txok})
    cand = [r["addr"] for r in rows if r["weak"] and r["port_c"] == "readable" and r["tx_pass"]]
    prev = sw / "weak.json"
    rep = {"covered": len(rows), "weak": sum(1 for r in rows if r["weak"]), "candidates": cand,
           "ab": json.loads(prev.read_text(encoding="utf-8")).get("ab", []) if prev.is_file() else []}  # kept
    (sw / "weak.json").write_text(json.dumps({"rows": rows, **rep}, indent=1) + "\n", encoding="utf-8")
    print(f"covered {len(rows)}; weak {rep['weak']} (readable with a PASS translator C: {len(cand)})")
    if a.ab and cand:
        script = json.loads(pathlib.Path(a.script).read_text(encoding="utf-8"))
        todo, n = [a.group.split(",") if a.group else cand], len(list(sw.glob("ab_run_*")))
        while todo:
            group = todo.pop(0)
            res = ab_run(a, cfg, script, sw, group, n)
            n += 1
            rep["ab"].append({"functions": group, **res})
            print(f"A/B {len(group)} function(s) {group[:4]}: {res}", flush=True)
            (sw / "weak.json").write_text(json.dumps({"rows": rows, **rep}, indent=1) + "\n", encoding="utf-8")
            if (res["frames_differ"] or res["log_first_diff"]) and len(group) > 1 and len(rep["ab"]) < a.ab_runs:
                todo += [group[:len(group) // 2], group[len(group) // 2:]]


def ab_run(a, cfg, script, sw, group, n):
    """One copy of the game folder (its build folder reused: each A/B builds only what changed) with tx_first +=
    group, built and run as the sweep's first run (the script's skips only)."""
    src = cfg["dir"]
    dst = sw / "ab_game"
    if not dst.exists():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build"))
    g = json.loads((src / "game.json").read_text(encoding="utf-8"))
    g["tx_first"] = sorted(set(g.get("tx_first", [])) | set(group))
    (dst / "game.json").write_text(json.dumps(g, indent=2) + "\n", encoding="utf-8")
    bd = f"{a.build}-ab"
    r = subprocess.run([sys.executable, "-B", str(HERE / "build.py"), "--game", str(dst), "--build-dir", bd,
                        "--engine", "--jobs", str(a.jobs)], capture_output=True, text=True, timeout=1500)
    (sw / f"ab_build_{n}.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    if r.returncode:
        return {"error": f"build exit {r.returncode}", "frames_differ": 0, "log_first_diff": None}
    bb = Build(dst, bd)
    rd = sw / f"ab_run_{n}"
    if rd.exists():
        raise SystemExit(f"exists: {rd}")
    code, secs = run_port(bb, game_config.load(dst), script, rd, initial_skip(bb, script), must_list(cfg))
    return {"exit": code, "seconds": secs, **compare(sw / "step_00", rd)}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sweep")
    w = sub.add_parser("weak")
    for q, k in ((s, "game"), (w, "game"), (s, "build"), (w, "build"), (s, "script"), (s, "out"), (w, "sweep")):
        q.add_argument(f"--{k}", required=True)
    s.add_argument("--steps", type=int, default=20)
    s.add_argument("--minutes", type=float, default=60)
    s.add_argument("--resume", action="store_true", help="go on from --out's blockers.json")
    w.add_argument("--ab", action="store_true")
    w.add_argument("--script")
    w.add_argument("--ab-runs", type=int, default=7)
    w.add_argument("--jobs", type=int, default=4)
    w.add_argument("--group", help="A/B these addresses (comma-separated) after the A/B runs so far")
    a = p.parse_args()
    sweep(a) if a.cmd == "sweep" else weak(a)


if __name__ == "__main__":
    main()
