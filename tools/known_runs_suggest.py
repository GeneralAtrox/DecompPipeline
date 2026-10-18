"""Suggest known_runs.tsv "dead" rows for unexplained game-range runs that are unreachable code (tools/completeness.py).

usage: python tools/known_runs_suggest.py --project DIR [--state DIR] [--append]
A run is suggested only when all of these hold (SH-4, the same decoding completeness.py does):
  - it starts right after a block end: the instruction 4 bytes before it is rts, rte, jmp @rN, braf, bra or a
    `bt/bf`-free tail, so the 2 bytes before it are that instruction's delay slot and execution never falls in;
  - nothing references it from outside: no image word (folded P0/P1/P2) names a byte of it and no bra/bsr/bt/bf or
    PC-relative load at a decoded instruction aims into it (completeness.references), except from another dead run;
  - past any leading nop/zero padding, it ends with a block end and its delay slot (then only padding), so it is
    a whole unreachable block: an epilogue copy after a tail jump (SHC cross-jumping), a branch-only stub, or a
    second epilogue.
Runs some word or branch names (possible missing functions, switch cases or data) are listed apart, never suggested.
Each row is `start end dead refs:<sources in other dead runs, or -> auto: <shape>`, which completeness.py --check
verifies like a hand-written one.
Prints the rows; --append adds them to STATE/known_runs.tsv (never rewriting an existing line). Read-only otherwise.
"""
import argparse
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
import completeness as cp  # noqa: E402
from common import use_project  # noqa: E402

PAD = cp.PAD_WORDS


def block_end(op):
    """The block-ending instruction an opcode is, else None (each has a delay slot)."""
    if op == 0x000B:
        return "rts"
    if op == 0x002B:
        return "rte"
    if op & 0xF0FF == 0x402B:
        return "jmp"
    if op & 0xF0FF == 0x0023:
        return "braf"
    if op >> 12 == 0xA:
        return "bra"
    return None


def words(img, base, lo, hi):
    return [struct.unpack_from("<H", img, a - base)[0] for a in range(lo, hi - 1, 2)]


def shape(img, base, s, e):
    """A short description when [s, e) is a whole unreachable block after a block end, else None."""
    if s % 2 or e % 2 or s - 4 < base or e > base + len(img):
        return None
    before = block_end(struct.unpack_from("<H", img, s - 4 - base)[0])
    if before is None:
        return None
    w = words(img, base, s, e)
    lead = 0
    while lead < len(w) and w[lead] in PAD:
        lead += 1
    # one or more blocks, each ending in a block end and its delay slot, optional padding between and after
    ends, i = [], lead
    while i < len(w):
        j = i
        while j < len(w) and block_end(w[j]) is None:
            j += 1
        if j + 1 >= len(w):
            return None  # falls out of the run, or a block end without its delay slot
        ends.append(block_end(w[j]))
        i = j + 2
        while i < len(w) and w[i] in PAD:
            i += 1
    if not ends:
        return None
    pad = f"{lead * 2} padding bytes, then " if lead else ""
    return f"auto: unreferenced code after a `{before}` (its delay slot ends the block before): {pad}" \
           f"{len(ends)} block(s) ending in {'/'.join(f'`{x}`' for x in ends)}; nothing outside dead code branches to it or names it"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--state", help="the state folder to read (default: the project's)")
    ap.add_argument("--append", action="store_true", help="append the suggested rows to STATE/known_runs.tsv")
    a = ap.parse_args()
    use_project(a.project)
    from core import paths
    cfg = paths.config()
    if paths.PROJECT["target"] != "dreamcast_sh4":
        sys.exit("known_runs_suggest.py decodes SH-4 only")
    state = pathlib.Path(a.state).resolve() if a.state else paths.STATE
    if not (state / "function_bodies.txt").exists():
        sys.exit("no state/function_bodies.txt: re-run  decomp.py catalog  (server stopped) to write it")
    img = pathlib.Path(cfg["binary_path"]).read_bytes()
    base, game = int(cfg["load_address"], 16), paths.game_ranges(cfg)
    insn = set()
    for lo, hi in cp.ranges(state / "code_ranges.txt", 0):
        insn.update(range(lo, hi))
    known_path = state / "known_runs.tsv"
    known = cp.read_known(known_path)
    _, runs, _ = cp.classify(img, base, game, cp.ranges(state / "function_bodies.txt", 1), insn,
                             cp.table_bytes(state / "switch_tables.tsv"), known)
    refs = cp.references(img, base, insn, [])
    cands, named, other = [], [], 0
    for s, e, _ in runs:
        hits = {(t, src) for t in range(s, e) for src in refs.get(t, []) if not s <= src < e}
        if hits:
            named.append((s, e, sorted({t for t, _ in hits}), sorted({src for _, src in hits})))
            continue
        why = shape(img, base, s, e)
        if why:
            cands.append((s, e, why))
        else:
            other += 1
    # a dead run's own branches count as references (completeness.references): one dead block may branch into
    # another. Keep a candidate only while every such reference lies in a dead run, and list them as its evidence.
    old_dead = [(s, e) for s, e, cls, *_ in known if cls == "dead"]
    while True:
        dead = old_dead + [(s, e) for s, e, _ in cands]
        refs2 = cp.references(img, base, insn, dead)
        rows, keep = [], []
        for s, e, why in cands:
            found = sorted({src for t in range(s, e) for src in refs2.get(t, []) if not s <= src < e})
            if all(any(ds <= x < de for ds, de in dead) for x in found):
                keep.append((s, e, why))
                rows.append(f"{s:08x}\t{e:08x}\tdead\trefs:{','.join(f'{x:08x}' for x in found) or '-'}\t{why}")
        if len(keep) == len(cands):
            break
        cands = keep
    for r in rows:
        print(r)
    print(f"# {len(rows)} suggested of {len(runs)} unexplained runs; {len(named)} named from outside (not suggested); "
          f"{other} unreferenced but not a whole block after a block end", file=sys.stderr)
    for s, e, targets, found in named:  # candidates for data_funcs.py --add (a missing function) or a data row
        print(f"#   named {s:08x}..{e:08x}: {','.join(f'{x:08x}' for x in targets[:6])} from "
              f"{','.join(f'{x:08x}' for x in found[:6])}", file=sys.stderr)
    if a.append and rows:
        old = known_path.read_text(encoding="utf-8") if known_path.exists() else \
            "# start\tend\tclass\tevidence\treason   (tools/completeness.py; auto rows from tools/known_runs_suggest.py)\n"
        if old and not old.endswith("\n"):
            old += "\n"
        known_path.write_text(old + "\n".join(rows) + "\n", encoding="utf-8")
        print(f"appended {len(rows)} row(s) to {known_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
