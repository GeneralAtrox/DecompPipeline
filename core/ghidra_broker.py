"""One Ghidra queue for every workspace on the machine (config "ghidra_broker"; slots 0, the default, turns it off).

Each workspace has its own Ghidra slots (ghidra.slot, config "ghidra_parallel"). With the broker on, a prepare also
takes a machine-wide token, so several games can run at once without more Ghidra JVMs than the machine holds:
  slots        JVMs allowed while RAM is under stop_at (the machine's slot rule: 2)
  third_below  one more JVM may start while RAM is under this percent (82)
  stop_at      no JVM starts at this percent of RAM in use or more (88)
  burst        opt-in, per workspace (project.json): up to this many JVMs while RAM is under burst_below (0: off)
  burst_below  the RAM percent under which burst applies (75)
  age_s        a waiter moves up one priority per age_s seconds waited, so none starves (120)
  by_ram       the cap from measured RAM headroom instead of third_below (off unless set; config.json sets it):
               every running Ghidra JVM may still grow to the largest one seen (its peak), every token held whose JVM
               has not started will take a peak; with that growth counted as in use already, one more JVM starts
               while it keeps RAM under fill_below (85: where work paused at the 95% stop resumes), up to max_jvms
               (0: cores / 4). The first `slots` JVMs may also start above fill_below, while they keep RAM under
               stop_line (95: the stop) and RAM is under stop_at, as before. No JVM starts that would take the
               machine past the stop line. The peak is measured (each JVM with the processes under it, Ghidra's
               decompile.exe: of each its working set or private bytes, the larger; read at the queue's head, kept in
               the folder's jvm_peak.json), peak_mb (1024) until the first sample.
RAM log: at most once a minute while a ticket waits at the queue's head, a line in the folder's ram_log.jsonl (RAM in
use, each Ghidra JVM's MB and, at fill_below or more, the machine's five largest processes), so a RAM spike can be
traced afterwards; written on a thread of its own, kept to about the last 1 MB (log_soon, ram_log).
The JVMs counted are every Ghidra JVM on the machine (started through the broker or not), plus each held token whose
process has not started its JVM yet. Waiters take tokens in priority order and then oldest first:
  worker    a live worker's own prepare (job.investigate)
  prefetch  the next function, prepared while the agent works (job.Prefetch)
  tool      gates, regress, prepare_list and the other tools (the default)
  idle      tools/idle_prepare.py: while it holds a token and a worker or prefetch waits, its stop_flag is set (it
            kills its Ghidra run and ends), so idle work never keeps a live worker waiting
The queue is a folder (config "dir", default %LOCALAPPDATA%/DecompPipeline/ghidra_broker): a ticket file per waiter,
a hold file per token, both named "<prio>_<time ns>_<pid>_<thread>", changed only under the folder's cross-process
lock. Files of processes that are gone (a crash, or a guard killing the process tree) are removed by the next waiter.

Every Ghidra JVM start takes a token: ghidra.slot (its whole prepare, in prio's turn) and, through ensure(), every
start outside one (ghidra._headless, tools/sdk_fid.headless) at priority "tool". A thread that holds a token takes
no second one, so a JVM is never counted twice. A slot server (core/ghidra_server) takes a Lease when it starts and
keeps it across its requests until it closes: one queue turn and one JVM start for a run of prepares, given back
between requests once another waiter has waited lease_s (Lease.contested).

HOLD: while a file named HOLD is in the folder, no token is granted (the monitor's machine-wide Ghidra hold). Runs
that hold tokens go on to their end; none is stopped, and idle holders are not asked to give way. A HOLD that holds
{"pass": KEY} lets through the processes whose environment has DP_BROKER_PASS=KEY (tools/wave.py's own prepares,
under the slot rule as before): held(). A held process waits outside the queue, with no ticket: at the queue's
head it could never be granted, and every ticket behind it, a pass holder's too, would wait with it.

DP_BROKER_DIR (environment): the folder, unless the config names one. The test suite sets it to a folder of its own
(tests/conftest.py, tests/test_sync.py), so a test never files a ticket in the machine's folder.
"""
import contextlib
import ctypes
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

from . import filelock

PRIO = {"worker": 0, "prefetch": 1, "tool": 2, "idle": 3}
YIELDS = PRIO["idle"]  # holders of this priority give their token to any higher priority that waits
DEFAULTS = {"slots": 0, "third_below": 82, "stop_at": 88, "age_s": 120, "burst": 0, "burst_below": 75,
            "by_ram": False, "fill_below": 85, "stop_line": 95, "max_jvms": 0, "peak_mb": 1024}
PEAK = "jvm_peak.json"
LOG, LOG_S, LOG_BYTES = "ram_log.jsonl", 60, 1 << 20  # ram_log(): its file, a line per LOG_S, about LOG_BYTES kept
HOLD = "HOLD"
DIR_ENV = "DP_BROKER_DIR"
_held = threading.local()  # n: tokens this thread holds (ensure takes none inside one)
_prio = threading.local()  # v: the priority of this thread's Ghidra slot (ghidra.slot), for a server's Lease


def settings(cfg):
    """(the config with defaults, folder): slots 0 means off. The folder: the config's "dir", else DP_BROKER_DIR, else
    the machine's."""
    b = {**DEFAULTS, **(cfg.get("ghidra_broker") or {})}
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return b, pathlib.Path(b.get("dir") or os.environ.get(DIR_ENV)
                           or pathlib.Path(base) / "DecompPipeline" / "ghidra_broker")


def ram_pct():
    """Physical memory in use, percent (None when unknown)."""
    try:
        if os.name == "nt":
            class Mem(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "ptotal", "pavail", "vtotal", "vavail", "x")]
            m = Mem()
            m.len = ctypes.sizeof(Mem)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            return 100.0 * (m.total - m.avail) / m.total
        total = os.sysconf("SC_PHYS_PAGES")
        return 100.0 * (total - os.sysconf("SC_AVPHYS_PAGES")) / total
    except (OSError, ValueError, AttributeError):
        return None


def allowed(b, ram):
    """How many Ghidra JVMs may run now: none at stop_at or more, one more than slots under third_below, and up to
    burst (when set) under burst_below."""
    if ram is not None and ram >= b["stop_at"]:
        return 0
    n = int(b["slots"]) + (1 if ram is not None and ram < b["third_below"] else 0)
    if ram is not None and ram < b["burst_below"]:
        n = max(n, int(b["burst"]))
    return n


def total_mb():
    """Physical memory, MB (None when unknown)."""
    try:
        import psutil
        return psutil.virtual_memory().total / 2**20
    except Exception:  # noqa: BLE001  (no psutil)
        return None


def by_ram(b, ram, total, jvm_mb, pending, peak_mb, cores=None):
    """How many Ghidra JVMs may run now under config "by_ram" (the module docstring): ram, percent in use now; total,
    the machine's MB; jvm_mb, each running Ghidra JVM's MB now; pending, the tokens held whose JVM has not started;
    peak_mb, one JVM's measured peak. A ticket is granted while the JVMs in use are fewer than this."""
    if ram is None or not total:
        return allowed(b, ram)
    if ram >= b["stop_at"]:
        return 0
    one = 100.0 * peak_mb / total
    committed = ram + sum(100.0 * max(0.0, peak_mb - m) / total for m in jvm_mb) + pending * one
    slots = int(b["slots"])
    ceiling = max(slots, int(b["max_jvms"]) or (cores or os.cpu_count() or 4) // 4)
    if ram < b["burst_below"]:
        ceiling = max(ceiling, int(b["burst"]))
    n = len(jvm_mb) + pending
    while n < ceiling and committed + one < (b["fill_below"] if n >= slots else b["stop_line"]):
        n += 1
        committed += one
    return n


def jvm_mem(pids, procs=()):
    """{pid: MB} of each process in pids with every process under it in procs (processes(); Ghidra's decompiler runs
    as the JVM's child decompile.exe): of each, its working set or its private bytes, the larger (gone ones left
    out)."""
    out = {}
    try:
        import psutil
    except ImportError:
        return out

    def mb(pid):
        m = psutil.Process(pid).memory_info()
        return max(m.rss, getattr(m, "private", 0)) / 2**20
    kids = {}
    for pid, ppid, *_ in procs:
        kids.setdefault(ppid, []).append(pid)
    for pid in pids:
        try:
            out[pid] = mb(pid)
        except Exception:  # noqa: BLE001  (gone, or access denied)
            continue
        todo, seen = list(kids.get(pid, ())), {pid}
        while todo:
            q = todo.pop()
            if q in seen:
                continue
            seen.add(q)
            with contextlib.suppress(Exception):
                out[pid] += mb(q)
            todo.extend(kids.get(q, ()))
    return out


def top_processes(n):
    """[[name, MB]] of the machine's n largest processes by working set ([] without psutil). Seconds on a busy machine:
    only on log_soon()'s own thread."""
    try:
        import psutil
    except ImportError:
        return []
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        m = p.info.get("memory_info")
        if m:
            rows.append((m.rss, p.info.get("name") or "?"))
    return [[name, round(rss / 2**20)] for rss, name in sorted(rows, reverse=True)[:n]]


_logging = threading.Lock()  # one log_soon() thread at a time in a process


def log_soon(d, ram, mem, top_at):
    """ram_log() on a thread of its own, so no grant waits on it; the five largest processes only at top_at percent
    of RAM or more, where a spike is worth tracing. Nothing when the log's last line is under LOG_S old."""
    try:
        if time.time() - (d / LOG).stat().st_mtime < LOG_S:
            return
    except OSError:
        pass
    if not _logging.acquire(blocking=False):
        return

    def run():
        try:
            ram_log(d, ram, mem, top=ram is not None and ram >= top_at)
        finally:
            _logging.release()
    threading.Thread(target=run, daemon=True).start()


def ram_log(d, ram, mem, now=None, top=True):
    """A line in folder d's ram_log.jsonl, unless the last is under LOG_S old: the time, RAM in use, each Ghidra JVM's
    MB (jvm_mem) and, with top, the machine's five largest processes. Past LOG_BYTES the older half goes. Waiters in
    other processes may write at the same minute; the folder's lock is not taken (a lost or doubled line is all
    that can happen)."""
    now = time.time() if now is None else now
    f = d / LOG
    try:
        st = f.stat()
        if now - st.st_mtime < LOG_S:
            return
        if st.st_size > LOG_BYTES:
            keep = f.read_bytes()[-(LOG_BYTES // 2):]
            f.write_bytes(keep[keep.find(b"\n") + 1:])
    except OSError:
        pass
    line = {"at": round(now, 1), "ram": ram, "jvm_mb": {str(p): round(m) for p, m in mem.items()},
            "top": top_processes(5) if top else []}
    with contextlib.suppress(OSError), open(f, "a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(line) + "\n")


def peak(b, d, mem):
    """One JVM's peak MB: the largest seen in folder d's jvm_peak.json, raised by mem ({pid: MB}); b's peak_mb until
    the first sample. Called under the folder's lock."""
    f = d / PEAK
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        seen = float(rec["mb"])
    except (OSError, ValueError, KeyError, TypeError):
        rec, seen = None, 0.0
    top = max(mem.items(), key=lambda kv: kv[1], default=(0, 0.0))
    if top[1] > seen:
        seen = top[1]
        f.write_text(json.dumps({"mb": round(seen, 1), "pid": top[0], "at": round(time.time(), 1)}), encoding="utf-8")
    return seen if seen > 0 else float(b["peak_mb"])


def cap(b, d, holds, found, procs=()):
    """How many Ghidra JVMs may run now: by_ram() when config "by_ram" is set, else allowed(). found: jvms(procs);
    procs: processes(). Writes the RAM log (log_soon)."""
    ram = ram_pct()
    mem = jvm_mem(found, procs)
    log_soon(d, ram, mem, float(b["fill_below"]))
    if not b.get("by_ram"):
        return allowed(b, ram)
    return by_ram(b, ram, total_mb(), [mem.get(p, 0.0) for p in found], in_use(holds, found) - len(found),
                  peak(b, d, mem))


def _parents():
    """{pid: ppid} of the whole machine from one call, or None where psutil has no such map (then each process's own
    ppid is read, the slow way)."""
    try:
        import psutil
        return psutil._psplatform.ppid_map()
    except Exception:  # noqa: BLE001
        return None


def processes():
    """[(pid, ppid, name)] of the machine's processes ([] without psutil). The parents come from one whole-machine map:
    psutil's per-process ppid reads that map again for each process, and with create_time that was 9 s for about 400
    processes in a fresh process (every prepare_list or job start; 0.02 s this way)."""
    try:
        import psutil
    except ImportError:
        return []
    parent = _parents()
    out = []
    for p in psutil.process_iter(["pid", "name"] if parent is not None else ["pid", "ppid", "name"]):
        i = p.info
        ppid = parent.get(i["pid"], 0) if parent is not None else i["ppid"]
        out.append((i["pid"], ppid or 0, (i["name"] or "").lower()))
    return out


def _is_ghidra(pid):
    try:
        import psutil
        return any("ghidra" in a.lower() for a in psutil.Process(pid).cmdline())
    except Exception:  # noqa: BLE001  (gone, or access denied: not one we can count)
        return False


def jvms(procs, is_ghidra=None):
    """{pid: its ancestors' pids} of the Ghidra JVMs among procs."""
    is_ghidra = is_ghidra or _is_ghidra
    parent = {p[0]: p[1] for p in procs}
    out = {}
    for pid, _ppid, name, *_ in procs:
        if name in ("java.exe", "javaw.exe", "java") and is_ghidra(pid):
            up, seen = [], {pid}
            q = parent.get(pid, 0)
            while q and q not in seen and len(up) < 32:
                up.append(q)
                seen.add(q)
                q = parent.get(q, 0)
            out[pid] = up
    return out


def in_use(holds, found):
    """Ghidra JVMs counted against the limit: every JVM found, plus each holder process's tokens beyond the JVMs
    under it (a token held, its JVM not started yet). found: jvms()."""
    per = {}
    for h in holds:
        per[_pid(h)] = per.get(_pid(h), 0) + 1
    under = {p: 0 for p in per}
    for up in found.values():
        for q in up:
            if q in under:
                under[q] += 1
                break
    return len(found) + sum(max(0, n - under[p]) for p, n in per.items())


def _pid(name):
    try:
        return int(name.split("_")[2])
    except (IndexError, ValueError):
        return 0


def _since(name):
    try:
        return int(name.split("_")[1]) / 1e9
    except (IndexError, ValueError):
        return 0.0


def _started(pid):
    """When process pid started (epoch seconds; 0 when unknown)."""
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001  (no psutil, gone, or access denied)
        return 0.0


def _boot():
    """When the machine started (epoch seconds; 0 when unknown)."""
    try:
        import psutil
        return psutil.boot_time()
    except Exception:  # noqa: BLE001  (no psutil)
        return 0.0


def _reap(d):
    """Remove the files of processes that are gone, or whose pid a newer process now has, and every file from before
    the machine started (Windows gives a dead process's pid to another after a restart, and a process's start time
    cannot always be read)."""
    boot = _boot()
    for p in d.glob("[thy]_*.json"):
        name = p.stem[2:]
        pid = _pid(name)
        if _since(name) < boot - 1 or not filelock.pid_alive(pid) or _started(pid) > _since(name) + 1:
            p.unlink(missing_ok=True)


def queue_key(name, now, age_s):
    """A ticket's place: its priority, less one per age_s seconds waited (never below 0), then its age."""
    age = max(0.0, now - _since(name))
    return (max(0, int(name[0]) - int(age // age_s) if age_s > 0 else int(name[0])), name.split("_")[1])


def state(d, now=None, age_s=0):
    """(holds, tickets): their names without the t_/h_ prefix, tickets in queue order."""
    now = time.time() if now is None else now
    return (sorted(p.stem[2:] for p in d.glob("h_*.json")),
            sorted((p.stem[2:] for p in d.glob("t_*.json")), key=lambda n: queue_key(n, now, age_s)))


def _wait(b, d, name, prio, stop_flag, poll):
    """Queue ticket `name` and wait until it is granted (its ticket becomes a hold). Returns the seconds waited; on a
    stop the ticket is removed and RuntimeError raised. A process the HOLD stops (held) files no ticket and waits
    outside the queue, saying so once on stderr: at the queue's head it could never be granted, and every ticket
    behind it would wait with it. When the HOLD goes or names its pass, it files the ticket under its first name, so
    it keeps its place."""
    ticket, hold, lock = d / f"t_{name}.json", d / f"h_{name}.json", d / "broker"
    body = json.dumps({"label": prio[1], "prio": prio[0], "since": time.time()})
    t0 = time.monotonic()
    head = queued = noted = False
    try:
        while True:
            procs = processes() if head else ()  # the scan (tens of ms) only at the queue's head, unlocked
            found = jvms(procs) if head else None
            with filelock.locked(lock):
                _reap(d)
                on_hold = held(d)
                if on_hold and queued:  # held: wait outside the queue (the ticket keeps its name, so its place)
                    ticket.unlink(missing_ok=True)
                    queued = head = False
                elif not on_hold and not queued:
                    ticket.write_text(body, encoding="utf-8")
                    queued = True
                holds, tickets = state(d, age_s=float(b["age_s"]))
                head = queued and bool(tickets) and tickets[0] == name
                if head and found is not None:
                    n = cap(b, d, holds, found, procs)
                    if n > 0 and in_use(holds, found) < n:
                        ticket.replace(hold)
                        return time.monotonic() - t0
                if int(name[0]) < YIELDS and queued:  # a live waiter: idle holders give way
                    for h in holds:
                        if int(h[0]) >= YIELDS:
                            (d / f"y_{h}.json").touch()
            if on_hold and not noted:
                print(f"ghidra_broker: a HOLD in {d} stops this process ({PASS_ENV} not its pass); waiting outside the "
                      "queue", file=sys.stderr, flush=True)
                noted = True
            if stop_flag is not None and stop_flag.is_set():
                raise RuntimeError("stopped while waiting for a machine-wide Ghidra token")
            if not head or found is not None:
                time.sleep(poll)
    except BaseException:
        _drop(d, name)
        raise


def _drop(d, name):
    with filelock.locked(d / "broker"):
        for p in (d / f"t_{name}.json", d / f"h_{name}.json", d / f"y_{name}.json"):
            p.unlink(missing_ok=True)


def _name(prio):
    return f"{PRIO[prio]}_{time.time_ns():020d}_{os.getpid()}_{threading.get_ident()}"


@contextlib.contextmanager
def token(cfg, prio="tool", label="", stop_flag=None, poll=0.25):
    """Hold a machine-wide Ghidra token for the block (nothing when the broker is off). Yields the seconds waited.
    stop_flag: waiting ends with RuntimeError once it is set; for prio "idle" it is also set when a worker or a
    prefetch is kept waiting by this hold."""
    b, d = settings(cfg)
    if int(b["slots"]) <= 0:
        yield 0.0
        return
    d.mkdir(parents=True, exist_ok=True)
    name = _name(prio)
    waited = _wait(b, d, name, (prio, label), stop_flag, poll)
    done = threading.Event()
    try:
        if PRIO[prio] >= YIELDS and stop_flag is not None:
            threading.Thread(target=_yield_watch, args=(d / f"y_{name}.json", stop_flag, done, poll),
                             daemon=True).start()
        _held.n = holding() + 1
        try:
            yield waited
        finally:
            _held.n -= 1
            done.set()
    finally:
        _drop(d, name)


class Lease:
    """A token held by a long-lived Ghidra (a slot server, core/ghidra_server) across its requests, instead of one
    token per request: a served prepare then neither queues nor starts a JVM. Taken by lease(), given back by
    release() (from any thread) when the server closes. contested(): the server should close between requests and
    give the token back, so a lease never keeps the queue waiting for long:
      - a ticket has waited lease_s seconds or more (fairness: other sessions, or this one's master, get their turn);
      - a ticket waits and the server has had no request for idle_s seconds (an idle JVM holds no token anyone needs);
      - the lease is "idle" priority and any other priority waits (as an idle token's holder gives way, _wait)."""

    def __init__(self, b, d, name):
        self.b, self.d, self.name, self.waited = b, d, name, 0.0

    def contested(self, idle_for, lease_s, idle_s, now=None):
        now = time.time() if now is None else now
        waiting = [p.stem[2:] for p in self.d.glob("t_*.json") if p.stem[2:] != self.name]
        if not waiting:
            return False
        if int(self.name[0]) >= YIELDS and any(int(n[0]) < YIELDS for n in waiting):
            return True
        return idle_for >= idle_s or max(now - _since(n) for n in waiting) >= lease_s

    def release(self):
        if self.name:
            _drop(self.d, self.name)
            self.name = None


def lease(cfg, prio="tool", label="", stop_flag=None, poll=0.25):
    """Wait for a token in prio's turn and return it as a Lease (None when the broker is off)."""
    b, d = settings(cfg)
    if int(b["slots"]) <= 0:
        return None
    d.mkdir(parents=True, exist_ok=True)
    out = Lease(b, d, _name(prio))
    out.waited = _wait(b, d, out.name, (prio, label), stop_flag, poll)
    return out


PASS_ENV = "DP_BROKER_PASS"


def held(d):
    """A HOLD in folder d stops this process: there is one, and it names no pass, or not this process's."""
    try:
        text = (d / HOLD).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        key = (json.loads(text) or {}).get("pass") if text.strip() else None
    except (ValueError, AttributeError):
        key = None
    return not (key and os.environ.get(PASS_ENV) == key)


def holding():
    """How many tokens this thread holds."""
    return getattr(_held, "n", 0)


def on(cfg):
    """Whether the machine-wide queue is on (slots > 0)."""
    return int(settings(cfg)[0]["slots"]) > 0


def current_prio():
    """The priority this thread waits in (ghidra.slot's prio; "tool" outside one)."""
    return getattr(_prio, "v", "tool")


@contextlib.contextmanager
def prio_of(prio):
    """This thread waits in prio's turn for the block (a server that its prepare starts takes its Lease so)."""
    old = getattr(_prio, "v", None)
    _prio.v = prio
    try:
        yield
    finally:
        _prio.v = old if old is not None else "tool"


@contextlib.contextmanager
def ensure(cfg, prio="tool", label="", stop_flag=None):
    """A token for one Ghidra JVM start, unless this thread already holds one (ghidra.slot's, around its prepare):
    one token per JVM, never two. Yields the seconds waited."""
    if holding():
        yield 0.0
        return
    with token(cfg, prio, label, stop_flag) as waited:
        yield waited


def _yield_watch(flag, stop_flag, done, poll):
    while not done.is_set():
        if flag.exists():
            stop_flag.set()
            return
        done.wait(poll)
