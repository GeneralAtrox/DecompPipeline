"""One Ghidra per slot (r15 prototype, config "ghidra_serve"): a long-lived headless Ghidra keeps a read-only slot's
program open and runs TcPrepare once per request (core/ghidra_serve/TcServe.java), instead of a new JVM, project
open and program open for every prepare (about 76% of a short prepare).

Which prepares: only slots 1.. (read-only copies). Slot 0 (the master, which saves the queued marks) starts a fresh
Ghidra as before. Staged prepares (gates, regress, probes: their own scripts folder, also read-only) only with
"staged": true; their scripts folder is part of the server's key.

A server is keyed by (project folder, scripts folder, the scripts' names, sizes and mtimes, the catalog stamp): a
change in any (a script sync, a slot copy refresh after a catalog change) restarts it. It is also restarted after
every failed request (a script that threw may leave a decompiler or emulator behind), after `max_requests`
requests, and stopped after `idle_s` seconds unused, so its memory comes back. Each request has a watchdog: past
ghidra_timeout_s (or a stop) the server's whole process tree is killed and the request fails as a fresh prepare
would ("Ghidra timeout").

The machine-wide queue (core/ghidra_broker): a server takes one Lease when it starts and holds it until it closes, so
its requests neither queue nor start a JVM (ghidra.slot takes no token for a served slot). Between requests the
reaper closes it, giving the token back, once the lease is contested (another waiter has waited `lease_s` seconds,
or waits while the server has been idle `yield_idle_s`; an idle-priority lease gives way to any worker at once).

stop(slot) / stop_all(): before a slot copy is refreshed (ghidra._refresh_copy), in all_slots(), and before a
whole-program run (setup, catalog, seed, graph, export): nothing may hold a project those change or copy.
"""
import atexit
import contextlib
import os
import pathlib
import queue
import re
import subprocess
import threading
import time

from . import ghidra_broker, paths, toolcfg

DEFAULTS = {"enabled": False, "staged": False, "max_requests": 200, "idle_s": 120, "lease_s": 30,
            "yield_idle_s": 2}
SERVE_DIR = pathlib.Path(__file__).resolve().parent / "ghidra_serve"
DONE = re.compile(r"TcServe: done (\d+) (ok|error) ?(.*?)\s*(\(GhidraScript\))?\s*$")
_servers = {}  # slot index -> Server
_LOCK = threading.RLock()
_reaper = []


def settings(cfg):
    return {**DEFAULTS, **((cfg or {}).get("ghidra_serve") or {})}


def serves(cfg, slot_index, staging):
    s = settings(cfg)
    return bool(s["enabled"]) and slot_index > 0 and (not staging or bool(s["staged"]))


def key_of(project, scripts, stamp):
    files = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                  for p in sorted(pathlib.Path(scripts).glob("*.java")))
    return str(project), str(scripts), files, stamp


def command(cfg, project, scripts):
    """The server's command line: headless Ghidra on the read-only project, TcServe as its only script."""
    exe = os.path.join(toolcfg.need(cfg, "ghidra_home"), "support",
                       "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
    cmd = [exe, str(project), paths.GHIDRA_NAME, "-process", paths.config()["binary_path"].name, "-noanalysis",
           "-readOnly", "-scriptPath", f"{scripts};{SERVE_DIR}", "-postScript", "TcServe.java"]
    if os.name == "nt":  # analyzeHeadless.bat splits an unquoted argument at ";": quote the script path
        cmd = subprocess.list2cmdline(cmd[:-3]) + f' "{cmd[-3]}" ' + subprocess.list2cmdline(cmd[-2:])
    return cmd


class Server:
    def __init__(self, cfg, slot, project, scripts, key, env, lease=None):
        self.slot, self.key, self.n, self.used, self.heap = slot, key, 0, time.monotonic(), []
        self.lease = lease  # the machine-wide token this server's JVM holds (core/ghidra_broker.Lease), or None
        self.settings = settings(cfg)
        self.busy = 0  # requests in run() (start-up included): the idle reaper never closes a busy server
        self.lines = queue.Queue()
        self.proc = subprocess.Popen(
            command(cfg, project, scripts), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        self.pump = threading.Thread(target=self._pump, daemon=True, name=f"ghidra-serve-{slot}")
        self.pump.start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def alive(self):
        return self.proc.poll() is None

    def _until(self, done, timeout, on_line, stop_flag, out):
        """Read lines (to on_line and out) until done(line) is true; raises on exit, timeout or stop."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line is None:
                raise RuntimeError(f"Ghidra server (slot {self.slot}) exited:\n" + "".join(out[-25:]))
            if line:
                out.append(line)
                if on_line:
                    on_line(line)
                if "TcServe: heap " in line:
                    self.heap.append(line.split("TcServe: heap ", 1)[1].split()[:2])
                if done(line):
                    return line
                if "Press any key to continue" in line:  # the launcher failed and waits on our stdin
                    raise RuntimeError(f"Ghidra server (slot {self.slot}) failed to start:\n" + "".join(out[-25:]))
            if time.monotonic() > deadline:
                raise RuntimeError("Ghidra timeout")
            if stop_flag is not None and stop_flag.is_set():
                raise RuntimeError("Ghidra stopped")

    def ready(self, timeout, on_line=None, stop_flag=None, out=None):
        self._until(lambda line: "TcServe: ready" in line, timeout, on_line, stop_flag, out if out is not None else [])

    def request(self, args, timeout, on_line=None, stop_flag=None):
        """TcPrepare with args: returns its output lines; raises RuntimeError on its failure, a timeout or a stop."""
        rid, out = self.n, []
        self.n += 1
        self.proc.stdin.write("REQ " + "\t".join([str(rid)] + [str(a) for a in args]) + "\n")
        self.proc.stdin.flush()
        line = self._until(lambda x: (m := DONE.search(x)) is not None and int(m[1]) == rid, timeout, on_line,
                           stop_flag, out)
        self.used = time.monotonic()
        m = DONE.search(line)
        if m[2] != "ok":
            raise RuntimeError(f"Ghidra failed (server, slot {self.slot}): {m[3]}\n" + "".join(out[-25:]))
        return out

    def close(self, wait=30):
        try:
            if self.alive():
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
                self.proc.wait(wait)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if self.alive():
            paths.kill_tree(self.proc)
        self.pump.join(10)  # the pipe ends with the process
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except OSError:
                pass
        if self.lease is not None:  # the JVM is gone: its token goes back to the queue
            self.lease.release()


def _current(srv, key, s):
    return bool(srv and srv.key == key and srv.n < int(s["max_requests"]) and srv.alive())


def run(cfg, slot, project, scripts, stamp, args, env, on_line=None, stop_flag=None):
    """TcPrepare args on slot's server (started, or restarted, as needed). Caller holds the slot. Returns the
    output (the server's start-up lines too when this request started it). A server that has to start first waits
    for a machine-wide Lease (core/ghidra_broker), kept for its whole life, unless the thread holds a token itself
    (it then counts the JVM); asked for outside _LOCK, which a token holder may need. A server that is not current
    is closed before that wait, so its own lease never stands in the way of its successor's."""
    s = settings(cfg)
    key = key_of(project, scripts, stamp)
    lease = None
    try:
        while True:
            with _LOCK:
                srv = _servers.get(slot)
                if srv is not None and not _current(srv, key, s):
                    _servers.pop(slot).close()
                    srv = None
                if srv is not None:
                    srv.busy += 1
                    fresh = False
                    break
                if lease is not None or ghidra_broker.holding() or not ghidra_broker.on(cfg):
                    srv = _servers[slot] = Server(cfg, slot, project, scripts, key, env, lease)
                    lease, fresh = None, True
                    srv.busy += 1
                    _start_reaper(s)
                    break
            lease = ghidra_broker.lease(cfg, ghidra_broker.current_prio(), f"server slot {slot}", stop_flag)
    finally:
        if lease is not None:  # not handed to a server (an error before it started)
            lease.release()
    return _run(srv, fresh, args, int(cfg["ghidra_timeout_s"]), on_line, stop_flag)


def _run(srv, fresh, args, timeout, on_line, stop_flag):
    out = []
    try:
        if fresh:
            srv.ready(timeout, on_line, stop_flag, out)
        out += srv.request(args, timeout, on_line, stop_flag)
    except BaseException:
        stop(srv.slot, wait=0)  # a failed, timed-out or stopped request: its server's process tree is killed now
        raise
    finally:
        with _LOCK:
            srv.busy -= 1
            srv.used = time.monotonic()
    give_back(srv.slot)  # another waiter's turn: between requests, never during one
    return "".join(out)


def give_back(slot):
    """Close slot's server, giving its Lease back, when it serves no request and the lease is contested
    (core/ghidra_broker.Lease.contested). The slot may be reserved: its holder's next request waits for a lease of
    its own (run). True when closed."""
    with _LOCK:
        srv = _servers.get(slot)
        if srv is None or srv.busy or srv.lease is None:
            return False
        st = srv.settings
        if not srv.lease.contested(time.monotonic() - srv.used, float(st["lease_s"]), float(st["yield_idle_s"])):
            return False
        _servers.pop(slot)
    srv.close()
    return True


def up(slot):
    """Whether slot's server runs (core/ghidra_broker: a request to it starts no JVM)."""
    with _LOCK:
        srv = _servers.get(slot)
        return bool(srv and srv.alive())


def stop(slot, wait=30):
    with _LOCK:
        srv = _servers.pop(slot, None)
    if srv:
        srv.close(wait)


def stop_all():
    with _LOCK:
        for slot in list(_servers):
            stop(slot)


def _start_reaper(s):
    if _reaper:
        return

    def reap():
        while True:
            time.sleep(1)
            st = settings({"ghidra_serve": s})
            idle = float(st["idle_s"])
            now = time.monotonic()
            with _LOCK:
                old = [k for k, v in _servers.items() if not v.busy and now - v.used > idle]
                rest = [k for k in _servers if k not in old]
            for k in old:
                _close_if_idle(k, idle)
            for k in rest:
                give_back(k)

    t = threading.Thread(target=reap, daemon=True, name="ghidra-serve-reaper")
    _reaper.append(t)
    t.start()


def _close_if_idle(slot, idle):
    """Only a server no request is using (busy, under _LOCK, covers callers that hold no slot: regress, probes);
    the slot is taken too, so a job holding it (ghidra.slot) keeps its server."""
    from . import ghidra
    if not ghidra.try_slot(slot):
        return
    try:
        with _LOCK:
            srv = _servers.get(slot)
            if not srv or srv.busy or time.monotonic() - srv.used <= idle:
                return
            _servers.pop(slot)
        srv.close()
    finally:
        ghidra.release_slot(slot)


atexit.register(stop_all)
