"""Automatic test-input hint search: read a function's asm for compare constants, zero tests, loop counters and pointer
uses, turn them into candidate `oracle/hints/ADDR.txt` lines, probe each with tools/oracle_probe.py, and keep a line
only when the probe shows more valid vectors or more reached blocks (greedy: keep the best, try the next on top).

usage: python tools/hint_search.py --project DIR --tag T [--max-probes N] [--baseline PROBE.json] [--json OUT]
           [--packets DIR] ADDR... | @FILE
  --baseline  an oracle_probe --json file of the same project, clone and tag-free state (no hints): round 0 is
              taken from it instead of probing again (CLAUDE.md "no redundant work")
  --packets   where ADDR/asm.txt is read from (default: PROJECT/packets, else the baseline probe's work dir)
Rounds: round k probes every still-active function with its k-th candidate on top of the lines already kept, in ONE
oracle_probe run (one Ghidra copy, no extra JVM setup per function). Kept lines are written to PROJECT/oracle/hints
(a private project copy: point --project at one, never at a live workspace). Nothing else is written.
Candidates (the hint grammar is in oracle/TcInputs.java applyHints):
  range rN LO HI / genrange LOC LO HI  around a constant the value is compared with (K-2..K+2), a zero test (0..2),
                                       a loop counter (dt, 1..8)
  genretfn 0xCALLEE 0 3                every call to a callee returns a small value (a loop a mocked callee never ends)
  ptr LOC                              a parameter or a pointer field the code dereferences
A line for a LOC already kept is skipped (a later line for the same LOC would overwrite the earlier one).
Exit 0 (a report tool).
Nearest existing: tools/oracle_probe.py (it probes a hand-written hint; this tool writes the candidates and keeps the
ones that gain, calling it) and the by-hand hints of docs/NEW_GAME.md section 4.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
INSN = re.compile(r"^\s+([0-9a-f]{8})\s+(_?)([a-z0-9./]+)\s*([^;]*?)\s*(?:;(.*))?$")
RAM = (0x8C000000, 0x8D000000)
PARAMS = ("r4", "r5", "r6", "r7")


def num(s):
    s = s.strip().lstrip("#")
    return int(s, 16) if s.lower().startswith(("0x", "-0x")) else int(s)


def loc_of(base, off):
    return base if not off else f"{base}+0x{off:x}"


def candidates(asm):
    """[(rank, key, line, why)] from the asm text. State per register: ("param", rN, off) the parameter register
    plus off, ("const", K), ("mem", LOC) the word at LOC (rN+off, 0xGLOBAL, or a pointer chain LOC+off)."""
    st = {r: ("param", r, 0) for r in PARAMS}
    seen, out, ptrs = {}, [], {}

    def add(rank, key, line, why):
        if key not in seen:
            seen[key] = (rank, key, line, why)

    def hint_for(s, lo, hi, why, rank):
        if s and s[0] == "param" and s[2] == 0:
            add(rank, ("v", s[1]), f"range {s[1]} {max(lo, 0)} {hi}", why)
        elif s and s[0] == "mem":
            add(rank, ("v", s[1]), f"genrange {s[1]} {max(lo, 0)} {hi}", why)

    def near(s, k, why):
        if s and s[0] == "param" and s[2] == 0 or s and s[0] == "mem":
            if not RAM[0] <= (k & 0xFFFFFFFF) < RAM[1]:
                kk = k & 0xFFFFFFFF
                hint_for(s, kk - 2, kk + 2, f"compare with {k:#x}: {why}", 0 if kk else 1)

    def use_ptr(s):
        if s and s[0] == "param":
            ptrs.setdefault(("p", s[1]), f"ptr {s[1]}")
        elif s and s[0] == "mem":
            ptrs.setdefault(("p", s[1]), f"ptr {s[1]}")

    for raw in asm.splitlines():
        m = INSN.match(raw)
        if not m:
            continue
        _, _, op, args, comment = m.groups()
        a = [x.strip() for x in re.split(r",(?![^()]*\))", args)] if args else []
        if op in ("jsr", "bsr", "jmp", "braf", "bsrf"):
            cm = re.search(r"call ([0-9a-f]{8})", comment or "")
            if cm and op in ("jsr", "bsr"):
                add(1.5, ("c", cm.group(1)), f"genretfn 0x{cm.group(1)} 0 3", "callee return decides a loop or branch")
            for r in ("r0", "r1", "r2", "r3", *PARAMS):
                st.pop(r, None)
            continue
        if op == "mov" and len(a) == 2 and a[0].startswith("#"):
            st[a[1]] = ("const", num(a[0]) & 0xFFFFFFFF)
        elif op == "mov" and len(a) == 2 and a[0].startswith("r") and a[1].startswith("r"):
            if a[0] in st:
                st[a[1]] = st[a[0]]
            else:
                st.pop(a[1], None)
        elif op in ("mov.l", "mov.w") and len(a) == 2 and a[0].startswith("0x") and comment and "= 0x" in comment:
            st[a[1]] = ("const", int(comment.split("= 0x")[1].split()[0], 16))
        elif op == "add" and len(a) == 2 and a[0].startswith("#"):
            s, k = st.get(a[1]), num(a[0])
            if s and s[0] == "param":
                st[a[1]] = ("param", s[1], s[2] + k)
            elif s and s[0] == "const":
                st[a[1]] = ("const", (s[1] + k) & 0xFFFFFFFF)
            else:
                st.pop(a[1], None)
        elif op.startswith("mov.") and len(a) == 2 and "@" in a[0] and a[1].startswith("r"):  # load
            mm = re.match(r"@\((-?[0-9a-fx]+),(r\d+)\)$|@(r\d+)\+?$", a[0])
            s = None
            if mm:
                base = st.get(mm.group(2) or mm.group(3))
                d = num(mm.group(1)) if mm.group(1) else 0
                if base and base[0] == "param":
                    s = ("mem", loc_of(base[1], base[2] + d))
                elif base and base[0] == "const" and RAM[0] <= base[1] < RAM[1]:
                    s = ("mem", f"0x{base[1] + d:x}")
                elif base and base[0] == "mem":
                    s = ("mem", loc_of(base[1], d))
                use_ptr(base)
            if s:
                st[a[1]] = s
            else:
                st.pop(a[1], None)
        elif op.startswith("mov.") and len(a) == 2 and "@" in a[1]:  # store: the base is used as a pointer
            mm = re.match(r"@-?\(?(?:-?[0-9a-fx]+,)?(r\d+)\)?\+?$", a[1])
            if mm:
                use_ptr(st.get(mm.group(1)))
        elif op == "cmp/eq" and len(a) == 2 and a[0].startswith("#"):
            near(st.get("r0"), num(a[0]), "cmp/eq")
        elif op.startswith("cmp/") and len(a) == 2 and a[0].startswith("r") and a[1].startswith("r"):
            for x, y in ((a[0], a[1]), (a[1], a[0])):
                if st.get(y, ("",))[0] == "const":
                    near(st.get(x), st[y][1], op)
        elif op == "tst" and len(a) == 2 and a[0] == a[1]:
            hint_for(st.get(a[0]), 0, 2, "zero test", 1)
        elif op in ("cmp/pz", "cmp/pl") and a:
            hint_for(st.get(a[0]), 0, 2, op, 1)
        elif op == "dt" and a:
            s = st.get(a[0])
            if s and s[0] == "param" and s[2] == 0:
                add(1, ("v", s[1]), f"range {s[1]} 1 8", "loop counter (dt)")
            st.pop(a[0], None)
        elif len(a) >= 1 and op not in ("cmp/eq", "tst", "fmov.s", "fmov"):
            st.pop(a[-1], None)
    out = sorted(seen.values(), key=lambda c: c[0])
    for key, line in ptrs.items():
        if ("v", key[1]) not in seen:
            out.append((2, key, line, "dereferenced"))
    return out


def order_lines(lines):
    """ptr lines first (a range line on the same register comes after and wins), then range/genrange."""
    return sorted(lines, key=lambda l: not l.startswith("ptr"))


def gain(old, new):
    """True when `new` (a probe result) beats `old`: more blocks reached, or (below 16 valid) more valid vectors,
    and never fewer valid vectors than the 16 the judge needs."""
    def valid(r):
        return min(r["dev_valid"], r["hidden_valid"])

    def blocks(r):
        return int(str(r["blocks"]).split("/")[0] or 0) if "/" in str(r["blocks"]) and "None" not in str(r["blocks"]) else 0
    if new.get("error") or new.get("unsupported"):
        return False
    if valid(new) < min(16, valid(old)):
        return False
    return blocks(new) > blocks(old) or (valid(old) < 16 and valid(new) > valid(old))


def run_probe(project, tag, addrs, out_json):
    cmd = [sys.executable, "-B", str(TOOL / "tools" / "oracle_probe.py"), "--project", str(project), "--tag", tag,
           "--jobs", "1", "--json", str(out_json), *addrs]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    res = json.loads(pathlib.Path(out_json).read_text(encoding="utf-8"))["results"]
    return {r["addr"]: r for r in res}


def search(project, tag, addrs, asm_of, baseline, max_probes, log=print, probe=run_probe, save=None):
    project = pathlib.Path(project)
    hints = project / "oracle" / "hints"
    hints.mkdir(parents=True, exist_ok=True)
    state = {a: {"best": baseline[a], "kept": [], "cands": [c for c in candidates(asm_of(a))], "i": 0, "probes": 0,
                 "secs": 0.0, "log": []} for a in addrs}
    for rnd in range(1, max_probes + 1):
        trial = {}
        for a, s in state.items():
            kept_keys = {l.split()[1] for l in s["kept"]}
            while s["i"] < len(s["cands"]) and s["cands"][s["i"]][2].split()[1] in kept_keys:
                s["i"] += 1
            if s["i"] < len(s["cands"]) and s["probes"] < max_probes:
                trial[a] = s["cands"][s["i"]]
        if not trial:
            break
        for a, c in trial.items():
            (hints / f"{a}.txt").write_text("\n".join(order_lines(state[a]["kept"] + [c[2]])) + "\n", encoding="utf-8",
                                            newline="\n")
        t = time.time()
        res = probe(project, tag, list(trial), pathlib.Path(project).parent / f"{project.name}_{tag}_r{rnd}.json")
        per = (time.time() - t) / len(trial)
        for a, c in trial.items():
            s, r = state[a], res.get(a, {"error": "no result", "dev_valid": 0, "hidden_valid": 0, "blocks": "0/0"})
            s["probes"] += 1
            s["secs"] += per
            s["i"] += 1
            ok = gain(s["best"], r)
            s["log"].append({"line": c[2], "why": c[3], "dev": r.get("dev"), "hidden": r.get("hidden"),
                             "blocks": r.get("blocks"), "kept": ok})
            log(f"r{rnd} {a} {c[2]!r}: dev {r.get('dev')} hidden {r.get('hidden')} blocks {r.get('blocks')} "
                f"{'KEEP' if ok else 'drop'}")
            if ok:
                s["kept"].append(c[2])
                s["best"] = r
        for a, s in state.items():  # restore each function's file to its kept lines
            f = hints / f"{a}.txt"
            if s["kept"]:
                f.write_text("\n".join(order_lines(s["kept"])) + "\n", encoding="utf-8", newline="\n")
            elif f.exists():
                f.unlink()
        if save:
            save(state)
    return state


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--max-probes", type=int, default=6)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--packets")
    ap.add_argument("--json")
    ap.add_argument("addrs", nargs="+")
    a = ap.parse_args()
    addrs = []
    for x in a.addrs:
        addrs += pathlib.Path(x[1:]).read_text(encoding="utf-8").split() if x.startswith("@") else [x]
    base = {r["addr"]: r for r in json.loads(pathlib.Path(a.baseline).read_text(encoding="utf-8"))["results"]}
    pk = pathlib.Path(a.packets) if a.packets else pathlib.Path(a.project) / "packets"

    def asm_of(addr):
        f = pk / addr / "asm.txt"
        return f.read_text(encoding="utf-8") if f.exists() else ""
    t0 = time.time()
    def save(state):
        if a.json:
            pathlib.Path(a.json).write_text(json.dumps({"secs": round(time.time() - t0, 1), "functions": {
                k: {"kept": v["kept"], "probes": v["probes"], "secs": round(v["secs"], 1), "before": base[k],
                    "after": v["best"], "log": v["log"]} for k, v in state.items()}}, indent=1), encoding="utf-8")
    state = search(a.project, a.tag, addrs, asm_of, base, a.max_probes, save=save,
                   log=lambda m: print(f"[{time.time() - t0:5.0f}s] {m}", flush=True))
    save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
