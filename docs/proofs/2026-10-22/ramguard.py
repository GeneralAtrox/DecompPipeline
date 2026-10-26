"""Run CMD with a RAM brake: suspend its process tree at >= 95% machine RAM, resume after 5 readings under 85%.

usage: python ramguard.py SECONDS -- CMD [ARG ...]   (wraps the command; the hard time limit kills the tree)
"""
import subprocess
import sys
import time

import psutil

STOP, RESUME, CALM = 95.0, 85.0, 5


def tree(p):
    try:
        return [p] + p.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def main(argv):
    seconds = float(argv[0])
    cmd = argv[2:] if argv[1] == "--" else argv[1:]
    proc = subprocess.Popen(cmd)
    root = psutil.Process(proc.pid)
    end, paused, calm = time.time() + seconds, False, 0
    while proc.poll() is None:
        if time.time() > end:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=60)
            print(f"ramguard: killed after {seconds:g} s", file=sys.stderr, flush=True)
            proc.wait(timeout=60)
            return 124
        ram = psutil.virtual_memory().percent
        if not paused and ram >= STOP:
            for p in tree(root):
                try:
                    p.suspend()
                except psutil.Error:
                    pass
            paused, calm = True, 0
            print(f"ramguard: RAM {ram}%: paused", file=sys.stderr, flush=True)
        elif paused:
            calm = calm + 1 if ram < RESUME else 0
            if calm >= CALM:
                for p in tree(root):
                    try:
                        p.resume()
                    except psutil.Error:
                        pass
                paused = False
                print(f"ramguard: RAM {ram}%: resumed", file=sys.stderr, flush=True)
        time.sleep(1)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
