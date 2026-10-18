"""Reference frames from an emulator, for comparing the port's dumps against the console's image.

Flycast is used as a tool only: this script starts the workbench build of Flycast (build-research-runtime) as a
separate process, drives it through its loopback control socket (JSON lines) and reads the SQLite recording it
writes. No Flycast code is used or copied; the socket protocol and the table layout are read from its docs
(docs/workbench/Workbench.md). The executable is --flycast, else config.local.json "flycast".

    python tools/emu_capture.py --game <the game's port folder> --frames 300,900,1500,2100
        [--out DIR] [--seconds 240] [--around 2] [--flycast EXE]

Frame numbers are the port's: frame N is the port's N-th scene (`--dump-frames N`), the emulator's render
generation N+1 (each STARTRENDER counts one). Render counts only line up when both sides draw the same scenes;
the port skips the logo movies, so for anything after them pair frames by a game word instead:

    python tools/emu_capture.py --game ... --watch ADDR --values 16165,25233 --around 100
        [--ram --at-pc PC]

`--watch` is a guest u32 (e.g. a game's demo clock); a port run with `--dump-watch ADDR` logs its value for each
dumped frame. Frames are saved as `emu_wVALUE.png`. `--ram --at-pc PC` also dumps the 16 MB of RAM
exactly when the instruction at PC (kmRender's entry, where the port dumps with `--dump-ram 1`) completes with the
word equal to the value; it needs DynarecObservation, which slows Flycast (give --seconds 540).

How it works:
1. Flycast starts with the disc, the presentation bus recorded without pixels (cheap), a fixed RTC and no save
   state; the render count is read from that recording while it runs.
2. A little before each wanted frame the recording switches to one that stores each presented frame (RGB24 from
   the renderer, at `rend.Resolution` 480 = 640x480), then back to the cheap one.
3. The frames are written as `emu_NNNNNN.png` into --out, and Flycast is closed (taskkill /T if it does not exit).

The whole run is bounded by --seconds; nothing is left running.

Input: the control socket has no input command, so menus and missions are reached by replaying a pad recording.
Record one by playing (Flycast's own MapleRecord; the run ends when the window is closed, or at --seconds):

    python tools/emu_capture.py --game ... --record C:/runs/tc_mission1.fcmr --seconds 900

then pass --replay C:/runs/tc_mission1.fcmr to a capture run: the same inputs arrive at the same emulator ticks
(a run that diverges from the recording stops). Pair the replay's frames with the port's by a game word (--watch),
since the port's scripted input is not the recording's.
"""
import argparse
import base64
import json
import os
import pathlib
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

from game_config import PIPELINE, load as load_game

RTC_SEED = 946684800                # 2000-01-01, what Flycast's own record/replay pins
RENDER_COMPLETED = 4                # pvr_present_events.type
FRAMEBUFFER_CAPTURED = 5
PRESENTED_RGB24 = 2                 # PvrFramebufferKind of a renderer read-back
RAM_BASE, RAM_SIZE = 0x8C000000, 0x01000000
LEAD = 6                           # renders between switching the recording and the first wanted frame


class Control:
    """The workbench control socket: one JSON object per line each way, every request carrying the token."""

    def __init__(self, port, token):
        self.port, self.token, self.sock, self.buf, self.next_id = port, token, None, b"", 1

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=5.0)

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def request(self, cmd, args=None, timeout=30.0):
        rid = self.next_id
        self.next_id += 1
        line = {"id": rid, "token": self.token, "cmd": cmd}
        if args:
            line["args"] = args
        self.sock.settimeout(timeout)
        self.sock.sendall((json.dumps(line) + "\n").encode())
        while True:
            while b"\n" not in self.buf:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("control socket closed")
                self.buf += chunk
            raw, self.buf = self.buf.split(b"\n", 1)
            reply = json.loads(raw)
            if reply.get("id") != rid:
                continue
            if not reply.get("ok"):
                raise RuntimeError(f"{cmd}: {reply.get('error')}")
            return reply.get("result")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def quote(value):
    # Flycast's -config splits on ',' and accepts a double-quoted value.
    return f'"{value}"' if any(c in value for c in ", '") else value


def launch(exe, disc, port, token, db, maple=None):
    """db: the cheap render-count recording, or None (a watched word is read over the socket instead). maple:
    ("MapleRecord" or "MapleReplay", the .fcmr path), or None."""
    entries = [
        f"research:ControlPort={port}", f"research:ControlToken={token}",
        "config:rend.ThreadedRendering=no", "config:rend.Resolution=480", "config:rend.WideScreen=no",
        "config:rend.WidescreenGameHacks=no", "config:Dreamcast.AutoLoadState=no",
        "research:DynarecObservation=yes", f"research:DreamcastRtcSeed={RTC_SEED}",
    ]
    if db is not None:
        entries += [f"research:WorkbenchRecord={quote(str(db))}", "research:WorkbenchBuses=pvr-present"]
    if maple is not None:
        entries.append(f"research:{maple[0]}={quote(str(maple[1]))}")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen([str(exe), "-config", ",".join(entries), str(disc)], cwd=str(exe.parent),
                            creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
    else:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def query(db, sql, args=()):
    """One read of a recording Flycast is still writing (WAL mode allows it); None until the table exists."""
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2.0)
        try:
            return con.execute(sql, args).fetchone()[0]
        finally:
            con.close()
    except sqlite3.OperationalError:
        return None


def last_render(db):
    return query(db, "SELECT MAX(render_gen) FROM pvr_present_events WHERE type = ?", (RENDER_COMPLETED,)) or 0


def last_captured(db):
    return query(db, "SELECT MAX(fb_source_render_gen) FROM pvr_present_events WHERE type = ?",
                 (FRAMEBUFFER_CAPTURED,)) or 0


def windows(targets, around, lead):
    """Group the targets into capture windows [lo, hi], merging those closer than two leads."""
    spans = []
    for t in sorted(targets):
        lo, hi = t - around, t + around
        if spans and lo <= spans[-1][1] + 2 * lead:
            spans[-1][1] = hi
        else:
            spans.append([lo, hi])
    return spans


def read_watch(ctl, addr):
    """(tick, value): the guest u32 at addr, with the scheduler tick read just before it."""
    tick = ctl.request("status").get("tick", 0)
    data = base64.b64decode(ctl.request("mem_read", {"addr": f"0x{addr:08x}", "len": 4})["base64"])
    return tick, int.from_bytes(data, "little")


def frames_of(db):
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT tick, fb_source_render_gen, fb_w, fb_h, fb_row_bytes, bytes"
                           " FROM pvr_present_events WHERE type = ? AND fb_kind = ? AND bytes IS NOT NULL"
                           " ORDER BY tick", (FRAMEBUFFER_CAPTURED, PRESENTED_RGB24)).fetchall()
    finally:
        con.close()


def save_frames(captures, out, keep, samples):
    """Writes the wanted frames: by render (emu_NNNNNN.png) or, with a watch, by the watched value the game had
    when the frame was presented (emu_wVALUE.png, the last sample taken before it). Returns the names written."""
    from PIL import Image
    written = []
    for db, lo, hi in captures:
        for tick, gen, w, h, row, data in frames_of(db):
            if samples is None:
                key = gen - 1
                if key not in keep:
                    continue
                name = f"emu_{key:06d}.png"
            else:
                before = [v for t, v in samples if t <= tick]
                if not before or not lo <= before[-1] <= hi:
                    continue
                key = before[-1]
                name = f"emu_w{key}.png"
            Image.frombuffer("RGB", (w, h), data, "raw", "RGB", row, 1).save(out / name)
            written.append(key)
    return sorted(set(written))


def connect(ctl, proc, deadline):
    while True:
        if proc.poll() is not None:
            raise TimeoutError(f"flycast exited early ({proc.returncode})")
        try:
            ctl.connect()
            ctl.request("capabilities")
            return
        except OSError:
            ctl.close()
            if time.monotonic() > deadline:
                raise TimeoutError("control socket never came up")
            time.sleep(0.5)


def wait(cond, what, proc, deadline, step=0.05):
    while not cond():
        if time.monotonic() > deadline or proc.poll() is not None:
            raise TimeoutError(what())
        time.sleep(step)


def record(exe, disc, path, seconds):
    """Flycast with MapleRecord until its window is closed or the bound; then a clean exit, which finalizes the
    trace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + seconds
    port, token = free_port(), secrets.token_hex(16)
    proc = launch(exe, disc, port, token, None, ("MapleRecord", path))
    ctl = Control(port, token)
    try:
        connect(ctl, proc, deadline)
        print(f"emu: recording the pad input to {path}; close Flycast's window to finish (at most {seconds:.0f} s)")
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
    except (TimeoutError, RuntimeError, ConnectionError) as e:
        print(f"emu: {e}")
    finally:
        if proc.poll() is None:
            try:
                ctl.request("exit", timeout=10.0)
            except (OSError, RuntimeError, ConnectionError, AttributeError):
                pass
        ctl.close()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            pass
        kill_tree(proc)
    size = path.stat().st_size if path.is_file() else 0
    print(f"emu: recording {'written' if size else 'not written'}: {path} ({size} bytes)")
    return 0 if size else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game", required=True, help="the game's port folder (with game.json)")
    ap.add_argument("--frames", help="port frame numbers, comma separated")
    ap.add_argument("--watch", help="instead of frames: a guest u32 address (hex) that the targets are values of")
    ap.add_argument("--values", help="with --watch: the values to capture at, comma separated (a port run's "
                                     "--dump-watch prints them for its frames)")
    ap.add_argument("--out", help="output folder (default: <game>/build/emu)")
    ap.add_argument("--seconds", type=float, default=240.0, help="hard bound on the whole run")
    ap.add_argument("--around", type=int, default=2, help="frames (or, with --watch, value units) either side")
    ap.add_argument("--ram", action="store_true", help="with --watch: also dump the 16 MB of RAM at each value "
                                                         "(emu_wVALUE.ram; the port's --dump-ram 1 is its pair)")
    ap.add_argument("--at-pc", help="with --ram: dump when the instruction at this PC (hex) completes with the "
                                    "watched word equal to the value, e.g. kmRender's entry, where the port dumps")
    ap.add_argument("--flycast", help="the workbench Flycast executable (default: config.local.json \"flycast\")")
    ap.add_argument("--record", help="play and record the pad input to this .fcmr file (no capture)")
    ap.add_argument("--replay", help="replay this .fcmr recording's input while capturing")
    a = ap.parse_args()
    if not a.flycast:
        sys.path.insert(0, str(PIPELINE))
        from core import toolcfg  # noqa: E402  (no imports from the rest of core)
        a.flycast = toolcfg.need(toolcfg.load(PIPELINE), "flycast")
    if not a.record and bool(a.frames) == bool(a.watch and a.values):
        raise SystemExit("give --frames, or --watch ADDR with --values (or --record FILE)")
    if a.replay and not pathlib.Path(a.replay).is_file():
        raise SystemExit(f"no recording {a.replay}")

    game = load_game(pathlib.Path(a.game))
    disc = pathlib.Path(game["disc"])
    exe = pathlib.Path(a.flycast)
    if a.record:
        return record(exe, disc, pathlib.Path(a.record).resolve(), a.seconds)
    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.game) / "build" / "emu"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.db*"):
        old.unlink()
    watch = int(a.watch, 16) if a.watch else None
    targets = [int(v, 0) for v in (a.values or a.frames).split(",") if v.strip()]
    if watch is None:
        targets = [f + 1 for f in targets]          # frame N is render generation N+1
    keep = {t - 1 + d for t in targets for d in range(-a.around, a.around + 1)}
    spans = windows(targets, a.around, LEAD if watch is None else 0)

    deadline = time.monotonic() + a.seconds
    port, token = free_port(), secrets.token_hex(16)
    count_db = out / "count_0.db" if watch is None else None
    proc = launch(exe, disc, port, token, count_db,
                  ("MapleReplay", pathlib.Path(a.replay).resolve()) if a.replay else None)
    ctl = Control(port, token)
    captures, samples, status = [], ([] if watch is not None else None), "timeout"
    latest = [(0, 0)]

    def progress():
        if watch is None:
            return last_render(count_db)
        latest[0] = read_watch(ctl, watch)
        if latest[0][1] < 1 << 30:                 # a pointer or junk before the game sets it is not progress
            samples.append(latest[0])
            return latest[0][1]
        return 0
    try:
        connect(ctl, proc, deadline)
        print(f"emu: flycast pid {proc.pid}, control port {port}")
        for i, (lo, hi) in enumerate(spans):
            start = lo - LEAD if watch is None else lo - a.around
            exact = [t for t in targets if a.ram and a.at_pc and watch is not None and lo <= t <= hi][:1]
            if exact:   # armed before waiting: the watched word may jump straight to the value
                ctl.request("checkpoint_set", {"pc": int(a.at_pc, 16), "gate_addr": watch, "gate_value": exact[0]})
            wait(lambda: progress() >= start, lambda: f"{lo} not reached (at {progress()})", proc, deadline,
                 0.05 if watch is None else 0.01)
            if count_db is not None:
                ctl.request("record_stop", timeout=120.0)
            cap = out / f"capture_{i}.db"
            ctl.request("record_start", {"path": str(cap), "config": {
                "buses": "pvr-present", "rows": {"framebuffer_bytes": True}}}, timeout=60.0)
            captures.append((cap, lo, hi))
            print(f"emu: capturing {lo}..{hi} (at {progress()})")
            for t in (t for t in targets if a.ram and watch is not None and lo <= t <= hi):
                if exact:
                    # Exact: the emulator pauses when the instruction at PC completes with the watched word == t.
                    wait(lambda: ctl.request("status")["checkpoint"]["triggered"], lambda: f"PC at {t} not hit",
                         proc, deadline, 0.01)
                    ctl.request("checkpoint_clear")
                else:
                    wait(lambda: progress() >= t, lambda: f"{t} not reached", proc, deadline, 0.002)
                    ctl.request("pause", timeout=30.0)
                v = read_watch(ctl, watch)[1]
                ram = out / f"emu_w{v}.ram"
                ctl.request("mem_dump", {"addr": f"0x{RAM_BASE:08x}", "len": RAM_SIZE, "path": str(ram)}, 120.0)
                ctl.request("resume", timeout=30.0)
                print(f"emu: RAM at {v} -> {ram.name}")
            done = (lambda: last_captured(cap) >= hi) if watch is None else (lambda: progress() > hi)
            wait(done, lambda: f"capture to {hi} not finished", proc, deadline, 0.05 if watch is None else 0.01)
            ctl.request("record_stop", timeout=300.0)
            if count_db is not None and i + 1 < len(spans):
                count_db = out / f"count_{i + 1}.db"
                ctl.request("record_start", {"path": str(count_db), "config": {"buses": "pvr-present"}})
        status = "ok"
    except (TimeoutError, RuntimeError, ConnectionError) as e:
        print(f"emu: {e}")
    finally:
        for cmd, t in (("record_stop", 60.0), ("exit", 10.0)):
            try:
                ctl.request(cmd, timeout=t)
            except (OSError, RuntimeError, ConnectionError, AttributeError):
                pass
        ctl.close()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            pass
        kill_tree(proc)

    written = save_frames([c for c in captures if c[0].is_file()], out, keep, samples)
    print(f"emu: {status}; wrote {len(written)} frame(s) to {out}: {', '.join(map(str, written))}")
    return 0 if status == "ok" and written else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())

