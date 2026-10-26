"""How far each console is from DecompPipeline's definition of done (docs/DEFINITION_OF_DONE.md): five gates per
console, each green, amber or red, with every check's number, its target and its source, and the missing pieces named.

usage: python tools/bounded.py 600 python tools/scorecard.py [--console KEY ...] [--projects-dir DIR]
           [--pending REF ...] [--waste-audit FILE] [--spec FILE] [--json FILE]
  --projects-dir  the folder the spec's roots are relative to (config key projects_dir, else DecompPipeline's parent)
  --pending REF   also look for a source on these branches (not landed): it then counts as partial, never as done
Read-only: it writes nothing but --json. A number with no source is "unmeasured", with the missing source named;
nothing is estimated. Thresholds, stages and roots: tools/scorecard/spec.json. After the gates: the job registry's
heavy jobs per day (tools/jobreg.py): run, reused and refused with the hours saved, cross-checks, jobs started outside
it and the duplicates that got past it (--json: "job_registry").
"""
import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
import scorecard_data as sd  # noqa: E402

TOOL = sd.TOOL
SPEC = TOOL / "tools" / "scorecard" / "spec.json"
GATES = ["1 Full toolset", "2 Disc to working game, hands-off", "3 No waste", "4 Trust", "5 Scale (Too Human)"]


def check(name, status, value=None, target=None, source=None, missing=None):
    return {"name": name, "status": status, "value": value, "target": target, "source": source, "missing": missing}


def unmeasured(name, missing, target=None):
    return check(name, "unmeasured", target=target, missing=missing)


def at_most(name, value, target, source, unit=""):
    """A cost-like number: pass at or under target, partial up to twice it, fail beyond."""
    if value is None:
        return unmeasured(name, source, f"<= {target}{unit}")
    st = "pass" if value <= target else "partial" if value <= 2 * target else "fail"
    return check(name, st, round(value, 4), f"<= {target}{unit}", source)


def at_least(name, value, target, source, unit="%"):
    """A share-like number: pass at or over target, partial from 90% of it, fail below."""
    if value is None:
        return unmeasured(name, source, f">= {target}{unit}")
    st = "pass" if value >= target else "partial" if value >= 0.9 * target else "fail"
    return check(name, st, round(value, 1), f">= {target}{unit}", source)


def gate_status(checks):
    sts = {c["status"] for c in checks} - {"info"}
    return "red" if "fail" in sts else "green" if sts <= {"pass"} else "amber"


def probe(src, item, fmt):
    """("pass"|"partial"|"fail", where or what is missing) for a spec probe ("all"/"any" globs, "json_key")."""
    pats = [p.format(**fmt) for p in item.get("all") or item.get("any") or []]
    wheres = [src.where(p) for p in pats]
    if item.get("json_key"):
        rel, key = item["json_key"][0].format(**fmt), item["json_key"][1]
        text, w = src.read(rel)
        try:
            ok = text is not None and key in json.loads(text)
        except ValueError:
            ok = False
        pats, wheres = pats + [f"{rel} key {key!r}"], wheres + [w if ok else None]
    if item.get("any"):
        hit = [w for w in wheres if w]
        if not hit:
            base = os.path.commonprefix(pats).rpartition("/")[0]
            return "fail", f"none of {base}/{{" + ", ".join(p[len(base) + 1:] for p in pats) + "}"
        return ("pass", "checkout") if "checkout" in hit else ("partial", hit[0] + " (not landed)")
    gone = [p for p, w in zip(pats, wheres) if not w]
    if gone:
        return "fail", "missing " + ", ".join(gone)
    off = sorted({w for w in wheres if w != "checkout"})
    return ("partial", ", ".join(off) + " (not landed)") if off else ("pass", "checkout")


def gate_toolset(src, spec, con, projects_dir):
    fmt = {"target": con["target"], "cpu": con["cpu"]}
    out, got = [], {}
    for st in spec["stages"]:
        status, where = probe(src, st, fmt)
        ext = con.get(st.get("external") or "")
        if status == "fail" and ext and (pathlib.Path(projects_dir) / ext).is_dir():
            head = sd.head_of(pathlib.Path(projects_dir) / ext)
            where = f"own code not yet imported into DP: {ext}" + (f" @ {head}" if head else "") + " (not a DP stage)"
            status = "partial"
        if st.get("requires") and got.get(st["requires"]) == "fail" and status != "fail":
            status, where = "fail", f"needs the {st['requires']} stage first ({where})"
        got[st["id"]] = status
        out.append(check(st["name"], status, source=where if status != "fail" else None,
                         missing=where if status != "pass" else None))
    return out


def by_game(wss):
    games = {}
    for w in wss:
        g = games.setdefault(w["game_root"], {"root": w["game_root"], "workspaces": []})
        g["workspaces"].append(w)
    for g in games.values():
        ws = g["workspaces"]
        g["name"] = pathlib.Path(g["root"]).name
        bins = {}  # pilots of one binary (Grandia's ws-1pct, ws-1pct8) count each function once
        for w in ws:
            b = bins.setdefault(w["binary"], [set(), set()])
            b[0] |= w["_scope"]
            b[1] |= w["_matched"]
        g["scope"] = sum(len(s) for s, _ in bins.values())
        g["matched"] = sum(len(s & m) for s, m in bins.values())
        rds = [w["readable"] for w in ws if w["readable"]]
        g["verified"] = sum(r["verified"] for r in rds) if rds else None
        g["port"] = any(w["port"] for w in ws)
        g["scenarios"] = any(w["scenarios"] for w in ws)
        g["audits"] = [w["final_audit"] for w in ws if w["final_audit"]]
        g["divergence"] = [w["divergence"] for w in ws if w["divergence"]]
        port_log = next((w["port_oplog"] for w in ws if w.get("port_oplog")), None)  # one game folder
        g["oplogs"] = [w["oplog"] for w in ws if w.get("oplog")] + ([port_log] if port_log else [])  # no record: absent
        g["oplog_whole"] = [w.get("oplog") or [] for w in ws] + ([port_log or []] if g["port"] else [])
        g["pct"] = 100.0 * g["matched"] / g["scope"] if g["scope"] else 0.0
        g["scope_sources"] = sorted({w["scope_source"] for w in ws})
        g["scope_measured"] = all(s.startswith("core.audit") for s in g["scope_sources"])
    return sorted(games.values(), key=lambda g: (g["pct"], g["matched"]), reverse=True)


def div_check(name, divs, scenarios):
    if not divs:
        why = ("port/divergence_scenarios.json exists but no state/divergence.json result" if scenarios
               else "no state/divergence.json (needs a port and the divergence finder)")
        return unmeasured(name, why, "0 diverged, 0 errors, >= 1 clean")
    c = {k: sum((d["counts"].get(k) or 0) for d in divs) for k in ("clean", "diverged", "float-close", "error")}
    bad = c["diverged"] + c["error"] + c["float-close"]
    st = "pass" if bad == 0 and c["clean"] > 0 else "fail"
    return check(name, st, f"{c['clean']} clean, {c['diverged']} diverged, {c['float-close']} float-close, "
                 f"{c['error']} errors", "0 not clean", "; ".join(f"{d['file']} ({d['when']})" for d in divs))


def gate_handsoff(games, th):
    if not games:
        return [check("reference game", "fail", missing="no DP workspace for this console")]
    g = games[0]
    raw = at_least("raw verified (matched / functions in scope)", g["pct"], th["verified_pct"],
                   f"state/ledger.json: {g['matched']}/{g['scope']}; scope: {', '.join(g['scope_sources'])}")
    if not g["scope_measured"] and raw["status"] == "pass":  # ledger rows are not the game's functions: never green
        raw.update(status="partial", missing="functions in scope not from core.audit's game ranges")
    out = [check("reference game", "info", g["name"], source=f"{len(g['workspaces'])} workspace(s) under {g['root']}"),
           raw]
    if g["verified"] is None:
        out.append(unmeasured("readable verified", "no state/readable.json (readable stage not run)",
                              f">= {th['readable_pct']}%"))
    else:
        out.append(at_least("readable verified (of matched)", 100.0 * g["verified"] / max(g["matched"], 1),
                            th["readable_pct"], f"state/readable.json: {g['verified']}/{g['matched']}"))
    if g["audits"]:
        verdicts = [a["verdict"] for a in g["audits"]]
        st = "pass" if all(v == "PASS" for v in verdicts) else "fail" if "FAIL" in verdicts else "partial"
        out.append(check("final audit", st, ", ".join(sorted(set(verdicts))), "PASS",
                         "; ".join(a["file"] for a in g["audits"])))
    else:
        out.append(unmeasured("final audit", "no state/final_audit-*.txt (tools/final_audit.py not run)", "PASS"))
    out.append(unmeasured("port builds", "port/game.json exists, but no build result is recorded", "builds")
               if g["port"] else check("port builds", "fail", missing="no port/game.json for the game"))
    out.append(div_check("zero divergence", g["divergence"], g["scenarios"]))
    n = sd.operator_steps(g["oplogs"]) if g["oplogs"] else None
    whole = n is not None and sd.whole_run(g["oplog_whole"])
    if n is None:
        out.append(unmeasured("manual operator steps", "no record in a state/operator_steps.jsonl of the game's "
                              "workspaces or port folder (core/oplog: no DP driver has run there since it records)", "0"))
    elif n["steps"] == 0 and not whole:  # no step, but no run that went the whole way either: no evidence of one
        out.append(unmeasured("manual operator steps", f"{n['start']} hand start(s), nothing else, but no run id has "
                              f"records in every workspace of the game and its port folder: no end-to-end run "
                              f"recorded", "0"))
    else:
        out.append(at_most("manual operator steps (since the log began)", n["steps"], 0,
                           f"{len(g['oplogs'])} state/operator_steps.jsonl since {n['since']}: {n['start']} hand "
                           f"start(s), the first free; {n['stop']} stop(s); {n['act']} act(s)"))
    return out


def rates(wss, games):
    """The console's measured per-function figures: all its workspaces' agent spend over the distinct functions
    matched (a function two pilots matched counts once)."""
    a = {k: sum(w["agent"][k] for w in wss) for k in ("attempts", "seconds", "timed", "usd", "costed", "tokens",
                                                       "tokened")}
    matched = sum(g["matched"] for g in games)
    rd = [w["readable"] for w in wss if w["readable"]]
    rdn = sum(r["readable"] for r in rd)
    r = {"matched": matched, "readable": rdn, "prepare_n": sum(len(w["prepare_s"]) for w in wss),
         "prepare_median_s": sd.median([x for w in wss for x in w["prepare_s"]])}
    r["raw_usd"] = a["usd"] / matched if matched and a["costed"] else None
    r["raw_tokens"] = a["tokens"] / matched if matched and a["tokened"] else None
    r["raw_s"] = a["seconds"] / matched if matched and a["timed"] else None
    r["rd_usd"] = sum(x["agent"]["usd"] for x in rd) / rdn if rdn and any(x["agent"]["costed"] for x in rd) else None
    r["rd_tokens"] = (sum(x["agent"]["tokens"] for x in rd) / rdn if rdn and any(x["agent"]["tokened"] for x in rd)
                      else None)
    rt = {k: sum(x["time"][k] for x in rd if x.get("time")) for k in ("rows", "readable", "seconds")}
    r["rd_s"], r["rd_timed"] = (rt["seconds"] / rt["readable"] if rt["readable"] else None), rt
    bt = [w["busy"] for w in wss if w.get("busy")]
    alive = sum(b["alive_s"] for b in bt)
    r["busy"] = {"alive_s": alive, "busy_s": sum(b["busy_s"] for b in bt), "lives": sum(b["lives"] for b in bt),
                 "open": sum(b.get("open", 0) for b in bt),
                 "by": {c: sum(b["by"].get(c, 0.0) for b in bt) for c in sd.BUSY}} if bt else None
    return r


def add(*xs):
    return None if any(x is None for x in xs) else sum(xs)


def gate_waste(r, th, audit):
    out = []
    if audit is None:
        out.append(unmeasured("waste audit open findings", "no docs/WASTE_AUDIT_*.md in the checkout, the pending "
                              "branches or any worktree", "0"))
    else:
        name, path, where, text = audit
        parsed = sd.parse_waste_audit(text)
        n = len(parsed["open"])
        st = ("pass" if n == 0 else "fail") if where == "checkout" else "partial" if n == 0 else "fail"
        out.append(check("waste audit open findings", st, n, "0", f"{path} [{where}]",
                         ", ".join(f"#{x[0]} ({x[2][:20]})" for x in parsed["open"]) or None))
    src = f"ledger + readable.json over {r['matched']} matched / {r['readable']} readable"
    out.append(at_most("agent tokens per finished function", add(r["raw_tokens"], r["rd_tokens"]),
                       th["agent_tokens_per_fn"], src if r["rd_tokens"] is not None else
                       ("raw stage only; readable unmeasured" if r["raw_tokens"] is not None else "no ledger")))
    out.append(at_most("agent cost per finished function", add(r["raw_usd"], r["rd_usd"]), th["agent_usd_per_fn"],
                       src if r["rd_usd"] is not None else "no readable.json for a total", " USD"))
    out.append(at_most("agent seconds per matched function (raw)", r["raw_s"], th["agent_s_per_fn"],
                       f"ledger attempts' duration_s over {r['matched']} matched" if r["raw_s"] is not None
                       else "no ledger attempt durations", " s"))
    out.append(at_most("prepare median per function", r["prepare_median_s"], th["prepare_median_s"],
                       f"state/prepare_timing.jsonl ({r['prepare_n']} prepares)" if r["prepare_n"]
                       else "no state/prepare_timing.jsonl", " s"))
    b = r["busy"]
    if b is None:
        out.append(unmeasured("idle share", "no state/worker_busy/ log (core/busylog: no worker has run since it "
                              "records)", f"<= {th['idle_share_pct']}%"))
    elif not b["alive_s"]:
        out.append(unmeasured("idle share", f"state/worker_busy: {b['open']} worker run(s), none with its end "
                              "recorded (a killed process loses the idle time after its last event)",
                              f"<= {th['idle_share_pct']}%"))
    else:
        out.append(at_most("idle share (worker time not on real work)", 100.0 * (1 - b["busy_s"] / b["alive_s"]),
                           th["idle_share_pct"], f"state/worker_busy: {b['lives']} worker run(s) that ended "
                           f"({b['open']} left open by a killed process left out), "
                           f"{b['alive_s'] / 3600:.2f} worker-hours; " + ", ".join(
                               f"{name} {100.0 * b['by'][c] / b['alive_s']:.1f}%" for c, name in sd.BUSY.items()),
                           "%"))
    return out


def gate_trust(src, spec, con, key, games, mut):
    fmt = {"target": con["target"], "cpu": con["cpu"]}
    out = []
    for item in spec["prewave_items"]:
        status, where = probe(src, item, fmt)
        out.append(check(f"pre-wave {item['name']}", status, source=where if status != "fail" else None,
                         missing=where if status != "pass" else None))
    th = spec["thresholds"]["mutation_kill_pct"]
    if mut is None:
        out.append(unmeasured("mutation-audit kill rate", "tools/mutation_audit/baseline.json not found", f">= {th}%"))
    else:
        text, where = mut
        keys = {k: v for k, v in sd.mutation_rates(text).items() if spec["mutation_keys"].get(k) == key}
        if not keys:
            out.append(unmeasured("mutation-audit kill rate", "no sampled workspace of this console in "
                                  "tools/mutation_audit/sample.json", f">= {th}%"))
        else:
            low = min(keys, key=lambda k: keys[k]["rate"] if keys[k]["rate"] is not None else -1)
            c = at_least("mutation-audit kill rate (lowest workspace)", keys[low]["rate"], th,
                         f"baseline.json [{where}]: " + ", ".join(f"{k} {v['rate']}% ({v['killed']}/{v['mutants']})"
                                                                  for k, v in sorted(keys.items())))
            if where != "checkout" and c["status"] == "pass":
                c["status"], c["missing"] = "partial", f"baseline on {where}, not landed"
            out.append(c)
    out.append(div_check("zero divergence (all ports)", [d for g in games for d in g["divergence"]],
                         any(g["scenarios"] for g in games)))
    return out


def projection(n, r, par):
    """(USD, raw-stage hours) for n functions at rates r, or None where a rate is missing."""
    usd = add(r["raw_usd"], r["rd_usd"])
    hours = (None if r["raw_s"] is None or r["prepare_median_s"] is None else
             max(n * r["raw_s"] / par["workers"], n * r["prepare_median_s"] / par["slots"]) / 3600)
    return (None if usd is None else n * usd), hours


def gate_scale(spec, r, n_src, par, ref=None):
    b = spec["baseline"]
    n, nsrc = n_src
    out = [check("baseline size", "pass", n, source=nsrc) if n else unmeasured("baseline size", nsrc)]
    usd, h = projection(n or 0, r, par)
    if n is None or usd is None:
        out.append(unmeasured("projected agent cost", "no function count" if n is None else
                              "no measured raw + readable cost on this console", f"<= {b['budget_usd']} USD"))
    else:
        out.append(at_most("projected agent cost (agent on every function)", usd, b["budget_usd"],
                           f"{n} x {usd / n:.4f} USD measured", " USD"))
    if n is None or h is None:
        out.append(unmeasured("projected machine hours", "no function count" if n is None else
                              "no measured agent seconds and prepare times on this console",
                              f"<= {b['budget_hours']} h"))
    else:
        out.append(at_most("projected machine hours (raw stage)", h, b["budget_hours"],
                           f"max(agent {r['raw_s']:.1f} s / {par['workers']} workers, prepare "
                           f"{r['prepare_median_s']:.2f} s / {par['slots']} slots) x {n}; config.json max_workers, "
                           "ghidra_parallel", " h"))
    rt = r["rd_timed"]
    if n is None or h is None or r["rd_s"] is None:
        out.append(unmeasured("projected machine hours (raw + readable)", "no function count" if n is None else
                              "no measured raw-stage hours" if h is None else
                              f"no readable function whose agent attempts all carry duration_s ({rt['rows']} timed "
                              "row(s))", f"<= {b['budget_hours']} h"))
    else:
        h_rd = n * r["rd_s"] / par["workers"] / 3600
        out.append(at_most("projected machine hours (raw + readable)", h + h_rd, b["budget_hours"],
                           f"raw {h:,.1f} h + readable {r['rd_s']:.1f} s / {par['workers']} workers x {n} = "
                           f"{h_rd:,.1f} h; readable.json: {rt['seconds']:.0f} s over {rt['readable']} readable of "
                           f"{rt['rows']} rows timed in full (agent-run rows only: an upper bound)", " h"))
    if n and ref and (r["raw_usd"] is None or r["raw_s"] is None):
        usd, h = projection(n, ref[1], par)
        out.append(check(f"for scale only: at {ref[0]}'s measured rates", "info",
                         f"{usd:,.0f} USD, {h:,.1f} h raw stage" if usd is not None and h is not None else None,
                         source="not this console's own rates; does not count toward the gate"))
    return out


def scorecard(spec, src, projects_dir, keys=None, waste=None, scope=True):
    from core import toolcfg
    cfg = toolcfg.load(TOOL)
    par = {"workers": cfg.get("max_workers") or 1, "slots": cfg.get("ghidra_parallel") or 1}
    audits = sd.waste_audits(src, waste)
    mut = src.read("tools/mutation_audit/baseline.json")
    mut = mut if mut[0] is not None else None
    n_src = sd.baseline_functions(spec, projects_dir)
    out, done = {}, []
    for key, con in spec["consoles"].items():
        if keys and key not in keys:
            continue
        wss, notes = [], []
        for root in con["roots"]:
            found, cut = sd.discover(pathlib.Path(projects_dir) / root)
            if cut:
                notes.append(f"discovery stopped after {sd.MAX_DIRS} folders under {root}")
            wss += [sd.workspace_metrics(w, scope) for w in found]
        wss = [w for w in wss if w["target"] == con["target"]]
        games = by_game(wss)
        r = rates(wss, games)
        done.append((con["name"], r))
        ref = max(done, key=lambda x: x[1]["matched"])
        gates = [gate_toolset(src, spec, con, projects_dir), gate_handsoff(games, spec["thresholds"]),
                 gate_waste(r, spec["thresholds"], audits[0] if audits else None),
                 gate_trust(src, spec, con, key, games, mut),
                 gate_scale(spec, r, n_src, par, ref if ref[1]["matched"] else None)]
        out[key] = {"name": con["name"], "target": con["target"], "workspaces": len(wss), "notes": notes,
                    "games": [{k: g[k] for k in ("name", "root", "scope", "matched", "verified", "pct")}
                              for g in games],
                    "gates": [{"gate": GATES[i], "status": gate_status(c), "checks": c} for i, c in enumerate(gates)]}
    return out


def render(card):
    lines = []
    for con in card.values():
        lines += ["", f"== {con['name']} ({con['target']}): {con['workspaces']} workspace(s)"]
        for g in con["games"]:
            lines.append(f"   game {g['name']}: {g['matched']}/{g['scope']} matched ({g['pct']:.1f}%)"
                         + (f", {g['verified']} readable verified" if g["verified"] is not None else ""))
        lines += [f"   note: {n}" for n in con["notes"]]
        for g in con["gates"]:
            miss = [c["name"] for c in g["checks"] if c["status"] not in ("pass", "info")]
            lines.append(f"  {g['status'].upper():6} {g['gate']}" + (f"  (open: {', '.join(miss)})" if miss else ""))
            for c in g["checks"]:
                val = "" if c["value"] is None else f" = {c['value']}"
                tgt = f" (target {c['target']})" if c["target"] else ""
                lines.append(f"         {c['status']:10} {c['name']}{val}{tgt}")
                if c["source"] and c["status"] != "unmeasured" and c["source"] != c["missing"]:
                    lines.append(f"                    source: {c['source']}")
                if c["missing"]:
                    lines.append(f"                    {'missing source' if c['status'] == 'unmeasured' else 'open'}: "
                                 f"{c['missing']}")
    return "\n".join(lines)


def render_jobs(days, folder):
    """The job registry's per-day waste (tools/jobreg.py; CLAUDE.md "No redundant work")."""
    lines = ["", f"== Job registry {folder}: heavy jobs per day (CLAUDE.md 'No redundant work')"]
    if not days:
        return "\n".join(lines + ["   no records (no heavy job has gone through tools/jobreg.py or its hook yet)"])
    lines.append("   day          run  hours  reused  saved h  refused  saved h  cross-checks  outside  duplicates")
    for day, d in days.items():
        lines.append(f"   {day:10} {d['run']:5} {d['run_h']:6.2f} {d['reused']:7} {d['saved_h']:8.2f} "
                     f"{d['refused']:8} {d['refused_h']:8.2f} {d['cross_checks']:13} {d['outside']:8} "
                     f"{d['duplicates']:11}")
    lines.append("   reused/refused saved h: the run time of the earlier job each pointed to; outside: heavy jobs the "
                 "hook saw start outside the wrapper; duplicates: past the guard (a command started again)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--console", nargs="*")
    ap.add_argument("--projects-dir")
    ap.add_argument("--pending", nargs="*", default=[])
    ap.add_argument("--waste-audit")
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    from core import toolcfg
    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    pdir = pathlib.Path(a.projects_dir or toolcfg.load(TOOL).get("projects_dir") or TOOL.parent)
    card = scorecard(spec, sd.Sources(TOOL, a.pending), pdir, a.console, a.waste_audit)
    print(f"DecompPipeline scorecard (docs/DEFINITION_OF_DONE.md); projects {pdir}; pending refs: "
          f"{', '.join(a.pending) or 'none'}")
    print(render(card))
    import jobreg
    _, folder = jobreg.settings()
    days = sd.job_days(jobreg.records(folder))
    print(render_jobs(days, folder))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(card, job_registry=days), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
