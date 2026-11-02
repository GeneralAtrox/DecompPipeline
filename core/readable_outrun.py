"""Readable stage: the matched raw C runs behind the out-pointer delivery (core/readable_outptr.py), and the runner's
vectors file they make: a trace run (T lines: how far the original reads a buffer), then two painted runs (B lines:
which bytes it wrote before the call), then D lines for the readable run."""
import hashlib
import json
import os
import pathlib
import threading

from . import check, paths, readable_stack
from .readable_outptr import FLIPS, deliver, lines_for, parked_bytes, parse_ob, parse_ot, trace_lines, type_sizes, with_lines


SALVAGE = True  # a run that times out keeps its finished vectors (RawRuns.run); False: the judge as it was (A/B proofs)
SKIP_MAX = 12   # vectors one run may leave out (each costs a timeout)


def keep_lines(text):
    return "".join(ln for ln in text.splitlines(keepends=True) if ln.startswith(("V ", "OB ", "OT ")))


def split_vectors(path):
    """(head lines, [(vector id, set, its lines from the V line)]) of a vectors file."""
    head, blocks = [], []
    for ln in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if ln.startswith("V "):
            t = ln.split()
            blocks.append((int(t[1]), t[2] if len(t) > 2 else "", []))
        (blocks[-1][2] if blocks else head).append(ln)
    return head, blocks


def finished(text):
    """(ids of the vectors a runner output holds whole, their V/OB/OT lines): a vector counts from its V line to its E
    line, which the runner flushes after each one."""
    done, lines, cur = set(), [], []
    for ln in text.splitlines(keepends=True):
        if ln.startswith("V "):
            cur = [ln]
        elif ln.startswith(("OB ", "OT ")) and cur:
            cur.append(ln)
        elif ln.strip() == "E" and cur:
            done.add(int(cur[0].split()[1]))
            lines += cur
            cur = []
    return done, "".join(lines)


class RawRuns:
    """Runs the matched raw C of addr on vectors files, built once on the first run that is not cached. Each run's
    output (its V, OB and OT lines) is cached in the function's cache_file (build/ADDR/raw_runs.json, shared by its
    build folders: the agent's check, the official judge, verify) by the raw text, the vectors, the sets and
    everything else the run reads (inputs())."""

    def __init__(self, cfg, addr, meta, harness, build_dir, sets):
        self.cfg, self.meta, self.harness, self.build_dir, self.sets = cfg, meta, harness, build_dir, sets
        self.raw = paths.MATCHED / f"{addr}.c"
        self.built = None
        self.cache_file = build_dir.parent / "raw_runs.json"
        self.runtime = None

    def inputs(self):
        """The rest of what a run reads, hashed once: the runner's build (its thunk from the harness meta, the
        readable runtime's files, the link options, the 32-bit compiler's environment, the judge's build and run
        code) and the runner's arguments (the binary, the code ranges, the FSCA table)."""
        from . import readable_check as rcheck
        if self.runtime is None:
            h = hashlib.sha256(b"raw runs 2\0" + check.gen_thunk(self.meta).encode() + b"\0"
                               + repr(rcheck.link_flags(self.cfg)).encode())
            table = paths.fsca_table() if self.cfg.get("fsca_table") else paths.FSCA_TABLE  # as rcheck.run
            core = pathlib.Path(__file__).parent
            files = [*(p for d in (rcheck.RUNTIME_RD, paths.RUNTIME) for p in sorted(d.iterdir()) if p.is_file()),
                     rcheck.MSVC_ENV_X86, self.cfg["binary_path"], paths.CODE_RANGES, pathlib.Path(table),
                     *(core / f for f in ("readable_check.py", "readable_outrun.py", "check.py", "pch_warnings.py"))]
            for p in files:
                try:
                    data = pathlib.Path(p).read_bytes()
                except OSError:
                    data = None
                h.update(pathlib.Path(p).name.encode() + b"\0" + (hashlib.sha256(data).digest() if data is not None
                                                                   else b"-") + b"\0")
            self.runtime = h.hexdigest()
        return self.runtime

    def available(self):
        return self.raw.exists()

    def run(self, vec):
        """The run's V/OB/OT text, or None (no raw C, or it does not build or run). A run that times out (the raw C
        loops on the bytes a B line painted: TC 8c038c2c hangs in 4 of 99 vectors) keeps the vectors it finished: the
        runner flushes after each vector's E line, so the output holds them whole, and the vector it hung in is the
        next one in the file. That vector is left out and the rest run again (up to SKIP_MAX times); a left-out
        vector has no OB lines, so its out-pointer lines stay O lines (vectors_file) while every other vector gets
        the original's bytes. The text is cached under the whole file's key. A run that finishes no vector, or fails
        any other way, is None, as before."""
        if not self.raw.exists():
            return None
        key = hashlib.sha1(b"ob7" + (b"s" if SALVAGE else b"") + b"\0" + self.raw.read_bytes() + b"\0"
                           + vec.read_bytes() + b"\0" + ",".join(self.sets).encode() + self.inputs().encode()).hexdigest()
        cache = paths.read_json(self.cache_file, {}) if self.cache_file.exists() else {}
        if key in cache:
            return cache[key]
        text, err, out = self._run_once(vec)
        if text is None and SALVAGE and err == "runner timeout":
            text = self._salvage(vec, out)
        if text is None:
            return None
        cache = paths.read_json(self.cache_file, {}) if self.cache_file.exists() else {}  # another folder's runs too
        cache = {k: v for k, v in cache.items() if k != key}
        cache[key] = text
        if len(cache) > 16:  # the latest runs only (another harness or raw C makes new keys)
            cache = dict(list(cache.items())[-16:])
        tmp = self.cache_file.with_name(f"{self.cache_file.name}.{os.getpid()}_{threading.get_ident()}")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        try:
            os.replace(tmp, self.cache_file)  # whole: a reader never sees half a file
        except OSError:  # Windows refused (a reader has it open): this run is not cached
            tmp.unlink(missing_ok=True)
        return text

    def _run_once(self, vec):
        """(text, None, out file) of one runner run, or (None, why, out file or None)."""
        from . import readable_check as rcheck
        if self.built is None:
            self.built, _ = rcheck.build(self.cfg, [self.raw], check.gen_thunk(self.meta), {}, self.build_dir)
        if not self.built:
            return None, "build", None
        out, err = rcheck.run(self.cfg, self.harness, self.build_dir, self.sets, vec)
        if err or not out.exists():
            return None, err or "no output", out
        return keep_lines(out.read_text(encoding="utf-8", errors="replace")), None, out

    def _salvage(self, vec, out):
        """The text of the vectors a timed-out run finished, and of the rest run again without the vector it hung in."""
        head, todo = split_vectors(vec)
        which = self.sets[0] if len(self.sets) == 1 else None  # the runner takes the vectors of these sets only
        texts = []
        for _ in range(SKIP_MAX):
            done, text = finished(out.read_text(encoding="utf-8", errors="replace") if out and out.exists() else "")
            texts.append(text)
            todo = [x for x in todo if x[0] not in done]
            hung = next((i for i, x in enumerate(todo) if which is None or x[1] == which), None)
            if hung is None:
                break
            del todo[hung]  # the vector the run hung in: left out
            if not todo:
                break
            part = self.build_dir / "vectors_rest.txt"
            part.write_text("".join(head + [ln for x in todo for ln in x[2]]), encoding="utf-8")
            text, err, out = self._run_once(part)
            if err is None:
                texts.append(text)
                break
            if err != "runner timeout":
                return None
        return "".join(texts) or None


def raw_runs(cfg, addr, meta, harness, build_dir, sets):
    return RawRuns(cfg, addr, meta, harness, build_dir, sets)


def original_bytes(runner, src, extra, build_dir):
    """[parse_ob] of the matched raw C run once per paint (FLIPS) with B lines for extra's O lines: the original's
    bytes at each such call, or None. Its results differ from the oracle's where the original reads the painted bytes
    after the call (that is the point); the runs only tell which bytes the original wrote (deliver), and the bytes
    delivered are always the vector's own, so a run that went another way before the call can only make the judge
    stricter (a byte the readable code must have written, or the vector's byte where the original read its own
    write)."""
    runs = []
    for k, flip in enumerate(FLIPS):
        vec = with_lines(src, {v: [f"B{x[1:]} {flip:x}" for x in xs] for v, xs in extra.items()},
                         build_dir / f"vectors_b{k}.txt", after_v=True)
        text = runner.run(vec)
        if text is None:
            return None
        runs.append(parse_ob(text))
    return runs


def vectors_file(cfg, meta, harness, specs, build_dir, include_dirs, addr=None, sets=("dev", "hidden")):
    """The runner's vectors: harness/vectors.txt itself, or (out-pointer calls) a copy in build_dir with lines before
    each vector's E line: D lines with the original's bytes at those calls (original_bytes, from runs of the matched
    raw C of addr), or O lines (canary copies of the vector's bytes) where those are not available. Returns (path,
    number of lines)."""
    from .check import parse_results
    src = harness / "vectors.txt"
    if not specs:
        return src, 0
    exp = parse_results(harness / "expected.txt")
    lo = readable_stack.frame_lo()  # a guest stack address is the own frame's (a global below it is memory)
    need = {s[2] for s in specs if s[2] != "void"}
    sizes = {}
    # a call passes a guest stack address there: size the pointees. A "trace" spec with no sizeof (void *) only gets its
    # size from the trace run below, so it counts here too: without it the local never got the mocked callee's bytes
    if lines_for(exp, specs, lambda b: 1, frame_lo=lo) or trace_lines(exp, specs, lo):
        if need:
            sizes = type_sizes(cfg, need, build_dir.parent / (build_dir.name + "_outsz"), include_dirs)
    else:
        return src, 0
    # the raw build folder is wiped by each build: its vectors live in the readable build folder
    runner = raw_runs(cfg, addr, meta, harness, build_dir.parent / (build_dir.name + "_orig"), sets) if addr else None
    extents = {}
    tl = trace_lines(exp, specs, lo)
    if tl and runner and runner.available():
        text = runner.run(with_lines(src, tl, build_dir / "vectors_t.txt", after_v=True))
        extents = parse_ot(text) if text else {}
    extra = lines_for(exp, specs, sizes.get, extents, lo)
    if not extra:
        return src, 0
    dst = build_dir / "vectors_out.txt"
    runs = original_bytes(runner, src, extra, build_dir) if runner and runner.available() else None
    parked = []  # S lines: the bytes the original stored itself before each call (stale_out_words)
    if runs:
        from .check import parse_vectors
        from .compare import Memory
        vecs, image = parse_vectors(src), cfg["binary_path"].read_bytes()
        for vid, lines in extra.items():
            for i, line in enumerate(lines):
                _, n, target, field, guest, _ = line.split()
                got = [(r.get(vid, {}).get((int(n), target, int(field))), flip) for r, flip in zip(runs, FLIPS)]
                if all(h is not None for h, _ in got):
                    lines[i] = f"D {n} {target} {field} {deliver(got, int(guest, 16), Memory(image, vecs[vid]))}"
                    parked.append(f"S {vid} {n} {field} {parked_bytes(got, int(guest, 16), Memory(image, vecs[vid]))}")
    with_lines(src, extra, dst)
    dst.with_suffix(".parked").write_text(chr(10).join(parked) + chr(10), encoding="utf-8")
    return dst, sum(len(v) for v in extra.values())
