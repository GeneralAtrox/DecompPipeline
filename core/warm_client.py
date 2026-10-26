"""The agent's check.py, warm (core/warm_check): the check's command line goes to the session's ready check process
and its output and exit code come back. Standard library only, and imported by path, not as part of core: check.py
starts a new Python for every check, and this is all it loads.

run(argv) returns the exit code. When the session has no ready process (DP_WARM_CHECK unset), or it cannot be
reached or refuses, the check runs as it always did: subprocess.call(argv).
"""
import json
import os
import socket
import subprocess
import sys

ENV = "DP_WARM_CHECK"  # "<port>:<token>", set for the agent's session by core/warm_check.session


def _recv(s, n):
    out = bytearray()
    while len(out) < n:
        b = s.recv(min(1 << 16, n - len(out)))
        if not b:
            raise OSError("closed")
        out += b
    return bytes(out)


def warm(argv):
    """(exit code, output bytes) from the ready process, or None."""
    spec = os.environ.get(ENV, "")
    port, _, token = spec.partition(":")
    if not port.isdigit() or not token:
        return None
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=10) as s:
            s.settimeout(None)  # the check is bounded on the serving side
            s.sendall((json.dumps({"token": token, "argv": list(argv), "cwd": os.getcwd(),
                                   "env": dict(os.environ)}) + "\n").encode())
            head = bytearray()
            while not head.endswith(b"\n"):
                b = s.recv(1)
                if not b:
                    return None
                head += b
            reply = json.loads(head)
            if not reply.get("ok"):
                return None
            return int(reply["rc"]), _recv(s, int(reply["n"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run(argv):
    got = warm(argv)
    if got is None:
        return subprocess.call(argv)
    rc, out = got
    sys.stdout.flush()
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return rc
