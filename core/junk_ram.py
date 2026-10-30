"""The junk-RAM judge pass (config "junk_ram", Dreamcast only; off by default: absent or false, and then nothing here
runs and no check result, harness, facts file or oracle stamp gains a byte).

C that reads the wrong place in unset global or heap RAM can read 0 exactly where the original read 0 (the runner
leaves unset RAM zero), and pass. With the flag on, a check that passes today's comparison runs the C a second time
with every unset byte past the image filled with a fixed junk pattern, and compares it with the original machine
code run by xemu (targets/dreamcast_sh4/xemu, the same runtime, vectors and report) on the same junk: the "junk
oracle". Only the vectors where xemu's zero run equals expected.txt (there xemu is the oracle) and where the
original's junk run finishes without a fault (the oracle drops a faulting vector) are judged; the others are counted
and left to today's pass. A function needs both passes. The junk oracle depends on the harness, the binary and xemu,
never on the C: it is made once per harness and kept under build/junk_ram/ (a mutant or a rejudge reuses it).

config "strict_judge": {"junk_ram": "report"}: the junk pass's differences go to the check result's "strict_report"
("junk_ram (report only): ..."), and the verdict is today's. "enforce" (the default) fails the verdict.

How the junk gets in without a runtime change: the runner copies the game image (its first argument) to 0x8c010000
and leaves the rest of its RAM zero. Given the image padded with junk to the end of RAM, every byte past the image
starts as junk; the vector's M lines, the stack paint and the C's own writes go on top as before. So "unset" is
exactly the bytes the judge leaves at zero (tc_rt.c: g_pristine beyond the image, not poked by an M line, not in
g_written). Not covered: zero bytes inside the image file, RAM below 0x8c010000.

Nearest existing: tools/junk_ram.py (the report-only proof tool of the junk round, now a thin wrapper over this)
and tools/xemu_check.py (xemu against expected.txt, a stamp-time cross-check) - neither is part of the judge; this
module holds their shared parts (the junk image, the xemu runner's build and function config) once, for both.
"""
import hashlib
import os
import pathlib
import shutil
import threading
import time


SEED = 1                # the judge's junk pattern: byte k past 0x8c010000 is shake_256("junk_ram:1") byte k
IMAGE_LO = 0x10000      # tc_rt.c tcrt_init: the image goes to RAM offset 0x10000 (0x8c010000)
RAM_SIZE = 0x01000000   # TCRT_RAM_SIZE: the runner refuses a bigger image than RAM_SIZE - IMAGE_LO
TARGET = "dreamcast_sh4"
MODES = ("enforce", "report")
XEMU_SRCS = ["xe_main.c", "xe_int.c", "xe_mac.c", "xe_fpu.c", "xe_ghmem.c"]
RUNTIME_SRCS = ["runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c"]
XEMU_FLAGS = ("/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC")
_LOCK, _KEY_LOCKS, _ORACLES, _BODIES = threading.Lock(), {}, {}, {}


def mode(cfg):
    """None while the flag is off, else "enforce" or "report" (config "strict_judge" "junk_ram"); ValueError on
    another target or a bad mode."""
    if not (cfg.get("junk_ram") is True or cfg.get("junk_ram") == 1):
        return None
    if cfg.get("target") != TARGET:
        raise ValueError(f'config "junk_ram": the junk-RAM pass is the Dreamcast\'s ({TARGET}: its RAM layout and '
                         f'xemu, its original\'s emulator) only, not {cfg.get("target")}')
    m = (cfg.get("strict_judge") or {}).get("junk_ram", "enforce")
    if m not in MODES:
        raise ValueError(f'config "strict_judge": junk_ram is "enforce" or "report", not {m!r}')
    return m


def junk_image(image, seed=SEED):
    """The image padded to the end of guest RAM with the junk pattern (per address, whatever the image's size)."""
    n = RAM_SIZE - IMAGE_LO
    if len(image) >= n:
        return bytes(image[:n])
    return bytes(image) + hashlib.shake_256(f"junk_ram:{seed}".encode()).digest(n)[len(image):]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _key_lock(k):
    with _LOCK:
        return _KEY_LOCKS.setdefault(k, threading.Lock())


def _put(path, data):
    """Write path whole (a temporary file, then a rename): a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}_{threading.get_ident()}")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, path)
    except OSError:  # another process put the same content there first (Windows refuses the rename)
        tmp.unlink(missing_ok=True)


def image_file(cfg, seed=SEED):
    """build/junk_ram/image_KEY.bin: the junk image of the workspace's binary, made once per binary and seed."""
    from . import paths
    image = cfg["binary_path"].read_bytes()
    p = paths.BUILD / "junk_ram" / f"image_{_sha(image + f' seed {seed}'.encode())[:16]}.bin"
    if not p.is_file():
        _put(p, junk_image(image, seed))
    return p


# xemu's side: tools/xemu_check.py uses these too (one copy).
def bodies(path):
    """entry -> [(lo, hi)]: the function bodies Ghidra has (state/function_bodies.txt, TcCatalog), the oracle's body:
    meta blocks can leave out a body's last instruction."""
    path = pathlib.Path(path) if path else None
    if path not in _BODIES:
        out = {}
        if path and path.exists():
            for ln in path.read_text(encoding="utf-8").splitlines():
                w = ln.split()
                if len(w) == 3:
                    out.setdefault(w[0], []).append((int(w[1], 16), int(w[2], 16)))
        _BODIES[path] = out
    return _BODIES[path]


def _sz_callees(arg):
    """meta "fpscr_sz" (TcFpscr.Sz, core/sz_entry.arg), "sz:MODES:CALLEE.XY_...", as [(callee, XY)]."""
    parts = (arg or "").split(":")
    return [tuple(c.split(".")) for c in parts[2].split("_")] if len(parts) > 2 and parts[2] else []


def config_text(meta, fpscr, body=()):
    """xemu's function config (XEMU_FN): the entry, the body spans, the result registers, FPSCR at entry."""
    lines = [f"entry {meta['addr']}"]
    spans = []
    ranges = [(int(lo, 16), int(hi, 16) + 1) for lo, hi in meta.get("blocks") or []] + list(body)
    for lo, hi in sorted(ranges):
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    lines += [f"body {lo:08x} {hi:08x}" for lo, hi in spans]
    if meta.get("ret_reg"):
        lines.append(f"retx {meta['ret_reg']}")
    elif meta.get("ret") == "u64":
        lines.append("u64")
    lines.append(f"fpscr {fpscr:08x}")
    for c, xy in _sz_callees(meta.get("fpscr_sz")):  # TcFpscr.Sz: a callee that returns with another FPSCR.SZ
        lines.append(f"szx {c} {''.join(d if d in '01' else '9' for d in xy)}")
    return "\n".join(lines) + "\n"


def xemu_cmd(cl, bdir, exe):
    """The command that builds runner.exe with xemu as tc_thunk (the unchanged runtime around it)."""
    from . import paths
    xdir = paths.TARGET / "xemu"
    return [cl, *XEMU_FLAGS, f"/I{paths.RUNTIME}", f"/I{xdir}", f"/Fo{bdir}\\", f"/Fe{exe}",
            *(str(paths.RUNTIME / s) for s in RUNTIME_SRCS), *(str(xdir / s) for s in XEMU_SRCS)]


def _cl(cfg):
    from . import check
    env = check.msvc_env(cfg)
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if not cl:
        raise RuntimeError("cl.exe not found in the Visual Studio environment")
    return env, cl


def xemu_runner(cfg):
    """(build/junk_ram/xemu_KEY/runner.exe, KEY): built once per content (the compiler, its INCLUDE, the flags,
    every runtime and xemu file)."""
    from . import paths
    env, cl = _cl(cfg)
    st = os.stat(cl)
    h = hashlib.sha256(repr((XEMU_FLAGS, cl, st.st_size, st.st_mtime_ns,
                             next((v for k, v in env.items() if k.upper() == "INCLUDE"), ""))).encode())
    for f in sorted([*(paths.RUNTIME / s for s in RUNTIME_SRCS), *paths.RUNTIME.glob("*.h"),
                     *(paths.TARGET / "xemu").glob("*")]):
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    key = h.hexdigest()[:16]
    d = paths.BUILD / "junk_ram" / f"xemu_{key}"
    exe = d / "runner.exe"
    with _key_lock(str(d)):
        if not exe.is_file():
            tmp = d.with_name(f"{d.name}.tmp{os.getpid()}_{threading.get_ident()}")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            rc, log, reason = paths.run_bounded(xemu_cmd(cl, tmp, tmp / "runner.exe"), 300, cwd=tmp, env=env)
            if rc != 0 or reason:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError(f"xemu runner build failed: {reason or log[-500:]}")
            try:
                os.replace(tmp, d)
            except OSError:  # another process built it first
                pass
            shutil.rmtree(tmp, ignore_errors=True)
    return exe, key


def unfit(meta, image, vecs, zero, junk, expected, frame_lo, strict):
    """The vectors the junk oracle cannot judge: {vid: why} - the original's junk run did not finish or faulted
    ("fault"), or xemu is no oracle for the function ("xemu": every vector, when its zero run differs from
    expected.txt in any one: a model gap - the SR mask, store queues, mock_junk - shows in some vectors and can hide
    in the rest; Spawn 8c024dd4 differs in 90 of 96 by the SR mask, and in the other 6 with junk)."""
    from .compare import compare_vector
    out = {}
    for vid, vec in vecs.items():
        z, j = zero.get(vid), junk.get(vid)
        if vid not in expected or z is None or compare_vector(meta, image, vec, expected[vid], z, frame_lo, strict, []):
            return {v: "xemu" for v in vecs}
        if j is None or not j.get("done") or j.get("faults") or "crash" in j or j.get("coderead"):
            out[vid] = "fault"
    return out


class _Every:
    """Every word (compare's "float_nan"): the junk oracle has no float-store tracking (TcTrack's FN lines), and
    xemu's default NaN is not the runner's (0xffc00000 against 0xffffffff: V8 8c0638a0), so a word NaN on both sides
    matches in the junk pass."""

    def __contains__(self, _):
        return True


def oracle(cfg, addr, harness, meta, image, vecs, expected, frame_lo, strict):
    """The junk oracle of a harness: (xemu's junk run {vid: parsed}, unfit {vid: why}); made once per key (the
    harness's files, the binary, the junk seed, xemu, the fsca table, the code ranges, the function config)."""
    from . import paths
    from . import check
    from .compare import parse_results
    exe, xkey = xemu_runner(cfg)
    jimg = image_file(cfg)
    fpscr = int(str(cfg.get("fpscr_entry") or "0x00040001"), 16)
    raw = paths.read_json(harness / "meta.json")
    fn = config_text(raw, fpscr, bodies(paths.FUNCTION_BODIES).get(addr, ()))
    table = paths.fsca_table() if cfg.get("fsca_table") else paths.FSCA_TABLE
    h = hashlib.sha256(f"junk oracle 1 seed {SEED} xemu {xkey} image {jimg.name}\n{fn}".encode())
    for p in (harness / "vectors.txt", harness / "expected.txt", harness / "meta.json", paths.CODE_RANGES, table):
        h.update(f"{p.name} {_sha(p.read_bytes()) if p.is_file() else '-'}\n".encode())
    key = h.hexdigest()[:24]
    with _key_lock(key):
        if key in _ORACLES:
            return _ORACLES[key]
        d = paths.BUILD / "junk_ram" / "oracle" / f"{addr}_{key}"
        if not (d / "junk.txt").is_file() or not (d / "zero.txt").is_file():
            tmp = d.with_name(f"{d.name}.tmp{os.getpid()}_{threading.get_ident()}")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            (tmp / "fn.txt").write_text(fn, encoding="utf-8")
            env = dict(os.environ, XEMU_FN=str(tmp / "fn.txt"), XEMU_MODEL="")
            for tag, binary in (("zero", cfg["binary_path"]), ("junk", jimg)):
                out = tmp / f"{tag}.txt"
                cmd = [str(exe), str(binary), str(paths.CODE_RANGES), str(table), str(harness / "vectors.txt"),
                       "all", str(out)]
                check.run_runner(cmd, cfg["check_timeout_s"], out, check.output_cap(harness), env=env)
                if not out.is_file():
                    out.write_text("", encoding="utf-8")
            try:
                os.replace(tmp, d)
            except OSError:
                d = tmp  # another process made the same one: use this copy, then let it go
        zero, junk = parse_results(d / "zero.txt"), parse_results(d / "junk.txt")
        if ".tmp" in d.name:
            shutil.rmtree(d, ignore_errors=True)
        res = (junk, unfit(meta, image, vecs, zero, junk, expected, frame_lo, strict))
        with _LOCK:
            if len(_ORACLES) > 64:
                _ORACLES.clear()
            _ORACLES[key] = res
        return res


def verdicts(meta, image, vecs, ids, ojunk, cjunk, unfit_, frame_lo, strict):
    """The junk pass over ids: ({vid: diffs} of the judged vectors that differ, judged count, {why: count} not
    judged). The C's junk run runs every vector in one process, in vectors.txt order: from the first vector it has
    no result for on (a hang or a crash), nothing more is judged, and that vector fails only if it is fit."""
    from .compare import compare_vector
    order = list(vecs)
    stop = next((k for k, v in enumerate(order) if not (cjunk.get(v) or {}).get("done")), len(order))
    pos = {v: k for k, v in enumerate(order)}
    fails, judged, skipped = {}, 0, {"xemu": 0, "fault": 0, "after_stop": 0}
    for vid in ids:
        if pos[vid] > stop:
            skipped["after_stop"] += 1
        elif vid in unfit_:
            skipped[unfit_[vid]] += 1
        else:
            judged += 1
            o = dict(ojunk[vid], float_nan=_Every())
            d = compare_vector(meta, image, vecs[vid], o, cjunk.get(vid), frame_lo, strict, [])
            if d:
                fails[vid] = d
    return fails, judged, skipped


def judge(cfg, jmode, addr, meta, harness, cmd, out, vecs, exp, image, sets, frame_lo, strict, result):
    """The second pass of check.run_check (the module's doc), into result: "junk_ram" {mode, vectors judged,
    failing, failing ids, skipped}; enforce: the failures count in "failing" and "first_diffs"; report: they go to
    "strict_report"."""
    from . import check, strict_judge
    from .compare import parse_results
    t0 = time.monotonic()
    ojunk, unfit_ = oracle(cfg, addr, harness, meta, image, vecs, exp, frame_lo, strict)
    jout = out.with_name("actual_junk.txt")
    jcmd = [cmd[0], str(image_file(cfg)), *cmd[2:-1], str(jout)]
    rc, log, reason = check.run_runner(jcmd, cfg["check_timeout_s"], jout, check.output_cap(harness))
    cjunk = parse_results(jout)
    jr = {"mode": jmode, "vectors": 0, "failing": 0, "failing_ids": [], "skipped": {}}
    if reason or rc:
        jr["run_error"] = reason or f"exit {rc}: {log.strip()[-200:]}"
    for s in sets:
        ids = [i for i, v in vecs.items() if v["set"] == s]
        fails, judged, skipped = verdicts(meta, image, vecs, ids, ojunk, cjunk, unfit_, frame_lo, strict)
        jr["vectors"] += judged
        for k, n in skipped.items():
            jr["skipped"][k] = jr["skipped"].get(k, 0) + n
        for vid, d in sorted(fails.items()):
            jr["failing"] += 1
            if len(jr["failing_ids"]) < 50:
                jr["failing_ids"].append(vid)
            if jmode == "report":
                strict_judge.note(result, s, vid, [f"junk_ram (report only): {x}" for x in d])
            elif s == "dev" and len(result["first_diffs"]) < 3:
                result["first_diffs"].append({"vector": vid, "inputs": check._describe_inputs(meta, vecs[vid]),
                                              "diffs": ["with unset RAM filled with junk (the original, run by "
                                                        "xemu on the same junk, does otherwise):", *d[:7]]})
    if jmode == "enforce":
        result["failing"] += jr["failing"]
    jr["seconds"] = round(time.monotonic() - t0, 2)
    result["junk_ram"] = jr
