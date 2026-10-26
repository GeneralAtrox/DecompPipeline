"""The two fixed costs a fresh tool process paid before its first prepare:
1. ghidra_broker.processes() takes the parents from one whole-machine map: the same (pid, ppid, name) as psutil's
   per-process ppid and name, and in a fresh process in well under a second (it was 9 s for about 400 processes);
   jvms() takes the 3-field rows as it took the old 4-field ones;
2. tools/common.server_state answers None at once when no TCP socket has the workspace's port (the refused connect
   Windows retries for 2 s is not made), and otherwise asks the port exactly as before: a server of this project, of
   another project, a listener that is not a server, and sockets that cannot be listed.
Live processes and loopback sockets only. Run: python -W error tests/test_broker_fast_start.py.
"""
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import ghidra_broker  # noqa: E402
import common  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def slow_processes():
    """The scan as it was: psutil's own ppid for each process (the answers processes() must keep)."""
    import psutil
    return {p.info["pid"]: (p.info["ppid"] or 0, (p.info["name"] or "").lower())
            for p in psutil.process_iter(["pid", "ppid", "name"])}


class Processes(unittest.TestCase):
    def test_same_rows(self):
        fast = ghidra_broker.processes()
        slow = slow_processes()
        again = {r[0]: r for r in ghidra_broker.processes()}
        rows = {r[0]: r for r in fast}
        both = [pid for pid in rows if pid in slow and pid in again and again[pid] == rows[pid]]  # alive throughout
        self.assertGreater(len(both), 20)
        self.assertTrue(all(len(r) == 3 for r in fast))
        for pid in both:
            self.assertEqual(rows[pid][1:], slow[pid], pid)
        me = rows[__import__("os").getpid()]
        self.assertEqual(me[1], __import__("os").getppid())

    def test_fresh_process_is_fast(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from core import ghidra_broker as g; "
                "t = time.perf_counter(); n = len(g.processes()); print(n, time.perf_counter() - t)")
        out = subprocess.run([sys.executable, "-c", code, str(ROOT)], capture_output=True, text=True, timeout=120)
        n, took = out.stdout.split()
        self.assertGreater(int(n), 20)
        self.assertLess(float(took), 1.5, out.stdout)

    def test_without_the_map(self):  # a psutil without ppid_map: each process's own ppid, as before
        with mock.patch.object(ghidra_broker, "_parents", lambda: None):
            rows = {r[0]: r for r in ghidra_broker.processes()}
        slow = slow_processes()
        same = [pid for pid in rows if pid in slow]
        self.assertGreater(len(same), 20)
        self.assertGreater(sum(rows[pid][1:] == slow[pid] for pid in same), 0.9 * len(same))

    def test_jvms_takes_three_fields(self):
        procs = [(1, 0, "explorer.exe"), (2, 1, "python.exe"), (3, 2, "java.exe")]
        self.assertEqual(ghidra_broker.jvms(procs, is_ghidra=lambda pid: True), {3: [2, 1]})
        self.assertEqual(ghidra_broker.jvms([p + (0.0,) for p in procs], is_ghidra=lambda pid: True), {3: [2, 1]})


class Server:
    """A loopback HTTP server answering /api/state with {"project": name}."""
    def __init__(self, name):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"project": name, "workers": []}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerState(unittest.TestCase):
    def ws(self, port, name="Game"):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "project.json").write_text(json.dumps({"name": name, "port": port}), encoding="utf-8")
        return d

    def test_no_server_answers_at_once(self):
        ws = self.ws(free_port())
        with mock.patch.object(common, "api", side_effect=AssertionError("asked a port nobody has")):
            t = time.perf_counter()
            self.assertIsNone(common.server_state(ws))
            self.assertLess(time.perf_counter() - t, 0.5)
        with mock.patch.object(common, "port_bound", lambda port: True):  # the old way: the refused connect
            self.assertIsNone(common.server_state(ws))

    def test_a_server_answers_as_before(self):
        srv = Server("Game")
        try:
            self.assertEqual(common.server_state(self.ws(srv.port)), {"project": "Game", "workers": []})
            self.assertIsNone(common.server_state(self.ws(srv.port, name="Other")))  # another project's server
            with mock.patch("psutil.net_connections", side_effect=PermissionError):  # sockets not listed: ask
                self.assertEqual(common.server_state(self.ws(srv.port)), {"project": "Game", "workers": []})
        finally:
            srv.close()

    def test_a_listener_that_is_not_a_server(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        try:
            self.assertTrue(common.port_bound(s.getsockname()[1]))
            self.assertIsNone(common.server_state(self.ws(s.getsockname()[1]), timeout=2))
        finally:
            s.close()

    def test_no_port(self):
        self.assertIsNone(common.server_state(self.ws(None)))


if __name__ == "__main__":
    unittest.main()
