"""Compare two judging runs of the same frozen copies (judge.py output): base_<game>_<toolset>.json against
after_<game>_<toolset>.json, or summarise one tag alone (`python compare.py base`).

    python compare.py base after                  (each toolset against itself)
    python compare.py base:configured after:latest  (TAG:TOOLSET pins the toolset of each side)

Every field of every judgement is compared except its timing ("seconds"); verdict, score, failing/total, the
vectors and the first differences are listed first. Prints a table per game and toolset and every difference."""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
GAMES = (("tc", "Toy Commander (SH-4)"), ("df2", "Dragon Force 2 ws-btl (SH-2)"), ("skies", "Skies decomp-gekko (GameCube)"),
         ("th", "Too Human (Xbox 360)"))
KEY = ("verdict", "score", "failing", "total", "vectors", "first_diffs")
# Known run-to-run noise (measured: two runs on one compiler differ here): in these FAILs the C under test passes
# uninitialised stack words, so the "got 0x..." values of their first differences are host stack garbage (part of a
# randomised host address among them). Only those values are masked, and only for these functions.
NOISE = {("tc", "8c03054c"), ("tc", "8c058938")}


def unroot(v):
    """v with every path into a copy ('...\\cproof\\m_skies\\...') starting at <copy>\\ instead of the machine's folder."""
    if isinstance(v, dict):
        return {k: unroot(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unroot(x) for x in v]
    if isinstance(v, str):
        return re.sub(r"[A-Za-z]:[^\s'\"]*?[\\/]cproof[\\/]", "<copy>/", v)
    return v


def masked(game, addr, r):
    r = {k: v for k, v in r.items() if k != "seconds"}
    # The copy's own folder differs between machines (on laptop2, under its dpwork folder): only that prefix is
    # replaced, wherever a judgement names a path in the copy.
    r = unroot(r)
    if (game, addr) in NOISE and r.get("first_diffs"):
        r["first_diffs"] = json.loads(re.sub(r"got 0x[0-9a-f]+", "got <noise>", json.dumps(r["first_diffs"])))
    return r


def load(tag, game, ts):
    f = HERE / f"{tag}_{game}_{ts}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def counts(run):
    out = {}
    for kind in ("raw", "readable"):
        for r in run[kind].values():
            k = f"{kind} {r['verdict']}"
            out[k] = out.get(k, 0) + 1
    return out


def main():
    specs = sys.argv[1:] or ["base"]
    tags = [x.split(":")[0] for x in specs]
    pinned = [x.split(":")[1] if ":" in x else None for x in specs]
    same_all = True
    for ts in (("configured", "latest") if not all(pinned) else ("pinned",)):
        for game, name in GAMES:
            runs = [load(t, game, p or ts) for t, p in zip(tags, pinned)]
            if not all(runs):
                print(f"{name} [{ts}]: missing {[t for t, r in zip(tags, runs) if not r]}")
                same_all = False
                continue
            heads = "; ".join(f"{t}: {r['x64']['banner'].split('Version ')[-1]}"
                              + (f", x86 {r['x86']['banner'].split('Version ')[-1]}" if r.get("x86") else "")
                              for t, r in zip(tags, runs))
            print(f"{name} [{ts}] {heads}  {counts(runs[0])}")
            if len(runs) < 2:
                continue
            a, b = runs
            n = diffs = 0
            for kind in ("raw", "readable"):
                if set(a[kind]) != set(b[kind]):
                    print(f"  {kind}: different function sets")
                    diffs += 1
                for addr in a[kind]:
                    x, y = masked(game, addr, a[kind][addr]), masked(game, addr, b[kind].get(addr, {}))
                    n += 1
                    if x != y:
                        diffs += 1
                        changed = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
                        print(f"  DIFF {kind} {addr}: {changed}")
                        for k in [c for c in KEY if c in changed] or changed[:3]:
                            print(f"    {k}: {json.dumps(x.get(k))[:300]}  ->  {json.dumps(y.get(k))[:300]}")
            noisy = sum(1 for k in ("raw", "readable") for ad in a[k] if (game, ad) in NOISE and
                        {q: v for q, v in a[k][ad].items() if q != "seconds"} != {q: v for q, v in b[k][ad].items() if q != "seconds"})
            print(f"  {n} judgements compared, {diffs} differ ({noisy} more differ only in masked noise)")
            same_all &= diffs == 0
    if len(tags) == 2:
        print("ALL IDENTICAL" if same_all else "DIFFERENCES FOUND")


if __name__ == "__main__":
    main()
