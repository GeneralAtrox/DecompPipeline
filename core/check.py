"""The official judge: lint -> build -> run on test vectors -> byte-exact comparison with the oracle.

Verdict is PASS only when every rule holds and every vector in the requested sets matches
the original machine code's observable effects bit for bit.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import threading
import time

from . import filelock, paths, proof, readable_stack, strict_judge, target_hooks, toolcfg
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


# What a Visual Studio environment cache keeps (state/msvc_env.json, msvc_env_x86.json; port/tools/build.py keeps
# the same): the compiler's and linker's variables, Visual Studio's and the Windows SDK's, and the Windows ones a
# process needs. Nothing else the shell had is written to disk: no session token, pass key or address.
CAPTURED = ("PATH", "INCLUDE", "LIB", "LIBPATH", "EXTERNAL_INCLUDE", "CL", "_CL_", "LINK", "_LINK_",
            "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "OS", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
            "COMMONPROGRAMW6432", "PROGRAMDATA", "ALLUSERSPROFILE", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "PLATFORM", "COMMANDPROMPTTYPE", "DEVENVDIR", "EXTENSIONSDKDIR", "HTMLHELPDIR", "NETFXSDKDIR",
            "UCRTVERSION", "UNIVERSALCRTSDKDIR", "VCIDEINSTALLDIR", "VCINSTALLDIR", "VCTOOLSINSTALLDIR",
            "VCTOOLSREDISTDIR", "VCTOOLSVERSION", "VSINSTALLDIR", "VISUALSTUDIOVERSION", "WINDOWSLIBPATH",
            "VCPKG_ROOT")  # the port build's vcpkg toolchain
CAPTURED_RE = re.compile(r"VS\d+COMNTOOLS|VSCMD_\w+|__VSCMD_\w+|__DOTNET_\w+|FRAMEWORK\w*|WINDOWSSDK\w*", re.I)


def kept(name):
    return name.upper() in CAPTURED or bool(CAPTURED_RE.fullmatch(name))


def env_cache(path):
    """A cached Visual Studio environment, only its kept() variables; a cache written before that rule is cleaned in
    place."""
    env = paths.read_json(path)
    if env and not all(kept(k) for k in env):
        env = {k: v for k, v in env.items() if kept(k)}
        paths.write_json(path, env)
    return env


def captured(out):
    """The kept() variables of the environment `set` printed."""
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {k: v for k, v in env.items() if kept(k)}


def msvc_env(cfg):
    vcvars = toolcfg.need(cfg, "vcvars").replace("/", "\\")
    env = env_cache(paths.MSVC_ENV)
    if toolcfg.cache_current(paths.MSVC_ENV, env, vcvars, cfg):
        return env
    rc, out, reason = paths.run_bounded(toolcfg.vcvars_command(vcvars, cfg), 180)
    if reason:
        raise RuntimeError(f"could not load the Visual Studio environment (vcvars64.bat): {reason}")
    env = toolcfg.loaded(rc, captured(out), vcvars, cfg)  # its exit code and the pin decide, not how it looks
    paths.write_json(paths.MSVC_ENV, env)
    toolcfg.write_key(paths.MSVC_ENV, env, vcvars, cfg)
    return env


RUNTIME_SRCS = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
CL_FLAGS = ("/nologo", "/O1", "/fp:strict", "/W4", "/WX")


def runtime_objs(cl, env):
    """The runtime's objects, compiled once per content (every runtime file, the compiler, its INCLUDE and the flags)
    into build/runtime_obj/KEY/ and linked by every build after: compiling them was 0.38 s of a 0.59 s build. Each
    translation unit compiles alone, so an object is the same whichever build compiles it. None when they do not
    compile (a runtime warning under /WX): the full build then reports it as before."""
    h = hashlib.sha256(repr((CL_FLAGS, cl, os.stat(cl).st_size, os.stat(cl).st_mtime_ns,
                             next((v for k, v in env.items() if k.upper() == "INCLUDE"), ""))).encode())
    for f in sorted(paths.RUNTIME.iterdir()):
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    d = paths.BUILD / "runtime_obj" / h.hexdigest()[:16]  # build_prune passes it by (not a function folder)
    objs = [d / f"{s[:-2]}.obj" for s in RUNTIME_SRCS]
    if all(o.is_file() for o in objs):
        return objs
    tmp = d.with_name(f"{d.name}.tmp{os.getpid()}_{threading.get_ident()}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    cmd = [cl, "/c", *CL_FLAGS, "/TC", f"/I{paths.RUNTIME}", f"/Fo{tmp}\\", *(str(paths.RUNTIME / s) for s in RUNTIME_SRCS)]
    rc, _out, reason = paths.run_bounded(cmd, 120, cwd=tmp, env=env)
    if rc != 0 or reason:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    with filelock.locked(d.parent):  # build/runtime_obj.lock: a complete set another build links is never removed
        if not all(o.is_file() for o in objs):
            shutil.rmtree(d, ignore_errors=True)  # an incomplete set (a file deleted by hand)
            try:
                os.replace(tmp, d)
            except OSError:  # Windows refused (a file of the old set still open): this build compiles it itself
                pass
    shutil.rmtree(tmp, ignore_errors=True)  # another build put the same set there first
    return objs if all(o.is_file() for o in objs) else None


RUNNERS_KEPT = 8  # runner builds kept per folder (build/ADDR/raw_runners): the latest


def _runner_key(cl, env, build_dir, rt):
    """The sha256 of everything a raw build reads: the compiler (path, size, mtime), its INCLUDE and LIB, the flags,
    work.c and thunk.c as copied, every file of runtime/ and whether its objects come from runtime_objs, and the
    judge's own build code (this file: the flags, MINMAX and its unoptimized rebuild)."""
    st = os.stat(cl)
    h = hashlib.sha256(repr(("runner 1", CL_FLAGS, (cl, st.st_size, st.st_mtime_ns), rt is not None,
                             *(next((v for k, v in env.items() if k.upper() == n), "") for n in ("INCLUDE", "LIB")))
                            ).encode())
    for p in (build_dir / "work.c", build_dir / "thunk.c", pathlib.Path(__file__)):
        h.update(b"file " + p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    for f in sorted((f for f in paths.RUNTIME.rglob("*") if f.is_file()),
                    key=lambda f: f.relative_to(paths.RUNTIME).as_posix()):
        h.update(f"rt {f.relative_to(paths.RUNTIME).as_posix()}".encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def keep_runner(build_dir, kept, exe="runner.exe"):
    """Keep build_dir's runner as kept (a whole file or none), and only the latest RUNNERS_KEPT of the folder."""
    tmp = kept.with_name(f"{kept.name}.tmp{os.getpid()}_{threading.get_ident()}")
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build_dir / exe, tmp / exe)
        with filelock.locked(kept.parent):  # build/ADDR/raw_runners.lock
            if not (kept / exe).is_file():
                shutil.rmtree(kept, ignore_errors=True)
                os.replace(tmp, kept)
            old = sorted((d for d in kept.parent.iterdir() if d.is_dir() and ".tmp" not in d.name),
                         key=lambda d: d.stat().st_mtime_ns)[:-RUNNERS_KEPT]
            for d in old:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:  # not kept: the next build of these inputs compiles again
        pass
    shutil.rmtree(tmp, ignore_errors=True)


def build(cfg, meta, work_file, build_dir):
    """Compile work.c + its generated thunk + the runtime into runner.exe.
    A clean build (no message) is kept by the key of all it read (_runner_key) in build_dir's parent, raw_runners/:
    a build of the very same inputs (the official judge of the work.c the agent's last check built) takes that
    runner instead of compiling again. The vectors always run afresh."""
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    shutil.copyfile(work_file, build_dir / "work.c")
    (build_dir / "thunk.c").write_text(gen_thunk(meta), encoding="utf-8")
    env = msvc_env(cfg)
    env_path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl.exe", path=env_path)
    if not cl:
        raise RuntimeError("cl.exe not found in the Visual Studio environment")
    rt = runtime_objs(cl, env)
    kept = build_dir.parent / "raw_runners" / _runner_key(cl, env, build_dir, rt)[:24]
    if (kept / "runner.exe").is_file():
        shutil.copyfile(kept / "runner.exe", build_dir / "runner.exe")
        try:
            os.utime(kept)  # the latest kept stay
        except OSError:
            pass
        return True, []
    ok, msgs = _build(build_dir, env, cl, rt)
    if ok and not msgs:
        keep_runner(build_dir, kept)
    return ok, msgs


def _build(build_dir, env, cl, rt):
    # Zero-warning build: every warning at /W4 is an error, in work.c and in our own runtime alike.
    srcs = list(RUNTIME_SRCS)
    head = [cl, *CL_FLAGS, f"/I{paths.RUNTIME}", f"/Fo{build_dir}\\", "/FA", f"/Fa{build_dir}\\",
            f"/Fe{build_dir / 'runner.exe'}"]
    cmd = ([*head, "/Tc", str(build_dir / "work.c"), "/Tc", str(build_dir / "thunk.c"), *map(str, rt)] if rt else
           [*head, "/TC", str(build_dir / "work.c"), str(build_dir / "thunk.c"), *(str(paths.RUNTIME / s) for s in srcs)])
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
            objs = [build_dir / "work.obj", build_dir / "thunk.obj",
                    *(rt or [build_dir / f"{s[:-2]}.obj" for s in srcs])]
            cmd = [cl, "/nologo", f"/Fe{build_dir / 'runner.exe'}", *map(str, objs)]
            rc, out, reason = paths.run_bounded(cmd, 120, cwd=build_dir, env=env)
        if rc != 0 or reason:
            return False, ["build without optimization (float min/max under DAZ) failed:", *out.splitlines()[-15:]]
    return True, [re.sub(r"^.*\\work\.c", "work.c", e) for e in keep][:20]


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    """Full check of work_file. Returns a result dict; result['verdict'] is 'PASS' or 'FAIL'.

    harness_dir: test vectors from somewhere else (the watcher gate checks staged oracle output)."""
    hook = target_hooks.get("run_check")  # a target with its own runtime and harness format (core/target_hooks)
    if hook:
        return hook(cfg, addr, work_file, sets=sets, build_dir=build_dir, harness_dir=harness_dir)
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
    table = paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE  # made current, as for a prepare
    cmd = [str(build_dir / "runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES), str(table),
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
    lo = readable_stack.frame_lo()  # the own frame: writes from there up are the original's scratch
    strict = strict_judge.modes(cfg)  # report-only call snapshots or exit frame (core/strict_judge)
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            d = compare_vector(meta, image, vecs[i], exp[i], act.get(i), lo, strict, report := [])
            strict_judge.note(result, s, i, report)
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
    g = proof.grade(summary)  # report only (TcDirs): "fully proven" or "proven where tested", with the figures
    if g:
        result["coverage"].update({k: summary.get(k) for k in ("branch_dirs_total", "branch_dirs_hit_dev",
                                                              "branch_dirs_hit_all")}, grade=g["grade"])
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
