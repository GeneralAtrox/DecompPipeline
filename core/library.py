"""refine.py library: every readable/src/ADDR.c compiled as one program with the port's game.h, at the judge's flags.

`check` and `verify` compile each file alone, against a game.h made for that one function, where every other function
and every global is a macro on its guest address (a call faults and is logged). The port links all files together
against one game.h, where functions are real prototypes and globals real `extern`s. This command builds exactly that:
1. One library game.h: readable.game_h in port form (every registry function, every named global as extern), plus
   the raw prototype of each function without a registry name that a readable file defines, calls or uses as data
   (fn_ADDR, from that function's own harness signature, else the caller's callee record). `refine.py coverage`
   counts those unnamed functions; here they only let the files compile.
2. Each file is one translation unit: cl.exe (32-bit, readable_check.C_FLAGS: /W4 /WX ..., /O1) with the include
   path check uses (the generated game.h first, then readable/include, the readable runtime, the raw runtime), in
   parallel (`workers`), each object cached under OUT/cache by a content hash (source, generated game.h, the include
   tree, the runtime headers, flags, compiler), diagnostics included, so a re-run compiles only what changed.
3. The objects are archived with lib.exe /WX: a symbol two objects define (a global defined twice, two files
   exporting the same function, a helper that is not static in two files) is LNK4006, an error under /WX.
4. One generated unit (core/library_decls) holds game.h plus every file's external-linkage declarations, each under
   a #line naming its file: two files declaring one external name with different types (which the linker never
   checks in C), or a file's declaration that differs from game.h, fail there.
Exit 0 only with zero diagnostics anywhere. The report lists each failing file with its first diagnostic and groups
repeated diagnostics (the same message with the quoted names blanked) with counts and examples.
"""
import collections
import concurrent.futures
import hashlib
import re
import shutil

from . import callee_drift, library_decls, paths, readable
from . import readable_check as rcheck

DIAG = re.compile(r"\b(?:fatal )?(error|warning) ([A-Z]+\d+)\s*:\s*(.*)")
WRAPPERS = ("C2220", "LNK1218")  # "warning treated as an error": the warning itself is the diagnostic
LOC = re.compile(r"(?:^|[\\/])([0-9a-f]{8})\.c\((\d+)\)")


def wrapper(line):
    return any(f" {w}:" in line for w in WRAPPERS)


def tools(cfg):
    env = rcheck.msvc_env_x86(cfg)
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    found = {x: shutil.which(f"{x}.exe", path=path) for x in ("cl", "lib")}
    missing = [x for x, p in found.items() if not p]
    if missing:
        raise RuntimeError(f"{', '.join(missing)}.exe not found in the 32-bit Visual Studio environment")
    return env, found


def raw_declarations(reg, files):
    """({addr: raw signature} of the functions without a registry name the files define, call or use as data,
    [notes]). The signature is the function's own harness signature, else its callee record."""
    own, sigs, notes, records = callee_drift.Signatures(paths.HARNESS), {}, [], collections.defaultdict(set)
    for addr in files:
        meta = paths.read_json(paths.HARNESS / addr / "meta.json")
        if not meta:
            if addr not in reg.functions:
                notes.append(f"{addr}: no harness meta and no registry prototype (its own name cannot be declared)")
            continue
        if addr not in reg.functions:
            sigs[addr] = meta["signature"]
        for c in meta["callees"]:
            if c["addr"] not in reg.functions:
                records[c["addr"]].add(c["signature"])
        for d in rcheck.data_functions(reg, addr, meta):
            if d not in reg.functions and own(d):
                sigs.setdefault(d, own(d))
    for c, recs in records.items():
        if own(c):
            sigs.setdefault(c, own(c))
        elif len(recs) > 1:
            notes.append(f"{c}: callers record {len(recs)} different signatures and it has no harness; using "
                         f"`{sorted(recs)[0]}`")
            sigs.setdefault(c, sorted(recs)[0])
        else:
            sigs.setdefault(c, next(iter(recs)))
    return sigs, notes


def tree_key(game_h, cl):
    """Hash of everything a file's compile reads besides the file: game.h, the include tree, runtime headers, flags."""
    h = hashlib.sha256(f"{cl}\0{' '.join(rcheck.C_FLAGS)}\0/O1\0".encode() + game_h.encode())
    for root in (readable.INCLUDE, rcheck.RUNTIME_RD, paths.RUNTIME):
        for f in sorted(root.rglob("*.h")):
            if not (root == readable.INCLUDE and f.name == "game.h" and f.parent == root):
                h.update(f"{f.relative_to(root).as_posix()}\0".encode() + f.read_bytes() + b"\0")
    return h.hexdigest()


def diagnostics(out, files_dir):
    """[line] of compiler/librarian diagnostics in out, with the build folder's paths cut to the file name."""
    prefix = str(files_dir).replace("/", "\\").rstrip("\\") + "\\"
    return [ln.strip().replace(prefix, "") for ln in out.splitlines() if DIAG.search(ln) and not wrapper(ln)]


def compile_one(env, cl, addr, src_text, inc, out, base_key):
    """{"ok", "msgs", "obj"} of one file, from the cache when its key was compiled before."""
    key = hashlib.sha256(base_key.encode() + b"\0" + src_text.encode("utf-8")).hexdigest()[:24]
    cache, obj = out / "cache" / f"{key}.json", out / "cache" / f"{key}.obj"
    rec = paths.read_json(cache) if cache.exists() else None
    if rec is not None and (not rec["ok"] or obj.exists()):
        return dict(rec, msgs=[m for m in rec["msgs"] if not wrapper(m)], obj=obj, cached=True)
    work = out / "src" / f"{addr}.c"
    work.write_text(src_text, encoding="utf-8")
    cmd = [cl, "/nologo", "/c", "/O1", *rcheck.C_FLAGS, *(f"/I{d}" for d in (inc, readable.INCLUDE, rcheck.RUNTIME_RD,
                                                                               paths.RUNTIME)), f"/Fo{obj}", str(work)]
    rc, text, reason = paths.run_bounded(cmd, 180, cwd=out / "src", env=env)
    msgs = diagnostics(text, out / "src") + ([f"{addr}.c: error TIMEOUT: compile {reason}"] if reason else [])
    if rc != 0 and not msgs:
        msgs = [f"{addr}.c: error EXIT{rc}: " + " | ".join(text.strip().splitlines()[-3:])]
    rec = {"ok": rc == 0 and not reason and not msgs, "msgs": msgs[:40]}
    if not rec["ok"]:
        obj.unlink(missing_ok=True)
    paths.write_json(cache, rec)
    return dict(rec, obj=obj, cached=False)


def kind_of(msg):
    """The diagnostic's kind: its code and message with quoted names blanked ("C4013: '...' undefined; ...")."""
    m = DIAG.search(msg)
    if not m:
        return msg[:120]
    return f"{m.group(1)} {m.group(2)}: " + re.sub(r"'[^']*'", "'...'", m.group(3)).strip()[:140]


def names_in(msg):
    m = DIAG.search(msg)
    return re.findall(r"'([^']*)'", m.group(3)) if m else []


def build(cfg, out, workers=4, log=print):
    """Build the library into out; returns (ok, facts). Prints the report and writes it to out/library_report.txt."""
    out.mkdir(parents=True, exist_ok=True)
    for d in ("cache", "src", "obj", "include", "decls"):
        (out / d).mkdir(exist_ok=True)
    reg = readable.Registry()
    files = sorted(p.stem for p in readable.SRC.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem))
    sigs, notes = raw_declarations(reg, files)
    game_h = readable.game_h(reg, addrs=sorted(sigs), raw_sigs=sigs)
    (out / "include" / "game.h").write_text(game_h, encoding="utf-8")
    env, exe = tools(cfg)
    base = tree_key(game_h, exe["cl"])
    texts = {a: (readable.SRC / f"{a}.c").read_text(encoding="utf-8", errors="replace") for a in files}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        res = dict(zip(files, ex.map(lambda a: compile_one(env, exe["cl"], a, texts[a], out / "include", out,
                                                          base), files)))
    lines, groups = [], collections.defaultdict(lambda: {"files": set(), "n": 0, "names": collections.Counter()})
    failing = {a: r["msgs"] for a, r in res.items() if not r["ok"]}

    def count(stage, addr, msgs):
        for m in msgs:
            g = groups[(stage, kind_of(m))]
            g["files"].add(addr)
            g["n"] += 1
            g["names"].update(names_in(m))

    for a, msgs in failing.items():
        count("compile", a, msgs)
    lib_msgs = archive(env, exe["lib"], out, [a for a in files if res[a]["ok"]], res)
    for m in lib_msgs:
        count("archive", (re.findall(r"([0-9a-f]{8})\.obj", m) or ["lib"])[0], [m])
    unit, info = library_decls.combined_unit([(a, readable.SRC / f"{a}.c", texts[a]) for a in files])
    (out / "decls" / "all_decls.c").write_text(unit, encoding="utf-8")
    decl_msgs = compile_unit(env, exe["cl"], out)
    for m in decl_msgs:
        loc = LOC.search(m)
        count("declarations", loc.group(1) if loc else "game.h", [m])
    ok = not failing and not lib_msgs and not decl_msgs
    cached = sum(r["cached"] for r in res.values())
    lines.append(f"library: {len(files)} readable file(s); {len(files) - len(failing)} compile clean, "
                 f"{len(failing)} fail ({cached} from the object cache); archive: {len(lib_msgs)} diagnostic(s); "
                 f"cross-file declarations: {info['checked']} checked, {len(decl_msgs)} diagnostic(s), "
                 f"{len(info['skipped'])} skipped (they name a type only their file defines)")
    lines.append(f"  game.h: {len(reg.functions)} registry functions, {len(reg.globals)} globals, {len(sigs)} unnamed "
                 "function(s) declared by their raw harness signature (refine.py coverage lists them)")
    lines += [f"  note: {n}" for n in notes[:20]]
    if failing:
        lines.append(f"failing files: {len(failing)} (first diagnostic each)")
        lines += [f"  {a}: {msgs[0] if msgs else '?'}" for a, msgs in sorted(failing.items())]
    lines += [f"archive (lib.exe /WX): {m}" for m in lib_msgs[:40]]
    lines += [f"declarations: {m}" for m in decl_msgs[:60]]
    if groups:
        lines.append("diagnostic kinds (stage, kind: files, diagnostics, most frequent names):")
        for (stage, k), g in sorted(groups.items(), key=lambda kv: (-len(kv[1]["files"]), kv[0])):
            top = ", ".join(f"{n} ({c})" for n, c in g["names"].most_common(5))
            lines.append(f"  {len(g['files']):4} file(s) {g['n']:5} x  [{stage}] {k}" + (f"  e.g. {top}" if top else ""))
    lines += [f"  skipped declaration: {a}.c({ln}): {why}" for a, ln, why in info["skipped"][:20]]
    every = [m for msgs in failing.values() for m in msgs] + lib_msgs + decl_msgs
    in_files = sum(bool(LOC.search(m) or re.search(r"[0-9a-f]{8}\.obj", m)) for m in every)
    if every:
        lines.append(f"where: {in_files} diagnostic(s) point into readable/src files (route: the file; sync-names, "
                     f"run --redo), {len(every) - in_files} into headers or game.h (route: the header designers)")
    lines.append(("PASS" if ok else "FAIL") + " library: " + ("every readable file compiles together with game.h at "
                 "/W4 /WX, no symbol defined twice, no conflicting declaration" if ok else
                 f"{len(failing)} file(s) fail to compile, {len(lib_msgs)} archive and {len(decl_msgs)} declaration "
                 "diagnostic(s)"))
    for ln in lines:
        log(ln)
    (out / "library_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    kinds = {f"[{s}] {k}": len(g["files"]) for (s, k), g in groups.items()}
    return ok, {"files": len(files), "clean": len(files) - len(failing), "failing": sorted(failing),
                "archive": len(lib_msgs), "declarations": len(decl_msgs), "kinds": kinds, "unnamed": len(sigs)}


def archive(env, lib, out, addrs, res):
    """lib.exe /WX over the objects (copied as obj/ADDR.obj so messages name the file); [diagnostic]."""
    for f in (out / "obj").glob("*.obj"):
        f.unlink()
    for a in addrs:
        shutil.copyfile(res[a]["obj"], out / "obj" / f"{a}.obj")
    if not addrs:
        return []
    rsp = out / "obj" / "objects.rsp"
    rsp.write_text("\n".join(f'"{out / "obj" / f"{a}.obj"}"' for a in addrs) + "\n", encoding="utf-8")
    rc, text, reason = paths.run_bounded([lib, "/nologo", "/WX", f"/OUT:{out / 'readable.lib'}", f"@{rsp}"], 600,
                                         cwd=out / "obj", env=env)
    msgs = diagnostics(text, out / "obj")
    if (rc != 0 or reason) and not msgs:
        msgs = [f"lib.exe: error EXIT{rc}: {reason or ''} " + " | ".join(text.strip().splitlines()[-3:])]
    return msgs


def compile_unit(env, cl, out):
    """cl /c of the generated declarations unit at the judge's flags; [diagnostic] (paths as #line gives them)."""
    src = out / "decls" / "all_decls.c"
    cmd = [cl, "/nologo", "/c", *rcheck.C_FLAGS, *(f"/I{d}" for d in (out / "include", readable.INCLUDE,
                                                                       rcheck.RUNTIME_RD, paths.RUNTIME)),
           f"/Fo{out / 'decls' / 'all_decls.obj'}", str(src)]
    rc, text, reason = paths.run_bounded(cmd, 600, cwd=out / "decls", env=env)
    msgs = [ln.strip() for ln in text.splitlines() if DIAG.search(ln) and not wrapper(ln)]
    if (rc != 0 or reason) and not msgs:
        msgs = [f"all_decls.c: error EXIT{rc}: {reason or ''} " + " | ".join(text.strip().splitlines()[-3:])]
    return msgs

