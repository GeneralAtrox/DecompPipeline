"""Link a GameCube game's REL modules onto its main.dol at fixed addresses, into one DOL that every part of DP reads
as it reads main.dol (Ghidra's GameCube loader, the C runner, the judge, ppc-translate), and record where each
module went (link.json), so every function keeps an identity that does not depend on the layout.

    python gcrel.py link  <main.dol> <out_dir> <module.rel> [...] [--inputs TcInputs.java]
        out_dir (new): linked.dol, link.json, seeds.txt (REL function entries for Ghidra's analysis)
    python gcrel.py where <link.json> <addr> [...]           an address's identity: module, section, offset
    python gcrel.py addr  <link.json> <module> <section> <offset>   the address of module/section/offset
    python gcrel.py ids   <link.json> <state/function_bodies.txt> [out.tsv]   every catalogued function's identity

What the game's own loader (OSLink) does at run time, done once here: each module's sections get an address, then
its relocations are applied. A REL lists, per module it refers to (its imports: module 0 is main.dol, and a module
usually refers to itself), a run of 8-byte relocations: the distance from the previous one, the type, the target
section and the addend. R_DOLPHIN_SECTION starts patching another of this module's own sections from its start,
R_DOLPHIN_NOP only moves on, R_DOLPHIN_END ends the run. The target is the addend itself for module 0 (an absolute
main.dol address), else that module's section address plus the addend. The types are PowerPC's (the ELF ABI):
ADDR32, ADDR24, ADDR16, ADDR16_LO/HI/HA, ADDR14 (and its branch-hint forms), REL24, REL14 (and its forms).

Placement (fixed, from the inputs alone): modules in module-id order; first every module's code sections, then its
data sections, then its bss, each aligned to the module's alignment (version 2+, else 32), from the end of main.dol
(its sections and bss) upwards, never inside the windows the oracle and the runner use: the scratch area and the
generator heap (TcInputs.java's SCR .. GEN_HEAP_HI) and the stack (target.json). Code goes into the DOL's free text
slots, data into its free data slots (a run is cut where a window is skipped); bss needs no bytes (MEM1 outside the
sections reads as zero, in the runner and in Ghidra). A module that refers to a module not linked keeps those
words as the file has them, except that a call (REL24) goes to the module's own "unresolved" function, as the
game's OSUnlink sets it.

A function's identity is (module id, section, offset): `where` gives it for an address of the linked image; main.dol
addresses are their own identity (module 0).
"""
import hashlib
import json
import pathlib
import re
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gcdisc  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[1]
R_NONE, R_ADDR32, R_ADDR24, R_ADDR16, R_ADDR16_LO, R_ADDR16_HI, R_ADDR16_HA = range(7)
R_ADDR14, R_ADDR14_BRTAKEN, R_ADDR14_BRNTAKEN, R_REL24, R_REL14, R_REL14_BRTAKEN, R_REL14_BRNTAKEN = range(7, 14)
R_DOLPHIN_NOP, R_DOLPHIN_SECTION, R_DOLPHIN_END = 201, 202, 203
RAM = (0x80000000, 0x81800000)


class LinkError(ValueError):
    pass


def align_up(v, a):
    return -(-v // a) * a


class Rel:
    """A REL module's header, section table and import table."""

    def __init__(self, data, name):
        if gcdisc.rel_info(data) is None:
            raise LinkError(f"{name}: not a REL module")
        self.data, self.name = data, name
        (self.id, _, _, nsec, sec_off, _, _, self.version, self.bss_size, _, imp_off, imp_size) = \
            struct.unpack(">12I", data[:0x30])
        self.sections = []  # (index, file offset, size, exec, bss)
        for k in range(nsec):
            off, size = struct.unpack(">2I", data[sec_off + 8 * k:sec_off + 8 * k + 8])
            if size:
                self.sections.append((k, off & ~1, size, bool(off & 1), off == 0))
        self.imports = [struct.unpack(">2I", data[imp_off + 8 * k:imp_off + 8 * k + 8]) for k in range(imp_size // 8)]
        self.entries = {"prolog": (data[0x30], struct.unpack(">I", data[0x34:0x38])[0]),
                        "epilog": (data[0x31], struct.unpack(">I", data[0x38:0x3C])[0]),
                        "unresolved": (data[0x32], struct.unpack(">I", data[0x3C:0x40])[0])}
        self.align, self.bss_align = (struct.unpack(">2I", data[0x40:0x48]) if self.version >= 2 else (0, 0))
        self.align, self.bss_align = self.align or 32, self.bss_align or 32

    def relocations(self, offset):
        """[(type, section, addend)...] runs from a file offset: (pos delta, type, section, addend) to the END."""
        out, at = [], offset
        while True:
            if at + 8 > len(self.data):
                raise LinkError(f"{self.name}: relocations run past the end of the file")
            delta, typ, sec, addend = struct.unpack(">HBBI", self.data[at:at + 8])
            out.append((delta, typ, sec, addend))
            at += 8
            if typ == R_DOLPHIN_END:
                return out


def reserved_windows(inputs=None, target=None):
    """The [lo, hi) windows no module may cover: TcInputs.java's scratch area and generator heap, target.json's
    stack."""
    tj = json.loads((target or TOOL / "target.json").read_text(encoding="utf-8"))
    src = (inputs or TOOL / "template" / "TcInputs.java").read_text(encoding="utf-8")
    consts = {m.group(1): int(m.group(2), 16) for m in
              re.finditer(r"static final long (\w+) = 0x([0-9a-fA-F]+)L?;", src)}
    scratch = [int(v, 16) for v in tj["memory"]["scratch"]]
    lo = min(scratch[0], consts.get("GEN_HEAP_LO", scratch[1]))
    hi = max(scratch[1], consts.get("GEN_HEAP_HI", scratch[1]))
    stack = [int(v, 16) for v in tj["memory"]["stack"]]
    return sorted([(lo, hi), (stack[0], stack[1])])


def main_end(dol):
    secs = gcdisc.dol_sections(dol)
    bss, size = secs["bss"]
    return max([a + n for _, _, a, n in secs["text"] + secs["data"]] + [bss + size])


class Cursor:
    """Hands out addresses upwards from a start, around the reserved windows, below the top of MEM1."""

    def __init__(self, start, windows, top=RAM[1]):
        self.at, self.windows, self.top, self.jumped = start, windows, top, False

    def take(self, size, align, what):
        a = align_up(self.at, align)
        for lo, hi in self.windows:
            if a < hi and a + size > lo:
                a = align_up(hi, align)
                self.jumped = True
        if a + size > min([lo for lo, _ in self.windows if lo >= a] + [self.top]):
            raise LinkError(f"{what}: {size} bytes do not fit below 0x{self.top:08x} (MEM1 is 24 MB; link fewer "
                            f"modules: --rels)")
        self.at = a + size
        return a


def place(dol, rels, windows):
    """{(module id, section index): address} and the code / data runs [(start, [(address, bytes)])]."""
    cur = Cursor(align_up(main_end(dol), 32), windows)
    addr, runs = {}, {"text": [], "data": []}
    for kind, want in (("text", lambda s: s[3]), ("data", lambda s: not s[3] and not s[4]), ("bss", lambda s: s[4])):
        cur.jumped = True  # each kind starts a run of its own
        for r in rels:
            for s in r.sections:
                if not want(s):
                    continue
                a = cur.take(s[2], r.bss_align if s[4] else r.align, f"{r.name} section {s[0]}")
                addr[(r.id, s[0])] = a
                if kind == "bss":
                    continue
                if cur.jumped:
                    runs[kind].append([])
                    cur.jumped = False
                runs[kind][-1].append((a, bytearray(r.data[s[1]:s[1] + s[2]])))
    return addr, runs


def patch(mem, p, typ, v, name):
    """Apply one relocation at address p: mem is [(address, bytearray)] of the placed sections."""
    for base, b in mem:
        if base <= p < base + len(b):
            o = p - base
            break
    else:
        raise LinkError(f"{name}: a relocation at 0x{p:08x} is outside the module's sections")
    w = struct.unpack(">I", b[o:o + 4])[0] if o + 4 <= len(b) else None
    if typ == R_ADDR32:
        struct.pack_into(">I", b, o, v & 0xFFFFFFFF)
    elif typ == R_ADDR24:
        struct.pack_into(">I", b, o, (w & 0xFC000003) | (v & 0x03FFFFFC))
    elif typ in (R_ADDR16, R_ADDR16_LO, R_ADDR16_HI, R_ADDR16_HA):
        half = {R_ADDR16: v, R_ADDR16_LO: v, R_ADDR16_HI: v >> 16, R_ADDR16_HA: (v + 0x8000) >> 16}[typ]
        struct.pack_into(">H", b, o, half & 0xFFFF)
    elif typ in (R_ADDR14, R_ADDR14_BRTAKEN, R_ADDR14_BRNTAKEN):
        struct.pack_into(">I", b, o, (w & 0xFFFF0003) | (v & 0xFFFC))
    elif typ == R_REL24:
        d = v - p
        if not -0x2000000 <= d < 0x2000000:
            raise LinkError(f"{name}: a REL24 branch at 0x{p:08x} cannot reach 0x{v:08x}")
        struct.pack_into(">I", b, o, (w & 0xFC000003) | (d & 0x03FFFFFC))
    elif typ in (R_REL14, R_REL14_BRTAKEN, R_REL14_BRNTAKEN):
        d = v - p
        if not -0x8000 <= d < 0x8000:
            raise LinkError(f"{name}: a REL14 branch at 0x{p:08x} cannot reach 0x{v:08x}")
        struct.pack_into(">I", b, o, (w & 0xFFFF0003) | (d & 0xFFFC))
    else:
        raise LinkError(f"{name}: relocation type {typ} at 0x{p:08x} is not one OSLink applies")
    return w


def relocate(rels, addr, runs):
    """Apply every module's relocations; per module {"applied", "against_missing": {id: n}, "redirected"} and the
    function entries the relocations show (call targets in the modules' code)."""
    mem = [x for kind in ("text", "data") for run in runs[kind] for x in run]
    ids = {r.id for r in rels}
    code = [(addr[(r.id, s[0])], s[2]) for r in rels for s in r.sections if s[3]]
    stats, seeds = {}, set()
    for r in rels:
        st = stats[r.id] = {"applied": 0, "against_missing": {}, "redirected_to_unresolved": 0}
        usec, uoff = r.entries["unresolved"]
        unresolved = addr.get((r.id, usec), None) if usec else None
        for target, off in r.imports:
            sec, pos = None, 0
            for delta, typ, tsec, addend in r.relocations(off):
                if typ == R_DOLPHIN_SECTION:
                    sec, pos = tsec, 0
                    continue
                pos += delta
                if typ in (R_DOLPHIN_NOP, R_NONE):
                    continue
                if typ == R_DOLPHIN_END:
                    break
                if sec is None or (r.id, sec) not in addr:
                    raise LinkError(f"{r.name}: a relocation before any section, or into section {sec}")
                p = addr[(r.id, sec)] + pos
                if target == 0:
                    v = addend
                elif target in ids:
                    if (target, tsec) not in addr:
                        raise LinkError(f"{r.name}: a relocation against module {target} section {tsec}, "
                                        f"which it does not have")
                    v = addr[(target, tsec)] + addend
                else:
                    st["against_missing"][str(target)] = st["against_missing"].get(str(target), 0) + 1
                    if typ == R_REL24 and unresolved is not None:
                        patch(mem, p, R_REL24, unresolved + uoff, r.name)
                        st["redirected_to_unresolved"] += 1
                    continue
                w = patch(mem, p, typ, v, r.name)
                st["applied"] += 1
                if typ == R_REL24 and w is not None and w >> 26 == 18 and w & 1 and \
                        any(lo <= v < lo + n for lo, n in code):
                    seeds.add(v)  # a bl into a module's code: a function entry
    return stats, seeds


def write_dol(dol, runs):
    """main.dol with the modules' code runs in its free text slots and their data runs in its free data slots."""
    secs = gcdisc.dol_sections(dol)
    slots = {i: (a, dol[off:off + n]) for i, off, a, n in secs["text"] + secs["data"]}
    free = {"text": [i for i in range(7) if i not in slots], "data": [i for i in range(7, 18) if i not in slots]}
    placed = []
    for kind in ("text", "data"):
        if len(runs[kind]) > len(free[kind]):
            raise LinkError(f"{len(runs[kind])} {kind} runs, main.dol has {len(free[kind])} free {kind} slot(s)")
        for slot, run in zip(free[kind], runs[kind]):
            lo, hi = run[0][0], max(a + len(b) for a, b in run)
            body = bytearray(hi - lo)
            for a, b in run:
                body[a - lo:a - lo + len(b)] = b
            slots[slot] = (lo, bytes(body))
            placed.append({"slot": slot if slot < 7 else slot - 7, "kind": kind, "start": f"{lo:08x}",
                           "end": f"{hi:08x}"})
    offs, addrs, sizes, body = [0] * 18, [0] * 18, [0] * 18, bytearray()
    pos = 0x100
    for i in sorted(slots):
        a, b = slots[i]
        offs[i], addrs[i], sizes[i] = pos, a, len(b)
        pad = -len(b) % 32
        body += b + bytes(pad)
        pos += len(b) + pad
    bss, bss_size = secs["bss"]
    head = struct.pack(">18I18I18I3I", *offs, *addrs, *sizes, bss, bss_size, secs["entry"]).ljust(0x100, b"\0")
    return head + bytes(body), placed


def writable(r):
    """The module's writable sections, by CodeWarrior's REL layout (.text, .ctors, .dtors, .rodata, .data, .bss): the
    last data section and the bss."""
    data = [s for s in r.sections if not s[3] and not s[4]]
    return ([max(data, key=lambda s: s[0])] if data else []) + [s for s in r.sections if s[4]]


def link(dol, modules, windows):
    """(linked DOL bytes, link record) of main.dol and [(name, REL bytes)]."""
    rels = sorted((Rel(b, name) for name, b in modules), key=lambda r: r.id)
    seen = {}
    for r in rels:
        if r.id == 0 or r.id in seen:
            raise LinkError(f"{r.name}: module id {r.id} is {'main.dol' if r.id == 0 else seen[r.id]}'s")
        seen[r.id] = r.name
    addr, runs = place(dol, rels, windows)
    stats, seeds = relocate(rels, addr, runs)
    out, placed = write_dol(dol, runs)
    for r in rels:
        for k in ("prolog", "epilog", "unresolved"):
            sec, off = r.entries[k]
            if sec and (r.id, sec) in addr:
                seeds.add(addr[(r.id, sec)] + off)
    lay = gcdisc.layout(dol)["project"]
    game = lay["game_ranges"] + [[f"{addr[(r.id, s[0])]:08x}", f"{addr[(r.id, s[0])] + s[2]:08x}"]
                                 for r in rels for s in r.sections if s[3]]
    globs = lay["global_ranges"] + [[f"{addr[(r.id, s[0])]:08x}", f"{addr[(r.id, s[0])] + s[2]:08x}"]
                                    for r in rels for s in writable(r)]
    record = {
        "main_dol": {"sha256": hashlib.sha256(dol).hexdigest(), "end": f"{main_end(dol):08x}"},
        "reserved": [[f"{lo:08x}", f"{hi:08x}"] for lo, hi in windows],
        "modules": [{"module_id": r.id, "name": r.name, "sha256": hashlib.sha256(r.data).hexdigest(),
                     "version": r.version, "align": r.align, "bss_align": r.bss_align,
                     "sections": [{"index": s[0], "exec": s[3], "bss": s[4], "size": s[2],
                                   "addr": f"{addr[(r.id, s[0])]:08x}"} for s in r.sections],
                     **{k: (f"{addr[(r.id, r.entries[k][0])] + r.entries[k][1]:08x}"
                            if r.entries[k][0] and (r.id, r.entries[k][0]) in addr else None)
                        for k in ("prolog", "epilog", "unresolved")},
                     "imports": [m for m, _ in r.imports], "relocations": stats[r.id]} for r in rels],
        "dol_sections": placed,
        "linked_sha256": hashlib.sha256(out).hexdigest(),
        "project": {"game_ranges": game, "global_ranges": globs},
        "seeds": len(seeds),
    }
    return out, record, sorted(seeds)


def where(record, a):
    """The identity of address a: {"module": id, "name", "section", "offset"} (module 0: main.dol, the address)."""
    for m in record["modules"]:
        for s in m["sections"]:
            lo = int(s["addr"], 16)
            if lo <= a < lo + s["size"]:
                return {"module": m["module_id"], "name": m["name"], "section": s["index"], "offset": f"0x{a - lo:x}",
                        "addr": f"{a:08x}"}
    return {"module": 0, "name": "main.dol", "section": None, "offset": f"0x{a:08x}", "addr": f"{a:08x}"}


def address(record, module, section, offset):
    for m in record["modules"]:
        if m["module_id"] == module:
            for s in m["sections"]:
                if s["index"] == section and 0 <= offset < s["size"]:
                    return int(s["addr"], 16) + offset
    raise LinkError(f"module {module} has no section {section} holding offset 0x{offset:x}")


def ids(record, bodies_text):
    """The identity of every catalogued function: a TSV (address, module id, module, section, offset) from the
    catalog's function_bodies.txt ("entry lo hi" lines), one row per entry."""
    entries = sorted({int(ln.split()[0], 16) for ln in bodies_text.splitlines() if ln.strip()})
    rows = [where(record, a) for a in entries]
    return "addr\tmodule_id\tmodule\tsection\toffset\n" + "".join(
        f"{w['addr']}\t{w['module']}\t{w['name']}\t{'' if w['section'] is None else w['section']}\t{w['offset']}\n"
        for w in rows)


def link_files(dol_path, out_dir, rel_paths, inputs=None):
    out_dir = pathlib.Path(out_dir)
    dol = pathlib.Path(dol_path).read_bytes()
    mods = [(pathlib.Path(p).name, pathlib.Path(p).read_bytes()) for p in rel_paths]
    out, record, seeds = link(dol, mods, reserved_windows(pathlib.Path(inputs) if inputs else None))
    out_dir.mkdir(parents=True, exist_ok=False)  # a new folder: never overwrite an earlier link
    write_outputs(out_dir, out, record, seeds)
    return record


def write_outputs(out_dir, out, record, seeds):
    (out_dir / "linked.dol").write_bytes(out)
    (out_dir / "seeds.txt").write_text("".join(f"{a:08x}\n" for a in seeds), encoding="utf-8")
    (out_dir / "link.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def main(argv):
    if len(argv) < 3 or argv[0] not in ("link", "where", "addr", "ids"):
        raise SystemExit(__doc__)
    if argv[0] == "link":
        inputs = None
        if "--inputs" in argv:
            k = argv.index("--inputs")
            inputs, argv = argv[k + 1], argv[:k] + argv[k + 2:]
        rec = link_files(argv[1], argv[2], argv[3:], inputs)
        for m in rec["modules"]:
            print(f"module {m['module_id']:4} {m['name']:24} " + " ".join(
                f"s{s['index']}@{s['addr']}" for s in m["sections"]) + f"  {m['relocations']}")
        print(f"{argv[2]}: linked.dol ({rec['linked_sha256'][:16]}), {rec['seeds']} seeds, DOL sections "
              f"{[(d['kind'], d['start'], d['end']) for d in rec['dol_sections']]}")
        return
    record = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    if argv[0] == "ids":
        text = ids(record, pathlib.Path(argv[2]).read_text(encoding="utf-8"))
        if len(argv) > 3:
            pathlib.Path(argv[3]).write_text(text, encoding="utf-8")
        else:
            print(text, end="")
    elif argv[0] == "where":
        for a in argv[2:]:
            print(json.dumps(where(record, int(a, 16))))
    else:
        print(f"{address(record, int(argv[2], 0), int(argv[3], 0), int(argv[4], 16)):08x}")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except LinkError as e:
        raise SystemExit(f"gcrel: {e}")
