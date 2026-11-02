"""Planted wrong answers that the lockstep report and the scorecard's gate status once passed (docs/proofs/check-audit.md).
Here because tests/test_lockstep.py and tests/test_scorecard.py are over the kit's size cap and may only shrink."""
import contextlib
import io
import pathlib
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "port" / "tools"), str(TOOL / "tools"), str(TOOL)]

import lockstep  # noqa: E402
import scorecard as sc  # noqa: E402


class LockstepExcuses(unittest.TestCase):
    ITEMS = [{"name": "label", "words": 1, "addr": "0", "flags": {"label", "time"}},
             {"name": "frames", "words": 1, "addr": "4", "flags": {"frames"}},
             {"name": "x", "words": 1, "addr": "8", "flags": set()}]

    def test_a_port_a_frame_off_is_not_excused(self):
        frames = [{"label": 100 + i, "jump": None, "draw": (100 + i, i, 0, 0, 0, 0, 0)} for i in range(200)]

        def verdict(port_state, emu_state=lambda i: i):
            emu = [(100 + i, 2 * i + 2, "%08x %08x %08x" % (100 + i, i, emu_state(i))) for i in range(200)]
            port = ((100 + i, i, "%08x %08x %08x" % (100 + i, i, port_state(i))) for i in range(200))
            res = lockstep.compare(self.ITEMS, emu, frames, port)
            with contextlib.redirect_stdout(io.StringIO()):
                return res, lockstep.report(res, emu)
        res, rc = verdict(lambda i: i)
        self.assertEqual((res["drawn"], rc), (0, 0))
        res, rc = verdict(lambda i: i + 1)                               # a whole frame ahead: excused every frame
        self.assertEqual((res["first"], rc), (None, 2))
        res, rc = verdict(lambda i: i, lambda i: i - (i == 50))          # one sample within the draw: rare, a pass
        self.assertEqual((res["drawn"], rc), (1, 0))
        res, rc = verdict(lambda i: i, lambda i: i - (i % 10 == 0))      # 10%: too many to call a pass
        self.assertEqual((res["first"], res["drawn"], rc), (None, 19, 2))

    def test_nothing_compared_is_no_verdict(self):
        res = dict(frames=0, loads=0, load_diffs=0, drawn=0, known=0, traced=5, last=4, driven=5, first=None,
                   first_time=None, pointers=0, floats=0, float_max=0.0, time_diffs=[], diffs=[])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lockstep.report(res, []), 2)
            self.assertEqual(lockstep.report(dict(res, frames=99, loads=99, load_diffs=99), []), 2)
            self.assertEqual(lockstep.report(dict(res, frames=99, loads=1), []), 0)


class GateStatus(unittest.TestCase):
    def test_nothing_measured_is_never_green(self):
        self.assertEqual(sc.gate_status([sc.check("a", "info")]), "amber")
        self.assertEqual(sc.gate_status([]), "amber")


if __name__ == "__main__":
    unittest.main()
