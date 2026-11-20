"""tools/AutoOnboard/autoonboard.py's estimate and translate stages. Estimate: a stratified sample of the workspace's scope is prepared (tools/prepare_list.py)
and translated and judged (tools/translate_backlog.py --record), and the whole scope's hours, translator PASS share and
disk are projected from it, with a range. Console-free: any workspace DP's prepare and translator stage take.

Sample: the scope (tools/wave_game.py's step 1 list, so the estimate covers what the translate stage will run) sorted
by instruction count and cut into BANDS bands of equal size; a seeded draw of the same number from each band. Every
band is the same share of the scope, so the projection weights each band's mean by its count.
Projection, per band b with N_b functions and the sample's per-function seconds: prepare hours sum(N_b * mean_b(prepare
total_s, state/prepare_timing.jsonl)) / slots, translate hours sum(N_b * mean_b(translate_s + judge_s)) / jobs, times
the sample's own measured wall over that model of the sample (JVM starts, queueing) when both phases ran whole in this
run. The range is the 10th to 90th percentile of REPS bootstrap draws within each band (seeded). Disk: the sample's mean
bytes per function under harness/, packets/, runs/, build/ and matched/, times the scope.
The PASS share's range is also widened to the sample's 95% Wilson score interval (pass_range): a bootstrap of a sample
with no FAIL is 100-100, which a finite sample cannot show (60 of 60 PASS still allows about 6% FAIL; the rule of three
gives 5%). Taken over the whole sample, not per band: an approximation, wider rather than narrower.
What it does not cover: agent attempts (none run here) and functions whose prepare fails (counted, not projected).
Translate: tools/wave.py run --game WS --stop-after translate --tx-chunk N --fn-seconds FN_S under tools/jobreg.py and
tools/bounded.py, each run under the 50-minute wait (or autoonboard.py --seconds) and resumed by the next: wave_game prepares
and translates one chunk at a time and records each function as it ends (wave_game.recorded), so a run cut off mid-chunk
keeps what it finished. One function's prepare is killed after FN_S s and recorded as a timeout, not tried again at that
limit; N, sized from the estimate so a chunk ends within TX_FILL of one run, is only tuning. A resumed run tells jobreg
how far the pass is (--changed): the same command, but its inputs have moved on; a run that recorded nothing refuses
the next identical one. No harness_reuse
needed (recording refuses a stale harness; the agent step, which needs it, is not run). A run that does not finish the
pass writes a partial GAPS.md (onboard_gaps) and stops with how much is done; the next run goes on.

Nearest existing: targets/xbox360_ppc/tools/proof_pick.py sample - five size bands drawn in turn too, but it picks roots
whose 4-call closure fits a budget (Xbox 360 catalog_inputs fields, ghidra_setup.closure), so its bands are not shares
of the scope and cannot be projected from; docs/proofs/2026-10-30/gc-tx/pick.py (branch x360-judge-codeptr) draws from
overlapping shape groups (float, jump table, ...) for test coverage, with no counts to weigh by. tools/wave_game.py
--only runs a sample through the whole route and keeps hours per step, not per function, and its state would mark
steps done for the full pass.
"""
import json
import pathlib
import random
import subprocess
import sys
import time

import onboard_stages as S

BANDS, SAMPLE, SLOTS, JOBS, REPS, SEED = 5, 60, 2, 4, 400, 20261115
PREPARE_S, TRANSLATE_S = 1800, 900  # together under one 50-minute wait; prepare_list and the judge resume
ROOTS = ("harness", "packets", "runs", "build")


def bands(funcs, k=BANDS):
    """[[addr, ...] per band]: the scope by (instructions, address) cut into k bands of (nearly) equal size."""
    order = [f["addr"] for f in sorted(funcs, key=lambda f: (f.get("insns", 0), f["addr"]))]
    return [order[i * len(order) // k:(i + 1) * len(order) // k] for i in range(k)]


def draw(bs, n=SAMPLE, seed=SEED):
    """{band index: sorted sample} with n // len(bs) (at least 2, at most the band) from each band."""
    rng, per = random.Random(seed), max(2, n // len(bs))
    return {i: sorted(rng.sample(b, min(per, len(b)))) for i, b in enumerate(bs) if b}


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def project(sizes, per, slots=SLOTS, jobs=JOBS, calib=1.0, reps=REPS, seed=SEED):
    """sizes {band: N_b}; per {band: [{"prepare_s", "translate_s", "pass", "bytes"}, ...]}. The projection and its
    range: hours, PASS share and GB, each (point, low, high)."""
    def one(sample):
        p = sum(sizes[b] * _mean([r["prepare_s"] for r in rs if r["prepare_s"] is not None]) for b, rs in sample.items())
        t = sum(sizes[b] * _mean([r["translate_s"] for r in rs]) for b, rs in sample.items())
        n = sum(sizes[b] for b in sample)
        return ((p / slots + t / jobs) * calib / 3600,
                sum(sizes[b] * _mean([r["pass"] for r in rs]) for b, rs in sample.items()) / max(n, 1),
                sum(sizes[b] * _mean([r["bytes"] for r in rs]) for b, rs in sample.items()) / 2**30)
    rng, point = random.Random(seed), one(per)
    boots = [one({b: [rng.choice(rs) for _ in rs] for b, rs in per.items() if rs}) for _ in range(reps)]
    return {k: (round(point[i], 3), round(_pct([x[i] for x in boots], 0.1), 3), round(_pct([x[i] for x in boots], 0.9), 3))
            for i, k in enumerate(("hours", "pass", "GB"))}


def wilson(k, n, z=1.96):
    """The Wilson score interval (low, high) of k successes in n: never 1-1 or 0-0 for a finite n."""
    if n <= 0:
        return 0.0, 1.0
    p, d = k / n, 1 + z * z / n
    mid, half = (p + z * z / (2 * n)) / d, z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (0.0 if k == 0 else max(0.0, mid - half)), (1.0 if k == n else min(1.0, mid + half))


def pass_range(point, lo, hi, k, n):
    """The PASS share's (point, low, high): the bootstrap range widened to the sample's Wilson interval."""
    wl, wh = wilson(k, n)
    return round(point, 3), round(min(lo, wl), 3), round(max(hi, wh), 3)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def du(path):
    if path.is_file():
        return path.stat().st_size
    return sum(q.stat().st_size for q in path.rglob("*") if q.is_file()) if path.is_dir() else 0


def scope(ctx, ws):
    """tools/wave_game.py's step 1 list (in-scope catalog functions not tiered "excluded"), in a child process."""
    r = subprocess.run([sys.executable, "-B", str(ctx.tool / "tools" / "wave_game.py"), "_scope", str(ws)],
                       capture_output=True, text=True, timeout=1800, cwd=ctx.tool)
    if r.returncode:
        raise S.StageError(f"wave_game.py _scope failed: {r.stderr.strip()[-300:]}", ["the scope list"],
                           f"py -3.12 tools/wave_game.py _scope {ws}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def timings(ws, addrs, since):
    """{addr: total_s} of each listed function's latest prepare ending after `since` (state/prepare_timing.jsonl)."""
    out, f = {}, ws / "state" / "prepare_timing.jsonl"
    if f.is_file():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("addr") in addrs and r.get("at", 0) >= since and r.get("ok"):
                out[r["addr"]] = (r.get("phases") or {}).get("total")
    return out


def sample_prepares(ws, sample, t0):
    """(this run's prepare times, every prepare time) of the sample. A re-run keeps the harnesses an earlier run made and
    prepares nothing, so the projection takes every recorded time; only the calibration needs this run's own walls."""
    return timings(ws, set(sample), t0 - 1), timings(ws, set(sample), 0)


class Estimate(S.Built):
    tools = ("tools/prepare_list.py", "tools/translate_backlog.py", "tools/wave_game.py", "tools/jobreg.py",
             "tools/bounded.py")

    def calls(self, ctx):
        return [f"tools/wave_game.py _scope WS; a seeded {SAMPLE}-function draw from {BANDS} size bands",
                f"tools/jobreg.py run -- tools/bounded.py {PREPARE_S} -- tools/prepare_list.py --project WS @sample "
                f"--serve --slots {SLOTS}",
                f"tools/jobreg.py run -- tools/bounded.py {TRANSLATE_S} -- tools/translate_backlog.py --project WS "
                f"--only @sample --jobs {JOBS} --record --json ROWS (rows reduced into estimate_detail.json, then deleted)"]

    def run(self, ctx):
        ws, state = S.ws_of(ctx), ctx.project / S.STATE_DIR
        cat = {f["addr"]: f for f in json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]}
        sc = [a for a in scope(ctx, ws) if a in cat]
        if not sc:
            raise S.StageError(f"{ws}: the scope is empty", ["functions in scope"], "game_end in project.json")
        bs = bands([cat[a] for a in sc])
        picks = draw(bs)
        sample = sorted(a for v in picks.values() for a in v)
        lst = state / "estimate_sample.txt"
        lst.write_text("".join(a + "\n" for a in sample), encoding="utf-8")
        t0 = time.time()
        rc, tail = S.run_tool(ctx, "estimate", ["tools/prepare_list.py", "--project", ws, f"@{lst}", "--serve",
                                                "--slots", SLOTS, "--out", state / "estimate_prepare.tsv"], PREPARE_S,
                              why=f"onboard estimate of {ctx.name}: prepare a {len(sample)}-function sample",
                              produces=ws / "harness" / sample[0])
        prep_wall = time.time() - t0
        if rc not in (0, 1):  # 1: some prepares failed (counted below); anything else stopped the run
            raise S.StageError(f"prepare_list.py exited {rc}: {' | '.join(tail[-3:])}", ["the sample's harnesses"],
                               f"read {S.log_path(ctx, 'estimate')}; a run again resumes (prepare_list keeps them)")
        rows_f, t1 = state / "estimate_rows.json", time.time()
        rc, tail = S.run_tool(ctx, "estimate", ["tools/translate_backlog.py", "--project", ws, "--only", f"@{lst}",
                                                "--jobs", JOBS, "--record", "--json", rows_f], TRANSLATE_S,
                              why=f"onboard estimate of {ctx.name}: translate and judge the sample", produces=rows_f)
        tx_wall = time.time() - t1
        if rc or not rows_f.is_file():
            raise S.StageError(f"translate_backlog.py exited {rc}: {' | '.join(tail[-3:])}", ["the sample's verdicts"],
                               f"read {S.log_path(ctx, 'estimate')}")
        rows = {r["addr"]: r for r in json.loads(rows_f.read_text(encoding="utf-8"))["rows"]}
        rows_f.unlink()
        fresh, prep = sample_prepares(ws, sample, t0)
        if not prep:
            raise S.StageError(f"none of the {len(sample)} sample functions has a prepare: {' | '.join(tail[-3:])}",
                               ["the sample's harnesses"], f"read {S.log_path(ctx, 'estimate')} and "
                               f"{ws / 'state' / 'prepare_timing.jsonl'}: the cause is around the functions (Ghidra)")
        band_of = {a: b for b, v in picks.items() for a in v}
        per = {b: [] for b in picks}
        for a in sample:
            r = rows.get(a, {})
            tx = (r.get("translate_s") or (r.get("translator") or {}).get("translate_s") or 0) + (r.get("judge_s") or 0)
            per[band_of[a]].append({"addr": a, "prepare_s": prep.get(a), "translate_s": tx,
                                    "pass": 1.0 if r.get("verdict") == "PASS" else 0.0, "verdict": r.get("verdict"),
                                    "bytes": sum(du(ws / d / a) for d in ROOTS) + du(ws / "matched" / f"{a}.c")})
        model = (sum(x["prepare_s"] or 0 for v in per.values() for x in v) / SLOTS
                 + sum(x["translate_s"] for v in per.values() for x in v) / JOBS)
        whole = len(fresh) == len(sample)  # every sample function prepared in this run: the walls are the sample's own
        calib = (prep_wall + tx_wall) / model if whole and model > 0 else 1.0
        sizes = {b: len(bs[b]) for b in picks}
        got = project(sizes, per, calib=calib)
        judged = [x for v in per.values() for x in v if x["verdict"]]
        got["pass"] = pass_range(*got["pass"], int(sum(x["pass"] for x in judged)), len(judged))
        verdicts = {}
        for v in per.values():
            for x in v:
                verdicts[x["verdict"] or "no row"] = verdicts.get(x["verdict"] or "no row", 0) + 1
        est = {"scope": len(sc), "sample": len(sample), "bands": [{"functions": len(b), "insns": [
            cat[b[0]].get("insns"), cat[b[-1]].get("insns")]} for b in bs], "seed": SEED, "slots": SLOTS, "jobs": JOBS,
            "prepared_now": len(fresh), "prepared_before": len(prep) - len(fresh),
            "prepare_wall_s": round(prep_wall, 1), "translate_wall_s": round(tx_wall, 1),
            "model_s": round(model, 1), "calibration": round(calib, 3), "verdicts": verdicts, "projection": got,
            "per_function": per, "covers": "Ghidra prepare + translator + judge; no agents; failed prepares not projected"}
        (state / "estimate_detail.json").write_text(json.dumps(est, indent=1) + "\n", encoding="utf-8", newline="\n")
        h, p, g = got["hours"], got["pass"], got["GB"]
        fmt = lambda t, k=1: f"{t[0]:.{k}f} ({t[1]:.{k}f}-{t[2]:.{k}f})"  # noqa: E731
        return {"numbers": {"scope": len(sc), "sample": len(sample), "projected hours": fmt(h),
                            "translator PASS": f"{p[0]:.0%} ({p[1]:.0%}-{p[2]:.0%})",
                            "FAIL at most (95%)": f"{1 - p[1]:.0%}", "projected GB": fmt(g, 2),
                            "sample verdicts": ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items())),
                            "calibration": round(calib, 2)},
                "paths": {"estimate": str(state / "estimate_detail.json"), "sample": str(lst)},
                "message": (f"{len(sc)} functions in scope: about {h[0]:.1f} h ({h[1]:.1f}-{h[2]:.1f}) of prepare and "
                            f"translation on {SLOTS} slots and {JOBS} jobs, translator PASS {p[0]:.0%} "
                            f"({p[1]:.0%}-{p[2]:.0%}: up to {1 - p[1]:.0%} may FAIL), {g[0]:.2f} GB; from a "
                            f"{len(sample)}-function sample. Agents for the FAILs are not in it"),
                "next": [f"Plan the translate stage's disk ({g[2]:.2f} GB at most by the sample) and time "
                         f"({h[2]:.1f} h at most) on the build box"] + ([
                             "Over 5 GB: cut the run down first (CLAUDE.md)"] if g[2] > 5 else [])}



TX_BOUND_S, TX_CHUNK, TX_FILL = 2700, 100, 0.66  # a run under the 50-minute wait; default chunk; a chunk's share of a run
REUSE_NEXT = ("Before the agent step (tools/wave.py run --game WS, no --stop-after) a person decides harness_reuse: no "
              "check proves a workspace's harnesses reusable (the oracle stamp does not cover the Ghidra project's own "
              "state; AGENTS.md, harness_reuse, on only in the gamecube_ppc target.json). This pass needed no key: "
              "recording refuses a stale harness")


# Two prepare limits (prepare_list --kill-s), one function each. Saturn Bomberman's first chunk, 2026-11-16: 117 functions in
# 40 min, 11 timeouts (8.5% of rows) at 300 s, about 70% of the chunk's time. So every function gets FN_S first; the ones
# killed there get one try at FN_LATE_S once nothing unrecorded is left (wave_game --fn-seconds-late); a kill at FN_LATE_S
# is final. The sample's prepares took about 8 s. jobreg keys the command text, so 60 and 300 are two different jobs.
FN_S, FN_LATE_S = 60, 300
PACE_N, PACE_MIN = 40, 10  # the latest prepares that set the measured pace; fewer than this and it is not used


def measured_per(ws, slots=None):
    """Seconds per function on this box, from the latest PACE_N prepares in state/prepare_timing.jsonl (each record's
    phases.total, failures included: a prepare that hangs to its own timeout costs its slot that long), over the
    slots. None with fewer than PACE_MIN records. Saturn Bomberman, 2026-11-15: the estimate's sample gave about 8 s
    a function, but four functions (06006372, KRNL's endless main loop, among them) each ran about 900 s and failed, so
    a chunk of 219 did not end in 2700 s."""
    try:
        rows = [json.loads(ln) for ln in (ws / "state" / "prepare_timing.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return None
    tot = [r["phases"]["total"] for r in rows[-PACE_N:] if isinstance(r.get("phases", {}).get("total"), (int, float))]
    if len(tot) < PACE_MIN:
        return None
    return sum(tot) / len(tot) / (slots or SLOTS)


def chunk_size(ctx, bound):
    """Functions per chunk: TX_FILL of one bound run at the slower of the estimate's projected seconds per function
    (prepare and translate, on its slots and jobs) and the pace this workspace's latest prepares measured
    (measured_per), else TX_CHUNK."""
    per = []
    try:
        est = json.loads((ctx.project / S.STATE_DIR / "estimate_detail.json").read_text(encoding="utf-8"))
        per.append(est["projection"]["hours"][0] * 3600 / est["scope"])
    except (OSError, ValueError, KeyError, ZeroDivisionError, TypeError, IndexError):
        est = {}
    m = measured_per(S.ws_of(ctx), est.get("slots") if isinstance(est, dict) else None)
    if m:
        per.append(m)
    per = max(per, default=0)
    return max(10, min(1000, int(TX_FILL * bound / per))) if per > 0 else TX_CHUNK


def stale_ghidra_locks(ws):
    """Removes the Ghidra project locks a time-bound stop leaves: bounded.py has killed the whole tree, so no Ghidra
    owns them (Skies, 2026-11-15: ghidra_slots/slot1/geae8p.lock stayed after the stopped run). Returns the paths."""
    try:
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    gdir = pathlib.Path(cfg.get("ghidra_dir") or ws)
    gone = []
    for q in (*gdir.glob("ghidra_slots/*/*.lock*"), *gdir.glob("ghidra/*.lock*")):
        # a lock the killed tree's last process still holds for a moment (Saturn Bomberman, 2026-11-16: WinError 32 on
        # slot1's .lock~ ended the stage with no verdict, after it had recorded its rows): a few tries, then left for the
        # next run, which clears it before it starts
        for _ in range(5):
            try:
                if q.is_file():
                    q.unlink()
                    gone.append(q)
                break
            except PermissionError:
                time.sleep(2)
            except FileNotFoundError:
                break
    return gone


class Translate(S.Built):
    """The translator's first pass: tools/wave.py run --game WS --stop-after translate --tx-chunk N, bounded, resumed
    by the next run (wave_game's state file and its recorded chunks). Only the summary comes back."""
    tools = ("tools/wave.py", "tools/wave_game.py", "tools/translate_backlog.py", "tools/prepare_list.py",
             "tools/jobreg.py", "tools/bounded.py")

    def bound(self, ctx):
        return max(300, min(TX_BOUND_S, getattr(ctx, "seconds", TX_BOUND_S + 200) - 200))

    @staticmethod
    def late(ctx):
        """The retry pass limit: autoonboard.py --late-seconds, else FN_LATE_S; 0 runs no retry pass."""
        got = getattr(ctx, "late_seconds", None)
        return FN_LATE_S if got is None else got

    def calls(self, ctx):
        b = self.bound(ctx)
        return [f"tools/jobreg.py run -- tools/bounded.py {b} -- tools/wave.py run --game WS --stop-after translate "
                f"--tx-chunk N --fn-seconds {FN_S} --fn-seconds-late {self.late(ctx)} (N from the estimate: {TX_FILL:.0%} of a {b} s run)",
                "read state/wave_game.json, wave_game_summary.json, wave_game_translate.tsv; on a run that does not "
                "finish, tools/AutoOnboard/onboard_gaps.write (a partial GAPS.md)"]

    def run(self, ctx):
        import wave_game
        ws, b = S.ws_of(ctx), self.bound(ctx)
        n = chunk_size(ctx, b)
        f, mark = ws / "state" / "wave_game.json", ws / "state" / "onboard_translate_start.json"
        st0 = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        sc0 = set(st0.get("scope") or [])
        before = len(wave_game.recorded(ws, st0, FN_S) & sc0)
        try:
            last = json.loads(mark.read_text(encoding="utf-8")).get("recorded")
        except (OSError, ValueError, AttributeError):
            last = None
        # the same command resumes a pass whose state has moved on since the last start: jobreg's key cannot see that.
        # No progress since the last start: no --changed, and jobreg refuses an identical re-run of a timed-out job
        changed = (f"resume: {before} of {len(sc0)} functions recorded (was {last} at the last start)"
                   if sc0 and last is not None and before > last else None)
        if sc0:
            mark.write_text(json.dumps({"recorded": before, "at": S.stamp()}) + "\n", encoding="utf-8")
        t0 = time.time()
        rc, tail = S.run_tool(ctx, "translate", ["tools/wave.py", "run", "--game", ws, "--stop-after", "translate",
                                                 "--tx-chunk", n, "--fn-seconds", FN_S, "--fn-seconds-late", self.late(ctx)], b,
                              why=f"onboard translate of {ctx.name}: the translator's first pass, no agents",
                              produces=ws / "state" / "wave_game.json", changed=changed)
        mins = max((time.time() - t0) / 60, 1e-6)
        if rc == 124:
            stale_ghidra_locks(ws)
        st = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"steps": {}}
        sc = set(st.get("scope") or [])
        done, total = len(wave_game.recorded(ws, st, FN_S) & sc), len(sc)
        rows = wave_game.prepare_rows(ws)
        t1 = sum(1 for a, (s, lim, _) in rows.items() if s == "timeout" and lim == FN_S and a in sc)
        t2 = sum(1 for a, (s, lim, _) in rows.items() if s == "timeout" and lim == FN_LATE_S and a in sc)
        timeouts = t1 + t2
        this = done - (before if sc0 else 0)
        pace = {"recorded this run": this, "minutes": round(mins, 1), "per minute": round(this / mins, 2),
                "prepare timeouts": timeouts, f"timeouts at {FN_S} s": t1, f"timeouts at {FN_LATE_S} s": t2,
                "prepare limit s": FN_S, "late limit s": FN_LATE_S}
        pl = (f"; this run recorded {this} in {pace['minutes']} min ({pace['per minute']} a "
              f"minute), {t1} prepare timeout(s) at {FN_S} s and {t2} at {FN_LATE_S} s (the {FN_S} s ones get one "
              f"{FN_LATE_S} s try once nothing else is left; a {FN_LATE_S} s one is final)")
        if not st["steps"].get("translate"):
            import onboard_gaps
            out = ctx.project / S.STATE_DIR / "GAPS.md"
            try:
                g = onboard_gaps.write(ws, ctx.console, out, ctx.name)
                gl = (f"; partial GAPS.md written ({g['translated']} translated so far, {g['not translated']} not, "
                      f"{g['refused/unjudged']} refused or unjudged, {g['not tried']} not tried yet): {out}")
            except (SystemExit, OSError, ValueError) as e:
                gl = f"; no partial GAPS.md ({e})"
            why = "it reached its time bound" if rc == 124 else f"wave rc {rc}: {' | '.join(tail[-2:])}"
            left_n = total - done
            eta = round(left_n / pace["per minute"], 1) if pace["per minute"] else None
            nums = {"rows recorded": done, "scope": total, "recorded this run": this, "minutes": pace["minutes"],
                    "per minute": pace["per minute"], f"timeouts at {FN_S} s": t1, f"timeouts at {FN_LATE_S} s": t2,
                    "left": left_n, "projected minutes left": eta}
            raise S.StageError(f"translate: not finished in this {b} s run ({why}): {done} of {total} recorded "
                               f"(per function; chunks of {n}), {total - done} left" + pl + gl,
                               [f"the other {total - done} function(s) of the first pass"],
                               "autoonboard.py again: it resumes at translate, the chunks already done are kept",
                               status="partial", numbers=nums)
        logs = ws / "state" / "wave_game_logs"  # the summary and the short rows stay; the children's logs go
        if rc == 0 and logs.is_dir():
            for q in logs.glob("*.log"):
                q.unlink(missing_ok=True)
        sm = json.loads((ws / "state" / "wave_game_summary.json").read_text(encoding="utf-8"))
        route = sm.get("route", {})
        return {"numbers": {"scope": sm.get("scope"), **{k: v for k, v in sorted(route.items())},
                            "no harness": len(sm.get("no_harness") or []), "hours": sm.get("hours"), **pace},
                "paths": {"summary": str(ws / "state" / "wave_game_summary.json"),
                          "rows": str(ws / "state" / "wave_game_translate.tsv")},
                "message": f"translator pass over {sm.get('scope')} functions in chunks of {n}: "
                           + ", ".join(f"{k} {v}" for k, v in sorted(route.items())) + "; no harness_reuse needed" + pl,
                "next": ["Read GAPS.md (the gaps stage) before any agent run on the FAILs", REUSE_NEXT]}
