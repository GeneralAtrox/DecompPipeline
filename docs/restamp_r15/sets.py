"""Combined r15 re-stamp sets per game (static: packets, harness meta, state lists; no Ghidra).

usage: python sets.py KEY WORKSPACE OUTDIR
Writes OUTDIR/KEY/{restamp,rejudge,requeue,left_out}.txt (`addr  # reasons`) and OUTDIR/KEY.json (counts).
Inputs that live outside the workspaces are copied into inputs/ next to this script: Spawn's r15-oracle class lists
(oracle_c*_main.txt), the TcLive "?" functions (rlive_KEY.txt), Spawn's ovl jump-ins (ovl_jumpin_functions.tsv) and
frames_static.py's large frames (large_frames.tsv).
"""
import json
import os
import pathlib
import re
import sys

DP = pathlib.Path(__file__).resolve().parents[2]  # the repo this copy lives in
sys.path.insert(0, str(DP / "tools"))
sys.path.insert(0, str(DP))
from common import use_project  # noqa: E402

key, ws_arg, outdir = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
ws = use_project(ws_arg)
import selective_restamp as sr  # noqa: E402
from core import paths  # noqa: E402

INPUTS = pathlib.Path(__file__).resolve().parent / "inputs"

# r15-oracle classes (Spawn's r15_oracle_report.md, inputs/oracle_c*_main.txt) per game
ORACLE_CLASSES = {
    "tc": {"class 6 Duck decoder (TcCatalog ADPCM ranges)": ["8c09acc4", "8c09ae88", "8c09af40", "8c09b164"],
           "class 7 TcSq (open)": ["8c09cf44"]},
    "ps2": {"class 8 TcLive context switch": ["8c1831e4", "8c1846a0"]},
}
if key == "sp":
    for n in range(1, 6):
        f = INPUTS / f"oracle_c{n}_main.txt"
        if f.exists():
            ORACLE_CLASSES.setdefault("sp", {})[f"class {n}"] = re.findall(r"\b8c[0-9a-f]{6}\b", f.read_text())
    ORACLE_CLASSES["sp"]["class 5 TcJumpIn join"] = ["8c021282", "8c0129c6"]

harness = {d.name for d in paths.HARNESS.iterdir() if (d / "meta.json").is_file()}
matched = {p.stem for p in paths.MATCHED.glob("*.c")} & harness


def asm(a):
    d = paths.PACKETS / a
    fs = [d / "asm.txt"] if (d / "asm.txt").exists() else sorted(d.glob("asm_*.txt"))
    return "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in fs)


LINE = re.compile(r"^[ \t]+([0-9a-f]{8})[ \t]+(_?\S+)[ \t]*(.*?)[ \t]*(;.*)?$", re.M)  # one line each
ins = {a: [(int(m.group(1), 16), m.group(2).lstrip("_"), m.group(3).replace(" ", "")) for m in LINE.finditer(asm(a))]
       for a in harness if (paths.PACKETS / a).is_dir()}
meta = {a: json.loads((paths.HARNESS / a / "meta.json").read_text(encoding="utf-8")) for a in harness}
callees = {a: [c["addr"] if isinstance(c, dict) else c for c in m.get("callees") or []] for a, m in meta.items()}

sets = {}


def add(name, fns, pool=None):
    sets[name] = sorted(set(fns) & (matched if pool is None else pool))


# --change fret+fargs (TcFret, TcFguide)
add("fret", sr.fret_candidates(paths.HARNESS))
add("fargs", sr.fargs_candidates(paths.HARNESS, paths.PACKETS))
# r15-oracle classes
LEFT_OUT = {"sp": {  # r15_oracle_report.md: no prepare, no requeue
    "8c059630": "AICA mailbox: port-replaced", "8c059740": "AICA mailbox: port-replaced",
    "8c05a490": "AICA wave RAM: port-replaced", "8c058300": "real MMIO: can't be fixed",
    "8c010080": "interrupt counter poll: can't be fixed", "8c0123a4": "interrupt counter poll: can't be fixed",
    "8c024cf8": "mid-entry stack switch: excluded", "8c024cbe": "P2 uncached entry: excluded (TcCatalog)",
    "8c012b90": "TcJumpIn: joins 8c0129c6", "8c012be0": "TcJumpIn: joins 8c0129c6",
    "8c012bf0": "TcJumpIn: joins 8c0129c6", "8c021536": "TcJumpIn: joins 8c021282"}}.get(key, {})
for cls, fns in ORACLE_CLASSES.get(key, {}).items():  # mostly refused/unmatched today: prepare, then requeue
    add(f"r15-oracle {cls}", [a for a in fns if a not in LEFT_OUT], harness | set(fns))
# TcExit.halts: an endless `bra .` in the body, or a callee that is one
halt = {a for a, xs in ins.items() if any(mn == "bra" and ops == f"0x{pc:08x}" for pc, mn, ops in xs)}
add("TcExit halts", halt | {a for a, cs in callees.items() if set(cs) & halt})
# xemu: every ftrc function (TcFpu saturating ftrc; runtime sh4_ftrc) + the ghmem pair
ftrc = {a for a, xs in ins.items() if any(mn == "ftrc" for _, mn, _ in xs)}
add("xemu ftrc", ftrc)
if key == "ps2":
    add("xemu ghmem (uninitializedRead)", ["8c0682b4", "8c1453cc"])
# TcLive large frames (r15 moved by a register): the functions, and the callers whose callee may change r8..r14
# through one (where TcLive's per-callee record lands)
lf = {a for a, xs in ins.items() if any(re.fullmatch(r"r(\d+),r15", ops) and ops != "r15,r15" and mn in ("add", "sub", "mov")
                                         for _, mn, ops in xs)}
fns = {}
for a in os.listdir(paths.PACKETS):
    p = paths.PACKETS / a
    if (p / "meta.json").exists():
        own, pushed, pref = sr.asm_facts(str(p))
        pm = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        fns[a] = (own - pushed, pushed, [c["addr"] if isinstance(c, dict) else c for c in pm.get("callees") or []])
writes = {a: set(f[0]) for a, f in fns.items()}
changed = True
while changed:
    changed = False
    for a, (_, pushed, cs) in fns.items():
        for c in cs:
            more = writes.get(c, set()) - pushed - writes[a]
            if more:
                writes[a] |= more
                changed = True
tainted = {a for a in lf if writes.get(a)}
changed = True
while changed:
    changed = False
    for a, (_, pushed, cs) in fns.items():
        if a not in tainted and writes.get(a) and any(c in tainted for c in cs):
            tainted.add(a)
            changed = True
GAME = {"tc": "tc", "sp": "sp", "ovl": "ovl", "ps2": "ps2"}[key]
spawn_lf = {r[1] for r in (line.split("\t") for line in (INPUTS / "large_frames.tsv").read_text().splitlines()
                           if line and not line.startswith("#")) if r[0] == GAME}
add("TcLive large-frame packets", lf | spawn_lf)  # operand scan here | frames_static.py (Spawn)
add("TcLive callers of a large-frame r8..r14 writer", {a for a, cs in callees.items() if set(cs) & tainted})
# ovl TcJumpIn: each entry joins its parent at the re-stamp (inputs/ovl_jumpin_functions.tsv); the parents are
# re-stamped, the entries stop being harnesses of their own
jumpins = {}
if key == "ovl":
    for line in (INPUTS / "ovl_jumpin_functions.tsv").read_text().splitlines():
        if line and not line.startswith("#"):
            entry, parent = line.split("\t")[:2]
            jumpins[entry] = parent
    add("ovl TcJumpIn parents (entries join them)", set(jumpins.values()), harness)


def read_list(p):
    p = pathlib.Path(p)
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"\s*(8c[0-9a-f]{6})\b\s*(.*)", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


restamp = {}
for name, fns in sets.items():
    for a in fns:
        restamp.setdefault(a, []).append(name)

# after the re-stamp: rejudge (verdict may change with no new C) and requeue (needs new C)
rejudge, requeue = {}, {}
for a, why in read_list(ws / "state/float_gaps/rejudge.txt").items():
    rejudge.setdefault(a, []).append("float_gaps rejudge: " + why)
for a, why in read_list(ws / "state/xemu_fix/rejudge.txt").items():
    rejudge.setdefault(a, []).append("xemu rejudge" + (": " + why if why else ""))
for a in read_list(INPUTS / f"rlive_{key}.txt"):
    rejudge.setdefault(a, []).append('TcLive "?" (r_live) re-check')
for a in sets.get("xemu ftrc", []):
    rejudge.setdefault(a, []).append("ftrc: judge only on its re-stamped harness (new runtime)")
if key == "ps2":
    rejudge.setdefault("8c182850", []).append("mirror rule (PASS 0/98 on the fixed-oracle staging harness)")
for a, why in read_list(ws / "state/float_gaps/requeue.txt").items():
    requeue.setdefault(a, []).append("float_gaps: " + why)
for a, why in read_list(ws / "state/xemu_fix/requeue.txt").items():
    requeue.setdefault(a, []).append("xemu: " + (why or "requeue"))
for a, why in read_list(ws / "state/ptr_views/requeue.txt").items():
    requeue.setdefault(a, []).append("ptr_views: " + why)
for a, why in read_list(ws / "state/mirror_ptrs/requeue.txt").items():
    requeue.setdefault(a, []).append("mirror review: " + (why or "can differ"))
for name, fns in sets.items():
    if name.startswith("r15-oracle"):
        for a in fns:
            if a not in matched and "class 7" not in name:
                requeue.setdefault(a, []).append(f"{name}: requeue on the new harness (probe: valid vectors)")
for parent in sorted(set(jumpins.values()) - matched):
    requeue.setdefault(parent, []).append("TcJumpIn parent: queue it on the joined harness")
if key == "sp":
    requeue.setdefault("8c021282", []).append("TcJumpIn joined 8c021536: re-match (live C tail-calls it)")
if key == "ps2":
    requeue.setdefault("8c1453cc", []).append("ghmem: C copies Ghidra's zeroing (FAIL 96/98 on the hint harness)")
for d in (rejudge, requeue):
    for a in list(d):
        if a not in matched:
            d[a].append("(not matched now)")

out = outdir / key
out.mkdir(parents=True, exist_ok=True)


def write(name, d):
    (out / f"{name}.txt").write_text("".join(f"{a}  # {'; '.join(v)}\n" for a, v in sorted(d.items())), encoding="utf-8")


write("restamp", restamp)
write("rejudge", rejudge)
write("requeue", requeue)
left_out = {a: [w] for a, w in LEFT_OUT.items()}
for entry, parent in jumpins.items():
    left_out.setdefault(entry, []).append(f"TcJumpIn: joins {parent} (re-stamped)")
write("left_out", left_out)
summary = {"key": key, "workspace": str(ws), "harnesses": len(harness), "matched": len(matched),
           "sets": {k: len(v) for k, v in sets.items()}, "restamp": len(restamp),
           "restamp_ftrc": len([a for a in restamp if a in set(sets.get("xemu ftrc", []))]),
           "rejudge": len(rejudge), "requeue": len(requeue),
           "requeue_in_restamp": len(set(requeue) & set(restamp)), "rejudge_in_restamp": len(set(rejudge) & set(restamp))}
(outdir / f"{key}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
print(json.dumps(summary))
