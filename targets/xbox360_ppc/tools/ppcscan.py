"""Offline instruction scan of a loaded Xbox 360 image (xex.py): the catalog inputs, without Ghidra.

Decoding is DecompPipeline's own, by primary and extended opcode (IBM PowerPC Architecture Book I-III; Xenon's
VMX128 opcodes 4, 5 and 6), and only as far as the catalog needs: what class each instruction is in, and where
branches go. It gives
- the function entries: every `.pdata` function (begin, length), every `bl` target outside them (leaf functions
  without unwind data) and every data word that points at a code address no function covers yet; an added entry
  runs to the next known entry or the end of its section;
- the register save/restore helpers the compiler calls from prologues and epilogues (`__savegprlr_N`,
  `__restgprlr_N`, `__savefpr_N`, `__restfpr_N`, `__savevmx_N`, `__restvmx_N`), found by their encodings;
- per function: instruction counts by class, direct callees, tail branches, indirect calls, jump tables, and the
  phase 1 refusal (DESIGN.md "Phase 1"): VMX/VMX128, FPSCR writes, estimates, atomics, cache and system
  instructions are refused before any Ghidra time is spent on them.

Ghidra's PowerPC languages decode no VMX128 at all, and some VMX128 words in opcode 4 decode there as other AltiVec
instructions; this scan, not Ghidra's mnemonics, decides which functions use vectors.
"""
import array
import bisect
import sys

# Extended opcodes (bits 21-30) of primary opcode 31 by class.
VMX31 = {6, 7, 38, 39, 71, 103, 135, 167, 199, 231, 359, 487, 519, 551, 647, 679, 775, 807, 903, 935,
         342, 374, 822}                                      # vector loads/stores, lvsl/lvsr, data stream touch
FLOAD31 = {535, 567, 599, 631}                               # lfsx lfsux lfdx lfdux
FSTORE31 = {663, 695, 727, 759, 983}                         # stfsx stfsux stfdx stfdux stfiwx
ATOMIC31 = {20, 84, 150, 214}                                # lwarx ldarx stwcx. stdcx.
CACHE31 = {1014, 86, 54, 470, 982}                           # dcbz/dcbz128 dcbf dcbst dcbi icbi
HINT31 = {278, 246}                                          # dcbt dcbtst (no effect on memory)
BARRIER31 = {598, 854}                                       # sync/lwsync eieio
SYS31 = {83, 146, 178, 371, 306, 566, 402, 434, 498, 915, 851}  # msr, mftb, tlb, slb
TRAP31 = {4, 68}                                             # tw td
FPMODE63 = {38, 70, 134, 583, 711, 64}                       # mtfsb1 mtfsb0 mtfsfi mffs mtfsf mcrfs
FCONV63 = {14, 15, 814, 815, 846}                            # fctiw fctiwz fctid fctidz fcfid
SPR_PLAIN = {1, 8, 9}                                        # XER LR CTR
SPR_VRSAVE = 256

CLASSES = ("vmx", "fpu", "fma", "estimate", "fconv", "fp_mode", "atomic", "cache", "barrier", "sys", "trap",
           "ld64", "r13")

# Phase 1 refusals, in the order they are reported (DESIGN.md).
REFUSE = (("vmx", "unsupported: VMX"), ("fp_mode", "unsupported: FPSCR"), ("estimate", "unsupported: estimate"),
          ("atomic", "unsupported: atomic"), ("cache", "unsupported: cache"), ("sys", "unsupported: system"))


def classify(w):
    """The classes of one instruction word (a tuple of CLASSES names, usually empty)."""
    op = w >> 26
    if op in (4, 5, 6):
        return ("vmx",)
    if op in (2, 3):
        return ("trap",)
    if op == 17:
        return ("sys",)
    if 48 <= op <= 55:
        return ("fpu",)
    if op == 58 or op == 62:
        return ("ld64",)
    if op == 59 or op == 63:
        xo5, xo10 = (w >> 1) & 0x1F, (w >> 1) & 0x3FF
        if op == 63 and xo10 in FPMODE63:
            return ("fpu", "fp_mode")
        if xo5 in (28, 29, 30, 31):
            return ("fpu", "fma")
        if (op == 59 and xo5 == 24) or (op == 63 and xo5 == 26):
            return ("fpu", "estimate")
        if op == 63 and xo10 in FCONV63:
            return ("fpu", "fconv")
        return ("fpu",)
    if op == 19:
        xo10 = (w >> 1) & 0x3FF
        return ("sys",) if xo10 in (18, 274) else ("barrier",) if xo10 == 150 else ()
    if op == 31:
        xo10 = (w >> 1) & 0x3FF
        if xo10 in VMX31:
            return ("vmx",)
        if xo10 in FLOAD31 or xo10 in FSTORE31:
            return ("fpu",)
        if xo10 in ATOMIC31:
            return ("atomic",)
        if xo10 in CACHE31:
            return ("cache",)
        if xo10 in BARRIER31:
            return ("barrier",)
        if xo10 in TRAP31:
            return ("trap",)
        if xo10 in (339, 467):  # mfspr / mtspr
            spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
            if spr in SPR_PLAIN:
                return ()
            return ("vmx",) if spr == SPR_VRSAVE else ("sys",)
        if xo10 in SYS31:
            return ("sys",)
    return ()


def uses_r13(w):
    """A D-form load/store or addi whose base register is r13 (the processor control block: TLS, thread)."""
    op = w >> 26
    return ((w >> 16) & 31) == 13 and (32 <= op <= 55 or op in (14, 58, 62))


def branch_target(w, addr):
    """(target, link) of an I-form branch (b, bl, ba, bla), else None."""
    if w >> 26 != 18:
        return None
    li = w & 0x03FFFFFC
    if li & 0x02000000:
        li -= 0x04000000
    return ((0 if w & 2 else addr) + li) & 0xFFFFFFFF, bool(w & 1)


def is_bcctr(w):
    return w >> 26 == 19 and ((w >> 1) & 0x3FF) == 528


def is_blr(w):
    return w == 0x4E800020


# -- the image --------------------------------------------------------------------------------------------------

class Image:
    def __init__(self, img, desc):
        self.img, self.desc, self.base = img, desc, desc["load_address"]
        self.words = array.array("I", img[:len(img) - len(img) % 4])
        if sys.byteorder == "little":
            self.words.byteswap()
        loaded = [s for s in desc["sections"] if s.get("loaded", True)]
        self.code = [(s["start"], s["raw_end"]) for s in loaded if s["execute"]]
        self.data = [(s["start"], s["raw_end"]) for s in loaded if not s["execute"]]

    def word(self, a):
        return self.words[(a - self.base) >> 2]

    def in_code(self, a):
        return any(lo <= a < hi for lo, hi in self.code) and not a & 3

    def code_section(self, a):
        return next(((lo, hi) for lo, hi in self.code if lo <= a < hi), None)


def find_helpers(im):
    """{name_N: address} of the save/restore helpers, found by their encodings. Each family is a run of one
    instruction (or li + one instruction) per register; the helper for register N is that run's Nth entry."""
    def enc_d(op, rt, ra, d):
        return (op << 26) | (rt << 21) | (ra << 16) | (d & 0xFFFF)

    families = {
        "savegprlr": ([enc_d(62, 14, 1, -0x98)], 14, 31, 4),   # std r14,-0x98(r1)
        "restgprlr": ([enc_d(58, 14, 1, -0x98)], 14, 31, 4),   # ld r14,-0x98(r1)
        "savefpr": ([enc_d(54, 14, 12, -0x90)], 14, 31, 4),    # stfd f14,-0x90(r12)
        "restfpr": ([enc_d(50, 14, 12, -0x90)], 14, 31, 4),    # lfd f14,-0x90(r12)
        "savevmx": ([enc_d(14, 11, 0, -0x120), 0x7DCB61CE], 14, 31, 8),   # li r11,-0x120; stvx v14,r11,r12
        "restvmx": ([enc_d(14, 11, 0, -0x120), 0x7DCB60CE], 14, 31, 8),   # li r11,-0x120; lvx v14,r11,r12
        "savevmx128": ([enc_d(14, 11, 0, -0x400), 0x100B61CB], 64, 127, 8),  # li r11,-0x400; stvx128 v64,r11,r12
        "restvmx128": ([enc_d(14, 11, 0, -0x400), 0x100B60CB], 64, 127, 8),  # li r11,-0x400; lvx128 v64,r11,r12
    }
    found, ranges = {}, []
    w = im.words
    for name, (pattern, first, last, step) in families.items():
        hits = []
        for lo, hi in im.code:
            i, end = (lo - im.base) >> 2, (hi - im.base) >> 2
            while True:
                try:
                    i = w.index(pattern[0], i, end)
                except ValueError:
                    break
                if all(w[i + k] == p for k, p in enumerate(pattern)):
                    hits.append(im.base + 4 * i)
                i += 1
        if len(hits) == 1:
            start = hits[0]
            for n in range(first, last + 1):
                found[f"{name}_{n}"] = start + step * (n - first)
            end = start + step * (last - first + 1)
            while w[(end - im.base) >> 2] != 0x4E800020:  # the run's tail (stw/lwz r12, mtlr) up to its blr
                end += 4
            ranges.append((name, start, end + 4))
        elif hits:
            found[f"{name}_ambiguous"] = hits
    return found, ranges


def scan(img, desc):
    """The catalog inputs (see the module doc) as a dict."""
    im = Image(img, desc)
    helpers, helper_ranges = find_helpers(im)
    helper_lo = {lo: (name, lo, hi) for name, lo, hi in helper_ranges}
    thunks = {e["thunk"]: e for e in desc["imports"] if e["thunk"]}
    funcs = {}
    for begin, size, _prolog in desc["pdata"]:
        funcs[begin] = {"size": size, "source": "pdata"}
    starts = sorted(funcs)

    def covered(a):
        i = bisect.bisect_right(starts, a) - 1
        return i >= 0 and a < starts[i] + funcs[starts[i]]["size"]

    def in_helper(a):
        return any(lo <= a < hi for _, lo, hi in helper_ranges)

    def in_thunk(a):
        i = bisect.bisect_right(thunk_starts, a) - 1
        return i >= 0 and a < thunk_starts[i] + 16

    thunk_starts = sorted(thunks)
    # bl targets and data pointers that no .pdata function covers become entries.
    bl_targets = set()
    for lo, hi in im.code:
        for a in range(lo, hi, 4):
            w = im.word(a)
            if w >> 26 == 18 and w & 1:
                t = branch_target(w, a)[0]
                if im.in_code(t):
                    bl_targets.add(t)
    added = {}
    for t in sorted(bl_targets):
        if not covered(t) and not in_helper(t) and not in_thunk(t):
            added[t] = "bl"
    ptrs = set()
    for lo, hi in im.data:
        for a in range(lo & ~3, hi - 3, 4):
            v = im.word(a)
            if im.in_code(v):
                ptrs.add(v)
    for t in sorted(ptrs):
        if t not in added and not covered(t) and not in_helper(t) and not in_thunk(t) and im.word(t) != 0:
            added[t] = "pointer"
    # An added entry runs to the next entry, helper, thunk or the end of its code section.
    bounds = sorted(set(starts) | set(added) | set(helper_lo) | set(thunks) | {hi for _, hi in im.code})
    for t, src in added.items():
        i = bisect.bisect_right(bounds, t)
        end = bounds[i] if i < len(bounds) else im.code_section(t)[1]
        end = min(end, im.code_section(t)[1])
        while end > t + 4 and im.word(end - 4) == 0:  # alignment padding is not the function's
            end -= 4
        funcs[t] = {"size": end - t, "source": src}
    out = []
    for begin in sorted(funcs):
        f = funcs[begin]
        feat = features(im, begin, f["size"], f["source"], helpers, thunks)
        if in_helper(begin):  # the helpers have .pdata entries, but they are part of their callers' frames
            feat["kind"], feat["refuse"] = "frame_helper", "frame helper: runs inline in its callers"
        out.append(feat)
    mids = sorted(t for t in bl_targets if t not in funcs and covered(t) and not in_helper(t))
    return {
        "image": {k: desc[k] for k in ("original_pe_name", "load_address", "image_size", "entry_point",
                                        "image_sha1")},
        "helpers": helpers,
        "helper_ranges": [[n, lo, hi] for n, lo, hi in helper_ranges],
        "imports": desc["imports"],
        "functions": out,
        "mid_entries": mids,
        "stats": stats(out, mids),
    }


def features(im, begin, size, source, helpers, thunks):
    helper_addrs = set(helpers.values()) if helpers else set()
    counts = dict.fromkeys(CLASSES, 0)
    callees, tails, imports, frame_helpers = [], [], [], []
    insns = indirect = tables = 0
    end = begin + size
    for a in range(begin, end, 4):
        w = im.word(a)
        insns += 1
        for c in classify(w):
            counts[c] += 1
        if uses_r13(w):
            counts["r13"] += 1
        bt = branch_target(w, a)
        if bt:
            t, link = bt
            if t in thunks:
                imports.append(t)
            elif t in helper_addrs:
                frame_helpers.append(t)
            elif link:
                callees.append(t)
            elif not begin <= t < end:
                tails.append(t)
        elif is_bcctr(w):
            if w & 1:
                indirect += 1
            else:
                tables += 1
    refuse = next((why for c, why in REFUSE if counts[c]), None)
    return {"addr": f"{begin:08x}", "size": size, "source": source, "kind": "function", "insns": insns,
            "counts": {k: v for k, v in counts.items() if v}, "callees": sorted({f"{t:08x}" for t in callees}),
            "tail_calls": sorted({f"{t:08x}" for t in tails}), "imports": sorted({f"{t:08x}" for t in imports}),
            "frame_helpers": len(frame_helpers), "indirect_calls": indirect, "jump_tables": tables,
            "refuse": refuse}


def stats(funcs, mids):
    by_source, refused = {}, {}
    for f in funcs:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1
        if f["refuse"]:
            refused[f["refuse"]] = refused.get(f["refuse"], 0) + 1
    ok = [f for f in funcs if not f["refuse"]]
    return {"functions": len(funcs), "by_source": by_source, "refused": refused, "phase1": len(ok),
            "phase1_insns": sum(f["insns"] for f in ok), "with_fma": sum(1 for f in ok if "fma" in f["counts"]),
            "mid_entries": len(mids)}
