"""Per-game tally for READABLE_TX.md: the readable pass's results against what agents spent on the same functions.
usage: tally.py OUT.json game=ledger.json:results.json[:lift.json] ..."""
import json
import statistics
import sys


def agent_cost(row):
    """(usd, tokens, attempts) of a row's agent attempts (machine adoptions and re-adoptions cost nothing)."""
    usd = tok = n = 0
    for a in row.get("attempts") or []:
        if a.get("kind") in ("draft", "machine", "readopt", "lookalike"):
            continue
        usd += a.get("cost_usd") or 0
        tok += (a.get("tokens_in") or 0) + (a.get("tokens_out") or 0)
        n += 1
    return usd, tok, n


out = {}
for arg in sys.argv[2:]:
    game, paths = arg.split("=", 1)
    parts = paths.split("|")
    ledger = json.load(open(parts[0], encoding="utf-8"))
    rows = {}
    for p in parts[1:]:
        for r in json.load(open(p, encoding="utf-8")):
            if r["addr"] not in rows or r.get("stage") != "lifted":
                rows[r["addr"]] = r
    stages = {}
    for r in rows.values():
        st = r["stage"]
        if st == "lifted":
            st = "gaps" if r.get("gaps") else "lifted (not judged)"
        stages[st] = stages.get(st, 0) + 1
    done = {a: agent_cost(r) for a, r in ledger.items() if r.get("status") in ("readable", "stale")}
    paid = [c for c in done.values() if c[2]]
    mean_usd = statistics.mean(c[0] for c in paid) if paid else 0
    mean_tok = statistics.mean(c[1] for c in paid) if paid else 0
    adopted = [a for a, r in rows.items() if r["stage"] == "pass"]
    same_usd = sum(done[a][0] for a in adopted if a in done and done[a][2])
    same_tok = sum(done[a][1] for a in adopted if a in done and done[a][2])
    same_n = sum(1 for a in adopted if a in done and done[a][2])
    rest = len(adopted) - same_n
    out[game] = {
        "functions": len(rows), "stages": stages, "adopted": len(adopted),
        "adopted_share": round(len(adopted) / len(rows), 4) if rows else 0,
        "agent_paid_functions": len(paid), "agent_usd_total": round(sum(c[0] for c in paid), 2),
        "agent_tokens_total": sum(c[1] for c in paid),
        "mean_usd_per_function": round(mean_usd, 5), "mean_tokens_per_function": round(mean_tok),
        "adopted_agent_did": same_n, "adopted_agent_usd": round(same_usd, 2), "adopted_agent_tokens": same_tok,
        "adopted_not_done": rest, "adopted_not_done_usd_est": round(rest * mean_usd, 2),
        "adopted_not_done_tokens_est": round(rest * mean_tok),
        "start_pass_judge": stages.get("gate", 0),
    }
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), indent=1)
print(json.dumps(out, indent=1))
