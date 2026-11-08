"""Inventory of Xbox 360 development symbol sets: every version fact each set's files hold, for choosing which sets can name
a game's functions (targets/xbox360_ppc/RESEARCH_SYMBOLS.md).

A set is a folder, or a .7z/.zip/.rar archive that is extracted (only its symbol and executable files) into a scratch
folder, read, and deleted before the next one. For each set it records:
  xex/xed  title ID, game version and base version (the title update), media ID, disc, original PE name, the import
           libraries with their versions, and the static libraries the game linked with their exact versions (XAPILIB,
           D3D9, XGRAPHC, XAUDIO, ...: the SDK build and QFE)
  pdb      link time (the info stream's signature), age, GUID, the linker and PDB DLL versions, machine, the compilers that
           built its modules (S_COMPILE2/3), and every library it linked with the folder it came from
  map      the module name, link timestamp and the libraries in its Lib:Object column
  pe/exe   the COFF link time
  all      version strings: SDK (2.0.NNNN.N), Havok, Wwise, PhysX, Bink, Scaleform, SpeedTree, FMOD, Miles, Lua, zlib,
           Gamebryo, RenderWare, Granny, NaturalMotion, and Unreal Engine 3 markers
plus the middleware families its libraries belong to, so old Havok (hkcollide) and new Havok (hkp*), or old Wwise
(akaudioengine) and new Wwise (aksoundengine), tell themselves apart.

scan writes one JSON line per set to <out>/inventory.jsonl, skips sets already there (same name and size), stops starting
new sets after --budget-min, and waits while RAM use is above --max-ram (Windows). report writes <out>/inventory.md: one
table row per set, then the sets grouped by SDK build and by Havok and Wwise version.

Nearest existing: pdb_names.py - reads one PDB to name a retail build's functions; it records no versions and takes one
set, not a library of archives (its Msf, Pdb and MapSymbols are reused here). xex.py - parses one XEX for the pipeline but
raises on a title update (.xed, a delta patch) and does not read the static-library versions.

The file readers are in pdb_readers.py.

usage: py -3.12 pdb_inventory.py scan <folder of sets or archives> --out <dir> [--tmp <dir>] [--budget-min N]
                                     [--max-ram PCT] [--only REGEX] [--7z <path>]
       py -3.12 pdb_inventory.py report --out <dir>
"""
import argparse
import collections
import ctypes
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pdb_readers import read_map, read_pdb, read_pe, read_xex  # noqa: E402

ARCHIVES = (".7z", ".zip", ".rar")
WANTED = ("*.pdb", "*.map", "*.xex", "*.xed", "*.pe", "*.exe")


def wipe(path):
    """Deletes a folder, read-only files included (7-Zip keeps the archive's read-only attribute), and says whether it is gone.
    A silent failure here once carried one set's files into every later set's record."""
    def writable(func, p, _exc):
        os.chmod(p, 0o666)
        func(p)
    if path.exists():
        shutil.rmtree(path, onexc=writable)
    return not path.exists()


def seven_zip():
    """7-Zip on PATH, else in the Program Files folders named by the environment."""
    found = shutil.which("7z")
    if found:
        return found
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base and (pathlib.Path(base) / "7-Zip" / "7z.exe").exists():
            return str(pathlib.Path(base) / "7-Zip" / "7z.exe")
    return None


# Library basename -> middleware family. Order matters: the first match wins.
FAMILIES = [
    ("havok (new, hkp*)", r"^hkp|^hka|^hkg|^hkcd"),
    ("havok (old)", r"^hk"),
    ("wwise (new, aksoundengine)", r"^aksoundengine|^akmusicengine.*|^akstreammgr"),
    ("wwise", r"^ak"),
    ("physx", r"physx|^nx|^nxcooking|^pxtask"),
    ("bink", r"bink"),
    ("scaleform", r"^gfx|scaleform"),
    ("speedtree", r"speedtree"),
    ("fmod", r"^fmod"),
    ("miles", r"^mss|^miles"),
    ("lua", r"^lua"),
    ("granny", r"granny"),
    ("naturalmotion", r"^nm|euphoria|morpheme"),
    ("renderware", r"^rw[a-z]"),
    ("gamebryo", r"^ni[a-z]|gamebryo"),
    ("anark", r"anark"),
    ("demonware", r"^bd[a-z]|demonware"),
    ("gamespy", r"gamespy|^gs[a-z]"),
    ("massive ads", r"massive"),
    ("umbra", r"umbra"),
    ("kynapse", r"kynapse|^kaim"),
    ("simul", r"simul"),
    ("iggy", r"iggy"),
    ("xbox 360 sdk", r"^(xapilib|xapi|xboxkrnl|xbdm|d3d9|d3dx9|xgraphics|xaudio|xaudio2|xmedia|xmp|xonline|xnet|xhv|xact|"
                     r"xmcore|xmahal|x3daudio|xparty|xmic|libcmt|libcpmt|oldnames|vcomp|xbox|xam|xuirun|xuirender|"
                     r"xavatar|xhttp|xauth|nuiapi|xperf|xrnm|xjson|xsim|xbc|xtl)"),
]
FAMILY_RE = [(n, re.compile(p, re.I)) for n, p in FAMILIES]


def ram_percent():
    if os.name != "nt":
        return 0

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(f"f{i}", ctypes.c_ulonglong) for i in range(7)]
    m = MemoryStatus()
    m.length = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.load


def below_normal():
    if os.name == "nt":   # child processes (7-Zip) inherit a below-normal priority class
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)


def family(lib):
    base = re.sub(r"\.lib$", "", pathlib.PureWindowsPath(lib).name, flags=re.I)
    for n, r in FAMILY_RE:
        if r.search(base):
            return n
    return None


# ---------------------------------------------------------------------------------------------------------------- sets
NAME_DATE = re.compile(r"\[(\d{4}-\d{2}-\d{2})(?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\]")


def read_set(name, root, size):
    found, rec = {}, {"set": name, "size": size, "files": []}
    m = NAME_DATE.search(name)
    rec["date"] = m.group(1) if m else None
    rec["game"] = re.sub(r"^\[X360\]\s*", "", NAME_DATE.split(name)[0]).strip() if m else name
    readers = {".xex": read_xex, ".xed": read_xex, ".pdb": read_pdb, ".map": read_map, ".pe": read_pe, ".exe": read_pe}
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        r = readers.get(f.suffix.lower())
        if not r:
            continue
        try:
            item = r(f, found)
        except Exception as e:  # noqa: BLE001 - one bad file must not lose the set
            item = {"file": f.name, "error": f"{type(e).__name__}: {e}"[:200]}
        item["kind"] = f.suffix.lower()[1:]
        item["path"] = str(f.relative_to(root))
        rec["files"].append(item)
    rec["versions"] = {k: dict(v.most_common(8)) for k, v in sorted(found.items())}
    libs = set()
    for f in rec["files"]:
        libs.update(f.get("libraries") or [])
    fam = collections.defaultdict(list)
    for lib in sorted(libs):
        n = family(lib)
        if n:
            fam[n].append(re.sub(r"\.lib$", "", pathlib.PureWindowsPath(lib).name, flags=re.I))
    rec["families"] = {k: v[:40] for k, v in sorted(fam.items())}
    sdk = collections.Counter(f["sdk_build"] for f in rec["files"] if f.get("sdk_build"))
    if not sdk:   # no XEX: the newest SDK version string in the PE or PDB
        for s in found.get("sdk", {}):
            sdk[int(s.split(".")[2])] += 1
        rec["sdk_from"] = "version strings" if sdk else None
    else:
        rec["sdk_from"] = "xex"
    rec["sdk_builds"] = sorted(sdk)
    rec["ue3"] = any(len(f.get("ue3_markers") or []) >= 3 for f in rec["files"])
    exe = [f for f in rec["files"] if f.get("title_id")]
    if exe:
        rec["title_id"] = exe[0]["title_id"]
        rec["game_versions"] = sorted({f["version"] for f in exe})
    return rec


def extract(archive, dest, seven):
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [seven, "x", str(archive), f"-o{dest}", "-y", "-r", "-bso0", "-bsp0", "-bse1", *WANTED,
           "*.7z", "*.zip", "*.rar"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    for inner in [q for q in dest.rglob("*") if q.suffix.lower() in ARCHIVES]:   # one level of archives inside
        subprocess.run([seven, "x", str(inner), f"-o{inner.parent / inner.stem}", "-y", "-r", "-bso0", "-bsp0", *WANTED],
                       capture_output=True, text=True, timeout=1800)
        inner.unlink()
    return p.returncode, (p.stderr or "").strip()[-300:]


def scan(a):
    below_normal()
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(a.tmp) if a.tmp else out / "tmp"
    inv = out / "inventory.jsonl"
    done = set()
    if inv.exists():
        for ln in inv.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            done.add((r["set"], r["size"]))
    seven = a.seven or seven_zip()
    items = sorted(p for p in src.iterdir() if p.is_dir() or p.suffix.lower() in ARCHIVES)
    if a.only:
        items = [p for p in items if re.search(a.only, p.name, re.I)]
    t0, n_new = time.time(), 0
    for p in items:
        size = p.stat().st_size if p.is_file() else sum(q.stat().st_size for q in p.rglob("*") if q.is_file())
        name = p.stem if p.is_file() else p.name
        if (name, size) in done:
            continue
        if a.budget_min and time.time() - t0 > a.budget_min * 60:
            print(f"budget reached after {n_new} sets; run again to continue")
            break
        while a.max_ram and ram_percent() > a.max_ram:
            if a.budget_min and time.time() - t0 > a.budget_min * 60:
                break
            print(f"RAM {ram_percent()}% > {a.max_ram}%: waiting")
            time.sleep(60)
        t1 = time.time()
        if p.is_file():
            if not seven:
                print("no 7-Zip found (--7z)")
                return 2
            work = tmp / "set"
            if not wipe(work):
                print(f"cannot empty {work}; stopping so no set picks up another's files")
                return 3
            try:
                rc, err = extract(p, work, seven)
                rec = read_set(name, work, size)
                if rc:
                    rec["extract_error"] = f"7z exit {rc}: {err}"
            except subprocess.TimeoutExpired:
                rec = {"set": name, "size": size, "error": "extract timed out (1800 s)"}
            finally:
                wipe(work)
        else:
            rec = read_set(name, p, size)
        rec["seconds"] = round(time.time() - t1, 1)
        with open(inv, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        n_new += 1
        print(f"{name}: SDK {rec.get('sdk_builds')} UE3 {rec.get('ue3')} {len(rec.get('files', []))} files "
              f"{rec['seconds']} s", flush=True)
    wipe(tmp)
    print(f"{n_new} sets added; {len(done) + n_new} in {inv}")
    return 0


# -------------------------------------------------------------------------------------------------------------- report
def top(d, n=3):
    return ", ".join(list(d)[:n]) if d else ""


def report(a):
    out = pathlib.Path(a.out)
    recs = [json.loads(ln) for ln in (out / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
    recs.sort(key=lambda r: (r.get("game") or r["set"]).lower() + (r.get("date") or ""))
    L = ["# Xbox 360 symbol sets: versions", "",
         f"{len(recs)} sets, made by `pdb_inventory.py report` on {datetime.date.today()}. SDK is the build of the static "
         "libraries in the XEX (or, without a XEX, the 2.0.NNNN version strings). Link time is the PDB's, else the "
         "MAP's or PE's.", "",
         "| Game | Build date | Files | SDK | Title ID / version | Link time | Compiler | UE3 | Havok | Wwise | Other middleware |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    by_sdk, by_havok, by_wwise = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    for r in recs:
        files = collections.Counter(f["kind"] for f in r.get("files", []))
        link = next((f.get("link_time") for f in r.get("files", []) if f["kind"] == "pdb" and f.get("link_time")), None) \
            or next((f.get("link_time") for f in r.get("files", []) if f.get("link_time")), None)
        comp = collections.Counter()
        for f in r.get("files", []):
            comp.update(f.get("compilers") or {})
        fam = r.get("families", {})
        ver = r.get("versions", {})
        havok = top(ver.get("havok", {})) or ("old: " + top(fam.get("havok (old)", []), 2) if "havok (old)" in fam else "") \
            or ("new: " + top(fam.get("havok (new, hkp*)", []), 2) if "havok (new, hkp*)" in fam else "")
        wwise = top(ver.get("wwise", {})) or ("old (akaudioengine)" if "wwise" in fam else "") \
            or ("new (aksoundengine)" if "wwise (new, aksoundengine)" in fam else "")
        other = sorted(set(fam) - {"havok (old)", "havok (new, hkp*)", "wwise", "wwise (new, aksoundengine)", "xbox 360 sdk"})
        other = [f"{o} {top(ver.get(o.split()[0], {}), 1)}".strip() for o in other]
        sdk = ", ".join(map(str, r.get("sdk_builds") or [])) or "?"
        tv = (r.get("title_id") or "") + (" " + "/".join(r.get("game_versions", [])) if r.get("game_versions") else "")
        err = " (" + (r.get("error") or r.get("extract_error") or "")[:40] + ")" if r.get("error") or r.get("extract_error") else ""
        L.append(f"| {r.get('game') or r['set']}{err} | {r.get('date') or ''} | "
                 f"{' '.join(f'{k} {v}' for k, v in sorted(files.items()))} | {sdk} | {tv} | {link or ''} | "
                 f"{comp.most_common(1)[0][0] if comp else ''} | {'yes' if r.get('ue3') else ''} | {havok} | {wwise} | "
                 f"{'; '.join(other)} |")
        for b in r.get("sdk_builds") or ["?"]:
            by_sdk[str(b)].append(f"{r.get('game')} {r.get('date') or ''}".strip())
        for h in (ver.get("havok") or {}):
            by_havok[h].append(f"{r.get('game')} {r.get('date') or ''}".strip())
        for w in (ver.get("wwise") or {}):
            by_wwise[w].append(f"{r.get('game')} {r.get('date') or ''}".strip())
    for title, idx in (("By SDK build", by_sdk), ("By Havok version string", by_havok), ("By Wwise version string", by_wwise)):
        L += ["", f"## {title}", "", "| Version | Sets |", "|---|---|"]
        for k in sorted(idx, key=lambda s: (len(s), s)):
            L.append(f"| {k} | {'; '.join(sorted(set(idx[k])))} |")
    (out / "inventory.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out / 'inventory.md'} ({len(recs)} sets)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("src")
    s.add_argument("--out", required=True)
    s.add_argument("--tmp")
    s.add_argument("--budget-min", type=float, default=0)
    s.add_argument("--max-ram", type=int, default=0)
    s.add_argument("--only")
    s.add_argument("--7z", dest="seven")
    r = sub.add_parser("report")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return scan(a) if a.cmd == "scan" else report(a)


if __name__ == "__main__":
    sys.exit(main())
