"""A warm agent check (row 24 of the second waste audit): the agent's `python check.py` runs the same check command
as before, in a new process as before, but one started and loaded ahead of time, so the check does not wait for
Python and the pipeline's imports.

session() runs around an agent's session (core/agent.run_attempt). It starts one "spare": a Python process that
imports what the session's last check imported (the first spare only the basics), then waits. check.py
(core/warm_client, the address in the session's environment) sends its command line; the spare runs the tool's main
exactly as `python TOOL ARGS` would (sys.argv, sys.path[0] the tool's folder, and check.py's own environment and
working folder), its output and exit code go back, and it ends. The next spare starts then. A spare serves one check
only, so nothing a check computes is kept for the next: only code is loaded ahead.

Refused, so check.py runs the check cold as before (subprocess.call): a command that is not an agent check of this
workspace (a tool outside TOOL, no --agent, another --project), a spare that died or is not ready, or a
project.json changed since the spare loaded it (core/paths reads it when imported). Config "warm_check": false
turns it off.

The spare: python -c SPARE TOOL MODULES_FILE (core.warm_check.spare, the tool's folder first on sys.path).
"""
import contextlib
import hashlib
import json
import os
import pathlib
import secrets
import socket
import shutil
import subprocess
import sys
import tempfile
import threading

TOOL = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ("decomp.py", "refine.py")  # the agent checks: core/agent.CHECK_LAUNCHER, core/readable_sandbox.CHECK_LAUNCHER
CHECK_S = 3600  # a check's bound here (the check bounds its own build and runner far below this)
SPARE = "import sys; sys.path[0] = sys.argv[1]; from core.warm_check import spare; sys.exit(spare(sys.argv[2]))"


def _fingerprint():
    try:
        return hashlib.sha256((pathlib.Path(os.environ["DECOMP_PROJECT"]) / "project.json").read_bytes()).hexdigest()
    except (KeyError, OSError):
        return None


def spare(modules_file):
    """The spare's life: load, say ready, run one check, record what it imported, end."""
    loaded = _fingerprint()
    try:
        names = json.loads(pathlib.Path(modules_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        names = []
    import importlib.util
    for name, file in names:  # the last check's modules, each only from the file that check loaded it from
        try:
            spec = importlib.util.find_spec(name)
            if spec is not None and spec.origin == file:
                __import__(name)
        except Exception:  # noqa: BLE001  (one the check imports after setting up its path: it loads then)
            pass
    out = sys.stdout
    out.write("READY\n")
    out.flush()
    line = sys.stdin.readline()
    if not line:
        return 0
    req = json.loads(line)
    if _fingerprint() != loaded:
        out.write("STALE\n")
        out.flush()
        return 0
    out.write("GO\n")
    out.flush()
    sys.stdin = open(os.devnull, encoding="utf-8")  # as a check run from check.py: no input
    os.environ.clear()  # check.py's environment and folder, as a cold check inherits them
    os.environ.update(req["env"])
    os.chdir(req["cwd"])
    import runpy
    sys.argv = list(req["argv"][1:])
    sys.path[0] = os.path.dirname(os.path.abspath(sys.argv[0]))  # `python TOOL/decomp.py`: the script's folder
    rc = 0
    try:
        runpy.run_path(req["argv"][1], run_name="__main__")
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if not isinstance(e.code, (int, type(None))):
            print(e.code, file=sys.stderr)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            mods = [[m, getattr(getattr(sys.modules[m], "__spec__", None), "origin", None)]
                    for m in list(sys.modules) if m not in ("__main__", "__mp_main__")]
            pathlib.Path(modules_file).write_text(json.dumps(mods), encoding="utf-8")
        except OSError:
            pass
    return rc


class Session:
    """One agent session's spares and the local socket check.py reaches them on."""

    def __init__(self, env, cwd, project, tool_dir=TOOL):
        self.env, self.cwd, self.project = dict(env), str(cwd), pathlib.Path(project).resolve()
        self.tool_dir = pathlib.Path(tool_dir).resolve()  # the agent checks' tools are here (a test's own folder)
        self.token = secrets.token_hex(16)
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="dp_warm_"))
        self.modules = self.dir / "modules.json"
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.proc, self.closed = None, False
        self.lock = threading.Lock()
        self.served = self.refused = 0
        self._start()
        threading.Thread(target=self._serve, daemon=True).start()

    def _start(self):
        env = dict(self.env, DECOMP_PROJECT=str(self.project))
        env.pop("DP_WARM_CHECK", None)
        self.proc = subprocess.Popen([sys.executable, "-c", SPARE, str(TOOL), str(self.modules)],
                                     cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        self.ready = threading.Event()
        threading.Thread(target=self._wait_ready, args=(self.proc, self.ready), daemon=True).start()

    @staticmethod
    def _wait_ready(proc, ready):
        if proc.stdout.readline().rstrip() == b"READY":  # its text output: "\r\n" on Windows
            ready.set()

    def env_vars(self):
        return {"DP_WARM_CHECK": f"{self.port}:{self.token}"}

    def allowed(self, req):
        argv = req.get("argv") or []
        if (req.get("token") != self.token or len(argv) < 4 or "--agent" not in argv
                or not isinstance(req.get("env"), dict) or not req.get("cwd")):
            return False
        tool = pathlib.Path(argv[1]).resolve()
        if tool.parent != self.tool_dir or tool.name not in TOOLS:
            return False
        try:
            return pathlib.Path(argv[argv.index("--project") + 1]).resolve() == self.project
        except (ValueError, IndexError):
            return False

    def _serve(self):
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    self._one(conn)
                except OSError:
                    pass

    def _one(self, conn):
        f = conn.makefile("rb")
        try:
            req = json.loads(f.readline() or b"{}")
        except ValueError:
            req = {}
        got = self._run(req) if self.allowed(req) else None
        if got is None:
            self.refused += 1
            conn.sendall(b'{"ok": false}\n')
            return
        rc, out = got
        self.served += 1
        conn.sendall((json.dumps({"ok": True, "rc": rc, "n": len(out)}) + "\n").encode() + out)

    def _run(self, req):
        """(exit code, output) of the check on the spare, or None (refused: check.py runs it cold); then the next
        spare starts."""
        with self.lock:
            proc, ready = self.proc, self.ready
            if self.closed or not ready.is_set() or proc.poll() is not None:
                return None
            try:
                proc.stdin.write((json.dumps(req) + "\n").encode())
                proc.stdin.close()
                if proc.stdout.readline().rstrip() != b"GO":
                    return None
                out = []
                t = threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True)
                t.start()
                t.join(CHECK_S)
                if t.is_alive():
                    from . import paths
                    paths.kill_tree(proc)
                    return None
                return proc.wait(timeout=60), out[0]
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return None
            finally:
                _close(proc)
                if not self.closed:
                    self._start()

    def close(self):
        """Ends the spare: an idle one on its empty input; one still running a check (the session ended under it, as
        a cold check ends with the session's process tree) killed with its tree."""
        self.closed = True
        with contextlib.suppress(OSError):
            self.sock.close()
        proc = self.proc
        if proc and proc.poll() is None:
            with contextlib.suppress(OSError, ValueError):
                proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                from . import paths
                paths.kill_tree(proc)
        with self.lock:  # the serving thread is done with it
            if proc:
                _close(proc)
        shutil.rmtree(self.dir, ignore_errors=True)


def _close(proc):
    for s in (proc.stdin, proc.stdout):
        with contextlib.suppress(OSError, ValueError):
            s.close()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


@contextlib.contextmanager
def session(cfg, env, cwd, project):
    """The extra environment for an agent's session ({} when config "warm_check" is false or no spare could start);
    the spares run in env and cwd, as the session's own commands do."""
    if cfg.get("warm_check") is False:
        yield {}
        return
    try:
        s = Session(env, cwd, project)
    except OSError:
        yield {}
        return
    try:
        yield s.env_vars()
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(spare(sys.argv[1]))
