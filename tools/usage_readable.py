"""usage_report.py readable: what each readable attempt cost, read and ran (see usage_report.py).

Per attempt (runs/ADDR/readable/attempt-NN/transcript.jsonl, matched to state/readable.json attempts by n):
cache writes, cache reads, output and thinking tokens, the first call's context, turns, the files it read, the
commands the CLI refused, and the verdict the ledger recorded (PASS or not). Then totals by verdict, the effort
settings seen, the most read files and the refused commands. Read-only; nothing here depends on a project.
"""
import collections
import json
import re
import statistics
import time

K = ("cache_w", "cache_r", "output", "thinking", "first", "turns")


def parse_since(text):
    """'6h', '2d', '90m' ago, or an ISO date/time ('2026-10-13', '2026-10-13T20:00'); None: everything."""
    if not text:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd])", text)
    if m:
        return time.time() - float(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except ValueError:
            pass
    raise SystemExit(f"--since {text!r}: use 6h, 2d, 90m or an ISO date/time")


def transcript(path):
    """Usage, reads and refusals of one session's stream-json transcript."""
    row = dict.fromkeys(K, 0)
    row.update(reads=[], refused=[], first=None, result=False)
    bash, seen = {}, set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        u = m.get("usage") or {}
        if e.get("type") == "assistant" and u and row["first"] is None:
            row["first"] = sum(u.get(k) or 0 for k in ("cache_creation_input_tokens", "cache_read_input_tokens",
                                                        "input_tokens"))
        for c in m.get("content") or []:
            if not isinstance(c, dict) or c.get("id") in seen:
                continue
            if c.get("type") == "tool_use":
                seen.add(c.get("id"))
                inp = c.get("input") or {}
                if c.get("name") == "Read":
                    row["reads"].append(str(inp.get("file_path", "")).replace("\\", "/").rsplit("/", 1)[-1])
                elif c.get("name") == "Bash":
                    bash[c.get("id")] = inp.get("command", "")
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in bash and c.get("is_error"):
                text = c.get("content") if isinstance(c.get("content"), str) else json.dumps(c.get("content"))
                if "denied" in text or "permission" in text.lower():
                    row["refused"].append(bash[c["tool_use_id"]])
        if e.get("type") == "result":
            ru = e.get("usage") or {}
            row.update(result=True, cache_w=ru.get("cache_creation_input_tokens") or 0,
                       cache_r=ru.get("cache_read_input_tokens") or 0, output=ru.get("output_tokens") or 0,
                       turns=e.get("num_turns") or 0)
            row["thinking"] = (ru.get("output_tokens_details") or {}).get("thinking_tokens") or sum(
                (mu or {}).get("thinkingTokens") or 0 for mu in (e.get("modelUsage") or {}).values())
    row["first"] = row["first"] or 0
    return row


def rows(ws, since=None):
    led = json.loads((ws / "state" / "readable.json").read_text(encoding="utf-8"))
    led = led.get("functions", led)
    out = []
    for t in sorted((ws / "runs").glob("*/readable/attempt-*/transcript.jsonl")):
        if since and t.stat().st_mtime < since:
            continue
        addr, n = t.parts[-4], int(t.parent.name.split("-")[1])
        att = next((a for a in (led.get(addr) or {}).get("attempts") or [] if a.get("n") == n), {})
        r = transcript(t)
        if not r["result"]:  # a killed session has no result event: take the ledger's numbers
            r.update(output=att.get("tokens_out") or r["output"], thinking=att.get("thinking_tokens") or 0,
                     turns=att.get("turns") or 0)
        r.update(addr=addr, n=n, verdict=att.get("verdict") or "?", effort=att.get("effort"),
                 killed=att.get("killed"), when=t.stat().st_mtime)
        out.append(r)
    return out


def _med(xs):
    return statistics.median(xs) if xs else 0


def report(ws, since=None, per_attempt=True):
    rs = rows(ws, since)
    print(f"{ws}: {len(rs)} readable attempts" + (f" since {time.strftime('%Y-%m-%d %H:%M', time.localtime(since))}"
                                                  if since else ""))
    if not rs:
        return rs
    if per_attempt:
        print(f"{'addr':9} {'n':>2} {'verdict':8} {'effort':7} {'turns':>5} {'first':>6} {'cache_w':>8} {'cache_r':>8} "
              f"{'output':>7} {'think':>7} {'reads':>5} {'refused':>7}  files read")
        for r in sorted(rs, key=lambda r: r["when"]):
            print(f"{r['addr']:9} {r['n']:2} {r['verdict']:8} {str(r['effort']):7} {r['turns']:5} {r['first']:6} "
                  f"{r['cache_w']:8} {r['cache_r']:8} {r['output']:7} {r['thinking']:7} {len(r['reads']):5} "
                  f"{len(r['refused']):7}  {' '.join(sorted(set(r['reads'])))[:80]}")
    print(f"\n{'verdict':10} {'n':>4}  medians: {'first':>6} {'cache_w':>8} {'cache_r':>8} {'output':>7} {'think':>7} "
          f"{'turns':>5} {'reads':>5}   means: {'cache_w':>8} {'cache_r':>8} {'output':>7}")
    groups = collections.defaultdict(list)
    for r in rs:
        groups["PASS" if r["verdict"] == "PASS" else "not PASS"].append(r)
        groups["all"].append(r)
    for g in ("PASS", "not PASS", "all"):
        xs = groups.get(g) or []
        if not xs:
            continue
        med = {k: _med([r[k] for r in xs]) for k in K}
        mean = {k: statistics.mean(r[k] for r in xs) for k in K}
        print(f"{g:10} {len(xs):4}           {med['first']:6.0f} {med['cache_w']:8.0f} {med['cache_r']:8.0f} "
              f"{med['output']:7.0f} {med['thinking']:7.0f} {med['turns']:5.0f} {_med([len(r['reads']) for r in xs]):5.0f}"
              f"          {mean['cache_w']:8.0f} {mean['cache_r']:8.0f} {mean['output']:7.0f}")
    by_effort = collections.Counter((str(r["effort"]), r["verdict"] == "PASS") for r in rs)
    print("\nby effort (attempts PASS / all): " + ", ".join(
        f"{e}: {by_effort[(e, True)]}/{by_effort[(e, True)] + by_effort[(e, False)]}"
        for e in sorted({e for e, _ in by_effort})))
    fns = collections.defaultdict(list)
    for r in rs:
        fns[r["addr"]].append(r)
    done = sum(1 for xs in fns.values() if any(r["verdict"] == "PASS" for r in xs))
    print(f"functions {len(fns)}, passed {done}; attempts per passed function "
          f"{sum(len(xs) for xs in fns.values() if any(r['verdict'] == 'PASS' for r in xs)) / max(done, 1):.2f}")
    reads = collections.Counter(f for r in rs for f in set(r["reads"]))
    print("files read (attempts): " + ", ".join(f"{f} {c}" for f, c in reads.most_common(12)))
    refused = collections.Counter(c.strip()[:90] for r in rs for c in r["refused"])
    print(f"refused commands: {sum(refused.values())} in {sum(1 for r in rs if r['refused'])} attempts")
    for c, k in refused.most_common(8):
        print(f"  {k:4}  {c}")
    return rs
