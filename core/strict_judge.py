"""The stricter judge: four target.json flags, each off by default (absent or false), that make the oracle and the
runtimes treat a mocked call less kindly and the judge compare more. With all four off nothing here adds a byte to
the facts file, a harness or a check result.

  mock_junk       the mocked callee leaves junk in the scratch registers it does not return in (the oracle's
                  TcStrict.junk; the runtimes junk the ones they model). C that reads one back after a call only
                  matches by chance. No report mode: it changes the harness, not the comparison. A compared argument
                  the function passes on with a callee's junk still in it may also be the value it had without junk
                  (expected.txt "CJ call field hex": the SH-4's value before the junk, the GameCube's clobber 0): no
                  junk-free run tells C that passes that value from C that leaves the register (alternative()).
  mock_writes     a callee known to write through a pointer argument does, at each mocked call, on both sides: a
                  TcJunk byte stream the size the fact says. The facts are project.json "mock_write_facts" (below):
                  no fact source in the pipeline says a callee writes (callee signatures carry no pointer types,
                  TcSum covers registers only), so each callee is listed by hand with its evidence. No report mode.
  call_snapshots  per pointer argument, the bytes this run stored at [p, p + extent) before the call (expected.txt
                  "CS call field hex", "xx" where the original stored nothing; the runtimes print the same line from
                  the vectors.txt "W" request), compared only for an argument the callee is compared on (meta
                  callees "regs") and only on the stored bytes; a stored word NaN on both sides matches, as in the
                  memory comparison (the emulator's NaN bits are not the hardware's: Spawn 8c056460 stores the
                  oracle's 0x7fc00000 where the C's is 0xffffffff).
  exit_frame      the stack pointer and the callee-saved registers at the exit (expected.txt "XR": SH r15, r8..r14,
                  fr12..fr15; PowerPC r1, r14..r31, f14..f31), for a run that returned. The stack pointer is only
                  ever reported, never enforced: the C keeps none (the runtimes report the entry one), so a moved one
                  is the original's convention, which no C can follow (Toy Commander 8c061ad4 pushes fr12 for the
                  callee it tail-calls, 8c061630, which pops it; Grandia 0606cb1a pops its caller's push of r2).

config.json (or config.local.json, project.json) "strict_judge": {"call_snapshots": "report", "exit_frame":
"report"} makes either comparison report-only, with no new harness: its differences then go to the check result's
"strict_report" and do not fail the verdict ("enforce", the default, fails it).

project.json "mock_write_facts": {"<callee hex>": {"<field>": size, ..., "why": "<evidence>"}}: field one of the
target's integer argument registers (abi int_args) or a stack word s0..s7, size 1..256 bytes, why required (the
callee's code or a manual that shows the write). The facts file gets "mock_write callee field size" lines only while
mock_writes is on, so an unused list leaves the oracle stamp unchanged.

A field's size can instead be an advance (Dreamcast only: little-endian words, the shared TcStrict and tc_strict.h):
{"advance": {"align": A, "op": B, "operands": {"<op hex>": [a, s], ...}}}. The callee is a script operand reader that
moves a cursor: the 4-byte word at the pointer is a guest address pc, and at each mocked call, on both sides, pc =
up(pc, A); op = the B-byte word at pc; pc += B; and for an op listed, pc = up(pc, a) + s (an op not listed adds
nothing more); the word is then written (Toy Commander 8c048914 Script_ReadIntConst: align 2, op 2, c2 [1, 1], c3
[2, 2], c4 [4, 4]). Nothing is written when the word or the op bytes are not in RAM (TcBase.inRam), or the word is an
instruction's. Its facts token is "aA.B:op.a.s:..." (ops in order); a call snapshot of that field takes 4 bytes.
"""
import struct

FLAGS = {"mock_junk": 1, "mock_writes": 2, "call_snapshots": 4, "exit_frame": 8}
REPORTABLE = ("call_snapshots", "exit_frame")
MODES = ("enforce", "report")
MAX_SIZE = 256  # tc_strict.h TCS_MAX_SIZE
MAX_OPS = 16    # an advance's listed ops (tc_strict.h TCS_MAX_OPS)
# The exit frame's registers in XR order, and how each compares: a word (the mirror rule's same_word), f32 or f64.
XR_SH = [("r15", "w")] + [(f"r{i}", "w") for i in range(8, 15)] + [(f"fr{i}", "f32") for i in range(12, 16)]
XR_PPC = [("r1", "w")] + [(f"r{i}", "w") for i in range(14, 32)] + [(f"f{i}", "f64") for i in range(14, 32)]


def on(cfg, flag):
    """Whether a flag is on: true or 1 (TcFacts reads the facts line "<flag> 1")."""
    return cfg.get(flag) is True or cfg.get(flag) == 1


def write_facts(cfg):
    """project.json "mock_write_facts" as [(callee 8 hex digits, field, size or advance token)], sorted; ValueError on
    a bad entry."""
    facts = cfg.get("mock_write_facts") or {}
    if not isinstance(facts, dict):
        raise ValueError('project.json "mock_write_facts" must map each callee to its fields and evidence')
    fields = set((cfg.get("abi") or {}).get("int_args") or ()) | {f"s{i}" for i in range(8)}
    out = []
    for callee, entry in facts.items():
        c = str(callee).lower().removeprefix("0x")
        if not c or len(c) > 8 or any(ch not in "0123456789abcdef" for ch in c):
            raise ValueError(f'project.json "mock_write_facts" {callee}: not a callee address')
        if not isinstance(entry, dict) or not isinstance(entry.get("why"), str) or not entry["why"].strip():
            raise ValueError(f'project.json "mock_write_facts" {callee}: no evidence ("why": what shows the write)')
        for field, size in entry.items():
            if field == "why":
                continue
            if field not in fields:
                raise ValueError(f'project.json "mock_write_facts" {callee}: {field} is no pointer argument '
                                 f'({", ".join(sorted(fields))})')
            if isinstance(size, dict):
                size = advance(cfg, f"{callee} {field}", size)
            elif not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_SIZE:
                raise ValueError(f'project.json "mock_write_facts" {callee} {field}: size 1..{MAX_SIZE} bytes')
            out.append((c.zfill(8), field, size))
    return sorted(out, key=lambda w: (w[0], w[1]))


def advance(cfg, where, entry):
    """A cursor advance (the module's doc) as its facts token "aA.B:op.a.s:..."; ValueError on a bad one."""
    bad = f'project.json "mock_write_facts" {where}: '
    if cfg.get("target") != "dreamcast_sh4":
        raise ValueError(bad + "an advance is modelled on the Dreamcast only (little-endian words)")
    adv, num = entry.get("advance"), (lambda v, ok: isinstance(v, int) and not isinstance(v, bool) and ok(v))
    if set(entry) != {"advance"} or not isinstance(adv, dict) or set(adv) != {"align", "op", "operands"}:
        raise ValueError(bad + 'an advance is {"advance": {"align": A, "op": B, "operands": {op hex: [a, s]}}}')
    if not num(adv["align"], lambda v: v in (1, 2, 4)) or not num(adv["op"], lambda v: v in (1, 2, 4)):
        raise ValueError(bad + "align and op are 1, 2 or 4 bytes")
    ops = adv["operands"]
    if not isinstance(ops, dict) or not 1 <= len(ops) <= MAX_OPS:
        raise ValueError(bad + f"operands: 1..{MAX_OPS} ops")
    toks = []
    for op, v in ops.items():
        try:
            k = int(str(op).lower().removeprefix("0x"), 16)
        except ValueError:
            k = -1
        if not 0 <= k < 1 << 8 * adv["op"] or not isinstance(v, list) or len(v) != 2 \
                or not num(v[0], lambda x: x in (1, 2, 4)) or not num(v[1], lambda x: 0 <= x <= MAX_SIZE):
            raise ValueError(bad + f"operand {op}: an op of {adv['op']} bytes, [align 1|2|4, size 0..{MAX_SIZE}]")
        toks.append((k, f"{k:x}.{v[0]}.{v[1]}"))
    if len({k for k, _ in toks}) != len(toks):
        raise ValueError(bad + "an op is listed twice")
    return ":".join([f"a{adv['align']}.{adv['op']}"] + [t for _, t in sorted(toks)])


def facts_lines(cfg):
    """core/ghidra.facts_body's lines: "<flag> 1" per flag on, and the write facts while mock_writes is on."""
    lines = [f"{flag} 1" for flag in FLAGS if on(cfg, flag)]
    if on(cfg, "mock_writes"):
        lines += [f"mock_write {c} {f} {n}" for c, f, n in write_facts(cfg)]
    return lines


def modes(cfg):
    """{"call_snapshots": mode, "exit_frame": mode} from config "strict_judge" (default "enforce")."""
    sj = cfg.get("strict_judge") or {}
    if not isinstance(sj, dict) or any(k not in REPORTABLE for k in sj):
        raise ValueError(f'config "strict_judge": only {" and ".join(REPORTABLE)} have a mode ("enforce" or "report")')
    out = {k: sj.get(k, "enforce") for k in REPORTABLE}
    if any(m not in MODES for m in out.values()):
        raise ValueError(f'config "strict_judge": a mode is "enforce" or "report", not {out}')
    return out


def parse(cur, t):
    """A result line CJ, CS or XR (compare.parse_results, ppc_compare.parse_results)."""
    if t[0] == "CJ" and len(t) > 3:
        cur.setdefault("cj", {})[(int(t[1]), int(t[2]))] = int(t[3], 16)
    elif t[0] == "CS" and len(t) > 3:
        cur.setdefault("cs", {})[(int(t[1]), int(t[2]))] = t[3]
    elif t[0] == "XR":
        cur["xr"] = [int(x, 16) for x in t[1:]]


def alternative(exp, k, field):
    """The CJ value of call k's argument field (the value it had without a callee's junk), or None."""
    return (exp.get("cj") or {}).get((k, field))


def _nan(bits, kind):
    if kind == "f32":
        return (bits & 0x7F800000) == 0x7F800000 and bits & 0x007FFFFF != 0
    return (bits & 0x7FF0000000000000) == 0x7FF0000000000000 and bits & 0x000FFFFFFFFFFFFF != 0


def _same_bytes(want, have, word):
    """A snapshot's bytes: each stored byte equal ("xx": not compared), or a whole stored word (4 bytes from the
    pointer on; word: its struct format) NaN on both sides."""
    if len(have) != len(want):
        return False
    for j in range(0, len(want), 8):
        w, h = want[j:j + 8], have[j:j + 8]
        if all(w[i:i + 2] in ("xx", h[i:i + 2]) for i in range(0, len(w), 2)):
            continue
        try:
            nan = len(w) == 8 and all(_nan(struct.unpack(word, bytes.fromhex(x))[0], "f32") for x in (w, h))
        except ValueError:  # "xx" in the expected word, or no hex
            nan = False
        if not nan:
            return False
    return True


def _snaps(meta, exp, act, reg_index, word):
    names = {}
    for reg, i in reg_index.items():
        names.setdefault(i, reg)
    regs = {int(c["addr"], 16): {reg_index[r] for r in c["regs"] if r in reg_index} for c in meta["callees"]}
    ec, ac, got, out = exp["calls"], act["calls"], act.get("cs") or {}, []
    for (k, field), want in sorted((exp.get("cs") or {}).items()):
        if k >= len(ec) or k >= len(ac) or ec[k][0] != ac[k][0] or field not in regs.get(ec[k][0], ()):
            continue  # a call the calls' comparison already fails, or an argument the callee is not compared on
        have = got.get((k, field), "-")
        if _same_bytes(want, have, word):
            continue
        out.append(f"call #{k + 1} to 0x{ec[k][0]:08x}: the bytes argument {names.get(field, field)} points to at "
                   f"the call (what the function stored there before it; xx: not compared): expected {want}, got "
                   + ("nothing (not readable there)" if have == "-" else have))
    return out[:4] + ([f"... and {len(out) - 4} more call snapshots differ"] if len(out) > 4 else [])


def _exit(exp, act, spec, same_word, sp_report):
    want, have = exp.get("xr"), act.get("xr")
    if want is None:
        return []
    if have is None:
        return ["the exit frame (the stack pointer and callee-saved registers): expected it, got nothing"]
    out = []
    for i, (name, kind) in enumerate(spec):
        if i == 0:  # the stack pointer: reported only (the module's doc)
            e, a = want[0], have[0]
            if not same_word(e, a):
                sp_report.append(f"{name} at exit: the original's is 0x{e:08x}, the C's 0x{a:08x} (the C keeps no "
                                 "stack pointer: a frame the original leaves to a callee or pops for its caller)")
            continue
        e, a = want[i] if i < len(want) else 0, have[i] if i < len(have) else None
        if a is not None and (same_word(e, a) if kind == "w" else e == a or (_nan(e, kind) and _nan(a, kind))):
            continue
        w = 16 if kind == "f64" else 8
        out.append(f"{name} at exit: expected 0x{e:0{w}x}, got " + ("nothing" if a is None else f"0x{a:0{w}x}")
                   + " (the caller keeps it: the original restores it before returning)")
    return out


def compare(meta, exp, act, reg_index, spec, same_word, mode=None, report=None, word="<I"):
    """The call snapshots' and the exit frame's differences of one vector: enforced ones returned, report-only ones
    (mode {"call_snapshots"|"exit_frame": "report"}, and the stack pointer's) appended to report. [] when the harness
    has neither. word: a guest word's struct format (compare.WORD; the GameCube's ">I")."""
    out, sp = [], []
    for kind, diffs in (("call_snapshots", _snaps(meta, exp, act, reg_index, word)),
                        ("exit_frame", _exit(exp, act, spec, same_word, sp))):
        if (mode or {}).get(kind, "enforce") != "report":
            out += diffs
        elif report is not None:
            report += [f"{kind} (report only): {d}" for d in diffs]
    if report is not None:
        report += [f"exit_frame (report only): {d}" for d in sp]
    return out


def note(result, vset, vid, report):
    """A vector's report-only differences into the check result's "strict_report" (absent while there are none)."""
    if not report:
        return
    r = result.setdefault("strict_report", {"vectors": 0, "first": []})
    r["vectors"] += 1
    if len(r["first"]) < 5:
        r["first"].append({"set": vset, "vector": vid, "diffs": report[:6]})
