"""The readable judge's per-function opt-ins (project.json "readable_judge"): a tester artifact named for one function,
with its evidence, removed for exactly that function. Nothing is applied to a function the listing does not name, and
raw checks (readable_check.check_raw, which passes no reads) never see any of it.

  "readable_judge": {
    "8c020b30": {"exit_unread": {"regs": ["r4", "r7"], "tail_callers": ["8c01c7fa"], "why": "..."}},
    "8c0615e0": {"r0_kept": {"callee": "8c061498", "why": "..."}},
    "8c05ed5c": {"drop_vectors": {"ids": [97], "known_callees": true, "why": "..."}},
    "8c097186": {"image_base": {"base": "0x5b000000", "why": "..."}},
    "8c05ed5c": {"out_targets": {"targets": ["8c05e374", "8c05d768"], "why": "..."}}}

- exit_unread: r4..r7 at exit (meta r_exports, TcOut) not compared. TcOut counts a register as read after a call when
  its walk from the call's return address finds no instruction, which is where a tail jump (`jmp`/`bra` to the
  function) leaves: past the caller's code. There the function's r4..r7 become the tail caller's own at its exit, so
  they are read only if the tail caller exports them. Applied only while every listed tail caller's own harness exists
  and exports none of regs (its meta r_exports): what the judge itself compares for that caller.
- r0_kept: the callee never writes r0, so after a call to it r0 is what it was at that call. A mock hands back a drawn
  r0, and the original's next `jsr @r0` reaches that drawn value instead of the callee. Applied only to a call through
  r0 (its target is its own r0) right after a call to callee that was itself through r0: its target becomes that r0.
- drop_vectors: those vectors are not judged. Applied only to a vector in which the original calls a target outside
  the code ranges (state/code_ranges.txt): a function pointer into data, which the console never runs. With
  "known_callees": true, also to a vector whose original calls a code address that is none of the function's callees
  (its meta callee records and its out_targets): a table index past the table's end, which reads the word beyond it.
- image_base: the readable runner is linked at this base (readable_check.link_flags). The runtime reports a word equal
  to the host address of the function under test as its guest address (rd_veh.c rd_self_guest), so a vector whose
  value equals that host address is misreported; another base moves the function away from it.
- out_targets: functions the original calls only through a function pointer (a table's entry), so its harness lists
  no callee record for them. The out-pointer delivery (readable_outptr) reads the callee records, so each listed
  function that has its own harness gets one for that step alone: the bytes the original's stack holds at its
  `T *` out parameter reach the readable local, as for a direct call (the compared meta is unchanged). Nothing else
  moves: the calls are judged as before.

A malformed entry is refused (ValueError), never silently ignored. ENABLED False gives the judge without any opt-in
(the mutation A/B's OLD side).

Nearest existing: readable_ca (narrows CA calls for every function by prototype) and compare.run_flag (a per-function
project.json listing with evidence) - neither covers exit registers, a mock's r0, impossible vectors or the image base,
and readable_check.py is over 400 lines, so it may not grow."""
import re

from . import asm_pool, compare as check, paths

ENABLED = True
FORMS = ("exit_unread", "r0_kept", "drop_vectors", "image_base", "out_targets")
_ADDR = re.compile(r"[0-9a-f]{8}")
_ARG = re.compile(r"r[0-7]|fr([4-9]|1[01])")  # readable_check.ARG_REG
R0 = check.REG_INDEX["r0"]


def entry(cfg, addr):
    """The checked opt-ins of the function at addr: {form: entry}, {} when none (or not ENABLED)."""
    if not ENABLED or not addr:
        return {}
    listing = cfg.get("readable_judge") or {}
    if not isinstance(listing, dict):
        raise ValueError('project.json "readable_judge" must map each function address to its opt-ins')
    e = listing.get(addr) or {}
    for form, v in e.items():
        if form not in FORMS:
            raise ValueError(f'project.json "readable_judge" {addr}: unknown opt-in {form!r} (known: {", ".join(FORMS)})')
        if not isinstance(v, dict) or not str(v.get("why", "")).strip():
            raise ValueError(f'project.json "readable_judge" {addr} {form}: no evidence ("why")')
    x = e.get("exit_unread")
    if x and (not x.get("regs") or not set(x["regs"]) <= {"r4", "r5", "r6", "r7"} or not x.get("tail_callers")
              or not all(_ADDR.fullmatch(str(c)) for c in x["tail_callers"])):
        raise ValueError(f'project.json "readable_judge" {addr} exit_unread: "regs" (r4..r7) and "tail_callers" '
                         "(8 hex digits) are both needed")
    if "r0_kept" in e and not _ADDR.fullmatch(str(e["r0_kept"].get("callee", ""))):
        raise ValueError(f'project.json "readable_judge" {addr} r0_kept: "callee" must be 8 hex digits')
    if "drop_vectors" in e and not (e["drop_vectors"].get("ids") and all(isinstance(i, int) for i in
                                                                          e["drop_vectors"]["ids"])):
        raise ValueError(f'project.json "readable_judge" {addr} drop_vectors: "ids" must list vector numbers')
    if "known_callees" in e.get("drop_vectors", {}) and not isinstance(e["drop_vectors"]["known_callees"], bool):
        raise ValueError(f'project.json "readable_judge" {addr} drop_vectors: "known_callees" must be true or false')
    if "image_base" in e and not re.fullmatch(r"0x[0-9a-f]{4}0000", str(e["image_base"].get("base", ""))):
        raise ValueError(f'project.json "readable_judge" {addr} image_base: "base" must be 0x....0000')
    if "out_targets" in e and not (e["out_targets"].get("targets") and all(
            _ADDR.fullmatch(str(a)) for a in e["out_targets"]["targets"])):
        raise ValueError(f'project.json "readable_judge" {addr} out_targets: "targets" must list 8-hex-digit addresses')
    return e


def out_meta(cfg, meta):
    """meta with a callee record (address, argument registers, signature: its own harness's) added for each
    out_targets function of meta's function that the harness does not list, for the out-pointer delivery only;
    meta itself when there is none (or the target has no harness)."""
    x = entry(cfg, meta.get("addr")).get("out_targets")
    if not x:
        return meta
    have, extra = {c["addr"] for c in meta["callees"]}, []
    for a in x["targets"]:
        m = paths.read_json(paths.HARNESS / a / "meta.json", {})
        if a not in have and m.get("signature"):
            regs = [q["storage"] for q in m.get("params") or () if _ARG.fullmatch(str(q.get("storage")))]
            extra.append({"addr": a, "regs": regs, "signature": m["signature"]})
    return dict(meta, callees=meta["callees"] + extra) if extra else meta


def build_cfg(cfg, addr):
    """cfg with the function's own readable runner base (image_base), else cfg itself."""
    e = entry(cfg, addr).get("image_base")
    return dict(cfg, readable_image_base=e["base"]) if e else cfg


def _exports_unread(meta, x):
    """meta without the regs of x, while every tail caller's own harness exports none of them; else meta."""
    for c in x["tail_callers"]:
        m = paths.read_json(paths.HARNESS / c / "meta.json", {})
        if not m or set(m.get("r_exports") or ()) & set(x["regs"]):
            return meta
    return dict(meta, r_exports=[r for r in meta.get("r_exports") or () if r not in x["regs"]])


def _r0_kept(exp, callee):
    for run in exp.values():
        c = run["calls"]
        for k in range(1, len(c)):
            if c[k - 1][0] == callee and c[k - 1][R0] == callee and c[k][0] == c[k][R0] != callee:
                c[k][0] = c[k][R0] = callee
    return exp


def _drops(exp, ids, known=None):
    """The ids whose run calls a target outside the code ranges (an empty code-range file: none), or, with known (a
    set of addresses), a code address not in it."""
    ranges = asm_pool.code_ranges(paths.CODE_RANGES)
    code = lambda t: any(lo <= t < hi for lo, hi in ranges)  # noqa: E731
    bad = lambda t: not code(t) or (known is not None and t not in known)  # noqa: E731
    return {i for i in ids if i in exp and ranges and any(bad(c[0]) for c in exp[i]["calls"])}


def judged(cfg, meta, reads, vecs, exp):
    """(meta, vecs, exp) as the readable judge compares them for meta's function: unchanged without an opt-in, or for
    a raw check (reads None)."""
    e = entry(cfg, meta.get("addr")) if reads is not None else {}
    if "exit_unread" in e:
        meta = _exports_unread(meta, e["exit_unread"])
    if "r0_kept" in e:
        exp = _r0_kept(exp, int(e["r0_kept"]["callee"], 16))
    if "drop_vectors" in e:
        known = None
        if e["drop_vectors"].get("known_callees"):
            known = {int(c["addr"], 16) for c in meta["callees"]} | {int(a, 16) for a in
                                                                    (e.get("out_targets") or {}).get("targets", ())}
        gone = _drops(exp, e["drop_vectors"]["ids"], known)
        vecs = {i: v for i, v in vecs.items() if i not in gone}
    return meta, vecs, exp
