"""The bounds of a fast reference recording (tools/ref_fast.py record): its disk, its time and its swap.

- Stream: Flycast writes its RAM frames (research.RamFramesPath) into a named pipe, and a thread packs them as they
  arrive (ram_delta.pack_stream), so the raw stream is never on disk and no packing is left once Flycast stops: what
  is left is at most Flycast's 64 MB write buffer. A full pipe holds Flycast back (its frames wait in emulated time),
  so the run goes at the packer's speed when that is the slower.
- Bound: asked every CHECK_S seconds as the run goes; a reason to stop early, or None. Over the disk cap (the folder
  and what the packer holds), on a disk short of room, or, once WARM_S seconds and WARM_VB vertical blanks have run,
  projected over the cap or past the time limit at the speed so far.
- time_limit: the run's limit follows its vertical blanks (START_S + vblanks / min_vbps), at most --seconds.
- check_disk: refuses to start without twice the estimated size free (EST_KB_PER_VB, at most the cap).
- swap_in / restore: a recording goes to NAME.new and replaces NAME only once it is complete and verified; the old
  reference stays until then (an interrupted swap is put back by restore).

Nearest existing: port/tools/ram_delta.py - it packs a finished raw file after the run (pack_raw), which held 50 GB
per scenario of a reference game on 2026-11-06; this packs the stream while Flycast runs and bounds disk and time.
"""
import ctypes
import os
import pathlib
import secrets
import shutil
import threading

import ram_delta

MAX_GB = 5.0           # a scenario's reference, at most (the coordinator's limit, 2026-11-06)
EST_KB_PER_VB = 64     # stored KB per vertical blank before a run measures it (2026-10-18 TC references: 17-26)
FREE_MIN_GB = 2.0      # a run stops when the disk has less free
START_S = 120          # the time limit's fixed part: Flycast's start, the save states, the packing left at the end
MIN_VBPS = 8.0         # the slowest speed a run is given time for (2026-10-19, TC unloaded: about 38)
WARM_S, WARM_VB, CHECK_S = 90.0, 600, 5.0
PACK_S = 120           # the packer's time to finish once Flycast has stopped


def time_limit(vblanks, seconds, min_vbps=MIN_VBPS):
    return min(float(seconds), START_S + vblanks / min_vbps)


def folder_bytes(path):
    return sum(p.stat().st_size for p in pathlib.Path(path).rglob("*") if p.is_file())


def check_disk(where, vblanks, max_gb=MAX_GB, free=None):
    """The estimated size in bytes; SystemExit when the disk has less than twice that free (at most the cap)."""
    est = min(vblanks * EST_KB_PER_VB * 1024, max_gb * 1e9)
    have = shutil.disk_usage(where).free if free is None else free
    if have < 2 * est:
        raise SystemExit(f"{where}: {have / 1e9:.1f} GB free, under twice the estimated {est / 1e9:.2f} GB "
                         f"({vblanks} vertical blanks): not started")
    return est


class Bound:
    def __init__(self, until, limit_s, used, free, cap_gb=MAX_GB, t0=0.0, warm_s=WARM_S, every=CHECK_S):
        self.until, self.limit, self.used, self.free, self.cap = until, limit_s, used, free, cap_gb * 1e9
        self.t0, self.warm_s, self.every, self.last, self.seen = t0, warm_s, every, None, {}

    def __call__(self, vb, now):
        if self.last is not None and now - self.last < self.every:
            return None
        self.last = now
        used, el = self.used(), now - self.t0
        self.seen = {"vb": vb, "wall_s": round(el, 1), "bytes": used}
        if used > self.cap:
            return f"over the disk cap: {used / 1e9:.2f} GB > {self.cap / 1e9:.1f} GB at vertical blank {vb}"
        if self.free() < FREE_MIN_GB * 1e9:
            return f"the disk has under {FREE_MIN_GB} GB free at vertical blank {vb}"
        if el < self.warm_s or vb < WARM_VB:
            return None
        rate = vb / el
        need_s, need_b = self.until / rate, used * self.until / vb
        self.seen.update(vbps=round(rate, 2), projected_s=round(need_s), projected_gb=round(need_b / 1e9, 2))
        if need_b > self.cap:
            return (f"would need about {need_b / 1e9:.1f} GB for {self.until} vertical blanks ({used / 1e9:.2f} GB at "
                    f"{vb}), over the {self.cap / 1e9:.1f} GB cap")
        if need_s > self.limit:
            return (f"too slow: {rate:.1f} vertical blanks a second; {self.until} need about {need_s:.0f} s, over the "
                    f"{self.limit:.0f} s limit")
        return None


def _pipe(name):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateNamedPipeW.restype = ctypes.c_void_p
    k32.CreateNamedPipeW.argtypes = [ctypes.c_wchar_p] + [ctypes.c_uint32] * 6 + [ctypes.c_void_p]
    k32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    # inbound, byte mode, blocking, one instance, 1 MB in
    h = k32.CreateNamedPipeW(name, 0x1, 0x0, 1, 0, 1 << 20, 0, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f"cannot create the pipe {name}")
    return k32, h


class Stream:
    """The pipe (name: research.RamFramesPath) and its packing thread. stop: set to a reason to stop storing."""

    def __init__(self, dest, keyframe, buffer_mb=64):
        self.name = rf"\\.\pipe\ref_fast_{os.getpid()}_{secrets.token_hex(4)}"
        self.writer = ram_delta.Writer(dest, keyframe, buffer_mb)
        self.stop, self.stats, self.error = None, None, None
        self.k32, self.handle = _pipe(self.name)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def held(self):
        return self.writer.held

    def _run(self):
        import msvcrt
        try:
            self.k32.ConnectNamedPipe(self.handle, None)  # 0 with ERROR_PIPE_CONNECTED if Flycast came first: fine
            fd = msvcrt.open_osfhandle(self.handle, os.O_RDONLY | os.O_BINARY)
            with os.fdopen(fd, "rb", buffering=1 << 20) as f:
                self.stats = ram_delta.pack_stream(f, None, writer=self.writer, stop=lambda: self.stop)
        except Exception as e:  # noqa: BLE001 - reported by finish()
            self.error = f"{type(e).__name__}: {e}"

    def finish(self, timeout=PACK_S):
        """The packer's stats once the writer has gone (None, with self.error, if it failed or ran over timeout)."""
        if self.thread.is_alive():
            try:
                open(self.name, "wb").close()  # nobody connected (Flycast stopped early): end the wait
            except OSError:
                pass
        self.thread.join(timeout)
        if self.thread.is_alive():
            self.error = self.error or f"packing did not finish within {timeout} s of Flycast stopping"
            return None
        return self.stats


def swap_in(new, ref):
    """new replaces ref (renames only); ref's old folder is kept until new is in place."""
    new, ref = pathlib.Path(new), pathlib.Path(ref)
    old = ref.with_name(ref.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if ref.exists():
        os.replace(ref, old)
    os.replace(new, ref)
    shutil.rmtree(old, ignore_errors=True)


def restore(ref):
    """A swap cut between its renames: the old reference back in place."""
    ref = pathlib.Path(ref)
    old = ref.with_name(ref.name + ".old")
    if not ref.exists() and old.exists():
        os.replace(old, ref)


def verify(new, frames, vblanks):
    """Why the packed folder new is not a whole reference, or None: its frames readable, as many as packed, the last
    one rebuilt, reaching vblanks."""
    r = ram_delta.Reader(pathlib.Path(new) / "ram")
    try:
        if not frames or len(r) != frames:
            return f"{len(r)} frames in the index, {frames} packed"
        if len(r.frame(len(r) - 1)) != ram_delta.RAM_SIZE:
            return "the last frame does not rebuild"
        last = int(r.tag(len(r) - 1)[1])
        return None if last >= vblanks - WARM_VB else f"the frames end at vertical blank {last}, not {vblanks}"
    finally:
        r.close()
