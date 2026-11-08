"""tools/prepare_list.py --serve with a served Ghidra that cannot start (core/ghidra_server.StartError): the first start
failure stops the run, and the server's start-up raises StartError at Ghidra's abort line. Moved from
tests/test_harness_reuse.py (PrepareList) under the 400-line cap. Ghidra is never run.
Run: python -W error tests/test_prepare_list_serve.py
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import prepare_list  # noqa: E402


class PrepareListServe(unittest.TestCase):
    def test_server_start_failure_stops_the_run(self):
        """A served Ghidra that cannot start (2026-11-06, build box A: a workspace under .work, "Path element starting with
        '.' is not permitted") failed every function of a 400-function list, one JVM start each, about a minute
        apiece: the first start failure stops the run, the rest listed as not tried."""
        from core import ghidra_server
        made = []

        def prepare(a):
            made.append(a)
            raise ghidra_server.StartError("Ghidra server (slot 1) aborted: Path element starting with '.'\nmore")

        addrs = [f"8000{k}000" for k in range(1, 9)]
        rows = prepare_list.run({}, addrs, prepare, lambda a: None, 1, log=lambda m: None)
        self.assertEqual(made, addrs[:1])
        self.assertEqual({r["state"] for r in rows}, {"error"})
        self.assertTrue(all(r["detail"].startswith("not tried: StartError: ") for r in rows[1:]), rows[1])

    def test_server_abort_line_is_a_start_error(self):
        """The server's start-up raises StartError at Ghidra's abort line, not after the kill timeout, and on an exit
        before TcServe is ready; a request's failure stays a RuntimeError (one function's)."""
        import queue
        import time
        from core import ghidra_server

        def fake(lines):
            srv = ghidra_server.Server.__new__(ghidra_server.Server)
            srv.slot, srv.heap, srv.lines = 1, [], queue.Queue()
            for line in lines:
                srv.lines.put(line)
            return srv
        t0 = time.monotonic()
        with self.assertRaises(ghidra_server.StartError) as e:
            fake(["INFO HEADLESS: execution starts\n", "ERROR (HeadlessAnalyzer) Abort due to Headless analyzer "
                  "error: Path element starting with '.' is not permitted java.lang.IllegalArgument\n"]).ready(600)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertIn("Path element starting with '.'", str(e.exception))
        with self.assertRaises(ghidra_server.StartError):
            fake(["INFO starting\n", None]).ready(600)
        with self.assertRaises(RuntimeError) as e:
            fake(["TcServe: ready\n", None]).ready(600) or fake([None])._until(lambda x: False, 600, None, None, [])
        self.assertNotIsInstance(e.exception, ghidra_server.StartError)


if __name__ == "__main__":
    unittest.main()
