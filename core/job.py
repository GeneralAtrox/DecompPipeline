"""One function, start to finish: prepare in Ghidra, run Haiku attempts, judge, record. Runs on a worker thread."""
import datetime
import json
import re
import shutil
import time
from pathlib import Path

from . import agent, check, ghidra, paths

HARNESS_FILES = ("vectors.txt", "expected.txt", "meta.json")
BACKOFF_S = (60, 120, 240, 480, 600)


class Fatal(Exception):
    """Stops every worker (authentication failure, spend cap)."""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def score_text(s):
    if s is None:
        return "none"
    if s >= 3 * 10 ** 6:
        return "rule violations"
    if s >= 2 * 10 ** 6:
        return "does not build (error or warning)"
    if s >= 10 ** 6 // 2:
        return "runner crash/timeout"
    return f"{s} failing vectors"


def model_cfg(cfg, addr):
    """cfg for addr's agent attempts. state/escalate.json ({addr: model}) names the leftovers escalated to a
    stronger model: they get escalate_budget_usd and escalate_timeout_s per attempt instead of the defaults."""
    try:
        model = json.loads((paths.STATE / "escalate.json").read_text(encoding="utf-8")).get(addr)
    except (OSError, ValueError):
        model = None
    if not model:
        return cfg
    return dict(cfg, model=model, attempt_budget_usd=cfg.get("escalate_budget_usd", 5.0),
                attempt_timeout_s=cfg.get("escalate_timeout_s", 1800))


def known_blocker(cfg, run_atts):
    """The name of the known checker limitation (target.json known_blockers) that every difference of the last
    `after` attempts of this run comes from, or None. Such a function is parked as failed ("blocked: ...") instead
    of retried; requeue it by that detail once the checker fix lands. Any other difference keeps it retrying."""
    for b in cfg.get("known_blockers") or []:
        last = run_atts[-b["after"]:]
        if len(last) < b["after"]:
            continue
        lines = [[x for fd in a.get("first_diffs") or [] for x in fd.get("diffs") or []] for a in last]
        if all(a.get("stage") == "compare" and ls and all(re.search(b["rx"], x) for x in ls)
               for a, ls in zip(last, lines)):
            return b["name"]
    return None


def attempt_summary(att):
    if att.get("tamper"):
        return "TAMPER: " + att["tamper"]
    if att["stage"] == "lint":
        return f"{len(att['lint'])} rule violation(s)"
    if att["stage"] == "build":
        return "does not build (error or warning)"
    s = f"dev {att['dev_total'] - att['dev_failing']}/{att['dev_total']} match, " \
        f"hidden {att['hidden_total'] - att['hidden_failing']}/{att['hidden_total']} match"
    return s + (f"; {att['run_error']}" if att.get("run_error") else "")


def investigate(o, w, addr):
    """o: Orchestrator, w: Worker. Returns the final status."""
    cfg = o.cfg
    f = o.functions[addr]
    r = o.rec(addr)
    r.update({"status": "waiting", "started": now(), "attempts": [], "tier": f["tier"], "score": f["score"],
              "insns": f["insns"], "model": model_cfg(cfg, addr)["model"], "system": o.system_of.get(addr), "worker": w.id})
    r.pop("verdict_detail", None)
    o.save()
    w.set_phase(addr, f"waiting for a free Ghidra slot ({cfg.get('ghidra_parallel', 1)} in total)")
    try:
        with ghidra.slot(cfg, w.stop_flag) as gs:
            if w.stop_flag.is_set():
                return finish(o, addr, "stopped", w.stop_reason)
            r["status"] = "preparing"
            w.set_phase(addr, f"Ghidra slot {gs}: export packet, generate vectors, run original in emulator")
            o.log(f"[W{w.id}] {addr}: preparing in Ghidra slot {gs} "
                  f"(tier {r['tier']}, score {r['score']}, system {r['system']})")
            meta, summary = ghidra.prepare(cfg, addr, stop_flag=w.stop_flag, slot_index=gs)
    except Exception as e:  # noqa: BLE001
        return finish(o, addr, "stopped" if w.stop_flag.is_set() else "error", str(e).splitlines()[0])
    r["signature"] = meta["signature"]
    r["oracle"] = summary
    if meta.get("stale_callers"):  # ghidra.prepare: a new signature leaves these callers' records stale
        o.log(f"[W{w.id}] {addr}: new signature; caller harnesses to re-prepare: {' '.join(meta['stale_callers'])}")
    if meta.get("unsupported"):
        return finish(o, addr, "skipped", "unsupported: " + meta["unsupported"])
    mv = cfg["min_valid_vectors"]
    if not summary or summary["dev_valid"] < mv or summary["hidden_valid"] < mv:
        why = ", ".join(f"{k} x{v}" for k, v in (summary or {}).get("invalid", {}).items())
        return finish(o, addr, "skipped", f"oracle could not produce {mv}+ valid vectors per set ({why})")
    o.log(f"[W{w.id}] {addr}: oracle ok: dev {summary['dev_valid']}, hidden {summary['hidden_valid']}, "
          f"blocks {summary['blocks_hit_all']}/{summary['blocks_total']}")
    r["harness_sha256"] = {n: paths.sha256_file(paths.HARNESS / addr / n) for n in HARNESS_FILES}
    r["status"] = "running"
    o.save()

    best, stalls, prev_work, previous_text, n, waits, run_atts = None, 0, None, "", 1, 0, []
    imported = paths.IMPORTS / f"{addr}.c"
    if imported.exists():  # relocated C of the same or a similar function from an earlier project
        att = judge_import(o, w, addr, imported)
        r["attempts"].append(att)
        o.save()
        if att["verdict"] == "PASS":
            return finish(o, addr, "matched", "PASS: imported from an earlier project, no agent run" + weak_note(att))
        prev_work = att["work_file"]
        note = imported.with_suffix(".note")  # why work.c starts from this C, when it is not a cross-project import
        previous_text = agent.PREVIOUS.format(n=0, summary=att["summary"], details=att["feedback"]) + (
            "\n" + note.read_text(encoding="utf-8").strip() + "\n" if note.exists() else IMPORT_NOTE)
    while n <= cfg["max_attempts"]:
        if w.stop_flag.is_set():
            return finish(o, addr, "stopped", w.stop_reason)
        o.check_budget()
        until = o.usage.blocked_until()
        if until:
            if wait_for_reset(o, w, addr, n, until):
                return finish(o, addr, "stopped", w.stop_reason)
            continue
        att = attempt(o, w, addr, n, meta, summary, prev_work, previous_text)
        if att.get("api_error") == "auth":
            finish(o, addr, "stopped", "claude CLI authentication failed")
            raise Fatal("claude CLI authentication failed: log in again (claude auth login), then restart workers")
        if att.get("api_error") == "rate_limit" and not att.get("tamper"):
            until = o.usage.blocked_until()
            if until:
                if wait_for_reset(o, w, addr, n, until):
                    return finish(o, addr, "stopped", w.stop_reason)
                continue
            delay = BACKOFF_S[min(waits, len(BACKOFF_S) - 1)]
            waits += 1
            o.log(f"[W{w.id}] {addr}: rate limited; attempt {n} not counted, retrying in {delay}s")
            w.set_phase(addr, f"rate limited: waiting {delay}s before retrying attempt {n}", n)
            if w.stop_flag.wait(delay):
                return finish(o, addr, "stopped", w.stop_reason)
            continue
        waits = 0
        r["attempts"].append(att)
        o.save()
        if att["verdict"] == "PASS":
            return finish(o, addr, "matched", f"PASS on attempt {n}" + weak_note(att))
        if att.get("tamper"):
            return finish(o, addr, "failed", "tamper: " + att["tamper"])
        if w.stop_flag.is_set():
            return finish(o, addr, "stopped", w.stop_reason)
        run_atts.append(att)
        blocker = known_blocker(cfg, run_atts)
        if blocker:
            return finish(o, addr, "failed", f"blocked: {blocker}; parked after attempt {n} until the checker fix")
        s = att["score"]
        if best is None or s < best:
            best, stalls = s, 0
        else:
            stalls += 1
            if stalls >= cfg["stall_limit"]:
                return finish(o, addr, "failed", f"no progress for {stalls} attempts (best: {score_text(best)})")
        prev_work = att["work_file"]
        previous_text = agent.PREVIOUS.format(n=n, summary=att["summary"], details=att["feedback"])
        n += 1
    return finish(o, addr, "failed", f"attempt limit {cfg['max_attempts']} reached (best: {score_text(best)})")


def wait_for_reset(o, w, addr, n, until):
    """The account hit its 5-hour or weekly limit: sleep until it resets. Returns True if stopped meanwhile."""
    delay = max(30, until - time.time() + 30)
    at = time.strftime("%a %H:%M", time.localtime(until))
    o.log(f"[W{w.id}] {addr}: usage limit reached; waiting until {at} (attempt {n} not counted)")
    w.set_phase(addr, f"usage limit: waiting until {at}", n)
    return w.stop_flag.wait(delay)


def attempt(o, w, addr, n, meta, summary, prev_work, previous_text):
    cfg = model_cfg(o.cfg, addr)
    run_dir = paths.RUNS / addr / f"attempt-{n:02d}"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    box, task = agent.build_sandbox(cfg, addr, meta, summary, prev_work, previous_text)
    (run_dir / "TASK.md").write_text(task, encoding="utf-8")
    before = agent.sandbox_hashes(box)
    w.set_phase(addr, f"{cfg['model']} attempt {n}/{cfg['max_attempts']}", n)
    o.log(f"[W{w.id}] {addr}: attempt {n} started ({cfg['model']})")
    t0 = time.time()
    session = agent.run_attempt(cfg, box, task, run_dir / "transcript.jsonl",
                                lambda m: o.log(f"[W{w.id}] {addr} #{n}: {m}"), w.stop_flag,
                                lambda info: o.usage.update(info, f"W{w.id} {addr} attempt {n}"))
    att = {"n": n, "model": cfg["model"], "started": now(), "duration_s": round(time.time() - t0, 1), **session}
    att["model"] = att.get("model_actual") or cfg["model"]  # the alias resolves to whatever the CLI ships today
    w.add_cost(att.get("cost_usd") or 0)
    work_file = run_dir / "work.c"
    if (box / "work.c").exists():
        shutil.copyfile(box / "work.c", work_file)
    else:
        work_file.write_text("", encoding="utf-8")
    att["work_file"] = str(work_file)
    after = agent.sandbox_hashes(box)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        att["tamper"] = "files other than work.c changed: " + ", ".join(changed)
    harness_now = {nm: paths.sha256_file(paths.HARNESS / addr / nm) for nm in HARNESS_FILES}
    if harness_now != o.rec(addr)["harness_sha256"]:
        att["tamper"] = "harness files changed during the attempt"
    if att.get("api_error") and not att.get("tamper"):
        return att  # not judged: the session failed before the agent could work
    return judge(o, w, addr, n, att, work_file, run_dir)


IMPORT_NOTE = ("\nwork.c starts as the verified C of a similar function from an earlier game by the same developer, "
               "with its addresses translated to this game. Keep what still applies and fix the differences.\n")


def judge_import(o, w, addr, imported):
    run_dir = paths.RUNS / addr / "attempt-00"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    work_file = run_dir / "work.c"
    shutil.copyfile(imported, work_file)
    o.log(f"[W{w.id}] {addr}: checking the imported C")
    att = {"n": 0, "model": "import", "started": now(), "duration_s": 0.0, "cost_usd": 0.0, "work_file": str(work_file)}
    return judge(o, w, addr, 0, att, work_file, run_dir)


def judge(o, w, addr, n, att, work_file, run_dir):
    cfg = o.cfg
    w.set_phase(addr, f"official check of attempt {n} (dev + hidden)", n)
    res = check.run_check(cfg, addr, str(work_file), sets=("dev", "hidden"), build_dir=paths.BUILD / addr / "official")
    (run_dir / "check.json").write_text(check.dumps(res), encoding="utf-8")
    verdict = "FAIL" if att.get("tamper") else res["verdict"]
    dev, hid = res["vectors"].get("dev", {}), res["vectors"].get("hidden", {})
    att.update({
        "verdict": verdict, "stage": res["stage"], "score": res["score"], "lint": res["lint"],
        "build": res["build"][:6], "dev_failing": dev.get("failing"), "dev_total": dev.get("total"),
        "hidden_failing": hid.get("failing"), "hidden_total": hid.get("total"),
        "first_diffs": res["first_diffs"], "run_error": res.get("run_error"), "coverage": res.get("coverage"),
    })
    att["summary"] = attempt_summary(att)
    att["feedback"] = check.format_for_agent({**res, "verdict": "FAIL"}) + (
        f"\nHidden set: {hid.get('failing')} of {hid.get('total')} vectors differ." if hid else "")
    o.log(f"[W{w.id}] {addr}: attempt {n} -> {verdict} ({att['summary']})"
          + (f" cost ${att['cost_usd']:.4f}" if att.get("cost_usd") is not None else ""))
    return att


def weak_note(att):
    """Report only: a pass whose vectors never reach some call site (its callee's arguments were never compared)."""
    cov = att.get("coverage") or {}
    total, reached = cov.get("calls_total"), cov.get("calls_reached")
    if not total or reached == total:
        return ""
    untried = cov.get("calls_untried")
    return (f" (weak pass: {total - reached} of {total} call sites never reached: "
            f"{', '.join(cov['calls_unreached'][:4])}" + (f"; {untried} untried by the reach pass: re-prepare"
                                                          if untried else "") + ")")


def finish(o, addr, status, detail):
    r = o.rec(addr)
    r.update({"status": status, "verdict_detail": detail, "finished": now()})
    r["cost_usd"] = round(sum(a.get("cost_usd") or 0 for a in r["attempts"]), 4)
    r["duration_s"] = round(sum(a.get("duration_s") or 0 for a in r["attempts"]), 1)
    if r["attempts"] and Path(r["attempts"][-1]["work_file"]).exists():
        paths.WORK.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r["attempts"][-1]["work_file"], paths.WORK / f"{addr}.c")
    if status == "matched":
        paths.MATCHED.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r["attempts"][-1]["work_file"], paths.MATCHED / f"{addr}.c")
        r["matched_file"] = paths.show(paths.MATCHED / f"{addr}.c")
        cov = r["attempts"][-1].get("coverage") or {}  # flagged, never silent: why each unreached call stays so
        if cov.get("calls_unreached"):
            r["unreached_calls"] = cov.get("calls_reasons") or {a: "" for a in cov["calls_unreached"]}
        else:
            r.pop("unreached_calls", None)
    if status in ("matched", "failed"):
        ghidra.queue_mark(addr, status.upper(), f"{detail}; {len(r['attempts'])} attempt(s), model {r.get('model')}, "
                                                f"{r['finished']}")
    o.save()
    o.log(f"{addr}: {status.upper()} - {detail}")
    if status == "skipped" and detail.startswith("oracle could not produce") and getattr(o, "watcher", None):
        o.watcher.notify(addr)
    return status
