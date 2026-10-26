"""Judge a frozen workspace copy (setup_mirror.py) fresh, for the compiler-update proof.

    python judge.py --dp DP_CHECKOUT --mirror DIR --out results.json

Every cache that could stand in for a judgement is removed first: the copy's cached compiler environments
(state/msvc_env*.json) and its whole build/ (runtime objects, every function's build). No verdict memo is read:
the judge (core/check.run_check, the target's own when it has one; core/readable_check.check_readable for the
readable sample) is called directly, one function at a time. The cl banners of the x64 and x86 environments the
judge itself uses are recorded.
--toolset configured: vcvars as the judge runs it (it prefers VC/Auxiliary/Build/Microsoft.VCToolsVersion.v143.default.txt,
which on this laptop names the side-by-side 14.38 toolset); --toolset latest: VCToolsVersion set to
Microsoft.VCToolsVersion.default.txt's toolset (the newest installed, the one a Visual Studio update replaces), which
vcvars then uses. The judge itself is unchanged. RAM: a reading at 95% or more pauses until five readings in a row are under 85%."""
import argparse
import ctypes
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time


def ram_percent():
    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                                                                          ("tp", "ap", "tf", "af", "tv", "av", "ae")]
    m = MS()
    m.len = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return 100.0 * (m.tp - m.ap) / m.tp


def ram_gate(log):
    if ram_percent() < 95:
        return
    log(f"RAM {ram_percent():.0f}%: paused")
    under = 0
    while under < 5:
        time.sleep(10)
        under = under + 1 if ram_percent() < 85 else 0
    log("RAM back under 85%: resumed")


def banner(env):
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl.exe", path=path)
    if not cl:
        return {"cl": None, "banner": "cl.exe not found"}
    p = subprocess.run([cl], env=env, capture_output=True, text=True, timeout=60)
    lines = [ln.strip() for ln in (p.stderr + p.stdout).splitlines() if ln.strip()]
    return {"cl": cl, "banner": lines[0] if lines else ""}


def vcvars_clean(vcvars64):
    """Both vcvars (64 and the 32 beside it, as the judges derive it) run once with their output kept: a nonzero exit or
    any [ERROR: line (VsDevCmd, winsdk) fails the run before judging. The judge itself discards that output."""
    for bat in (vcvars64, vcvars64.replace("vcvars64.bat", "vcvars32.bat")):
        bat = bat.replace("/", "\\")
        p = subprocess.run(f'cmd /d /s /c ""{bat}" && echo VCVARS_OK"', capture_output=True, text=True, timeout=180,
                           env={k: v for k, v in os.environ.items() if k != "VCToolsVersion"})
        out = p.stdout + p.stderr
        bad = [ln for ln in out.splitlines() if "[ERROR:" in ln]
        if p.returncode != 0 or bad or "VCVARS_OK" not in out:
            raise SystemExit(f"{bat}: vcvars failed (exit {p.returncode}): {bad[:3]}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dp", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--toolset", choices=("configured", "latest"), default="configured")
    ap.add_argument("--vcvars", help="this vcvars64.bat instead of the config's (a -vcvars_ver pin; its vcvars32.bat "
                                     "beside it serves the x86 judge, as the judge derives it)")
    a = ap.parse_args()
    mirror = pathlib.Path(a.mirror).resolve()
    for f in (mirror / "state").glob("msvc_env*.json"):
        f.unlink()
    shutil.rmtree(mirror / "build", ignore_errors=True)
    os.environ["DECOMP_PROJECT"] = str(mirror)
    sys.path.insert(0, str(pathlib.Path(a.dp).resolve()))
    from core import check, paths
    assert paths.ROOT == mirror, paths.ROOT
    cfg = paths.config()
    os.environ.pop("VCToolsVersion", None)
    if a.vcvars:
        cfg["vcvars"] = a.vcvars
    vcvars_clean(cfg["vcvars"]) if a.toolset == "configured" else None
    if a.toolset == "latest":
        build = pathlib.Path(cfg["vcvars"]).parent
        os.environ["VCToolsVersion"] = (build / "Microsoft.VCToolsVersion.default.txt").read_text().strip()
    sample = json.loads((mirror / "sample.json").read_text(encoding="utf-8"))
    log = lambda m: print(m, flush=True)  # noqa: E731
    out = {"dp": str(pathlib.Path(a.dp).resolve()), "mirror": str(mirror), "target": cfg["target"],
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "toolset": a.toolset, "vcvars": cfg["vcvars"],
           "x64": banner(check.msvc_env(cfg)), "raw": {},
           "readable": {}}
    env64 = check.msvc_env(cfg)
    out["x64"].update(VCToolsVersion=env64.get("VCToolsVersion"), WindowsSDKVersion=env64.get("WindowsSDKVersion"))
    log(f"x64: {out['x64']['banner']} (toolset {out['x64']['VCToolsVersion']}, SDK {out['x64']['WindowsSDKVersion']})")
    if sample.get("readable"):
        from core import readable
        from core import readable_check as rcheck
        env86 = rcheck.msvc_env_x86(cfg)
        out["x86"] = dict(banner(env86), VCToolsVersion=env86.get("VCToolsVersion"),
                          WindowsSDKVersion=env86.get("WindowsSDKVersion"))
        log(f"x86: {out['x86']['banner']}")
    for it in sample["raw"]:
        ram_gate(log)
        t = time.time()
        try:
            r = check.run_check(cfg, it["addr"], str(mirror / "src" / f"{it['addr']}.c"),
                                build_dir=paths.BUILD / it["addr"])
        except Exception as e:
            r = {"verdict": "ERROR", "note": f"{type(e).__name__}: {e}"[:400]}
        r["seconds"] = round(time.time() - t, 2)
        out["raw"][it["addr"]] = r
        log(f"raw {it['addr']} {it['kind']}: {r['verdict']} score {r.get('score')} "
            f"{r.get('failing')}/{r.get('total')} ({r['seconds']} s)")
    if sample.get("readable"):
        reg = readable.Registry()
        for it in sample["readable"]:
            ram_gate(log)
            t = time.time()
            try:
                r = rcheck.check_readable(cfg, it["addr"], str(mirror / "readable" / "src" / f"{it['addr']}.c"),
                                          paths.BUILD / it["addr"] / "rd", reg=reg)
            except Exception as e:
                r = {"verdict": "ERROR", "note": f"{type(e).__name__}: {e}"[:400]}
            r["seconds"] = round(time.time() - t, 2)
            out["readable"][it["addr"]] = r
            log(f"readable {it['addr']}: {r['verdict']} {r.get('failing')}/{r.get('total')} ({r['seconds']} s)")
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    log(f"wrote {a.out}")


if __name__ == "__main__":
    main()
