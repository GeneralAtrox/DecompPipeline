"""The readable judge's calls without a callee record (expected.txt CA) compared on the registers the callee takes.

TcTrack writes CA after a call the harness has no callee record for (a call through a function pointer, or a caller
whose harness lists no callees): every argument register the original set since its entry or its last call. Some of
them are leftovers the callee never reads (TC's Mat3_SetScale, 8c0622f6, takes r4 and fr4; its callers leave r5..r7
set). readable_check.callee_reads already narrows recorded callees to the registers their own harness takes; this
narrows CA the same way, by the target's prototype in the readable names (the project's or SDK.json): its convention
placement, or the names entry's "storage". A target with no names prototype (an untyped function-table slot), or a
variadic one, keeps every CA register: nothing is guessed. Raw checks (check_raw) pass no prototypes and stay as
they are. The caller's readable C calls the target through that same prototype, so a register outside it cannot reach
the callee: comparing it would demand a value no readable call can give.

Nearest existing: readable_check.callee_reads - it narrows recorded callees by their own harness's parameters; CA
calls have no record (and readable_check.py is over 400 lines, so it may not grow)."""
import re

from . import compare as check

ARG_REG = re.compile(r"r[0-7]|fr([4-9]|1[01])")


def target_regs(reg, addr):
    """The argument registers the prototype of addr (8 hex digits) takes, or None (no names prototype, variadic, or a
    prototype the registry cannot place: keep the full compare)."""
    from . import readable, readable_abi  # here: readable imports the judge's modules
    e = reg.functions.get(addr)
    if not e or not e.get("proto") or readable.is_variadic(e["proto"]):
        return None
    try:
        st = readable_abi.override(reg, addr, e["proto"]) or readable_abi.storages(reg, e["proto"])
    except readable.RegistryError:
        return None
    return {s for s in st if ARG_REG.fullmatch(s)}


def ca_reads(reg, harness):
    """{target (int): the registers its prototype takes} for every CA call target in harness/expected.txt that has
    one; merged into readable_check's reads (whose callee keys are strings, so the two never meet)."""
    out = {}
    for run in check.parse_results(harness / "expected.txt").values():
        for k in run.get("call_args", {}):
            t = run["calls"][k][0]
            if t not in out:
                out[t] = target_regs(reg, f"{t:08x}")
    return {t: r for t, r in out.items() if r is not None}


def narrow(exp, reads):
    """exp (parse_results) with each CA call to a target in reads (ca_reads' int keys) cut to the registers that
    target takes; stack words and other entries are kept. reads None or without int keys: exp unchanged."""
    ca = {t: r for t, r in (reads or {}).items() if isinstance(t, int)}
    if not ca:
        return exp
    for run in exp.values():
        for k, regs in run.get("call_args", {}).items():
            t = run["calls"][k][0]
            if t in ca:
                run["call_args"][k] = [r for r in regs if not ARG_REG.fullmatch(r) or r in ca[t]]
    return exp
