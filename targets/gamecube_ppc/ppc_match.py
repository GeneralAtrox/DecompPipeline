"""The byte-match check for the GameCube target: compile a function's original-style C with the game's CodeWarrior
compiler (mwcceppc) and diff the object against the original's, split from the DOL by decomp-toolkit (dtk), with
objdiff-cli. A 100% match proves the C is the source, instruction for instruction; the behaviour check (ppc_check,
the oracle's vectors) stays the fallback for C that does not match.

Verdicts (grade): "matched" (objdiff 100%), "equivalent" (behaviour PASS, no full match), "FAIL". `check` names the
check that gave the verdict: "objdiff" or "behaviour".

Nothing here is game-specific and nothing is vendored. The external programs are this machine's paths in the
git-ignored config.local.json (`mwcc_root`: the dtk-template compiler pack, holding GC/<version>/mwcceppc.exe;
`objdiff_cli`; `dtk`); the game's settings are the workspace's project.json "match" object:
  compiler        "GC/1.3.2" (a folder of mwcc_root)
  cflags          the compiler flags, a list
  include         include folders, relative to the workspace (the tool's match_include/ is always added)
  target_objects  the folder of dtk's split objects (dtk dol split <config> <out>: <out>/obj), relative
  symbols         dtk's symbols.txt (name = .text:0xADDR; // type:function ...), relative
  src_dir         where original-style C lives as <addr>.c, relative (optional)
The original-style C of a function is <work>.mw.c beside the behaviour C, else <src_dir>/<addr>.c.

CLI (pilots, flag searches): python targets/gamecube_ppc/ppc_match.py --project <ws> <addr> <file.c>
                            [--compiler GC/x.y] [--cflags "..."] [--build <dir>]
"""
import json
import os
import pathlib
import re
import shlex
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
INCLUDE = HERE / "match_include"
TIMEOUT_S = 120


# ---------- ELF objects (relocatable, 32-bit; the split objects and mwcc's are big-endian PowerPC) ----------
def elf_symbols(data):
    """{name: {"section": name, "value": st_value, "size": st_size, "type": STT}} of the defined symbols."""
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise ValueError("not a 32-bit ELF object")
    e = ">" if data[5] == 2 else "<"
    shoff, = struct.unpack_from(e + "I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", data, 0x2e)
    secs = [struct.unpack_from(e + "IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]

    def name_at(strtab, off):
        base = secs[strtab][4]
        end = data.index(b"\0", base + off)
        return data[base + off:end].decode("latin-1")

    names = [name_at(shstrndx, s[0]) if shstrndx < shnum else "" for s in secs]
    out = {}
    for s in secs:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        off, size, link, entsize = s[4], s[5], s[6], s[9] or 16
        for k in range(1, size // entsize):
            st_name, value, sz, info, _other, shndx = struct.unpack_from(e + "IIIBBH", data, off + k * entsize)
            if not st_name or shndx == 0 or shndx >= 0xff00:
                continue
            out[name_at(link, st_name)] = {"section": names[shndx] if shndx < len(names) else "",
                                           "value": value, "size": sz, "type": info & 0xf}
    return out


# ---------- dtk's symbols.txt ----------
_SYM = re.compile(r"^\s*([^\s=]+)\s*=\s*([.\w]+):0x([0-9A-Fa-f]+);(.*)$")


def dtk_symbols(text):
    """{addr (int): name} of the function symbols in a dtk symbols.txt."""
    out = {}
    for line in text.splitlines():
        m = _SYM.match(line)
        if m and "type:function" in m.group(4):
            out[int(m.group(3), 16)] = m.group(1)
    return out


def symbol_for(addr, table):
    """The original's symbol name of the function at addr (a dtk symbols table), else dtk's default fn_XXXXXXXX."""
    return table.get(addr) or f"fn_{addr:08X}"


def find_object(obj_dir, symbol):
    """The split object that defines `symbol` (a function), or None."""
    for p in sorted(pathlib.Path(obj_dir).rglob("*.o")):
        try:
            syms = elf_symbols(p.read_bytes())
        except (ValueError, struct.error, IndexError):
            continue
        s = syms.get(symbol)
        if s and s["type"] == 2:  # STT_FUNC
            return p
    return None


# ---------- objdiff-cli's JSON ----------
def match_percent(doc, symbol):
    """The match percent objdiff reports for `symbol` (either side), or None: the first object in the document whose
    symbol name is `symbol` and that carries match_percent (snake or camel case)."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            sym = x.get("symbol")
            name = sym.get("name") if isinstance(sym, dict) else x.get("name")
            pct = x.get("match_percent", x.get("matchPercent"))
            if name == symbol and pct is not None:
                found.append(float(pct))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(doc)
    return max(found) if found else None


# ---------- verdict ----------
def grade(behaviour_verdict, match):
    """(grade, check, note) from the behaviour verdict ("PASS", "FAIL", or None when it did not run) and the match
    result (None when not run). A full match outranks the behaviour check; a full match the behaviour check FAILs is
    still "matched" (the bytes are the original's) with a note: the oracle or the harness is wrong."""
    pct = (match or {}).get("percent")
    if pct is not None and pct >= 100.0:
        note = ("objdiff 100% but the behaviour check FAILs: an oracle or harness defect"
                if behaviour_verdict == "FAIL" else None)
        return "matched", "objdiff", note
    if behaviour_verdict == "PASS":
        return "equivalent", "behaviour", None
    return "FAIL", "behaviour" if behaviour_verdict is not None else "objdiff", None


# ---------- settings ----------
def settings(cfg, root):
    """The match settings (programs and project paths resolved), or None when the project has no "match" object."""
    m = cfg.get("match")
    if not m:
        return None
    root = pathlib.Path(root)

    def rel(p):
        return None if p is None else (root / p).resolve()

    return {
        "mwcc_root": cfg.get("mwcc_root"), "objdiff_cli": cfg.get("objdiff_cli"),
        "compiler": m.get("compiler"), "cflags": list(m.get("cflags") or []),
        "include": [rel(p) for p in m.get("include") or []] + [INCLUDE],
        "target_objects": rel(m.get("target_objects")), "symbols": rel(m.get("symbols")),
        "src_dir": rel(m.get("src_dir")), "timeout_s": int(m.get("timeout_s") or TIMEOUT_S),
    }


def native_source(st, addr, work_file=None):
    """The original-style C for addr: <work>.mw.c beside the behaviour C, else <src_dir>/<addr>.c; or None."""
    if work_file:
        w = pathlib.Path(work_file)
        p = w.with_name(w.stem + ".mw.c")
        if p.is_file():
            return p
    if st.get("src_dir"):
        p = st["src_dir"] / f"{addr}.c"
        if p.is_file():
            return p
    return None


def compiler_exe(st):
    if not st.get("mwcc_root"):
        raise RuntimeError("config key 'mwcc_root' is not set: give the compiler pack's folder in config.local.json")
    if not st.get("compiler"):
        raise RuntimeError("project.json match.compiler is not set (e.g. \"GC/1.3.2\")")
    exe = pathlib.Path(st["mwcc_root"]) / st["compiler"] / "mwcceppc.exe"
    if not exe.is_file():
        raise RuntimeError(f"no mwcceppc.exe at {exe}")
    return exe


def compile_cmd(st, src, out):
    """mwcceppc with the project's flags; the source's own folder is searched first (with -nodefaults, mwcc does not
    look beside the source for #include "...")."""
    cmd = [str(compiler_exe(st)), *st["cflags"]]
    for inc in [pathlib.Path(src).resolve().parent, *st["include"]]:
        cmd += ["-i", str(inc)]
    return cmd + ["-c", str(src), "-o", str(out)]


def objdiff_cmd(st, target_o, base_o, symbol, out_json):
    if not st.get("objdiff_cli"):
        raise RuntimeError("config key 'objdiff_cli' is not set: give objdiff-cli's path in config.local.json")
    return [str(st["objdiff_cli"]), "diff", "-1", str(target_o), "-2", str(base_o), symbol,
            "--format", "json", "-o", str(out_json)]


def _run(cmd, timeout, cwd):
    from core import paths  # the bounded runner (kills the process tree on timeout)
    return paths.run_bounded(cmd, timeout, cwd=str(cwd))


def run(st, addr, src, build_dir, runner=_run):
    """Compile src and diff it against the original's object. Returns {"stage": "compile"|"target"|"diff"|"done",
    "percent": float or None, "symbol", "target_object", "compiler", "cflags", "errors": [...]}."""
    a = int(addr, 16) if isinstance(addr, str) else addr
    build_dir = pathlib.Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    table = dtk_symbols(st["symbols"].read_text(encoding="utf-8")) if st.get("symbols") and st["symbols"].is_file() \
        else {}
    sym = symbol_for(a, table)
    res = {"stage": "compile", "percent": None, "symbol": sym, "target_object": None, "compiler": st.get("compiler"),
           "cflags": st.get("cflags"), "source": str(src), "errors": []}
    base_o = build_dir / "match.o"
    if base_o.exists():
        base_o.unlink()
    rc, out, reason = runner(compile_cmd(st, src, base_o), st["timeout_s"], build_dir)
    if rc != 0 or reason or not base_o.is_file():
        res["errors"] = [ln for ln in out.splitlines() if ln.strip()][:20] or [f"mwcceppc: {reason or rc}"]
        return res
    res["stage"] = "target"
    target = find_object(st["target_objects"], sym) if st.get("target_objects") else None
    if target is None:
        res["errors"] = [f"no split object defines {sym} (run dtk dol split; is the symbol named?)"]
        return res
    res["target_object"] = str(target)
    if sym not in elf_symbols(base_o.read_bytes()):
        res["errors"] = [f"the compiled object does not define {sym}: name the function {sym}"]
        return res
    res["stage"] = "diff"
    out_json = build_dir / "objdiff.json"
    if out_json.exists():
        out_json.unlink()
    rc, out, reason = runner(objdiff_cmd(st, target, base_o, sym, out_json), st["timeout_s"], build_dir)
    if rc != 0 or reason or not out_json.is_file():
        res["errors"] = [ln for ln in out.splitlines() if ln.strip()][-20:] or [f"objdiff-cli: {reason or rc}"]
        return res
    res["percent"] = match_percent(json.loads(out_json.read_text(encoding="utf-8")), sym)
    if res["percent"] is None:
        res["errors"] = [f"objdiff's report has no match percent for {sym}"]
        return res
    res["stage"] = "done"
    return res


def attach(cfg, root, addr, work_file, result, build_dir, runner=_run):
    """The check's result with the match check added (hooks.run_check): "match" (run's result, when the project has
    a "match" object and the function has original-style C), "grade", "check" and "grade_note". A full match makes
    the verdict PASS."""
    match = None
    st = settings(cfg, root)
    src = native_source(st, addr, work_file) if st else None
    if src is not None:
        try:
            match = run(st, addr, src, pathlib.Path(build_dir) / "match", runner=runner)
        except (RuntimeError, OSError, ValueError) as e:
            match = {"stage": "error", "percent": None, "source": str(src), "errors": [str(e)]}
        result["match"] = match
    g, check, note = grade(result.get("verdict"), match)
    result.update({"grade": g, "check": check})
    if note:
        result["grade_note"] = note
    if g == "matched":
        result["verdict"] = "PASS"
    return result


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("addr")
    ap.add_argument("src")
    ap.add_argument("--compiler")
    ap.add_argument("--cflags", help="replaces project.json match.cflags (one string, shell-split)")
    ap.add_argument("--build", help="build folder (default <ws>/build/<addr>/match)")
    a = ap.parse_args(argv)
    os.environ["DECOMP_PROJECT"] = str(pathlib.Path(a.project).resolve())
    sys.path.insert(0, str(HERE.parents[1]))
    from core import paths
    st = settings(paths.config(), paths.ROOT)
    if st is None:
        raise SystemExit("project.json has no \"match\" object")
    if a.compiler:
        st["compiler"] = a.compiler
    if a.cflags is not None:
        st["cflags"] = shlex.split(a.cflags)
    res = run(st, a.addr, pathlib.Path(a.src).resolve(), pathlib.Path(a.build or paths.BUILD / a.addr / "match"))
    print(json.dumps(res, indent=1))
    return 0 if res["stage"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
