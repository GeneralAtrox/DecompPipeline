"""Where pipeline tokens go. Three read-only reports:

  python tools/usage_report.py ledger   --project DIR [--project DIR ...]
      sum ledger attempts by project, model, outcome, attempt number, tier, size, turns, final status
  python tools/usage_report.py attempts --project DIR [--project DIR ...]
      token anatomy of agent attempts from runs/*/attempt-*/transcript.jsonl (cache, output, thinking, first call)
  python tools/usage_report.py sessions --hours H
      Claude Code transcripts (~/.claude/projects/**/*.jsonl) since H hours ago, by model and session kind
  python tools/usage_report.py readable --project DIR [--since 6h|2026-10-13T20:00] [--summary]
      readable attempts: cache writes and reads, output, thinking, first call, files read, refused commands,
      PASS or not from state/readable.json (usage_readable.py)
On a subscription the dollar figures are notional (API-equivalent); usage windows are the real limit.
"""
import argparse
import collections
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402


def _bucket(n, edges):
    for e in edges:
        if n <= e:
            return f"<={e}"
    return f">{edges[-1]}"


def _name(ws):
    if ws.parent.name == "decomp":  # a game folder holding several workspaces under decomp/
        return f"{ws.parent.parent.name.split()[0][:5]}:{ws.name}"
    return f"{ws.parent.name.split()[0][:5]}:{ws.name.removeprefix('decomp').lstrip('_') or 'main'}"


def ledger_report(wss):
    keys = ("project", "model", "outcome", "attempt_n", "tier", "insns", "turns", "final_status")
    agg = {k: collections.defaultdict(lambda: [0, 0, 0, 0.0]) for k in keys}
    tot, per_fn = [0, 0, 0, 0.0], []
    for ws in wss:
        proj = _name(ws)
        proj_key = "Spawn:sst*" if proj.startswith("Spawn:sst") else proj
        L = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))["functions"]
        for addr, r in L.items():
            fin, fn_in = r.get("status"), 0
            for a in r.get("attempts") or []:
                vals = {"project": proj_key, "model": a.get("model"),
                        "outcome": a.get("verdict") or (a.get("run_error") and "run_error") or "none",
                        "attempt_n": min(a.get("n", 1), 3), "tier": r.get("tier"),
                        "insns": _bucket(r.get("insns") or 0, [30, 80, 200, 500]),
                        "turns": _bucket(a.get("turns") or 0, [5, 10, 20, 40]), "final_status": fin}
                t = (a.get("tokens_in") or 0, a.get("tokens_out") or 0, a.get("cost_usd") or 0)
                for k, v in vals.items():
                    row = agg[k][v]
                    row[0] += 1
                    row[1] += t[0]
                    row[2] += t[1]
                    row[3] += t[2]
                tot[0] += 1
                tot[1] += t[0]
                tot[2] += t[1]
                tot[3] += t[2]
                fn_in += t[0]
            per_fn.append((fn_in, proj, addr, fin, r.get("insns"), len(r.get("attempts") or [])))
    print(f"TOTAL attempts {tot[0]}  in {tot[1] / 1e6:.1f}M  out {tot[2] / 1e6:.2f}M  notional ${tot[3]:.0f}")
    for k, d in agg.items():
        print(f"\n== by {k}  (attempts, tokens_in M, share of in, tokens_out M, $)")
        for v, (n, ti, to, c) in sorted(d.items(), key=lambda x: -x[1][1]):
            print(f"  {str(v):22} {n:6} {ti / 1e6:8.1f} {100 * ti / max(tot[1], 1):5.1f}% {to / 1e6:7.2f} {c:8.1f}")
    per_fn.sort(reverse=True)
    print("\n== top 10 functions by tokens_in")
    for t in per_fn[:10]:
        print(f"  {t[0] / 1e6:6.2f}M {t[1]:16} {t[2]} {str(t[3]):8} insns {t[4]} attempts {t[5]}")
    top = sum(t[0] for t in per_fn[:len(per_fn) // 10])
    print(f"top 10% of functions take {100 * top / max(tot[1], 1):.0f}% of tokens_in")


def _transcript(path):
    """(first-call context tokens or None, final result event or None)."""
    first, res = None, None
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        m = e.get("message")
        u = m.get("usage") if isinstance(m, dict) else None
        if e.get("type") == "assistant" and u and first is None:
            first = sum(u.get(k) or 0 for k in ("cache_creation_input_tokens", "cache_read_input_tokens", "input_tokens"))
        if e.get("type") == "result":
            res = e
    return first, res


def attempts_report(wss):
    K = ("cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens", "thinking")
    tot, n, base, ok_out, bad_out = dict.fromkeys(K, 0), 0, [], [], []
    for ws in wss:
        for t in (ws / "runs").glob("*/attempt-*/transcript.jsonl"):
            first, res = _transcript(t)
            if not res or not res.get("usage"):
                continue
            u = res["usage"]
            n += 1
            for k in K[:3]:
                tot[k] += u.get(k) or 0
            tot["thinking"] += (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
            if first:
                base.append(first)
            chk = t.parent / "check.json"
            v = json.loads(chk.read_text(encoding="utf-8")).get("verdict") if chk.exists() else None
            (ok_out if v == "PASS" else bad_out).append(u.get("output_tokens") or 0)
    print("attempts", n)
    for k in K:
        print(f"  {k:30} total {tot[k] / 1e6:8.1f}M  per attempt {tot[k] / max(n, 1) / 1e3:6.1f}k")
    print(f"  thinking share of output {100 * tot['thinking'] / max(tot['output_tokens'], 1):.0f}%")
    if base:
        base.sort()
        print(f"  first-call context (system prompt + tools + TASK): median {base[len(base) // 2] / 1e3:.1f}k")
    for name, xs in (("PASS", ok_out), ("not PASS", bad_out)):
        xs.sort()
        if xs:
            print(f"  output per attempt {name:9}: median {xs[len(xs) // 2] / 1e3:.1f}k  "
                  f"p90 {xs[int(len(xs) * .9)] / 1e3:.1f}k  n {len(xs)}")


def sessions_report(hours):
    since = time.time() - hours * 3600
    root = pathlib.Path.home() / ".claude" / "projects"
    F = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    by = collections.defaultdict(lambda: [0, 0, 0, 0, 0, set()])
    for p in root.rglob("*.jsonl"):
        if p.stat().st_mtime < since:
            continue
        kind = ("subagent:" if "subagents" in p.parts else "main:") + p.relative_to(root).parts[0][:34]
        seen = set()
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") if isinstance(d, dict) else None
            u = m.get("usage") if isinstance(m, dict) else None
            if not u or d.get("type") != "assistant":
                continue
            if m.get("id") in seen:  # streamed chunks repeat the same message id and usage
                continue
            seen.add(m.get("id"))
            r = by[(m.get("model") or "?", kind)]
            for i, f in enumerate(F):
                r[i] += u.get(f) or 0
            r[4] += 1
            r[5].add(p.name)
    print(f"{'model':24} {'kind':44} {'calls':>6} {'files':>5} {'in':>7} {'cache_wr':>9} {'cache_rd':>9} {'out':>7}"
          "  (M tokens)")
    for (model, kind), r in sorted(by.items(), key=lambda x: -(x[1][1] + x[1][2] + x[1][0])):
        print(f"{model:24} {kind:44} {r[4]:6} {len(r[5]):5} {r[0] / 1e6:7.2f} {r[1] / 1e6:9.2f} {r[2] / 1e6:9.1f} "
              f"{r[3] / 1e6:7.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ledger", "attempts"):
        s = sub.add_parser(name)
        s.add_argument("--project", action="append", required=True)
    s = sub.add_parser("sessions")
    s.add_argument("--hours", type=float, required=True)
    s = sub.add_parser("readable")
    s.add_argument("--project", action="append", required=True)
    s.add_argument("--since", help="6h, 2d, 90m, or an ISO date/time")
    s.add_argument("--summary", action="store_true", help="no per-attempt rows")
    a = ap.parse_args()
    if a.cmd == "sessions":
        sessions_report(a.hours)
    elif a.cmd == "readable":
        import usage_readable
        for d in a.project:
            usage_readable.report(workspace(d), usage_readable.parse_since(a.since), not a.summary)
    else:
        wss = [workspace(d) for d in a.project]
        (ledger_report if a.cmd == "ledger" else attempts_report)(wss)
    return 0


if __name__ == "__main__":
    sys.exit(main())
