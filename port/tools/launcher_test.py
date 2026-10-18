"""The launcher contract's test (port/README.md "Launcher contract"): runs a built port.exe through setup, verify and
play, bounded, and checks each exit code and what each run leaves in its data folder.

    python launcher_test.py --exe PORT.EXE --disc GOOD.cue [--wrong-disc OTHER_GAME.cue] [--work DIR]

GOOD is this game's disc image. OTHER_GAME (optional) is another game's image, for the exit code 5 cases. WORK (default
a new temporary folder, removed afterwards) receives a copy of GOOD. Exits 0 when every case passes.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

TIMEOUT = 600  # a setup copies the whole image


def run(exe, *args, timeout=TIMEOUT):
    try:
        p = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/IM", pathlib.Path(exe).name, "/T", "/F"], capture_output=True)
        return None, "", "timed out"


class Cases:
    def __init__(self):
        self.failed = 0

    def check(self, name, ok, detail=""):
        print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")
        self.failed += not ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--disc", required=True)
    ap.add_argument("--wrong-disc")
    ap.add_argument("--work")
    a = ap.parse_args()
    exe, disc = pathlib.Path(a.exe).resolve(), pathlib.Path(a.disc).resolve()
    root = pathlib.Path(a.work) if a.work else pathlib.Path(tempfile.mkdtemp(prefix="port-launcher-"))
    root.mkdir(parents=True, exist_ok=True)
    c = Cases()
    try:
        code, out, err = run(exe, "--version", timeout=30)
        lines = out.strip().splitlines()
        c.check("--version: exit 0, one line", code == 0 and len(lines) == 1, f"exit {code}, {out!r}")
        version = lines[0] if lines else ""

        work = root / "work"
        code, out, err = run(exe, "--setup-only", "--no-dialog", "--data-directory", work, "--disc", disc)
        c.check("setup: exit 0", code == 0, f"exit {code}, {err.strip()}")
        record = work / "installation.json"
        info = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
        c.check("setup: installation.json {version, assets}", info.get("version") == version and
                info.get("assets") == "assets/disc", str(info))
        assets = work / info.get("assets", "assets/disc")
        c.check("setup: the image is copied", (assets / disc.name).is_file(), str(assets))
        c.check("setup: logs/setup.log", (work / "logs" / "setup.log").is_file())

        code, out, err = run(exe, "--assets", assets, "--verify-assets", timeout=120)
        c.check("verify: the copy, exit 0", code == 0, f"exit {code}, {err.strip()}")
        empty = root / "empty"
        empty.mkdir(exist_ok=True)
        code, out, err = run(exe, "--assets", empty, "--verify-assets", timeout=60)
        c.check("verify: no image, exit 4", code == 4 and err.strip(), f"exit {code}, {err!r}")

        missing = root / "missing"
        code, out, err = run(exe, "--setup-only", "--no-dialog", "--data-directory", missing, "--disc",
                             root / "no such disc.cue", timeout=60)
        c.check("setup: a missing disc, exit 4, one line starting error:", code == 4 and
                len(err.strip().splitlines()) == 1 and err.startswith("error: "), f"exit {code}, {err!r}")
        c.check("setup: a failure writes no installation.json", not (missing / "installation.json").exists())
        lone = root / "lone"
        lone.mkdir(exist_ok=True)
        shutil.copy(disc, lone / disc.name)  # the .cue without its tracks
        code, out, err = run(exe, "--setup-only", "--no-dialog", "--data-directory", root / "lone_work", "--disc",
                             lone / disc.name, timeout=60)
        c.check("setup: missing track files, exit 4", code == 4, f"exit {code}, {err!r}")

        if a.wrong_disc:
            wrong = pathlib.Path(a.wrong_disc).resolve()
            code, out, err = run(exe, "--setup-only", "--no-dialog", "--data-directory", root / "wrong", "--disc",
                                 wrong, timeout=120)
            c.check("setup: another game, exit 5, one line", code == 5 and len(err.strip().splitlines()) == 1,
                    f"exit {code}, {err!r}")
            c.check("setup: another game writes no installation.json",
                    not (root / "wrong" / "installation.json").exists())
            code, out, err = run(exe, "--assets", wrong.parent, "--verify-assets", timeout=120)
            c.check("verify: another game, exit 5", code == 5, f"exit {code}, {err!r}")

        data = root / "userdata"
        code, out, err = run(exe, "--assets", assets, "--data-directory", data, "--headless", "--fixed-clock", "1",
                             "--seconds", "8", timeout=120)
        log = data / "logs" / "port.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        c.check("play: --assets with --data-directory runs to the watchdog (exit 2)", code == 2, f"exit {code}")
        c.check("play: the log is in the data folder and the boot file loaded", "bytes loaded" in text, str(log))
    finally:
        if not a.work:
            shutil.rmtree(root, ignore_errors=True)
    print(f"{c.failed} failed")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
