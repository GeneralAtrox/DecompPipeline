"""Gate 5 of tools/scorecard.py, scale (split from it at its 400-line cap; docs/DEFINITION_OF_DONE.md): a console's
whole binary projected at its measured rates, agent cost and machine hours against the spec's budgets.
Nearest existing: tools/scorecard_data.py - the readers every gate shares (390 lines); this is one gate's arithmetic.
"""
from scorecard_data import at_most, check, unmeasured


def add(*xs):
    return None if any(x is None for x in xs) else sum(xs)


def projection(n, r, par):
    """(USD, raw-stage hours) for n functions at rates r, or None where a rate is missing."""
    usd = add(r["raw_usd"], r["rd_usd"])
    hours = (None if r["raw_s"] is None or r["prepare_median_s"] is None else
             max(n * r["raw_s"] / par["workers"], n * r["prepare_median_s"] / par["slots"]) / 3600)
    return (None if usd is None else n * usd), hours


def gate_scale(spec, r, n_src, par, ref=None):
    b = spec["baseline"]
    n, nsrc = n_src
    out = [check("baseline size", "pass", n, source=nsrc) if n else unmeasured("baseline size", nsrc)]
    usd, h = projection(n or 0, r, par)
    if n is None or usd is None:
        out.append(unmeasured("projected agent cost", "no function count" if n is None else
                              "no measured raw + readable cost on this console", f"<= {b['budget_usd']} USD"))
    else:
        out.append(at_most("projected agent cost (agent on every function)", usd, b["budget_usd"],
                           f"{n} x {usd / n:.4f} USD measured", " USD"))
    if n is None or h is None:
        out.append(unmeasured("projected machine hours", "no function count" if n is None else
                              "no measured agent seconds and prepare times on this console",
                              f"<= {b['budget_hours']} h"))
    else:
        out.append(at_most("projected machine hours (raw stage)", h, b["budget_hours"],
                           f"max(agent {r['raw_s']:.1f} s / {par['workers']} workers, prepare "
                           f"{r['prepare_median_s']:.2f} s / {par['slots']} slots) x {n}; config.json max_workers, "
                           "ghidra_parallel", " h"))
    rt = r["rd_timed"]
    if n is None or h is None or r["rd_s"] is None:
        out.append(unmeasured("projected machine hours (raw + readable)", "no function count" if n is None else
                              "no measured raw-stage hours" if h is None else
                              f"no readable function whose agent attempts all carry duration_s ({rt['rows']} timed "
                              "row(s))", f"<= {b['budget_hours']} h"))
    else:
        h_rd = n * r["rd_s"] / par["workers"] / 3600
        out.append(at_most("projected machine hours (raw + readable)", h + h_rd, b["budget_hours"],
                           f"raw {h:,.1f} h + readable {r['rd_s']:.1f} s / {par['workers']} workers x {n} = "
                           f"{h_rd:,.1f} h; readable.json: {rt['seconds']:.0f} s over {rt['readable']} readable of "
                           f"{rt['rows']} rows timed in full (agent-run rows only: an upper bound)", " h"))
    if n and ref and (r["raw_usd"] is None or r["raw_s"] is None):
        usd, h = projection(n, ref[1], par)
        out.append(check(f"for scale only: at {ref[0]}'s measured rates", "info",
                         f"{usd:,.0f} USD, {h:,.1f} h raw stage" if usd is not None and h is not None else None,
                         source="not this console's own rates; does not count toward the gate"))
    return out
