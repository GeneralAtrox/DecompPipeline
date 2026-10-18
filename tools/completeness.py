"""Account for every byte of a project's game range: function code, literal-pool data, switch tables, instructions in
no function ("orphan"), padding, the project's explained runs (known_runs.tsv: data, dead code), or unknown. Orphan
runs, and unknown runs that look like code (an rts, a prologue), are listed: likely functions the catalog is missing.

usage: python tools/completeness.py --project DIR [--out FILE.md] [--min 8] [--state DIR] [--known FILE] [--check]
       (default out: DIR/COMPLETENESS.md; state: DIR/state; known: STATE/known_runs.tsv)
  --check  exit 1 when a byte is unexplained (unknown or orphan, outside known_runs.tsv) or an entry is stale, or when
           the workspace consistency check fails (core/doctor.py, also tools/doctor.py: artefacts of addresses that
           are no longer functions, matched functions without harness meta or matched C, game functions in no
           system: tools/assign_systems.py); it prints the readable
           denominator too. Bytes alone cannot say a removed function's files were retired (tools/retire.py).
Read-only on the project except for the output file. No Ghidra: it reads the binary, state/function_bodies.txt and
state/code_ranges.txt (function bodies and all instructions, written by TcCatalog), state/switch_tables.tsv (TcSwitch:
a table's base, entry kind and count) and decodes SH-4 PC-relative loads and branches itself, so it only supports SH-4
targets.

known_runs.tsv (hand-written, one run per line, tab-separated; '#' starts a comment):
  start  end  class  evidence  reason          start inclusive, end exclusive, hex
  class data: bytes the program reads as data (a float constant, a marker block); evidence "ptr:A,B": image words at
              A, B whose value (P0/P1/P2 folded) lies in the run. Checked: each word holds such a value.
  class dead: code no execution reaches (an alternate entry or stub nothing names, an epilogue after a tail jump);
              evidence "refs:A,B" or "refs:-": every reference into the run from outside it (an image word naming it,
              a bra/bsr/bt/bf or PC-relative load at an instruction byte or inside a dead run aiming into it). Checked:
              the references found are exactly those, and each lies inside a dead run.
An entry is stale when its evidence no longer holds or the catalog now explains its bytes (code, pool or a table).
Extra functions (project.json "extra_functions", core/scope.py: library code outside the game ranges that the project
decompiles) are reported on a line of their own (functions, code and literal-pool bytes), never mixed into the
game-range classes above.
"""
import argparse
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import use_project  # noqa: E402

RTS, NOP = 0x000B, 0x0009
# alignment fill: zeros, nop, and nop byte-swapped (0x0900, an invalid opcode some library objects pad with)
PAD_WORDS = (0x0000, NOP, 0x0900)
ORPHAN = 99  # the score of a run that is mostly disassembled instructions belonging to no function
CLASSES = ("code", "pool", "table", "data", "dead", "padding", "orphan", "unknown")


def fold(v):
    """A P0/P1/P2 mirror of main RAM (0x0c.., 0xac..) as the 0x8c.. address."""
    return (v & 0x00FFFFFF) | 0x8C000000 if (v >> 24) in (0x0C, 0x8C, 0xAC) else v


def pool_refs(img, base, lo, hi):
    """Addresses read by PC-relative loads inside [lo, hi): mov.w @(d,PC) (2 bytes), mov.l @(d,PC) (4), mova (4)."""
    refs = {}
    for pc in range(lo, hi, 2):
        op = struct.unpack_from("<H", img, pc - base)[0]
        if op >> 12 == 0x9:
            refs[pc + 4 + (op & 0xFF) * 2] = 2
        elif op >> 12 == 0xD or op >> 8 == 0xC7:
            refs[(pc & ~3) + 4 + (op & 0xFF) * 4] = 4
    return refs


def flow_target(op, pc):
    """The address a bra/bsr/bt/bf[/s] or a PC-relative load (mov.w, mov.l, mova) at pc names, else None."""
    if op >> 12 in (0xA, 0xB):
        d = op & 0xFFF
        return pc + 4 + 2 * (d - 0x1000 if d & 0x800 else d)
    if op >> 8 in (0x89, 0x8B, 0x8D, 0x8F):
        d = op & 0xFF
        return pc + 4 + 2 * (d - 0x100 if d & 0x80 else d)
    if op >> 12 == 0x9:
        return pc + 4 + (op & 0xFF) * 2
    if op >> 12 == 0xD or op >> 8 == 0xC7:
        return (pc & ~3) + 4 + (op & 0xFF) * 4
    return None


def looks_like_code(words):
    """A rough score: rts, PR save/restore and register pushes are rare in data."""
    score = sum(w == RTS for w in words) * 3 + sum(w in (0x4F22, 0x4F26) for w in words) * 2
    score += sum((w & 0xFF0F) == 0x2F06 for w in words)  # mov.l rN,@-r15
    return score


def ranges(path, skip):
    return [tuple(int(x, 16) for x in line.split()[skip:skip + 2])
            for line in path.read_text().splitlines() if line.strip()]


def table_bytes(path):
    """{address: "table"} for the entries of each switch table TcSwitch resolved (a sled is code, not a table)."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        p = line.split("\t")
        if line.startswith("#") or len(p) < 5 or not p[2].startswith(("braf.", "jmp.")):
            continue
        size = {"b": 1, "w": 2, "l": 4}[p[2][-1]]
        base = int(p[3], 16)
        out.update((x, "table") for x in range(base, base + int(p[4]) * size))
    return out


def read_known(path):
    """[(start, end, class, evidence, reason, line number)] from known_runs.tsv; exits on a malformed line."""
    out = []
    if not path or not path.exists():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        p = line.split("\t")
        if len(p) < 5 or p[2] not in ("data", "dead") or not p[3].startswith(("ptr:", "refs:")):
            sys.exit(f"{path}:{n}: want start, end, class (data|dead), evidence (ptr:..|refs:..), reason; got {line!r}")
        out.append((int(p[0], 16), int(p[1], 16), p[2], p[3], p[4], n))
    return out


def classify(img, base, game, bodies, insn, tables=None, known=()):
    """Totals per class, and the runs left outside functions: (start, end, score) for orphan and unknown bytes no
    known run explains, plus the bytes the catalog explains (code, pool, table) for checking known runs."""
    kind = dict(tables or {})
    for lo, hi in bodies:
        for x in range(lo, hi):
            kind[x] = "code"
        for r, n in pool_refs(img, base, lo, hi - (hi - lo) % 2).items():
            for x in range(r, r + n):
                kind.setdefault(x, "pool")
    explained = dict(kind)
    for s, e, cls, *_ in known:
        for x in range(s, e):
            kind.setdefault(x, cls)
    totals, runs = dict.fromkeys(CLASSES, 0), []
    for glo, ghi in game:
        x = glo
        while x < ghi:
            if x in kind:
                totals[kind[x]] += 1
                x += 1
                continue
            start = x
            while x < ghi and x not in kind:
                x += 1
            run = img[start - base:x - base]
            words = [struct.unpack_from("<H", run, i)[0] for i in range(0, len(run) - 1, 2)]
            if all(w in PAD_WORDS for w in words):
                totals["padding"] += len(run)
            elif sum(y in insn for y in range(start, x)) * 2 >= len(run):
                totals["orphan"] += len(run)
                runs.append((start, x, ORPHAN))
            else:
                totals["unknown"] += len(run)
                runs.append((start, x, looks_like_code(words)))
    return totals, runs, explained


def references(img, base, insn, dead):
    """{target: [source]}: image words (4-aligned, folded) and the flow targets of instructions at instruction bytes
    or inside dead runs."""
    refs = {}
    for a in range(base, base + len(img) - 3, 4):
        refs.setdefault(fold(struct.unpack_from("<I", img, a - base)[0]), []).append(a)
    pcs = {x & ~1 for x in insn}
    for s, e in dead:
        pcs.update(range(s & ~1, e, 2))
    for pc in sorted(pcs):
        if base <= pc and pc + 2 <= base + len(img):
            t = flow_target(struct.unpack_from("<H", img, pc - base)[0], pc)
            if t is not None:
                refs.setdefault(t, []).append(pc)
    return refs


def extras_bytes(img, base, bodies_path, extras):
    """(functions, code bytes, pool bytes) of the extra functions: their function_bodies.txt ranges, and the literal-
    pool bytes their PC-relative loads read outside every function body."""
    rows = [line.split() for line in bodies_path.read_text().splitlines() if line.strip()]
    mine = [(int(p[1], 16), int(p[2], 16)) for p in rows if p[0] in extras]
    every = set()
    for p in rows:
        every.update(range(int(p[1], 16), int(p[2], 16)))
    pool = set()
    for lo, hi in mine:
        for r, n in pool_refs(img, base, lo, hi - (hi - lo) % 2).items():
            pool.update(x for x in range(r, r + n) if x not in every)
    return len({p[0] for p in rows if p[0] in extras}), sum(hi - lo for lo, hi in mine), len(pool)


def check_known(img, base, game, known, explained, refs):
    """Stale known runs: [(line, start, end, why)]."""
    stale = []
    dead = [(s, e) for s, e, cls, *_ in known if cls == "dead"]
    in_game = lambda a: any(lo <= a < hi for lo, hi in game)  # noqa: E731
    for s, e, cls, ev, _, n in known:
        why = []
        if not (s < e and in_game(s) and in_game(e - 1)):
            why.append("not a run inside the game ranges")
        cat = sorted({explained[x] for x in range(s, e) if x in explained})
        if cat:
            why.append("the catalog now explains bytes of it (" + ", ".join(cat) + ")")
        listed = [a for a in ev.split(":", 1)[1].split(",") if a not in ("", "-")]
        if cls == "data":
            for a in listed:
                a = int(a, 16)
                ok = a % 4 == 0 and base <= a and a + 4 <= base + len(img)
                if not ok or not s <= fold(struct.unpack_from("<I", img, a - base)[0]) < e:
                    why.append(f"the word at {a:08x} does not point into it")
            if not listed:
                why.append("data needs ptr: evidence")
        else:
            found = sorted({src for t in range(s, e) for src in refs.get(t, []) if not s <= src < e})
            if found != sorted(int(a, 16) for a in listed):
                why.append("references now " + (",".join(f"{a:08x}" for a in found) or "-"))
            for a in found:
                if not any(ds <= a < de for ds, de in dead):
                    why.append(f"referenced from {a:08x}, outside every dead run")
        if why:
            stale.append((n, s, e, "; ".join(why)))
    return stale


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--out")
    ap.add_argument("--min", type=int, default=8, help="smallest run to list, in bytes")
    ap.add_argument("--state", help="the state folder to read (default: the project's)")
    ap.add_argument("--known", help="the explained runs (default: STATE/known_runs.tsv)")
    ap.add_argument("--check", action="store_true", help="exit 1 on an unexplained byte or a stale entry")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import paths
    cfg = paths.config()
    if paths.PROJECT["target"] != "dreamcast_sh4":
        sys.exit("completeness.py decodes SH-4 only")
    state = pathlib.Path(a.state).resolve() if a.state else paths.STATE
    if not (state / "function_bodies.txt").exists():
        sys.exit("no state/function_bodies.txt: re-run  decomp.py catalog  (server stopped) to write it")
    img = pathlib.Path(cfg["binary_path"]).read_bytes()
    base, game = int(cfg["load_address"], 16), paths.game_ranges(cfg)
    insn = set()
    for lo, hi in ranges(state / "code_ranges.txt", 0):
        insn.update(range(lo, hi))
    known_path = pathlib.Path(a.known).resolve() if a.known else state / "known_runs.tsv"
    known = read_known(known_path)
    totals, runs, explained = classify(img, base, game, ranges(state / "function_bodies.txt", 1), insn,
                                       table_bytes(state / "switch_tables.tsv"), known)
    refs = references(img, base, insn, [(s, e) for s, e, cls, *_ in known if cls == "dead"]) if known else {}
    stale = check_known(img, base, game, known, explained, refs)
    size = sum(totals.values())
    sus = sorted((r for r in runs if r[2] >= 3 and r[1] - r[0] >= a.min), key=lambda r: (-r[2], r[0]))
    unexplained = totals["orphan"] + totals["unknown"]
    out = pathlib.Path(a.out) if a.out else ws / "COMPLETENESS.md"
    extras = paths.scope().extras
    extra_line = ""
    if extras:  # their own line: outside the game ranges, so never in the classes above
        n, code, pool = extras_bytes(img, base, state / "function_bodies.txt", extras)
        extra_line = (f"extra functions (project.json extra_functions, outside the game ranges): {n} of "
                      f"{len(extras)} listed in the catalog, code {code:,} bytes, literal pools {pool:,} bytes")
    lines = [f"# Game-range completeness: {cfg.get('name')}", "", "| Class | Bytes | Share |", "|---|---|---|"]
    lines += [f"| {k} | {v:,} | {100 * v / size:.1f}% |" for k, v in totals.items()]
    lines += ["", extra_line + "."] if extra_line else []
    lines += ["", f"Explained runs: {len(known)} from {known_path if known else '(none)'}; stale: {len(stale)}. "
              f"Unexplained (orphan + unknown): {unexplained:,} bytes in {len(runs)} runs.", ""]
    if stale:
        lines += ["| Line | Start | End | Stale because |", "|---|---|---|---|"]
        lines += [f"| {n} | {s:08x} | {e:08x} | {w} |" for n, s, e, w in stale] + [""]
    lines += [f"Runs outside functions: {len(runs)}; {len(sus)} listed (>= {a.min} bytes): orphan instructions "
              f"(score {ORPHAN}) or unknown bytes that look like code (score >= 3).", "",
              "| Start | End | Bytes | Score |", "|---|---|---|---|"]
    lines += [f"| {s:08x} | {e:08x} | {e - s} | {sc} |" for s, e, sc in sus]
    lines += ["", "All unexplained runs:", "", "| Start | End | Bytes | Score |", "|---|---|---|---|"]
    lines += [f"| {s:08x} | {e:08x} | {e - s} | {sc} |" for s, e, sc in runs]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(" ".join(f"{k} {v:,}" for k, v in totals.items()),
          f"| unexplained {unexplained:,} in {len(runs)} runs | stale {len(stale)} -> {out}")
    if extra_line:
        print(extra_line)
    for n, s, e, w in stale:
        print(f"  stale line {n}: {s:08x}..{e:08x}: {w}")
    if a.check:
        from core import doctor
        ok, dlines, _ = doctor.check(ws)
        print("\n".join(dlines))
        if unexplained or stale or not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()
