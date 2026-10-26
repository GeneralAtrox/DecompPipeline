"""laptop2: harness comparisons of the full re-proof. off: master's flags-off harnesses (land_off) against the rebased
branch's (rb_off), byte for byte (meta.json without oracle_stamp, which hashes the scripts). on: which of rb_on's differ
from land_off (vectors.txt, expected.txt, meta.json), and the functions whose harness is refused or short in either.
Writes proof/harness_cmp.json."""
import json
import os
import pathlib

W = pathlib.Path(os.environ["DP_PREWAVE_DIR"])  # the work folder (on laptop2: its dpwork\prewave)
KEYS = ("grandia", "skies", "tc", "ps2", "spawn-main")


def files(h):
    out = {}
    for f in sorted(h.iterdir()) if h.is_dir() else []:
        b = f.read_bytes()
        if f.name == "meta.json":
            m = json.loads(b)
            m.pop("oracle_stamp", None)
            b = json.dumps(m, sort_keys=True).encode()
        out[f.name] = b
    return out


def hdir(tree, key, cfg):
    return W / f"repo-{tree}" / "tests" / ".work" / f"probe-{key}_{tree}_{cfg}" / "harness"


res = {}
for key in KEYS:
    a, b, c = hdir("land", key, "off"), hdir("rb", key, "off"), hdir("rb", key, "on")
    fa = sorted(x.name for x in a.iterdir())
    r = {"functions": len(fa), "off_same": 0, "off_diff": [], "on_diff": [], "on_same": 0}
    for f in fa:
        A, B, C = files(a / f), files(b / f), files(c / f)
        if A == B:
            r["off_same"] += 1
        else:
            r["off_diff"].append([f, sorted(k for k in set(A) | set(B) if A.get(k) != B.get(k))])
        if A == C:
            r["on_same"] += 1
        else:
            r["on_diff"].append([f, sorted(k for k in set(A) | set(C) if A.get(k) != C.get(k))])
    res[key] = r
    print(key, r["functions"], "off same", r["off_same"], "off diff", len(r["off_diff"]), r["off_diff"][:5],
          "| on diff", len(r["on_diff"]))
(W / "proof" / "harness_cmp.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
