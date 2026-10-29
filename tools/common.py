"""Shared helpers for the command-line tools in tools/ (not a tool itself)."""
import json
import os
import pathlib
import re
import sys
import urllib.request

TOOL = pathlib.Path(__file__).resolve().parents[1]  # DecompPipeline/
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))


def workspace(d):
    """A project workspace folder (with project.json), resolved; exits with a message otherwise."""
    p = pathlib.Path(d).resolve()
    if not (p / "project.json").exists():
        sys.exit(f"{p} is not a project workspace (no project.json)")
    return p


def use_project(d):
    """Point core.paths at workspace d (call before importing core); returns the resolved folder."""
    p = workspace(d)
    os.environ["DECOMP_PROJECT"] = str(p)
    return p


def project_json(ws):
    return json.loads((pathlib.Path(ws) / "project.json").read_text(encoding="utf-8"))


def write_project_json(pj, raw, data):
    """Write data back to project.json pj, read earlier as raw: the file's own indent (that of its first indented
    line), line endings and ASCII escaping are kept, so an --apply edit changes only the lines it adds."""
    m = re.search(r"\n([ \t]+)\S", raw)
    text = json.dumps(data, indent=m.group(1) if m else 2, ensure_ascii=raw.isascii()) + "\n"
    pathlib.Path(pj).write_bytes((text.replace("\n", "\r\n") if "\r\n" in raw else text).encode("utf-8"))


def norm_addr(a):
    a = a.strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{1,8}", a):
        raise ValueError(f"not an address: {a!r}")
    return a.rjust(8, "0")


def expand_addrs(items):
    """Addresses from the command line; @FILE reads one or more per line (keeps hex lists off the command line,
    which Windows Defender flags). '#' starts a comment in a file; CRLF or LF, with or without a BOM. Each address
    once, in first-seen order; the number of duplicates dropped goes to stderr."""
    out = []
    for it in items or []:
        if it.startswith("@"):
            for line in pathlib.Path(it[1:]).read_text(encoding="utf-8-sig").splitlines():
                out += line.split("#", 1)[0].replace(",", " ").split()
        else:
            out.append(it)
    addrs = [norm_addr(a) for a in out]
    uniq = list(dict.fromkeys(addrs))
    if len(uniq) < len(addrs):
        print(f"addresses: {len(addrs)} listed, {len(addrs) - len(uniq)} duplicate(s) dropped, {len(uniq)} unique",
              file=sys.stderr, flush=True)
    return uniq


def api(port, path, body=None, timeout=60):
    """JSON call to a running pipeline server on 127.0.0.1:port."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def port_bound(port):
    """False only when no TCP socket on this machine has local port `port` (so no server can answer there); True
    when one has, or when the sockets cannot be listed. It spares server_state the refused connect, which Windows
    retries for 2 s."""
    try:
        import psutil
        return any(c.laddr and c.laddr.port == int(port) for c in psutil.net_connections("tcp"))
    except Exception:  # noqa: BLE001  (no psutil, or not allowed to list: ask the port as before)
        return True


def server_state(ws, timeout=5):
    """The /api/state of the server running this workspace, or None when none answers on its port (or a
    different project does)."""
    cfg = project_json(ws)
    port = cfg.get("port")
    if not port or not port_bound(port):
        return None
    try:
        st = api(port, "/api/state", timeout=timeout)
    except OSError:
        return None
    return st if st.get("project") == cfg.get("name") else None
