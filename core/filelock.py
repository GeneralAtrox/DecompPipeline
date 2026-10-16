"""Cross-process locking for the state files more than one process writes (the ledgers, systems.json, usage.json).

A read-modify-write holds `locked(path)`: an exclusive lock on `<path>.lock` beside the file (msvcrt on Windows,
fcntl elsewhere), so two processes (a refine run and a tool, two refine runs, the server and a tool) never lose each
other's entries. Within one process a per-file reentrant lock serialises threads, and a thread may nest `locked` on
the same file. The wait is bounded: past `wait` seconds it raises LockTimeout naming the lock file. The operating
system drops the lock when its process exits, so a leftover .lock file is normal and never blocks anyone.

Every write is atomic: a temporary file unique to the process and thread, then os.replace. A reader never sees a
half-written file, with or without the lock. On Windows the replace fails while another process has the file open
without delete sharing (a monitor reading it), and opening the file fails for a moment while another process
replaces it ([Errno 13]): both are retried with a short backoff for up to RETRY_S seconds (read_text, write_atomic),
then raise PermissionError naming the file.

`claim(lock)` (batch 21) is a different tool: exclusive ownership of one thing (a function, core/fn_claim) for a long
stretch of work, against other processes and other threads alike, with an owner record for the rival's `busy`
message; stale_claims finds and clears the records their dead owners left.
"""
import contextlib
import copy
import json
import os
import pathlib
import threading
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl

WAIT_S = 60.0
POLL_S = 0.02
RETRY_S = 5.0  # how long a refused open or replace is retried (Windows sharing: a reader or a replace in flight)


class LockTimeout(RuntimeError):
    pass


_GUARD = threading.Lock()
_HELD = {}  # lock path -> {"rlock": RLock, "depth": int, "fd": int or None}


def _key(lock):
    k = os.path.abspath(lock)
    return os.path.normcase(k)


def _try_lock(fd):
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd):
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _timeout(lock, wait):
    return LockTimeout(f"{lock}: still locked by another process after {wait:g} s (a writer of "
                       f"{pathlib.Path(lock).name[:-5]} is stuck or very slow; the lock goes away when that process "
                       "exits, the .lock file itself is harmless)")


@contextlib.contextmanager
def locked(path, wait=WAIT_S):
    """Hold the cross-process lock of `path` (the file itself need not exist)."""
    lock = str(path) + ".lock"
    with _GUARD:
        entry = _HELD.setdefault(_key(lock), {"rlock": threading.RLock(), "depth": 0, "fd": None})
    deadline = time.monotonic() + wait
    if not entry["rlock"].acquire(timeout=max(wait, 0)):
        raise _timeout(lock, wait)
    try:
        if entry["depth"] == 0:
            pathlib.Path(lock).parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o666)
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise _timeout(lock, wait)
                time.sleep(POLL_S)
            entry["fd"] = fd
        entry["depth"] += 1
        try:
            yield
        finally:
            entry["depth"] -= 1
            if entry["depth"] == 0:
                fd, entry["fd"] = entry["fd"], None
                try:
                    _unlock(fd)
                finally:
                    os.close(fd)
    finally:
        entry["rlock"].release()


def retry_refused(fn, path, what, limit=None):
    """fn() retried while Windows refuses it (PermissionError: another process has the file open, or is replacing
    it), with a backoff from 10 ms to 250 ms for up to `limit` (RETRY_S) seconds; then PermissionError naming path."""
    limit = RETRY_S if limit is None else limit
    deadline, delay = time.monotonic() + limit, 0.01
    while True:
        try:
            return fn()
        except PermissionError as e:
            if time.monotonic() >= deadline:
                raise PermissionError(e.errno, f"{what} {path}: still refused after {limit:g} s (another process "
                                      f"keeps the file open without sharing, or keeps replacing it): {e}") from e
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def read_text(path):
    """The file's text; an open Windows refuses for a moment (a replace in flight) is retried (retry_refused)."""
    path = pathlib.Path(path)
    return retry_refused(lambda: path.read_text(encoding="utf-8"), path, "reading")


def write_atomic(path, text):
    """Replace `path` with `text` in one step (unique temporary file, then os.replace, retried while refused)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        retry_refused(lambda: os.replace(tmp, path), path, "replacing")
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(path, data, indent=2):
    write_atomic(path, json.dumps(data, indent=indent))


def read_json(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(read_text(path))


def update_json(path, fn, default=None, wait=WAIT_S, indent=2):
    """Locked read-modify-write: fn(data) changes the freshly loaded data (or a copy of `default`) in place; the
    result is written atomically. Returns (the data written, fn's return value)."""
    with locked(path, wait):
        data = read_json(path, default)
        ret = fn(data)
        write_json(path, data, indent)
        return data, ret


# Claims (batch 21): exclusive ownership of one thing (a function: core/fn_claim) across processes AND threads, for a
# long stretch of work. Unlike `locked`, another thread of the same process is a rival too; only the owning thread may
# nest. The OS lock on `<name>.lock` is the truth (the system drops it when its process dies); `<name>.owner` (JSON:
# pid, purpose, since, command) only names the owner for a rival's `busy` message. An owner file whose lock nobody
# holds is stale (its process died): stale_claims reports it and clears it while holding the lock.

class Busy(RuntimeError):
    """The claim is held by another process or thread past the wait; str() names the owner."""


_CLAIMS = {}  # lock key -> {"thread": ident, "depth": int, "fd": int or None}


def owner_of(lock):
    """The owner record of a claim's lock file (a dict), or None."""
    try:
        return read_json(pathlib.Path(str(lock)[:-5] + ".owner"))
    except (OSError, ValueError):
        return None


def describe(rec):
    if not rec:
        return "owner unknown (claimed a moment ago, or by an older tool)"
    return (f"pid {rec.get('pid')} ({rec.get('purpose', '?')}) since {rec.get('since', '?')}"
            + (f": {rec['command']}" if rec.get("command") else ""))


def _busy(lock, wait, other_thread):
    who = "another thread of this process" if other_thread else describe(owner_of(lock))
    return Busy(f"busy: {pathlib.Path(lock).stem} is held by {who} (waited {wait:g} s)")


@contextlib.contextmanager
def claim(lock, wait=0.0, owner=None):
    """Hold the claim of `lock` (a path ending in .lock) for the block; wait up to `wait` seconds for a rival, then
    raise Busy. `owner` (dict) is written to the .owner file beside it while the claim is held."""
    lock, key, me = str(lock), _key(str(lock)), threading.get_ident()
    deadline = time.monotonic() + max(wait, 0)
    while True:
        with _GUARD:
            entry = _CLAIMS.get(key)
            if entry is None:
                entry = _CLAIMS[key] = {"thread": me, "depth": 0, "fd": None}
            if entry["thread"] == me:
                break
        if time.monotonic() >= deadline:
            raise _busy(lock, wait, True)
        time.sleep(POLL_S)
    owner_file = pathlib.Path(lock[:-5] + ".owner")
    try:
        if entry["depth"] == 0:
            pathlib.Path(lock).parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o666)
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise _busy(lock, wait, False)
                time.sleep(POLL_S)
            entry["fd"] = fd
            rec = {"pid": os.getpid(), "since": time.strftime("%Y-%m-%dT%H:%M:%S"), **(owner or {})}
            try:
                write_json(owner_file, rec, indent=None)
            except OSError:  # the record only names the owner for a rival's message; the OS lock is the claim
                pass
        entry["depth"] += 1
    except BaseException:
        if entry["depth"] == 0:
            with _GUARD:
                _CLAIMS.pop(key, None)
        raise
    try:
        yield
    finally:
        entry["depth"] -= 1
        if entry["depth"] == 0:
            fd = entry["fd"]
            try:
                owner_file.unlink(missing_ok=True)
            except OSError:
                pass  # left behind: stale_claims clears it once nobody holds the lock
            try:
                _unlock(fd)
            finally:
                os.close(fd)
                with _GUARD:
                    _CLAIMS.pop(key, None)


def pid_alive(pid):
    """Whether a process with this id runs (reports only: the OS lock decides whether a claim is held)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stale_claims(folder, clear=True):
    """({name: owner record} of the stale claims under folder: an owner file whose lock nobody holds, its process
    gone; each is removed while this process holds the lock when clear), ({name: record} of the claims held now)."""
    stale, held = {}, {}
    folder = pathlib.Path(folder)
    for own in sorted(folder.glob("*.owner")) if folder.is_dir() else []:
        lock, rec = str(own)[:-6] + ".lock", owner_of(str(own)[:-6] + ".lock") or {}
        try:
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o666)
        except OSError:
            continue
        try:
            if not _try_lock(fd):
                held[own.stem] = rec
                continue
            try:
                stale[own.stem] = dict(rec, alive=pid_alive(rec.get("pid")))
                if clear:
                    own.unlink(missing_ok=True)
            finally:
                _unlock(fd)
        finally:
            os.close(fd)
    return stale, held
