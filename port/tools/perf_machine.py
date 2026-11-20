"""The machine a benchmark runs on: processor, power plan, clock, load before a run, and the other processes' work
while it ran. Split from perf.py to keep it under the file-size cap.

Nearest existing: port/tools/perf.py - these lived there; a run on a shared machine (build box A) needs the other processes'
share during the run, which a load sample taken before it cannot give.
"""
import os
import platform
import subprocess
import time


def clock_mhz():
    """The processors' clock now and at most, in MHz (mean over the cores), or None where Windows does not say."""
    try:
        import ctypes
        n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
        buf = (ctypes.c_ulong * (6 * n))()
        if ctypes.WinDLL("powrprof").CallNtPowerInformation(11, None, 0, buf, ctypes.sizeof(buf)):
            return None
        return [round(sum(buf[6 * i + k] for i in range(n)) / n) for k in (2, 1)]
    except (OSError, AttributeError):
        return None


def machine_state():
    """What decides how fast this machine runs today: processor, power plan, mains or battery. Two runs on the same
    machine are only comparable when these match (a laptop on battery or on a saving plan clocks down)."""
    out = {"processor": platform.processor(), "plan": "", "mains": None, "mhz": clock_mhz()}
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "HARDWARE/DESCRIPTION/System/CentralProcessor/0".replace("/", chr(92))) as k:
            out["processor"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        status = (ctypes.c_ubyte * 12)()
        if ctypes.windll.kernel32.GetSystemPowerStatus(status) and status[0] in (0, 1):
            out["mains"] = bool(status[0])
        got = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=20).stdout
        out["plan"] = got[got.find("(") + 1:got.rfind(")")] if "(" in got else got.strip()
    except (OSError, ImportError, AttributeError, subprocess.SubprocessError):
        pass
    return out


def quiet(max_cpu, wait_s):
    """Processor load now (percent), after waiting up to wait_s for it to fall under max_cpu."""
    try:
        import psutil
    except ImportError:
        return -1.0
    end = time.time() + wait_s
    while True:
        cpu = psutil.cpu_percent(interval=3)
        if cpu <= max_cpu or time.time() >= end:
            return cpu
        time.sleep(20)


def cpu_snapshot():
    """{pid: (name, processor seconds)} of every process that can be read now, or {} without psutil."""
    try:
        import psutil
    except ImportError:
        return {}
    out = {}
    for p in psutil.process_iter(["name", "cpu_times"]):
        t = p.info.get("cpu_times")
        if t is not None:
            out[p.pid] = (p.info.get("name") or "?", t.user + t.system)
    return out


def others_since(before, seconds):
    """The other processes' processor time since `before` (the run's own process has ended and is not in it): their
    share of all the processors and the three busiest by name. Processes that started and ended inside are missed."""
    after, cores = cpu_snapshot(), os.cpu_count() or 1
    if not after or seconds <= 0:
        return {"share": 0.0, "seconds": 0.0, "top": []}
    used = {}
    for pid, (name, t) in after.items():
        d = t - before[pid][1] if pid in before and before[pid][0] == name else t
        if d > 0.05 and pid != os.getpid() and name.lower() not in ("system idle process", "idle"):
            used[name] = used.get(name, 0.0) + d
    total = sum(used.values())
    top = sorted(used.items(), key=lambda kv: -kv[1])[:3]
    return {"share": total / (seconds * cores), "seconds": round(total, 1), "top": [(n, round(v, 1)) for n, v in top]}
