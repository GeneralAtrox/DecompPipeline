"""Function ID databases built from a Katana SDK release, and FID applied to a game (batch 25a).

usage:
  python tools/sdk_fid.py build --sdk SRC --release NAME --work DIR [--libs RX] [--exclude RX] [--tools DIR]
                                [--formats sysrof_lib,sysrof_obj] [--target dreamcast_sh4] [--timeout S]
  python tools/sdk_fid.py apply --project WS --out DIR --fidb TAG=PATH [--fidb TAG=PATH ...] [--threshold 10]
                                [--ghidra DIR] [--timeout S]

build: SRC is an SDK folder, an installer .iso, or an InstallShield 5 data1.hdr. Steps, each skipped when its stamp
says it is done for the same inputs: extract (7-Zip for an .iso, tools/is5cab.py for the cabinet) into
WORK/NAME/src; split each SHC library with the SDK's libsplit.exe and convert every object with elfcnv.exe into
WORK/NAME/split/<library>/; import them into the Ghidra project WORK/NAME/ghidra under the target's language
(SdkFidPrep makes sure every code symbol is a function); then WORK/NAME/NAME.fidb, one FID library per SDK library
(SdkFidBuild), with WORK/NAME/fidb_libs.tsv and WORK/NAME/times.json.
apply: a fresh raw import of the project's binary in OUT/ghidra or --ghidra DIR (TcSetup, then SdkFidSeed: exactly the catalog's
functions with the catalog's bodies, analysed), FID with each database (SdkFidApply) into OUT/fid_raw.tsv, merged
into OUT/tc_fid.tsv (one row per function; tools/sdk_fid_lib.merge_fid).
SDK files are only read; nothing from them is copied anywhere but WORK. The FID databases hold hashes and names.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
import is5cab  # noqa: E402
import sdk_fid_lib as L  # noqa: E402
from bounded import kill_tree  # noqa: E402
sys.path.insert(0, str(TOOL))
from core import toolcfg  # noqa: E402  (config.json + the git-ignored config.local.json)

SCRIPTS = TOOL / "tools" / "fid_scripts"


def config():
    """config.json with config.local.json over it (7-Zip: the "sevenzip" key, else 7z on PATH)."""
    return toolcfg.load(TOOL)


def run(cmd, timeout, cwd=None, env=None, log=None):
    """Run cmd bounded; on expiry kill its whole process tree. Returns (rc, output); rc 124 on a timeout."""
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=os.name != "nt")
    try:
        out, _ = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        out, _ = p.communicate(timeout=60)
        rc = 124
    text = (out or b"").decode("utf-8", "replace")
    if log:
        pathlib.Path(log).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(log).write_text(text, encoding="utf-8")
    return rc, text


def headless(cfg, project_dir, name, args, timeout, log):
    pathlib.Path(project_dir).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JAVA_HOME=toolcfg.need(cfg, "java_home"))
    env["PATH"] = os.path.join(env["JAVA_HOME"], "bin") + os.pathsep + env["PATH"]
    exe = os.path.join(toolcfg.need(cfg, "ghidra_home"), "support", "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
    rc, out = run([exe, project_dir, name, *args], timeout, env=env, log=log)
    if rc == 124:
        raise RuntimeError(f"Ghidra killed after {timeout} s (log {log})")
    if rc != 0 or "SCRIPT ERROR" in out or "Unable to lock project" in out or re.search(r"\.java:\d+: error:", out):
        raise RuntimeError(f"Ghidra failed rc={rc} (log {log}):\n" + "\n".join(out.splitlines()[-15:]))
    return out


def target_json(target):
    return json.loads((TOOL / "targets" / target / "target.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- build

def resolve_source(src, rel_dir, timeout, log=print):
    """The folder holding the SDK's files: SRC itself, or its extraction under rel_dir/src."""
    src = pathlib.Path(src)
    if src.is_dir() and not list(src.glob("data1.hdr")):
        return src
    hdr = src
    if src.suffix.lower() == ".iso":
        iso_dir = rel_dir / "iso"
        want = {"iso": str(src), "size": src.stat().st_size, "mtime": src.stat().st_mtime_ns}
        if not L.stamp_matches(rel_dir / "iso.json", want):
            rc, out = run([toolcfg.need(config(), "sevenzip", which="7z"), "x", "-y", f"-o{iso_dir}", src], timeout, log=rel_dir / "logs" / "iso.log")
            if rc != 0:
                raise RuntimeError(f"7-Zip failed on {src} (rc {rc})")
            L.write_stamp(rel_dir / "iso.json", want)
        log(f"iso: {iso_dir}")
        src = iso_dir
    if src.is_dir():
        found = sorted(src.rglob("data1.hdr"))
        if not found:
            return src
        hdr = found[0]
    out_dir = rel_dir / "src"
    want = {"hdr": L.file_digest(hdr), "cab": hdr.with_suffix(".cab").stat().st_size}
    if not L.stamp_matches(rel_dir / "src.json", want):
        counts = is5cab.extract(hdr, out_dir, only=r"\.(lib|obj|a|o|elf|exe|msg|off)$", log=log)
        if counts["bad"]:
            raise RuntimeError(f"is5cab: {counts['bad']} files failed their MD5")
        L.write_stamp(rel_dir / "src.json", want)
    log(f"cabinet: {out_dir}")
    return out_dir


def find_tool(roots, name):
    for root in roots:
        hits = sorted(pathlib.Path(root).rglob(name), key=lambda p: ("hitachi" not in str(p).lower(), str(p)))
        if hits:
            return hits[0]
    raise RuntimeError(f"{name} not found under {', '.join(map(str, roots))} (pass --tools)")


def split_library(fam, path, kind, dest, tools, log=print):
    """Objects of one library as ELF files in dest. Returns (objects, failed names)."""
    for p in dest.glob("*"):
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    failed, n = [], 0
    if kind == L.ELF:
        shutil.copy2(path, dest / (path.stem + ".elf"))
        return 1, []
    if kind == L.AR:
        for name, data in L.ar_members(path.read_bytes()):
            if data[:4] == b"\x7fELF":
                (dest / (pathlib.PurePath(name).stem + ".elf")).write_bytes(data)
                n += 1
        return n, []
    tmp = dest / "_sysrof"
    tmp.mkdir()
    shutil.copy2(path, tmp / path.name)
    if kind == L.SYSROF_LIB:
        rc, out = run([tools["libsplit"], path.name], 600, cwd=tmp)
        (tmp / path.name).unlink()
    objs = sorted(q for q in tmp.iterdir() if q.is_file())
    for obj in objs:
        elf = dest / (obj.stem + ".elf")
        rc, out = run([tools["elfcnv"], obj.name, elf], 120, cwd=tmp)
        if rc != 0 or not elf.is_file() or "successful" not in out.lower():
            failed.append(obj.name)
            elf.unlink(missing_ok=True)
        else:
            n += 1
    shutil.rmtree(tmp)
    if failed:
        log(f"split {fam}: elfcnv failed on {len(failed)}: {' '.join(failed[:8])}")
    return n, failed


def build(a):
    cfg = config()
    lang = target_json(a.target)["ghidra_language"]
    rel_dir = pathlib.Path(a.work) / a.release
    rel_dir.mkdir(parents=True, exist_ok=True)
    times = {}
    t0 = time.time()
    root = resolve_source(a.sdk, rel_dir, a.timeout)
    times["extract_s"] = round(time.time() - t0, 1)
    libs = L.select_libraries(root, a.libs, a.exclude, tuple(a.formats.split(",")))
    print(f"{len(libs)} libraries under {root}")
    tool_roots = [pathlib.Path(a.tools)] if a.tools else [root, pathlib.Path(a.sdk)]
    tools = {}
    t0 = time.time()
    split_digest = {}
    for fam, path, kind in libs:
        dest = rel_dir / "split" / fam
        want = {"source": L.file_digest(path), "kind": kind, "path": str(path)}
        stamp = dest / ".done.json"
        if not L.stamp_matches(stamp, want):
            if kind in (L.SYSROF_LIB, L.SYSROF_OBJ) and not tools:
                tools = {t: find_tool(tool_roots, f"{t}.exe") for t in ("libsplit", "elfcnv")}
            n, failed = split_library(fam, path, kind, dest, tools)
            L.write_stamp(dest / "objects.json", {"objects": n, "failed": failed})
            L.write_stamp(stamp, want)
        split_digest[fam] = want["source"]
    times["split_s"] = round(time.time() - t0, 1)

    proj, name = rel_dir / "ghidra", "fid_" + re.sub(r"[^A-Za-z0-9_]", "_", a.release)
    imported_path = proj / "imported.json"
    imported = json.loads(imported_path.read_text()) if imported_path.is_file() else {}
    todo = [f for f in split_digest if imported.get(f) != split_digest[f] and list((rel_dir / "split" / f).glob("*.elf"))]
    t0 = time.time()
    for i, chunk in enumerate(L.chunks([str(rel_dir / "split" / f) for f in todo], max_items=6)):
        log = rel_dir / "logs" / f"import_{int(time.time())}_{i}.log"
        out = headless(cfg, proj, name, ["-import", *chunk, "-recursive", "-overwrite", "-loader", "ElfLoader",
                                         "-processor", lang, "-scriptPath", SCRIPTS, "-preScript", "SdkFidOptions.java",
                                         "-postScript", "SdkFidPrep.java"],
                       a.timeout, log)
        for d in chunk:
            fam = pathlib.Path(d).name
            saved = len(re.findall(rf"REPORT: Save succeeded for: /{re.escape(fam)}/", out))
            want = len(list(pathlib.Path(d).glob("*.elf")))
            if saved == want:
                imported[fam] = split_digest[fam]
            else:
                print(f"import {fam}: {saved} of {want} objects saved (see {log})")
        L.write_stamp(imported_path, imported)
    times["import_s"] = round(time.time() - t0, 1)

    fidb = rel_dir / f"{a.release}.fidb"
    ready = sorted(f for f in split_digest if imported.get(f) == split_digest[f])
    want = {"libs": {f: split_digest[f] for f in ready}, "lang": lang, "version": a.release}
    t0 = time.time()
    if not (fidb.is_file() and L.stamp_matches(rel_dir / "fidb.json", want)):
        lst = rel_dir / "fidb_libs.txt"
        lst.write_text("\n".join(ready) + "\n", encoding="utf-8")
        out = headless(cfg, proj, name, ["-scriptPath", SCRIPTS, "-preScript", "SdkFidBuild.java", fidb, a.release,
                                         "shc", lang, lst], a.timeout, rel_dir / "logs" / "fidb.log")
        rows = []
        for m in re.finditer(r"FIDLIB (\S+) programs=(\d+) attempted=(\d+) added=(\d+) excluded=(\d+)(.*?)\s*\(Ghidra",
                             out):
            rows.append([m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6).strip()])
        L.write_tsv(rel_dir / "fidb_libs.tsv", ["library", "objects", "functions", "added", "excluded", "why"], rows)
        L.write_stamp(rel_dir / "fidb.json", want)
    times["fidb_s"] = round(time.time() - t0, 1)
    prev = json.loads((rel_dir / "times.json").read_text()) if (rel_dir / "times.json").is_file() else {}
    L.write_stamp(rel_dir / "times.json", {"last": times, "first": prev.get("first", times)})
    print(f"{fidb}: {len(ready)} of {len(libs)} libraries; times {times}")
    return 0 if len(ready) == len([f for f in split_digest if list((rel_dir / 'split' / f).glob('*.elf'))]) else 1


# ---------------------------------------------------------------- apply

def project_names(ws):
    """{addr: name} from the workspace's readable/names/*.json registries."""
    names = {}
    for p in sorted((ws / "readable" / "names").glob("*.json")):
        for addr, e in json.loads(p.read_text(encoding="utf-8")).get("functions", {}).items():
            m = re.search(r"(\w+)\s*\(", e.get("proto", ""))
            if m:
                names[addr.lower()] = m.group(1)
    return names


def bodies_file(ws, out):
    """out/bodies.txt: the catalog's functions with the body ranges TcCatalog wrote (entry lo hi, hi exclusive)."""
    cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
    lines, have = [], set()
    src = ws / "state" / "function_bodies.txt"
    entries = {f["addr"].lower() for f in cat}
    for ln in src.read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) == 3 and p[0].lower() in entries:
            lines.append(" ".join(x.lower() for x in p))
            have.add(p[0].lower())
    lines += [f"{e} {e} {int(e, 16) + 2:08x}" for e in sorted(entries - have)]
    path = out / "bodies.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, len(entries)


def run_scripts(target, out):
    """out/scripts: the FID scripts plus the target's TcSetup (pre-analysis setup) and TcGraph (call graph), copied
    (never edited) into one folder: a .bat launcher splits a "dir1;dir2" -scriptPath at the semicolon."""
    d = out / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    tscripts = TOOL / "targets" / target / "ghidra_scripts"
    for src in [*SCRIPTS.glob("*.java"), tscripts / "TcSetup.java", tscripts / "TcGraph.java"]:
        if src.is_file():
            shutil.copy2(src, d / src.name)
    return d


def apply(a):
    cfg = config()
    ws = pathlib.Path(a.project).resolve()
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    tj = target_json(proj["target"])
    binary = (ws / proj["binary"]).resolve()
    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    bodies, n = bodies_file(ws, out)
    gdir, gname = pathlib.Path(a.ghidra or out / "ghidra").resolve(), proj.get("ghidra_name", "game") + "_fid"
    if any(part.startswith(".") for part in gdir.parts[1:]):
        raise SystemExit(f"{gdir}: Ghidra refuses a project path with a '.'-element; pass --ghidra elsewhere")
    want = {"binary": L.file_digest(binary), "bodies": L.file_digest(bodies), "lang": tj["ghidra_language"]}
    scripts = run_scripts(proj["target"], out)
    t0 = time.time()
    if not L.stamp_matches(out / "import.json", want):
        shutil.rmtree(gdir, ignore_errors=True)
        pre = ["-preScript", "TcSetup.java"] if (scripts / "TcSetup.java").is_file() else []
        headless(cfg, gdir, gname, ["-import", binary, "-processor", tj["ghidra_language"], "-loader",
                                    tj["ghidra_loader"], "-loader-baseAddr", tj["load_address"],
                                    "-scriptPath", scripts, *pre, "-preScript", "SdkFidSeed.java",
                                    bodies, "-postScript", "SdkFidSeed.java", bodies, "enforce"],
                 a.timeout, out / "logs" / "import.log")
        L.write_stamp(out / "import.json", want)
    t_import = time.time() - t0
    t0 = time.time()
    raw = out / "fid_raw.tsv"
    graph = ["-postScript", "TcGraph.java", out / "graph.tsv"] if (scripts / "TcGraph.java").is_file() else []
    log = headless(cfg, gdir, gname, ["-process", binary.name, "-noanalysis", "-readOnly",
                                      "-scriptPath", scripts, *graph,
                                      "-postScript", "SdkFidApply.java", raw, str(a.threshold),
                                      tj["load_address"].replace("0x", ""),
                                      *[x for f in a.fidb for x in f.split("=", 1)]], a.timeout,
                   out / "logs" / "apply.log")
    for m in re.finditer(r"FIDAPPLY .*?(?=\s+\(Ghidra)", log):
        print(m.group(0))
    rows = L.merge_fid(L.read_tsv(raw), project_names(ws), int(proj["game_end"], 16))
    L.write_tsv(out / "tc_fid.tsv", L.TC_FID_HEADER, rows)
    lib = [r for r in rows if r["region"] == "library"]
    uniq = sum(r.get("unique") == "yes" for r in lib)
    best = sum(r.get("unique") == "best" for r in lib)
    amb = sum(r.get("unique") == "no" for r in lib)
    print(f"{n} catalog functions; library {len(lib)}: unique {uniq}, best of several {best}, tied {amb}, "
          f"below threshold {sum(r['status'] == 'below' for r in lib)}, no match "
          f"{sum(r['status'] == 'nomatch' for r in lib)}, too small {sum(r['status'] == 'small' for r in lib)}; "
          f"import {t_import:.0f} s, FID {time.time() - t0:.0f} s -> {out / 'tc_fid.tsv'}")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sdk", required=True)
    b.add_argument("--release", required=True)
    b.add_argument("--work", required=True)
    b.add_argument("--libs")
    b.add_argument("--exclude")
    b.add_argument("--tools")
    b.add_argument("--formats", default=f"{L.SYSROF_LIB},{L.SYSROF_OBJ}")
    b.add_argument("--target", default="dreamcast_sh4")
    b.add_argument("--timeout", type=int, default=5400)
    p = sub.add_parser("apply")
    p.add_argument("--project", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--fidb", action="append", required=True, help="TAG=PATH.fidb")
    p.add_argument("--ghidra", help="folder for the scratch Ghidra project (default OUT/ghidra)")
    p.add_argument("--threshold", type=float, default=10.0)
    p.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else apply(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
