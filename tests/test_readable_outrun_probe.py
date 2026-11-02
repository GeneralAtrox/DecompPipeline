"""A void * out-parameter is sized by the trace run: vectors_file must reach that run (core/readable_outrun)."""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import check, readable_outptr, readable_outrun, readable_stack  # noqa: E402

TARGET = 0x8c07bba0
LO = 0x8cff0000
GUEST = 0x8cffde00


def spec():
    """A callee's `void *` parameter in r5 (call record field 2), sized by the trace run."""
    fields = readable_outptr.Spec._fields
    values = {"addr": "8c07bba0", "base": "void", "reg": "r5", "field": 2, "size": "trace", "compared": True}
    return readable_outptr.Spec(*[values.get(f, "") for f in fields])


class Probe(unittest.TestCase):
    def run_it(self, arg, extents):
        rec = [TARGET] + [0] * 21
        rec[2] = arg
        exp = {0: {"calls": [rec]}}
        runner = mock.Mock()
        runner.available.return_value = True
        runner.run.return_value = "traced"
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "vectors.txt").write_text("V 0 dev" + chr(10) + "E" + chr(10), encoding="utf-8")
            build = d / "b"
            build.mkdir()
            with mock.patch.object(check, "parse_results", return_value=exp),                     mock.patch.object(readable_stack, "frame_lo", return_value=LO),                     mock.patch.object(readable_outrun, "raw_runs", return_value=runner),                     mock.patch.object(readable_outrun, "parse_ot", return_value=extents),                     mock.patch.object(readable_outrun, "original_bytes", return_value=None),                     mock.patch.object(readable_outrun, "with_lines",
                                      side_effect=lambda src, extra, dst, after_v=False: dst):
                path, n = readable_outrun.vectors_file({}, {}, d, [spec()], build, (), "8c000000")
            return path.name, n, runner

    def test_void_pointer_gets_its_size_from_the_trace_run(self):
        name, n, runner = self.run_it(GUEST, {(0, 0, "8c07bba0", 2): 16})
        self.assertEqual((name, n), ("vectors_out.txt", 1))
        runner.run.assert_called_once()

    def test_a_trace_that_reads_nothing_delivers_nothing(self):
        self.assertEqual(self.run_it(GUEST, {})[:2], ("vectors.txt", 0))

    def test_a_pointer_outside_the_stack_is_no_out_parameter(self):
        name, n, runner = self.run_it(0x8c100000, {})
        self.assertEqual((name, n), ("vectors.txt", 0))
        runner.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
