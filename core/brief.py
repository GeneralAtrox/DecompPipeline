"""The 'Copy agent prompt' brief: everything a stronger agent needs to take over one function."""
from . import ghidra, paths


def _rel(p):
    return paths.show(p) if p else None


def agent_prompt(o, addr):
    addr = paths.norm_addr(addr)
    cfg = o.cfg
    r = o.ledger["functions"].get(addr, {"addr": addr, "status": "queued", "attempts": []})
    f = o.functions.get(addr, {})
    meta = paths.read_json(paths.HARNESS / addr / "meta.json") or {}
    last = r["attempts"][-1] if r.get("attempts") else None
    status = r.get("status")
    model = cfg["model"].capitalize()
    if status == "matched":
        problem = f"Already matched ({r.get('verdict_detail')}). <describe what you want changed>"
    elif last:
        problem = f"{model} failed: {r.get('verdict_detail')}. Last attempt: {last.get('summary')}."
    else:
        problem = "<describe what is wrong>"
    work = _rel(paths.WORK / f"{addr}.c")
    read = [_rel(paths.PACKETS / addr / p.name) for p in ghidra.packet_files(paths.PACKETS / addr)]
    read += [_rel(paths.PACKETS / addr / "meta.json"), _rel(paths.RUNTIME / "tc_rt.h")]
    if last:
        run = paths.RUNS / addr / f"attempt-{last['n']:02d}"
        read += [_rel(last.get("work_file")), _rel(run / "TASK.md"), _rel(run / "check.json")]
    check_cmd = f"python {_rel(paths.TOOL / 'decomp.py')} --project {_rel(paths.ROOT)} check {addr} --work {work}"
    return {
        "format": "decomp_pipeline_v1",
        "task": (f"Make {work} a C translation of the {cfg['cpu']} function at 0x{addr} in {cfg['binary_path'].name} "
                 f"({cfg['name']}) that PASSES the official byte-exact behavioural check (dev + hidden vectors). "
                 f"Rules R1-R11 in the TASK.md below apply unchanged."),
        "problem": problem,
        "function": {
            "address": f"0x{addr}", "signature": meta.get("signature") or r.get("signature"),
            "status": status, "system": o.system_of.get(addr), "tier": f.get("tier"),
            "difficulty_score": f.get("score"), "bytes": f.get("bytes"), "instructions": f.get("insns"),
            "basic_blocks": f.get("blocks"), "cyclomatic": f.get("cyclo"),
            "callees": [c["addr"] for c in meta.get("callees", [])], "globals": meta.get("globals", []),
            "sh4_special": meta.get("sh4_special", []), "oracle": r.get("oracle"),
        },
        "attempts": [{
            "n": a["n"], "model": a.get("model"), "verdict": a.get("verdict"), "result": a.get("summary"),
            "rule_violations": a.get("lint"), "build_errors": a.get("build"),
            "first_differences": a.get("first_diffs"), "agent_final_message": a.get("final_message"),
            "cost_usd": a.get("cost_usd"), "duration_s": a.get("duration_s"), "work_file": _rel(a.get("work_file")),
            "transcript": _rel(paths.RUNS / addr / f"attempt-{a['n']:02d}" / "transcript.jsonl"),
        } for a in r.get("attempts", [])],
        "files": {
            "read": read,
            "edit": [work],
            "query_only": [f"{_rel(paths.GHIDRA_PROJECT / (paths.GHIDRA_NAME + '.gpr'))} (Ghidra project, language "
                           f"{cfg['ghidra_language']})", f"{check_cmd} (official verdict)"],
            "note": (f"Never edit {_rel(paths.HARNESS)}, {_rel(paths.TOOL)} or the project's oracle/ folder. The "
                     f"verdict is only the official check output; copy your result into {_rel(paths.WORK)} and run it."),
        },
    }
