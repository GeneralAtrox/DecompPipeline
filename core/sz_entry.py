"""FPSCR.SZ at a function's entry, from its callers' code: the judge runs a function in the mode the game calls it in.

The SH-4's FPSCR.SZ makes fmov move 64-bit register pairs, and a function can be called with SZ=1 already set: its
caller ran fschg, loaded FPSCR, or called a function that returns with SZ flipped (Toy Commander 8c09de06 calls
8c09ce34, `rts; fschg`, around the TM2X handlers' loop). The oracle starts such a function in each mode its callers
use (TcPrepare script arg 13, TcFpscr.Sz), the C runners start the C in it (vectors.txt line z), and the translator gives it
a body per mode (by_mode, from meta.json "sz_entry").

How the modes are found (exact; nothing is guessed):
- each function's SZ is followed along its own control flow from each entry mode (0 and 1), through fschg, through
  `lds Rm,FPSCR` of a known value (a constant, or FPSCR read by sts and changed by and/or with constants: the
  compiler's PR switches; or FPSCR saved in its own frame and loaded back) and through calls: a callee's SZ at its
  return for each entry mode (its own result, worked out to a fixed point). A call through a pointer made with SZ=0
  keeps it when no function referenced as a pointer word anywhere in the image (a literal-pool word only a call
  takes as its target is no such reference) returns another SZ when entered with 0; one made with SZ=1 reaches the
  functions "fpscr_sz_from_caller" names and returns what they return (no fact: unknown);
- a call site's SZ, for each mode its caller runs in, is a mode its callee runs in;
- a function no direct call reaches, or one referenced as a pointer word, is also called in the ABI's mode, SZ=0;
  project.json "fpscr_sz_from_caller" {addr: evidence} (the fallback for callers the facts cannot follow: calls
  through a table) makes that pointer call's mode SZ=1 instead;
- a call site whose SZ depends on data (paths that meet with SZ different, an lds of an unknown value, a callee with
  an unknown result, code the flow does not reach) is reported and left out, as is an SZ=1 call through a pointer
  (its targets need the project fact). Call sites in catalog ranges the function's entry never reaches are left
  out and counted.
A context switch (it loads FPSCR and also r15 or a control register, SR, GBR or a bank, from saved state: a thread
switch, an interrupt's return) gives its caller SZ back as it was: the code after its call runs only once the
caller's own context is restored. They are listed (context_switches).
PR is not followed: in a function with fpchg, fschg's SZ is unknown (fschg does nothing while PR=1).

usage: python -m core.sz_entry --ws WORKSPACE [--json OUT]: the affected functions and the reported sites.

Nearest existing: the translator's fpu_modes (translator/sh4translate/translate.py) - it follows SZ inside one
function's code only, from SZ=0 at its entry, and never across a call into a callee or back from it.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import sys

VERSION = 2
SZ_BIT = 1 << 20
DIGIT = {0: "0", 1: "1", None: "u", -1: "n"}
_memo = {}


def _decoder():
    here = pathlib.Path(__file__).resolve().parents[1] / "targets" / "dreamcast_sh4" / "translator"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from sh4translate.decode import decode
    return decode


def bodies_of(text):
    """{entry: [(lo, hi)]} from state/function_bodies.txt ("entry lo hi", hi exclusive)."""
    out = {}
    for ln in text.splitlines():
        w = ln.split()
        if len(w) == 3:
            e, lo, hi = (int(x, 16) for x in w)
            out.setdefault(e, []).append((lo, hi))
    return out


class Flow:
    """One function's SZ along its control flow from each entry mode: its call sites' SZ and its SZ at return."""

    def __init__(self, img, base, entry, ranges, decode):
        self.img, self.base, self.entry = img, base, entry
        self.insns = {}
        for lo, hi in ranges:
            for a in range(lo, hi, 2):
                o = a - base
                if 0 <= o and o + 2 <= len(img):
                    self.insns[a] = decode(img[o] | img[o + 1] << 8, a)
        ops = {i.op for i in self.insns.values()}
        self.changes = "fschg" in ops or any(i.op in ("lds", "lds.l") and i.imm == 6 for i in self.insns.values())
        self.pr_unknown = "fpchg" in ops
        # a context switch: it loads FPSCR and also r15 or a control register (SR, GBR, the banks) from saved state
        self.context = self.changes and any(i.op.startswith("ldc") or (i.n == 15 and i.op in (
            "mov.l@_ld", "mov.l@disp_ld", "mov.l@pc", "mov.l@r0_ld")) for i in self.insns.values())
        self.calls = [(a, i) for a, i in sorted(self.insns.items()) if i.op in ("bsr", "bsrf", "jsr")
                      or (i.op in ("bra", "jmp") and not (i.op == "bra" and i.ea in self.insns))]
        self.sites = {0: {}, 1: {}}   # entry mode: {site: (target or None, SZ at the callee's entry: 0, 1, None)}
        self.pool_calls = set()       # the mov.l @(disp,PC) whose word only a call used as its target
        self.exit = (0, 1)            # SZ at return for entry 0 and 1 (None: unknown; -1: never returns)
        self.unreached = 0

    def word(self, a):
        o = a - self.base
        return struct.unpack_from("<I", self.img, o)[0] if 0 <= o and o + 4 <= len(self.img) else None

    def target(self, i, regs):
        if i.op in ("bsr", "bra"):
            return i.ea
        if i.op in ("jsr", "jmp"):
            v = regs.get(i.m)
            return v[1] if v and v[0] == "c" else None
        return None

    def run(self, effects, keep_unknown):
        """Follow SZ from each entry mode. effects: {function: its exit pair}; keep_unknown: SZ after a call to an
        unknown target made with SZ 0 and with SZ 1 (None: not known)."""
        plain = not self.changes and tuple(keep_unknown) == (0, 1) and all(
            effects.get(self.target(i, {}), (0, 1)) == (0, 1) for _, i in self.calls)
        if plain:   # nothing in it or its known callees changes SZ (a jsr's target is found by the flow only)
            plain = not any(i.op in ("jsr", "jmp", "braf", "bsrf") for i in self.insns.values())
        x = [0, 1]
        for e in (0, 1):
            if plain:
                self.sites[e] = {a: (self.target(i, {}), e) for a, i in self.calls}
            else:
                self.sites[e], x[e] = self.follow(e, effects, keep_unknown)
                if self.context and x[e] is None:
                    x[e] = e    # its caller runs on only once its own context, FPSCR included, is back
        self.exit = tuple(x)

    def follow(self, e, effects, keep_unknown):
        state = {self.entry: (e, {"sp": 0})}
        sites, rets, work = {}, set(), [self.entry]
        computed = set()   # SZ at the jumps through a table: their cases are the code no other path reaches
        slots = set()      # the delayed transfers whose slot ran (a + 2 is a slot, not a case)

        def join(b, sz, regs):
            if b not in self.insns:
                return
            if b not in state:
                state[b] = (sz, regs)
                work.append(b)
                return
            osz, oregs = state[b]
            n = (osz if osz == sz else None, {k: v for k, v in oregs.items() if regs.get(k) == v})
            if n != (osz, oregs):
                state[b] = n
                work.append(b)

        while work or computed:
            if not work:
                sz = computed.pop() if len(computed) == 1 else None
                computed.clear()
                for b in self.insns:
                    if b not in state and b - 2 not in slots:
                        join(b, sz, {})
                continue
            a = work.pop()
            sz, regs = state[a]
            regs = dict(regs)
            i = self.insns[a]
            tgt = self.target(i, regs)
            if i.delayed:
                slots.add(a)
            if tgt is not None and i.op in ("jsr", "jmp") and len(regs[i.m]) > 2:
                self.pool_calls.add(regs[i.m][2])
            for x in [i] if not i.delayed else ([self.insns[a + 2]] if a + 2 in self.insns else []):
                sz = self.step(sz, regs, x)   # a delay slot runs before the transfer
            o = i.op
            if o in ("bsr", "jsr", "bsrf"):
                old = sites.get(a, (tgt, sz))
                sites[a] = (tgt, sz if old[1] == sz and old[0] == tgt else None)
                after = self.after(sz, tgt, effects, keep_unknown)
                if after != -1:
                    join(a + 4, after, {k: v for k, v in regs.items() if not isinstance(k, int) or k > 7})
            elif o in ("bra", "jmp") and tgt is not None and tgt in self.insns:
                join(tgt, sz, regs)
            elif o == "jmp" and regs.get(i.m, ("",))[0] == "p" and regs[i.m][1] in self.insns:
                computed.add(sz)        # a jump computed from an address in its own body (a Duff's device)
            elif o in ("bra", "jmp"):   # a tail jump: the callee's return is this function's
                old = sites.get(a, (tgt, sz))
                sites[a] = (tgt, sz if old[1] == sz and old[0] == tgt else None)
                rets.add(self.after(sz, tgt, effects, keep_unknown))
                if tgt is None:         # or a jump through a table: its cases run in this SZ
                    computed.add(sz)
            elif o == "braf":           # a jump through a table: its cases run in this SZ
                computed.add(sz)
            elif o in ("rts", "rte"):
                rets.add(sz)
            elif o in ("bt", "bf"):
                join(a + 2, sz, regs)
                join(i.ea, sz, regs)
            elif o in ("bt/s", "bf/s"):
                join(a + 4, sz, regs)
                join(i.ea, sz, regs)
            else:
                join(a + 2, sz, regs)
        self.state = state
        self.unreached = sum(a not in sites for a, _ in self.calls)   # catalog code its entry never reaches
        rets.discard(-1)
        return sites, (-1 if not rets else rets.pop() if len(rets) == 1 else None)

    @staticmethod
    def after(sz, tgt, effects, keep_unknown):
        if sz is None:
            return None
        if tgt is None:
            return keep_unknown[sz]
        return effects.get(tgt, (0, 1))[sz]

    CONST = {"shll": lambda v: v << 1, "shll2": lambda v: v << 2, "shll8": lambda v: v << 8,
             "shll16": lambda v: v << 16, "shlr": lambda v: v >> 1, "shlr2": lambda v: v >> 2,
             "shlr8": lambda v: v >> 8, "shlr16": lambda v: v >> 16}
    UNARY = {"not": lambda v: ~v, "neg": lambda v: -v, "extu.b": lambda v: v & 0xFF, "extu.w": lambda v: v & 0xFFFF,
             "swap.w": lambda v: v >> 16 | v << 16}

    def step(self, sz, regs, x):
        o = x.op
        if o == "fschg":
            return None if self.pr_unknown or sz is None else sz ^ 1
        loaded = self.frame(sz, regs, x)
        if o in ("lds", "lds.l") and x.imm == 6:
            v = regs.get(x.m) if o == "lds" else loaded   # lds.l: the compiler's restore of what it saved
            if v is None:
                return None
            return v[1] >> 20 & 1 if v[0] == "c" else v[1]
        if o.startswith(("sts.l", "stc.l", "lds.l", "ldc.l")):
            return sz
        m, n = regs.get(x.m), regs.get(x.n)
        out = None
        if o in ("mov.l@+_ld", "mov.l@_ld", "mov.l@disp_ld") and x.m == 15:
            out = loaded
        elif o == "mov.l@pc":
            w = self.word(x.ea)
            out = ("c", w, x.addr) if w is not None else None
        elif o == "mov#":
            out = ("c", x.imm & 0xFFFFFFFF)
        elif o == "mov":
            out = m
        elif o == "sts" and x.imm == 6:
            out = ("f", sz)                    # FPSCR read: SZ as it is now
        elif o in self.UNARY and m and m[0] == "c":
            out = ("c", self.UNARY[o](m[1]) & 0xFFFFFFFF)
        elif o in self.CONST and n and n[0] == "c":
            out = ("c", self.CONST[o](n[1]) & 0xFFFFFFFF)
        elif o in ("or", "and") and n and m and m[0] == "c":
            out = self.mask(n, o, m[1])
        elif o == "add#" and n and n[0] == "c":
            out = ("c", (n[1] + x.imm) & 0xFFFFFFFF)
        elif o in ("add", "sub") and n and n[0] in "cp":
            out = ("p", n[1])                  # an address computed from a constant one
        elif o in ("or#", "and#") and regs.get(0):
            r0 = self.mask(regs[0], o[:-1], x.imm)
            regs[0] = r0
            return sz
        for r in self.writes(x):
            regs.pop(r, None)
            if r == 15 and not (o == "add#" or "@-" in o or "@+" in o):
                regs.pop("sp", None)           # r15 from anything else: the frame is not followed further
                regs.pop("fr", None)
        if out is not None:
            regs[x.n] = out
        return sz

    SIZE = {"b": 1, "w": 2, "l": 4}

    def frame(self, sz, regs, x):
        """The function's own frame (regs "sp": r15's offset from its entry value, "fr": ((offset, value), ...)), so
        FPSCR saved on the stack and loaded back is followed (sts.l fpscr,@-r15 / lds.l @r15+,fpscr, or sts into a
        register stored there). Updates both and returns the word an r15-based load reads (None: not known)."""
        o, sp = x.op, regs.get("sp")
        if sp is None:
            return None
        fr = dict(regs.get("fr", ()))
        size = 8 if o.startswith("fmov") and sz else 4 if o.startswith(("fmov", "sts", "stc", "lds", "ldc"))             else self.SIZE.get(o[4:5], 4)
        if o.startswith("fmov") and sz is None and ("@-" in o or "@+" in o) and 15 in (x.n, x.m):
            regs.pop("sp", None)
            regs.pop("fr", None)
            return None
        got = None
        if "@-" in o and x.n == 15:                         # a push
            sp -= size
            for k in range(sp - 3, sp + size):
                fr.pop(k, None)
            v = ("f", sz) if o == "sts.l" and x.imm == 6 else regs.get(x.m) if o == "mov.l@-_st" else None
            if v is not None:
                fr[sp] = v
        elif ("@+" in o or o in ("lds.l", "ldc.l")) and x.m == 15:   # a pop
            got = fr.get(sp) if size == 4 else None
            sp += size
        elif o in ("mov.l@_ld", "mov.l@disp_ld") and x.m == 15:
            got = fr.get(sp + (x.imm if "disp" in o else 0))
        elif "_st" in o and not o.startswith("f") and (x.n == 15 if "disp" not in o or o[4] == "l" else x.m == 15):
            at = sp + (x.imm if "disp" in o else 0)
            for k in range(at - 3, at + self.SIZE.get(o[4:5], 4)):
                fr.pop(k, None)
            if o in ("mov.l@_st", "mov.l@disp_st") and regs.get(x.m) is not None:
                fr[at] = regs[x.m]
        elif o == "add#" and x.n == 15:
            sp += x.imm
        regs["sp"] = sp
        regs["fr"] = tuple(sorted(fr.items()))
        return got

    @staticmethod
    def mask(v, op, k):
        if v[0] == "c":
            return ("c", (v[1] | k) if op == "or" else (v[1] & k))
        if op == "or" and k & SZ_BIT:
            return ("f", 1)
        if op == "and" and not k & SZ_BIT:
            return ("f", 0)
        return v

    @staticmethod
    def writes(x):
        """Integer registers an instruction may write (n, and m for post-increment loads; r0 for its implicit forms)."""
        o = x.op
        out = set()
        if "@+" in o or o in ("lds.l", "ldc.l", "mac.l", "mac.w"):
            out.add(x.m)                      # post-increment of the address register
        if "@-" in o or o in ("mac.l", "mac.w"):
            out.add(x.n)                      # pre-decrement
        if o in ("mova", "and#", "or#", "xor#") or "gbr_ld" in o or o in ("mov.b@disp_ld", "mov.w@disp_ld"):
            return out | {0}
        if "_st" in o or o.startswith(("cmp/", "tst", "b", "j", "r", "f", "lds", "ldc", "pref", "ocb", "clr", "set",
                                       "nop", "div0u", "trapa", "sleep")):
            return out                        # stores, compares, branches, FPU and system-register loads
        return out | {x.n}


def analyse(img, base, bodies, hand=None, assume_keep=False):
    """{"modes": {entry: sorted modes}, "why": {entry: evidence}, "data": [(caller, site, target)], "ptr1": [(caller,
    site)], "changers": {entry: exit pair}, "keep_unknown": bool} for the functions of bodies ({entry: [(lo, hi)]}) in
    the image bytes img at base. hand: project.json "fpscr_sz_from_caller". assume_keep: a call to an unknown target
    keeps SZ even where a function that changes it is referenced as a pointer (a cross-check, never the fact)."""
    decode = _decoder()
    hand = {int(a, 16) for a in (hand or {})}
    flows = {e: Flow(img, base, e, r, decode) for e, r in bodies.items()}
    loads = {}   # literal-pool word -> the mov.l @(disp,PC) that read it
    for f in flows.values():
        for a, i in f.insns.items():
            if i.op == "mov.l@pc":
                loads.setdefault(i.ea, set()).add(a)
    keep = (0, 1)
    for _ in range(4):    # (each mode's rule only turns unknown, once)
        effects = {}
        for _ in range(40):   # the callees' results at a fixed point, for this rule
            for f in flows.values():
                f.run(effects, keep)
            new = {e: f.exit for e, f in flows.items() if f.exit != (0, 1)}
            if new == effects:
                break
            effects = new
        # a function's address in the image is a pointer to it, except in a pool word every load of which only a
        # call took as its target (jsr @rN after mov.l @(disp,PC),rN)
        used = set().union(*(f.pool_calls for f in flows.values()))
        words = set()
        for o in range(0, len(img) - 3, 2):
            w = struct.unpack_from("<I", img, o)[0]
            if w in flows and not (base + o in loads and loads[base + o] <= used):
                words.add(w)
        # with SZ=0 a pointer call keeps it unless some function a pointer reaches returns another SZ when entered
        # with 0; with SZ=1 it reaches the functions the project fact names (no fact: unknown)
        k0 = 0 if keep[0] == 0 and (assume_keep or not any(
            f in words and x[0] not in (0, -1) for f, x in effects.items())) else None
        ones = {effects.get(h, (0, 1))[1] for h in hand if h in flows} - {-1}
        k1 = 1 if assume_keep else ones.pop() if len(ones) == 1 and keep[1] is not None else None
        if (k0, k1) == keep:
            break
        keep = (k0, k1)
    called = {t for f in flows.values() for t, _ in f.sites[0].values() if t in flows}
    modes = {e: set() for e in flows}
    why = {}
    for e in flows:
        if e not in called or e in words:
            m = 1 if e in hand else 0
            modes[e].add(m)
            if m:
                why[e] = "project.json fpscr_sz_from_caller (called through a pointer with SZ=1)"
    for e in hand:
        if e in modes and e in called:
            modes[e].add(1)
            why.setdefault(e, "project.json fpscr_sz_from_caller")
    changed = True
    while changed:
        changed = False
        for c, f in flows.items():
            for e in sorted(modes[c]):
                for site, (t, sz) in f.sites[e].items():
                    if sz is None or t not in flows or sz in modes[t]:
                        continue
                    if sz:
                        why.setdefault(t, f"{c:08x} calls it at {site:08x} with SZ=1")
                    modes[t].add(sz)
                    changed = True
    data, ptr1, flips = set(), set(), {}
    for c, f in flows.items():
        for e in sorted(modes[c]):
            for site, (t, sz) in f.sites[e].items():
                if sz is not None and effects.get(t, (0, 1))[sz] == 1 - sz:
                    flips.setdefault(c, set()).add(t)   # a call that comes back in the other mode
                if sz is None:
                    data.add((c, site, t))
                elif sz and (t is None or t not in flows):
                    ptr1.add((c, site))
    return {"modes": {e: sorted(m) for e, m in modes.items() if m}, "why": why, "data": sorted(data, key=str),
            "ptr1": sorted(ptr1), "changers": effects, "flips": {c: sorted(t) for c, t in flips.items()}, "keep_unknown": keep,
            "context": sorted(e for e, f in flows.items() if f.context),
            "unreached": sum(f.unreached for f in flows.values())}


def entry_modes(cfg, paths):
    """{addr "8c..": [modes]} for the functions some caller runs with SZ=1 (the others: [0], left out), from the
    project's image and catalog (state/fpscr_sz_entry.json caches it, keyed by its inputs)."""
    bodies_path, binary = paths.FUNCTION_BODIES, cfg.get("binary_path")
    if (paths.PROJECT.get("target") or "dreamcast_sh4") != "dreamcast_sh4" or not bodies_path.exists() or binary is None:
        _memo.clear()   # (an SH-4 fact: another target's prepares get no arg 13 and keep their stamps)
        return {}
    hand = cfg.get("fpscr_sz_from_caller") or {}
    key = hashlib.sha256(f"{VERSION} {cfg.get('binary_sha256')} {json.dumps(hand, sort_keys=True)}\n".encode()
                         + bodies_path.read_bytes()).hexdigest()[:16]
    if _memo.get("key") == key:
        return _memo["modes"]
    cache = paths.STATE / "fpscr_sz_entry.json"
    try:
        got = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    if not got or got.get("key") != key:
        r = analyse(pathlib.Path(binary).read_bytes(), _base(paths), bodies_of(bodies_path.read_text()), hand)
        got = {"key": key, "modes": {f"{e:08x}": m for e, m in sorted(r["modes"].items()) if 1 in m},
               "callees": {f"{c:08x}": {f"{t:08x}": "".join(DIGIT[x] for x in r["changers"][t]) for t in ts}
                           for c, ts in sorted(r["flips"].items())},
               "why": {f"{e:08x}": w for e, w in sorted(r["why"].items()) if 1 in r["modes"].get(e, ())},
               "data_sites": [f"{c:08x}@{s:08x}" for c, s, _ in r["data"]],
               "pointer_sites_sz1": [f"{c:08x}@{s:08x}" for c, s in r["ptr1"]]}
        try:
            cache.write_text(json.dumps(got, indent=1) + "\n", encoding="utf-8")
        except OSError:
            pass
    _memo.update(key=key, modes=got["modes"], callees=got["callees"])
    return got["modes"]


def callees(cfg, paths):
    """{caller "8c..": {callee: its SZ at return entered with 0, then 1 (0, 1, u unknown, n never returns)}} for the
    callers of a function that does not return with SZ as it was entered (the oracle's mocked call returns with it)."""
    entry_modes(cfg, paths)
    return _memo.get("callees", {})


def _base(paths):
    tj = json.loads((paths.TARGET / "target.json").read_text(encoding="utf-8"))
    return int(str(paths.PROJECT.get("image_base") or tj.get("load_address", "0x8c010000")), 16)


def arg(cfg, paths, addr):
    """TcPrepare's script arg 13 (TcFpscr.Sz): "-" for a function only ever called with SZ=0 whose callees all return SZ as
    they found it, else "sz:" its modes ("0", "1", "01"), then ":" and its callees that change SZ with their SZ at
    return ("sz:0:8c18c9ec.10_8c18ca20.01", callees())."""
    a = addr.lower()
    m, c = entry_modes(cfg, paths).get(a) or [0], callees(cfg, paths).get(a)
    if m == [0] and not c:
        return "-"
    return "sz:" + "".join(str(x) for x in m) + (":" + "_".join(f"{t}.{x}" for t, x in sorted(c.items())) if c else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ws", required=True, help="a workspace (folder with project.json and state/)")
    ap.add_argument("--json", default="")
    ap.add_argument("--assume-keep", action="store_true", help="cross-check: unknown targets keep SZ")
    a = ap.parse_args()
    ws = pathlib.Path(a.ws)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    tj = json.loads((pathlib.Path(__file__).resolve().parents[1] / "targets" / proj["target"] / "target.json")
                    .read_text(encoding="utf-8"))
    base = int(str(proj.get("image_base") or tj.get("load_address", "0x8c010000")), 16)
    r = analyse((ws / proj["binary"]).read_bytes(), base,
                bodies_of((ws / "state" / "function_bodies.txt").read_text()), proj.get("fpscr_sz_from_caller"),
                a.assume_keep)
    aff = {e: m for e, m in r["modes"].items() if 1 in m}
    out = {"affected": {f"{e:08x}": {"modes": m, "why": r["why"].get(e, "")} for e, m in sorted(aff.items())},
           "changers": {f"{e:08x}": list(v) for e, v in sorted(r["changers"].items())},
           "callees": {f"{c:08x}": {f"{t:08x}": "".join(DIGIT[x] for x in r["changers"][t]) for t in ts}
                       for c, ts in sorted(r["flips"].items())},
           "keep_unknown": r["keep_unknown"], "unreached_sites": r["unreached"], "context_switches": [f"{e:08x}" for e in r["context"]],
           "data_sites": [f"{c:08x}@{s:08x}->" + (f"{t:08x}" if t is not None else "?") for c, s, t in r["data"]],
           "pointer_sites_sz1": [f"{c:08x}@{s:08x}" for c, s in r["ptr1"]]}
    text = json.dumps(out, indent=1)
    if a.json:
        pathlib.Path(a.json).write_text(text + "\n", encoding="utf-8")
    print(f"affected {len(aff)}, functions that change SZ {len(r['changers'])}, data-dependent sites "
          f"{len(r['data'])}, callers of those {len(r['flips'])}, SZ=1 pointer sites {len(r['ptr1'])}, unknown targets keep SZ: {r['keep_unknown']}, "
          f"context switches {' '.join(f'{e:08x}' for e in r['context'])}; call sites no entry reaches "
          f"{r['unreached']} (left out)")
    for e, v in out["affected"].items():
        print(f"  {e} {v['modes']} {v['why']}")


if __name__ == "__main__":
    main()
