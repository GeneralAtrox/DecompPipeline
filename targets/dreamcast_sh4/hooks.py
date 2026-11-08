"""Dreamcast hooks (core/target_hooks): only which functions a scoped oracle change reaches.

"listed_limits_or_rw": the per-function limits (TcOracle.limits, core/oracle_limits) reach only a function
project.json "oracle_limits" lists, and the stored value of a hardware register (TcHw.atExit) only one whose listing
both reads and writes a register. "unlisted_call_args": TcTrack's CA lines at a call no callee record lists reach
only a function core/unchecked_args.sh_in_scope names. Every other function makes the same bytes, so its stamp stays
as it was. An unknown
scope counts as in scope: its stamp follows the change, which only costs a re-prepare.
"""
from core import oracle_limits


def _reads_and_writes(cfg, addr):
    ops = ((cfg.get("hw_ops") or {}).get(addr) or {}).get("ops") or []
    return bool({o.split()[1] for o in ops if o.startswith("read ")} & {o.split()[1] for o in ops if o.startswith("write ")})


def stamp_scope(cfg, addr, scope):
    if scope == "unlisted_call_args":  # TcTrack.of: CA lines at calls no callee record lists (core/unchecked_args)
        from core import paths, unchecked_args
        return unchecked_args.hook(cfg, addr, paths.FUNCTION_BODIES, 2, False)
    if scope == "listed_limits_or_rw":
        return oracle_limits.arg(cfg, addr) != "-" or _reads_and_writes(cfg, addr)
    return True
