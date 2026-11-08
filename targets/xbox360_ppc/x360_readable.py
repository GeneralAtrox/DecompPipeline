"""Readable C for the Xbox 360 target: the readable runtime's build (runtime_rd), raw C run through it (check_raw: the
runtime's own regression test) and the readable judge (check_readable). The Xbox 360 counterpart of
targets/gamecube_ppc/ppc_readable.py, installed through hooks.py (readable_check_raw, readable_check); the parts
that are the same for both PowerPC targets are that module's (the host-stack pairing, the readable lint, the
functions used as data, running the runner), not copies.

The readable runtime is a 64-bit build (core/readable_check.build x64: the raw judge's Visual Studio environment) with
the runner's own image at 0x5a000000 (target.json readable_image_base) and the guest regions at their own addresses:
the heap 0x40000000, the stack 0x70000000, the kernel page r13 points at 0x7fff0000 and the image 0x82000000. So
readable code uses real pointers, structs and named globals and calls other original functions by name (a call to a
guest address faults and is logged as the oracle's mocked callee: runtime_rd/rd_veh.c). Guest memory stays big
endian: readable code reads and writes it through rd_be.h's be_ types (be_get, be_set). Registers are 64-bit: an
integer parameter or result is extended to 64 bits as its type says (x360_readable_abi), and r3 is compared in all
64 bits, as for raw C. The comparison is x360_compare's, as for raw C (x360_check), plus the one difference readable
code forces: a pointer the original passes into its own stack frame matches a pointer into the host stack when the
pairing is one to one within the vector. Nothing else is loosened.

Not carried over (each makes a correct readable function FAIL, never a wrong one pass): variadic callees (refused),
vector (VMX128) arguments across a call (the oracle refuses them too), out-pointers into the caller's frame, stack
addresses stored to memory, lean sandboxes. The stricter judge's CJ/CS/XR lines (core/strict_judge) compare as for
raw C: only when the oracle wrote them.
"""
import pathlib
import re
import sys

from core import paths, readable, readable_be
from core import readable_check as rc

import x360_check
import x360_compare as cmp
import x360_readable_abi as abi

_gc = str(pathlib.Path(__file__).resolve().parent.parent / "gamecube_ppc")
if _gc not in sys.path:
    sys.path.append(_gc)
import ppc_readable as gc  # noqa: E402  (the PowerPC parts both targets share)

RUNTIME_RD = paths.TARGET / "runtime_rd"
RD_SOURCES = ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_be.c")
RUNTIME_SOURCES = [RUNTIME_RD / s for s in RD_SOURCES] + [paths.RUNTIME / "tc_ppc.c"]
FRAME_MAX = 0x1000  # the deepest own frame below the entry r1 that a stack-pointer argument may point into
FN_PAT = r"8[2-9a-f][0-9a-f]{6}"  # an image address (a function, fn_ADDR)
# The raw API besides core/readable_lint.RAW_API: the 64-bit, byte-reversed and float-store forms, the last call's
# other result.
RAW_API_X360 = ("rd64", "rdf64", "wr64", "wrf64", "wrf32d", "rd16r", "rd32r", "wr16r", "wr32r", "tc_lastr", "tc_lastf")


def data_decls(addr, meta):
    """(functions raw.c uses as data that have a harness, {address: `void fn_ADDR(void)`} for catalog functions it
    uses as data that have none). Only the address of such a function is used, so game.h declares it with that
    placeholder and readable C names it; before, it had no name and RR3 refused the number (Too Human 823323b8 passes
    the callback 0x83118888, never prepared in the sample: 2 of the x360-ref sample's 32 lint failures)."""
    data = gc.data_functions(addr, meta, pat=FN_PAT)
    f = paths.MATCHED / f"{addr}.c"
    if not f.exists():
        return data, {}
    known = {c["addr"] for c in meta["callees"]} | {addr} | set(data)
    text = f.read_text(encoding="utf-8", errors="replace")
    used = {m.lower() for m in re.findall(rf"0x(?:[fF]{{8}})?({FN_PAT})", text)} - known
    starts = catalog_starts()
    return data, {a: f"void fn_{a}(void)" for a in sorted(used & starts)}


# A direct call in raw C: what takes its result (rN = / fN = / return / nothing), tc_call or tc_callf, the target.
CALL_SITE = re.compile(r"(?:\b([rf]\d+)\s*=\s*(?:\([^()]*\)\s*)?|\breturn\s+(?:\([^()]*\)\s*)?)?"
                       rf"\btc_call(f?)\(\s*0x(?:[fF]{{8}})?({FN_PAT})u?(?:ll)?\s*,")
ANY_CALL = re.compile(r"\btc_callf?\(")


def call_sites(text):
    """{target: (result, last a.r index, last a.f index)} of raw C's direct calls: result "r" (it takes r3), "f" (f1)
    or "v" (nothing); the argument registers raw C set since the function's entry or its call before (as the oracle's
    CA line counts them for a call through a pointer), in text order; -1 when none. Several calls to one target: the
    widest."""
    out, starts = {}, [m.start() for m in ANY_CALL.finditer(text)]
    for m in CALL_SITE.finditer(text):
        at = text.index("tc_call", m.start())
        seg = text[max((s for s in starts if s < at), default=0):m.start()]
        ri = max((int(x) for x in re.findall(r"\ba\.r\[(\d)\]\s*=(?!=)", seg)), default=-1)
        fi = max((int(x) for x in re.findall(r"\ba\.f\[(\d+)\]\s*=(?!=)", seg)), default=-1)
        res = ("f" if m.group(2) else "r") if (m.group(1) or m.group(0).lstrip().startswith("return")) else "v"
        old = out.get(m.group(3), ("v", -1, -1))
        out[m.group(3)] = (res if res != "v" else old[0], max(ri, old[1]), max(fi, old[2]))
    return out


def callee_sigs(addr, meta):
    """{addr: the signature readable C calls it by} for every function raw C calls directly. A callee keeps its raw
    signature (x360_readable_abi.register_sig), with the result raw C takes when that says void (Ghidra's guess: Too
    Human 82f7d2d0 stores the r3 of fn_8277b880, `void (u32, u32)`). A call target the harness does not list (outside
    the prepared closure: 82981000 calls 0x829bc1c8, 82a4be58 returns 0x82a48470's r3) gets one from the call site:
    the result it takes, a u64 per integer register and an f64 per float register raw C set before it. Before, neither
    could be written: game.h had no declaration or a void one, and the readable runs failed compare."""
    f = paths.MATCHED / f"{addr}.c"
    sites = call_sites(f.read_text(encoding="utf-8", errors="replace")) if f.exists() else {}
    out = {}
    for c in meta["callees"]:
        sig = abi.register_sig(c["signature"])
        res = sites.get(c["addr"], ("v",))[0]
        ret, name, params = abi.parse_proto(sig)
        if ret == "void" and res != "v":
            sig = f"{'f64' if res == 'f' else 'u64'} {name}({', '.join(f'{t} {n}' for t, n in params) or 'void'})"
        out[c["addr"]] = sig
    for a, (res, ri, fi) in sites.items():
        if a in out or a == addr:
            continue
        params = [f"u64 param_r{k + 3}" for k in range(ri + 1)] + [f"f64 param_f{k + 1}" for k in range(fi + 1)]
        out[a] = f"{dict(r='u64', f='f64', v='void')[res]} fn_{a}({', '.join(params) or 'void'})"
    return out


_STARTS = {}


def catalog_starts():
    """The catalog's function addresses (state/catalog.json), read once per process."""
    if "s" not in _STARTS:
        cat = paths.read_json(paths.CATALOG) or {}
        _STARTS["s"] = {e["addr"] for e in (cat.get("functions", []) if isinstance(cat, dict) else cat)}
    return _STARTS["s"]


def build(cfg, sources, thunk_text, protos, build_dir, include_dirs=()):
    """Compile sources + generated thunk and prototype table + the readable runtime into a 64-bit rd_runner.exe:
    core's readable build (core/readable_check.build) with this target's runtime, so the same flags, checks and caches.
    tc_ppc.c computes fmadd fused (TC_FMA_FUSED's default), so a target configured otherwise is refused here."""
    if cfg.get("fma", "two_roundings") != "fused":
        return False, ['the readable runtime computes fmadd fused: target.json "fma" must be "fused"']
    return rc.build(cfg, sources, thunk_text, protos, build_dir, include_dirs,
                    runtime=(RUNTIME_RD, RUNTIME_SOURCES, (pathlib.Path(__file__), pathlib.Path(abi.__file__))),
                    x64=True)


def compare(cfg, meta, harness, out_file, sets, pair_stack=False):
    """Compare the runner's output with the oracle's: x360_compare's per-vector comparison (x360_check), with the
    stack-pointer pairing for readable C (pair_stack: ppc_readable.map_stack_args with this record and stack)."""
    vecs = cmp.parse_vectors(harness / "vectors.txt")
    exp = cmp.parse_results(harness / "expected.txt")
    act = cmp.parse_results(out_file)
    stacks = gc.host_stacks(out_file) if pair_stack else {}
    image = pathlib.Path(cfg["binary_path"]).read_bytes()
    result = {"vectors": {}, "first_diffs": [], "failing": 0, "total": 0}
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails = []
        for i in ids:
            d = (gc.map_stack_args(meta, exp[i], act.get(i), stacks.get(i), frame_lo=cmp.SP - FRAME_MAX,
                                   reg_index=cmp.REG_INDEX, stack_hi=cmp.STACK[1]) if pair_stack else [])
            d += cmp.compare_vector(meta, image, vecs[i], exp[i], act.get(i))
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
    whole raw API (big-endian rd/wr, tc_call/tc_callf/tc_lastr/tc_lastf, the ppc_ helpers), so every raw function
    runs here."""
    harness = pathlib.Path(harness_dir or paths.HARNESS / addr)
    meta = paths.read_json(harness / "meta.json")
    ok, msgs = build(cfg, [work_file], x360_check.gen_thunk(meta), {}, pathlib.Path(build_dir))
    if not ok:
        return {"verdict": "FAIL", "stage": "build", "build": msgs}
    out, err = gc.run(cfg, harness, pathlib.Path(build_dir), sets)
    result = compare(cfg, meta, harness, out, sets) | {"stage": "compare", "build": msgs}
    if err:
        result["run_error"] = err
    return rc.verdict(result, sets)


def image_ranges(image):
    """The guest ranges a readable function must name rather than write as numbers (RR3): the loaded image. The kernel
    page r13 points at and the heap are the vectors' scratch, not named things."""
    return [(cmp.IMAGE_BASE, cmp.IMAGE_BASE + len(image))]


def lint_wide(src, ranges):
    """RR3 for a 64-bit literal: an image address sign-extended to a register's 64 bits (0xffffffff82150af4, as the
    original's lis leaves it) is still a guest address written as a number. ppc_readable.lint_ppc reads the whole
    literal and sees none (found in the first Too Human sample: two agents wrote image addresses so)."""
    from core.lint import strip_comments
    errors = []
    for lit in re.findall(r"\b0[xX]([0-9a-fA-F]+)", strip_comments(src)):
        v = int(lit, 16)
        if v >> 32 == 0xFFFFFFFF and any(lo <= v & 0xFFFFFFFF < hi for lo, hi in ranges):
            errors.append(f"RR3: guest address 0x{v & 0xFFFFFFFF:08x} written as a number (sign-extended to 64 bits); "
                          "name it in the registry")
    return errors


def check_readable(cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, harness_dir=None, **_):
    """Full check of one readable function (readable/src/<addr>.c) against the original's test vectors: the Xbox 360
    counterpart of ppc_readable.check_readable (same result layout and steps: lint, build against game.h, run, compare
    as raw C with the stack-pointer pairing). game.h is the 64-bit one (core/readable.game_h wide); a callee known by its
    raw signature takes 64-bit integers (x360_readable_abi.register_sig). Options of the SH-4
    judge this target does not have yet (lean, callee_harness, stub) are ignored."""
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
        data, placeholders = data_decls(addr, meta)
        sigs = placeholders | callee_sigs(addr, meta) | {addr: meta["signature"]}
        protos = {a: v for a, v in abi.pointer_protos(cmp.parse_results(harness / "expected.txt")).items()}
        for a in sorted(set(reg.functions) | set(sigs) | set(data)):
            p = reg.proto(a, raw_sig=sigs.get(a))
            protos[int(a, 16)] = abi.kinds(reg, p, meta if a == addr else None)
            if protos[int(a, 16)][0].endswith("*") and a in sigs and a != addr:
                raise readable.RegistryError(f"{readable.parse_proto(p)[1]}: a variadic callee (its variable "
                                             "arguments are not judged on this target yet)")
        thunk = abi.thunk_c(reg, addr, proto, meta)
        game_h = readable.game_h(reg, verify_self=addr, addrs=list(sigs) + data, raw_sigs=sigs, wide=True)
    except readable.RegistryError as e:
        result["lint"] = [f"registry: {e}"]
        return result
    if not pathlib.Path(src_file).exists():
        result["lint"] = [f"no readable file {src_file}"]
        return result
    src = pathlib.Path(src_file).read_text(encoding="utf-8", errors="replace")
    image = pathlib.Path(cfg["binary_path"]).read_bytes()
    result["lint"] = (readable_be.registry_lint(reg, readable.INCLUDE)
                      + gc.lint_ppc(src, reg, name, proto, image_ranges(image), retire.mirrors(paths.ROOT),
                                    raw_api=RAW_API_X360, fn_pat=FN_PAT)
                      + lint_wide(src, image_ranges(image)))
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
    out, err = gc.run(cfg, harness, build_dir, sets)
    result |= compare(cfg, meta, harness, out, sets, pair_stack=True) | {"stage": "compare"}
    if err:
        result["run_error"] = err
    return rc.verdict(result, sets)
