"""The full re-proof's mutation audits (tools/mutation_audit.py run, with a chosen judge tree), on private workspace
copies whose harness/ is one prepare run's (l2full.py). Nothing of the copied workspaces is written.
usage: py -3.12 l2audit.py WORKERS JUDGE:HARNESS ...    e.g. l2audit.py 6 land:land_off rb:rb_on rb:rb_off
  JUDGE    repo-JUDGE judges (land: master's own judge; rb: the rebased branch's)
  HARNESS  TREE_CFG: the harnesses of repo-TREE/tests/.work/probe-KEY_TREE_CFG
Writes proof/audit_JUDGE_HARNESS.json ({key: {addr: {sha, outcomes} | {error}}}, mutation_audit.run's form); a key
already in it is skipped (resume). Workspaces wait for RAM under 85% before each key.
DP_PREWAVE_DIR: another folder laid out as laptop2's dpwork\\prewave (the main-laptop check, prewave-judge.md)."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import psutil

W = pathlib.Path(os.environ["DP_PREWAVE_DIR"])  # the work folder (on laptop2: its dpwork\prewave)
PROOF = W / "proof"
WS = {"grandia": W / r"ws\grandia\ws-1pct8", "skies": W / r"ws\skies\decomp-gekko", "tc": W / r"ws\tc\decomp",
      "ps2": W / r"ws\ps2\decomp\main", "spawn-main": W / r"ws\spawn\decomp\main"}
MUTANTS, TIME_CAP_S = 10, 3600  # tools/mutation_audit.py's
env = dict(os.environ, LOCALAPPDATA=str(W / "appdata"))
env.pop("DP_BROKER_PASS", None)
sample = json.loads((W / "repo-rb" / "tools" / "mutation_audit" / "sample.json").read_text(encoding="utf-8"))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def copy_ws(key, harness_tag, tree):
    live, dst = WS[key], W / "ws_audit" / f"{key}_{harness_tag}"
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "state").mkdir(parents=True)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p["binary"] = str((live / p["binary"]).resolve())
    p.pop("ghidra_dir", None)
    (dst / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    for f in (live / "state").iterdir():
        if f.is_file() and not f.name.startswith(("ledger.before", "msvc_env")) and not f.name.endswith(".lock"):
            shutil.copyfile(f, dst / "state" / f.name)
    if (live / "state" / "fsca").is_dir():
        shutil.copytree(live / "state" / "fsca", dst / "state" / "fsca")
    for d in ("matched", "oracle", "reference"):
        if (live / d).is_dir():
            shutil.copytree(live / d, dst / d)
    shutil.copytree(W / f"repo-{tree}" / "tests" / ".work" / f"probe-{key}_{harness_tag}" / "harness", dst / "harness")
    return dst


workers = sys.argv[1]
for spec in sys.argv[2:]:
    judge, htag = spec.split(":")
    out_p = PROOF / f"audit_{judge}_{htag}.json"
    out = json.loads(out_p.read_text(encoding="utf-8")) if out_p.exists() else {}
    for key in WS:
        if key in out:
            continue
        while psutil.virtual_memory().percent >= 85:
            log("RAM", psutil.virtual_memory().percent, "% >= 85: wait")
            time.sleep(60)
        ws = copy_ws(key, htag, htag.split("_")[0])
        log("audit", judge, htag, key)
        t = time.time()
        with tempfile.TemporaryDirectory(prefix="maudit_", dir=W) as tmp:
            j = pathlib.Path(tmp) / "run.json"
            cmd = [sys.executable, "-B", r"tools\mutation_check.py", "--project", str(ws), "--report", "--json", str(j),
                   "--max-mutants", str(MUTANTS), "--time-cap", str(TIME_CAP_S), "--workers", workers,
                   "--build-dir", str(pathlib.Path(tmp) / "b")] + sample[key]
            with open(PROOF / f"audit_{judge}_{htag}_{key}.log", "w", encoding="utf-8") as f:
                r = subprocess.run([sys.executable, r"tools\bounded.py", "10800"] + cmd, cwd=W / f"repo-{judge}",
                                   env=env, stdout=f, stderr=subprocess.STDOUT)
            recs = (json.loads(j.read_text(encoding="utf-8")) if j.exists() else {}).get("functions") or {}
        if not recs:
            sys.exit(f"{key}: no records (exit {r.returncode}); see the log")
        out[key] = {a: ({"error": rec["error"][:200]} if rec.get("error") else
                        {"sha": rec["src_sha256"], "outcomes": rec.get("outcomes", "")}) for a, rec in recs.items()}
        out_p.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
        shutil.rmtree(ws)
        log("done", judge, htag, key, f"{time.time() - t:.0f}s", len(recs), "functions")
log("all done")
