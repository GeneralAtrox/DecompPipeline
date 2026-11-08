"""Two builds (or one build with two sets of switches) against each other: every frame and the sound, over whole recordings.

A swap behind a switch is proven by this: run --old and --new from the same recordings, on the fixed clock with no window, and
compare a hash of every frame and of the sound. It resumes from results.jsonl in --out, skips a recording that is only the start
of a longer one, and stops at the first difference with both run folders kept (logs and the names of the frames that differ;
frames are hashed and deleted as the run writes them, so dump a differing one again with --dump-frames to look at it). Exit 0 all same, 4 a difference, 5 budget reached
(run it again to go on), 6 a run hit --run-seconds before its end, 7 the game could not write a frame or the sound (another
process held the file; run it again). A proof is not done until a planted fault in the new build has been shown to make this exit 4.

    py -3.12 port/tools/replay_compare.py --disc game.cue --out proof --old a/port.exe --old-env DP_ARM_FAST=0 --new c/port.exe \
        --replays recordings_dir [--new-arg=--pvr-yuv --new-arg=simd]

Nearest existing: port/tools/regress.py compares against goldens at a few chosen frames, not two builds at every frame; this is
docs/proofs/tc-movie-fast/replay_compare.py with one game's paths and scripts taken out.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import time


def pad_bytes(data):
    """The recording's pad stream: what decides whether one recording is the start of another."""
    header = struct.unpack("<I", data[8:12])[0]
    at = 12 + header
    n = struct.unpack("<I", data[at:at + 4])[0]
    return data[at + 4:at + 4 + n]


def last_vblank(data):
    return struct.unpack("<I", data[-4:])[0]


def pick_recordings(paths):
    """Longest first; one whose pad stream starts a longer one adds nothing."""
    loaded = sorted(((p, p.read_bytes()) for p in paths), key=lambda r: last_vblank(r[1]), reverse=True)
    keep, skipped = [], []
    for p, data in loaded:
        pads = pad_bytes(data)
        longer = next((k for k in keep if k[2].startswith(pads)), None)
        if longer:
            skipped.append((p.name, longer[0].name))
        else:
            keep.append((p, last_vblank(data), pads))
    return sorted(((p, stop) for p, stop, _ in keep), key=lambda r: r[1]), skipped  # shortest first: a difference shows soonest


def blake(path):
    return hashlib.blake2b(path.read_bytes(), digest_size=12).hexdigest()


def run_side(args, folder, exe, env_items, extra, recording, stop):
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    env = dict(os.environ)
    env.update(item.split("=", 1) for item in env_items)
    cmd = [exe, "--disc", args.disc, "--log", str(folder / "port.log"), "--data-directory", str(folder / "data"),
           "--dump-dir", str(folder), "--headless", "--fixed-clock", "1", "--seconds", str(args.run_seconds),
           "--size", "640x480", "--record", "off", "--present", "tick", "--stop-at", str(stop), "--dump-every", "1",
           "--audio-dump", "a.wav", "--replay", str(recording)] + extra
    begun = time.time()
    frames = {}

    def take(final):
        # Hashed and deleted while the run goes, so a whole recording never sits on disk (a long one is tens of GB of frames).
        # gfx_capture.c writes a frame's files on the game thread in order: once a later frame's appear, the earlier ones are whole.
        names = [p for p in folder.rglob("frame*") if p.is_file()]
        if not final:
            last = max((p.name[6:12] for p in names), default=None)
            names = [p for p in names if p.name[6:12] != last]
        for p in names:
            frames[p.name] = blake(p)
            try:
                p.unlink()
            except OSError:  # a scanner holding it; the next pass deletes it
                pass

    with open(folder / "out.txt", "w") as out:
        # Bounded by the game's own watchdog (--seconds) and this timeout; a child that outlives both is an error, not a pass.
        child = subprocess.Popen(cmd, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        while child.poll() is None:
            if time.time() - begun > args.run_seconds + 120:
                if os.name == "nt":  # its whole tree, by its own PID
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, timeout=60)
                child.kill()
                child.wait()
                raise subprocess.TimeoutExpired(cmd, args.run_seconds + 120)
            time.sleep(2)
            take(False)
        code = child.returncode
    take(True)
    wav = folder / "a.wav"
    sound = blake(wav) if wav.exists() else None
    sound_bytes = wav.stat().st_size if wav.exists() else 0
    if wav.exists():
        wav.unlink()
    log = (folder / "port.log").read_text(errors="replace").splitlines()
    return dict(exit=code, frames=frames, wav=sound, wav_bytes=sound_bytes, exit_line=[l for l in log if l.startswith("EXIT")][-1:],
                faults=[l[:160] for l in log if "out of step" in l or "FAULT" in l][:4],
                unwritten=[l[:160] for l in log if "cannot write" in l][:4], seconds=round(time.time() - begun))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disc", required=True)
    ap.add_argument("--out", required=True, help="results.jsonl and the run folders")
    ap.add_argument("--old", required=True, help="exe of the old path")
    ap.add_argument("--new", required=True, help="exe of the new path")
    ap.add_argument("--old-env", action="append", default=[], metavar="K=V")
    ap.add_argument("--new-env", action="append", default=[], metavar="K=V")
    ap.add_argument("--old-arg", action="append", default=[], metavar="ARG")
    ap.add_argument("--new-arg", action="append", default=[], metavar="ARG")
    ap.add_argument("--replays", help="folder of .ptrec")
    ap.add_argument("--replay", action="append", default=[], help="one .ptrec; may repeat")
    ap.add_argument("--stop-at", type=int, help="stop every run here instead of at the recording's end (a planted-fault check)")
    ap.add_argument("--tail", type=int, default=0, help="frames one side may have past the other's last: --present interp counts a frame at its flip, --present tick when it is shown, so a run stopped between them differs by one")
    ap.add_argument("--run-seconds", type=int, default=1500, help="one run's bound")
    ap.add_argument("--budget", type=int, default=3300, help="seconds before this stops starting recordings")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = out / "results.jsonl"
    paths = [pathlib.Path(p) for p in args.replay]
    if args.replays:
        paths += sorted(pathlib.Path(args.replays).glob("*.ptrec"))
    jobs, skipped = pick_recordings(paths)
    if not jobs:
        print("no recordings")
        return 2
    print("start of a longer recording, not run:", skipped, flush=True)
    done = {json.loads(l)["job"] for l in results.read_text().splitlines()} if results.exists() else set()
    begun = time.time()
    for recording, stop in jobs:
        name = recording.stem
        stop = args.stop_at or stop
        if name in done:
            continue
        if time.time() - begun > args.budget:
            print("budget reached, run again to go on", flush=True)
            return 5
        with cf.ThreadPoolExecutor(2) as pool:
            a = pool.submit(run_side, args, out / (name + "_old"), args.old, args.old_env, args.old_arg, recording, stop)
            b = pool.submit(run_side, args, out / (name + "_new"), args.new, args.new_env, args.new_arg, recording, stop)
            a, b = a.result(), b.result()
        fa, fb = a.pop("frames"), b.pop("frames")
        if any("watchdog" in " ".join(side["exit_line"]) for side in (a, b)):
            # A run cut off by its bound proves nothing, and its last frame and sound are cut short. No row: the next try runs it again.
            print(name, "stop", stop, "reached --run-seconds", args.run_seconds, "before the end:", len(fa), len(fb), "frames; raise it", flush=True)
            return 6
        if a["unwritten"] or b["unwritten"]:
            # A frame or the sound the game could not write (another process held the file) is the harness's fault, not the game's. No row.
            print(name, "a dump was not written, run it again:", a["unwritten"][:1], b["unwritten"][:1], flush=True)
            return 7
        differ =[k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]
        row = dict(job=name, stop=stop, frames_old=len(fa), frames_new=len(fb), distinct=len(set(fa.values())), differ=len(differ),
                   first=differ[:3], wav_same=a["wav"] == b["wav"], old=a, new=b)
        with open(results, "a") as h:
            h.write(json.dumps(row) + "\n")
        print(name, "stop", stop, "frames", len(fa), len(fb), "distinct", row["distinct"], "differ", len(differ), "sound bytes",
              a["wav_bytes"], "same" if row["wav_same"] else "DIFFERENT", "exit", a["exit"], b["exit"], a["exit_line"], a["faults"][:1],
              "seconds", a["seconds"], b["seconds"], flush=True)
        if differ or abs(len(fa) - len(fb)) > args.tail or not row["wav_same"] or a["exit"] != b["exit"] or not fa:
            print("DIFFERENCE: stopped; folders kept:", name, flush=True)
            return 4
        shutil.rmtree(out / (name + "_old"), ignore_errors=True)
        shutil.rmtree(out / (name + "_new"), ignore_errors=True)
    print("ALL SAME", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
