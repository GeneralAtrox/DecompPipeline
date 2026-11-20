"""The game's C++ exception tables, read from the image at gen time (docs/x360/EXCEPTIONS_AND_LONGJMP.md, part (b)).
port/games/<game>/eh.json names the game's frame handler (MSVC's __CxxFrameHandler, whose handler data is a FuncInfo with magic
0x19930522) and its throw (_CxxThrowException; a call of it never returns). Every .pdata function with the exception flag and that
handler shares a FuncInfo with its catch and unwind funclets; scan() groups them, reads the unwind map, the try map with its catch
handlers and the IP-to-state map, finds each catch funclet's continuation (the constant r3 at its exits) and validates the lot.
A wrong state runs the wrong destructors without a sign, so a group that fails is kept with its reasons: gen_xbox writes them into
the runtime's table (xb_eh_fn), and a throw through such a frame stops with the reason instead of unwinding (runtime/xb_eh.h, which
checks the tables in guest memory again at the throw).
Layouts, as Too Human's image has them (82a4ff28's 260 functions): FuncInfo {magic, maxState, pUnwindMap, nTryBlocks, pTryBlockMap,
nIPMapEntries, pIPtoStateMap, 0, EHFlags}; UnwindMapEntry {toState, action} (8 bytes); TryBlockMapEntry {tryLow, tryHigh, catchHigh,
nCatches, pHandlerArray} (20); HandlerType {adjectives, pType, dispCatchObj, addressOfHandler} (16), the catch object at the
establisher frame (the parent's entry r1, which a funclet gets in r12) + dispCatchObj; IP-to-state {Ip, State} (8), an IP's state
being that of the last entry at or below it, the call instruction's own address.
The SEH frames a throw may pass (scan(seh=...)): every .pdata function whose handler is xboxkrnl's __C_specific_handler (its import
thunk, seh_handler()) has a scope table at its handler data, {count, {begin, end, handler, target}[]}: target 0 is a __finally whose
handler is its funclet (r12 the frame's entry r1, as a catch funclet's), else an __except whose handler is its filter. read_seh()
checks each scope lies in its function; such a function records its call sites too, and the runtime runs its __finally blocks.
usage: python eh_tables.py <workspace dir> <game dir>   (prints the groups that fail and why: the scan over the whole image)
Nearest existing: tools in this folder read the image for one purpose each (xb_join.py, shader_convert.py); none reads .pdata or
the C++ EH tables, and the translator's decoder (xenontranslate.decode) is reused here for the continuation scan.
"""
import json
import pathlib
import struct
import sys

MAGIC = (0x19930520, 0x19930521, 0x19930522)
LIMIT = 4096        # states, try blocks, catches per try, IP map entries: more is not a table


def load(game):
    """port/games/<game>/eh.json: {"frame_handler": int, "throw": [int]}, or None without the file."""
    p = pathlib.Path(game) / "eh.json"
    if not p.exists():
        return None
    t = json.loads(p.read_text(encoding="utf-8"))
    return {"frame_handler": int(t["frame_handler"], 16), "throw": [int(a, 16) for a in t.get("throw", [])]}


class Img:
    def __init__(self, data, base):
        self.data, self.base = data, base

    def has(self, a, n=4):
        return 0 <= a - self.base and a - self.base + n <= len(self.data)

    def w(self, a):
        if not self.has(a):
            raise LookupError(f"{a:08x} is outside the image")
        return struct.unpack_from(">I", self.data, a - self.base)[0]

    def s(self, a):
        v = self.w(a)
        return v - (1 << 32) if v & 0x80000000 else v


def pdata(img, meta):
    """[(begin, end, exception flag)] of the image's .pdata (Xbox 360: {begin, prolog:8 length:22 32bit:1 exception:1})."""
    out = []
    for sec in meta.get("sections", []):
        if sec.get("name") != ".pdata":
            continue
        for o in range(sec["start"], sec["raw_end"] - 7, 8):
            b, p = img.w(o), img.w(o + 4)
            if b:
                out.append((b, b + 4 * ((p >> 8) & 0x3FFFFF), p >> 31))
    return sorted(out)


def read_group(img, fi, fns):
    """The tables of the FuncInfo at fi, shared by the functions fns [(begin, end)]."""
    g = {"funcinfo": fi, "functions": sorted(fns), "unwind": [], "tries": [], "ipmap": [], "continuations": {}, "problems": []}
    try:
        g["magic"] = img.w(fi) & 0x1FFFFFFF
        g["max_state"] = img.s(fi + 4)
        ntry, nip = img.w(fi + 12), img.w(fi + 20)
        if not (0 <= g["max_state"] <= LIMIT and ntry <= LIMIT and nip <= LIMIT):
            g["problems"].append(f"FuncInfo {fi:08x}: counts out of range (maxState {g['max_state']}, {ntry} try blocks, {nip} IP entries)")
            return g
        um, tm, ipm = img.w(fi + 8), img.w(fi + 16), img.w(fi + 24)
        g["unwind"] = [(img.s(um + 8 * k), img.w(um + 8 * k + 4)) for k in range(g["max_state"])]
        for t in range(ntry):
            e = tm + 20 * t
            n, ha = img.w(e + 12), img.w(e + 16)
            if n > LIMIT:
                g["problems"].append(f"try {t}: {n} catches")
                n = 0
            g["tries"].append({"low": img.s(e), "high": img.s(e + 4), "catch_high": img.s(e + 8),
                               "handlers": [tuple(img.w(ha + 16 * h + 4 * k) for k in range(4)) for h in range(n)]})
        g["ipmap"] = [(img.w(ipm + 8 * k), img.s(ipm + 8 * k + 4)) for k in range(nip)]
    except LookupError as e:
        g["problems"].append(f"FuncInfo {fi:08x} not readable: {e}")
    return g


def continuations(img, lo, hi, noreturn=()):
    """The constant r3 at each exit of the funclet [lo, hi) (a blr, or a branch out of it: the epilogue helper returns): the
    continuation a catch funclet hands back. None when an exit's r3 is not known. A path follows branches inside the funclet;
    a call of a noreturn function (the throw) ends it. A register an instruction might write is forgotten, and all of them at a
    place two paths meet."""
    from xenontranslate.decode import decode
    reach, leaders, work = set(), {lo}, [lo]
    while work:
        a = work.pop()
        while lo <= a < hi and a not in reach and img.has(a):
            reach.add(a)
            i = decode(img.w(a), a)
            o, lk = i.op, i.f.get("lk", 0)
            if o in ("b", "bc") and not lk:
                if lo <= i.target < hi:
                    leaders.add(i.target)
                    work.append(i.target)
                if o == "b" or (i.bo & 0x14) == 0x14:
                    break
                leaders.add(a + 4)
            elif o == "b" and lk and i.target in noreturn:
                break
            elif o in ("bclr", "bcctr") and not lk and (i.bo & 0x14) == 0x14:
                break
            elif o == "illegal":
                break
            a += 4
    out, regs = set(), {}

    def set_(r, v):
        if v is None:
            regs.pop(r, None)
        else:
            regs[r] = v & 0xFFFFFFFF
    for a in sorted(reach):
        if a in leaders or a - 4 not in reach:
            regs = {}
        i = decode(img.w(a), a)
        o, f, lk = i.op, i.f, i.f.get("lk", 0)
        exit_ = (o in ("bclr", "bcctr") and not lk) or (o in ("b", "bc") and not lk and not lo <= i.target < hi)
        if exit_:
            if 3 not in regs:
                return None
            out.add(regs[3])
        if o in ("b", "bc", "bclr", "bcctr") and lk:
            for r in [0] + list(range(3, 13)):
                regs.pop(r, None)
        elif o in ("addi", "addis"):
            sh = 16 if o == "addis" else 0
            base = 0 if f["ra"] == 0 else regs.get(f["ra"])
            set_(f["rd"], None if base is None else base + (f["simm"] << sh))
        elif o == "ori":
            set_(f["ra"], regs[f["rs"]] | f["uimm"] if f["rs"] in regs else None)
        elif o == "or" and f.get("rs") == f.get("rb"):
            set_(f["ra"], regs.get(f["rs"]))
        elif o.startswith("st"):           # a store writes no register, but an update form writes its base
            if "u" in o[2:]:
                regs.pop(f.get("ra"), None)
        elif o not in ("b", "bc", "bclr", "bcctr", "cmpi", "cmpli", "cmp", "cmpl"):
            for k in ("rd", "ra", "rs", "rb", "rt"):
                regs.pop(f.get(k), None)
    return sorted(out)


def chain(unwind, s, to):
    """Whether the toState chain from state s reaches state to (with -1 its end) without leaving the map or looping."""
    for _ in range(len(unwind) + 2):
        if s == to:
            return True
        if not 0 <= s < len(unwind):
            return False
        s = unwind[s][0]
    return False


def validate(g):
    """The reasons the group g cannot be trusted at a throw ([] when it can): magic; every state's toState chain ends at -1;
    each try's states [low, high] lie in the unwind map with chains through low, and its catch states above them; each catch
    handler is a funclet of the group; one function of the group is the parent; the IP-to-state map is ordered, its states in
    the map and its IPs inside the group's functions; and every catch continuation lies inside the parent."""
    p = list(g["problems"])
    if p:
        return p
    fns, u, ms = g["functions"], g["unwind"], g["max_state"]

    def inside(a, ranges=fns):
        return any(lo <= a < hi for lo, hi in ranges)
    if g["magic"] not in MAGIC:
        p.append(f"magic {g['magic']:08x} is not MSVC's 0x1993052x")
    for s, (to, _) in enumerate(u):
        if not -1 <= to < ms:
            p.append(f"unwind state {s}: toState {to} outside -1..{ms - 1}")
        elif not chain(u, s, -1):
            p.append(f"unwind state {s}: the toState chain does not end at -1")
    handlers = set()
    for t, tb in enumerate(g["tries"]):
        lo_, hi_, ch = tb["low"], tb["high"], tb["catch_high"]
        if not 0 <= lo_ <= hi_ < ch < ms:
            p.append(f"try {t}: states {lo_}..{hi_}, catches to {ch}: not inside the unwind map of {ms} states")
            continue
        for s in range(lo_, hi_ + 1):
            if not chain(u, s, lo_):
                p.append(f"try {t}: state {s}'s chain does not pass the try's state {lo_}")
        if not tb["handlers"]:
            p.append(f"try {t}: no catch")
        for adj, ty, disp, addr in tb["handlers"]:
            handlers.add(addr)
            if not any(lo == addr for lo, _ in fns):
                p.append(f"try {t}: catch handler {addr:08x} is not a function sharing this FuncInfo")
    actions = {a for _, a in u if a}
    parents = [(lo, hi) for lo, hi in fns if lo not in handlers and lo not in actions]
    if len(parents) != 1:
        p.append(f"{len(parents)} functions of the group are not catch or unwind funclets (one parent expected)")
    g["parent"] = parents[0][0] if len(parents) == 1 else None
    last = None
    for ip, st in g["ipmap"]:
        if last is not None and ip <= last:
            p.append(f"IP-to-state entry {ip:08x} is not above the one before it")
        last = ip
        if not -1 <= st < ms:
            p.append(f"IP-to-state entry {ip:08x}: state {st} outside -1..{ms - 1}")
        if not inside(ip):
            p.append(f"IP-to-state entry {ip:08x} is outside the functions of this FuncInfo")
    if g["parent"] is not None:
        pr = [r for r in fns if r[0] == g["parent"]]
        for h, cs in sorted(g["continuations"].items()):
            if cs is None:
                p.append(f"catch handler {h:08x}: its continuation is not known (r3 at an exit is not a constant)")
                continue
            for c in cs:
                if not inside(c, pr):
                    p.append(f"catch handler {h:08x}: continuation {c:08x} is outside the parent {g['parent']:08x}")
    return p


def seh_handler(meta):
    """The import thunk of xboxkrnl's __C_specific_handler (the frame handler of __try / __finally / __except), or None."""
    names = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "export_names.json").read_text(encoding="utf-8"))
    for i in meta.get("imports", []):
        n = names.get(str(i.get("library", "")).split(".")[0], {}).get(str(i.get("ordinal")))
        if i.get("thunk") and n and n[0] == "__C_specific_handler":
            return i["thunk"]
    return None


def read_seh(img, b, e, begins):
    """A __C_specific_handler function [b, e) and its scope table (at its handler data, b - 4), validated (g["problems"])."""
    g = {"kind": "seh", "funcinfo": None, "scope": 0, "functions": [(b, e)], "parent": None, "scopes": [], "problems": []}
    p = g["problems"]
    try:
        g["scope"] = st = img.w(b - 4)
        n = img.w(st)
        if not 0 < n <= 64:
            p.append(f"scope table {st:08x}: {n} scopes")
            return g
        g["scopes"] = [tuple(img.w(st + 4 + 16 * k + 4 * j) for j in range(4)) for k in range(n)]
    except LookupError as x:
        p.append(f"scope table of {b:08x}: {x}")
        return g
    for k, (lo, hi, h, jt) in enumerate(g["scopes"]):
        if not b <= lo < hi <= e:
            p.append(f"scope {k}: {lo:08x}..{hi:08x} is outside the function {b:08x}..{e:08x}")
        if jt == 0 and h not in begins:
            p.append(f"scope {k}: __finally handler {h:08x} is not a function")
        if jt and not b <= jt < e:
            p.append(f"scope {k}: __except target {jt:08x} is outside the function {b:08x}..{e:08x}")
    return g


def scan(data, meta, frame_handler, throws=(), seh=None):
    """The groups of the image's FuncInfo tables for frame_handler, each validated (g["problems"]); with seh (the
    __C_specific_handler thunk), also one group ("kind": "seh") per function of that handler, with its scope table."""
    img = Img(data, meta.get("load_address", 0x82000000))
    rows = pdata(img, meta)
    by = {}
    for b, e, x in rows:
        if x and img.has(b - 8, 8) and img.w(b - 8) == frame_handler:
            by.setdefault(img.w(b - 4), []).append((b, e))
    out = []
    for fi, fns in sorted(by.items()):
        g = read_group(img, fi, fns)
        if not g["problems"]:
            ends = dict(fns)
            for tb in g["tries"]:
                for h in tb["handlers"]:
                    if h[3] in ends:
                        g["continuations"][h[3]] = continuations(img, h[3], ends[h[3]], set(throws))
        g["problems"] = validate(g)
        out.append(g)
    if seh:
        begins = {b for b, _, _ in rows}
        out += [read_seh(img, b, e, begins) for b, e, x in rows if x and img.has(b - 8, 8) and img.w(b - 8) == seh]
    return out


def frame_handler(data, meta):
    """MSVC's __CxxFrameHandler, found without eh.json: the .pdata handler (the word at begin - 8) of the most exception-flagged
    functions whose handler data (begin - 4) points at a FuncInfo magic (0x1993052x), or None. ppcscan uses it to read the
    catch continuations of a title it has no port/games folder for."""
    img = Img(data, meta.get("load_address", 0x82000000))
    n = {}
    for b, _e, x in pdata(img, meta):
        if x and img.has(b - 8, 8):
            fi = img.w(b - 4)
            if img.has(fi) and img.w(fi) & 0x1FFFFFFF in MAGIC:
                n[img.w(b - 8)] = n.get(img.w(b - 8), 0) + 1
    return max(n, key=n.get) if n else None


def continuation_set(groups):
    """Every catch continuation of the groups (those that failed validation too): addresses the funclets hand back in r3, which
    run in the parent's frame (landing slots), not as functions. ppcscan.code_built_starts leaves them out."""
    return {c for g in groups for cs in g.get("continuations", {}).values() for c in (cs or []) if c}


def facts(groups):
    """The translator's Facts.eh (xenontranslate/eh.py): every function of a group records its call sites; a parent whose catches
    return gets a landing slot. A group that failed validation records its call sites too (a throw through it then stops with
    the reason) but gets no landing."""
    fns, land = set(), {}
    for g in groups:
        fns |= {lo for lo, _ in g["functions"]}
        if not g["problems"] and g.get("parent") is not None:
            cs = sorted({c for v in g["continuations"].values() for c in v})
            if cs:
                land[g["parent"]] = cs
    return {"fns": fns, "land": land}    # a __C_specific_handler function (no parent) records its call sites: the runtime runs its __finally


def c_table(groups):
    """The runtime's table: one row per function of a group, sorted by entry: {begin, end, funcinfo, parent, reason or 0}."""
    rows, seh = [], []

    def why(g):
        w = "; ".join(g["problems"])[:300].replace("\\", "/").replace('"', "'")
        return f'"{w}"' if w else "0"
    for g in groups:
        if g.get("kind") == "seh":       # xb_eh_seh: {begin, end, scope table, reason or 0}
            seh.append((*g["functions"][0], g["scope"], why(g)))
            continue
        for lo, hi in g["functions"]:
            rows.append((lo, hi, g["funcinfo"], g.get("parent") or 0, why(g)))
    rows.sort()
    seh.sort()
    out = ["const XbEhFn xb_eh_fn[] = {"] + [f"    {{0x{a:08x}u, 0x{b:08x}u, 0x{c:08x}u, 0x{d:08x}u, {w}}}," for a, b, c, d, w in rows]
    out += ["    {0, 0, 0, 0, 0}"] if not rows else []
    out += ["};", f"const unsigned xb_eh_fn_count = {len(rows)};", "", "const XbSeh xb_eh_seh[] = {"]
    out += [f"    {{0x{a:08x}u, 0x{b:08x}u, 0x{c:08x}u, {w}}}," for a, b, c, w in seh] + (["    {0, 0, 0, 0}"] if not seh else [])
    return out + ["};", f"const unsigned xb_eh_seh_count = {len(seh)};", ""]


def main(argv):
    ws, game = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "translator"))
    eh = load(game)
    if eh is None:
        print(f"no eh.json in {game}")
        return 1
    meta = json.loads((ws / "image.json").read_text(encoding="utf-8"))
    every = scan((ws / "image.bin").read_bytes(), meta, eh["frame_handler"], eh["throw"], seh=seh_handler(meta))
    groups, sehs = [g for g in every if g.get("kind") != "seh"], [g for g in every if g.get("kind") == "seh"]
    bad = [g for g in every if g["problems"]]
    f = facts(groups)
    sc = [s for g in sehs for s in g["scopes"]]
    print(f"{len(sehs)} __C_specific_handler functions ({sum(1 for s in sc if not s[3])} __finally, {sum(1 for s in sc if s[3])} __except "
          f"scopes), {sum(1 for g in sehs if g['problems'])} fail validation")
    print(f"{len(groups)} FuncInfo groups, {len(f['fns'])} functions, {sum(len(g['tries']) for g in groups)} try blocks, "
          f"{len(f['land'])} landing functions, {len(bad)} groups fail validation")
    for g in bad[:20]:
        print(f"  {g['funcinfo'] or g['scope']:08x} ({', '.join(f'{lo:08x}' for lo, _ in g['functions'])}): {'; '.join(g['problems'])[:300]}")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
