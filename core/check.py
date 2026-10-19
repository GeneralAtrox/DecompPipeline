"""The official judge: lint -> build -> run on test vectors -> byte-exact comparison with the oracle.

Verdict is PASS only when every rule holds and every vector in the requested sets matches
the original machine code's observable effects bit for bit.
"""
import json
import pathlib
import re
import shutil
import threading
import time

from . import paths, toolcfg
from .compare import (IMAGE_BASE, REG_INDEX, SP, STACK_LO, Memory, _f, compare_vector, is_nan,  # noqa: F401
                      parse_results, parse_vectors, same_f32)
from .lint import lint

MINMAX = re.compile(r"^\s+v?(min|max)[sp][sd]\s", re.M)  # x64 listing: SSE/AVX float min and max
OUTPUT_CAP_MIN = 16 << 20  # the runner's result file: at most max(16 MB, 8 x expected.txt) (run_runner)


def output_cap(harness):
    try:
        return max(OUTPUT_CAP_MIN, 8 * (pathlib.Path(harness) / "expected.txt").stat().st_size)
    except OSError:
        return OUTPUT_CAP_MIN


def run_runner(cmd, timeout, out, cap):
    """Run a runner whose result file is out, bounded in time (timeout) and in output (cap bytes).

    C that writes across the whole guest RAM in a loop keeps the runner bounded, but its result file grows to GBs,
    and parsing that for the compare would take GBs of memory. Past the cap the runner is stopped (its tree killed)
    and out emptied, so every vector fails. Returns run_bounded's (rc, log, reason)."""
    out = pathlib.Path(out)
    out.unlink(missing_ok=True)  # a stale result must not trip the cap (nor be compared)
    over, done = threading.Event(), threading.Event()

    def watch():
        while not done.wait(0.1):
            try:
                if out.stat().st_size > cap:
                    over.set()
                    return
            except OSError:
                pass
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        rc, log, reason = paths.run_bounded(cmd, timeout, stop_flag=over)
    finally:
        done.set()
        t.join()
    if over.is_set():
        for _ in range(50):  # the killed runner may hold the file a moment longer
            try:
                out.write_text("", encoding="utf-8")
                break
            except OSError:
                time.sleep(0.1)
        reason = f"output over {cap >> 20} MB"
    return rc, log, reason


def gen_thunk(meta):
    args, internal = [], False  # internal: the thunk needs the runner-only interface (tcrt_low, tcrt_set_retx)
    for p in meta["params"]:
        st, kind = p["storage"], p["kind"]
        if re.fullmatch(r"r[4-7]", st):
            e = f"r[{int(st[1:]) - 4}]"
            if kind == "f32":
                e = f"tc_u2f({e})"
        elif re.fullmatch(r"r[0-3]", st):  # an extra input register (target abi extra_inputs, vectors.txt line L)
            e, internal = f"tcrt_low({st[1:]})", True
            if kind == "f32":
                e = f"tc_u2f({e})"
        elif re.fullmatch(r"fr([4-9]|1[01])", st):
            e = f"fr[{int(st[2:]) - 4}]"
            if kind != "f32":
                e = f"tc_f2u({e})"
        elif st == "stack":
            e = f"rd32(0x{SP + p['stack_offset']:08x}u)"
            if kind == "f32":
                e = f"tc_u2f({e})"
        else:
            raise ValueError(f"unsupported parameter storage {st}")
        if kind == "s32":
            e = f"(s32){e}"
        args.append(e)
    call = f"{meta['name']}({', '.join(args)})"
    ret = meta["ret"]
    if meta.get("ret_reg"):  # a nonstandard result register: reported as RETX
        body, internal = f"tcrt_set_retx({'tc_f2u' if ret == 'f32' else '(u32)'}({call}));", True
    elif ret == "u64":  # r0 (low word) and r1 (high word, reported as RETX)
        body, internal = f"u64 v = {call}; *out_r0 = (u32)v; tcrt_set_retx((u32)(v >> 32));", True
    else:
        body = {"void": f"{call};", "f32": f"*out_fr0 = {call};"}.get(ret, f"*out_r0 = (u32){call};")
    inc = '#include "tc_rt_internal.h"\n' if internal else ""
    return f"""#include <string.h>
#include "tc_rt.h"
{inc}{meta['signature']};
static f32 tc_u2f(u32 u) {{ f32 f; memcpy(&f, &u, 4); return f; }}
static u32 tc_f2u(f32 f) {{ u32 u; memcpy(&u, &f, 4); return u; }}
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{{
    (void)r; (void)fr; (void)out_r0; (void)out_fr0; (void)tc_u2f; (void)tc_f2u;
    {body}
}}
"""


def msvc_env(cfg):
    env = paths.read_json(paths.MSVC_ENV)
    if env:
        return env
    vcvars = toolcfg.need(cfg, "vcvars").replace("/", "\\")
    rc, out, reason = paths.run_bounded(f'cmd /d /s /c ""{vcvars}" >nul 2>&1 && set"', 180)
    if rc != 0 or reason:
        raise RuntimeError("could not load the Visual Studio environment (vcvars64.bat)")
    env = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    paths.write_json(paths.MSVC_ENV, env)
    return env


def build(cfg, meta, work_file, build_dir):
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    shutil.copyfile(work_file, build_dir / "work.c")
    (build_dir / "thunk.c").write_text(gen_thunk(meta), encoding="utf-8")
    env = msvc_env(cfg)
    env_path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl.exe", path=env_path)
    if not cl:
        raise RuntimeError("cl.exe not found in the Visual Studio environment")
    # Zero-warning build: every warning at /W4 is an error, in work.c and in our own runtime alike.
    srcs = ["runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c"]
    cmd = [cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", f"/I{paths.RUNTIME}", f"/Fo{build_dir}\\",
           "/FA", f"/Fa{build_dir}\\", f"/Fe{build_dir / 'runner.exe'}", str(build_dir / "work.c"),
           str(build_dir / "thunk.c"), *(str(paths.RUNTIME / s) for s in srcs)]
    rc, out, reason = paths.run_bounded(cmd, 120, cwd=build_dir, env=env)
    keep = [ln for ln in out.splitlines() if ("error" in ln or "warning" in ln) and ".c" in ln]
    if rc != 0 or reason:
        errs = keep or out.splitlines()[-15:]
        return False, [re.sub(r"^.*\\work\.c", "work.c", e) for e in errs][:20]
    # The runner computes under DAZ (FPSCR.DN=1), and the optimizer turns a float compare-and-select into
    # minss/maxss, which takes a denormal operand as zero: the SH-4 moves the selected register bit for bit
    # (Toy Commander 8c01484a stores a denormal it chose with fcmp/gt; the /O1 build stored 0). Unoptimized,
    # the select stays a compare, a branch and a move, as in the C source.
    if MINMAX.search((build_dir / "work.asm").read_text(encoding="utf-8", errors="replace")):
        cmd = [cl, "/nologo", "/c", "/Od", "/fp:strict", "/W4", "/WX", "/TC", f"/I{paths.RUNTIME}",
               f"/Fo{build_dir}\\", str(build_dir / "work.c")]
        rc, out, reason = paths.run_bounded(cmd, 120, cwd=build_dir, env=env)
        if rc == 0 and not reason:
            objs = [build_dir / f"{s[:-2]}.obj" for s in ["work.c", "thunk.c", *srcs]]
            cmd = [cl, "/nologo", f"/Fe{build_dir / 'runner.exe'}", *map(str, objs)]
            rc, out, reason = paths.run_bounded(cmd, 120, cwd=build_dir, env=env)
        if rc != 0 or reason:
            return False, ["build without optimization (float min/max under DAZ) failed:", *out.splitlines()[-15:]]
    return True, [re.sub(r"^.*\\work\.c", "work.c", e) for e in keep][:20]


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    """Full check of work_file. Returns a result dict; result['verdict'] is 'PASS' or 'FAIL'.

    harness_dir: test vectors from somewhere else (the watcher gate checks staged oracle output)."""
    harness = harness_dir or paths.HARNESS / addr
    meta = paths.read_json(harness / "meta.json")
    from . import ret_reg  # the original returns in fr0 whatever the signature says: compare fr0 too
    meta = ret_reg.judged(meta, ret_reg.listed(paths.STATE), addr)
    summary = paths.read_json(harness / "oracle_summary.json") or {}
    result = {"addr": addr, "sets": list(sets), "verdict": "FAIL", "stage": "lint", "lint": [], "build": [],
              "vectors": {}, "first_diffs": [], "failing": 0, "total": 0}
    src = pathlib.Path(work_file).read_text(encoding="utf-8", errors="replace")
    result["lint"] = lint(src, meta)
    if not result["lint"]:  # R12: frame addresses the original passes (core/frame_args)
        from . import frame_args, sh4_asm
        result["lint"] = frame_args.lint_frame_args(src, sh4_asm.read_packet(paths.PACKETS / addr))
    if result["lint"]:
        return _finish(result, sets, summary)
    result["stage"] = "build"
    build_dir = build_dir or (paths.BUILD / addr)
    ok, msgs = build(cfg, meta, work_file, build_dir)
    result["build"] = msgs
    if not ok:
        return _finish(result, sets, summary)
    result["stage"] = "run"
    out = build_dir / "actual.txt"
    which = sets[0] if len(sets) == 1 else "all"
    cmd = [str(build_dir / "runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES), str(paths.FSCA_TABLE),
           str(harness / "vectors.txt"), which, str(out)]
    rc, log, reason = run_runner(cmd, cfg["check_timeout_s"], out, output_cap(harness))
    if reason:
        result["run_error"] = f"runner {reason} (an infinite loop in your code?)"
    elif rc != 0:
        result["run_error"] = f"runner exited with code {rc}: {log.strip()[-300:]}"
    vecs = parse_vectors(harness / "vectors.txt")
    exp = parse_results(harness / "expected.txt")
    act = parse_results(out)
    image = cfg["binary_path"].read_bytes()
    result["stage"] = "compare"
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            d = compare_vector(meta, image, vecs[i], exp[i], act.get(i))
            if d:
                fails.append(i)
                if s == "dev" and len(result["first_diffs"]) < 3:
                    result["first_diffs"].append({"vector": i, "inputs": _describe_inputs(meta, vecs[i]),
                                                  "diffs": d[:8]})
        result["vectors"][s] = {"total": len(ids), "failing": len(fails), "failing_ids": fails[:50]}
        result["failing"] += len(fails)
        result["total"] += len(ids)
    if not result["first_diffs"] and result["failing"]:
        result["first_diffs"].append({"vector": None, "inputs": "", "diffs": ["(failures are only in the hidden set)"]})
    return _finish(result, sets, summary)


def _describe_inputs(meta, vec):
    parts = []
    for p in meta["params"]:
        if re.fullmatch(r"r[4-7]", p["storage"]):
            parts.append(f"{p['name']}=0x{vec['r'][int(p['storage'][1:]) - 4]:08x}")
        elif re.fullmatch(r"r[0-3]", p["storage"]) and vec.get("low"):
            parts.append(f"{p['name']} (in {p['storage']})=0x{vec['low'][int(p['storage'][1:])]:08x}")
        else:
            parts.append(f"{p['name']} in {p['storage']}")
    for i, bits in enumerate(vec.get("fh") or []):
        if f"fr{12 + i}" in meta.get("fr_state", []):
            parts.append(f"sh4_fr({12 + i})={_f(bits)}")
    for i, bits in enumerate(vec.get("gs") or []):
        if f"r{8 + i}" in meta.get("r_state", []):
            parts.append(f"sh4_r({8 + i})=0x{bits:08x}")
    if meta.get("fpu_state") and vec.get("fq"):  # the whole FPU is state (line Q)
        parts.append("sh4_fr(0..15)=" + ",".join(_f(b) for b in vec["fq"]))
    return ", ".join(parts)


def _finish(result, sets, summary):
    min_valid = paths.config()["min_valid_vectors"]
    enough = all(result["vectors"].get(s, {}).get("total", 0) >= min_valid for s in sets)
    result["verdict"] = "PASS" if (result["stage"] == "compare" and result["failing"] == 0 and enough
                                   and "run_error" not in result) else "FAIL"
    result["coverage"] = {"blocks_total": summary.get("blocks_total"), "blocks_hit_dev": summary.get("blocks_hit_dev"),
                          "blocks_hit_all": summary.get("blocks_hit_all")}
    sites = summary.get("call_sites")  # call sites no valid vector reaches leave a "weak" pass (core/call_coverage)
    if sites is not None:
        unreached = sorted(a for a, n in sites.items() if not n)
        reach = summary.get("reach")  # the reach pass (TcReach): why each site it could not reach stays unreached
        why = (reach or {}).get("unreached") or {}
        result["coverage"].update({"calls_total": len(sites), "calls_reached": len(sites) - len(unreached),
                                   "calls_unreached": unreached[:20],
                                   "calls_untried": len([a for a in unreached if a not in why]),
                                   "calls_reasons": {a: why.get(a, "untried: the harness predates the reach pass")
                                                     for a in unreached[:20]}})
    if summary.get("stack_balance"):  # report only (TcBalance): unpushed callee-saved pops, r15 off at exit
        result["stack_balance"] = summary["stack_balance"]
    # Mismatch score used for "is the agent still making progress": lower is better.
    big = 10 ** 6
    result["score"] = {"lint": big * 3, "build": big * 2, "run": big}.get(result["stage"], result["failing"])
    if result["stage"] == "compare" and "run_error" in result:
        result["score"] = max(result["failing"], 1) + big // 2
    return result


def format_for_agent(result):
    lines = []
    if result["lint"]:
        lines.append("DEV FAIL - rule violations (fix these first):")
        lines += [f"  - {e}" for e in result["lint"]]
        return "\n".join(lines)
    if result["stage"] == "build":
        lines.append("DEV FAIL - work.c does not build cleanly (every compiler warning is an error, rule R11):")
        lines += [f"  {e}" for e in result["build"]]
        return "\n".join(lines)
    v = result["vectors"].get("dev", {})
    if result["verdict"] == "PASS":
        lines.append(f"DEV PASS - all {v.get('total')} dev vectors match the original.")
        lines.append("Stop now. (The hidden set is checked after your session ends.)")
        return "\n".join(lines)
    lines.append(f"DEV FAIL - {v.get('failing')} of {v.get('total')} dev vectors differ from the original.")
    if result.get("run_error"):
        lines.append(f"  runner: {result['run_error']}")
    for fd in result["first_diffs"]:
        lines.append(f"  vector {fd['vector']}  inputs: {fd['inputs']}")
        lines += [f"    - {d}" for d in fd["diffs"]]
    return "\n".join(lines)


def dumps(result):
    return json.dumps(result, indent=2)
