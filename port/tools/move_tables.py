"""game.json move_tables: whole guest data tables moved to new addresses at build time, for a port that needs a table to
grow where the next object sits right after it (Power Stone 2's g_fighters is followed by g_stageObj, so fighters 5..8
need the table somewhere else, such as the extra RAM, game.json extra_ram).

Translated C holds guest addresses as literals, in the P0 (0x0c..), P1 (0x8c..) and P2 (0xac..) forms. For each table
{"from": addr, "bytes": n, "to": addr, "ends": [function addrs]}:
  - every literal inside [from, from + bytes) becomes to + its offset, in the same form (a 0x0c.. literal goes to 0x08..);
  - the end value from + bytes is also the next object's address, so it moves only where "ends" lists it (a loop that
    walks the table and stops there; the translator often folds base + 4 * stride into it): "FUNC" moves it throughout
    that function, "FUNC@INSN" only in the code after the instruction comment INSN (up to the next one), for a
    function that also uses the same literal as the next object's base (Power Stone 2's CharPick_Update loads
    g_flow's address both as the player slots' loop end and as g_flow itself);
  - comments keep their text.
A table of equal entries can grow at run time: "stride" (hex bytes an entry, dividing "bytes"), "max" (entries the room
at "to" holds) and "counts", a list of "FUNC@INSN" where the original entry count (bytes / stride) is compiled into the
code (a loop bound such as `mov #4,r11`, and each compare the translator folded it into, `(s32)r3 >= (s32)0x4u`; or the
table's size in an end pointer, base + 0xe4e0; INSN may be a branch's delay slot): in that instruction's code the count becomes the run-time count g_move_count[k] (k: the table's place in the list; 0 means
the original count, guest_mem.c), and the table's listed ends become to + stride * that count. With the count left at 0
the code computes what it did; a game raises it (Power Stone 2: 8 fighters) once its sites are all listed.
The judged files are never edited (their check would drop them from the build): like tx_sqfast.apply, each wrapper whose
judged C changes includes a rewritten copy in the build folder (tx_move/ADDR.c). gen_port also moves the names of
globals inside a table in game.h, so readable C follows. Every listed end must be found, or the build stops: a stale
list would leave a loop running to the old address.

Nearest existing: overlay_slots.py rebase_text - rebases a code overlay's own literals by a run-time delta and
leaves end values alone; this moves data tables to fixed new addresses and rewrites the named end compares.
"""
import re

from overlay_slots import COMMENT, LITERAL
from tx_sqfast import INCLUDE  # the wrapper's include of its judged C (or of an earlier pass's copy)
ADDR = re.compile(r"[0-9a-f]{8}")
END = re.compile(r"([0-9a-f]{8})(?:@([0-9a-f]{8}))?")
# each address in the translator's instruction comment: /* ADDR  mov.l ... */, or a branch with its delay slot,
# /* ADDR  bt/s 0xTARGET; ADDR  mov #4,r10 */
INSN = re.compile(r"(?:^/" + chr(92) + r"*|;)\s*([0-9a-f]{8})\s")
PHYS = 0x1fffffff  # the address without its P0/P1/P2 bits
COUNT_SLOTS = 32  # g_move_count[] in guest_mem.c
COUNT_DECL = "extern unsigned int g_move_count[32]; /* move_tables counts (guest_mem.c) */"


def count_expr(t):
    """C for table t's run-time entry count: g_move_count[slot], or the original count while that is 0."""
    k = t["slot"]
    return f"(g_move_count[{k}] ? g_move_count[{k}] : {t['count']}u)"


def _addr(v, what, err):
    if not isinstance(v, str) or not ADDR.fullmatch(v):
        raise err(f"game.json move_tables {what}: {v!r} (8 lowercase hex digits)")
    return int(v, 16)


def extra_ram(raw, err):
    """game.json extra_ram, checked: the room moved tables usually go to."""
    v = raw.get("extra_ram", False)
    if not isinstance(v, bool):
        raise err(f"game.json extra_ram: {v!r} (true: a second 16 MB at 0x88000000, also 0x08000000 and "
                  "0xa8000000, for data the port moves out of the main RAM; runtime/guest_mem.c)")
    return v


def config(raw, err):
    """The checked list from game.json's move_tables (None when absent)."""
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise err('game.json move_tables: a list of {"from": addr, "bytes": n, "to": addr, "ends": [function addr]}')
    out = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict) or set(t) - {"name", "from", "bytes", "to", "ends", "stride", "max", "counts"} or \
                not {"from", "bytes", "to"} <= set(t):
            raise err(f'game.json move_tables[{i}]: {{"name"?, "from", "bytes", "to", "ends"?, "stride"?, "max"?, "counts"?}}')
        n = t["bytes"]
        if isinstance(n, str):
            n = int(n, 16) if re.fullmatch(r"[0-9a-f]+", n) else -1
        if not isinstance(n, int) or n <= 0:
            raise err(f"game.json move_tables[{i}].bytes: {t['bytes']!r} (a positive count, or hex digits)")
        ends = t.get("ends", [])
        if not isinstance(ends, list):
            raise err(f"game.json move_tables[{i}].ends: a list of function addresses (FUNC or FUNC@INSN)")
        for e in ends:
            if not isinstance(e, str) or not END.fullmatch(e):
                raise err(f"game.json move_tables[{i}].ends: {e!r} (FUNC or FUNC@INSN, 8 lowercase hex digits each)")
        stride, room, counts = t.get("stride"), n, t.get("counts", [])
        if stride is not None:
            stride = int(stride, 16) if isinstance(stride, str) and re.fullmatch(r"[0-9a-f]+", stride) else 0
            if stride <= 0 or n % stride:
                raise err(f"game.json move_tables[{i}].stride: {t['stride']!r} (hex digits, dividing bytes)")
            mx = t.get("max", n // stride)
            if not isinstance(mx, int) or isinstance(mx, bool) or mx < n // stride:
                raise err(f"game.json move_tables[{i}].max: {mx!r} (at least bytes / stride entries)")
            room = stride * mx
        elif "max" in t or counts:
            raise err(f"game.json move_tables[{i}]: max and counts need a stride")
        if not isinstance(counts, list) or any(not isinstance(c, str) or not END.fullmatch(c) or "@" not in c for c in counts):
            raise err(f"game.json move_tables[{i}].counts: a list of FUNC@INSN (8 lowercase hex digits each)")
        out.append({"name": str(t.get("name", t["from"])), "from": _addr(t["from"], f"[{i}].from", err) & PHYS,
                    "bytes": n, "to": _addr(t["to"], f"[{i}].to", err) & PHYS,
                    "ends": sorted(set(ends)), "stride": stride or 0, "count": n // stride if stride else 0,
                    "room": room, "counts": sorted(set(counts)), "slot": i})
    if len(out) > COUNT_SLOTS:
        raise err(f"game.json move_tables: at most {COUNT_SLOTS} tables")
    for kind in ("from", "to"):
        spans = sorted((t[kind], t[kind] + (t["room"] if kind == "to" else t["bytes"]), t["name"]) for t in out)
        for (a0, a1, an), (b0, _, bn) in zip(spans, spans[1:]):
            if b0 < a1:
                raise err(f"game.json move_tables: {an} and {bn} overlap where they {'are' if kind == 'from' else 'go'}")
    return out


def moved(value, moves, func=None, insn=None):
    """(the address value moves to, in the same P0/P1/P2 form, or None when it stays; (the table whose end it is, the
    "ends" entry that moved it), or None). A listed end comes first: one table's end can be the next moved table's start
    (g_cameras, g_playerSlots). insn: the instruction comment the literal follows."""
    p, form = value & PHYS, value & ~PHYS & 0xffffffff
    for t in moves:
        if p == t["from"] + t["bytes"]:
            for key in (func,) + tuple(f"{func}@{a}" for a in (insn if isinstance(insn, tuple) else (insn,))):
                if key in t["ends"]:
                    if t["stride"]:  # a counted table: its end follows the run-time count
                        return f"(0x{form | t['to']:08x}u + 0x{t['stride']:x}u * {count_expr(t)})", (t["name"], key)
                    return form | (t["to"] + t["bytes"]), (t["name"], key)
    for t in moves:
        if t["from"] <= p < t["from"] + t["bytes"]:
            return form | (t["to"] + p - t["from"]), None
    return None, None


def rewrite_text(text, moves, func):
    """(text with the literals moved, {(table name, "ends" entry): ends rewritten in func})."""
    ends, insn, out = {}, None, []

    def one(m):
        n, end = moved(int(m.group(1), 16), moves, func, insn)
        if n is None:
            return m.group(0)
        if end:
            ends[end] = ends.get(end, 0) + 1
        return n if isinstance(n, str) else f"0x{n:08x}u"
    for i, part in enumerate(COMMENT.split(text)):
        if i % 2:
            insn = tuple(INSN.findall(part)) or insn
            out.append(part)
        else:
            for key in (f"{func}@{a}" for a in insn or ()):
                for t in moves:
                    if key in t["counts"]:  # the count, and the table's size folded into an end pointer (base + 0xe4e0)
                        part, k = re.subn(r"(?<![\w.])0x%xu\b" % t["count"], count_expr(t), part)
                        part, k2 = re.subn(r"(?<![\w.])0x%xu\b" % t["bytes"], f"(0x{t['stride']:x}u * {count_expr(t)})", part)
                        if k + k2:
                            ends[(t["name"], key)] = ends.get((t["name"], key), 0) + k + k2
            out.append(LITERAL.sub(one, part))
    return "".join(out), ends


def apply(tx_texts, out, write_if_changed, moves):
    """Points each wrapper in {addr: text} whose included judged C (or the copy an earlier pass made, such as
    tx_sqfast's) holds a moved address at a rewritten copy in out/tx_move. Returns (functions changed, ends found);
    stops when a listed end function is not translated or has no end literal."""
    done, found = 0, set()
    for addr, wrapper in tx_texts.items():
        m = INCLUDE.search(wrapper)
        if not m:
            continue
        with open(m.group(1), encoding="utf-8") as f:
            text = f.read()
        new, ends = rewrite_text(text, moves, addr)
        found |= set(ends)
        if new == text:
            continue
        if "g_move_count[" in new:
            new = COUNT_DECL + chr(10) + new
        copy = out / "tx_move" / f"{addr}.c"
        copy.parent.mkdir(parents=True, exist_ok=True)
        write_if_changed(copy, new)
        tx_texts[addr] = wrapper[:m.start(1)] + copy.as_posix() + wrapper[m.end(1):]
        done += 1
    missing = [f"{t['name']} end in {e}" for t in moves for e in t["ends"] + t["counts"] if (t["name"], e) not in found
               or e.split("@")[0] not in tx_texts]
    if missing:
        raise SystemExit("move_tables: listed ends or counts not found in translated C: " + ", ".join(missing))
    return done, len(found)


def global_address(a, moves):
    """A named global's address (8 hex digits) as game.h should place it."""
    n, _ = moved(int(a, 16), moves)
    return a if n is None else f"{n:08x}"
