"""Readable stage: callee out-pointers into the caller's stack (batch 20; buffers batch 22).

Raw code often passes the address of a stack local to a callee that fills it (Grid_RayQuery(&query, ...), a
TOC buffer, Lib_HostFindFirst's record, CamAngles_Delta's two f32 outputs). The oracle mocks every callee: nothing is
written, and after the call the original reads back what the vector left in its own guest stack, so those bytes act
as the callee's output. Readable C keeps the local on the host stack and passed its host address, so it never saw
them and read garbage (TC 8c046ffa, 8c035af0, 8c04d2c4, 8c04d30e ...).

The judge makes the mocked callee deliver them where the readable call points. For each call the original makes to a
named callee, each non-const pointer parameter of the callee's registry prototype (never `const T *`, a function
pointer or a raw-named callee's u32) whose register the original set to a guest stack address becomes a runner line
`O call target field guest size`. The size (batch 22: buffers):
- the names entry's `"out": {"param": N}` (bytes) or `{"param": "other_param"}` (bytes in that argument; 0: none);
- `void *` / byte pointee with a parameter named size, len, length, bytes or nbytes (or ..._size, ..._len): the
  bytes that argument says at that call (File_Read(file, &header, 10): 8c04e55a);
- a struct or array type: its sizeof (one probe compile of the build's headers);
- a scalar or `void` pointee: at least its sizeof, and as far as the original reads the buffer after the call (bytes
  it did not write itself) in a trace run of the matched raw C (line T, rd_trace.c), up to the next stack address
  a call is passed: Gd_GetToc's `u32 *toc` is a 408-byte TOC (8c049bfc), Grid_CellOfPoint's `s32 *out_cell` three
  words (8c021cba).
The matched raw C runs twice with those lines (`B`: the region painted, two different paints) and prints the bytes
there at the call (the vector's, plus what the original wrote there before the call) and which of them it stored to
(r15: a stored value computed from the paint can keep it, TC 8c0117f4); they become `D` lines, and at
that call the readable run copies them to the host address the readable call passed in the same register (rd_veh.c
out_copy). The readable local then holds what the original's holds after the mocked call, whatever the readable code
wrote into it before (`= {0}`, which MSVC's C4701 often forces). Without the raw run (no matched raw C) the O lines
copy the vector's bytes where the readable code has not written (a painted host stack).
The judge pairs the two pointers one-to-one (readable_check.map_stack_args). A parameter the callee record does not
compare (Gd_GetToc's raw signature is `void`, so its record compares nothing) is compared from then on, when every
call passes a guest stack address there (paired); else it gets nothing. Everything else is compared as before: a
call that passes another pointer, or none, gets other bytes or none, and the vectors decide. The delivered bytes are
the vector's own, so nothing the judge saw before is hidden.
"""
import collections
import hashlib
import json
import os
import pathlib
import re
import threading

from . import readable_abi
from .compare import FRAME_LO, REG_INDEX, SP, STACK_HI
from .readable import RegistryError, is_variadic, parse_proto

MAX_OUT = 0x2000  # rd_veh.c out_add, rd_trace.c
_SIZES, _LOCK = {}, threading.Lock()
SIZE_NAME = re.compile(r"(?:\w*_)?(?:size|len|length|n?bytes)", re.I)
BYTE_TYPES = ("void", "u8", "s8", "char", "unsigned char", "signed char")
# size: None (sizeof base), "trace" (sizeof base or 0, and the original's reads), int bytes, ("arg", field)
Spec = collections.namedtuple("Spec", "addr field base size reg compared", defaults=(None, None, True))


def pointee(ctype):
    """The object type a writable single-level pointer parameter points at, else None."""
    t = " ".join(ctype.replace("*", " * ").split())
    if not t.endswith("*") or t.count("*") != 1 or "(" in t:
        return None
    base = t[:-1].strip()
    words = base.split()
    if "const" in words or base in ("void", "volatile void"):
        return None
    return base


def _field(storage):
    if storage.startswith("stack+"):
        return f"s{int(storage[6:]) // 4}" if int(storage[6:]) < 32 else None
    return readable_abi.arg_storage(storage)


def _size_rule(reg, ctype, name, params, place, explicit):
    """(pointee, size rule) of a parameter, or None when it gets nothing."""
    base = pointee(ctype)
    if base is None and " ".join(ctype.replace("*", " * ").split()) == "void *":
        base = "void"
    if base is None:
        return None
    if name in explicit:
        v = explicit[name]
        if isinstance(v, str):
            where = {n: _field(s) for (_, n), s in zip(params, place)}.get(v)
            return (base, ("arg", REG_INDEX[where])) if where in REG_INDEX and not where.startswith("fr") else None
        return (base, v) if isinstance(v, int) and v > 0 else None
    if base in BYTE_TYPES:
        for (t, n), s in zip(params, place):
            f = _field(s)
            if n != name and SIZE_NAME.fullmatch(n) and "*" not in t and f in REG_INDEX and not f.startswith("fr"):
                return base, ("arg", REG_INDEX[f])
    if base == "void":
        return base, "trace"
    try:
        reg.kind(base)  # a scalar (a struct or union typedef raises: passed by value)
    except RegistryError:
        return base, None
    return base, "trace"


def out_params(reg, meta, storage_of):
    """[Spec]: the writable pointer parameters of named callees. storage_of(callee record) -> its own harness storage
    or None (callee_storage). compared: the record compares that register (else see paired)."""
    out = []
    for c in meta["callees"]:
        e = reg.functions.get(c["addr"])
        if not e or is_variadic(e["proto"]) or c["addr"] == meta.get("addr"):
            continue
        params = parse_proto(e["proto"])[2]
        storage = storage_of(c)
        try:
            place = readable_abi.callee_place(reg, c, e["proto"], storage)
        except RegistryError:
            continue
        explicit = e.get("out") if isinstance(e.get("out"), dict) else {}
        for (t, n), s in zip(params, place):
            name = _field(s)
            if name not in REG_INDEX or name.startswith("fr"):
                continue
            compared = name in c["regs"]
            if not compared and storage is not None and s not in storage.values():
                continue  # the callee's own harness does not take it: its readable C cannot read it
            rule = _size_rule(reg, t, n, params, place, explicit)
            if rule:
                out.append(Spec(c["addr"], REG_INDEX[name], rule[0], rule[1], name, compared))
    return out


def paired(meta, exp, specs, frame_lo=FRAME_LO):
    """(specs, meta to compare with): a spec whose register the callee record does not compare is kept, and that
    register compared, when every recorded call to the callee passes a guest stack address there; else dropped."""
    keep, add = [], {}
    for s in specs:
        s = s if isinstance(s, Spec) else Spec(*s)
        if s.compared:
            keep.append(s)
            continue
        calls = [rec for res in exp.values() for rec in res.get("calls") or [] if rec[0] == int(s.addr, 16)]
        if calls and all(frame_lo <= rec[s.field] < STACK_HI for rec in calls):
            keep.append(s)
            add.setdefault(s.addr, []).append(s.reg)
    if not add:
        return keep, meta
    callees = [dict(c, regs=c["regs"] + [r for r in add[c["addr"]] if r not in c["regs"]]) if c["addr"] in add else c
               for c in meta["callees"]]
    return keep, dict(meta, callees=callees)


def lines_for(exp, specs, size_of, extents=None, frame_lo=FRAME_LO):
    """{vector id: ["O n target field guest size", ...]} for the original's calls (exp: parse_results) whose spec
    field holds a guest stack address. size_of: pointee type -> size or None (an unknown size copies nothing);
    extents: {(vector, call, target, field): bytes the original reads there after the call} (trace_lines' run)."""
    by_target = {}
    for s in specs:
        s = s if isinstance(s, Spec) else Spec(*s)
        by_target.setdefault(int(s.addr, 16), []).append(s)
    out = {}
    for vid, res in exp.items():
        for n, rec in enumerate(res.get("calls") or []):
            for s in by_target.get(rec[0], ()):
                g, size = rec[s.field], size_of(s.base) if s.base != "void" else None
                if isinstance(s.size, int):
                    size = s.size
                elif isinstance(s.size, tuple):
                    size = rec[s.size[1]]
                elif s.size == "trace":
                    size = max(size or 0, (extents or {}).get((vid, n, f"{rec[0]:08x}", s.field), 0))
                if frame_lo <= g < STACK_HI and size and size <= MAX_OUT and g + size <= STACK_HI:
                    out.setdefault(vid, []).append(f"O {n} {rec[0]:08x} {s.field} {g:08x} {size}")
    return out


def trace_lines(exp, specs, frame_lo=FRAME_LO):
    """{vector id: ["T n target field guest span"]} for the "trace" specs' calls: span up to the next guest stack
    address any call of the vector passes (another object), at most the entry r15 and MAX_OUT."""
    want = {}
    for s in specs:
        s = s if isinstance(s, Spec) else Spec(*s)
        if s.size == "trace":
            want.setdefault(int(s.addr, 16), []).append(s.field)
    out = {}
    for vid, res in exp.items():
        calls = res.get("calls") or []
        passed = {v for rec in calls for v in rec[1:21] if frame_lo <= v < STACK_HI}
        for n, rec in enumerate(calls):
            for field in want.get(rec[0], ()):
                g = rec[field]
                if not frame_lo <= g < SP:
                    continue
                end = min([v for v in passed if v > g] + [SP, g + MAX_OUT])
                out.setdefault(vid, []).append(f"T {n} {rec[0]:08x} {field} {g:08x} {end - g}")
    return out


def parse_ot(text):
    """{(vector, call, target, field): end} from a trace run's OT lines."""
    out, cur = {}, None
    for line in text.splitlines():
        t = line.split()
        if t[:1] == ["V"]:
            cur = int(t[1])
        elif t[:1] == ["OT"] and len(t) == 5 and cur is not None:
            out[(cur, int(t[1]), t[2], int(t[3]))] = int(t[4])
    return out


SIZES_KEPT = 8  # probe results kept per function (build/ADDR/type_sizes.json): the latest


def _sizes_key(cfg, types, include_dirs):
    """The sha256 of everything the size probe reads: the type names, every file of each include folder (by
    position, name and content), the 32-bit compiler (path, size, mtime) and its environment, the flags, and the
    probe's code (global_extents, this file)."""
    import shutil
    from . import readable_check as rcheck
    env = rcheck.msvc_env_x86(cfg)
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), "")) or ""
    st = os.stat(cl) if cl else None
    h = hashlib.sha256(repr(("type sizes 1", rcheck.C_FLAGS, sorted(types), (cl, st and st.st_size,
                                                                              st and st.st_mtime_ns),
                             *(next((v for k, v in env.items() if k.upper() == n), "") for n in ("INCLUDE", "LIB")))
                            ).encode())
    core = pathlib.Path(__file__).parent
    for p in (core / "global_extents.py", core / "readable_outptr.py"):
        h.update(b"file " + p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    for k, d in enumerate(include_dirs):
        d = pathlib.Path(d)
        for f in sorted((f for f in d.rglob("*") if f.is_file()), key=lambda f: f.relative_to(d).as_posix()):
            h.update(f"inc {k} {f.relative_to(d).as_posix()}".encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def type_sizes(cfg, types, build_dir, include_dirs):
    """{type: sizeof} for C type names, by one probe compile (core/global_extents.sizes) with the build's headers;
    a type the compiler refuses (incomplete, unknown) is left out. Kept by the key of all the probe reads
    (_sizes_key) in this process and in the function's build/ADDR/type_sizes.json (build_dir's parent, shared by its
    build folders and later runs): the official judge of the headers the agent's last check built, and verify, take
    the sizes instead of compiling the probe again."""
    from . import global_extents, paths
    key = _sizes_key(cfg, types, include_dirs)
    with _LOCK:
        if key in _SIZES:
            return dict(_SIZES[key])
    cache_file = pathlib.Path(build_dir).parent / "type_sizes.json"

    def kept():
        try:
            return paths.read_json(cache_file, {}) or {}
        except (OSError, ValueError):  # a file cut short by a kill: probed again
            return {}
    sizes = kept().get(key)
    if sizes is None:
        got, _ = global_extents.sizes(cfg, [(t, f"{t} rdo_value") for t in sorted(types)], build_dir, include_dirs)
        sizes = {t: n for t, n in got.items() if n}
        cache = {k: v for k, v in kept().items() if k != key}  # another folder's sizes too
        cache[key] = sizes
        cache = dict(list(cache.items())[-SIZES_KEPT:])
        tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}_{threading.get_ident()}")
        try:
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, cache_file)  # whole: a reader never sees half a file
        except OSError:  # Windows refused (a reader has it open): not kept, probed again next time
            tmp.unlink(missing_ok=True)
    with _LOCK:
        _SIZES[key] = sizes
    return dict(sizes)


def with_lines(src, extra, dst, after_v=False):
    """Write src's vectors with each vector's extra lines ({vector id: [line]}) before its E line (after_v: right after
    its V line, before its M lines) to dst."""
    out, cur = [], None
    for line in src.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.startswith("V "):
            cur = int(line.split()[1])
        elif line.startswith("E") and cur in extra and not after_v:
            out += [x + "\n" for x in extra[cur]]
        out.append(line)
        if line.startswith("V ") and after_v and cur in extra:
            out += [x + "\n" for x in extra[cur]]
    dst.write_text("".join(out), encoding="utf-8")
    return dst


def canary(a):
    """rd_veh.c rd_canary of a 32-bit address (the runtime's uintptr_t arithmetic wraps at 32 bits)."""
    return (0x91 + ((a * 37 + (a >> 7) * 11) & 0xFFFFFFFF) % 0x60) & 0xFF


FLIPS = (0x00, 0x5A)  # the raw runs' paints: canary(a) ^ flip (two, so a write equal to one paint is still seen)


def deliver(runs, guest, mem):
    """A D line's bytes from the raw runs' bytes at guest (runs: [(ob, flip)], ob a parse_ob value: the hex, or
    (hex, stores) with the runtime's mask of the bytes the original stored to; each run painted the region with
    canary ^ flip before the vector's M lines): a byte that still holds the paint in every run (a byte the vector
    leaves alone) or the vector's byte (one it pokes) the original did not write before the call, and reads as the
    vector has it (mem.initial: 0 where the vector leaves it alone). Any other byte the original wrote: "xx" (the
    readable code's byte stays). One paint alone cannot tell a write of the paint's own value (TC 8c04d900 copies a
    vector's random rodata byte into the buffer, once equal to the paint); the original writes the same in both runs,
    so it differs from one of the two paints. A write equal to the byte the vector poked there reads as not written
    (the same byte either way). A byte the original stored to is written whatever it holds: a value computed from the
    painted bytes themselves keeps the paint where the arithmetic leaves it (TC 8c0117f4: yaw = the first
    CamAngles_FromDir output + a lag of -2 keeps the paint's upper bytes in both runs, and read as unwritten, the
    readable's yaw got the vector's zeros there)."""
    runs = [((ob, None) if isinstance(ob, str) else ob, flip) for ob, flip in runs]
    out = []
    n = min(len(h) for (h, _), _ in runs) // 2
    for k in range(n):
        a = guest + k
        init, poked = mem.initial(a), mem_poked(mem, a)
        unwritten = all(int(h[2 * k:2 * k + 2], 16) == (init if poked else canary(a) ^ flip)
                        and not (st and st[k:k + 1] == "1") for (h, st), flip in runs)
        out.append(f"{init:02x}" if unwritten else "xx")
    return "".join(out)


def mem_poked(mem, a):
    return any(base <= a < base + len(data) for base, data in mem.patches)


def parse_ob(text):
    """{vector id: {(call, target, field): hex, or (hex, stores)}} from a raw run's OB lines (stores: one 0/1 per
    byte, 1 where the original stored to it; rd_veh.c out_copy)."""
    out, cur = {}, None
    for line in text.splitlines():
        t = line.split()
        if t[:1] == ["V"]:
            cur = int(t[1])
        elif t[:1] == ["OB"] and len(t) in (5, 6) and cur is not None:
            out.setdefault(cur, {})[(int(t[1]), t[2], int(t[3]))] = t[4] if len(t) == 5 else (t[4], t[5])
    return out
