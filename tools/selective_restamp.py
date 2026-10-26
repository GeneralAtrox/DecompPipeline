"""Selective re-stamp after an oracle change that only some harnesses can see: re-prepare those, prove on a random
sample that the others come out byte-identical, then give the others today's stamp without a prepare.

An oracle script change makes every stamp old (core/oracle_stamp hashes every *.java), but a change that acts only
on some functions leaves the other harnesses as they are. The TcLive/TcSq change (callees changing r8..r14, the
store queues) acts only on
- callers of a callee that may leave r8..r14 changed (header lines K): every function with such a callee, by a scan
  of the packets' asm (a writer changes rN without pushing it, or calls a writer; a superset of TcSum's writes is
  wanted, and the sample is the check of it);
- functions with their own `pref @rN`, rN one of r8..r14 or a copy of one (queue_pref; header line S);
- harnesses a rescue pass made (oracle_summary "rescued": TcCount.zeroCut acts in the overtime rescue only).
--change fret is the TcFret change (float callee results) instead: it acts only on functions with a callee whose
prototype returns f32 (harness meta.json callee records; Ghidra's signature where callee_drift adopted another).
--change fargs is the TcFguide change (float-guided arguments): a function with an f32 parameter whose packet asm
has a `mova` and an `fcmp/` (a superset of the functions TcFguide harvests a constant for). --change late is the
TcLate change (results for calls after the 16th): a harness with a vector that made more than 16 calls (expected.txt
CALL lines); in any other the draws and line C stay byte for byte. --change spin is the TcSpin change (a wait loop
nothing in it ends is a halt): opt-in, so only the functions project.json "spin_waits" lists. Changes join with +
(--change fret+fargs: either alters the harness).
Only harnesses current under the scripts before the change (--old-scripts: that commit's ghidra_scripts folder) are
refreshable; any other is stale for its own reasons and left to tools/reprepare_stale.py --stamps. --old-scripts can
be given more than once when the harnesses were stamped at different times (TC: the 2026-10-16 full re-stamp, then
112 more on 10-17), and a folder can be a baseline: ghidra_scripts/ with the *.java, and the project's catalog.json
and code_ranges.txt of that time beside it when those changed since (both are stamp inputs; keep their bytes,
CRLF included). --list FILE adds the addresses in FILE (first word of a line) to the candidates: a combined
re-stamp's own set (docs/restamp_r15/<game>/restamp.txt).

Phases (each resumable; run under tools/bounded.py, servers stopped):
  --phase sample  re-prepare --sample N refreshable non-candidates (seeded), each harness saved first under
                  state/stamp_refresh/before/, and compare vectors.txt, expected.txt and meta.json (its stamp aside)
                  byte for byte: the result goes to state/stamp_refresh.json. A harness whose only difference is
                  callee records the prepare took from callees re-prepared since (callee_drift.adopt: a stale
                  caller, not the scripts; the copy is compared after the same adoption) counts as clean
                  ("drift_only"; it is current after its prepare)
  --phase refresh with a sample all clean: the remaining refreshable non-candidates get today's stamp and
                  "oracle_refresh": "selective refresh, sample N, result" in their meta.json (none if any differed:
                  then re-stamp the whole workspace with reprepare_stale.py --stamps), except callers whose callee
                  records differ: they stay stale, listed in state/stamp_refresh_stale.txt for reprepare_stale.py
  --phase must    re-prepare the refreshable candidates (at most --max per run) and judge each one's matched raw C
usage: python tools/selective_restamp.py --project DIR --old-scripts DIR [--old-scripts DIR ...]
       --phase sample|refresh|must [--sample 100] [--seed 14] [--max 100] [--slots N]
       [--change r14|fret|fargs|late|spin|fret+fargs|none] [--list FILE] [--dry]
Exit 0 when the phase finished clean (all identical; nothing left; every judged raw C passes), 1 otherwise.
"""
import argparse
import datetime
import json
import os
import pathlib
import random
import re
import shutil
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import server_state, use_project  # noqa: E402
from core import callee_drift  # noqa: E402  (pure: no core.paths)

REGS = ["r%d" % n for n in range(8, 15)]
ASM = re.compile(r"^\s*[0-9a-f]{8}\s+_?(\S+)\s+(.*?)\s*(;.*)?$")
TARGET = re.compile(r"^\s*[0-9a-f]{8}\s+_?(?:bra|bsr|bt|bf|bt/s|bf/s)\s+0x([0-9a-f]{8})")
REG = re.compile(r"^r\d+$")


def queue_pref(lines):
    """Whether a `pref @rN` sends a store queue through r8..r14 (TcSq.of): rN one of them, or a register copied from
    one in the same block (`mov r14,r3`) and since moved only by add or an @-r3 / @r3+ access (Toy Commander
    8c075898). A block starts at a branch target and after a call (r0..r7 are the callee's); any other write to the
    register ends the copy. TcSq.of also wants exactly one of r8..r14 in the runtime state for a copy: a superset."""
    starts = {m.group(1) for m in map(TARGET.match, lines) if m}
    root, call = {}, False  # register -> the one of r8..r14 it holds (plus an offset); the last line was a call
    for ln in lines:
        m = ASM.match(ln)
        if not m:
            continue
        slot = ln.split()[1].startswith("_")
        if ln.split()[0].lower() in starts or call and not slot:
            root = {}
        mn, ops = m.group(1).lower(), [o.strip() for o in m.group(2).split(",")]
        after_call, call = call and slot, mn in ("jsr", "bsr", "bsrf")
        if mn == "pref" and len(ops) == 1 and ops[0].startswith("@"):
            if ops[0][1:] in REGS or ops[0][1:] in root:
                return True
        elif mn == "mov" and len(ops) == 2 and REG.match(ops[0]) and REG.match(ops[1]):
            src = ops[0] if ops[0] in REGS else root.get(ops[0])
            if src and ops[1] not in REGS:
                root[ops[1]] = src
            else:
                root.pop(ops[1], None)
        elif mn != "add" and not mn.startswith(("cmp", "tst")):
            root.pop(ops[-1], None)
        if after_call:  # the delay slot ran, then the callee owns r0..r7: a copy made there is gone too
            root = {}
    return False


def asm_facts(pkt):
    """(registers of r8..r14 the asm changes, those it pushes, whether it sends a store queue through r8..r14)."""
    own, pushed, pref = set(), set(), False
    for name in sorted(os.listdir(pkt)):
        if not (name.startswith("asm") and name.endswith(".txt")):
            continue
        with open(os.path.join(pkt, name), errors="replace") as f:
            lines = f.readlines()
            pref = pref or queue_pref(lines)
            for ln in lines:
                m = ASM.match(ln)
                if not m:
                    continue
                mn, ops = m.group(1).lower(), [o.strip() for o in m.group(2).split(",")]
                for r in REGS:
                    if mn == "mov.l" and ops == [r, "@-r15"]:
                        pushed.add(r)
                    elif mn == "mov.l" and ops == ["@r15+", r]:
                        continue
                    elif (ops and ops[-1] == r and not mn.startswith(("cmp", "tst"))) or any(
                            o in ("@%s+" % r, "@-%s" % r) for o in ops):
                        own.add(r)
    return own, pushed, pref


def candidates(packets):
    """The functions whose harness the change can alter: callers of a (transitive) r8..r14 writer, and pref @rN."""
    fns = {}
    for a in os.listdir(packets):
        meta = os.path.join(packets, a, "meta.json")
        if not os.path.exists(meta):
            continue
        with open(meta, encoding="utf-8") as f:
            m = json.load(f)
        own, pushed, pref = asm_facts(os.path.join(packets, a))
        callees = [c["addr"] if isinstance(c, dict) else c for c in m.get("callees") or []]
        fns[a] = (own - pushed, pushed, pref, callees)
    writes = {a: set(f[0]) for a, f in fns.items()}
    changed = True
    while changed:
        changed = False
        for a, (_, pushed, _, callees) in fns.items():
            for c in callees:
                add = writes.get(c, set()) - pushed - writes[a]
                if add:
                    writes[a] |= add
                    changed = True
    return {a for a, f in fns.items() if f[2] or any(writes.get(c) for c in f[3])}


def rescued(harness):
    """The functions whose harness a rescue pass made (oracle_summary.json "rescued")."""
    out = set()
    for d in pathlib.Path(harness).iterdir():
        p = d / "oracle_summary.json"
        if p.is_file() and json.loads(p.read_text(encoding="utf-8") or "{}").get("rescued"):
            out.add(d.name)
    return out


def fret_candidates(harness):
    """The functions whose harness TcFret can alter: a callee record whose (Ghidra) prototype returns f32."""
    out = set()
    for d in pathlib.Path(harness).iterdir():
        m = (d / "meta.json").is_file() and json.loads((d / "meta.json").read_text(encoding="utf-8") or "{}")
        if m and any((c.get("ghidra_signature") or c.get("signature") or "").startswith("f32 ")
                     for c in m.get("callees") or [] if isinstance(c, dict)):
            out.add(d.name)
    return out


def fargs_candidates(harness, packets):
    """The functions whose harness TcFguide can alter: an f32 parameter, and a `mova` and an `fcmp/` in the asm."""
    out = set()
    for d in pathlib.Path(harness).iterdir():
        m = (d / "meta.json").is_file() and json.loads((d / "meta.json").read_text(encoding="utf-8") or "{}")
        asm = pathlib.Path(packets) / d.name / "asm.txt"
        if (m and any(isinstance(q, dict) and q.get("kind") == "f32" for q in m.get("params") or [])
                and asm.is_file()):
            text = asm.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\bmova\b", text) and "fcmp/" in text:
                out.add(d.name)
    return out


def late_candidates(harness, calls=16):
    """The functions whose harness TcLate can alter: a vector that made more than `calls` calls (expected.txt)."""
    out = set()
    for d in pathlib.Path(harness).iterdir():
        p = d / "expected.txt"
        if not p.is_file():
            continue
        n = 0
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            n = 0 if line.startswith("V ") else n + line.startswith("CALL ")
            if n > calls:
                out.add(d.name)
                break
    return out


def spin_candidates(cfg):
    """The functions whose harness TcSpin can alter: those project.json "spin_waits" lists (compare.spin_wait; an
    entry without its evidence is refused). Every other harness stays byte for byte."""
    from core import compare
    return {x.lower() for x in cfg.get("spin_waits") or {} if compare.spin_wait(cfg, x)}


def harness_bytes(d, sig_of=None):
    """What must stay identical: vectors.txt, expected.txt, and meta.json without its stamp keys. With sig_of
    (callee_drift.Signatures), meta.json's callee records first take the callees' own harness signatures, as
    ghidra.prepare's callee_drift.adopt does, and drop a "ghidra_signature" equal to the signature (adopt keeps an
    earlier one; a fresh prepare records none where Ghidra's view already agrees)."""
    out = {}
    for n in ("vectors.txt", "expected.txt"):
        p = d / n
        out[n] = p.read_bytes() if p.exists() else None
    p = d / "meta.json"
    if p.exists():
        m = json.loads(p.read_text(encoding="utf-8"))
        for k in ("oracle_stamp", "oracle_refresh"):
            m.pop(k, None)
        if sig_of:
            m.setdefault("addr", d.name)
            callee_drift.adopt(m, sig_of)
            for c in m.get("callees") or []:  # Ghidra's view now agrees with the callee: a fresh prepare has no key
                if c.get("ghidra_signature") == c.get("signature"):
                    c.pop("ghidra_signature")
        out["meta.json"] = json.dumps(m, sort_keys=True).encode()
    return out


def compare(before, after, sig_of):
    """A sampled harness against its copy from before the prepare: "same"; "drift" when the only difference is
    callee records the prepare took from callees re-prepared since (callee_drift.adopt: a stale caller, not the
    scripts); otherwise the files that differ."""
    b, n = harness_bytes(before), harness_bytes(after)
    if b == n:
        return "same"
    b, n = harness_bytes(before, sig_of), harness_bytes(after, sig_of)
    return "drift" if b == n else sorted(k for k in b if b[k] != n.get(k))


def sample_clean(record):
    """Every sampled harness came out identical or differing only by callee drift."""
    n = len(record.get("sample") or [])
    clean = record.get("identical", 0) + len(record.get("drift_only") or [])
    return bool(n) and not record.get("differ") and clean == n


def refresh_split(rest, harness):
    """(the harnesses to refresh, {caller: drift} of those left stale): a caller whose callee records differ from
    the callees' harnesses is stale for its own reason and is not refreshed (tools/reprepare_stale.py)."""
    stale = callee_drift.scan(harness, set(rest))
    return [x for x in rest if x not in stale], stale


def baseline(folder):
    """(scripts {name: path}, catalog or None, code ranges or None) of an --old-scripts folder: its *.java, or a
    baseline folder's ghidra_scripts/*.java with the catalog.json and code_ranges.txt beside it."""
    d = pathlib.Path(folder)
    sd = d / "ghidra_scripts" if (d / "ghidra_scripts").is_dir() else d
    cat, rng = d / "catalog.json", d / "code_ranges.txt"
    return ({p.name: p for p in sd.glob("*.java")}, cat if sd != d and cat.is_file() else None,
            rng if sd != d and rng.is_file() else None)


def listed(path):
    """The addresses of a list file: the first word of each line, comments (#) left out."""
    out = set()
    for line in pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"\s*([0-9a-fA-F]{8})\b", line.split("#", 1)[0])
        if m:
            out.add(m.group(1).lower())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--old-scripts", required=True, action="append",
                    help="the target's ghidra_scripts folder before the change, or a baseline folder (docstring)")
    ap.add_argument("--phase", required=True, choices=("sample", "refresh", "must"))
    ap.add_argument("--sample", type=int, default=100)
    ap.add_argument("--seed", type=int, default=14)
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--slots", type=int)
    ap.add_argument("--change", choices=("r14", "fret", "fargs", "late", "spin", "fret+fargs", "none"), default="r14",
                    help="which oracle change (docstring)")
    ap.add_argument("--list", action="append", default=[], help="a file of more candidate addresses")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    ws = use_project(a.project)
    if not a.dry and a.phase != "refresh" and server_state(ws, timeout=3) is not None:
        sys.exit("the project's server is running: stop it first (prepare needs the master Ghidra project)")
    from core import cascade, ghidra, oracle_fresh, oracle_stamp, paths, refine
    cfg = paths.config()
    if a.slots:
        cfg["ghidra_parallel"] = a.slots
    new = oracle_fresh.Stamper(cfg)
    olds = []
    for folder in a.old_scripts:
        scripts, catalog, ranges = baseline(folder)
        if not scripts:
            sys.exit(f"no *.java in {folder}")
        old = oracle_fresh.Stamper(cfg)
        old.scripts = dict(scripts, **{"TcInputs.java": paths.INPUTS})
        if ranges:
            old.facts = oracle_stamp.facts_hashed(ghidra.facts_body(cfg).replace(str(paths.CODE_RANGES), str(ranges)))
        olds.append((old, catalog))

    def was_current(st, x):
        for old, catalog in olds:
            keep, paths.CATALOG = paths.CATALOG, catalog or paths.CATALOG
            try:
                if st == old.today(x):
                    return True
            finally:
                paths.CATALOG = keep
        return False

    cand = (candidates(paths.PACKETS) | rescued(paths.HARNESS) if a.change == "r14" else
            (fret_candidates(paths.HARNESS) if "fret" in a.change else set())
            | (fargs_candidates(paths.HARNESS, paths.PACKETS) if "fargs" in a.change else set())
            | (late_candidates(paths.HARNESS) if "late" in a.change else set())
            | (spin_candidates(cfg) if "spin" in a.change else set()))
    for f in a.list:
        cand |= listed(f)
    current, refreshable, other = [], [], []
    for x in oracle_fresh.matched_functions():
        st = (paths.read_json(paths.HARNESS / x / "meta.json") or {}).get("oracle_stamp")
        (current if st == new.today(x) else refreshable if was_current(st, x) else other).append(x)
    must = [x for x in refreshable if x in cand]
    rest = [x for x in refreshable if x not in cand]
    print(f"{len(current)} current, {len(refreshable)} refreshable ({len(must)} candidates, {len(rest)} not), "
          f"{len(other)} stale for other reasons (reprepare_stale.py --stamps); {len(cand)} candidates in packets",
          flush=True)
    record_path = paths.STATE / "stamp_refresh.json"
    record = paths.read_json(record_path) or {}
    workers = max(1, int(cfg.get("ghidra_parallel", 1)))

    def prepare(x):
        with ghidra.slot(cfg) as i:
            ghidra.prepare(cfg, x, slot_index=i)

    if a.phase == "sample":
        pool = sorted(set(rest) | set(record.get("sample", [])), key=lambda x: int(x, 16))
        sample = record.get("sample") or sorted(random.Random(a.seed).sample(pool, min(a.sample, len(pool))),
                                                key=lambda x: int(x, 16))
        before = paths.STATE / "stamp_refresh" / "before"
        todo = [x for x in sample if x in rest]
        print(f"sample: {len(sample)} (seed {a.seed}); {len(todo)} still to re-prepare", flush=True)
        if a.dry:
            return 0
        if not record.get("sample"):  # kept before any prepare: a run stopped midway resumes the same sample
            record.update(sample=sample, seed=a.seed)
            paths.write_json(record_path, record)
        for x in todo:
            if not (before / x).exists():
                shutil.copytree(paths.HARNESS / x, before / x)
        out = cascade.reprepare_all(todo, prepare, lambda x: None, workers=workers, log=lambda m: print(m, flush=True))
        same, drifted, diff = [], [], {}
        sig_of = callee_drift.Signatures(paths.HARNESS)
        for x in sample:
            if (paths.read_json(paths.HARNESS / x / "meta.json") or {}).get("oracle_stamp") != new.today(x):
                continue
            c = compare(before / x, paths.HARNESS / x, sig_of)
            (same.append(x) if c == "same" else drifted.append(x) if c == "drift" else diff.__setitem__(x, c))
        record.update(sample=sample, seed=a.seed, identical=len(same), drift_only=drifted, differ=diff,
                      errors=sorted(out["errors"]), done=len(same) + len(drifted) + len(diff),
                      date=datetime.date.today().isoformat())
        paths.write_json(record_path, record)
        print(f"sample: {len(same)} identical, {len(drifted)} differ only by callee drift (re-prepared, now current) "
              f"{drifted or ''}, {len(diff)} differ {diff or ''}; {len(out['errors'])} prepare error(s); "
              f"{len(sample) - record['done']} not done", flush=True)
        return 0 if sample_clean(record) else 1

    if a.phase == "refresh":
        n = len(record.get("sample") or [])
        if not sample_clean(record):
            sys.exit(f"refresh: no clean sample in {record_path} ({record or 'none'}): re-stamp the whole workspace")
        k = len(record.get("drift_only") or [])
        note = (f"selective refresh, sample {n}, {record['identical']} identical"
                + (f", {k} callee drift only" if k else "") + f" ({record['date']}, seed {record['seed']})")
        fresh, stale = refresh_split(rest, paths.HARNESS)
        print(f"refresh: {len(fresh)} harness(es) get today's stamp: {note}" + (" [dry]" if a.dry else ""), flush=True)
        if stale:
            print(f"refresh: {len(stale)} caller(s) with callee records that differ stay stale, for "
                  f"tools/reprepare_stale.py --project {a.project} (state/stamp_refresh_stale.txt): "
                  + callee_drift.describe(stale, 6), flush=True)
        if a.dry:
            return 0
        for x in fresh:
            mp = paths.HARNESS / x / "meta.json"
            meta = paths.read_json(mp)
            meta["oracle_stamp"], meta["oracle_refresh"] = new.today(x), note
            paths.write_json(mp, meta)
        (paths.STATE / "stamp_refresh_stale.txt").write_text(
            "".join(f"{x}  # callee records differ: " + ", ".join(c for c, _, _ in stale[x]) + "\n"
                    for x in sorted(stale)),
            encoding="utf-8")
        record.update(refreshed=len(fresh), refresh_note=note, drifted_callers=sorted(stale))
        paths.write_json(record_path, record)
        return 0

    todo = must[:max(0, a.max)]
    print(f"must: {len(must)} candidate(s) to re-prepare; this run {len(todo)}" + (" [dry]" if a.dry else ""),
          flush=True)
    if a.dry:
        return 0

    def judge(x):
        try:
            return refine.harness_stale(cfg, x)
        except refine.infra.InfraError as e:
            return str(e)

    out = cascade.reprepare_all(todo, prepare, judge, workers=workers, log=lambda m: print(m, flush=True))
    record.setdefault("must_failed", [])
    record["must_failed"] = sorted(set(record["must_failed"]) | set(out["failed"]))
    paths.write_json(record_path, record)
    print(f"must: {len(out['prepared'])} re-prepared, {len(out['errors'])} error(s), {len(out['failed'])} raw C "
          f"failing ({' '.join(sorted(out['failed']))}); {len(must) - len(out['prepared'])} left", flush=True)
    return 0 if not out["errors"] and not out["failed"] and len(out["prepared"]) == len(must) else 1


if __name__ == "__main__":
    sys.exit(main())
