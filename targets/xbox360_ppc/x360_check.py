"""The official judge for the Xbox 360 target: lint -> build -> run on the test vectors -> byte-exact comparison
with the oracle (the counterpart of targets/gamecube_ppc/ppc_check.py, installed by hooks.run_check).

The verdict rules are core/check's (_finish): PASS only when every vector of the requested sets matches. One
difference, this target's only (finish): a check that ran and compared every vector, none wrong and no runner error,
but on fewer valid vectors than "min_valid_vectors" in a set is NO_VECTORS, not FAIL (core/check calls it FAIL, as
every other target still does). NO_VECTORS is never PASS: every consumer that asks for PASS still sees a non-PASS. A
wrong vector, a lint, build or runner failure stays FAIL. The
judge facts that differ from the GameCube's: r3 is compared in all 64 bits, integer arguments are 64-bit, f1..f13
carry float arguments, and the runtime's multiply-add rounding follows target.json "fma" (DESIGN.md).
"""
import collections
import pathlib
import re
import shutil

from core import check as core_check
from core import paths, strict_judge

import x360_compare as cmp

SOURCES = ("runner.c", "tc_rt.c", "tc_ppc.c", "tc_exit.c")
INT_KINDS = {"u8": "(u8)", "u16": "(u16)", "u32": "(u32)", "s8": "(s8)(u8)", "s16": "(s16)(u16)",
             "s32": "(s32)(u32)", "u64": "", "s64": "(s64)"}
# How each declared return type fills the 64-bit r3.
RET_R3 = {"u8": "(u64)", "u16": "(u64)", "u32": "(u64)", "s8": "(u64)(s64)", "s16": "(u64)(s64)",
          "s32": "(u64)(s64)", "u64": "(u64)", "s64": "(u64)"}
FMA_MODES = {"two_roundings": 0, "fused": 1}


from x360_lint import MAX_C, lint  # noqa: E402,F401  (its own file: tools import it without a project)


def finish(result, sets, summary):
    """core/check._finish, then its FAIL for too few valid vectors alone (stage compare, no vector wrong, no runner
    error, a set under "min_valid_vectors") becomes NO_VECTORS with result["why"]: the judge has nothing wrong to show,
    only too little to judge on."""
    result = core_check._finish(result, sets, summary)
    mv = paths.config()["min_valid_vectors"]
    short = {s: result["vectors"].get(s, {}).get("total", 0) for s in sets}
    if (result["verdict"] == "FAIL" and result["stage"] == "compare" and not result["failing"]
            and "run_error" not in result and any(n < mv for n in short.values())):
        result["verdict"] = "NO_VECTORS"
        result["why"] = ("too few valid vectors, none wrong: " + ", ".join(f"{s} {n}" for s, n in short.items())
                         + f" (the judge needs {mv} in each)")
    return result


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
    # The runtime is compiled once per content and flags (core/check.runtime_objs: the compiler, every runtime file,
    # these flags with the "fma" define) and its objects linked: compiling it again for every function, and again for
    # its exit-state form, was most of a build. None (it does not compile): the build compiles it itself, as before.
    # Objects, not a .lib: a .lib member whose symbols the C under test all defines would be dropped silently; linked
    # objects keep the duplicate-symbol error.
    rt = core_check.runtime_objs(cl, env, SOURCES, (fma_define(cfg),))
    head = [cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", fma_define(cfg), f"/I{paths.RUNTIME}",
            f"/Fo{build_dir}\\", f"/Fe{build_dir / 'runner.exe'}"]
    cmd = ([*head, "/Tc", str(build_dir / "work.c"), "/Tc", str(build_dir / "thunk.c"), *map(str, rt)] if rt else
           [*head, "/TC", str(build_dir / "work.c"), str(build_dir / "thunk.c"),
            *(str(paths.RUNTIME / s) for s in SOURCES)])
    rc, out, reason = paths.run_bounded(cmd, 120, cwd=build_dir, env=env)
    keep = [ln for ln in out.splitlines() if ("error" in ln or "warning" in ln) and ".c" in ln]
    msgs = [re.sub(r"^.*\\work\.c", "work.c", e) for e in keep][:20]
    if rc != 0 or reason:
        return False, msgs or out.splitlines()[-15:]
    return True, msgs


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None, exit_file=None):
    """exit_file: the translator's exit-state form of the same function (xenontranslate/exit_form.py). When the
    harness has the exit state (target.json "exit_state") it is built and run too, and its exit record is compared per
    class (x360_compare.compare_exit, core/strict_judge.exit_modes): an enforced class that differs fails the vector,
    a report-only one goes to "strict_report"; result["exit"]: the vectors per class that differ, the vectors an
    enforced class fails ("failing") and the first of them. Without exit_file (agent C:
    no registers to record) the exit state is not compared."""
    harness = pathlib.Path(harness_dir or paths.HARNESS / addr)
    meta = paths.read_json(harness / "meta.json")
    summary = paths.read_json(harness / "oracle_summary.json") or {}
    result = {"addr": addr, "sets": list(sets), "verdict": "FAIL", "stage": "lint", "lint": [], "build": [],
              "vectors": {}, "first_diffs": [], "failing": 0, "total": 0}
    src = pathlib.Path(work_file).read_text(encoding="utf-8", errors="replace")
    result["lint"] = lint(src, meta)
    if result["lint"]:
        return finish(result, sets, summary)
    result["stage"] = "build"
    build_dir = pathlib.Path(build_dir or (paths.BUILD / addr))
    ok, msgs = build(cfg, meta, work_file, build_dir)
    result["build"] = msgs
    if not ok:
        return finish(result, sets, summary)
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
    act_x, xmodes = None, None
    if exit_file and any("xs" in e for e in exp.values()):
        xmodes = strict_judge.exit_modes(cfg)
        act_x, err = run_exit(cfg, meta, harness, exit_file, build_dir / "exit", which)
        if err:
            result["run_error"] = err
    xcount, xfail, xfirst = collections.Counter(), 0, None
    image = pathlib.Path(cfg["binary_path"]).read_bytes()
    result["stage"] = "compare"
    strict = strict_judge.modes(cfg)  # report-only call snapshots or exit frame (core/strict_judge)
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            report = []
            d = cmp.compare_vector(meta, image, vecs[i], exp[i], act.get(i), strict, report, (act_x or {}).get(i),
                                   xmodes, xcount)
            strict_judge.note(result, s, i, report)
            xd = [x for x in d if x.startswith("exit state, ")]
            if xd:
                xfail += 1
                xfirst = xfirst or {"set": s, "vector": i, "diffs": xd[:4]}
            if d:
                fails.append(i)
                if s == "dev" and len(result["first_diffs"]) < 3:
                    result["first_diffs"].append({"vector": i, "inputs": cmp.describe_inputs(meta, vecs[i]),
                                                  "diffs": d[:8]})
        result["vectors"][s] = {"total": len(ids), "failing": len(fails), "failing_ids": fails[:50]}
        result["failing"] += len(fails)
        result["total"] += len(ids)
    if xmodes is not None:
        result["exit"] = {"classes": dict(xcount), "modes": xmodes, "failing": xfail, "first": xfirst}
    if not result["first_diffs"] and result["failing"]:
        result["first_diffs"].append({"vector": None, "inputs": "", "diffs": ["(failures are only in the hidden set)"]})
    return finish(result, sets, summary)


def run_exit(cfg, meta, harness, exit_file, build_dir, which):
    """(the exit-state form's results by vector, error text or None): built like work.c (the same runtime and flags; no
    lint: it is the translator's own C, not an answer) and run with the runner's "exit" argument."""
    ok, msgs = build(cfg, meta, exit_file, build_dir)
    if not ok:
        return {}, "exit-state form: build failed: " + " | ".join(msgs[:4])
    out = build_dir / "actual.txt"
    cmd = [str(build_dir / "runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES), str(harness / "vectors.txt"),
           which, str(out), "exit"]
    rc, log, reason = core_check.run_runner(cmd, cfg["check_timeout_s"], out, core_check.output_cap(harness))
    err = (f"exit-state form: runner {reason}" if reason else
           f"exit-state form: runner exited with code {rc}" if rc else None)
    return cmp.parse_results(out), err
