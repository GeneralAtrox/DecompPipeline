"""The official judge for the GameCube PowerPC target: lint -> build -> run on the test vectors -> byte-exact
comparison with the oracle (the PowerPC counterpart of core/check.run_check, installed by hooks.run_check).

The verdict rules are core/check's (_finish): PASS only when every vector of the requested sets matches.
"""
import pathlib
import re
import shutil

from core import check as core_check
from core import paths, strict_judge
from core.lint import lint as core_lint

import ppc_compare as cmp

SOURCES = ("runner.c", "tc_rt.c", "tc_ppc.c")


def lint(src, meta):
    """core/lint's rules (R2-R6), with the float rule worded for this target: f64 is the register type here."""
    out = []
    for e in core_lint(src, meta):
        out.append(e.replace("double precision (use f32 only)", "write f64, the tc_rt.h type, not double"))
    return out


def gen_thunk(meta):
    args = []
    for p in meta["params"]:
        st, kind = p["storage"], p["kind"]
        if re.fullmatch(r"r([3-9]|10)", st):
            e = f"r[{int(st[1:]) - 3}]"
            if kind == "s32":
                e = f"(s32){e}"
            elif kind in ("f32", "f64"):
                raise ValueError(f"float parameter {p['name']} in {st}")
        elif re.fullmatch(r"f[1-8]", st):
            e = f"f[{int(st[1:]) - 1}]"
            if kind == "f32":
                e = f"(f32){e}"
        elif st == "stack":
            a = cmp.SP + p["stack_offset"]
            e = {"f32": f"rdf32(0x{a:08x}u)", "f64": f"rdf64(0x{a:08x}u)", "s32": f"(s32)rd32(0x{a:08x}u)"}.get(
                kind, f"rd32(0x{a:08x}u)")
        else:
            raise ValueError(f"unsupported parameter storage {st}")
        args.append(e)
    call = f"{meta['name']}({', '.join(args)})"
    ret = meta["ret"]
    if ret == "u64":
        body = f"u64 v = {call}; *out_r3 = (u32)(v >> 32); *out_r4 = (u32)v;"
    elif ret in ("f32", "f64"):
        body = f"*out_f1 = (f64){call};"
    elif ret == "void":
        body = f"{call};"
    else:
        body = f"*out_r3 = (u32){call};"
    return f"""#include "tc_rt.h"
{meta['signature']};
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1);
void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1)
{{
    (void)r; (void)f; (void)out_r3; (void)out_r4; (void)out_f1;
    {body}
}}
"""


def build(cfg, meta, work_file, build_dir):
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    shutil.copyfile(work_file, build_dir / "work.c")
    (build_dir / "thunk.c").write_text(gen_thunk(meta), encoding="utf-8")
    env = core_check.msvc_env(cfg)
    env_path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl.exe", path=env_path)
    if not cl:
        raise RuntimeError("cl.exe not found in the Visual Studio environment")
    # Zero-warning build: every warning at /W4 is an error, in work.c and in the runtime alike. /fp:strict: one
    # IEEE operation per C operation, as the PowerPC computes (no contraction into a fused multiply-add).
    cmd = [cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", f"/I{paths.RUNTIME}", f"/Fo{build_dir}\\",
           f"/Fe{build_dir / 'runner.exe'}", str(build_dir / "work.c"), str(build_dir / "thunk.c"),
           *(str(paths.RUNTIME / s) for s in SOURCES)]
    rc, out, reason = paths.run_bounded(cmd, 120, cwd=build_dir, env=env)
    keep = [ln for ln in out.splitlines() if ("error" in ln or "warning" in ln) and ".c" in ln]
    msgs = [re.sub(r"^.*\\work\.c", "work.c", e) for e in keep][:20]
    if rc != 0 or reason:
        return False, msgs or out.splitlines()[-15:]
    return True, msgs


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    harness = pathlib.Path(harness_dir or paths.HARNESS / addr)
    meta = paths.read_json(harness / "meta.json")
    summary = paths.read_json(harness / "oracle_summary.json") or {}
    result = {"addr": addr, "sets": list(sets), "verdict": "FAIL", "stage": "lint", "lint": [], "build": [],
              "vectors": {}, "first_diffs": [], "failing": 0, "total": 0}
    src = pathlib.Path(work_file).read_text(encoding="utf-8", errors="replace")
    result["lint"] = lint(src, meta)
    if result["lint"]:
        return core_check._finish(result, sets, summary)
    result["stage"] = "build"
    build_dir = pathlib.Path(build_dir or (paths.BUILD / addr))
    ok, msgs = build(cfg, meta, work_file, build_dir)
    result["build"] = msgs
    if not ok:
        return core_check._finish(result, sets, summary)
    result["stage"] = "run"
    out = build_dir / "actual.txt"
    which = sets[0] if len(sets) == 1 else "all"
    cmd = [str(build_dir / "runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES),
           str(harness / "vectors.txt"), which, str(out)]
    rc, log, reason = core_check.run_runner(cmd, cfg["check_timeout_s"], out, core_check.output_cap(harness))
    if reason:
        result["run_error"] = f"runner {reason} (an infinite loop in your code?)"
    elif rc != 0:
        result["run_error"] = f"runner exited with code {rc}: {log.strip()[-300:]}"
    vecs = cmp.parse_vectors(harness / "vectors.txt")
    exp = cmp.parse_results(harness / "expected.txt")
    act = cmp.parse_results(out)
    sections = cmp.dol_sections(pathlib.Path(cfg["binary_path"]).read_bytes())
    result["stage"] = "compare"
    strict = strict_judge.modes(cfg)  # report-only call snapshots or exit frame (core/strict_judge)
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            d = cmp.compare_vector(meta, sections, vecs[i], exp[i], act.get(i), strict, report := [])
            strict_judge.note(result, s, i, report)
            if d:
                fails.append(i)
                if s == "dev" and len(result["first_diffs"]) < 3:
                    result["first_diffs"].append({"vector": i, "inputs": cmp.describe_inputs(meta, vecs[i]),
                                                  "diffs": d[:8]})
        result["vectors"][s] = {"total": len(ids), "failing": len(fails), "failing_ids": fails[:50]}
        result["failing"] += len(fails)
        result["total"] += len(ids)
    if not result["first_diffs"] and result["failing"]:
        result["first_diffs"].append({"vector": None, "inputs": "", "diffs": ["(failures are only in the hidden set)"]})
    return core_check._finish(result, sets, summary)
