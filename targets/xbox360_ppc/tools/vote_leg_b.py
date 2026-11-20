"""Leg B of the instruction vote: Ghidra's p-code emulation of every case's words, with the oracle's Xenon
corrections and vector unit (vote_ghidra/TcVote.java). For build box A or build box B (ghidra_home and java_home in
config.local.json); never on the main laptop. tools/x360_vote.py leg-b drives it:

  prepare  writes <work>/vote_code.bin (every distinct word sequence at its own address from 0x82010000, after 64 KB
           of zeros from the load address 0x82000000), <work>/cases.txt (leg A's case lines, with the function's address
           where leg A has its index; memory moved into the heap as in leg A), <work>/scripts (the target's
           ghidra_scripts, core/ghidra_shared/ppc, template/TcInputs.java and TcVote.java) and <work>/run.cmd;
  run      runs that one analyzeHeadless (import raw, no analysis, TcVote.java, delete the project), bounded;
  collect  reads <work>/out.txt into the same result JSON as leg A (x360_vote.make_result).

Nearest existing: core/ghidra.py runs the judge's headless jobs on a game's prepared project (TcPrepare per function);
this needs a throwaway raw import of test words and a per-case register dump, which no judge script prints.
"""
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
X360 = HERE.parent
ROOT = X360.parents[1]
for p in (HERE, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import vote_leg_a as A  # noqa: E402

LOAD = 0x82000000
LANG = "PowerPC:BE:64:64-32addr"


def layout(groups):
    """{group index: address}: each word sequence at its own 16-byte aligned address from A.BASE."""
    at, out = A.BASE, {}
    for g, words in enumerate(groups):
        out[g] = at
        at += (4 * len(words) + 15) & ~15
    return out


def prepare(work, cases):
    """Write the leg's inputs into work. Returns (plan, addresses) for collect()."""
    work.mkdir(parents=True, exist_ok=True)
    plan, groups = A.prepare(cases)
    addr = layout(groups)
    image = bytearray(A.BASE - LOAD)
    for g, words in enumerate(groups):
        image += bytes(addr[g] - LOAD - len(image))
        image += b"".join(w.to_bytes(4, "big") for w in words)
    (work / "vote_code.bin").write_bytes(bytes(image) + bytes(16))
    lines = []
    for i, (c, (fn, _, why)) in enumerate(zip(cases, plan)):
        if why is None and fn is not None:
            ln = A.case_lines(i, c, fn)
            ln[0] = f"C {i} {addr[fn]:08x}"
            lines += ln
    (work / "cases.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sc = work / "scripts"
    shutil.rmtree(sc, ignore_errors=True)
    sc.mkdir()
    for f in [*(X360 / "ghidra_scripts").glob("*.java"), *(ROOT / "core" / "ghidra_shared" / "ppc").glob("*.java"),
              X360 / "template" / "TcInputs.java", HERE / "vote_ghidra" / "TcVote.java"]:
        shutil.copy2(f, sc / f.name)
    (work / "run.cmd").write_text(" ".join(command(work, {"ghidra_home": "<ghidra_home>"})) + "\n", encoding="utf-8")
    return plan


def command(work, cfg):
    exe = pathlib.Path(cfg["ghidra_home"], "support", "analyzeHeadless.bat")
    return [str(exe), str(work / "proj"), "VoteB", "-import", str(work / "vote_code.bin"), "-processor", LANG,
            "-loader", "BinaryLoader", "-loader-baseAddr", hex(LOAD), "-noanalysis", "-scriptPath", str(work / "scripts"),
            "-postScript", "TcVote.java", str(work / "cases.txt"), str(work / "out.txt"), "-deleteProject"]


def run(work, cfg, timeout=2700):
    """One bounded headless run (under the 50-minute wait the user's rules set). (ok, message)."""
    import os
    if not cfg.get("ghidra_home") or not cfg.get("java_home"):
        return False, "config.local.json needs ghidra_home and java_home"
    (work / "proj").mkdir(exist_ok=True)
    env = dict(os.environ, JAVA_HOME=cfg["java_home"])
    env["PATH"] = str(pathlib.Path(cfg["java_home"], "bin")) + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run(command(work, cfg), cwd=work, env=env, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout} s (split the corpus with --files)"
    (work / "headless.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    if r.returncode or not (work / "out.txt").is_file():
        return False, f"analyzeHeadless exit {r.returncode}: see {work / 'headless.log'}"
    return True, "ran"


def collect(work):
    """{case index: parsed output} from out.txt."""
    return A.parse_out((work / "out.txt").read_text(encoding="utf-8"))


def all_named(_fn):
    """Leg B prints every register a case names from the emulator's state, so none falls back to its input."""
    return {"r": set(range(32)), "f": set(range(32)), "v": set(range(128))}
