"""Xbox 360 side of the register hand-over check (core/handover.py, docs/HANDOVER_CHECK.md): the record of every function
in gen/, from the translator's own lifted blocks, and what each emitted gen/fn_ADDR.c stores, loads and calls.

usage: py -3.12 handover.py export --ws WS --gen OUT/gen --to DIR [--jobs N] [--game too_human]
           DIR/records.jsonl (one line a function: its blocks as reads and writes, and its C's facts), DIR/skipped.tsv
       py -3.12 handover.py check --ws WS --to DIR [--analysis OUT/analysis.json] [--overlay GEN2] [--path ADDR ...]
           DIR/findings.tsv, DIR/summary.json and a table. --overlay: a folder whose fn_ADDR.c replace gen/'s (a planted
           fault); --path: for each address, the findings on it, its direct callers and all it reaches.
Runs without the game and without a build. export lifts every function once (minutes, worker processes); check is seconds.

Nearest existing: port/tools/read_first.py - reads only gen/'s "read before set" lines, r and f registers, for the next
translation's callers; it checks no edge and sees nothing after a call.
"""
import argparse
import array
import bisect
import collections
import concurrent.futures
import json
import os
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
X360 = HERE.parent.parent
REPO = X360.parent.parent
sys.path[:0] = [str(REPO), str(X360 / "translator"), str(HERE)]
from core import handover as H  # noqa: E402

REG = re.compile(r"r\d+|f\d+|v\d+|c\d(lt|gt|eq|so)|ca|so|ov|ctr|lr|fpscr|vrsave|vscr")
ARGS = {f"r{k}" for k in range(3, 11)} | {f"f{k}" for k in range(1, 14)}
CLOBBER = {"r0", *(f"r{k}" for k in range(3, 13)), "f0", *(f"f{k}" for k in range(1, 14)), "ctr"}   # translate.CALL_CLOBBERS + the results
STATUS = {f"c{n}{b}" for n in (0, 1, 5, 6, 7) for b in ("lt", "gt", "eq", "so")} | {"ca", "so", "ov"}
HANDED = ("arg_", "sp0", "tcr12", "tcnv")     # entry values the caller's TcArgs brings: left undefined, so they show as needs
CR_ALL = {f"c{n}{b}" for n in range(8) for b in ("lt", "gt", "eq", "so")}
FRAME_BASES = {"r1", "r11", "r12"}            # fn_parts.FRAME_BASES: a save of a register's entry value is no read
# Kernel and XAM imports that never come back to the instruction after the call (export_names.json's names). Too Human's
# _CxxThrowException 82a4fcc0 -> RaiseException 82a6eef0 -> RtlRaiseException ended in a blr, so a throw was taken as returning
# and ret-set rows read registers after it (the catch funclet that follows reads r12). RtlRaiseException returns on hardware only
# for a continuable exception a handler continues (a C++ throw is not continuable); in this port every call to it stops the
# process (xb_call.c tc_call: no import of that ordinal, xb_stop "import"). RtlUnwind is not here: the CRT's unwinder calls it
# with its own return address as the target (82a6d190), and it comes back there.
NORETURN_IMPORTS = {"RtlRaiseException", "KeBugCheck", "KeBugCheckEx", "HalReturnToFirmware", "XamLoaderTerminateTitle",
                    "ExTerminateThread"}
TOK = re.compile(r"tc_st([gf])\(\s*(0x[0-9a-f]+|\d+)u\s*,|tc_callf?\(\s*(?:0x([0-9a-f]{8})u\s*,)?")
LD = re.compile(r"(tc_ldg|tc_ldf|xv_ldv)\(\s*(0x[0-9a-f]+|\d+)u\s*\)")
VST = re.compile(r"xv_stv\(\s*(0x[0-9a-f]+|\d+)u\s*,")


def channel(reg):
    if reg in ARGS:
        return "arg"
    if reg in ("r1", "r2", "r12", "r13"):
        return "abi"
    if re.fullmatch(r"[rf]\d+", reg):
        return "file"
    return "vector" if re.fullmatch(r"v\d+", reg) else "none"


ABI = H.Abi(lambda n: REG.fullmatch(n) is not None, channel, CLOBBER, STATUS, {"r3", "f1"})


NONVOL = re.compile(r"[rf](1[4-9]|2\d|3[01])")


def record(blocks, saves=False):
    """The core's record of one function from FunctionTranslator.build()'s blocks (before any pass). saves: the function is the
    game's setjmp (port/games/<game>/jumps.json), whose stores of r14..r31 and f14..f31 into the caller's jmp_buf are saves: the
    port never runs its C (each call of it is a host setjmp, and the runtime's xb_longjmp lands back in the caller's own frame)."""
    index = {b.label: k for k, b in enumerate(blocks)}
    out_b, out_s, calls = [], [], []
    # What a return leaves for the caller: r3, f1 (the lifted blocks do not say which is the result) and every vector register
    # the function writes, since the whole vector file is shared. Without it a result's computation would read nothing.
    vw = sorted({w for b in blocks for s in b.stmts if type(s).__name__ == "Assign" for w in s.writes() if re.fullmatch(r"v\d+", w)})
    at_ret = ["r", ["r3", "f1", *vw]]     # "r": read like "u", and it marks a return (the core works out which functions never return)

    def ret_of(jump):
        return ["u", at_ret[1]] if jump else at_ret      # a blr that jumps elsewhere reads the same, and is no return

    def conv(sts, entry, sw=False):
        """The block's statements, and whether LR at its end was loaded from outside the function's own frame."""
        out = []
        # frame: registers holding an address in this function's own frame (r1, r11, r12 and what is made from them without a
        # load); loaded: register -> the address registers of the load that last set it. Both within the block.
        frame, loaded, lr_jump = set(FRAME_BASES), {}, False
        for k, s in enumerate(sts):
            name = type(s).__name__
            if name in ("Comment", "Clobber"):     # the core ends a register's life at a call from the callee's writes
                continue
            if name == "Ret":
                out.append(ret_of(lr_jump))
                continue
            if name == "Assign":
                src = s.e.vars()
                if s.dst == "lr":
                    # a blr to an address loaded from outside the frame goes elsewhere, not back to the caller: longjmp
                    # 82a566a0 sets LR from its jmp_buf ([r7+0x134]) and its blr lands at setjmp's return point
                    at = frozenset(src) if s.e.has_load() else loaded.get(next(iter(src))) if len(src) == 1 else None
                    lr_jump = bool(at) and not (at & frame)
                if s.e.has_load():
                    loaded[s.dst] = frozenset(src)
                else:
                    loaded.pop(s.dst, None)
                if s.dst not in FRAME_BASES:
                    if not s.e.has_load() and src and src <= frame:
                        frame.add(s.dst)
                    else:
                        frame.discard(s.dst)
            if name == "CallS":
                nxt = next((x for x in sts[k + 1:] if type(x).__name__ != "Clobber"), None)
                tail = not (type(nxt).__name__ == "Assign" and nxt.dst == "lr")
                t = s.target
                calls.append({"at": s.addr, "to": (t.v & 0xFFFFFFFF) if type(t).__name__ == "Const" else None, "tail": tail, **({"sw": True} if sw else {})})
                out.append(["c", len(calls) - 1, sorted(t.vars() - {"g_"})])
            elif name == "Assign":
                uses = s.e.vars()
                if CR_ALL <= uses:       # mfcr: the whole condition register into an integer; which field is then
                    uses = uses - CR_ALL  # picked out is not followed, so it is no read of any (HANDOVER_CHECK.md)
                if entry and any(u.startswith(HANDED) for u in uses):
                    continue
                out.append(["e" if entry else "a", s.dst, sorted(u for u in uses if not u.startswith("tcr"))])
            elif name == "Store" and type(s.val).__name__ == "Var" and s.val.name not in s.addr.vars() and (
                    s.addr.vars() & frame or saves and NONVOL.fullmatch(s.val.name)):
                # a save: a register stored as it stands into the function's own frame, through r1 / r11 / r12 or an address made
                # from them (the inline __savegprlr helpers store through r1). Into any other address it is a read: setjmp
                # 82ec5460's stores of r14..r31 into the caller's jmp_buf read the caller's registers.
                out.append(["u", sorted(s.addr.vars())])
            else:
                out.append(["u", sorted(s.reads())])
                out += [["a", w, []] for w in sorted(s.writes())]
        return out, lr_jump
    extra = []
    for k, b in enumerate(blocks):
        sts, lr_jump = conv(b.stmts, k == 0)
        succ = [index[x] for x in b.term.succs()]
        if type(b.term).__name__ == "Switch":
            sts.append(["u", sorted(b.term.e.vars())])
            succ.append(len(blocks) + len(extra))
            extra.append(conv(b.term.default, False, True)[0])
        elif type(b.term).__name__ == "If":
            sts.append(["u", sorted(b.term.cond.vars())])
        elif type(b.term).__name__ == "Ret":
            sts.append(ret_of(lr_jump))
        out_b.append(sts)
        out_s.append(succ)
    return {"b": out_b + extra, "s": out_s + [[] for _ in extra], "c": calls}


def text_facts(text):
    """What a gen/fn_ADDR.c hands over: {"st": {callee: [regs stored before every call to it]}, "ld": [...], "vst": [...], "nv": bool}."""
    st, put = {}, set()
    for m in TOK.finditer(text):
        if m.group(1):
            put.add(("r" if m.group(1) == "g" else "f") + str(int(m.group(2), 0)))
            continue
        if m.group(3):
            t = m.group(3)
            st[t] = sorted(set(st[t]) & put) if t in st else sorted(put)
        put = set()
    ld = sorted({{"tc_ldg": "r", "tc_ldf": "f", "xv_ldv": "v"}[m.group(1)] + str(int(m.group(2), 0)) for m in LD.finditer(text)})
    return {"st": st, "ld": ld, "vst": sorted({"v" + str(int(m.group(1), 0)) for m in VST.finditer(text)}), "nv": "tcnv" in text}


# ---------------------------------------------------------------------------------------------------------- export
_ws = None
_saves = frozenset()


def _init(ws, game="too_human"):
    global _ws, _saves
    # gen_xbox imports core.paths, which needs a project folder (3355c21b brought the import here, and export stopped with "no
    # project" when run on its own); the workspace is one (project.json). A worker process inherits the variable.
    os.environ.setdefault("DECOMP_PROJECT", str(ws))
    from gen_xbox import jumps      # the game's setjmp: a save routine (record)
    _saves = frozenset(a for a, k in jumps(X360 / "port" / "games" / game).items() if k == "setjmp")
    from xenontranslate import workspace
    _ws = workspace.Workspace(ws, target=X360 / "target.json")
    _ws.facts.port = True
    _ws.img = (pathlib.Path(ws) / "image.bin").read_bytes()
    _ws.base = json.loads((pathlib.Path(ws) / "image.json").read_text()).get("load_address", 0x82000000)
    import xb_join   # gen_xbox.Kit.open's bodies: a catalogue function cut inside its own loop is lifted whole, as gen/ translates it
    for f, lohi in xb_join.join(_ws.bodies, _ws.img, _ws.base).items():
        _ws.bodies[f] = [lohi]
    _ws.starts = sorted(_ws.bodies)


def body_of(ws, addr):
    """gen_xbox.Kit.translate's choice: the catalogue function, else from addr to the end of the one it is inside, else a leaf."""
    import xb_leaf
    if addr in ws.bodies:
        return ws.bodies[addr]
    i = bisect.bisect_right(ws.starts, addr) - 1
    if i >= 0:
        lo, hi = ws.bodies[ws.starts[i]][-1]
        if lo <= addr < hi:
            return [(addr, hi)]
    return xb_leaf.body(ws.img, ws.base, addr)


def _one(job):
    addr, path = job
    from xenontranslate.translate import FunctionTranslator, Signature
    try:
        body = body_of(_ws, addr)
        if body is None:
            return addr, None, "no body"
        rec = record(FunctionTranslator(_ws.dol, addr, Signature.generic_for(addr), _ws.facts, body).build(), addr in _saves)
        rec["t"] = text_facts(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
        return addr, json.dumps(rec, separators=(",", ":")), None
    except Exception as e:  # noqa: BLE001  (one function the lifter refuses: the core takes it as code not analysed)
        return addr, None, f"{type(e).__name__}: {e}"


def export(ws, gen, to, jobs, game="too_human"):
    to.mkdir(parents=True, exist_ok=True)
    todo = sorted((int(p.stem[3:], 16), str(p)) for p in pathlib.Path(gen).glob("fn_*.c"))
    t0, done, bad = time.time(), 0, 0
    with open(to / "records.jsonl", "w", encoding="utf-8") as out, open(to / "skipped.tsv", "w", encoding="utf-8") as skip, \
            concurrent.futures.ProcessPoolExecutor(jobs, initializer=_init, initargs=(str(ws), game)) as pool:
        for addr, line, why in pool.map(_one, todo, chunksize=64):
            done += 1
            if line is None:
                bad += 1
                skip.write(f"{addr:08x}\t{why}\n")
            else:
                out.write(f"{addr:08x}\t{line}\n")
    print(f"export: {done - bad} functions recorded, {bad} skipped, {time.time() - t0:.0f} s")
    return done - bad, bad


# ----------------------------------------------------------------------------------------------------------- check
class GenEvidence(H.Evidence):
    def __init__(self, facts, analysis, pointed):
        self.f, self._pointed = facts, pointed
        self._keeps = {int(a, 16): frozenset(r) for a, r in (analysis or {}).get("keeps", {}).items()}
        self._kinds = {int(a, 16): k for a, k in (analysis or {}).get("kinds", {}).items()}

    def stores(self, caller, callee):
        st = self.f[caller]["st"].get(f"{callee:08x}")
        return None if st is None else frozenset(st)

    def loads(self, fn):
        return frozenset(self.f[fn]["ld"])

    def vstores(self, fn):
        return frozenset(self.f[fn]["vst"])

    def nv(self, fn):
        return self.f[fn]["nv"]

    def keeps(self, fn):
        return self._keeps.get(fn, H.EMPTY)

    def kind(self, fn):
        return self._kinds.get(fn)

    def pointed(self):
        return self._pointed


def game_noreturn(game):
    """The game's own functions that never return although their records show a return (port/games/<game>): the longjmp of
    jumps.json, the throws of eh.json and the list in noreturn.json. Too Human's longjmp 82a566a0 calls RtlUnwind when the
    jmp_buf names a frame to unwind (82a5698c) and then has an ordinary epilogue and blr; its CRT exit 82a4f310 tail-calls
    doexit 82a4f1b0, which returns when its third argument (retcaller, _cexit's) is set. RtlUnwind is no seed (the CRT's own
    unwinder comes back from it), and the check does not follow arguments, so the game's table says so."""
    from gen_xbox import jumps
    game = pathlib.Path(game)
    out = {a for a, k in jumps(game).items() if k == "longjmp"}
    for name, key in (("eh.json", "throw"), ("noreturn.json", "noreturn")):
        if (game / name).exists():
            out |= {int(a, 16) for a in json.loads((game / name).read_text(encoding="utf-8")).get(key, [])}
    return frozenset(out)


def load(to, ws=None, analysis=None, overlay=None, noreturn=()):
    """noreturn: more never-return seeds (game_noreturn)."""
    lines, facts = {}, {}
    with open(pathlib.Path(to) / "records.jsonl", encoding="utf-8") as fh:
        for ln in fh:
            a, _, body = ln.partition("\t")
            lines[int(a, 16)] = body
            k = body.rfind(',"t":')
            facts[int(a, 16)] = json.loads(body[k + 5:body.rstrip().rfind("}")])
    for p in (pathlib.Path(overlay).glob("fn_*.c") if overlay else ()):
        facts[int(p.stem[3:], 16)] = text_facts(p.read_text(encoding="utf-8", errors="replace"))
    pointed = set()
    if ws:
        # a function pointer lives in data: not in code (an inline jump table's case words, 82e00ffc's and 82eb54c0's: 13 of the 15
        # volatile ptr-target rows), not in .pdata (every function's unwind record) and not in .reloc. Every other word counts.
        data = (pathlib.Path(ws) / "image.bin").read_bytes()
        ij = json.loads((pathlib.Path(ws) / "image.json").read_text())
        base = ij.get("load_address", 0x82000000)
        skip = sorted((max(s["start"] - base, 0) & ~3, min(s["end"] - base, len(data)) & ~3) for s in ij.get("sections", [])
                      if s.get("code") or s.get("name") in (".pdata", ".reloc"))
        at = 0
        for lo, hi in skip + [(len(data) & ~3, len(data) & ~3)]:
            if lo > at:
                words = array.array("I", data[at:lo])
                if sys.byteorder == "little":
                    words.byteswap()
                pointed |= set(words) & set(lines)
            at = max(at, hi)
    # Too Human without the game's seeds: 10 ret-set rows read registers after longjmp, 44 after luaX_syntaxerror 82809ae8
    # (it ends in luaX_errorline 82809a50: longjmp, or the panic function and exit) and 3 after exit.
    seeds = (noreturn_thunks(ws) if ws else frozenset()) | frozenset(noreturn)
    an = json.loads(pathlib.Path(analysis).read_text(encoding="utf-8")) if analysis and pathlib.Path(analysis).exists() else None
    if an is not None and "keeps" not in an:     # OUT/cache/keeps_KEY.json itself: {address: [registers]}
        an = {"keeps": an, "kinds": {}}
    return H.Image(lines, ABI, seeds), GenEvidence(facts, an, pointed)


def noreturn_thunks(ws):
    """The import thunks (image.json "imports") of the NORETURN_IMPORTS: the core's never-return seeds."""
    names = json.loads((HERE.parent / "data" / "export_names.json").read_text(encoding="utf-8"))
    imports = json.loads((pathlib.Path(ws) / "image.json").read_text()).get("imports", [])
    out = set()
    for i in imports:
        n = names.get(str(i.get("library", "")).split(".")[0], {}).get(str(i.get("ordinal")))
        if i.get("thunk") and n and n[0] in NORETURN_IMPORTS:
            out.add(int(i["thunk"]))
    return frozenset(out)


def report(img, ev, to, paths=()):
    t0 = time.time()
    rows, seen = H.check(img, ev)
    to = pathlib.Path(to)
    with open(to / "findings.tsv", "w", encoding="utf-8") as fh:
        fh.write("kind\tcaller\tat\tcallee\treg\n")
        for r in rows:
            fh.write(f"{r['kind']}\t{r['caller']:08x}\t{r['at']:08x}\t{r['callee']:08x}\t{r['reg']}\n")
    tab = H.table(rows)
    print(f"{len(img.raw)} functions; " + "; ".join(f"{v} {k}" for k, v in sorted(seen.items())))
    print(f"{'kind':16}{'edges':>8}{'callees':>9}  by register class")
    for k, (n, callees, cls) in tab.items():
        print(f"{k:16}{n:>8}{callees:>9}  " + ", ".join(f"{c} {m}" for c, m in sorted(cls.items(), key=lambda x: -x[1])))
    top = {}
    for k in tab:
        cnt = collections.Counter((r["callee"], r["reg"].split()[0]) for r in rows if r["kind"] == k)
        top[k] = [[f"{a:08x}", reg, n] for (a, reg), n in cnt.most_common(12)]
        if top[k]:
            print(f"top {k}: " + ", ".join(f"{a} {reg} x{n}" for a, reg, n in top[k]))
    by_reader = collections.Counter()     # a forward finding belongs to the function that really reads the register
    for r in rows:
        if r["kind"] in ("fwd-unstored", "fwd-nochannel", "vec-unstored"):
            for g in H.readers(img, r["callee"], r["reg"]):
                by_reader[(r["kind"], g)] += 1
    top["readers"] = [[k, f"{g:08x}", n] for (k, g), n in by_reader.most_common(40)]
    print(f"forward findings by reading function ({len(by_reader)}): " + ", ".join(f"{k} {g} x{n}" for k, g, n in top["readers"][:14]))
    on = {}
    for a in paths:
        near = set(H.closure(img, [a], 0)) | {p for p in img.callers.get(a, ())}
        far = H.closure(img, [a])
        on[f"{a:08x}"] = {"in records": a in img.raw, "reaches": len(far), "needs": sorted(img.need.get(a, ())),
                          "on it or its callers": collections.Counter(r["kind"] for r in rows if r["caller"] in near or r["callee"] in near),
                          "in all it reaches": collections.Counter(r["kind"] for r in rows if r["caller"] in far or r["callee"] in far)}
        print(f"path {a:08x}: {json.dumps(on[f'{a:08x}'])}")
    (to / "summary.json").write_text(json.dumps({"functions": len(img.raw), "seen": seen, "table": tab, "top": top, "paths": on}, indent=1), encoding="utf-8")
    print(f"check: {len(rows)} findings, {time.time() - t0:.0f} s -> {to / 'findings.tsv'}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["export", "check"])
    ap.add_argument("--ws", required=True)
    ap.add_argument("--gen")
    ap.add_argument("--to", required=True)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--analysis")
    ap.add_argument("--overlay")
    ap.add_argument("--path", nargs="*", default=[])
    ap.add_argument("--game", default="too_human")   # port/games/<game>/jumps.json: the setjmp whose stores are saves
    a = ap.parse_args()
    if a.cmd == "export":
        export(pathlib.Path(a.ws), a.gen, pathlib.Path(a.to), a.jobs, a.game)
        return 0
    os.environ.setdefault("DECOMP_PROJECT", str(a.ws))   # gen_xbox imports core.paths, which needs a project folder (_init)
    img, ev = load(a.to, a.ws, a.analysis, a.overlay, game_noreturn(X360 / "port" / "games" / a.game))
    report(img, ev, a.to, [int(x, 16) for x in a.path])
    return 0


if __name__ == "__main__":
    sys.exit(main())
