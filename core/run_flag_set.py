"""The settable form of a project.json "run_flags" entry (TcCalls "rs:"; core/compare.run_flag parses the clearing form).

A wait loop that runs until a callee sets bits in a word of an object the function was given: Toy Commander's window
loop 8c077c30 clears bit 0x400 of *(r4 + 0x5c) (8c077c8c), then calls the window callbacks with r12 = r4 each pass
(8c07c8d8, 8c07c2b4) until that bit is set (8c077d30..8c077d36); on the console a callback sets it when the window
closes. Mocked callees store nothing, so every vector ran to the call limit. The entry
    {"base": "r4", "offset": "5c", "bits": "400", "callee": "8c07c8d8", "call": 3, "why": "<the evidence>"}
makes the Nth mocked call (from 1) to callee set those bits in the word at (base's value at the function's entry) +
offset, in the oracle (TcCalls) and every runtime (header line "f OFFSET CALLEE N REG BITS": runner.c and
rd_runner.c work out the word per vector from its R line; tc_strict.h's tcs_flag_bytes reads the word at the call and
sets the bits). Opt-in, one entry per function as before; an entry without evidence, or with a bad register, offset,
bits or count, is refused.

Nearest existing: core/compare.run_flag - it parses the clearing form (a fixed word stored 0); compare.py is over 400
lines, so it may not grow, and it calls this for an entry with "base"."""

BASES = ("r4", "r5", "r6", "r7")


def parse(addr, e):
    """(offset, callee, n, register number 4..7, bits) of the settable entry e of the function at addr."""
    if not isinstance(e.get("why"), str) or not e["why"].strip():
        raise ValueError(f'project.json "run_flags" {addr}: no evidence ("why": which callee sets the bits, and how)')
    if e.get("base") not in BASES or "word" in e:
        raise ValueError(f'project.json "run_flags" {addr}: "base" is one of {", ".join(BASES)} (the argument '
                         'register at the entry), with "offset" in place of "word"')
    try:
        off, bits, callee = (int(e[k], 16) for k in ("offset", "bits", "callee"))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f'project.json "run_flags" {addr}: "offset", "bits" and "callee" are hex') from None
    n = e.get("call")
    if (off % 4 or not 0 <= off < 0x1000 or not 0 < bits <= 0xFFFFFFFF or not isinstance(n, int)
            or isinstance(n, bool) or not 1 <= n <= 64):
        raise ValueError(f'project.json "run_flags" {addr}: an aligned offset below 0x1000, bits 1..ffffffff and '
                         '"call" 1..64')
    return off, callee, n, int(e["base"][1:]), bits


def note(flag):
    """core/agent.abi_notes' lines for meta "run_flag": the clearing form's text as it was, or the settable form's."""
    if "bits" not in flag:
        return ["", "## A run flag a callee clears", "",
                f"The word at 0x{flag['word']} is a run flag: the function loops while it is non-zero, and in the game "
                f"only fn_{flag['callee']}", f"(or its callees) clears it. In the tests, call {flag['call']} to "
                f"fn_{flag['callee']} stores 0 there, as tc_call does. So read the word",
                "from memory each time the original tests it; never keep a copy across a call or end the loop early."]
    at = f"{flag['base']} + 0x{int(flag['offset'], 16):x}"
    return ["", "## A run flag a callee sets", "",
            f"The word at {at} ({flag['base']} as the function got it) is a run flag: the function loops until bits "
            f"0x{int(flag['bits'], 16):x} are set in it, and in the game only fn_{flag['callee']}",
            f"(or its callees) sets them. In the tests, call {flag['call']} to fn_{flag['callee']} sets those bits "
            "there, as tc_call does. So read the word",
            "from memory each time the original tests it; never keep a copy across a call or end the loop early."]


def arg(f):
    """TcPrepare's script arg 12 for a parsed settable entry: "rs:OFFSET_CALLEE_N_REG_BITS" (no ",;= ")."""
    return f"rs:{f[0]:08x}_{f[1]:08x}_{f[2]}_{f[3]}_{f[4]:08x}"
