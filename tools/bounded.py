"""Run a command with a hard time limit; on expiry kill its whole process tree (taskkill /T /F on Windows).

usage: python tools/bounded.py SECONDS [--] CMD [ARG ...]
Exit code: the command's own, or 124 when it was killed. Git Bash `timeout` kills only the direct child, so a
Ghidra (java.exe) or python grandchild would keep running; use this wrapper instead.
"""
import os
import signal
import subprocess
import sys


def kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    seconds, cmd = float(argv[0]), argv[1:]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    p = subprocess.Popen(cmd, start_new_session=os.name != "nt")
    try:
        return p.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        p.wait(timeout=60)
        print(f"bounded.py: killed the process tree after {seconds:g} s", file=sys.stderr, flush=True)
        return 124
    except KeyboardInterrupt:
        kill_tree(p.pid)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
