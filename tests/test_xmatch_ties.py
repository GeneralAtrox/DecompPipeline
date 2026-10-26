"""xmatch near matches: the same in every process, whatever PYTHONHASHSEED; never a worse ratio than most_common(5).
most_common(5) cut a vote tie by set order, which follows the per-process string hash seed: over the 30 recorded
workspace pairs two seeds gave 1286 different near matches.
1. match (in processes with 4 seeds): of six candidates tied on votes, the one with the best ratio wins although it
   ranks 6th by address, and of candidates tied on votes and ratio the lowest address wins;
2. best_near (bound pruning) == computing every ratio, on random word lists with many ties.
Run: python -W error tests/test_xmatch_ties.py (also from test_tooling.py).
"""
import difflib
import json
import os
import pathlib
import random
import subprocess
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from core import xmatch  # noqa: E402

NEW = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from core import xmatch
class Img:
    def __init__(self, fns):
        self.functions, self.n = {a: {} for a in fns}, fns
xmatch.normalize = lambda img, a: (tuple(img.n[a]), (), ())
new = Img({"8c020000": %r, "8c020100": %r})
old = Img(json.loads(sys.argv[2]))
print(json.dumps({a: m["from"] for a, m in xmatch.match(old, new).items()}))
"""


class Ties(unittest.TestCase):
    def test_same_in_every_process(self):
        old = {f"8c01{k:04x}": [1, 2, 3, 4] + [60 + 10 * k + j for j in range(6)] for k in range(5)}  # 1 vote, 0.4
        old["8c01ff00"] = [1, 2, 3, 4, 77, 6, 7, 77, 9, 10]  # 1 vote too, ratio 0.8: ranks 6th by address
        twin = list(range(21, 33))
        for k in range(8):  # 8 equal copies of a near twin of 8c020100: tied on votes (5) and ratio (0.833)
            old[f"8c03{k:04x}"] = twin[:10] + [99] + twin[11:]
        script = SCRIPT % (NEW, twin[:1] + [98] + twin[2:])
        got = set()
        for seed in ("0", "1", "2", "12345"):
            p = subprocess.run([sys.executable, "-c", script, str(TOOL), json.dumps(old)], capture_output=True,
                               text=True, timeout=60, env=dict(os.environ, PYTHONHASHSEED=seed))
            self.assertEqual(p.returncode, 0, p.stderr)
            got.add(p.stdout.strip())
        self.assertEqual(got, {json.dumps({"8c020000": "8c01ff00", "8c020100": "8c030000"})})

    def test_best_near_is_every_ratio(self):
        rnd = random.Random(7)
        for _ in range(300):
            words = tuple(rnd.choice(range(4)) for _ in range(rnd.randint(1, 14)))
            prior = {f"{a:08x}": (tuple(rnd.choice(range(4)) for _ in range(rnd.randint(1, 14))),)
                     for a in rnd.sample(range(64), rnd.randint(1, 12))}
            ranked = list(prior)
            rnd.shuffle(ranked)
            near_min = rnd.choice((0.0, 0.5, 0.6, 0.8))
            want = None
            for c in ranked:
                r = difflib.SequenceMatcher(None, prior[c][0], words, autojunk=False).ratio()
                if want is None or r > want[1]:
                    want = (c, r)
            want = want if want[1] >= near_min else None
            self.assertEqual(xmatch.best_near(prior, ranked, words, near_min), want)


if __name__ == "__main__":
    unittest.main()
