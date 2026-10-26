"""Measurement only: a private copy of a live workspace (never written) where K functions chosen at random (seed 1)
from those with a harness lose their oracle stamp, so tools/prepare_list.py prepares them cold.
usage: python build_ws.py LIVE_WS PRIVATE_WS K [SEED]"""
import json, pathlib, random, shutil, sys

SKIP_DIRS = {"ghidra_scripts", "readable_snapshots", "stamp_refresh", "sig_override_backup", "usage_probe"}
live, ws, k = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3])
shutil.rmtree(ws, ignore_errors=True)
(ws / "state").mkdir(parents=True)
p = json.loads((live / "project.json").read_text(encoding="utf-8"))
p["binary"] = str((live / p["binary"]).resolve())
gsrc = pathlib.Path(p.pop("ghidra_dir")) / "ghidra" if p.get("ghidra_dir") else live / "ghidra"
(ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
for d in ("oracle", "matched", "lists"):
    if (live / d).is_dir():
        shutil.copytree(live / d, ws / d)
if (live / "readable" / "names").is_dir():
    shutil.copytree(live / "readable" / "names", ws / "readable" / "names")
shutil.copytree(gsrc, ws / "ghidra", ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
for f in (live / "state").iterdir():
    if f.is_dir():
        if f.name not in SKIP_DIRS:
            shutil.copytree(f, ws / "state" / f.name)
    elif not (".before" in f.name or f.suffix in (".log", ".lock") or f.name.startswith(("ghidra_marks",
              "prepare_timing", "events", "final_audit"))):
        shutil.copy2(f, ws / "state" / f.name)
have = sorted(d.name for d in (live / "harness").iterdir() if (d / "meta.json").exists())
for x in have:
    (ws / "harness" / x).mkdir(parents=True)
    shutil.copy2(live / "harness" / x / "meta.json", ws / "harness" / x / "meta.json")
picked = sorted(random.Random(int(sys.argv[4]) if len(sys.argv) > 4 else 1).sample(have, min(k, len(have))), key=lambda x: int(x, 16))
for x in picked:
    m = ws / "harness" / x / "meta.json"
    meta = json.loads(m.read_text(encoding="utf-8"))
    meta.pop("oracle_stamp", None)
    m.write_text(json.dumps(meta, indent=1), encoding="utf-8")
(ws / "picked.txt").write_text("\n".join(picked) + "\n", encoding="utf-8")
print(ws.name, len(have), "with a harness;", len(picked), "picked")
