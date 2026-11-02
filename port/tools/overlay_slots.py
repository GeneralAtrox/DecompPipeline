"""Slot units for gen_overlays.py (runtime/overlay.h): code the game loads at any align boundary of a region (one file,
several places, as a fighter's per-character code), written once at a canonical base. What differs from a fixed unit:
its extent is the region, its rivals are compared at the same offset from their bases, and its translator C takes the
addresses it folded in off its own position (rebase_text; tc_call sets tx_ovl_delta)."""
import json
import re

LITERAL = re.compile(r"(?<![\w.])0x([0-9a-f]{8})u" + chr(92) + "b")
COMMENT = re.compile("(/" + chr(92) + "*.*?" + chr(92) + "*/|//[^" + chr(92) + "n]*)", re.S)
SWITCH = re.compile(r"switch " + chr(92) + r"((" + chr(92) + "w+)" + chr(92) + r")(" + chr(92) + "s*" + chr(92) + "{(?:" + chr(92) + "s|/" + chr(92) + "*.*?" + chr(92) + "*/)*)(case )" + LITERAL.pattern, re.S)


def parse(uid, slot, error=ValueError):
    """The unit's slot {canon, lo, hi, align} from overlays.json's {"base", "region": [lo, hi], "align"}, or None."""
    if not slot:
        return None
    s = {"canon": int(slot["base"], 16), "lo": int(slot["region"][0], 16), "hi": int(slot["region"][1], 16),
         "align": int(slot["align"], 16)}
    if s["align"] & (s["align"] - 1) or s["lo"] % s["align"] or s["hi"] % s["align"]:
        raise error(f"overlay {uid}: slot align must be a power of two dividing the region")
    if s["canon"] % s["align"]:
        raise error(f"overlay {uid}: slot base must be an align boundary")
    return s


def canon_ranges(units):
    """[lo, hi) of each slot unit's canonical block, for the translator C worker (rebase_text)."""
    return [(u["slot"]["canon"], u["slot"]["canon"] + u["slot"]["align"]) for u in units if u.get("slot")]


def dump(ranges):
    """canon_ranges as the worker's --rebase argument."""
    return json.dumps([[f"{lo:08x}", f"{hi:08x}"] for lo, hi in ranges])


def load(arg):
    """... and back."""
    return [(int(lo, 16), int(hi, 16)) for lo, hi in json.loads(arg or "[]")]


def table_fields(u):
    """The PortOverlay initialiser's slot fields (overlay.h) for a unit: empty for a fixed one."""
    s = u.get("slot")
    return f", 0x{s['canon']:08x}u, 0x{s['lo']:08x}u, 0x{s['hi']:08x}u, 0x{s['align']:x}u" if s else ""


def extent(u):
    """Where a unit may be in RAM: its ranges, or (a slot unit) its slot region."""
    s = u.get("slot")
    return [(s["lo"], s["hi"])] if s else u["ranges"]


def rivals_of(units, grp, reads, i):
    """[(id, ranges, read)] of the units that can be loaded over unit i, in unit i's addresses. A slot unit's rivals
    are the others of its slot region, compared at the same offset from their bases (a slot unit is never loaded
    over a fixed one here)."""
    u, out = units[i], []
    for j, v in enumerate(units):
        if j == i or grp[j] != grp[i] or bool(u.get("slot")) != bool(v.get("slot")):
            continue
        s = v["slot"]["canon"] - u["slot"]["canon"] if u.get("slot") else 0  # a in unit i is a + s in unit j
        out.append((v["id"], [(lo - s, hi - s) for lo, hi in v["ranges"]],
                    lambda a, n, rd=reads[j], s=s: rd(a + s, n)))
    return out


def rebase_text(text, ranges):
    """The judged text with every address literal inside ranges ([lo, hi) of a slot unit's canonical base; the 0x8c
    and 0x0c forms alike) written (0xADDRu + tx_ovl_delta): the unit runs at its base's delta from where it was
    translated, and takes the addresses it folded in (pc-relative data, its own functions) with it. Comments and case
    labels keep theirs, so a switch on such a label takes the delta off what it compares (a jump table's target)."""
    def inside(lit):
        return any(lo <= int(lit, 16) & 0x0fffffff < hi for lo, hi in ranges)

    def one(m):
        return f"({m.group(0)} + tx_ovl_delta)" if inside(m.group(1)) else m.group(0)

    def scrutinee(m):
        return f"switch ({m.group(1)} - tx_ovl_delta){m.group(2)}{m.group(3)}0x{m.group(4)}u" if inside(m.group(4)) else m.group(0)
    text = SWITCH.sub(scrutinee, text)
    return "".join(p if i % 2 else re.sub(r"(?<!case )" + LITERAL.pattern, one, p)
                   for i, p in enumerate(COMMENT.split(text)))


def rebased(texts, ok, ranges, folder):
    """tx_ADDR.c texts (tx_port.inbound) with each function inside ranges compiled from a rebased copy of its judged
    file in folder (the #include line names it)."""
    rb = [(lo & 0x0fffffff, hi & 0x0fffffff) for lo, hi in ranges]
    for a, t in list(texts.items()):
        if not any(lo <= int(a, 16) & 0x0fffffff < hi for lo, hi in rb):
            continue
        folder.mkdir(parents=True, exist_ok=True)
        copy, new = folder / f"{a}.c", rebase_text(ok[a].read_text(encoding="utf-8"), rb)
        if not copy.exists() or copy.read_text(encoding="utf-8") != new:
            copy.write_text(new, encoding="utf-8", newline=chr(10))
        texts[a] = t.replace(f'#include "{ok[a].as_posix()}"', f'#include "{copy.as_posix()}"')
    return texts
