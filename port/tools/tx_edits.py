"""game.json tx_edits: a game's own text edits to copies of its translated C, for a port feature that has to change
what a translated function computes (Power Stone 2's 8 fighters: a stack array of 4, a shuffle that only works for 4,
a 4-bit mask) where no readable C is in the play path.

"tx_edits": "file.json" (beside game.json): {"prelude": [C lines put at the top of each edited copy: macros, helper
prototypes], "edits": [{"func": addr, "insn": addr, "find": text, "replace": text, "why": text}]}. An edit changes the
code that follows the instruction comment naming `insn` (up to the next comment); `find` must occur there exactly once
in the whole function, or the build stops: a stale edit would silently leave the old code. Comments keep their text.
An empty `insn` means anywhere in the function's code (a translated file without instruction comments).
The judged files are never edited: like move_tables.apply, each wrapper whose judged C changes includes a rewritten
copy in the build folder (tx_edit/ADDR.c). It runs before move_tables, so an edit is written with the original
addresses and its literals move with the rest. The game keeps the old behaviour by writing each replacement so that
it computes what the original did while its feature is off.

Nearest existing: move_tables.py counts - replaces one fixed literal (a table's entry count) at a listed instruction
with one fixed expression; this replaces any text at a listed instruction with the game's own.
"""
import json
import re

import move_tables
import tx_sqfast
from overlay_slots import COMMENT
from move_tables import INSN
from tx_sqfast import INCLUDE

ADDR = re.compile(r"[0-9a-f]{8}")


def configs(raw, game_dir, err):
    """game_config.load's keys for the passes below and the room they move data to, each checked by its owner."""
    return {"extra_ram": move_tables.extra_ram(raw, err), "move_tables": move_tables.config(raw.get("move_tables"), err),
            "tx_edits": config(raw.get("tx_edits"), game_dir, err)}


def config(raw, game_dir, err):
    """The checked edits from game.json's tx_edits (None when absent): {"prelude": text, "edits": {func: [edit]}}."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.endswith(".json"):
        raise err("game.json tx_edits: the name of a .json file beside game.json")
    path = game_dir / raw
    if not path.is_file():
        raise err(f"game.json tx_edits: {path} not found")
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict) or set(spec) - {"prelude", "edits"} or not isinstance(spec.get("edits"), list):
        raise err(f'{path}: {{"prelude"?: [lines], "edits": [...]}}')
    prelude = spec.get("prelude", [])
    if not isinstance(prelude, list) or any(not isinstance(x, str) for x in prelude):
        raise err(f"{path}: prelude is a list of C lines")
    by = {}
    for i, e in enumerate(spec["edits"]):
        if not isinstance(e, dict) or set(e) - {"func", "insn", "find", "replace", "why"} or \
                any(not isinstance(e.get(k), str) for k in ("func", "insn", "find", "replace")) or \
                not ADDR.fullmatch(e["func"]) or not (ADDR.fullmatch(e["insn"]) or e["insn"] == "") or not e["find"]:
            raise err(f'{path} edits[{i}]: {{"func": addr, "insn": addr, "find": text, "replace": text, "why"?}}')
        if "/*" in e["find"] or "/*" in e["replace"] or "//" in e["replace"]:
            raise err(f"{path} edits[{i}]: no comments in find or replace (edits apply between instruction comments)")
        by.setdefault(e["func"], []).append(e)
    return {"prelude": "".join(x + chr(10) for x in prelude), "edits": by}


def rewrite_text(text, edits, func):
    """text with the function's edits applied; SystemExit names each edit whose find is not there exactly once."""
    parts = COMMENT.split(text)
    insn, where = (), []
    for i, part in enumerate(parts):
        if i % 2:
            insn = tuple(INSN.findall(part)) or insn
        else:
            where.append((i, insn))
    bad = []
    for e in edits:
        hits = [(i, parts[i].count(e["find"])) for i, a in where if (not e["insn"] or e["insn"] in a) and e["find"] in parts[i]]
        if len(hits) != 1 or hits[0][1] != 1:
            bad.append(f"{func}@{e['insn']} {e['find'][:60]!r}: found {sum(n for _, n in hits)} times")
            continue
        parts[hits[0][0]] = parts[hits[0][0]].replace(e["find"], e["replace"])
    if bad:
        raise SystemExit("tx_edits: " + "; ".join(bad))
    return "".join(parts)


def apply(tx_texts, out, write_if_changed, spec):
    """Points each edited function's wrapper in {addr: text} at its rewritten copy in out/tx_edit. Returns
    (functions, edits); stops when an edited function is not translated."""
    missing = sorted(set(spec["edits"]) - set(tx_texts))
    if missing:
        raise SystemExit("tx_edits: not in the translated build: " + ", ".join(missing))
    n = 0
    for func, edits in sorted(spec["edits"].items()):
        wrapper = tx_texts[func]
        m = INCLUDE.search(wrapper)
        if not m:
            raise SystemExit(f"tx_edits: the wrapper of {func} includes no judged C")
        with open(m.group(1), encoding="utf-8") as f:
            text = f.read()
        copy = out / "tx_edit" / f"{func}.c"
        copy.parent.mkdir(parents=True, exist_ok=True)
        write_if_changed(copy, spec["prelude"] + rewrite_text(text, edits, func))
        tx_texts[func] = wrapper[:m.start(1)] + copy.as_posix() + wrapper[m.end(1):]
        n += len(edits)
    return len(spec["edits"]), n


def passes(cfg, tx_texts, out, write_if_changed, sq_fast=False):
    """gen_port's rewrites of the translated C's wrappers {addr: text}, in their order, each printing its counts."""
    if sq_fast or cfg.get("sq_fast"):
        print(f"sq_fast: {tx_sqfast.apply(tx_texts, out, write_if_changed)} translated functions with store queues")
    if cfg.get("tx_edits"):  # before move_tables: an edit is written with the original addresses
        print("tx_edits: %d translated functions, %d edits" % apply(tx_texts, out, write_if_changed, cfg["tx_edits"]))
    if cfg.get("move_tables"):  # after sq_fast: a wrapper may already include its rewritten copy
        n, ends = move_tables.apply(tx_texts, out, write_if_changed, cfg["move_tables"])
        print(f"move_tables: {n} translated functions with moved addresses, {ends} table ends")


def global_address(a, moves):
    """A named global's address as game.h places it: move_tables' when game.json moves tables (moves), else a."""
    return move_tables.global_address(a, moves) if moves else a
