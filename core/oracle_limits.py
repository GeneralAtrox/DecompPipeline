"""Per-function oracle limits (project.json "oracle_limits"): a function that really makes more calls or steps than
the oracle's defaults, opt-in per address with the evidence, as "spin_waits". SH-4 only (TcOracle.limits).

  "oracle_limits": {"8c0b1940": {"calls": 8192, "rets": 512, "why": "legal screen: 30 fades of about 190 calls"}}

calls  the most mocked calls in one vector (default 4096, TcOracle.CALL_LIMIT). A fixed loop of 256 x 19 calls can
       never make a valid vector under the default.
rets   how many calls get a scripted result (default 256, TcOracle.RETS); a later call returns 0. A wait that tests a
       callee's result every few calls ends before the last draw only if it has enough of them.
steps  the most instructions in one vector (default 500000, TcBase.STEP_LIMIT).
budget_s  this function's "ghidra_timeout_s" (0: the project's), so its oracle deadline and kill timeout. The shared
       facts file stays what the stamp hashes; the budget reaches the stamp through arg().

A limit decides which vectors are valid, never how one is compared: the judge compares the same records, there are
just more of them. Other functions keep the defaults, and with core's stamp scope (targets/dreamcast_sh4/hooks.py)
their stamps stay as they were.

Nearest existing: core/compare.run_flag (per-function oracle option) - that one models a callee's store; this only
moves limits, so it shares nothing but the shape of its entry.
"""

DEFAULTS = {"calls": 4096, "rets": 256, "steps": 500000, "budget_s": 0}
CALLS = (DEFAULTS["calls"], 8192)  # the top is the runners' array size (tc_rt.c MAX_CALLS, rd_internal.h RD_MAX_CALLS)
RETS = (DEFAULTS["rets"], 1024)  # the top is tc_rt_internal.h TCRT_RETS
STEPS = (DEFAULTS["steps"], 8000000)
RANGES = {"calls": CALLS, "rets": RETS, "steps": STEPS, "budget_s": (0, 14400)}


def arg(cfg, addr):
    """Script arg 14, "lim:CALLS_RETS_STEPS_BUDGET", or "-" for a function not listed. A malformed entry is refused
    (ValueError), never silently ignored."""
    e = (cfg.get("oracle_limits") or {}).get(addr)
    if e is None:
        return "-"
    if not isinstance(e, dict) or not e.get("why"):
        raise ValueError(f'project.json "oracle_limits" {addr}: no evidence ("why": what makes it need more)')
    for key in e:
        if key != "why" and key not in RANGES:
            raise ValueError(f'project.json "oracle_limits" {addr}: unknown limit {key!r}')
    vals = []
    for key, (lo, hi) in RANGES.items():
        v = e.get(key, lo)
        if not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f'project.json "oracle_limits" {addr}: {key} {v!r} is not in {lo}..{hi}')
        vals.append(v)
    return "lim:" + "_".join(str(v) for v in vals)


def run_cfg(cfg, addr):
    """The config one function's prepare runs under: cfg, with its budget_s as "ghidra_timeout_s" and no "ghidra_kill_s"
    when it lists one, so the oracle's deadline and the kill timeout move together."""
    budget = int(arg(cfg, addr).split("_")[-1]) if arg(cfg, addr) != "-" else 0
    return {k: v for k, v in dict(cfg, ghidra_timeout_s=budget).items() if k != "ghidra_kill_s"} if budget else cfg
