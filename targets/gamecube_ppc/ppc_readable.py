"""Readable C for the GameCube PowerPC target: the readable runtime's build (runtime_rd), raw C run through it
(check_raw: the runtime's own regression test) and the readable judge (check_readable). The PowerPC counterparts of
core/readable_check, installed through hooks.py (readable_check_raw, readable_check).

The readable runtime is a 32-bit build with MEM1 mapped at 0x80000000 (and its uncached view 0xc0000000), so readable
code uses real pointers, structs and named globals and calls other original functions by name. Guest memory stays
big endian: readable code reads and writes it through rd_be.h's be_ types (be_get, be_set). The comparison is
ppc_compare's, as for raw C (ppc_check), plus the one difference readable code forces, as in core/readable_check: a
pointer the original passes into its own stack frame matches a pointer into the host stack when the pairing is one
to one within the vector. Nothing else is loosened.

Not yet carried over from the SH-4 readable judge (each makes a correct readable function FAIL, never a wrong one
pass): variadic callees (refused), out-pointers into the caller's frame (core/readable_outptr: a local passed to a
mocked callee gets no bytes; the oracle's frame starts zeroed, so `T v = {0};` matches), stack addresses stored to
memory (readable_stack.pair_stores), lean sandboxes.
"""
import pathlib
import re
import shutil

from core import check as core_check
from core import paths, pch_warnings, readable, readable_be
from core import readable_check as rc

import ppc_check
import ppc_readable_abi as abi
import ppc_compare as cmp

RUNTIME_RD = paths.TARGET / "runtime_rd"
RD_SOURCES = ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_be.c")
RUNTIME_SOURCES = [RUNTIME_RD / s for s in RD_SOURCES] + [paths.RUNTIME / "tc_ppc.c"]
STACK_HI = 0x81800000
FRAME_MAX = 0x1000  # the deepest own frame below the entry r1 that a stack-pointer argument may point into


def protos_c(protos):
    """C source of the prototype table the call trap uses. protos: {addr int: (param letters, ret letter[, regs])}
    (RdProto in runtime_rd/rd_internal.h)."""
    rows = [f'    {{0x{a:08x}u, "{v[0]}", \'{v[1]}\', "{v[2] if len(v) > 2 else ""}"}},'
            for a, v in sorted(protos.items())]
    rows = rows or ['    {0u, "", \'i\', ""},']
    return ('#include "rd_internal.h"\n'
            "const RdProto rd_protos[] = {\n" + "\n".join(rows) + "\n};\n"
            f"const int rd_nprotos = {len(protos)};\n")


def build(cfg, sources, thunk_text, protos, build_dir, include_dirs=()):
    """Compile sources + generated thunk and prototype table + the readable runtime into rd_runner.exe: core's
    readable build (core/readable_check.build) with this target's runtime, so the same flags and checks (zero warnings
    at /W4, the port-form C4505 pass, no x87 float arithmetic or SSE min/max) and the same caches: the runtime compiled
    once per content (tc_ppc.c kept unoptimized: at /O1 it has x87 code) and a clean runner kept by the key of all it
    read. Each was rebuilt in every judgement: 1.6-1.7 s a build."""
    return rc.build(cfg, sources, thunk_text, protos, build_dir, include_dirs,
                    runtime=(RUNTIME_RD, RUNTIME_SOURCES, (pathlib.Path(__file__),)))


def run(cfg, harness, build_dir, sets=("dev", "hidden")):
    out = build_dir / "actual.txt"
    which = sets[0] if len(sets) == 1 else "all"
    cmd = [str(build_dir / "rd_runner.exe"), str(cfg["binary_path"]), str(paths.CODE_RANGES),
           str(harness / "vectors.txt"), which, str(out)]
    rc_, log, reason = core_check.run_runner(cmd, cfg["check_timeout_s"], out, core_check.output_cap(harness))
    err = f"runner {reason}" if reason else (f"runner exited with code {rc_}: {log.strip()[-300:]}" if rc_ else None)
    return out, err


def host_stacks(path):
    """{vector id: (lo, hi)} from the runner's HOSTSTACK lines."""
    res, cur = {}, None
    if not pathlib.Path(path).exists():
        return res
    for line in cmp._lines(path):
        t = line.split()
        if t and t[0] == "V":
            cur = int(t[1])
        elif t and t[0] == "HOSTSTACK" and cur is not None:
            res[cur] = (int(t[1], 16), int(t[2], 16))
    return res


def map_stack_args(meta, exp, act, stack, frame_lo=cmp.SP - FRAME_MAX):
    """Replace host-stack pointer arguments by the original's guest-stack pointers when they pair one to one
    (core/readable_check.map_stack_args, for the PowerPC record: r3..r10 and the stack words s0..s7). Returns the
    problems (a guest slot paired with two host slots or the reverse)."""
    if not act or not stack:
        return []
    lo, hi = stack
    callee_regs = {int(c["addr"], 16): c["regs"] for c in meta["callees"]}
    g2h, h2g, bad = {}, {}, []
    for k, (ec, ac) in enumerate(zip(exp["calls"], act["calls"])):
        if ec[0] != ac[0]:
            break
        for reg in callee_regs.get(ec[0], []):
            if reg[0] == "f":
                continue
            i = cmp.REG_INDEX[reg]
            g, h = ec[i], ac[i]
            if not (frame_lo <= g < STACK_HI and lo <= h < hi):
                continue
            if g2h.setdefault(g, h) != h or h2g.setdefault(h, g) != g:
                bad.append(f"call #{k + 1}: argument {reg} points at a different stack slot than in earlier calls")
                continue
            ac[i] = g
    return bad


def compare(cfg, meta, harness, out_file, sets, pair_stack=False):
    """Compare the runner's output with the oracle's: ppc_compare's per-vector comparison (ppc_check), with the
    stack-pointer pairing for readable C (pair_stack)."""
    vecs = cmp.parse_vectors(harness / "vectors.txt")
    exp = cmp.parse_results(harness / "expected.txt")
    act = cmp.parse_results(out_file)
    stacks = host_stacks(out_file) if pair_stack else {}
    sections = cmp.dol_sections(pathlib.Path(cfg["binary_path"]).read_bytes())
    result = {"vectors": {}, "first_diffs": [], "failing": 0, "total": 0}
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            d = map_stack_args(meta, exp[i], act.get(i), stacks.get(i)) if pair_stack else []
            d += cmp.compare_vector(meta, sections, vecs[i], exp[i], act.get(i))
            if d:
                fails.append(i)
                if len(result["first_diffs"]) < 3:
                    result["first_diffs"].append({"vector": i, "set": s, "diffs": d[:8]})
        result["vectors"][s] = {"total": len(ids), "failing": len(fails), "failing_ids": fails[:50]}
        result["failing"] += len(fails)
        result["total"] += len(ids)
    return result


def check_raw(cfg, addr, work_file, build_dir, sets=("dev", "hidden"), harness_dir=None):
    """Run raw (tc_rt.h) matched C through the readable runtime: its own regression test. The runtime implements the
    whole raw API (big-endian rd/wr, tc_call/tc_callf, the ppc_ helpers), so every raw function runs here."""
    harness = pathlib.Path(harness_dir or paths.HARNESS / addr)
    meta = paths.read_json(harness / "meta.json")
    ok, msgs = build(cfg, [work_file], ppc_check.gen_thunk(meta), {}, pathlib.Path(build_dir))
    if not ok:
        return {"verdict": "FAIL", "stage": "build", "build": msgs}
    out, err = run(cfg, harness, pathlib.Path(build_dir), sets)
    result = compare(cfg, meta, harness, out, sets) | {"stage": "compare", "build": msgs}
    if err:
        result["run_error"] = err
    return rc.verdict(result, sets)


RAW_API_PPC = ("rd64", "rdf64", "wr64", "wrf64", "wrf32d")  # besides core/readable_lint.RAW_API


def image_range(dol):
    """[lo, hi) of what the DOL loads: its text and data sections and its bss (named things live there)."""
    secs = cmp.dol_sections(dol)
    bss, bss_size = (int.from_bytes(dol[o:o + 4], "big") for o in (0xD8, 0xDC))
    return min(a for a, _ in secs), max([a + len(d) for a, d in secs] + [bss + bss_size])


def lint_ppc(src, reg, name, proto, image, mirror):
    """The readable rules for this target (core/readable_lint's RR1, RR2, RR7 and the raw rules, then RR3 and RR4
    for GameCube addresses, and core/readable_be's BE2)."""
    from core.readable_lint import readable_lint, void_result_lint
    from core.lint import strip_comments
    code = strip_comments(src)
    errors = [e for e in readable_lint(src, name, proto) if not e.startswith("RR3:")] + void_result_lint(src, reg)
    errors += [f"RR2: '{i}' is the raw runtime API; use pointers, structs and calls by name"
               for i in sorted(set(re.findall(r"[A-Za-z_]\w*", code)) & set(RAW_API_PPC))]
    lo, hi = image
    for lit in re.findall(r"\b0[xX]([0-9a-fA-F]+)", code):
        v = int(lit, 16)
        c = int(mirror.get(f"{v >> 24:02x}", f"{v >> 24:02x}"), 16) << 24 | v & 0xFFFFFF
        if lo <= c < hi:
            errors.append(f"RR3: guest address 0x{v:08x} written as a number; name it in the registry")
    for raw in sorted(set(re.findall(r"\bfn_(8[01][0-9a-f]{6})\b", code))):
        e = reg.functions.get(raw)
        if e and readable.parse_proto(e["proto"])[1] != f"fn_{raw}":
            errors.append(f"RR4: fn_{raw} is named now; call it by its name with its prototype: `{e['proto']}`")
    return sorted(set(errors + readable_be.source_lint(src, reg.typedefs)))


def data_functions(addr, meta):
    """Original functions the matched raw C uses as data (a function pointer it stores or passes), other than its
    callees: game.h declares them (core/readable_check.data_functions, for GameCube addresses)."""
    f = paths.MATCHED / f"{addr}.c"
    if not f.exists():
        return []
    known = {c["addr"] for c in meta["callees"]} | {addr}
    found = {m.lower() for m in re.findall(r"0x(8[01][0-9a-fA-F]{6})", f.read_text(encoding="utf-8", errors="replace"))}
    return sorted(a for a in found - known if (paths.HARNESS / a / "meta.json").exists())


def check_readable(cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, harness_dir=None, **_):
    """Full check of one readable function (readable/src/<addr>.c) against the original's test vectors: the
    PowerPC counterpart of core/readable_check.check_readable (same result layout). Lint (the readable rules, BE1 on
    the headers, BE2), build against game.h (core/readable.game_h: every other function a pointer to its guest
    address, every global a dereference of its guest address), run, and compare as raw C, with the stack-pointer
    pairing. Options of the SH-4 judge this target does not have yet (lean, callee_harness, stub) are ignored: the
    full headers are used."""
    from core import retire
    harness = pathlib.Path(harness_dir or paths.HARNESS / addr)
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
        data = data_functions(addr, meta)
        protos = {}
        for a in sorted(set(reg.functions) | set(sigs) | set(data)):
            p = reg.proto(a, raw_sig=sigs.get(a))
            protos[int(a, 16)] = abi.kinds(reg, p)
            if protos[int(a, 16)][0].endswith("*") and a in sigs and a != addr:
                raise readable.RegistryError(f"{readable.parse_proto(p)[1]}: a variadic callee (its variable "
                                             "arguments are not judged on this target yet)")
        thunk = abi.thunk_c(reg, addr, proto)
        game_h = readable.game_h(reg, verify_self=addr, addrs=list(sigs) + data, raw_sigs=sigs)
    except readable.RegistryError as e:
        result["lint"] = [f"registry: {e}"]
        return result
    if not pathlib.Path(src_file).exists():
        result["lint"] = [f"no readable file {src_file}"]
        return result
    src = pathlib.Path(src_file).read_text(encoding="utf-8", errors="replace")
    image = cfg["binary_path"].read_bytes()
    result["lint"] = (readable_be.registry_lint(reg, readable.INCLUDE)
                      + lint_ppc(src, reg, name, proto, image_range(image), retire.mirrors(paths.ROOT)))
    if result["lint"]:
        return result
    result["stage"] = "build"
    gen = build_dir.parent / (build_dir.name + "_inc")
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "game.h").write_text(game_h, encoding="utf-8")
    if reg.isolated:
        (gen / "types.h").write_text("/* empty names registry: no game types */\n", encoding="utf-8")
    ok, msgs = build(cfg, [src_file], thunk, protos, build_dir, include_dirs=(gen,) if reg.isolated
                     else (gen, readable.INCLUDE))
    result["build"] = msgs
    if not ok:
        return result
    out, err = run(cfg, harness, build_dir, sets)
    result |= compare(cfg, meta, harness, out, sets, pair_stack=True) | {"stage": "compare"}
    if err:
        result["run_error"] = err
    return rc.verdict(result, sets)
