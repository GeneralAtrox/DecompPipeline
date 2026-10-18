"""Local web UI + JSON API (127.0.0.1 only)."""
import json
import re
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import paths

UI = paths.UI / "index.html"
USAGE_STALE_S = 1800  # while the page is open and no worker is running, re-read usage this often


def port_taken(port, name):
    """Why `decomp.py serve` must not start on `port`, or None. Checked before the Orchestrator exists (it marks the
    ledger's running functions stopped): Spawn's sstma08 once had Power Stone 2's port, and a driver that found a
    server answering there switched that project's loop and watcher off. A pipeline server that answers names its
    project; anything else holding the port is another program."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
            st = json.load(r)
    except (OSError, ValueError):
        st = None
    other = st.get("project") if isinstance(st, dict) else None
    if other == name:
        return f"{name}'s server is already running on port {port}"
    if other:
        return f"port {port} is served by another project ({other}): give this project.json its own \"port\""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError as e:
        return f"port {port} is in use by another program ({e.strerror or e})"
    finally:
        s.close()
    return None


def serve(orch, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self.send(200, UI.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                if not orch.workers and orch.usage.age_s() > USAGE_STALE_S:
                    orch.usage.probe_async(orch.cfg, orch.log)
                return self.send(200, orch.state())
            m = re.fullmatch(r"/api/prompt/([0-9a-fA-F]{1,8})", self.path)
            if m:
                return self.send(200, orch.agent_prompt(m.group(1)))
            m = re.fullmatch(r"/api/attempt/([0-9a-fA-F]{8})/(\d+)", self.path)
            if m:
                rec = orch.ledger["functions"].get(m.group(1), {})
                for a in rec.get("attempts", []):
                    if a["n"] == int(m.group(2)):
                        return self.send(200, a)
                return self.send(404, {"error": "no such attempt"})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self.send(403, {"error": "cross-origin request refused"})
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/watcher/tool":  # wtool.py, authenticated by the session token
                try:
                    return self.send(200, orch.watcher.tool(body.get("token"), body.get("kind")))
                except PermissionError as e:
                    return self.send(403, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    return self.send(409, {"error": str(e)})
            try:
                if self.path in ("/api/start", "/api/investigate"):
                    orch.start(body.get("workers") or 1, body.get("addr") or None)
                elif self.path == "/api/usage/refresh":
                    orch.usage.probe_async(orch.cfg, orch.log)
                elif self.path == "/api/watcher":
                    orch.watcher.set_enabled(body.get("enabled"))
                elif self.path == "/api/loop":
                    orch.set_loop(body.get("enabled"))
                elif self.path == "/api/requeue":
                    return self.send(200, {"requeued": orch.requeue(body.get("addrs") or [], body.get("note") or "requeued")})
                elif self.path == "/api/stop":
                    if body.get("worker") is not None:
                        orch.stop_worker(body["worker"])
                    else:
                        orch.stop_all()
                else:
                    return self.send(404, {"error": "not found"})
            except Exception as e:  # noqa: BLE001
                return self.send(409, {"error": str(e)})
            self.send(200, {"ok": True})

    ThreadingHTTPServer.allow_reuse_address = False  # on Windows, reuse would let a second server share the port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{orch.cfg.get('name')} decomp monitor ({paths.show(paths.ROOT)}): http://127.0.0.1:{port}")
    httpd.serve_forever()
