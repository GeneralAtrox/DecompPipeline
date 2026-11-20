"""GameCube PowerPC hooks (core/target_hooks): the check, the agent sandbox and the uninitialised-stack scan, which
core implements for the SH-4. The modules beside this file hold the PowerPC versions. The check is the behaviour
check (ppc_check) with the byte-match check (ppc_match) added when the project configures one."""
import pathlib
import sys

_here = str(pathlib.Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

import ppc_agent  # noqa: E402
import ppc_check  # noqa: E402
import ppc_match  # noqa: E402
import ppc_readable  # noqa: E402


def run_check(cfg, addr, work_file, sets=("dev", "hidden"), build_dir=None, harness_dir=None):
    from core import paths
    res = ppc_check.run_check(cfg, addr, work_file, sets=sets, build_dir=build_dir, harness_dir=harness_dir)
    return ppc_match.attach(cfg, paths.ROOT, addr, work_file, res, build_dir or (paths.BUILD / addr))


def stamp_scope(cfg, addr, scope):
    """core/oracle_stamp: whether a scoped oracle change reaches addr. "unlisted_call_args": TcTrack's CA lines at a
    call no callee record lists reach only a body core/unchecked_args.ppc_in_scope names (the save and restore helpers
    are function entries in the bodies file, so a call of one is no flow out). "save_helper_frame": TcOracle's and
    TcVectors' entry frame (r11, r14..r31, f14..f31, vectors.txt X) reaches only a function that is itself a save or
    restore helper entry (save_helper), their results for the calls after the 16th (TcOracle.lateOf) only one
    that can make a 17th call (late_calls), and the scripted r4 after a callee returning r3:r4 (TcCallFx, vectors.txt
    Q) only one that can call such a callee (r4_calls), and the oracle's last resorts (TcVectors.lastResort, vectors.txt
    K) only one whose harness lost a vector (dropped). Then RETS 64 to 1024 (only a run past 64 calls: late_calls) and
    genretptr results (TcCallFx.grow: only a function whose hints file has the form, retptr_hint). "switch_body":
    TcSwitchBody's jump-table cases outside the body, TcSwitchHits' notes and TcSwitchPass's directed
    vectors reach only a body with a bctr
    (has_bctr). An unknown scope
    reaches every function."""
    if scope == "unlisted_call_args":
        from core import paths, unchecked_args
        return unchecked_args.hook(cfg, addr, paths.FUNCTION_BODIES, 4, True)
    if scope == "save_helper_frame":
        return save_helper(cfg, addr) or late_calls(cfg, addr) or r4_calls(cfg, addr) or dropped(addr) or (
            retptr_hint(addr))
    if scope == "switch_body":
        return has_bctr(cfg, addr)
    return True


def has_bctr(cfg, addr):
    """Whether addr's body (state/function_bodies.txt) holds a bcctr with LK 0 that always branches: the only
    instruction TcSwitchBody reads a table for. True (in scope) for a body not recorded or an image not read."""
    from core import paths, unchecked_args
    try:
        img = pathlib.Path(cfg["binary_path"]).read_bytes()
    except (KeyError, OSError):
        return True
    spans = unchecked_args.bodies(paths.FUNCTION_BODIES).get(str(addr).lower())
    secs = unchecked_args.dol_sections(img) if len(img) >= 0x100 else []
    if not spans or not secs:
        return True
    for lo, hi in spans:
        for a in range(lo, hi, 4):
            o = next((off + a - at for at, off, n in secs if at <= a <= at + n - 4), -1)
            w = int.from_bytes(img[o:o + 4], "big") if 0 <= o <= len(img) - 4 else 0
            if (w & 0xFC0007FF) == 0x4C000420 and (w >> 21) & 0x14 == 0x14:
                return True
    return False


def late_calls(cfg, addr):
    """Whether addr's body can make a 17th mocked call (TcOracle.lateOf, TcVectors.lateRepair: only such a run gets a
    result after the 16th): 17 call sites, or one and a loop (a branch back inside the body, or a computed jump). A
    call site is bl, bcctrl or bclrl, or a b or bc out of the body (a tail call); a bl to a save/restore helper runs
    inline and is none. A superset, read from the DOL and state/function_bodies.txt; True (in scope) for a function
    with no recorded body or an image that cannot be read."""
    from core import paths, unchecked_args
    try:
        img = pathlib.Path(cfg["binary_path"]).read_bytes()
    except (KeyError, OSError):
        return True
    spans = unchecked_args.bodies(paths.FUNCTION_BODIES).get(str(addr).lower())
    secs = unchecked_args.dol_sections(img) if len(img) >= 0x100 else []
    if not spans or not secs:
        return True

    def word(x):
        o = next((off + x - at for at, off, n in secs if at <= x <= at + n - 4), -1)
        return int.from_bytes(img[o:o + 4], "big") if 0 <= o <= len(img) - 4 else None

    def inside(t):
        return any(lo <= t < hi for lo, hi in spans)
    calls, loop = 0, False
    for lo, hi in spans:
        for a in range(lo, hi, 4):
            w = word(a)
            if w is None:
                continue
            op, lk = w >> 26, w & 1
            if op == 19 and (w >> 1) & 0x3FF in (16, 528):  # bclr(l), bcctr(l)
                calls += lk
                loop |= (w >> 1) & 0x3FF == 528 and not lk
                continue
            if op not in (16, 18):
                continue
            d = w & (0x03FFFFFC if op == 18 else 0xFFFC)
            d -= (0x04000000 if d & 0x02000000 else 0) if op == 18 else (0x10000 if d & 0x8000 else 0)
            t = (d + (0 if w & 2 else a)) & 0xFFFFFFFF
            if lk:
                calls += not save_helper(cfg, f"{t:08x}")
            elif not inside(t):
                calls += 1
            elif t <= a:
                loop = True
    return calls >= 17 or (calls > 0 and loop)


def r4_calls(cfg, addr):
    """Whether addr's body can call a callee that returns r3:r4 (TcFlow.returnsR4: TcOracle then scripts r4 after it,
    TcCallFx, vectors.txt Q): an indirect call (bcctrl, bclrl), or a bl or a tail b out of the body to a function whose
    body names r4 in a register field (bits 21..25 or 16..20) or makes a call of its own. A bl to a save/restore helper
    runs inline and is none. A superset, read from the DOL and state/function_bodies.txt; True (in scope) for a body
    that is not recorded or an image that cannot be read."""
    from core import paths, unchecked_args
    try:
        img = pathlib.Path(cfg["binary_path"]).read_bytes()
    except (KeyError, OSError):
        return True
    bodies = unchecked_args.bodies(paths.FUNCTION_BODIES)
    secs = unchecked_args.dol_sections(img) if len(img) >= 0x100 else []
    if not bodies.get(str(addr).lower()) or not secs:
        return True

    def word(x):
        o = next((off + x - at for at, off, n in secs if at <= x <= at + n - 4), -1)
        return int.from_bytes(img[o:o + 4], "big") if 0 <= o <= len(img) - 4 else None

    def calls(spans):  # its call sites' targets, None for an indirect one
        for lo, hi in spans:
            for a in range(lo, hi, 4):
                w = word(a) or 0
                op, lk = w >> 26, w & 1
                if op == 19 and (w >> 1) & 0x3FF in (16, 528) and lk:
                    yield None
                elif op in (16, 18):
                    d = w & (0x03FFFFFC if op == 18 else 0xFFFC)
                    d -= (0x04000000 if d & 0x02000000 else 0) if op == 18 else (0x10000 if d & 0x8000 else 0)
                    t = (d + (0 if w & 2 else a)) & 0xFFFFFFFF
                    if lk and not save_helper(cfg, f"{t:08x}") or not lk and not any(x <= t < y for x, y in spans):
                        yield t

    def sets_r4(t):
        spans = bodies.get(f"{t:08x}")
        if not spans or next(calls(spans), 0) != 0:
            return True
        words = (word(a) for lo, hi in spans for a in range(lo, hi, 4))
        return any(w is None or 4 in ((w >> 21) & 31, (w >> 16) & 31) for w in words)
    return any(t is None or sets_r4(t) for t in calls(bodies[str(addr).lower()]))


def dropped(addr):
    """Whether addr's harness lost a vector (oracle_summary.json "invalid" names a reason): TcVectors.lastResort runs
    only for a run the repairs gave up on, so a function whose harness lost none is made byte for byte as before, and
    one with no summary ran no oracle (none to reach). True (in scope) for a summary that cannot be read."""
    import json
    from core import paths
    f = paths.HARNESS / str(addr).lower() / "oracle_summary.json"
    if not f.exists():
        return False
    try:
        s = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not isinstance(s, dict) or not isinstance(s.get("invalid"), dict) or bool(s["invalid"])


def retptr_hint(addr):
    """Whether addr's hints file has a genretptr line (TcVectors.retPtrHints, TcCallFx.grow). False without one."""
    from core import paths
    f = paths.HINTS / f"{str(addr).lower()}.txt"
    try:
        return any(ln.split()[:1] == ["genretptr"] for ln in f.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def save_helper(cfg, addr):
    """Whether addr is a CodeWarrior save/restore helper entry in the binary (TcFlow.isSaveHelper's rule, as the
    translator's helper_walk reads it from the DOL: only (r11)-based stw/stfd/lwz/lfd/psq_st/psq_l, mtlr r0 and
    mr r1,r11 up to a blr, at most 40 instructions). True (in scope) when the binary cannot be read there."""
    import pathlib
    try:
        img = pathlib.Path(cfg["binary_path"]).read_bytes()
    except (KeyError, OSError):
        return True
    a = int(str(addr), 16)
    w = [int.from_bytes(img[4 * i:4 * i + 4], "big") for i in range(54)] if len(img) >= 0x100 else []
    secs = [(w[18 + k], w[k], w[36 + k]) for k in range(18) if w and w[36 + k]]

    def word(x):
        for base, off, size in secs:
            if base <= x and x + 4 <= base + size and off + (x - base) + 4 <= len(img):
                return int.from_bytes(img[off + x - base:off + x - base + 4], "big")
        return None

    if not secs:
        return True
    for n in range(40):
        i = word(a + 4 * n)
        if i is None:
            return False
        if i == 0x4E800020:  # blr
            return n > 0
        op, ra = i >> 26, (i >> 16) & 31
        if op in (36, 54, 32, 50, 60, 56) and ra == 11:  # stw stfd lwz lfd psq_st psq_l (r11)
            continue
        if i in (0x7C0803A6, 0x7D615B78):  # mtlr r0; mr r1,r11
            continue
        return False
    return False


def build_sandbox(cfg, addr, meta, summary, prev_work, previous_text):
    return ppc_agent.build_sandbox(cfg, addr, meta, summary, prev_work, previous_text)


def uninit_stack(packet, meta):
    """core/stack_uninit reads SH-4 asm; the PowerPC oracle starts every frame zeroed in both oracle and runner, so a
    read-before-write frame word is equal on both sides and needs no report."""
    return []


def readable_check_raw(cfg, addr, work_file, build_dir, sets=("dev", "hidden"), harness_dir=None):
    """Raw C through the readable runtime (core/readable_check.check_raw): the PowerPC readable runtime's own test."""
    return ppc_readable.check_raw(cfg, addr, work_file, build_dir, sets=sets, harness_dir=harness_dir)


def readable_check(cfg, addr, src_file, build_dir, sets=("dev", "hidden"), reg=None, harness_dir=None, **kw):
    """The readable judge (core/readable_check.check_readable): big-endian MEM1 at its own address, the EABI's
    argument placement, the PowerPC call record."""
    return ppc_readable.check_readable(cfg, addr, src_file, build_dir, sets=sets, reg=reg, harness_dir=harness_dir,
                                       **kw)


def readable_sandbox_decls(reg, addr, meta):
    """The readable sandbox's declarations (core/readable_sandbox.sandbox_decls): the callees' raw signatures."""
    import ppc_readable_sandbox
    return ppc_readable_sandbox.sandbox_decls(reg, addr, meta)


def readable_context_md(reg, addr, meta, cfg=None, raw_text="", types_body=None, decls=None, ext=None, views=None):
    """The readable sandbox's context.md (core/readable_context.context_md) with the PowerPC's argument placement."""
    import ppc_readable_sandbox
    return ppc_readable_sandbox.context_md(reg, addr, meta, cfg, raw_text, types_body, decls, ext, views)
