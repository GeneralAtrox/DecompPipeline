"""Calls whose arguments no judge compares: the proof gates' third reason a matched function is not verified.

The judge compares a call's argument registers from the callee's record (meta.json "callees": its regs) or, for a
target no record lists, from the CA line the oracle writes after that call (TcTrack: the registers the original set
since its entry or its last call). A call with neither has no argument compared at all, so wrong arguments pass:
Too Human 82981000's raw C zero-fills r8..r10, f1 and f13 at its call of 0x829bc1c8 (outside the prepared closure),
and a planted `fn_82a48470(1, 0.0)` for `(0, 0.0)` passed 82a4be58 (x360-ref3, 2026-11-07). Until 2026-11-07 the
oracles wrote CA only while the body also had a computed call or jump (and, on the Xbox 360, a tail branch to code in
no function): a direct call or branch to a target that is no prepared function got none.

At a direct call no record lists the judges also compare the registers the callee's own signature takes, whether or
not the caller set them (callee_drift.call_regs, 2026-11-07; a call through a pointer keeps its CA line alone). A
signature is the callee's harness meta.json params, and it counts only once proven: the callee's raw C matched on
that harness (its ledger row "matched"; its vectors vary every argument register, so a register it read but did not
declare would have failed it). 82a4be58 leaves the r3 and f1 it was entered with for 0x82a48470 and sets no
register first (an empty CA line); 82a48470's proven signature is `u32 fn_82a48470(void)` and its raw C reads no
register, so those values reach nothing and `fn_82a48470(1, 0.0)` is an equivalent mutant: it passes, rightly
(x360-ref ws4, 2026-11-07). The forwarded-register defect is 82d88b88: it sets r3 and r4 for 0x82d888b8, whose
proven signature also takes r5, and leaves r5 as it was entered; the original passes 0x40000200 there. Its raw C
passed the old judge and fails now (59/96), and the planted `a.r[2] += 1` is caught at raw and readable.
A function that runs on past its end calls the next address (TcRun pastEnd: a CALL there that no record lists);
that call gets the same judge-time record from the next function's signature. Where no proven signature exists
(no harness, not matched, a parameter the judge cannot place), a direct call or branch out of the body, or a run-on,
is unchecked: a guessed signature never gives a PASS.

A marked function is "unlisted call args unchecked": not verified at raw (proof.scan's state STATE), and its readable
file `unproven` (proof.readable_state). Marks live in the workspace's state/unchecked_args.json, written by
tools/args_marks.py, which re-reads the harnesses each run. A call is checked by a proven callee signature or, a call
through a pointer, by its CA line. "none": a target with no CA line and no proven signature; "unsigned": a direct or
run-on target (direct()) with no proven signature; "rejudge": a proven one whose registers the function's verdict did
not compare (finished before SINCE or before the callee's, and a call's CA line lacks one; a new match lifts it,
or args_marks --lift after a re-judge elsewhere, recorded in the file's "rejudged");
"empty": a CA line that lists no register, recorded but not marking (at a pointer call its arguments are entry
registers no prototype names; the judge has always taken those so). Read-only here, any workspace.

Nearest existing: core/proof.py - the coverage gate and the mutation record live there, but it is at the 400-line
cap; this module holds the third gate and proof.scan / proof.readable_state read it.
"""
import json
import pathlib

from . import filelock
from .callee_drift import index_of, signature_regs, unlisted

REASON = "unlisted call args unchecked"
STATE = "unchecked_args"  # proof.scan's state for a marked function that would otherwise be verified
FILE = "unchecked_args.json"  # in the workspace's state/


def harness_calls(harness_dir, addr):
    """{"none": [targets], "empty": [targets]} of one harness: the original's recorded calls (expected.txt CALL) to a
    target no callee record lists, with no CA line after it ("none") or one that lists no register ("empty"); hex
    strings, sorted. None when the harness has no meta.json or expected.txt."""
    h = pathlib.Path(harness_dir) / addr
    try:
        meta = json.loads(filelock.read_text(h / "meta.json"))
        text = filelock.read_text(h / "expected.txt")
    except (OSError, ValueError):
        return None
    listed = {int(c["addr"] if isinstance(c, dict) else c, 16) for c in meta.get("callees") or []}
    none, empty, pending = set(), set(), None
    for line in text.splitlines():
        ca = line.rstrip() == "CA" or line.startswith("CA ")
        if pending is not None and not ca:
            none.add(pending)
            pending = None
        if line.startswith("CALL "):
            t = int(line.split()[1], 16)
            pending = None if t in listed else t
        elif ca:
            if pending is not None and len(line.split()) == 1:
                empty.add(pending)
            pending = None
    if pending is not None:
        none.add(pending)
    return {"none": [f"{t:08x}" for t in sorted(none)], "empty": [f"{t:08x}" for t in sorted(empty - none)]}


def unchecked(calls):
    """The targets of harness_calls() (or a mark) that keep a function marked: "none", "unsigned" and "rejudge"."""
    c = calls or {}
    return sorted(set(c.get("none") or []) | set(c.get("unsigned") or []) | set(c.get("rejudge") or []))


def read(ws):
    """{addr: mark} of state/unchecked_args.json ({} without one). A mark: {"none", "empty", "unsigned", "why",
    "source"}."""
    p = pathlib.Path(ws) / "state" / FILE
    try:
        return (json.loads(filelock.read_text(p)) if p.exists() else {}).get("functions") or {}
    except ValueError:
        return {}


def rejudged(ws):
    """{addr: {target: time}} of state/unchecked_args.json: when args_marks --lift recorded a re-judge of addr that
    compared target's proven signature (its "rejudge" mark lifted)."""
    p = pathlib.Path(ws) / "state" / FILE
    try:
        return (json.loads(filelock.read_text(p)) if p.exists() else {}).get("rejudged") or {}
    except ValueError:
        return {}


def _few(xs):
    return ", ".join(xs[:4]) + (" ..." if len(xs) > 4 else "")


def note(mark):
    """One line for a marked function: its targets and why their arguments are not compared."""
    m = mark or {}
    parts = [f"no CA line at the call of {_few(m['none'])}"] if m.get("none") else []
    if m.get("unsigned"):
        parts.append(f"no proven signature for the direct call of {_few(m['unsigned'])}")
    if m.get("rejudge"):
        parts.append(f"judged before the signature of {_few(m['rejudge'])} was compared")
    if m.get("empty"):
        parts.append(f"also an empty CA line at the call of {_few(m['empty'])}")
    return f"{REASON}: " + "; ".join(parts) + "; prove the callee (or re-prepare with the CA lines), then rejudge"


# --- Stamp scope "unlisted_call_args" (target.json "stamp_scopes"; the targets' stamp_scope hooks) ---
# The change (TcTrack.of on all three targets, 2026-11-07) writes CA lines at a mocked call to a target no callee record
# lists, also when the body has no computed call or jump. Only such a body can make other bytes: one with a direct
# call or branch out of its spans to an address that is no function's entry (a listed callee's is one), or one that
# runs on past its end. Every other function keeps its stamp. A superset is safe (a needless re-prepare); the image's
# words are the test, so a function's scope never depends on its own harness.


def _out(t, spans):
    return not any(lo <= t < hi for lo, hi in spans)


def _falls(last, hi, spans, ends):
    """A span that runs on past its end hi into code outside the body: a call of the next address (TcRun.pastEnd)
    whose target no reference names, so no callee record lists it."""
    return last is not None and not ends(last) and not any(lo <= hi < h for lo, h in spans)


def sh_scan(word, spans):
    """SH-4 / SH-2 (computed, branch, falls) of a body: word(a) the 16-bit instruction at a, spans [(lo, hi)].
    computed: a jsr, jmp, braf or bsrf (CA was written before); branch: the bra, bsr, bt and bf targets outside the
    spans; falls: each span end the code runs on into."""
    computed, branch = False, set()
    for lo, hi in spans:
        for a in range(lo, hi, 2):
            w = word(a)
            if w is None:
                continue
            if (w & 0xF0FF) in (0x400B, 0x402B, 0x0023, 0x0003):
                computed = True
            elif w & 0xE000 == 0xA000:  # bra, bsr disp12
                d = w & 0xFFF
                branch.add(a + 4 + 2 * (d - 0x1000 if d & 0x800 else d))
            elif w & 0xF900 == 0x8900:  # bt, bf, bt/s, bf/s disp8
                d = w & 0xFF
                branch.add(a + 4 + 2 * (d - 0x100 if d & 0x80 else d))
    falls = {hi for _, hi in spans if _falls(word(hi - 4), hi, spans, lambda w: w in (0x000B, 0x002B)
                                             or w & 0xF000 == 0xA000 or (w & 0xF0FF) in (0x0023, 0x402B))}
    return computed, {t for t in branch if _out(t, spans)}, falls  # ends: rts rte bra braf jmp + slot


def ppc_scan(word, spans):
    """Xbox 360 sh_scan: computed a bcctr(l); branch the b, bl and bc targets; falls past a span not ending in blr,
    bctr or b."""
    computed, branch = False, set()
    for lo, hi in spans:
        for a in range(lo, hi, 4):
            w = word(a)
            if w is None:
                continue
            if w >> 26 == 19 and ((w >> 1) & 0x3FF) == 528:
                computed = True
                continue
            if w >> 26 == 18:  # b, bl
                d = w & 0x03FFFFFC
                t = (d - 0x04000000 if d & 0x02000000 else d) + (0 if w & 2 else a)
            elif w >> 26 == 16:  # bc
                d = w & 0xFFFC
                t = (d - 0x10000 if d & 0x8000 else d) + (0 if w & 2 else a)
            else:
                continue
            branch.add(t & 0xFFFFFFFF)
    falls = {hi for _, hi in spans if _falls(word(hi - 4), hi, spans, lambda w: w in (0x4E800020, 0x4E800420)
                                             or (w >> 26 == 18 and not w & 1))}
    return computed, {t for t in branch if _out(t, spans)}, falls


def _scoped(scan, entries, helpers=()):
    computed, branch, falls = scan
    return not computed and (any(t not in entries and t not in helpers for t in branch) or bool(falls))


def sh_in_scope(word, spans, entries):
    """SH-4 / SH-2: word(a) the 16-bit instruction at a; spans [(lo, hi)] the body; entries the functions' entries."""
    return _scoped(sh_scan(word, spans), entries)


def ppc_in_scope(word, spans, entries, helpers=()):
    """Xbox 360: word(a) the 32-bit instruction at a; helpers the frame helpers' entries (they run inline)."""
    return _scoped(ppc_scan(word, spans), entries, helpers)


def bodies(path):
    """{entry: [(lo, hi)]} of a function_bodies.txt ("entry lo hi" per range), hex keys lower case."""
    spans = {}
    p = pathlib.Path(path)
    for line in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        t = line.split()
        if len(t) >= 3 and not t[0].startswith("#"):
            spans.setdefault(t[0].lower(), []).append((int(t[1], 16), int(t[2], 16)))
    return spans


def state(s, mark):
    """proof.scan's state: STATE for a verified function with a mark, else s."""
    return STATE if s == "verified" and unchecked(mark) else s


def readable(fields, mark):
    """proof.readable_state: a marked function's file is `unproven` too ("args": its targets, "why": the note)."""
    if unchecked(mark):
        u = fields.setdefault("unproven", {"ok": False, "stores": [], "blocks": [], "n": 0, "oracle": "untried"})
        n, prev = note(mark), _strip(u)
        u.update(args=unchecked(mark), args_why=n, why="; ".join(x for x in (prev, n) if x))


_cache = {}


def _scan(cfg, addr, bodies_path, width, big, helpers_path=None):
    """(sh_scan or ppc_scan of addr, entries, frame helpers) over the image cfg["binary_path"] loaded at
    cfg["load_address"] and the bodies file; None for a function with no recorded body."""
    key = (str(bodies_path), str(cfg["binary_path"]), str(helpers_path))
    if key not in _cache:
        b = bodies(bodies_path)
        hp = pathlib.Path(helpers_path) if helpers_path else None
        hel = set((json.loads(hp.read_text(encoding="utf-8")).get("helpers") or {}).values()) if hp and hp.exists() \
            else set()
        _cache[key] = (b, {int(e, 16) for e in b}, pathlib.Path(cfg["binary_path"]).read_bytes(), hel)
    b, entries, image, hel = _cache[key]
    spans = b.get(addr.lower())
    if not spans:
        return None
    base, order = int(str(cfg["load_address"]), 16), "big" if big else "little"

    def word(a):
        o = a - base
        return int.from_bytes(image[o:o + width], order) if 0 <= o <= len(image) - width else None
    return (sh_scan if width == 2 else ppc_scan)(word, spans), entries, hel


def hook(cfg, addr, bodies_path, width, big, helpers_path=None):
    """The targets' stamp_scope for "unlisted_call_args": sh_in_scope (width 2) or ppc_in_scope (4) of addr; True (in
    scope) for a function with no recorded body. helpers_path: a catalog_inputs.json whose "helpers" are the frame
    helpers' entries."""
    r = _scan(cfg, addr, bodies_path, width, big, helpers_path)
    return True if r is None else _scoped(r[0], r[1], r[2] if width == 4 else ())


def direct(cfg, addr, bodies_path, width, big, helpers_path=None):
    """The hex targets addr's body calls or branches to directly outside its spans, or runs on into (frame helpers
    left out); None for a function with no recorded body."""
    r = _scan(cfg, addr, bodies_path, width, big, helpers_path)
    return None if r is None else {f"{t:08x}" for t in (r[0][1] | r[0][2]) - (r[2] if width == 4 else set())}


# --- The marks (tools/args_marks.py): state/unchecked_args.json, the ledger row, the readable row ---
WORDS = {"dreamcast_sh4": (2, False), "saturn_sh2": (2, True), "xbox360_ppc": (4, True)}  # width, big-endian
TOOL = pathlib.Path(__file__).resolve().parents[1]


def _cfg(ws):
    pj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    tj = json.loads((TOOL / "targets" / pj["target"] / "target.json").read_text(encoding="utf-8"))
    return pj, {"binary_path": (ws / pj["binary"]).resolve(),
                "load_address": pj.get("load_address") or tj["load_address"]}


SINCE = "2026-11-07T05:24:00"  # call_regs in the judges: a verdict finished before it compared CA registers only


def gate(ws, a, hc, rows=None, rj=None):
    """harness_calls(a) with the callees' proven signatures. A target a's body calls, branches or runs on to directly
    (direct()): "unsigned" without a proven signature; with one, checked when every call's CA line lists its
    registers (the judge before call_regs compared those), or a's verdict
    finished after SINCE and after the callee's (rows: the ledger; rj: rejudged(), a re-judge args_marks --lift
    recorded); else "rejudge". Any other target (a call through a pointer) is "none" without a CA line."""
    pj, cfg = _cfg(ws)
    width, big = WORDS[pj["target"]]
    calls = unlisted(ws / "harness" / a)
    if calls is None:
        return hc
    try:
        d = direct(cfg, a, ws / "state" / "function_bodies.txt", width, big, ws / "catalog_inputs.json")
    except OSError:  # no image here
        d = None
    d = set(calls) if d is None else d  # no recorded body or image: every target may be a direct one
    idx = index_of(pj["target"])
    rows = rows if rows is not None else json.loads(filelock.read_text(ws / "state" / "ledger.json"))["functions"]
    fin = lambda x: (rows.get(x) or {}).get("finished") or ""  # noqa: E731
    sigs = {t: signature_regs(t, idx, ws) for t in set(calls) & d}
    proven = {t for t, s in sigs.items() if s is not None}
    rejudge = sorted(t for t in proven if not all(c is not None and set(sigs[t]) <= set(c) for c in calls[t])
                     and not max(fin(a), (rj or {}).get(a, {}).get(t, "")) >= max(SINCE, fin(t)))
    none = [t for t in hc["none"] if t not in d]  # a call through a pointer: its CA line alone, as before
    return dict(hc, none=none, unsigned=sorted(set(sigs) - proven), rejudge=rejudge)


def static_scope(ws, addrs):
    """The addrs the stamp scope unlisted_call_args names (hook() over the workspace's image and bodies)."""
    pj, cfg = _cfg(ws)
    width, big = WORDS[pj["target"]]
    return [a for a in addrs
            if hook(cfg, a, ws / "state" / "function_bodies.txt", width, big, ws / "catalog_inputs.json")]


def marks(ws, addrs=None, from_file=None, scope=False):
    """(old marks, new marks) of a workspace: from its harnesses (addrs, the static scope of them or of every matched
    function, plus the marked ones; a function without a harness keeps its mark) or from from_file's "addr target ..."
    lines (added to the marks)."""
    old, rj = read(ws), rejudged(ws)
    new = {a: dict(m) for a, m in old.items()}
    if from_file:
        for line in pathlib.Path(from_file).read_text(encoding="utf-8").splitlines():
            t = line.split("#")[0].split()
            if t:
                new[t[0].lower()] = {"none": sorted(x.lower() for x in t[1:]), "empty": [],
                                     "source": f"file {pathlib.Path(from_file).name}"}
    else:
        led = json.loads(filelock.read_text(ws / "state" / "ledger.json")).get("functions") or {}
        todo = addrs if addrs is not None else [a for a, r in led.items() if r.get("status") == "matched"]
        for a in sorted(set(static_scope(ws, todo) if scope else todo) | set(old)):
            hc = harness_calls(ws / "harness", a)
            hc = gate(ws, a, hc, led, rj) if hc is not None else None
            if hc is not None and unchecked(hc):
                new[a] = dict(hc, source="harness")
            elif hc is not None:
                new.pop(a, None)
    for m in new.values():
        m["why"] = note(m)
    return old, new


def _strip(u):
    """An unproven's "why" without this gate's note (args_why)."""
    why, n = (u or {}).get("why") or "", (u or {}).get("args_why")
    return "; ".join(x for x in (why.replace(n, "") if n else why).split("; ") if x.strip())


def mark_ledger(rows, new, moves, since):
    """The ledger rows: a marked matched row gets "unproven" {reason, targets, since, source} (the ledger-truth gate's
    shape for another gate's mark), unless another gate's "unproven" is there; an unmarked row loses this gate's."""
    for a, row in rows.items():
        mine = (row.get("unproven") or {}).get("reason") == REASON
        if a in new and row.get("status") == "matched" and (mine or not row.get("unproven")):
            if not mine and not row.get("unverified") and (row.get("proof") or {}).get("state") in (None, "verified"):
                moves["raw"].append(a)
            row["unproven"] = {"reason": REASON, "targets": unchecked(new[a]), "since": since,
                               "source": "core/unchecked_args"}
        elif a not in new and mine:
            row.pop("unproven")
            moves["raw_lifted"].append(a)


def mark_readable(rows, new, moves, state_note):
    """The readable rows: a marked `readable` one turns `unproven` ("args" in its unproven); an unmarked one with
    "args" loses them, and turns `readable` again when they were its only reason. state_note: core/proof's."""
    for a, row in rows.items():
        u = row.get("unproven") if isinstance(row.get("unproven"), dict) else None
        if a in new and row.get("status") in ("readable", "unproven", "needs_review"):
            if row["status"] == "readable":
                moves["readable"].append(a)
                row["status"] = "unproven"
            fields = {"unproven": dict(u)} if u else {}
            readable(fields, new[a])
            row.update(fields, note=state_note(dict(fields, review=row.get("review")))[:400])
        elif a not in new and (u or {}).get("args"):
            u["why"] = _strip(u)
            u.pop("args")
            u.pop("args_why", None)
            if not u["why"]:
                u.pop("why")
            if not u.get("stores") and not u.get("why"):
                row.pop("unproven")
            fields = {k: row[k] for k in ("unproven", "review") if row.get(k)}
            row.pop("note", None)
            if fields:
                row["note"] = state_note(fields)[:400]
            elif row.get("status") == "unproven":
                row["status"] = "readable"
            moves["readable_lifted"].append(a)
