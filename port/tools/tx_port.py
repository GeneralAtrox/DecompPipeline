"""Translator C in the port (game.json "translator_c", gen_port.py --tx): the translator's judged C for every function
readable C does not cover, so a port runs past where agents have written readable C.

The C is the translator's port form (sh4translate translate_function sp_input: the body takes r15 and PR as inputs,
and the function the judge calls passes the judge's own entry values), written and judged by the translator's
tools/dp_judge.py --sp-input --out DIR: DIR/translated_sp/<game>/ADDR.c, and DIR/results_translated_sp_<game>*.json
with each file's verdict and sha256. The port compiles a file only when its row says PASS and the file on disk still
has that sha256: the very text the judge passed, never edited.

Per function (pick): game.json hardware and wrap wrappers, kit SDK modules and the host CRT first (as today); then
mode "mix": a passing readable file (readable/src/ADDR.c with a `readable` row in state/readable.json), else the
translator's C (and for game.json tx_first's functions); mode "only": the translator's C, readable C only where it has
none; else a logging stub.

The glue, generated here:
- inbound (tx_ADDR.c, one per function): the judged file #included with its own function renamed, and the
  registry's name defined over it with the registry prototype's argument kinds: it reserves a frame on the guest
  stack (runtime/tx_rt.c), puts the stack arguments where the original reads them (sp+offset) and calls body();
- outbound (txcall_NN.c): {guest address, glue} rows for tc_call: each glue unpacks the TcArgs registers into the
  callee's registry prototype (storages from core/readable_abi) and calls it by name (a game.json wrapper where one
  wraps it). tc_call takes any other address to the dispatch table's function with the argument words.
"""
import hashlib
import json
import pathlib
import re

MODES = ("mix", "only")
DYNAMIC = 0x2000   # a frame the text cannot bound (a computed stack move, a self call): 8 KB
MARGIN = 64        # bytes past the bound, and the word an outgoing call's argument area may reach
ARGS = 32          # the 8 stack argument words s0..s7 above a callee's sp (tc_rt.h TcArgs.s)
CHUNK = 180
# The C types a glue uses for each core/readable Registry.kind: 32-bit words, floats, the narrow ints as themselves
CTYPE = {"f": "f32", "c": "s8", "C": "u8", "h": "s16", "H": "u16", "i": "u32", "L": "u64", "v": "void"}
DEF = re.compile(r"(static\s+)?\b(void|u8|s8|u16|s16|u32|s32|u64|f32)\s+(\w+)\s*\(([^()]*)\)\s*\{")
R15 = re.compile(r"\br15\s*((?:[-+*/%&|^]|<<|>>)?)=(?!=)\s*([^;,]*)")
COMMENT = re.compile(r"/\*.*?\*/", re.S)
RESULTS = re.compile(r"results_translated_sp_(.+?)(?:_all)?(?:_part\d+of\d+)?\.json")  # _all: dp_judge --all


class GlueError(Exception):
    pass


def judged(tx_dir):
    """({addr: path} of the judged files a port may compile, {addr: why not} of the others). A later results file
    (by time) replaces an earlier one's row for the same function."""
    rows = {}
    for f in sorted(tx_dir.glob("results_translated_sp_*.json"), key=lambda p: p.stat().st_mtime_ns):
        m = RESULTS.fullmatch(f.name)
        for r in json.loads(f.read_text(encoding="utf-8")):
            rows[r["addr"]] = (r, m.group(1))
    ok, why = {}, {}
    for a, (r, game) in sorted(rows.items()):
        p = tx_dir / "translated_sp" / game / f"{a}.c"
        if r.get("verdict") != "PASS":
            why[a] = r.get("verdict") or "no verdict"
        elif not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != r.get("sha256"):
            why[a] = "changed since judged (no sha256 match)"
        else:
            ok[a] = p
    return ok, why


def store_queues(text):
    """A judged file that writes through the SH-4 store queues. Its model (a pref sends the queue to the RAM block)
    is the port's: the store-queue window is RAM area 3 (runtime/port.h), where the Kamui layer reads the stream."""
    return "sq_start(" in text


def pick(mode, readable, passing, tx, platform, sq=frozenset()):
    """(readable files to compile, translator files to compile): readable: every readable/src file the port would
    compile today; passing: those with a `readable` row; tx: the judged translator files; platform: the functions
    the game's own sources, the kit or the host CRT define (never either); sq: the translator files that write
    through the store queues, which mode "only" leaves to a readable file where there is one."""
    tx = {a for a in tx if a not in platform}
    if mode == "only":
        tx -= set(sq) & set(readable)
        return sorted(set(readable) - tx), sorted(tx)
    # readable C that passes; translator C elsewhere; a readable file without either (stale, and no translator C
    # the port can take) as today's build compiles it
    use_rd = sorted((set(passing) & set(readable)) | (set(readable) - tx))
    return use_rd, sorted(tx - set(use_rd))


def frame_bound(text):
    """Bytes below its entry sp that a judged body can write: every `r15 -= K` (and `r15 += K` with K negative)
    summed, which covers any path through it; DYNAMIC for a computed stack move or a call of itself."""
    code = COMMENT.sub(" ", text)
    if re.search(r"\bbody\s*\(\s*r15\b", code):
        return DYNAMIC
    n = 0
    for op, rhs in R15.findall(code):
        rhs = rhs.strip()
        k = re.fullmatch(r"(0x[0-9a-f]+|\d+)u", rhs)
        if op == "" and rhs in ("sp", "0"):
            continue
        if op in ("-", "+") and k:
            v = int(k.group(1), 0)
            n += v if op == "-" else (1 << 32) - v if v & 0x80000000 else 0
        else:
            return DYNAMIC
    return min(n, DYNAMIC) + MARGIN


def definitions(text):
    """(body's (ret, parameters), the judged function's (ret, name, parameters)) of a judged file; parameters are
    [(type, name)]. The port form's body returns r0 (u32) where DP's signature returns nothing."""
    body = outer = None
    for m in DEF.finditer(COMMENT.sub(" ", text)):
        ps = [tuple(p.split()) for p in m.group(4).split(",") if p.strip() and p.strip() != "void"]
        if m.group(3) == "body":
            body = (m.group(2), ps)
        elif not m.group(1):
            outer = (m.group(2), m.group(3), ps)
    if body is None or outer is None or len(body[1]) < 2:
        raise GlueError("not the port form (no body(sp, ret_addr, ...) and judged function)")
    return (body[0], body[1][2:]), outer


def ctype(reg, t):
    k = reg.kind(t)
    if k not in CTYPE:
        raise GlueError(f"no glue for the type {t!r}")
    return k, CTYPE[k]


def convert(value, have, want):
    """value (C type have) as the C type want: a float and a word move as bits (port_tx_u2f, port_tx_f2u)."""
    if have == want:
        return value
    if want == "f32":
        return f"port_tx_u2f((u32){value})"
    if have == "f32":
        return f"({want})port_tx_f2u({value})"
    return f"({want}){value}"


KEPT = "returns the body's r0 where the registry returns nothing"
TAIL_CALL = re.compile(r"(?:r0 = tc_call|fr0 = tc_callf)\(")
INSN = re.compile(r"[0-9a-f]{8}\s+(\S+)\s*([^;]*)")


def writes_result(insn, ops, fpu):
    """Whether one disassembled instruction may write the result register (r0, or fr0 for a float)."""
    ops = [o.strip() for o in ops.split(",")] if ops.strip() else []
    if any(re.fullmatch(r"@r0\+|@-r0", o) for o in ops):
        return True
    if fpu:
        return insn == "frchg" or bool(ops) and ops[-1] in ("fr0", "dr0", "xd0", "fv0")
    return bool(ops) and ops[-1] == "r0"


def tail_return(text, fpu):
    """Whether every exit of a judged body that the translator wrote as returning nothing returns what its last call
    returned: each `return;` follows a tc_call with no branch between and no instruction after the call that writes
    the result register (the instruction comments say; the translator leaves out a write the judge never reads)."""
    m = re.search(r"static (?:void|u32) body\(", text)
    start = m.start() if m else -1
    end = text.find("\n}", start)
    if start < 0 or end < 0:
        return False
    lines = [ln.strip() for ln in text[start:end + 2].splitlines()]
    exits = [i for i, ln in enumerate(lines) if ln in ("return;", "return r0;")]
    last = len(lines) - 1
    if not exits or any(ln and ln != "}" for ln in lines[exits[-1] + 1:last]):
        return False
    for i in exits:
        j = i - 1
        while j >= 0 and not TAIL_CALL.match(lines[j]):
            ln = lines[j]
            if ln.startswith("/*"):
                for insn, ops in INSN.findall(ln[2:-2]):
                    if writes_result(insn, ops, fpu):
                        return False
            elif re.match(r"[{}]|.*[{}]\s*$|(if|while|do|for|goto|case|default)\b|\w+:", ln):
                return False
            elif re.match(r"fr0\b" if fpu else r"r0\b", ln):
                return False
            j -= 1
        if j < 0:
            return False
    return True


def meta_storages(meta):
    return [p["storage"] if p["storage"] != "stack" else f"stack+{p['stack_offset']}" for p in meta["params"]]


def inbound_c(reg, readable_abi, addr, proto, meta, src, text, prefix=""):
    """tx_ADDR.c: the judged file and the registry's function over its body. prefix: an overlay workspace's
    (tools/gen_overlays.py): both defined names take it (PREFIX__name, PREFIX__txj_ADDR), so units over the same
    addresses never share a symbol; the file is compiled without the workspace's prefix.h."""
    from core.readable import is_variadic, parse_proto
    if is_variadic(proto):
        raise GlueError("a variadic prototype")
    ret, name, params = parse_proto(proto)
    sym, txj = (f"{prefix}__{name}", f"{prefix}__txj_{addr}") if prefix else (name, f"txj_{addr}")
    st = readable_abi.own_storages(reg, addr, proto, meta)
    (bret, bparams), (oret, oname, _) = definitions(text)
    mst = meta_storages(meta)
    # after DP's parameters, the port form's in_REG ones: an argument register the body only passes on
    extra = [(bt, n) for bt, n in bparams[len(mst):] if n.startswith("in_")]
    bparams = bparams[:len(bparams) - len(extra)]
    if len(mst) != len(bparams):
        raise GlueError(f"meta has {len(mst)} parameters, the judged body {len(bparams)}")
    rk, rt = ctype(reg, ret)
    typed = [(ctype(reg, t)[1], f"a{i}") for i, (t, _) in enumerate(params)]
    have = {s: (c, n) for (c, n), s in zip(typed, st) if s != "none"}
    args, stores, top = [], [], ARGS
    for (bt, _), s in zip(bparams, mst):
        c, n = have.get(s, (bt, "0"))
        args.append(convert(n, c, bt))
        if s.startswith("stack+"):
            off = int(s[6:])
            top = max(top, off + 4)
            stores.append(f"    port_tx_wr32(sp + {off}u, {convert(n, c, 'u32')});")
    # an unlisted float register: as the FPU state holds it (readable C keeps hidden ones there); r0..r3: as the
    # caller's translator C had them at its tc_call (port_tx_take_rlo; 0 from readable C)
    lo = [x for _, x in extra if x in ("in_r0", "in_r1", "in_r2", "in_r3")]
    for bt, x in extra:
        c, n = have.get(x[3:], (bt, f"sh4_fr({x[5:]})" if x.startswith("in_fr") else
                                f"lo[{x[4:]}]" if x in lo else "0"))
        args.append(convert(n, c, bt))
    for s, (c, n) in sorted(have.items()):  # a stack argument the raw signature leaves out: still where it is read
        if s.startswith("stack+") and s not in mst:
            top = max(top, int(s[6:]) + 4)
            stores.append(f"    port_tx_wr32(sp + {int(s[6:])}u, {convert(n, c, 'u32')});")
    m = re.search(r"\bbody\(\s*0x[0-9a-f]+u\s*,\s*(0x[0-9a-f]+u)", COMMENT.sub(" ", text))
    if not m:
        raise GlueError("the judged function's call of body() names no return address")
    call = f"body(sp, {m.group(1)}" + "".join(f", {x}" for x in args) + ")"
    frame = frame_bound(text)
    # a function the registry says returns nothing still hands back the body's r0 (the original leaves it in r0, and
    # a caller through a function pointer may read it): defined returning u32, which the cdecl ABI keeps compatible
    # with the void declaration elsewhere (this file does not include game.h)
    keep = rk == "v" and bret == "u32"
    rt = "u32" if keep else rt
    lines = [f"/* Generated by port/tools/tx_port.py: {name} ({addr}) from the translator's judged C"
             f"{'; ' + KEPT if keep else ''}. */",
             '#include "tx_rt.h"', f"#define {oname} {txj}", f'#include "{src.as_posix()}"', f"#undef {oname}",
             # a body that never returns (a main loop) leaves the glue after its call unreachable (C4702 at /W4)
             "#pragma warning(disable : 4702)",
             "", f"{rt} {sym}({', '.join(f'{c} {n}' for c, n in typed) or 'void'});",
             f"{rt} {sym}({', '.join(f'{c} {n}' for c, n in typed) or 'void'})", "{",
             f"    u32 sp = port_tx_enter({frame}u, {top}u);"]
    # whether the body keeps the whole FPU as state (it reads fr0..fr11 from sh4_fr): tc_call then leaves the FPU
    # state alone; else it is the body's locals that hold fr4..fr11 at a call
    whole = int(bool(re.search(r"\bfr\d+ = sh4_fr\((?:[0-9]|1[01])\);", text)))
    lines += [f"    int fpu = port_tx_fpu_whole({whole});"]
    lines += ["    u32 lo[4];", "    port_tx_take_rlo(lo);"] if lo else []
    lines += [f"    (void){n};" for _, n in typed]  # a registry argument the original does not read
    lines += stores
    if keep:
        lines += [f"    u32 r = {call};"]
    elif rk == "v":
        lines += [f"    {call};" if oret == "void" else f"    (void){call};"]
    elif oret == "void" and bret == "u32" and rk != "f":  # the body's r0
        lines += [f"    {rt} r = {convert(call, 'u32', rt)};"]
    elif oret == "void":
        if not tail_return(text, rk == "f"):
            raise GlueError(f"the registry returns {ret}, the judged function nothing")
        last = "port_tx_last_fr0()" if rk == "f" else "port_tx_last_r0()"
        lines += [f"    {rt} r;", f"    {call};", f"    r = {convert(last, 'f32' if rk == 'f' else 'u32', rt)};"]
    else:
        lines += [f"    {rt} r = {convert(call, oret, rt)};"]
    lines += ["    port_tx_fpu_whole(fpu);", f"    port_tx_leave(sp, {top}u);"]
    lines += ([] if rk == "v" and not keep else ["    return r;"]) + ["}"]
    return "\n".join(lines) + "\n"


def outbound_glue(reg, readable_abi, addr, proto, meta, callee, seen=None, kept=False):
    """The glue for tc_call(addr): unpacks the TcArgs block into proto's arguments and calls callee (its name, or
    the game.json wrapper's). seen: a caller's meta entry for addr, where it has no meta of its own (an SDK helper
    that takes r1, r2): its registers place the prototype as they do for readable C. kept: callee is translator C
    that hands back r0 though the registry returns nothing (inbound_c)."""
    from core.readable import is_variadic, parse_proto
    ret, _, params = parse_proto(proto)
    if meta:
        st = readable_abi.own_storages(reg, addr, proto, meta)
    elif seen:
        st = readable_abi.callee_place(reg, seen, proto)
    else:
        st = readable_abi.override(reg, addr, proto) or readable_abi.storages(reg, proto)
    args, last = [], -4
    for (t, _), s in zip(params, st):
        k = reg.kind(t)
        if k not in CTYPE or k in "Lv":
            raise GlueError(f"no glue for the type {t!r}")
        word = ("0" if s == "none" else f"a->fr[{int(s[2:]) - 4}]" if s.startswith("fr")
                else f"a->r[{int(s[1:]) - 4}]" if s.startswith("r") and int(s[1:]) >= 4
                else f"a->rlo[{int(s[1:])}]" if s.startswith("r") else f"a->s[{int(s[6:]) // 4}]")
        if s.startswith("stack+"):
            last = max(last, int(s[6:]))
            if int(s[6:]) >= ARGS:  # past the 8 words a call carries: on the caller's guest stack (tx_rt.c)
                word = f"port_tx_stack_word(a, {s[6:]}u)"
        if s.startswith("fr"):
            word = word if k == "f" else f"port_tx_f2u({word})"
        elif k == "f":
            word = f"port_tx_u2f({word})"
        args.append(word if k == "f" else f"({t})(uintptr_t){word}" if k == "i" else f"({t}){word}")
    if is_variadic(proto):  # the words after the last named one, as cdecl words (a double is two of them)
        args += [f"a->s[{k}]" for k in range(last // 4 + 1, ARGS // 4)]
    call = f"{callee}({', '.join(args)})"
    rk = reg.kind(ret)
    if kept and rk == "v":  # the r0 its body returns
        call = f"((u32 (*)({', '.join(t for t, _ in params) or 'void'})){callee})({', '.join(args)})"
        rk = "kept"
    # a callee that returns nothing leaves r0 as it was (the original may call through r0 again after it); a float
    # comes back as r0's bits too (the judged C reads `fr0 = u2f(r0)`) and in the FPU state's fr0
    body = {"v": f"{call};\n    o->r0 = a->rlo[0];",
            "f": f"o->fr0 = {call};\n    o->r0 = port_tx_f2u(o->fr0);\n    port_tx_set_fr0(o->fr0);",
            "L": f"o->r0 = (u32){call};", "kept": f"o->r0 = {call};"}.get(rk, f"o->r0 = (u32)(uintptr_t){call};")
    return [f"static void g_{addr}(const PortTcArgs *a, PortTcRet *o)", "{", "    (void)a;", "    (void)o;",
            f"    {body}", "}"]


def outbound_chunks(reg, readable_abi, protos, names, metas, wrap, wrapper_proto, tx_texts=None):
    """(txcall_NN.c texts, [addr: why no glue]): every declared function's tc_call glue, in address order."""
    rows, why, out = [], {}, []
    seen = {}
    for m in metas.values():
        for c in m.get("callees", []):
            seen.setdefault(c["addr"], c)
    kept = {a for a, t in (tx_texts or {}).items() if KEPT in t.split("\n", 1)[0]}
    for a in sorted(protos):
        callee = wrap.get(a) or names[a]
        try:
            rows.append((a, outbound_glue(reg, readable_abi, a, protos[a], metas.get(a), callee, seen.get(a),
                                                     a in kept and a not in wrap)))
        except Exception as e:  # noqa: BLE001  (a RegistryError or GlueError: tc_call's word call takes it)
            why[a] = str(e)[:160]
    for i in range(0, len(rows), CHUNK):
        part = rows[i:i + CHUNK]
        text = ["/* Generated by port/tools/tx_port.py: tc_call's glue, guest address -> registry function. */",
                '#include "game.h"', '#include "tx_rt.h"', ""]
        text += [wrapper_proto(protos[a], names[a], wrap[a]) for a, _ in part if a in wrap]
        for _, g in part:
            text += g
        text += ["", f"const PortTcRow port_tx_calls_{i // CHUNK:02d}[] = {{"]
        text += [f"    {{0x{a}u, g_{a}}}," for a, _ in part]
        text += ["};", f"const int port_tx_calls_{i // CHUNK:02d}_n = {len(part)};"]
        out.append("\n".join(text) + "\n")
    idx = ["/* Generated by port/tools/tx_port.py: tc_call's glue chunks, in address order. */", '#include "tx_rt.h"',
           ""]
    idx += [f"extern const PortTcRow port_tx_calls_{i:02d}[];\nextern const int port_tx_calls_{i:02d}_n;"
            for i in range(len(out))]
    idx += ["", "const PortTcChunk port_tx_chunks[] = {"]
    idx += [f"    {{port_tx_calls_{i:02d}, &port_tx_calls_{i:02d}_n}}," for i in range(len(out))]
    idx += ["    {0, 0}", "};", f"const int port_tx_nchunks = {len(out)};"]
    return out, "\n".join(idx) + "\n", why


def settings(cfg, cli_dir=None, cli_mode=None):
    """(folder, mode) of translator C for this build, or None (today's build): gen_port.py --tx/--tx-mode, else
    game.json "translator_c"."""
    tc = cfg.get("translator_c") or {}
    folder = cli_dir or tc.get("dir")
    if not folder:
        return None
    mode = cli_mode or tc.get("mode") or "mix"
    if mode not in MODES:
        raise SystemExit(f"translator C mode {mode!r}: one of {', '.join(MODES)}")
    return pathlib.Path(folder).resolve(), mode


def inbound(reg, readable_abi, paths, ok, platform, prefix=""):
    """({addr: tx_ADDR.c text}, {addr: why no glue}) for the judged files outside the platform functions (prefix:
    inbound_c's, an overlay workspace's)."""
    texts, why = {}, {}
    for a, src in sorted(ok.items()):
        if a in platform:
            continue
        meta = paths.read_json(paths.HARNESS / a / "meta.json")
        try:
            if not meta:
                raise GlueError("no harness meta.json")
            texts[a] = inbound_c(reg, readable_abi, a, reg.proto(a, meta), meta, src, src.read_text(encoding="utf-8"),
                                 prefix)
        except Exception as e:  # noqa: BLE001  (GlueError, RegistryError: the function stays readable or a stub)
            why[a] = str(e)[:160]
    return texts, why


def write(out, texts, write_if_changed):
    """tx/tx_ADDR.c for each text (unchanged files keep their times); an earlier run's other files go."""
    d = out / "tx"
    d.mkdir(exist_ok=True)
    for a, t in texts.items():
        write_if_changed(d / f"tx_{a}.c", t)
    for old in d.glob("tx_*.c"):
        if old.stem[3:] not in texts:
            old.unlink()
    return [d / f"tx_{a}.c" for a in sorted(texts)]
