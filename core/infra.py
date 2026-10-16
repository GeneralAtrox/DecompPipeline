"""Infrastructure errors are not verdicts (batch 21).

A readable attempt cycle (preflight, sandbox, agent, judge) or a verify can die on the file system or the operating
system rather than on the function: a file the tool itself wrote is missing or refused ([Errno 2], [WinError 2],
[WinError 183], [Errno 13]), or the linker cannot open an object the tool's own build just wrote (LNK1181 on
build/ADDR/rd_preflight/protos.obj when two workers shared a build folder). Such a failure says nothing about the
function: it is raised as InfraError, retried once, and recorded as `infra_error` (never `blocked`, `failed` or
`error`). `refine.py status` lists such rows, old ones included (an `error` or `blocked` row whose note matches).
Pure: no core.paths.
"""
import re

# A Python OSError's text, or a compiler/linker refusing to open a file under the tool's own folders.
INFRA_RX = re.compile(
    r"\[(?:Errno|WinError) \d+\]"
    r"|\bLNK1(?:181|104)\b[^\n]*cannot open[^\n]*[\\/](?:build|sandbox|readable_snapshots)[\\/]"
    r"|\bC1083\b[^\n]*[\\/](?:build|sandbox|readable_snapshots)[\\/]", re.I)
STATUS = "infra_error"


class InfraError(RuntimeError):
    """An attempt cycle stopped on a file-system or OS error, not on the function."""


def text_of(*parts):
    """One text of strings, lists of strings and Nones."""
    out = []
    for p in parts:
        if isinstance(p, (list, tuple)):
            out += [str(x) for x in p if x]
        elif p:
            out.append(str(p))
    return "; ".join(out)


def is_infra(*parts):
    return bool(INFRA_RX.search(text_of(*parts)))


def check(*parts, what="build"):
    """Raise InfraError when the messages (strings or lists) show an infrastructure error."""
    text = text_of(*parts)
    if INFRA_RX.search(text):
        raise InfraError(f"{what}: {text}"[:600])


def is_infra_exc(e):
    """An exception that is an infrastructure error: InfraError, or an OSError (file system, process start)."""
    return isinstance(e, (InfraError, OSError))


def rows(led):
    """{addr: note} of readable ledger rows that record an infrastructure error, not a verdict: status infra_error,
    an `error` or `blocked` row whose note is one (rows from before batch 21), or a redo_failed of that kind."""
    out = {}
    for addr, r in led.items():
        r = r or {}
        st, note = r.get("status"), r.get("note") or ""
        rf = r.get("redo_failed") or {}
        if st == STATUS or (st in ("error", "blocked") and is_infra(note)):
            out[addr] = f"{st}: {note}"
        elif rf and (rf.get("outcome") == STATUS or (rf.get("outcome") in ("error", "blocked")
                                                     and is_infra(rf.get("note")))):
            out[addr] = f"redo {rf.get('outcome')} (file kept {st}): {rf.get('note', '')}"
    return out
