"""Compile the Sonic repo's SDK sources with an SHC we already own, into a work folder outside every repository.

usage: python -I sbuild.py REPO SHCDIR OUTREL [--only RX] [--jobs N] [--limit S]
Objects land in REPO/OUTREL/<source path>.obj (OUTREL is relative, with no hyphen: SHC refuses them). Flags are read
from the repo's config/compilers.mk. One row per source in OUTREL/build.tsv: source, route, rc, flags, message.
Nearest existing: tools/sdk_fid.py - fingerprints SDK libraries we own, compiles nothing.
"""
import argparse
import concurrent.futures as cf
import os
import pathlib
import re
import subprocess
import sys

SHCFLAGS = "-cpu=sh4 -endian=little"
GAME = SHCFLAGS + " -fpu=single -speed -extra=a=800 -macsave=0"
RUNTIME = SHCFLAGS + " -fpu=single"
SECT = "-section=p=PSG,c=CSG,d=DSG,b=BSG"
ASMFLAGS = "-cpu=sh4 -endian=little -sjis -private"
SG = ("_0sbver fm fmcrc gdDec gdctl gdfs gdfsdir gdfshn gdfsif lcd lcdvms njpad pd_kbd pdmain sg_fhref sg_flcommon "
      "sg_playtime sg_rdalahd sg_syCbl sg_syb2 sg_sybt sg_sycf sg_syfb sg_syrtc sg_userflag sg_userhis sh4tmr syCache "
      "syChain syIntCtrl syMalloc sySq").split()


def overrides(repo):
    """{source path without .c: extra SDK flags} from compilers.mk's per-object lines."""
    out = {}
    for ln in (repo / "config" / "compilers.mk").read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"(build/\S+\.obj)(?:\s+\S+)*:\s*(?:private\s+)?SDK\s*\+=\s*(.+)$", ln)
        if m:
            out[m.group(1)[len("build/"):-len(".obj")]] = m.group(2).strip()
    return out


def flags_for(src, over):
    """(flags, includes) of one source, as the repo's rules give them; R10's -double=float is -fpu=single to this SHC."""
    fl, inc = flags_raw(src, over)
    return fl.replace("-double=float", "-fpu=single"), inc


def flags_raw(src, over):
    p = pathlib.PurePosixPath(src)
    stem = str(p.with_suffix(""))
    inc = "sdk/include,sdk,modules,."
    if stem.startswith("sdk/runtime/"):
        fl = SHCFLAGS if p.stem in ("_duchek", "_log10", "_unpack", "_xti", "_xtostr") else RUNTIME
        return fl, "sdk/include,sdk"
    if stem.startswith("sdk/ninja/") and p.stem.endswith("_kamui_lib"):
        return GAME + " " + SECT, inc
    fl = SHCFLAGS + " " + over.get(stem, "")
    if stem == "sdk/ninja/njTexLoad":
        return fl + " -size -macsave=0 -extra=a=400 -section=p=PSG,c=CSG,d=DSG,b=BSG32", inc
    if stem == "sdk/shinobi/ov_main":
        return fl + " -section=p=PSG,c=CSG,d=DSGLH,b=BSG", inc
    if (stem.startswith(("sdk/ninja/", "sdk/sofdec/", "modules/movie/sdk/", "loader/sdk/")) or
            (stem.startswith("sdk/shinobi/") and (p.stem in SG or p.stem.startswith("sd_")))):
        fl += " " + SECT
    return fl, inc


def dos(path):
    """asmsh takes / for an option prefix."""
    return path.replace("/", "\\")


def has_inline(path):
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="latin-1"), flags=re.S)
    return bool(re.search(r"^\s*#\s*pragma\s+(inline_asm|asm)\b", text, re.M))


def run(cmd, cwd, env, limit):
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        out, _ = p.communicate(timeout=limit)
        return p.returncode, out.decode("latin-1")
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        p.communicate(timeout=30)
        return 124, "timeout"


def build(src, repo, shc, outrel, over, limit):
    full = repo / src
    obj = outrel + "/" + src.rsplit(".", 1)[0] + ".obj"
    (repo / obj).parent.mkdir(parents=True, exist_ok=True)
    tmp = repo / outrel / "tmp" / re.sub(r"\W", "_", src)
    tmp.mkdir(parents=True, exist_ok=True)
    if src.endswith(".src"):
        rc, out = run([str(shc / "asmsh.exe"), dos(src), "-object=" + dos(obj), *ASMFLAGS.split()], repo, dict(os.environ), limit)
        return src, "asm", rc, ASMFLAGS, out, obj
    flags, inc = flags_for(src, over)
    env = dict(os.environ, SHC_LIB=str(shc), SHC_INC=inc, SHC_TMP=str(tmp))
    if has_inline(full):
        asm = obj[:-4] + ".src"
        rc, out = run([str(shc / "shc.exe"), src, f"-objectfile={asm}", "-code=asmcode", *flags.split()], repo, env, limit)
        if rc == 0 and "(E)" not in out and "(F)" not in out:
            rc2, out2 = run([str(shc / "asmsh.exe"), dos(asm), "-object=" + dos(obj), *ASMFLAGS.split()], repo, env, limit)
            return src, "c+asm", rc2, flags, out + out2, obj
        return src, "c+asm", rc or 1, flags, out, obj
    rc, out = run([str(shc / "shc.exe"), src, f"-objectfile={obj}", "-code=machinecode", *flags.split()], repo, env, limit)
    return src, "c", rc, flags, out, obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("shc")
    ap.add_argument("outrel")
    ap.add_argument("--only")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=float, default=120)
    a = ap.parse_args()
    repo, shc = pathlib.Path(a.repo).resolve(), pathlib.Path(a.shc).resolve()
    over = overrides(repo)
    srcs = []
    for top in ("sdk", "modules/movie/sdk", "loader/sdk"):
        for p in sorted((repo / top).rglob("*")):
            if p.suffix in (".c", ".src") and p.is_file():
                srcs.append(p.relative_to(repo).as_posix())
    if a.only:
        srcs = [s for s in srcs if re.search(a.only, s)]
    rows = []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for src, route, rc, flags, out, obj in ex.map(lambda s: build(s, repo, shc, a.outrel, over, a.limit), srcs):
            ok = rc == 0 and (repo / obj).is_file() and "(E)" not in out and "(F)" not in out
            first = next((x.strip() for x in out.splitlines() if re.search(r"\((E|F|W)\)", x)), out.strip()[:120] if not ok else "")
            rows.append((src, route, "ok" if ok else f"fail{rc}", flags, first.replace("\t", " ")))
    outp = repo / a.outrel / ("build_" + re.sub(r"\W", "_", a.only or "all") + ".tsv")
    outp.write_text("\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    n = sum(r[2] == "ok" for r in rows)
    print(f"{n} of {len(rows)} sources built -> {outp}")
    for r in [r for r in rows if r[2] != "ok"][:40]:
        print(" ", r[0], r[1], r[2], r[4][:150])


if __name__ == "__main__":
    sys.exit(main())
