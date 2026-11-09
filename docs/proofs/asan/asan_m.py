"""asan_m.py (build box A, fps144, 11-08): one AddressSanitizer run each for Toy Commander, Robo Pit and Power Stone 2.
  build  TC live 2b2ddec on its live kit 35112d00 + the ASan patch (build.py --asan); Robo Pit a4fffdc on DP checked-asan
         (its own port/build.py, DP_ASAN=1, translations reused). PS2 arrives built from build box B (asan_l2.py).
  run    each on a short existing input, headless, fixed clock, ASAN_OPTIONS=continue_on_error=1; ASan's reports kept.
  clean  the worktrees, builds and runs; only results.txt and the reports (keep/) stay, then the caller fetches and deletes.
Nearest existing: the build box's own-proof script (TC builds on build box A) and menus3/mrun.py (Robo Pit's build) - their steps, reused here
with --asan; neither runs a checked build."""
import os, re, shutil, subprocess, sys, tarfile, time, pathlib, json
R = pathlib.Path(r"C:\Projects\DecompPipeline\.work\asanrun")
TB = pathlib.Path("C:/Projects/ToyCommander/.work/tc-bots")
K0, G0 = TB / "kit", TB / "game"
TW = pathlib.Path("C:/Projects/ToyCommander/.work/asan")
TK, TG = TW / "kit", TW / "game"
RW = pathlib.Path(r"C:\Projects\SegaSaturn\Robopit\.work\asan")
RK, RG, RO = RW / "kit", RW / "game", RW / "out"
TX = pathlib.Path(r"C:\Projects\SegaSaturn\Robopit\.work\fe\win\tx")
VC = "C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars32.bat"
VCPKG = pathlib.Path("C:/Projects/ToyCommander/.work/battle-ai/game/port/build/x86/vcpkg_installed")
MIRROR_DECOMP = "C:/Projects/ToyCommander/.work/battle-ai/decomp"
TCUE = "C:/Projects/ToyCommander/.work/tc-fe/disc/Toy Commander (Europe) (En,Fr,De,Es).cue"
RCUE = r"C:\dpx\robopit\disc\Robo Pit (Europe).cue"
PCUE = r"C:\dpx\ps2\disc\Power Stone 2 (Europe).cue"
BT = "C:/Projects/ToyCommander/.work/buildtools"
TMP = R / "tmp"
E = dict(os.environ, TEMP=str(TMP), TMP=str(TMP), NINJA=BT + "/ninja.exe", VCVARS32=VC,
         PATH=BT + "/cmake-4.4.0-windows-x86_64/bin;" + BT + ";" + os.environ["PATH"])
LOW = 0x4000
ROUTE = ("start@100+4,start@300+4,start@500+4,start@700+4,start@900+4,start@1100+4,b@1300+4,b@1500+4,a@1700+6,"
         + ",".join(f"right@{f}+4" for f in range(1800, 2281, 40)) + ",a@2500+6,a@2650+6,a@2800+6,a@2950+6")
g = lambda repo, *a: subprocess.run(["git", "-C", str(repo)] + list(a), capture_output=True, text=True)


def note(t):
    print(t, flush=True)
    with open(R / "results.txt", "a", encoding="utf-8") as f:
        f.write(t + "\n")


def jr(kit, why, cmd, env, t=5400):
    r = subprocess.run([sys.executable, "-B", str(kit / "tools/jobreg.py"), "run", "--why", why, "--timeout", str(t), "--"] + cmd,
                       cwd=kit, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=t + 300, creationflags=LOW)
    return r.returncode, r.stdout + r.stderr


def worktree(base, path, commit, patch):
    if not path.is_dir():
        g(base, "worktree", "add", "-q", "--detach", str(path), commit)
    g(path, "checkout", "-q", "-f", "--detach", commit)
    if patch:
        r = g(path, "apply", "--whitespace=nowarn", "--ignore-whitespace", "--exclude=port/README.md", str(R / "asan.patch"))
        assert r.returncode == 0, r.stderr
    if base == K0:
        shutil.copy2(K0 / "config.local.json", path / "config.local.json")


def build_tc():
    worktree(K0, TK, "35112d00", True)
    worktree(G0, TG, "2b2ddec", False)
    gj = TG / "port/game.json"
    cfg = json.loads(gj.read_text(encoding="utf-8"))
    cfg["decomp"], cfg["fsca_table"] = MIRROR_DECOMP, MIRROR_DECOMP + "/state/fsca_table.bin"
    cfg["translator_c"]["dir"] = MIRROR_DECOMP + "/translated_port"
    gj.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    bd = TG / "port/build/asan"
    subprocess.run(["robocopy", str(VCPKG), str(bd / "vcpkg_installed"), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"], capture_output=True)
    rc, out = jr(TK, "TC live 2b2ddec on kit 35112d00 + the AddressSanitizer patch (build.py --asan), one checked run",
                 [sys.executable, "-B", "port/tools/build.py", "--game", str(TG / "port"), "--engine", "--asan", "--build-dir", "asan",
                  "--no-build-gc", "--jobs", "6"], dict(E, DECOMP_PIPELINE=str(TK)))
    note(f"build tc: rc {rc} {out[-1500:] if rc else out[-200:]}")
    return bd / "port.exe"


def build_rp():
    worktree(K0, RK, "checked-asan", False)
    shutil.rmtree(RG, ignore_errors=True)
    RG.mkdir(parents=True)
    with tarfile.open(R / "rp_game.tar") as t:
        t.extractall(RG, filter="data")
    (RG / "research/ws-main/binary").mkdir(parents=True, exist_ok=True)
    shutil.copy2(R / "image.bin", RG / "research/ws-main/binary/image.bin")
    shutil.rmtree(RO, ignore_errors=True)
    RO.mkdir(parents=True)
    for d in ("txA", "txB"):
        shutil.copytree(TX / d, RO / d)
    subprocess.run(["robocopy", str(VCPKG), str(RO / "vcpkg_installed"), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"], capture_output=True)
    rc, out = jr(RK, "Robo Pit a4fffdc on DP checked-asan (6f1149a8 + the AddressSanitizer patch, DP_ASAN=1), one checked run",
                 [sys.executable, str(RG / "port/build.py"), "--out", str(RO), "--reuse"], dict(E, DP_ROOT=str(RK), DP_ASAN="1"))
    note(f"build rp: rc {rc} {out[-1500:] if rc else out[-200:]}")
    return RO / "robopit.exe"


def run(name, exe, args, timeout=2400, plant=False):
    if os.environ.get("ONLY_RUN") and os.environ["ONLY_RUN"] not in name:
        return
    D = R / "runs" / name
    shutil.rmtree(D, ignore_errors=True)
    D.mkdir(parents=True)
    if not exe.is_file():
        note(f"run {name}: no exe {exe}")
        return
    t0 = time.time()
    with open(D / "asan.txt", "w") as o:
        p = subprocess.Popen([str(exe)] + [str(a).replace("{D}", str(D)) for a in args], cwd=exe.parent, stdout=o,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=LOW,
                             env=dict(os.environ, ASAN_OPTIONS="continue_on_error=1", PORT_DATA_DIR=str(D / "data"),
                                      **({"DP_ASAN_PLANT": "1"} if plant else {})))
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            code = "killed"
    text = (D / "asan.txt").read_text(errors="replace")
    log = (D / "port.log").read_text(errors="replace") if (D / "port.log").is_file() else ""
    errs = re.findall(r"ERROR: AddressSanitizer: [^\n]*", text)
    note(f"run {name}: exit {code} in {time.time() - t0:.0f} s; ASan errors {len(errs)}; "
         + "; ".join(l[:150] for l in log.splitlines() if re.match(r"EXIT|crash|watchdog", l))[:400])
    seen = set()
    for m in re.finditer(r"ERROR: AddressSanitizer: (.*?)\n(.*?)(?:\n\n|\Z)", text, re.S):
        frames = [f.strip()[:200] for f in m.group(2).splitlines() if re.match(r"\s*#\d", f)][:6]
        key = (m.group(1).split(" on ")[0], tuple(frames[:3]))
        if key in seen:
            continue
        seen.add(key)
        note("   " + m.group(1)[:200] + "\n" + "\n".join("      " + f for f in frames))
    (R / "keep").mkdir(exist_ok=True)
    shutil.copy2(D / "asan.txt", R / "keep" / f"{name}_asan.txt")
    if log:
        (R / "keep" / f"{name}_port_tail.log").write_text("\n".join(log.splitlines()[-60:]), encoding="utf-8")
    shutil.rmtree(D, ignore_errors=True)


ph = sys.argv[1:] or ["build", "run", "clean"]
TMP.mkdir(parents=True, exist_ok=True)
note("started " + time.strftime("%H:%M") + " " + " ".join(ph))
if "build" in ph:
    g(K0, "fetch", "-q", str(R / "asan.bundle"), "+checked-asan:refs/heads/checked-asan")
    build_tc()
    build_rp()
if "run" in ph:
    tex = TG / "port/build/asan/port.exe"
    tc = ["--disc", TCUE, "--headless", "--fixed-clock", "1", "--size", "640x480", "--settings", "none", "--seconds", "2300",
          "--log", "{D}/port.log", "--data-directory", "{D}/data"]
    PL = {"tc": (tex, tc + ["--replay", str(TB / "scene/rec_plain/frontend.ptrec"), "--stop-at", "120"]),
          "ps2": (R / "ps2/PowerStone2/port.exe", ["--disc", PCUE, "--headless", "--fixed-clock", "1", "--settings", "none",
                  "--seconds", "600", "--log", "{D}/port.log", "--data-directory", "{D}/data", "--stop-at", "120"]),
          "robopit": (RO / "robopit.exe", ["--disc", RCUE, "--headless", "--det", "--present-thread", "off", "--frames", "120",
                      "--seconds", "600", "--save", "{D}/save.bin", "--log", "{D}/port.log"])}
    for k, (exe, args) in PL.items():  # the planted overflow first: each build must report it (host_main.cpp)
        run(f"plant_{k}", exe, args, timeout=900, plant=True)
    run("tc_frontend", tex, tc + ["--replay", str(TB / "scene/rec_plain/frontend.ptrec"), "--stop-at", "3000"])
    run("tc_mission", tex, tc + ["--replay", str(TB / "scene/rec_plain/mission.ptrec"), "--stop-at", "6000"])
    run("ps2_111340", R / "ps2/PowerStone2/port.exe",
        ["--disc", PCUE, "--headless", "--fixed-clock", "1", "--settings", "none", "--seconds", "2300", "--log", "{D}/port.log",
         "--data-directory", "{D}/data", "--replay", str(R / "20261107-111340.ptrec"), "--stop-at", "3061"])
    run("robopit_route", RO / "robopit.exe",
        ["--disc", RCUE, "--headless", "--det", "--present-thread", "off", "--frames", "3000", "--seconds", "1200",
         "--save", "{D}/save.bin", "--log", "{D}/port.log", "--input", ROUTE], timeout=1500)
if "clean" in ph:
    for base, wt in ((K0, TK), (G0, TG), (K0, RK)):
        g(base, "worktree", "remove", "--force", str(wt))
    for d in (TW, RW, R / "ps2", R / "runs", TMP):
        shutil.rmtree(d, ignore_errors=True)
    for f in ("asan.bundle", "rp_game.tar", "image.bin", "20261107-111340.ptrec", "asan.patch"):
        (R / f).unlink(missing_ok=True)
    note("left: " + ", ".join(str(d) for d in (TW, RW) if d.exists()) + f"; asanrun {sum(f.stat().st_size for f in R.rglob('*') if f.is_file()) // 1024} KB")
note("done " + time.strftime("%H:%M"))
