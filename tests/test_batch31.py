"""Batch 31: `decomp.py serve` refuses a port that is already taken (core/server.port_taken), checked before the
Orchestrator touches the ledger: this project's own server already running, another project's server answering
there (Spawn's sstma08 once had Power Stone 2's port), another program holding it; a free port passes.
Synthetic servers on 127.0.0.1. Run: python -W error tests/test_batch31.py (also from test_tooling.py).
"""
import json
import pathlib
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import test_sync  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import server  # noqa: E402


def fake_server(project):
    """A server answering /api/state like a pipeline server of `project`, on a free port."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = json.dumps({"project": project}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class PortTaken(unittest.TestCase):
    def serving(self, project):
        httpd = fake_server(project)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def test_same_project_running(self):
        why = server.port_taken(self.serving("Spawn sstma08"), "Spawn sstma08")
        self.assertIn("already running", why)

    def test_other_project(self):
        why = server.port_taken(self.serving("Power Stone 2"), "Spawn sstma08")
        self.assertIn("another project (Power Stone 2)", why)

    def test_other_program(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # bound, not listening: no server answers
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", 0))
        self.assertIn("in use by another program", server.port_taken(s.getsockname()[1], "Spawn sstma08"))

    def test_free_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertIsNone(server.port_taken(port, "Spawn sstma08"))


if __name__ == "__main__":
    unittest.main()
