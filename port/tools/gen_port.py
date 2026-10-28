"""Generate a game's port files from its decomp workspace and game.json: game.h, logging stubs, the guest-address
dispatch table and the kit's per-game files.

    python port/tools/gen_port.py [--game DIR] [--out DIR] [--engine]

--game: the folder with game.json (default: the current folder). Reads (never writes) the decomp workspace
(game.json "decomp") and imports the DecompPipeline core read-only. Writes into --out (default <game>/build/gen):
  include/game.h   every registry function and every unnamed function a readable file needs, as a prototype;
                   every named global as a fixed-address lvalue on its guest address (the image holds its data);
                   the guest C library's heap names (GUEST_CRT) mapped onto the kit's
  include/port_config.h, port_entry.c, port_files.cmake   from game.json (tools/game_config.py)
  stubs_NN.c       a logging stub for every declared function that no readable file, game source, chosen kit SDK
                   module or the host C library defines
  dispatch_NN.c    {guest address, host function, name} for every catalog function and every declared function,
                   sorted, in chunks; dispatch_index.c lists the chunks (runtime/dispatch.c searches them)
  scene_map.c      the scene dump's map from the game's scene_dump.json (tools/scene_map.py)
  fsca_table.c     FSCA's hardware sines, both FPSCR.RM tables, for runtime/sh4_rt.c (core/fsca.py, tools/fsca_rom.py)
  tx/tx_ADDR.c, txcall_NN.c   with translator C (game.json "translator_c", or --tx DIR [--tx-mode mix|only]): each
                   judged translator function the port compiles, and tc_call's glue (tools/tx_port.py)
  gen_report.txt   counts
"""
import argparse
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import game_config  # noqa: E402
import scene_map  # noqa: E402
import tx_port  # noqa: E402

CHUNK_STUBS = 36
CHUNK_ROWS = 180
# Registry names the host C runtime provides (the SHC C library functions a game links; runtime/port_libm.c has the
# float ones the 32-bit CRT only has inline).
CRT = {"acosf", "atoi", "ceilf", "floorf", "memcmp", "memmove", "memset", "memcpy", "rand", "srand", "sinf", "cosf", "tanf",
       "atan2f", "log10f", "powf", "sqrtf", "sprintf", "vsprintf", "strcat", "strchr", "strlen", "strncmp", "strncpy",
       "strcpy", "strcmp", "abs", "fabsf", "qsort", "ldexpf", "sscanf", "strstr"}
# The CRT names runtime/port_libm.c gives way on: the console's single-precision float maths rounds its own way, so
# where the game's own C of one passes the judge (readable, or the translator's in a build with translator C), the
# port compiles that instead of the host's double function (port_config.h PORT_OWN_CRT_name). sqrtf stays the SSE
# root (fsqrt is exact IEEE on both); a game without its own judged C of these keeps the host's, as before.
OWN_CRT = {"sinf", "cosf", "tanf", "acosf", "atan2f", "log10f", "powf", "ldexpf", "floorf", "ceilf"}
# Registry names the guest's own library defines over guest RAM (Shinobi's syMalloc_ object replaces the SHC heap
# functions with entries on the syMalloc allocator): never the host CRT's. game.h maps each onto the kit's name
# (sdk/sy_heap.c), so readable calls, dispatch rows and any logging stub use it and the host CRT keeps its own.
GUEST_CRT = {"malloc": "sy_guest_malloc", "free": "sy_guest_free", "calloc": "sy_guest_calloc",
             "realloc": "sy_guest_realloc"}
# Platform layer (the chosen sdk/ modules and the game's own sources): a non-static definition at column 0 whose
# name is a registry function replaces that function's logging stub.
DEF = re.compile(r"^(?!static\b)[A-Za-z_][\w \t*]*?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*(\{.*)?$")


def defined_names(files):
    """Names defined (non-static, at column 0) in the given C files; a parameter list may run over several lines."""
    out = set()
    for f in files:
        lines = pathlib.Path(f).read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            j = i
            while ln[:1].isalpha() and ln.count("(") > ln.count(")") and j + 1 < len(lines):
                j += 1
                ln += " " + lines[j].strip()
            m = DEF.match(ln)
            if m and m.group(1) not in ("if", "while", "for", "switch", "return"):
                out.add(m.group(1))
    return out


def args_parse():
    p = argparse.ArgumentParser()
    p.add_argument("--game", default=None, help="the folder with game.json (default: the current folder)")
    p.add_argument("--out", default=None, help="default: <game>/build/gen")
    p.add_argument("--engine", action="store_true", help="also compile the engine's readable files (default: stubs)")
    p.add_argument("--tx", default=None, help="translator C: tools/dp_judge.py --sp-input's --out folder")
    p.add_argument("--tx-mode", default=None, choices=tx_port.MODES, help="mix (default): readable C first; only")
    return p.parse_args()


def load_core(decomp, pipeline):
    os.environ["DECOMP_PROJECT"] = str(pathlib.Path(decomp).resolve())
    sys.path.insert(0, str(pathlib.Path(pipeline).resolve()))
    from core import library, paths, readable  # noqa: E402  (the pipeline's own modules, read-only use)
    return library, paths, readable


def global_lines(readable, reg):
    """{"extern DECL;": [typedef, #define]} turning each named global into an lvalue on its guest address."""
    out = {}
    for a, e in reg.globals.items():
        decl = e["decl"].rstrip(";")
        name = readable.global_name(decl)
        td = re.sub(rf"\b{name}\b", f"rdg_{name}_t", decl, count=1)
        out[f"extern {e['decl']};"] = [f"typedef {td};", f"#define {name} (*(rdg_{name}_t *)0x{a}u)"]
    return out


def guest_crt_lines(names):
    """#defines mapping the guest C library names the registry declares onto the kit's (GUEST_CRT)."""
    used = sorted(n for n in set(names) if n in GUEST_CRT)
    if not used:
        return []
    return ["/* The guest's C library heap (GUEST_CRT): guest RAM through the kit, never the host CRT. */"] + [
        f"#define {n} {GUEST_CRT[n]}" for n in used]


def port_game_h(readable, reg, sigs, names=()):
    text = readable.game_h(reg, addrs=sorted(sigs), raw_sigs=sigs)
    repl, lines, done = global_lines(readable, reg), [], 0
    for ln in text.splitlines():
        if ln in repl:
            lines += repl[ln]
            done += 1
        else:
            lines.append(ln)
        if ln == '#include "rd_rt.h"':  # before types.h: a struct field of the same name is renamed everywhere alike
            lines += guest_crt_lines(names)
    if done != len(reg.globals):
        raise SystemExit(f"game.h: {done} of {len(reg.globals)} globals rewritten")
    lines[0] = "/* Generated by port/tools/gen_port.py: do not edit. Globals live at their guest addresses. */"
    return "\n".join(lines) + "\n", done


def stub_c(readable, addr, proto):
    ret, name, params = readable.parse_proto(proto)
    body = [f"{proto.strip().rstrip(';')}", "{"]
    body += [f"    (void){n};" for _, n in params]
    body.append(f'    port_stub(0x{addr}u, "{name}");')
    if ret.strip() != "void":
        body.append("    return 0;")
    body.append("}")
    return body


def write_if_changed(path, text):
    if not path.exists() or path.read_text(encoding="utf-8") != text:  # unchanged: no CMake re-configure or rebuild
        path.write_text(text, encoding="utf-8")


def write_chunks(out, prefix, chunks, head):
    for i, chunk in enumerate(chunks):
        write_if_changed(out / f"{prefix}_{i:02d}.c", "\n".join(head + chunk) + "\n")


def check_names(cfg, names, protos):
    """The entry and the hardware functions game.json names must be the registry's names at those addresses (a
    hardware function at an address the registry does not name, unnamed library code, is checked with the catalog)."""
    entry = cfg["entry"]
    if names.get(entry["addr"]) != entry["name"]:
        raise SystemExit(f"game.json entry {entry['addr']}: the registry names it {names.get(entry['addr'])!r}, "
                         f"not {entry['name']}")
    if not re.search(r"^\s*void\s+\w+\s*\(\s*(void)?\s*\)", protos[entry["addr"]]):
        raise SystemExit(f"game.json entry {entry['name']}: port_entry.c calls it as void(void), the registry "
                         f"has {protos[entry['addr']]}")
    for x, n in cfg["hardware"].items():
        if x in names and names[x] != n:
            raise SystemExit(f"game.json hardware {x}: registry name {names.get(x)!r}, the game's sources define {n}")


def wrap_defines(src, files, wrap, names, pnames, tx=()):
    """game.json wrap {addr: Wrapper}: each readable file that names the wrapped function, other than its own file,
    compiles with Name=Wrapper, so its calls (and the address it takes) go to the game's wrapper, which calls the
    readable function itself. The verified text is compiled unchanged; only the callee's symbol differs."""
    out = {}
    for x, w in sorted(wrap.items()):
        name = names.get(x)
        if not name or (x not in files and x not in tx):
            engine = (pathlib.Path(src) / f"{x}.c").exists()  # past game_end: compiled only with --engine
            raise SystemExit(f"game.json wrap {x}: not a readable function the port compiles ({name!r})"
                             + ("; it is engine code: build with --engine" if engine else ""))
        if w not in pnames:
            raise SystemExit(f"game.json wrap {x}: the game's sources define no {w}")
        word = re.compile(rf"\b{re.escape(name)}\b")
        for f in files:
            if f != x and word.search((src / f"{f}.c").read_text(encoding="utf-8", errors="replace")):
                out.setdefault(f, []).append(f"{name}={w}")
    return out


def no_pch(src, files, defines):
    """The readable files that cannot use the precompiled game.h (CMakeLists.txt): it stands in for a first line of
    #include "game.h", and is built without a file's own Name=Wrapper defines."""
    def first_line(x):
        with open(src / f"{x}.c", encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    return [x for x in files if x in defines or first_line(x) != '#include "game.h"']


def wrapper_proto(proto, name, wrapper):
    return re.sub(rf"\b{re.escape(name)}(?=\s*\()", wrapper, proto.strip().rstrip(";"), count=1) + ";"


ARG_REGS = ("r4", "r5", "r6", "r7")


def pointer_adapter(reg, readable, readable_abi, addr, proto, meta, fn):
    """(name, C) of the dispatch row's adapter, or None. A call through a guest pointer (dispatch.c's thunk) passes the
    argument registers in order: readable C types a pointer call with the registers raw.c fills, r4..r7 first. A
    function whose own parameters skip or reorder them (say r4 and r6) gets an adapter that takes r4..r7
    and calls fn with its own; one with float, stack or variadic parameters keeps its row."""
    try:
        st = readable_abi.own_storages(reg, addr, proto, meta)
        ret, _, params = readable.parse_proto(proto)
        kinds = [reg.kind(t) for t, _ in params]
    except readable.RegistryError:
        return None
    if (readable.is_variadic(proto) or any(s not in ARG_REGS + ("none",) for s in st)
            or any(k not in "cChHi" for k in kinds) or list(st) == list(ARG_REGS[:len(st)])):
        return None
    top = max([ARG_REGS.index(s) + 1 for s in st if s != "none"] + [1])
    args = ["0" if s == "none" else f"({t})(uintptr_t){s}" if "*" in t else f"({t}){s}" for (t, _), s in zip(params, st)]
    unused = "".join(f" (void){r};" for r in ARG_REGS[:top] if r not in st)
    call = f"{fn}({', '.join(args)});"
    name = f"pa_{addr}"
    return name, (f"static {ret} {name}({', '.join(f'u32 {r}' for r in ARG_REGS[:top])}) {{{unused} "
                  f"{call if reg.kind(ret) == 'v' else 'return ' + call} }}")


def own_float_maths(names, have, meta_of):
    """The OWN_CRT functions whose own C the port can take: x and every function its C calls (harness meta.json
    "callees", followed down) have judged C in have. One callee without (e.g. a kernel whose translator C fails)
    keeps the host's function, so the game's C never calls a logging stub."""
    def whole(x, seen):
        if x in seen:
            return True
        seen.add(x)
        return x in have and all(whole(c["addr"], seen) for c in (meta_of(x) or {}).get("callees", []))
    return {x for x, n in names.items() if n in OWN_CRT and whole(x, set())}


def translator_c(cfg, tx, reg, readable, files, paths, engine):
    """(readable files, translator functions, {addr: tx_ADDR.c}, {addr: why not}) for a build with translator C
    (tools/tx_port.py): platform functions first, then readable or translator C as the mode says."""
    from core import lookalike, readable_abi  # noqa: E402
    ok, why = tx_port.judged(tx[0])
    if not engine:  # the skeleton, as for readable C: game code only, the engine stays logging stubs
        game_end = int(paths.config()["game_end"], 16)
        ok = {x: p for x, p in ok.items() if int(x, 16) < game_end}
    pnames = defined_names(cfg["sdk"] + cfg["sources"])
    names = {x: readable.parse_proto(e["proto"])[1] for x, e in reg.functions.items()}
    platform = set(cfg["hardware"]) | {x for x, n in names.items() if n in CRT or GUEST_CRT.get(n, n) in pnames}
    platform -= own_float_maths(names, set(ok) | set(files), lambda x: paths.read_json(paths.HARNESS / x / "meta.json"))
    texts, glue_why = tx_port.inbound(reg, readable_abi, paths, ok, platform)
    why.update(glue_why)
    passing = lookalike.readable_sources(paths.ROOT) - set(cfg.get("tx_first") or [])  # game.json tx_first
    sq = {a for a in texts if tx_port.store_queues(ok[a].read_text(encoding="utf-8"))}
    rd, txs = tx_port.pick(tx[1], files, passing, texts, platform, sq)
    return rd, txs, texts, why


def main():
    a = args_parse()
    cfg = game_config.load(game_config.find_game(a.game))
    if not cfg["decomp"] or not (cfg["decomp"] / "project.json").is_file():
        raise SystemExit(f"game.json decomp: no project.json in {cfg['decomp']}")
    library, paths, readable = load_core(cfg["decomp"], game_config.PIPELINE)
    out = pathlib.Path(a.out) if a.out else cfg["dir"] / "build" / "gen"
    (out / "include").mkdir(parents=True, exist_ok=True)
    stale = list(out.glob("stubs_*.c")) + list(out.glob("dispatch_*.c")) + list(out.glob("txcall_*.c"))
    reg = readable.Registry()
    files = sorted(p.stem for p in readable.SRC.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem))
    game_end = int(paths.config()["game_end"], 16)
    if not a.engine:  # the skeleton: game code only; engine (extra_functions) and SDK stay logging stubs
        files = [x for x in files if int(x, 16) < game_end]
    write_if_changed(out / "port_entry.c", game_config.port_entry_c(cfg))
    from core import fsca  # noqa: E402  (load_core put the pipeline on the path)
    write_if_changed(out / "fsca_table.c", fsca.table_c())
    tx = tx_port.settings(cfg, a.tx, a.tx_mode)
    txs, tx_texts, tx_why = [], {}, {}
    if tx:
        files, txs, tx_texts, tx_why = translator_c(cfg, tx, reg, readable, files, paths, a.engine)
    sigs, notes = library.raw_declarations(reg, files)
    for x in [*txs, *tx_why]:  # a translated (or left-out) function no readable file names: its raw signature
        if x not in reg.functions and x not in sigs and (paths.HARNESS / x / "meta.json").is_file():
            sigs[x] = paths.read_json(paths.HARNESS / x / "meta.json")["signature"]
    declared = sorted(set(reg.functions) | set(sigs))
    protos = {x: reg.proto(x, raw_sig=sigs.get(x)) for x in declared}
    names = {x: readable.parse_proto(p)[1] for x, p in protos.items()}
    game_h, nglob = port_game_h(readable, reg, sigs, names.values())
    write_if_changed(out / "include" / "game.h", game_h)
    write_if_changed(out / "scene_map.c", scene_map.scene_map_c(cfg, game_h, readable.INCLUDE))
    check_names(cfg, names, protos)
    hw = cfg["hardware"]
    defined = set(files) | set(txs)
    own_crt = {x for x in declared if names[x] in OWN_CRT and x in defined}  # the game's own C defines it
    crt = {x for x in declared if names[x] in CRT} - own_crt
    write_if_changed(out / "include" / "port_config.h", game_config.port_config_h(cfg, {names[x] for x in own_crt},
                                                                                 "BUS_BACKUPFILEHEADER" in game_h))
    pnames = defined_names(cfg["sdk"] + cfg["sources"])
    wrap = cfg["wrap"]
    defines = wrap_defines(readable.SRC, files, wrap, names, pnames, txs)
    tx_files, tx_calls = [], []
    if tx:
        from core import readable_abi  # noqa: E402
        tx_files = tx_port.write(out, {x: tx_texts[x] for x in txs}, write_if_changed)
        metas = {x: paths.read_json(paths.HARNESS / x / "meta.json") for x in declared
                 if (paths.HARNESS / x / "meta.json").is_file()}
        chunks, index, call_why = tx_port.outbound_chunks(reg, readable_abi, protos, names, metas, wrap, wrapper_proto,
                                                             {x: tx_texts[x] for x in txs})
        write_chunks(out, "txcall", [[c] for c in chunks], [])
        write_if_changed(out / "txcall_index.c", index)
        tx_calls = [out / f"txcall_{i:02d}.c" for i in range(len(chunks))] + [out / "txcall_index.c"]
        tx_why.update({x: f"tc_call glue: {w}" for x, w in call_why.items()})
    write_if_changed(out / "port_files.cmake", game_config.port_files_cmake(
        cfg, readable.SRC, files, readable.INCLUDE, paths.TARGET / "runtime_rd", defines,
        no_pch(readable.SRC, files, defines), (tx_files, tx_calls, paths.TARGET / "runtime") if tx else None))
    plat = {x for x in declared if GUEST_CRT.get(names[x], names[x]) in pnames and x not in defined and x not in hw}
    guest = sorted(names[x] for x in declared if names[x] in GUEST_CRT)
    stubs = [x for x in declared if x not in defined and x not in hw and x not in crt and x not in plat]
    blocks = [stub_c(readable, x, protos[x]) for x in stubs]
    chunks = [sum(blocks[i:i + CHUNK_STUBS], []) for i in range(0, len(blocks), CHUNK_STUBS)]
    write_chunks(out, "stubs", chunks, ["/* Generated by port/tools/gen_port.py: logging stubs. */",
                                        '#include "game.h"', '#include "port_stub.h"', ""])

    catalog = [f["addr"] for f in json.loads(paths.CATALOG.read_text(encoding="utf-8"))["functions"]]
    named = sorted(x for x in cfg["not_code"] if x in names)
    if named:
        raise game_config.ConfigError(f"game.json not_code lists named functions: {', '.join(named)}")
    every = sorted((set(catalog) | set(declared)) - set(cfg["not_code"]))
    parts = [every[i:i + CHUNK_ROWS] for i in range(0, len(every), CHUNK_ROWS)]
    rows, bare = [], [x for x in every if x not in names]
    unknown = sorted(x for x in hw if x not in names and x not in catalog)
    if unknown:
        raise game_config.ConfigError(f"game.json hardware {', '.join(unknown)}: neither a registry function nor a "
                                      "catalog function")
    from core import readable_abi  # noqa: E402
    meta_of = lambda x: (paths.read_json(paths.HARNESS / x / "meta.json")
                         if (paths.HARNESS / x / "meta.json").is_file() else None)
    adapted = 0
    for i, part in enumerate(parts):
        # A catalog function with no prototype (unnamed library code) gets a bare stub `u32 fn_ADDR(void)`: the
        # caller (cdecl) pops its own arguments, so any call through a guest pointer returns 0 safely.
        own = [x for x in part if x not in names and x not in hw]
        text = ["/* Generated by port/tools/gen_port.py: guest address -> host function. */", '#include "game.h"',
                '#include "port_dispatch.h"', '#include "port_stub.h"', ""]
        text += [f'static u32 fn_{x}(void) {{ port_stub(0x{x}u, "fn_{x}"); return 0; }}' for x in own]
        # a hardware function at an unnamed address: the game's sources define it (called by argument words)
        text += [f"u32 {hw[x]}(void);" for x in part if x not in names and x in hw]
        text += [wrapper_proto(protos[x], names[x], wrap[x]) for x in part if x in wrap]
        adapters = {}
        for x in part:
            meta = meta_of(x) if x in names else None
            ad = meta and pointer_adapter(reg, readable, readable_abi, x, protos[x], meta, wrap.get(x) or names[x])
            if ad:
                adapters[x] = ad[0]
                text.append(ad[1])
        adapted += len(adapters)
        text += ["", f"const PortDispatch port_dispatch_{i:02d}[] = {{"]
        for x in part:
            fn = wrap.get(x) or names.get(x) or hw.get(x) or f"fn_{x}"
            text.append(f'    {{0x{x}u, (PortFn){adapters.get(x, fn)}, "{fn}"}},')
            rows.append(x)
        text += ["};", f"const int port_dispatch_{i:02d}_n = {len(part)};"]
        write_if_changed(out / f"dispatch_{i:02d}.c", "\n".join(text) + "\n")
    idx = ["/* Generated by port/tools/gen_port.py: the dispatch chunks, in address order. */",
           '#include "port_dispatch.h"', ""]
    idx += [f"extern const PortDispatch port_dispatch_{i:02d}[];\nextern const int port_dispatch_{i:02d}_n;"
            for i in range(len(parts))]
    idx += ["", "const PortDispatchChunk port_dispatch_chunks[] = {"]
    idx += [f"    {{port_dispatch_{i:02d}, &port_dispatch_{i:02d}_n}}," for i in range(len(parts))]
    idx += ["};", f"const int port_dispatch_nchunks = {len(parts)};"]
    write_if_changed(out / "dispatch_index.c", "\n".join(idx) + "\n")
    keep = {f"stubs_{i:02d}.c" for i in range(len(chunks))} | {f"dispatch_{i:02d}.c" for i in range(len(parts))}
    keep |= {p.name for p in tx_calls}
    for old in stale:  # only chunks this run did not write: the rest keep their times, so nothing rebuilds
        if old.name not in keep | {"dispatch_index.c"}:
            old.unlink()

    unnamed = len(bare)
    report = [f"readable files: {len(files)}", f"registry functions: {len(reg.functions)}; unnamed functions declared "
              f"from raw signatures: {len(sigs)}", f"globals at fixed guest addresses: {nglob}",
              f"logging stubs: {len(stubs)} in {len(chunks)} file(s); port hardware functions: {len(hw)}; wrapped: {len(wrap)}; host C "
              f"runtime: {len(crt)} ({', '.join(sorted(names[x] for x in crt))}); the game's own C of host C "
              f"runtime names: {len(own_crt)} ({', '.join(sorted(names[x] for x in own_crt))}); platform layer: "
              f"{len(plat)}; guest C "
              f"library: {len(guest)} ({', '.join(f'{n}={GUEST_CRT[n]}' for n in guest)})",
              f"dispatch rows: {len(rows)} in {len(parts)} chunk(s); catalog functions with no declaration "
              f"(bare logging stub): {unnamed}; pointer-call adapters (parameters that skip or reorder r4..r7): "
              f"{adapted}", *[f"note: {n}" for n in notes[:20]]]
    if tx:
        report += [f"translator C ({tx[1]}, {tx[0]}): {len(txs)} functions; readable C: {len(files)}; "
                   f"not used: {len(tx_why)}", *[f"  tx {x}: {w}" for x, w in sorted(tx_why.items())]]
    (out / "gen_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
