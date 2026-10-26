"""Proof J (row 21): tools/game_setup.py makes the same workspace as the hand steps, from the disc, for a Dreamcast,
a Saturn and a GameCube game.

usage: python setup_proof.py hand LIVE_WS WS BINARY PORT    the hand step: LIVE_WS's project.json (binary pointed at
                                                            BINARY, copied in; own port; no ghidra_dir), its
                                                            oracle/TcInputs.java, then `decomp.py setup`, timed
       python setup_proof.py fields LIVE_WS OUT.json         the hand project.json's own decisions, for
                                                            game_setup.py --fields: every key but the ones the disc
                                                            gives (DISC) and the workspace's own (target, binary,
                                                            binary_sha256, port, ghidra_dir)
       python setup_proof.py new WS -- ARGS...               tools/game_setup.py setup ARGS (ending with WS), timed
       python setup_proof.py compare WS_A WS_B               every file both made: the binary, project.json (binary
                                                            path and port aside), oracle/TcInputs.java, and state/
                                                            (logs, the Ghidra script copies and the timing records aside)
DP_TREE names the DP tree to run. The broker pass is read from the HOLD into this process's environment only (never
printed, logged or written); the children inherit it.
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

OWN = ("target", "binary", "binary_sha256", "port", "ghidra_dir")
DISC = ("image_gaps", "game_ranges", "global_ranges", "fixed_regs", "seed_functions", "seed_entries")
STATE_SKIP = {"ghidra_scripts", "game_setup.json", "decode_check.tsv", "operator_steps.jsonl", "events.log"}


def env():
    e = dict(os.environ)
    hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
    if hold.exists():
        key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
        if key:
            e["DP_BROKER_PASS"] = key
        del key
    return e


def timed(cmd, ws, label):
    t = time.perf_counter()
    rc = subprocess.run(cmd, env=env(), timeout=5400).returncode
    rec = {"step": label, "rc": rc, "seconds": round(time.perf_counter() - t, 1)}
    with open(pathlib.Path(ws) / "proof_time.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)
    return rc


def hand(live, ws, binary, port):
    tree = pathlib.Path(os.environ["DP_TREE"])
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "disc").mkdir(parents=True)
    shutil.copyfile(binary, ws / "disc" / binary.name)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p.pop("ghidra_dir", None)
    p.update({"binary": f"disc/{binary.name}", "port": port})
    (ws / "project.json").write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8", newline="\n")
    (ws / "oracle").mkdir()
    shutil.copyfile(live / "oracle" / "TcInputs.java", ws / "oracle" / "TcInputs.java")
    return timed([sys.executable, str(tree / "decomp.py"), "--project", str(ws), "setup"], ws, "hand: decomp.py setup")


def fields(live, out):
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    pathlib.Path(out).write_text(json.dumps({k: v for k, v in p.items() if k not in OWN + DISC}, indent=1) + "\n",
                                 encoding="utf-8", newline="\n")


def new(ws, args):
    tree = pathlib.Path(os.environ["DP_TREE"])
    shutil.rmtree(ws, ignore_errors=True)
    return timed([sys.executable, str(tree / "tools" / "game_setup.py"), "setup", *args], ws.parent, f"new: {ws.name}")


def digest(f):
    b = f.read_bytes()
    if f.suffix in (".java", ".txt", ".tsv", ".json"):
        b = b.replace(b"\r\n", b"\n")  # a hand copy's line endings
    return hashlib.sha256(b).hexdigest()


def project(ws):
    p = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    binary = (ws / p.pop("binary")).resolve()
    p.pop("port", None)
    p.pop("ghidra_dir", None)
    return p, binary


def compare(a, b):
    pa, ba = project(a)
    pb, bb = project(b)
    out = {"binary": digest(ba) == digest(bb),
           "project_json": {k: [pa.get(k), pb.get(k)] for k in sorted(pa.keys() | pb.keys()) if pa.get(k) != pb.get(k)},
           "TcInputs": digest(a / "oracle" / "TcInputs.java") == digest(b / "oracle" / "TcInputs.java")}
    sa = {f.relative_to(a / "state").as_posix(): f for f in (a / "state").rglob("*") if f.is_file()}
    sb = {f.relative_to(b / "state").as_posix(): f for f in (b / "state").rglob("*") if f.is_file()}
    keep = lambda k: k.split("/")[0] not in STATE_SKIP and not k.endswith(".log")  # noqa: E731
    both = sorted(k for k in sa.keys() & sb.keys() if keep(k))
    out["state_files"] = both
    out["state_differ"] = [k for k in both if digest(sa[k]) != digest(sb[k])]
    out["state_only_a"] = sorted(k for k in sa.keys() - sb.keys() if keep(k))
    out["state_only_b"] = sorted(k for k in sb.keys() - sa.keys() if keep(k))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "hand":
        sys.exit(hand(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]).resolve(), pathlib.Path(sys.argv[4]),
                      int(sys.argv[5])))
    elif cmd == "fields":
        fields(pathlib.Path(sys.argv[2]), sys.argv[3])
    elif cmd == "new":
        sys.exit(new(pathlib.Path(sys.argv[2]).resolve(), sys.argv[4:]))
    else:
        compare(pathlib.Path(sys.argv[2]).resolve(), pathlib.Path(sys.argv[3]).resolve())
