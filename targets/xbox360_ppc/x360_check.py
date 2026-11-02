"""The official judge for the Xbox 360 target: lint -> build -> run on the test vectors -> byte-exact comparison
with the oracle (the counterpart of targets/gamecube_ppc/ppc_check.py, installed by hooks.run_check).

The verdict rules are core/check's (_finish): PASS only when every vector of the requested sets matches. The
judge facts that differ from the GameCube's: r3 is compared in all 64 bits, integer arguments are 64-bit, f1..f13
carry float arguments, and the runtime's multiply-add rounding follows target.json "fma" (DESIGN.md).
"""
import pathlib
import re
import shutil

from core import check as core_check
from core import paths
from core.lint import lint as core_lint

import x360_compare as cmp

SOURCES = ("runner.c", "tc_rt.c", "tc_ppc.c")
INT_KINDS = {"u8": "(u8)", "u16": "(u16)", "u32": "(u32)", "s8": "(s8)(u8)", "s16": "(s16)(u16)",
             "s32": "(s32)(u32)", "u64": "", "s64": "(s64)"}
# How each declared return type fills the 64-bit r3.
RET_R3 = {"u8": "(u64)", "u16": "(u64)", "u32": "(u64)", "s8": "(u64)(s64)", "s16": "(u64)(s64)",
          "s32": "(u64)(s64)", "u64": "(u64)", "s64": "(u64)"}
FMA_MODES = {"two_roundings": 0, "fused": 1}


MAX_C = 2_000_000  # work.c cap here; core/lint's 60 KB is an agent-readability limit, and 38 Too Human functions
#                    (2,000 to 8,000 instructions) have translator C past it even in the short form


def lint(src, meta):
    """core/lint's rules (R2-R6), with the float rule worded for this target: f64 is the register type here, and the
    size cap is MAX_C (core/lint's 60 KB cap is waived up to it)."""
    out = []
    for e in core_lint(src, meta):
        if e == "work.c is larger than 60 KB":
            if len(src) > MAX_C:
                out.append(f"work.c is larger than {MAX_C // 1000} KB")
            continue
        out.append(e.replace("double precision (use f32 only)", "write f64, the tc_rt.h type, not double"))
    return out


# A double the declared prototype passes in an integer register (Ghidra's guess for a parameter past f13): the
# register holds its 64 bits, so the C parameter is those bits as a double, as the translator's entry rebuilds r3..r10.
BITS_F64 = "static f64 tc_bits_f64(u64 x)\n{\n    union { f64 d; u64 b; } v;\n    v.b = x;\n    return v.d;\n}\n"


def gen_thunk(meta, stack_base=None):
    """stack_base: a C expression for the caller's stack pointer (the port kit); None is the judge's fixed one."""
    args = []
    for p in meta["params"]:
        st, kind = p["storage"], p["kind"]
        if re.fullmatch(r"r([3-9]|10)", st):
            if kind not in INT_KINDS and kind != "f64":
                raise ValueError(f"parameter {p['name']} in {st} has kind {kind}")
            e = f"tc_bits_f64(r[{int(st[1:]) - 3}])" if kind == "f64" else f"{INT_KINDS[kind]}r[{int(st[1:]) - 3}]"
        elif re.fullmatch(r"f([1-9]|1[0-3])", st):
            e = f"f[{int(st[1:]) - 1}]"
            if kind == "f32":
                e = f"(f32){e}"
            elif kind != "f64":
                raise ValueError(f"parameter {p['name']} in {st} has kind {kind}")
        elif st == "stack":
            a = f"0x{cmp.SP + p['stack_offset']:08x}u" if stack_base is None else f"{stack_base} + 0x{p['stack_offset']:x}u"
            e = {"f32": f"rdf32({a})", "f64": f"rdf64({a})"}.get(kind)
            if e is None:  # a stack argument occupies a doubleword; an int lives in its low (higher-address) word
                e = f"{INT_KINDS.get(kind, '')}rd64({a})"
        else:
            raise ValueError(f"unsupported parameter storage {st}")
        args.append(e)
    call = f"{meta['name']}({', '.join(args)})"
    ret = meta["ret"]
    if ret in ("f32", "f64"):
        body = f"*out_f1 = (f64){call};"
    elif ret == "void":
        body = f"{call};"
    elif ret in RET_R3:
        body = f"*out_r3 = {RET_R3[ret]}{call};"
    else:
        raise ValueError(f"unsupported return kind {ret}")
    helper = BITS_F64 if "tc_bits_f64" in call else ""
    return f"""#include "tc_rt.h"
{helper}{meta['signature']};
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1)
{{
    (void)r; (void)f; (void)out_r3; (void)out_f1;
    {body}
}}
"""


def fma_define(cfg):
    mode = cfg.get("fma", "two_roundings")
    if mode not in FMA_MODES:
        raise ValueError(f'target.json "fma" must be one of {sorted(FMA_MODES)}, not {mode!r}')
    return f"/DTC_FMA_FUSED={FMA_MODES[mode]}"


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
    cmd = [cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", fma_define(cfg), f"/I{paths.RUNTIME}",
           f"/Fo{build_dir}\\", f"/Fe{build_dir / 'runner.exe'}", str(build_dir / "work.c"), str(build_dir / "thunk.c"),
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
    image = pathlib.Path(cfg["binary_path"]).read_bytes()
    result["stage"] = "compare"
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            d = cmp.compare_vector(meta, image, vecs[i], exp[i], act.get(i))
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
