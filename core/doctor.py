"""Workspace consistency: artefacts agree with the catalog and the ledgers. Read-only, except one safe repair: a
stale function claim (state/fnlocks/ADDR.owner whose lock no process holds: its refine run, verify or sync died;
core/fn_claim) is reported and its record removed while the doctor holds that lock. Claims held now are listed.

Fails on:
- any orphan artefact: anything keyed by an address that is not a catalog function (core/retire.py scan: harness,
  packets, matched C, readable files, ledger rows, readable ledger rows, systems.json members, names...);
- a matched function (main ledger) without its harness meta or its matched C;
- a references / priority / broken_control address in project.json that is not a function;
- a function to decompile (catalog, inside the game ranges or listed in project.json "extra_functions") in no
  system, once state/systems.json exists (tools/assign_systems.py places them);
- an "extra_functions" address that is not a catalog function (core/scope.py);
- a readable file (of a current function) that uses a raw name the names registry has named: fn_ADDR of a named
  function, g_/DAT_ADDR of a named global, in code or comments (core/raw_names; refine.py verify syncs them).
  The raw names left for functions without a name are counted in a note;
- a matched function's harness whose callee record has another signature than the callee's own harness (Ghidra's
  stale view of the callee, or the callee re-prepared since: core/callee_drift; refine.preflight blocks it):
  re-prepare it (a prepare takes the records from the callees' harnesses). Other functions' harnesses
  with such records are counted in a note.
Reports, without failing: the proof gates' figure (core/proof.py: verified, matched_low_coverage, weak harnesses),
readable files whose readable.json status does not describe them (a file with no row or
with a status other than readable/stale/harness_stale/running, a "readable" row without a file), and the readable
denominator: readable files of current functions next to the matched functions they rewrite. Also, as the claims
are, what holds runs up (run_notes): the machine's Ghidra HOLD (and whether its writer still runs) and wave STOP file
(core/wave_ctl), readable rows in harness_stale or infra_error, and a `wave.py run --game` state that ended stuck.
"""
import collections
import json
import pathlib
import re
import time

from . import callee_drift, filelock, locations, proof, raw_names, retire, scope, sysassign

# a readable/src file exists for these: every status a readable PASS records (core/proof.readable_state), and these
FILE_STATUSES = proof.PASSED + ("stale", "harness_stale", "running")
TOOL = pathlib.Path(__file__).resolve().parents[1]


def run_notes(ws, pj, rd, funcs):
    """Read-only notes on what holds the workspace's runs up -> (notes, facts). The HOLD and STOP are the machine's:
    the HOLD's folder is the broker's (project.json "ghidra_broker" over the tool's config, core/ghidra_broker)."""
    from . import ghidra_broker, infra, toolcfg, wave_ctl
    notes, when = [], lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    d = ghidra_broker.settings({"ghidra_broker": pj.get("ghidra_broker") or toolcfg.load(TOOL).get("ghidra_broker")})[1]
    hold = wave_ctl.hold_info(d)
    if hold is not None:
        who = (f"pid {hold['pid']}, " + ("running" if hold["alive"] else "not running: a stopped wave keeps it to "
                                         "resume (wave.py run)") if "alive" in hold else "no pid recorded")
        since = f" since {when(hold['since'])}" if isinstance(hold.get("since"), (int, float)) else ""
        notes.append(f"a Ghidra HOLD is in {d} (by {hold.get('by', 'no writer recorded')}{since}; {who}): every Ghidra "
                     "run on this machine without its pass waits until `python tools/wave.py release` removes it")
    stop = wave_ctl.stop_mark(wave_ctl.STOP_FILE)
    if stop is not None:
        notes.append(f"a wave STOP file is in {wave_ctl.STOP_FILE} (written {when(stop[0] / 1e9)}): a wave running "
                     "since before then stops; one started after it ignores it")
    stale = sorted(a for a, r in rd.items() if a in funcs and (r or {}).get("status") == "harness_stale")
    if stale:
        notes.append(f"{len(stale)} readable row(s) harness_stale: the matched raw C fails its harness (re-prepare: "
                     f"python tools/prepare_list.py --project {ws} --harness-stale): " + " ".join(stale[:16]))
    broke = sorted(a for a in infra.rows(rd) if a in funcs)
    if broke:
        notes.append(f"{len(broke)} readable row(s) record an infrastructure error, not a verdict (refine.py status "
                     "lists them; a plain refine.py run tries them again): " + " ".join(broke[:16]))
    wg, stuck = ws / "state" / "wave_game.json", None
    if wg.exists():
        try:
            stuck = (json.loads(filelock.read_text(wg)) or {}).get("stuck")
        except (OSError, ValueError, AttributeError) as e:
            stuck = "unreadable state"
            notes.append(f"{wg} is unreadable ({type(e).__name__}): `wave.py run --game` refuses to start until it is "
                         "restored, fixed or removed")
        else:
            if stuck:
                notes.append(f"`wave.py run --game` ended stuck: {stuck} ({wg}; its next start tries that step again)")
    return notes, {"hold": hold is not None, "hold_alive": (hold or {}).get("alive"), "wave_stop": stop is not None,
                   "readable_harness_stale": len(stale), "readable_infra_error": len(broke), "wave_game_stuck": stuck}


def check(ws):
    """(ok, lines, facts)."""
    ws = pathlib.Path(ws)
    funcs = retire.functions(ws)
    plan = retire.scan(ws, funcs)
    led = (retire._read(ws, retire.LEDGER) or {}).get("functions") or {}
    rd = retire._read(ws, retire.READABLE) or {}
    pj = retire._read(ws, "project.json", {}) or {}
    fails, notes, detail = [], [], []

    if plan["items"]:
        addrs = sorted({i["addr"] for i in plan["items"]})
        fails.append(f"{len(plan['items'])} orphan artefact(s) of {len(addrs)} address(es) that are not functions "
                     f"(retire them: python tools/retire.py --project {ws} --dry, then without --dry):")
        detail = retire.summary({"items": plan["items"], "names_kept": []})
    notes += [x.strip() for x in retire.summary({"items": [], "names_kept": plan["names_kept"]})]

    matched = sorted(a for a, r in led.items() if r.get("status") == "matched" and a in funcs)
    no_c = [a for a in matched if not (ws / "matched" / f"{a}.c").exists()]
    no_meta = [a for a in matched if not (ws / "harness" / a / "meta.json").exists()]
    if no_c:
        fails.append(f"{len(no_c)} matched function(s) without matched/ADDR.c: {' '.join(no_c[:20])}")
    if no_meta:
        fails.append(f"{len(no_meta)} matched function(s) without harness/ADDR/meta.json: {' '.join(no_meta[:20])}")

    cfg_addrs = list(pj.get("references") or []) + list(pj.get("priority") or [])
    cfg_addrs += [x for x in (pj.get("broken_control") or [])[:1]]
    bad_cfg = sorted({a for a in cfg_addrs if re.fullmatch(r"[0-9a-f]{8}", str(a)) and a not in funcs})
    if bad_cfg:
        fails.append(f"project.json references/priority/broken_control not functions: {' '.join(bad_cfg)}")

    no_extra = scope.missing_extras(ws)
    if no_extra:
        fails.append(f"{len(no_extra)} project.json extra_functions address(es) not functions of the catalog: "
                     + " ".join(no_extra[:20]))
    loose, has_systems = sysassign.unassigned(ws)
    if loose:
        n_extra = len(set(loose) & scope.of(ws).extras)
        fails.append(f"{len(loose)} game function(s) in no system"
                     + (f" ({n_extra} of them extra functions)" if n_extra else "")
                     + f" (assign them: python tools/assign_systems.py --project {ws} --dry, then without --dry): "
                     + " ".join(loose[:12]))
    elif not has_systems:
        notes.append("no state/systems.json yet (decomp.py systems builds it)")

    drifted = callee_drift.scan(ws / "harness", set(funcs))
    drift_matched = {a: d for a, d in drifted.items() if a in set(matched)}
    if drift_matched:
        callees = collections.Counter(c for d in drift_matched.values() for c, _, _ in d)
        fails.append(f"{len(drift_matched)} matched function harness(es) with callee records that differ from the "
                     "callees' own harness signatures (servers stopped: a prepare takes the records from the callees' "
                     "harnesses; tools/reprepare_stale.py re-prepares the callers until nothing changes; by hand: "
                     "decomp.py prepare ADDR): callers " + " ".join(sorted(drift_matched)) + "; callees "
                     + " ".join(f"{c} (x{n})" for c, n in callees.most_common()) + ". "
                     + callee_drift.describe(drift_matched, 4))
    if len(drifted) > len(drift_matched):
        notes.append(f"{len(drifted) - len(drift_matched)} harness(es) of functions not matched have stale callee "
                     "records (re-prepared when the raw stage runs them again)")
    src = ws / "readable" / "src"
    files = sorted(p.stem for p in src.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem)) if src.is_dir() else []
    cur = [a for a in files if a in funcs]
    st = collections.Counter((rd.get(a) or {}).get("status", "no row") for a in cur)
    mismatched = [a for a in cur if (rd.get(a) or {}).get("status") not in FILE_STATUSES]
    missing = sorted(a for a, r in rd.items() if a in funcs and (r or {}).get("status") == "readable"
                     and a not in set(files))
    redo_failed = sorted(a for a, r in rd.items() if a in funcs and (r or {}).get("redo_failed"))
    raw, raw_counts = raw_names.scan(ws, set(cur))
    if raw:  # the goal's audit: no readable file uses a raw name the registry has named
        fails.append(f"{len(raw)} readable file(s) use raw names of named functions or globals (refine.py verify "
                     "syncs them; one it cannot rename stays in its sync_refused note): " + "; ".join(
                         f"{a}: {' '.join(f'{r} ({n})' for r, n in sorted(m.items()))}"
                         for a, m in sorted(raw.items())[:12]))
    notes.append(raw_names.unnamed_line(raw_counts))
    from . import call_coverage  # the figure the final audit's calls check judges (tools/call_coverage.py)
    cov = call_coverage.figure(call_coverage.site_rows(sorted(matched), ws / "harness"))
    notes.append(call_coverage.summary_line(cov))
    pcfg = proof.project_cfg(ws)  # the proof gates' figure (tools/coverage_gate.py, tools/final_audit.py "proof")
    prf = proof.figure(proof.scan(ws, matched, pcfg))
    notes.append(proof.summary_line(prf, proof.coverage_settings(pcfg)))
    if mismatched:
        notes.append(f"{len(mismatched)} readable file(s) whose readable.json status does not describe them "
                     f"(re-judge them: refine.py verify @FILE): " + " ".join(
                         f"{a}:{(rd.get(a) or {}).get('status', 'no row')}" for a in mismatched[:16]))
    if missing:
        notes.append(f"{len(missing)} readable.json row(s) say readable without a file: {' '.join(missing[:16])}")
    if redo_failed:
        notes.append(f"{len(redo_failed)} readable function(s) keep their file after a failed redo (redo_failed)")
    stale_claims, held = filelock.stale_claims(ws / "state" / "fnlocks", clear=True)
    if stale_claims:
        notes.append(f"{len(stale_claims)} stale function claim(s) cleared (the owner process is gone, the operating "
                     "system had released its lock): " + "; ".join(
                         f"{a}: {filelock.describe(r)}" + (" (that pid is reused)" if r.get("alive") else "")
                         for a, r in sorted(stale_claims.items())[:12]))
    if held:
        notes.append(f"{len(held)} function(s) claimed now by a running refine run, verify, sync-names or check: "
                     + "; ".join(f"{a}: {filelock.describe(r)}" for a, r in sorted(held.items())[:12]))
    run, run_facts = run_notes(ws, pj, rd, set(funcs))
    notes += run
    readable_pass = st["readable"]
    facts = {"orphans": len(plan["items"]), "orphan_addrs": len({i["addr"] for i in plan["items"]}),
             "readable_files": len(files), "readable_files_functions": len(cur), "readable_status": dict(st),
             "matched": len(matched), "catalog_functions": len(funcs), "status_mismatch": len(mismatched),
             "redo_failed": len(redo_failed), "unassigned": len(loose),
             "extras_not_functions": len(no_extra), "raw_named_files": len(raw),
             "raw_unnamed_uses": raw_counts[0], "stale_callee_harnesses": len(drift_matched),
             "stale_callee_harnesses_unmatched": len(drifted) - len(drift_matched),
             "stale_claims_cleared": len(stale_claims), "claimed": len(held), "call_coverage": cov, "proof": prf,
             **run_facts}
    others = "".join(f", {n} {k}" for k, n in sorted(st.items()) if k != "readable")
    notes.insert(0, f"readable: {len(files)} file(s) in readable/src, {len(cur)} of current functions "
                    f"({len(files) - len(cur)} not functions); of those {readable_pass} readable{others}; "
                    f"denominator: {len(matched)} matched functions")
    if pj.get("target", "dreamcast_sh4") in ("dreamcast_sh4", "saturn_sh2"):  # the listing reader is SuperH's
        from . import ret_reg, ret_use
        missed = ret_use.Project(ws).unlisted(ws / "state")
        if missed:
            fails.append(f"{len(missed)} function(s) whose callers read a result register the judge does not compare "
                         f"(run tools/float_gaps.py --project {ws} --write): "
                         + " ".join(f"{a}:{'+'.join(r)}" for a, r in missed[:20]))
        stale = {g: cs for g, cs in ret_use.Project(ws).fret_callers(ws / "state").items()
                 if set(cs) - set(ret_reg.fret_callers(ws / "state").get(g, ()))} if not missed else None
        if stale:
            fails.append(f"{len(stale)} caller(s) whose oracle would not draw float results for a callee they read in fr0 "
                         f"(run tools/float_gaps.py --project {ws} --write, then re-prepare them): "
                         + " ".join(sorted(stale)[:20]))
    if not locations.file_for(ws).exists():
        notes.append(f"no {locations.file_for(ws)}: where this game's files and emulators are (AGENTS.md; "
                     f"tools/game_setup.py locations writes the skeleton)")
    lines = [("FAIL " if fails else "OK ") + f"consistency: {len(fails)} problem group(s)"]
    if fails and detail:  # the orphan table under its heading (the first problem)
        fails = fails[:1] + detail + fails[1:]
    lines += [f"  {x}" if not x.startswith("  ") else x for x in fails] + [f"  note: {x}" for x in notes]
    return not fails, lines, facts
