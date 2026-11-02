"""The unaligned store-queue judge pass (config "sq_unaligned", Dreamcast only; off by default: absent or false, and
then nothing here runs and no check result gains a byte).

The oracle only ever draws a window pointer on a 32-byte line (TcSq.draw), but a game's stream can leave it inside a
line (TC: r14 = e0bc6044 after a clip block), and every later block of that stream stays off by as much. Readable C
that takes its line from the pointer (`p & ~31`) passes the aligned vectors and overwrites the stream's earlier stores
in play (TC 8c07557c: the movie quad's TCW). With the flag on, readable C that passes today's comparison runs a second
time on the same vectors with rN moved inside its line, a function needing both passes. Raw C is not judged on these.

The answers come from tools/sq_unaligned.py, kept under state/sq_unaligned/<addr>/: xemu runs the original on the
moved vectors, and a vector is kept only where xemu's run on the unmoved vector equals expected.txt, the moved run ends
without a fault, and the translator's C of the function agrees with xemu (two separate store-queue models: a mocked
callee that moves rN has no defined answer off the line, and is the usual vector dropped). This pass can only fail C
that passes today's judge, so no real catch can be lost; the oracle, its stamps and the harness stay as they are.

Nearest existing: core/junk_ram.py (a second pass with xemu as oracle, on the same vectors: it never moves the window
pointer, and the raw judge runs it) and tools/xemu_check.py (xemu against expected.txt, a stamp-time cross-check).
"""
import hashlib
import pathlib
import re
import shutil

TARGET = "dreamcast_sh4"
XEMU_MODEL = "sqwt,sqlog"  # write-through as the runner's window, each send logged as a write
DIFF_NOTE = ("(store-queue pass, config sq_unaligned: rN is inside its 32-byte line here. The bytes below rN in that "
             "line are the stream's earlier stores: leave them; a block ends at its line's end, never at rN + 0x20)")


def on(cfg):
    """The flag; ValueError on another target."""
    if not (cfg.get("sq_unaligned") is True or cfg.get("sq_unaligned") == 1):
        return False
    if cfg.get("target") != TARGET:
        raise ValueError(f'config "sq_unaligned": the Dreamcast\'s ({TARGET}) store queues only, not {cfg.get("target")}')
    return True


def window_index(meta):
    """The index in a vector's G line (r8..r14) of the function's window register, or None."""
    m = re.fullmatch(r"r(8|9|1[0-4])", (meta or {}).get("sq_window") or "")
    return int(m.group(1)) - 8 if m else None


def move_vectors(text, idx):
    """text of a vectors.txt with the window pointer of each vector moved 4..28 bytes into its line; (text, moved ids).
    A pointer outside the window (the function's RAM path) stays, and that vector is not this pass's."""
    out, vid, ids = [], None, []
    for ln in text.splitlines(True):
        if ln.startswith("V "):
            vid = int(ln.split()[1])
        elif ln.startswith("G ") and vid is not None:
            t = ln.split()
            v = int(t[1 + idx], 16)
            if v >> 26 == 0x38:
                t[1 + idx] = "%08x" % (v + 4 * (1 + vid % 7))
                ids.append(vid)
                ln = " ".join(t) + "\n"
        out.append(ln)
    return "".join(out), ids


def keep_blocks(text, ids):
    """text of a vectors.txt or expected.txt with only the V..E blocks of ids, all dev; what stands before the first block stays."""
    out, keep, ids = [], True, set(ids)
    for ln in text.splitlines(True):
        if ln.startswith("V "):
            keep = int(ln.split()[1]) in ids
            ln = ln.replace(" hidden", " dev")  # the aligned pass keeps the hidden set; an agent must see these answers
        if keep:
            out.append(ln)
    return "".join(out)


def key_of(cfg, harness):
    """What the answers depend on: the harness's vectors and answers, and the binary."""
    h = hashlib.sha256(cfg["binary_path"].read_bytes())
    for f in ("vectors.txt", "expected.txt", "meta.json"):
        h.update((pathlib.Path(harness) / f).read_bytes())
    return h.hexdigest()


def answers_dir(addr):
    from . import paths
    return paths.STATE / "sq_unaligned" / addr


def judge(cfg, addr, src_file, build_dir, sets, reg, callee_harness, lean, stub, meta, harness_dir, result):
    """The second pass of a readable check: result, failed when the C differs on a moved vector. harness_dir set: a
    private harness (this pass's own run, a regression fixture), which has no moved answers."""
    if harness_dir is not None or result.get("verdict") != "PASS" or stub or not on(cfg) or window_index(meta) is None:
        return result
    from . import paths
    d = answers_dir(addr)
    info = paths.read_json(d / "info.json") or {}
    if info.get("key") != key_of(cfg, paths.HARNESS / addr):
        return result | {"verdict": "FAIL", "stage": "sq_unaligned",
                         "lint": [f"sq_unaligned: no current answers for {addr}: run tools/sq_unaligned.py"]}
    if not info.get("kept"):
        return result | {"sq_unaligned": 0}
    h = pathlib.Path(build_dir).parent / (pathlib.Path(build_dir).name + "_sqh")
    shutil.rmtree(h, ignore_errors=True)
    h.mkdir(parents=True)
    for f in (paths.HARNESS / addr).iterdir():
        if f.is_file():
            shutil.copy(f, h / f.name)
    for f in ("vectors.txt", "expected.txt"):
        shutil.copy(d / f, h / f)
    from . import readable_check
    second = readable_check.check_readable(cfg, addr, src_file, pathlib.Path(build_dir).parent / (pathlib.Path(build_dir).name + "_sq"),
                                           sets=sets, reg=reg, harness_dir=h, callee_harness=callee_harness, lean=lean)
    # not the verdict: the kept vectors may be fewer than min_valid_vectors, and this pass is on top of a whole check
    if second.get("stage") == "compare" and not second.get("failing") and not second.get("run_error"):
        return result | {"sq_unaligned": info["kept"]}
    first = second.get("first_diffs") or [{"vector": None, "inputs": "", "diffs": ["(failures are only in the hidden set)"]}]
    for x in first:
        x["diffs"] = [DIFF_NOTE, *x.get("diffs", [])]
    return result | {"verdict": "FAIL", "stage": "sq_unaligned", "failing": second.get("failing", 0), "first_diffs": first,
                     "run_error": second.get("run_error"), "build": second.get("build", []), "lint": second.get("lint", [])}
