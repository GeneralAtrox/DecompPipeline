"""Dreamcast hooks (core/target_hooks): only which functions a scoped oracle change reaches.

"listed_limits_or_rw": the per-function limits (TcOracle.limits, core/oracle_limits) reach only a function
project.json "oracle_limits" lists, and the stored value of a hardware register (TcHw.atExit) only one whose listing
both reads and writes a register. Every other function makes the same bytes, so its stamp stays as it was. An unknown
scope counts as in scope: its stamp follows the change, which only costs a re-prepare.
"""
from core import oracle_limits


def _reads_and_writes(cfg, addr):
    ops = ((cfg.get("hw_ops") or {}).get(addr) or {}).get("ops") or []
    return bool({o.split()[1] for o in ops if o.startswith("read ")} & {o.split()[1] for o in ops if o.startswith("write ")})


def stamp_scope(cfg, addr, scope):
    if scope == "listed_limits_or_rw":
        return oracle_limits.arg(cfg, addr) != "-" or _reads_and_writes(cfg, addr)
    return True
