"""Call-site coverage: the call sites of each function that no valid test vector reaches.

The judge compares a callee's target and argument registers only where a vector's run makes the call. A call site no
vector reaches is never compared: matched raw C there is unverified, and readable C copies it (TC port phase C
crashed six times on such calls: a frame address dropped to 0, a NULL passed for a matrix). The oracle reports, per
call site, the valid vectors whose run reached its mocked callee (oracle_summary.json "call_sites"), and since the
reach pass (TcReach.java) why each site it could not reach stays unreached ("reach": {"runs", "unreached": {site:
reason}}).

A site counts as
- reached: some valid vector reaches it;
- explained: unreached, and the harness's reach pass gave the reason it could not reach it;
- untried: unreached, and the harness predates the reach pass (no "reach" key) or the pass was off: re-prepare it
  (tools/call_coverage.py reach).
Functions without a harness summary, or with a summary without "call_sites", are counted apart (no data).
"""
from . import paths


def site_rows(addrs, harness_dir=None):
    """{addr: row} for the functions of addrs with call-site data. A row: total, reached, unreached (sorted site
    list), reasons {site: reason} (explained ones), tried (the harness ran the reach pass), reach_runs, found (sites
    the reach pass reached that the first pass did not). Functions without data are left out."""
    hdir = harness_dir or paths.HARNESS
    out = {}
    for a in addrs:
        s = paths.read_json(hdir / a / "oracle_summary.json")
        if not s or s.get("call_sites") is None:
            continue
        sites = s["call_sites"]
        reach = s.get("reach")
        unreached = sorted(x for x, n in sites.items() if not n)
        reasons = dict((reach or {}).get("unreached") or {})
        out[a] = {"total": len(sites), "reached": len(sites) - len(unreached), "unreached": unreached,
                  "reasons": {x: reasons[x] for x in unreached if x in reasons}, "tried": reach is not None,
                  "reach_runs": (reach or {}).get("runs", 0), "found": list((reach or {}).get("found") or [])}
    return out


def figure(rows, nodata=0):
    """The project figure from site_rows: sites, reached, unreached, explained, untried, functions with an unreached
    site, functions with an untried one, percent reached."""
    f = {"functions": len(rows), "no_data": nodata, "sites": 0, "reached": 0, "unreached": 0, "explained": 0,
         "untried": 0, "fn_unreached": 0, "fn_untried": 0, "found": 0}
    for r in rows.values():
        f["sites"] += r["total"]
        f["reached"] += r["reached"]
        f["unreached"] += len(r["unreached"])
        f["explained"] += len(r["reasons"])
        f["found"] += len(r["found"])
        untried = len(r["unreached"]) - len(r["reasons"])
        f["untried"] += untried
        f["fn_unreached"] += bool(r["unreached"])
        f["fn_untried"] += bool(untried)
    f["percent"] = round(100.0 * f["reached"] / f["sites"], 2) if f["sites"] else 100.0
    return f


def summary_line(f):
    return (f"call sites reached {f['reached']}/{f['sites']} ({f['percent']}%); {f['unreached']} unreached in "
            f"{f['fn_unreached']} function(s): {f['explained']} explained by the reach pass, {f['untried']} untried "
            f"(in {f['fn_untried']} function(s): harness made before the reach pass); the reach pass reached "
            f"{f['found']} more" + (f"; {f['no_data']} function(s) without call-site data" if f["no_data"] else ""))


def matched_functions():
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    return sorted(a for a, r in led.items() if r.get("status") == "matched")


def report(addrs, log=print, top=None):
    """Ranked report (most unreached sites first) over addrs; returns (rows, figure)."""
    rows = site_rows(addrs)
    f = figure(rows, nodata=len(set(addrs) - set(rows)))
    log(summary_line(f))
    ranked = sorted((r for r in rows.items() if r[1]["unreached"]), key=lambda x: (-len(x[1]["unreached"]), x[0]))
    for a, r in ranked[:top] if top else ranked:
        state = "explained" if len(r["reasons"]) == len(r["unreached"]) else (
            "partly explained" if r["reasons"] else "untried")
        log(f"  {a}  {len(r['unreached'])} of {r['total']} unreached ({state})")
        for x in r["unreached"]:
            log(f"      {x}  {r['reasons'].get(x, 'untried: the harness predates the reach pass')}")
    return rows, f


def untried(rows):
    """Functions with an unreached site the reach pass has not tried (re-prepare them)."""
    return sorted(a for a, r in rows.items() if len(r["unreached"]) > len(r["reasons"]))
