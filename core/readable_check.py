"""The judge for readable C: the same test vectors and comparison as check.py, run by the readable runtime.

The readable runtime (targets/<target>/runtime_rd) is a 32-bit build with the guest RAM mapped at its own address,
so readable code uses real pointers, structs and named globals, and calls other original functions by name.
Differences from check.py, all forced by readable code and none of them a loosening:
- A pointer into the caller's own stack frame can't have the original's guest address (locals live on the host
  stack). An argument the original points into its stack (r15 region) matches a pointer into the host stack,
  provided the pairing is one-to-one within the vector (the same guest slot is always the same host slot).
- Writes are found by comparing memory before and after the run (check.py compares final contents too).
"""
import hashlib
import os
import pathlib
import re
import shutil
import threading

from . import check, filelock, paths, pch_warnings, readable_ca, readable_stack, strict_judge, target_hooks, toolcfg
from .compare import FRAME_LO, STACK_HI

RUNTIME_RD = paths.TARGET / "runtime_rd"
MSVC_ENV_X86 = paths.STATE / "msvc_env_x86.json"
RD_SOURCES = ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_sh4.c", "rd_p4.c", "rd_trace.c") + (
    ("rd_be.c",) if (RUNTIME_RD / "rd_be.c").exists() else ())  # big-endian guest memory (saturn_sh2: rd_be.h)
# The raw runtime's P4 store log (tc_p4.c), shared so both runtimes log, limit and report those stores alike.
RUNTIME_SOURCES = [RUNTIME_RD / s for s in RD_SOURCES] + [paths.RUNTIME / "tc_p4.c"]
# The judge's compiler flags: zero warnings at /W4 (every warning an error). The header check (core/header_lint)
# builds game.h with the same flags, so a header that passes it builds in every judgement.
C_FLAGS = ("/fp:strict", "/arch:SSE2", "/std:c11", "/W4", "/WX", "/TC")


def msvc_env_x86(cfg):
    vcvars = toolcfg.need(cfg, "vcvars").replace("/", "\\").replace("vcvars64.bat", "vcvars32.bat")
    env = check.env_cache(MSVC_ENV_X86)
    if toolcfg.cache_current(MSVC_ENV_X86, env, vcvars, cfg):
        return env
    rc, out, reason = paths.run_bounded(toolcfg.vcvars_command(vcvars, cfg), 180)
    if reason:
        raise RuntimeError(f"could not load the 32-bit Visual Studio environment (vcvars32.bat): {reason}")
    env = toolcfg.loaded(rc, check.captured(out), vcvars, cfg)  # its exit code and the pin decide, not how it looks
    paths.write_json(MSVC_ENV_X86, env)
    toolcfg.write_key(MSVC_ENV_X86, env, vcvars, cfg)
    return env


def protos_c(protos):
    """C source of the prototype table the call trap uses. protos: {addr int: (param letters, ret letter[, regs])},
    regs as RdProto.regs (rd_internal.h): "" or one code per parameter (0..7, a..h, S..Z, x, s or '.')."""
    rows = [f'    {{0x{a:08x}u, "{v[0]}", \'{v[1]}\', "{v[2] if len(v) > 2 else ""}"}},'
            for a, v in sorted(protos.items())]
    rows = rows or ['    {0u, "", \'i\', ""},']
    return ('#include "rd_internal.h"\n'
            "const RdProto rd_protos[] = {\n" + "\n".join(rows) + "\n};\n"
            f"const int rd_nprotos = {len(protos)};\n")


def runtime_rd_objs(cl, env, include_dirs, rd=None, sources=None):
    """{runtime source: (object, listing)} of the readable runtime (sources, default RUNTIME_SOURCES, in folder rd,
    default RUNTIME_RD: a target with its own build passes its own, targets/gamecube_ppc), compiled once per content into
    build/runtime_rd_obj/KEY/ and linked by every readable build after: compiling them was ~1.9 s of every readable
    check (TC, 3 runs: 1.82-2.09 s). KEY: the compiler (path, size, mtime), its INCLUDE, C_FLAGS and every file of
    runtime_rd/ and runtime/. Compiled with only those two folders on the include path and /showIncludes: the
    headers it opened are kept in headers.txt. None, so the build compiles the runtime itself as before, when they
    do not compile, or when a folder of include_dirs (the game's headers, searched first) holds a file named like
    one of those headers: only then could the game's folders change what the runtime compiles to. A source whose
    listing has x87 float arithmetic or SSE min/max (_x87_or_minmax) is kept compiled unoptimized, as _build would
    recompile it in every build (GameCube tc_ppc.c at /O1: an fsub; 1.3-1.4 s a build to recompile and relink)."""
    rd = RUNTIME_RD if rd is None else rd
    sources = RUNTIME_SOURCES if sources is None else sources

    def ident(p):
        st = os.stat(p)
        return p, st.st_size, st.st_mtime_ns
    h = hashlib.sha256(repr(("od 1", C_FLAGS, ident(cl), next((v for k, v in env.items() if k.upper() == "INCLUDE"), ""),
                             [s.name for s in sources])).encode())
    for folder in (rd, paths.RUNTIME):
        for f in sorted(folder.iterdir()):
            if f.is_file():
                h.update(folder.name.encode() + b"/" + f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    d = paths.BUILD / "runtime_rd_obj" / h.hexdigest()[:16]  # build_prune passes it by (not a function folder)
    out = {str(s): (d / f"{s.stem}.obj", d / f"{s.stem}.asm") for s in sources}
    names = d / "headers.txt"
    if not (names.is_file() and all(o.is_file() and a.is_file() for o, a in out.values())):
        tmp = d.with_name(f"{d.name}.tmp{os.getpid()}_{threading.get_ident()}")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        cmd = [cl, "/nologo", "/c", "/O1", *C_FLAGS, f"/I{rd}", f"/I{paths.RUNTIME}", "/showIncludes",
               f"/Fo{tmp}\\", "/FA", f"/Fa{tmp}\\", *map(str, sources)]
        rc, log, reason = paths.run_bounded(cmd, 180, cwd=tmp, env=env)
        slow = [s for s in sources if _x87_or_minmax(tmp / f"{s.stem}.asm")] if rc == 0 and not reason else []
        if slow:  # unoptimized, as _build would (its x87 check then finds these clean, or refuses them as before)
            rc, more, reason = paths.run_bounded([*cmd[:3], "/Od", *cmd[4:-len(sources)], *map(str, slow)], 180,
                                                 cwd=tmp, env=env)
            log += "\n" + more
        if rc != 0 or reason:
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        note = "Note: including file:"
        opened = {pathlib.PureWindowsPath(ln[len(note):].strip()).name.lower() for ln in log.splitlines()
                  if ln.startswith(note)}
        (tmp / "headers.txt").write_text("".join(f"{n}\n" for n in sorted(opened)), encoding="utf-8")
        with filelock.locked(d.parent):  # build/runtime_rd_obj.lock
            if not (names.is_file() and all(o.is_file() and a.is_file() for o, a in out.values())):
                shutil.rmtree(d, ignore_errors=True)  # an incomplete set only: a complete one is never removed
                try:
                    os.replace(tmp, d)
                except OSError:  # Windows refused (a file of the old set still open): compiled by the build itself
                    pass
        shutil.rmtree(tmp, ignore_errors=True)
    if not (names.is_file() and all(o.is_file() and a.is_file() for o, a in out.values())):
        return None
    opened = set(names.read_text(encoding="utf-8").split())
    for inc in include_dirs:
        if pathlib.Path(inc).is_dir() and any(f.name.lower() in opened for f in pathlib.Path(inc).rglob("*")):
            return None
    return out


RUNNERS_KEPT = check.RUNNERS_KEPT  # runner builds kept per folder (build/ADDR/rd_runners): the latest


def _runner_key(cl, env, names, include_dirs, link, rd=None, code=()):
    """The sha256 of everything a readable build reads: the compiler (path, size, mtime), its INCLUDE and LIB, the
    flags and link options, every source as copied (the C under test, thunk.c, protos.c), every file of each include
    folder (by position, name and content: the generated game.h, the project's readable/include) and of rd (default
    runtime_rd/) and runtime/, and the judge's own build code (this file, pch_warnings: the PCH pass, check: MINMAX;
    and code, a target build's own file)."""
    rd = RUNTIME_RD if rd is None else rd
    st = os.stat(cl)
    h = hashlib.sha256(repr(("rd_runner 1", C_FLAGS, (cl, st.st_size, st.st_mtime_ns), link,
                             *(next((v for k, v in env.items() if k.upper() == n), "") for n in ("INCLUDE", "LIB")))
                            ).encode())
    for p in [*map(pathlib.Path, names), *(pathlib.Path(__file__).parent / f for f in
                                           ("readable_check.py", "pch_warnings.py", "check.py")), *code]:
        h.update(b"file " + p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    for k, d in enumerate((*include_dirs, rd, paths.RUNTIME)):
        d = pathlib.Path(d)
        for f in sorted((f for f in d.rglob("*") if f.is_file()), key=lambda f: f.relative_to(d).as_posix()):
            h.update(f"inc {k} {f.relative_to(d).as_posix()}".encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def pointer_protos(exp):
    """The call trap's prototypes for the targets of calls through function pointers (expected.txt CA: the argument
    registers the original sets, TcTrack): the readable call passes them as its parameters, the integer ones first in
    r4..r7 order, then the float ones in fr4..fr11 order; its result is guessed at the call ('?', as for an unknown
    target). A target called with several CA sets takes the longest. exp: parse_results of expected.txt."""
    out = {}
    for run in exp.values():
        for k, regs in run.get("call_args", {}).items():
            ints, floats = [r for r in regs if r[0] == "r"], [r for r in regs if r[0] == "f"]
            kinds = "i" * len(ints) + "f" * len(floats)
            codes = "".join(r[1:] for r in ints) + "".join("abcdefgh"[int(r[2:]) - 4] for r in floats)
            t = run["calls"][k][0]
            if kinds and len(kinds) > len(out.get(t, ("",))[0]):
                out[t] = (kinds, "?", codes)
    return out


def build(cfg, sources, thunk_text, protos, build_dir, include_dirs=(), runtime=None):
    """Compile sources + generated thunk and prototype table + the readable runtime into rd_runner.exe. runtime:
    (folder, sources, the target build's own code files) of a target's own readable runtime (targets/gamecube_ppc),
    else RUNTIME_RD and RUNTIME_SOURCES.
    A clean build (no message) is kept by the key of all it read (_runner_key) in build_dir's parent, rd_runners/:
    a build of the very same inputs (the official judge of the work.c the agent's last check built, with the same
    header snapshot) takes that runner instead of compiling again (~2-3 s). The vectors always run afresh."""
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    names = []
    for i, src in enumerate(sources):
        dst = build_dir / ("work.c" if i == 0 else f"src{i}.c")
        shutil.copyfile(src, dst)
        names.append(str(dst))
    (build_dir / "thunk.c").write_text(thunk_text, encoding="utf-8")
    (build_dir / "protos.c").write_text(protos_c(protos), encoding="utf-8")
    env = msvc_env_x86(cfg)
    env_path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl.exe", path=env_path)
    if not cl:
        raise RuntimeError("cl.exe not found in the 32-bit Visual Studio environment")
    rd, rt_sources, code = runtime or (RUNTIME_RD, RUNTIME_SOURCES, ())
    key = _runner_key(cl, env, [*names, build_dir / "thunk.c", build_dir / "protos.c"], include_dirs,
                      link_flags(cfg), rd, code)
    kept = build_dir.parent / "rd_runners" / key[:24]
    if (kept / "rd_runner.exe").is_file():
        shutil.copyfile(kept / "rd_runner.exe", build_dir / "rd_runner.exe")
        try:
            os.utime(kept)  # the latest kept stay
        except OSError:
            pass
        return True, []
    ok, msgs = _build(cfg, names, build_dir, include_dirs, env, cl, *(runtime[:2] if runtime else ()))
    if ok and not msgs:
        keep_runner(build_dir, kept)
    return ok, msgs


def keep_runner(build_dir, kept):
    """Keep build_dir's runner as kept (a whole file or none), and only the latest RUNNERS_KEPT of the folder."""
    check.keep_runner(build_dir, kept, "rd_runner.exe")


def _build(cfg, names, build_dir, include_dirs, env, cl, rd=None, sources=None):
    rd = RUNTIME_RD if rd is None else rd
    sources = RUNTIME_SOURCES if sources is None else sources
    incs = [f"/I{d}" for d in (*include_dirs, rd, paths.RUNTIME)]
    flags = [*C_FLAGS, *incs, f"/Fo{build_dir}\\"]
    link = link_flags(cfg)
    rt = runtime_rd_objs(cl, env, include_dirs, rd, sources)  # None: the runtime is compiled here, as before
    own = [*names, str(build_dir / "thunk.c"), str(build_dir / "protos.c")]
    if rt:  # compile (C_FLAGS' /TC would take an .obj for C source), then link with the cached runtime's objects
        cmd = [cl, "/nologo", "/c", "/O1", *flags, "/FA", f"/Fa{build_dir}\\", *own]
        rc, out, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
        if rc == 0 and not reason:
            objs = [str(build_dir / f"{pathlib.Path(s).stem}.obj") for s in own]
            cmd = [cl, "/nologo", f"/Fe{build_dir / 'rd_runner.exe'}", *objs,
                   *(str(rt[str(s)][0]) for s in sources), *link]
            rc, more, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
            out += more
    else:
        cmd = [cl, "/nologo", "/O1", *flags, "/FA", f"/Fa{build_dir}\\", f"/Fe{build_dir / 'rd_runner.exe'}", *own,
               *map(str, sources), *link]
        rc, out, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
    keep = [ln for ln in out.splitlines() if ("error" in ln or "warning" in ln)]
    if rc != 0 or reason:
        return False, [re.sub(r"^.*\\work\.c", "work.c", e) for e in (keep or out.splitlines()[-15:])][:20]
    if pch := pch_warnings.check(cl, [*C_FLAGS, *incs], names, build_dir, env):  # C4505, as the port's build
        return False, pch
    # Every source of this build, as compiled: the C under test, the generated files and the runtime's own.
    srcs = [*names, str(build_dir / "thunk.c"), str(build_dir / "protos.c"), *map(str, sources)]
    listing = {s: build_dir / f"{pathlib.Path(s).stem}.asm" for s in srcs}
    obj = {s: build_dir / f"{pathlib.Path(s).stem}.obj" for s in srcs}
    for s, (o, a) in (rt or {}).items():  # the cached runtime's own objects and listings
        obj[s], listing[s] = o, a
    # Recompiled unoptimized, and the runner relinked from the same objects, is a source whose listing has:
    # - as in check.build, a minss/maxss the optimizer made of a float compare-and-select: under DAZ (FPSCR.DN=1) it
    #   takes a denormal operand as zero, where the SH-4 moves the selected register bit for bit (TC 8c01484a);
    # - x87 float arithmetic. A 32-bit build returns f32 in st(0), and the optimizer then computes on the x87 stack
    #   (TC 8c013436: fld, fmul st(0),st(0)): extended precision and the x87's own control word, which has no DAZ or
    #   FTZ and rounds to nearest, where the SH-4 (and check.py's 64-bit SSE build, MXCSR from FPSCR) computes in
    #   single precision with FPSCR.DN and FPSCR.RM. Unoptimized, MSVC computes in SSE registers and uses the x87
    #   only to move a result (fld/fstp: bit-exact, also for denormals) and to convert an integer (fild: exact; the
    #   store rounds as RM says, as rd_mem.c sets the x87 rounding control).
    slow = [s for s in srcs if _x87_or_minmax(listing[s])]
    if slow:
        cmd = [cl, "/nologo", "/c", "/Od", *flags, "/FA", f"/Fa{build_dir}\\", *slow]
        rc, out, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
        if rc == 0 and not reason:
            for s in slow:  # recompiled into build_dir (a cached runtime file too)
                obj[s], listing[s] = build_dir / f"{pathlib.Path(s).stem}.obj", build_dir / f"{pathlib.Path(s).stem}.asm"
            objs = [obj[s] for s in srcs]
            cmd = [cl, "/nologo", f"/Fe{build_dir / 'rd_runner.exe'}", *map(str, objs), *link]
            rc, out, reason = paths.run_bounded(cmd, 180, cwd=build_dir, env=env)
        if rc != 0 or reason:
            return False, ["build without optimization (x87 float code or float min/max under DAZ) failed:",
                           *out.splitlines()[-15:]]
        still = [pathlib.Path(s).name for s in slow if _x87_or_minmax(listing[s])]
        if still:  # never judge x87 arithmetic: it would not compute what the SH-4 computes
            return False, [f"{', '.join(still)}: x87 float arithmetic or SSE min/max even unoptimized (32-bit build); "
                           "the readable runtime cannot judge this float code bit-exactly"]
    return True, [re.sub(r"^.*\\work\.c", "work.c", e) for e in keep][:20]


# x87 instructions that compute (arithmetic, compares, roots, transcendentals, float-to-integer stores). Not counted:
# fld/fst/fstp/fxch/fldz/fld1 (moves, bit-exact), fchs/fabs (sign only), fild (exact), fnstsw/fnstcw/fldcw (status).
X87_MATH = re.compile(r"^\s+f(?:add|sub|mul|div|i(?:add|sub|mul|div|com|st)|com|ucom|tst|sqrt|prem|rndint|scale|sin|cos"
                      r"|sincos|ptan|patan|yl2x|2xm1|xtract|bstp)[a-z0-9]*\s", re.M)


def _x87_or_minmax(asm):
    text = asm.read_text(encoding="utf-8", errors="replace") if asm.exists() else ""
    return bool(check.MINMAX.search(text) or X87_MATH.search(text))


def host_stacks(path):
    """{vector id: (lo, hi, anchor)} from the runner's HOSTSTACK and VAANCHOR lines (anchor: (host, guest) address of
    a variadic function's first variable word, from its thunk, else None)."""
    from .compare import _lines
    res, anchors, cur = {}, {}, None
    for line in _lines(path):
        t = line.split()
        if t and t[0] == "V":
            cur = int(t[1])
        elif t and t[0] == "VAANCHOR" and cur is not None:
            anchors[cur] = (int(t[1], 16), int(t[2], 16))
        elif t and t[0] == "HOSTSTACK" and cur is not None:
            res[cur] = (int(t[1], 16), int(t[2], 16))
    return {k: (lo, hi, anchors.get(k)) for k, (lo, hi) in res.items()}


def map_stack_args(meta, exp, act, stack, maps=None, frame_lo=FRAME_LO):
    """Replace host-stack pointer arguments by the original's guest-stack pointers when they pair one-to-one.

    Returns a list of problems (a guest slot paired with two host slots or the reverse). A variadic function's thunk
    pairs its first variable word (the anchor): a va_list the function passes on must point exactly there. maps:
    (guest -> host, host -> guest) dicts to fill (readable_stack.pair_stores goes on with them). frame_lo: the
    original's own frame bottom (readable_stack.frame_lo()): an address below it is a global, never paired."""
    if not act or not stack:
        return []
    lo, hi, anchor = stack
    callee_regs = {int(c["addr"], 16): c["regs"] for c in meta["callees"]}
    (g2h, h2g), bad = maps if maps is not None else ({}, {}), []
    if anchor:
        h2g[anchor[0]], g2h[anchor[1]] = anchor[1], anchor[0]
    for k, (ec, ac) in enumerate(zip(exp["calls"], act["calls"])):
        if ec[0] != ac[0]:
            break
        # the registers compare_vector judges: a listed callee's, else the original's own (CA, a pointer call)
        for reg in callee_regs.get(ec[0], exp.get("call_args", {}).get(k, [])):
            i = check.REG_INDEX[reg]
            g, h = ec[i], ac[i]
            if not (frame_lo <= g < STACK_HI and lo <= h < hi):
                continue
            if g2h.setdefault(g, h) != h or h2g.setdefault(h, g) != g:
                bad.append(f"call #{k + 1}: argument {reg} points at a different stack slot than in earlier calls")
                continue
            ac[i] = g
    return bad


ARG_REG = re.compile(r"r[0-7]|fr([4-9]|1[01])")


def callee_reads(meta, reads):
    """meta with each callee record compared only on the argument registers the callee's own harness takes (reads:
    {callee addr: its parameters' storages}, from its own meta.json with the record's signature); stack words and
    runtime state as recorded. A caller's record can name a register its callee never reads: TC's Xmtrx_MulPop
    (8c06162c, `void (void)` in its own harness) loads r0 with a constant and falls into code that calls through r0,
    which the callee summary cannot resolve, so its callers' records compare r4 (the entry PR, a leftover pointer).
    The callee's readable C takes exactly its own parameters, so a register outside them cannot change what it does:
    comparing it would demand a value no readable call can give. Callees without their own meta keep every record
    register."""
    if not reads:
        return meta
    callees = [dict(c, regs=[r for r in c["regs"] if not ARG_REG.fullmatch(r) or r in reads[c["addr"]]])
               if c["addr"] in reads else c for c in meta["callees"]]
    return dict(meta, callees=callees)


def compare(cfg, meta, harness, out_file, sets, variadic=None, reads=None):
    """Compare the runner's output with the oracle's; same result layout as check.run_check. variadic: {callee addr:
    readable_varargs.variadic_info}, whose calls are judged on the stack words the original passes; reads:
    callee_reads'."""
    from .readable_stack import pair_stores
    from .readable_varargs import judged_meta, mask_calls
    meta = judged_meta(callee_reads(meta, reads), variadic)
    from . import ret_reg  # the original returns in fr0 whatever the signature says: compare fr0 too
    meta = ret_reg.judged(meta, ret_reg.listed(paths.STATE))
    vecs = check.parse_vectors(harness / "vectors.txt")
    exp = readable_ca.narrow(check.parse_results(harness / "expected.txt"), reads)
    act = check.parse_results(out_file)
    stacks = host_stacks(out_file) if out_file.exists() else {}
    image = cfg["binary_path"].read_bytes()
    result = {"vectors": {}, "first_diffs": [], "failing": 0, "total": 0}
    lo = readable_stack.frame_lo()  # the own frame: writes from there up are the original's scratch
    strict = strict_judge.modes(cfg)  # report-only call snapshots or exit frame (core/strict_judge)
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            mask_calls(variadic, image, vecs[i], exp[i], act.get(i))
            maps = ({}, {})
            d = map_stack_args(meta, exp[i], act.get(i), stacks.get(i), maps, lo)
            pair_stores(exp[i], act.get(i), stacks.get(i), *maps, check.Memory(image, vecs[i]).initial, lo)
            d += check.compare_vector(meta, image, vecs[i], exp[i], act.get(i), lo, strict, report := [])
            strict_judge.note(result, s, i, report)
            if d:
                fails.append(i)
                if len(result["first_diffs"]) < 3:
                    result["first_diffs"].append({"vector": i, "set": s, "diffs": d[:8]})
        result["vectors"][s] = {"total": len(ids), "failing": len(fails), "failing_ids": fails[:50]}
        result["failing"] += len(fails)
        result["total"] += len(ids)
    return result


def run(cfg, harness, build_dir, sets=("dev", "hidden"), vectors=None):
    """vectors: the runner's vectors file (readable_outrun.vectors_file), default harness/vectors.txt."""
    out = build_dir / "actual.txt"
    which = sets[0] if len(sets) == 1 else "all"
    table = paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE  # made current, as for a prepare
    cmd = [str(build_dir / "rd_runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES), str(table),
           str(vectors or harness / "vectors.txt"), which, str(out)]
    rc, log, reason = check.run_runner(cmd, cfg["check_timeout_s"], out, check.output_cap(harness))
    err = f"runner {reason}" if reason else (f"runner exited with code {rc}: {log.strip()[-300:]}" if rc else None)
    return out, err


def link_flags(cfg):
    """The runner's link options. A fixed image base, no ASLR: a vector's arbitrary function-pointer value (TC
    8c0633cc: 0x01040401) is a guest call only while it misses the runner's own code; with a per-build random base it
    sometimes hit .text and ran host code (a crash, a missing call). Without ASLR nothing randomized takes rd_p4.c's
    fixed pages either (with it, QACR0/1's page was taken in 26 of 300 runs). The target's readable_image_base keeps
    the image clear of guest memory."""
    base = cfg.get("readable_image_base") or "0x5a000000"
    return ["/link", "/LARGEADDRESSAWARE", "/NXCOMPAT", "/DYNAMICBASE:NO", "/FIXED:NO", f"/BASE:{base}"]


def verdict(result, sets):
    min_valid = paths.config()["min_valid_vectors"]
    enough = all(result["vectors"].get(s, {}).get("total", 0) >= min_valid for s in sets)
    ok = result.get("stage") == "compare" and not result["failing"] and enough and not result.get("run_error")
    result["verdict"] = "PASS" if ok else "FAIL"
    return result


def callee_storage(harness, callee, more=()):
    """The callee's parameter storage from its own meta.json (next to this harness, the project's, or in a harness
    directory of more), when that meta has the same signature; else None (readable.callee_regs then goes by the
    signature's names)."""
    from . import readable_abi
    for d in (harness.parent / callee["addr"], paths.HARNESS / callee["addr"], *(m / callee["addr"] for m in more)):
        try:
            m = paths.read_json(d / "meta.json", {})
        except (OSError, ValueError):
            continue
        if m.get("signature") == callee["signature"]:
            return readable_abi.param_storage(m)
    return None


_STARTS = {}


def data_functions(reg, addr, meta):
    """Functions the matched raw C uses as data (a function pointer it stores, passes or calls through), other than its
    callees: readable C names them, so game.h declares them (their prototype: the registry's or their harness's)."""
    f = paths.MATCHED / f"{addr}.c"
    if not f.exists() or not paths.CATALOG.exists():
        return []
    key = (str(paths.CATALOG), paths.CATALOG.stat().st_mtime_ns)
    if key not in _STARTS:
        cat = paths.read_json(paths.CATALOG)
        _STARTS[key] = {e["addr"] for e in (cat["functions"] if isinstance(cat, dict) else cat)}
    known = {c["addr"] for c in meta["callees"]} | {addr}
    found = {m.lower() for m in re.findall(r"0x([0-9a-fA-F]{8})", f.read_text(encoding="utf-8", errors="replace"))}
    return sorted(a for a in (found & _STARTS[key]) - known
                  if a in reg.functions or (paths.HARNESS / a / "meta.json").exists())


def check_readable(cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, harness_dir=None,
                   callee_harness=(), lean=False, stub=False):
    """Full check of one readable function (readable/src/<addr>.c) against the original's test vectors.

    harness_dir: test vectors from somewhere else (regression fixtures: a fresh private harness); callee_harness:
    more harness directories with the callees' meta.json (their parameter storage: callee_storage). lean: build
    against the function's lean headers only (readable_lean: what its agent sandbox holds) instead of game.h with
    every function and global and the project's readable/include; the proof that a lean sandbox is enough. stub: the
    file is refine's do-nothing preflight stub, which leaves every parameter out by design: RR6 is not applied."""
    hook = target_hooks.get("readable_check")  # a target with its own readable judge glue (gamecube_ppc)
    if hook:
        return hook(cfg, addr, src_file, build_dir, sets=sets, reg=reg, harness_dir=harness_dir,
                    callee_harness=callee_harness, lean=lean, stub=stub)
    from . import readable, readable_abi, readable_outptr, readable_outrun, readable_stack, readable_varargs
    from .readable_lint import fused_lint, pointer_bytes_lint, raw_name_lint, readable_lint, void_result_lint
    harness = harness_dir or paths.HARNESS / addr
    meta = paths.read_json(harness / "meta.json")
    result = {"addr": addr, "verdict": "FAIL", "stage": "lint", "lint": [], "build": []}
    if not meta:
        result["lint"] = [f"no harness for {addr} ({paths.show(harness)}/meta.json): prepare it first"]
        return result
    try:
        reg = reg or readable.Registry()
        proto = reg.proto(addr, meta)
        name = readable.parse_proto(proto)[1]
        sigs = {c["addr"]: c["signature"] for c in meta["callees"]} | {addr: meta["signature"]}
        data = data_functions(reg, addr, meta)  # function pointers the raw C uses as data: declared by name
        protos = {int(a, 16): reg.kinds(reg.proto(a, raw_sig=sigs.get(a)))
                  for a in sorted(set(reg.functions) | set(sigs) | set(data))}
        aliases, variadic, decl, reads = {}, {}, dict(sigs), readable_ca.ca_reads(reg, harness)  # CA: readable_ca
        for c in meta["callees"]:  # where each argument goes (by storage), and whether the raw name still fits
            storage = callee_storage(harness, c, callee_harness)
            if storage is not None and c["addr"] != addr:  # the registers the callee itself reads (callee_reads)
                reads[c["addr"]] = set(storage.values())
            if c["addr"] != addr:
                decl[c["addr"]] = readable_abi.storage_named(reg, c, storage)
            cproto = reg.proto(c["addr"], raw_sig=c["signature"])
            regs = readable.callee_regs(reg, c, cproto, storage)
            if regs:
                protos[int(c["addr"], 16)] += (regs,)
            if readable.is_variadic(cproto):  # the stack words judged at its calls (readable_varargs)
                place = readable_abi.callee_place(reg, c, cproto, storage)
                variadic[int(c["addr"], 16)] = readable_varargs.variadic_info(reg, c["addr"], place)
            if readable_abi.raw_alias_ok(reg, c, storage):
                aliases[c["addr"]] = c["signature"]
        for a, v in protos.items():  # a variadic function's named parameters: always by explicit storage
            if v[0].endswith("*") and len(v) == 2:
                protos[a] = v + (readable_abi.conv_regs(reg, reg.proto(f"{a:08x}", raw_sig=sigs.get(f"{a:08x}"))),)
        for a, v in pointer_protos(check.parse_results(harness / "expected.txt")).items():
            protos.setdefault(a, v)  # calls through function pointers: the arguments the original sets (CA)
        thunk = readable.thunk_c(reg, addr, meta)
        lean_h = None
        if lean:  # as the sandbox (refine.build_sandbox) declares them, with the file under test as the current one
            from . import global_extents, lean_proof, readable_lean
            raw = paths.MATCHED / f"{addr}.c"
            lean_h = readable_lean.headers_for(reg, meta, list(sigs) + data, decl,
                                               raw.read_text(encoding="utf-8") if raw.exists() else "",
                                               global_extents.for_process(cfg, reg),
                                               readable_lean.current_text(src_file))
            data += lean_h["functions"]
        game_h = readable.game_h(reg, verify_self=addr, addrs=list(sigs) + data, raw_sigs=decl, aliases=aliases,
                                 lean_globals=lean_h and lean_h["globals"])
    except readable.RegistryError as e:
        result["lint"] = [f"registry: {e}"]
        return result
    if not pathlib.Path(src_file).exists():
        result["lint"] = [f"no readable file {src_file}"]
        return result
    src = pathlib.Path(src_file).read_text(encoding="utf-8", errors="replace")
    from . import global_extents, raw_consts, retire  # RR3: a mirror-form number that names a symbol

    def named(v):
        return raw_consts.resolve(reg, global_extents.for_process(cfg, reg), v)
    result["lint"] = (readable_lint(src, name, proto, named, retire.mirrors(paths.ROOT))
                      + raw_name_lint(src, reg, set(aliases) | {addr})
                      + void_result_lint(src, reg) + ([] if stub else fused_lint(cfg, meta, src)))  # RR8, not on a stub
    raw_file = paths.MATCHED / f"{addr}.c"
    if raw_file.exists():
        raw_text = raw_file.read_text(encoding="utf-8", errors="replace")
        result["lint"] += pointer_bytes_lint(src, raw_text, cfg["binary_path"].read_bytes())
        if not stub:
            result["lint"] += readable_stack.stack_arg_lint(src, raw_text, reg, addr, meta, name)
    from . import readable_be  # big-endian guest memory (saturn_sh2): BE1-BE3; nothing on the Dreamcast
    result["lint"] += readable_be.target_lint(reg, src, raw_file.exists() and raw_text, harness)
    if result["lint"]:
        return result
    result["stage"] = "build"
    gen = build_dir.parent / (build_dir.name + "_inc")
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "game.h").write_text(game_h, encoding="utf-8")
    if lean_h:
        (gen / "types.h").write_text(lean_h["types.h"], encoding="utf-8")
        result["build"] = lean_proof.prove(cfg, lean_h.get("views"), build_dir.parent / (build_dir.name + "_proof"))
        if result["build"]:  # a projected struct whose layout is not the full one's (core/lean_proof)
            return result
    elif reg.isolated:
        (gen / "types.h").write_text("/* empty names registry: no game types */\n", encoding="utf-8")
    incs = (gen,) if reg.isolated or lean_h else (gen, readable.INCLUDE)
    ok, msgs = build(cfg, [src_file], thunk, protos, build_dir, include_dirs=incs)
    result["build"] = msgs
    if not ok:
        return result
    judged = meta
    try:  # callee out-pointers into the caller's stack: the mocked callee's bytes reach the readable local
        specs = readable_outptr.out_params(reg, meta, lambda c: callee_storage(harness, c, callee_harness))
        specs, judged = readable_outptr.paired(meta, check.parse_results(harness / "expected.txt"), specs,
                                               readable_stack.frame_lo())
        vec_file, n_out = readable_outrun.vectors_file(cfg, meta, harness, specs, build_dir,
                                                       (*incs, RUNTIME_RD, paths.RUNTIME), addr, sets)
        if n_out:  # named for the agent's failure text (refine.format_for_agent)
            result["out_pointers"] = sorted({readable.parse_proto(reg.functions[s[0]]["proto"])[1] for s in specs})
    except RuntimeError as e:  # the pointee size probe did not build: judged without the copies (stricter)
        vec_file, result["outptr_error"] = None, str(e)[:300]
    out, err = run(cfg, harness, build_dir, sets, vec_file)
    result |= compare(cfg, judged, harness, out, sets, variadic, reads) | {"stage": "compare"}
    if err:
        result["run_error"] = err
    return verdict(result, sets)


def check_raw(cfg, addr, work_file, build_dir, sets=("dev", "hidden"), harness_dir=None):
    """Run raw (tc_rt.h) matched C through the readable runtime: the runtime's own regression test (and its fixtures
    in tools/regress.py, "runtime": "readable"; harness_dir: their private harness). The runtime implements the whole
    raw API, so every raw function runs here, also those outside the C calling convention (r0..r3 parameters, r8..r14
    state, RETX results, callees taking r0..r3: tcrt_low, sh4_r, tcrt_set_retx, args.rlo)."""
    hook = target_hooks.get("readable_check_raw")  # a target with its own readable runtime glue (gamecube_ppc)
    if hook:
        return hook(cfg, addr, work_file, build_dir, sets=sets, harness_dir=harness_dir)
    harness = harness_dir or paths.HARNESS / addr
    meta = paths.read_json(harness / "meta.json")
    ok, msgs = build(cfg, [work_file], check.gen_thunk(meta), {}, build_dir)
    if not ok:
        return {"verdict": "FAIL", "stage": "build", "build": msgs}
    out, err = run(cfg, harness, build_dir, sets)
    result = compare(cfg, meta, harness, out, sets) | {"stage": "compare", "build": msgs}
    if err:
        result["run_error"] = err
    return verdict(result, sets)
