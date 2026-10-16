"""Readable stage: the matched raw C runs behind the out-pointer delivery (core/readable_outptr.py), and the runner's
vectors file they make: a trace run (T lines: how far the original reads a buffer), then two painted runs (B lines:
which bytes it wrote before the call), then D lines for the readable run."""
import hashlib
import json

from . import check, paths
from .readable_outptr import FLIPS, deliver, lines_for, parse_ob, parse_ot, trace_lines, type_sizes, with_lines


class RawRuns:
    """Runs the matched raw C of addr on vectors files, built once on the first run that is not cached. Each run's
    output (its V, OB and OT lines) is cached in cache_file by the raw text, the vectors, the sets and the runtime."""

    def __init__(self, cfg, addr, meta, harness, build_dir, sets):
        from . import readable_check as rcheck
        self.cfg, self.meta, self.harness, self.build_dir, self.sets = cfg, meta, harness, build_dir, sets
        self.raw = paths.MATCHED / f"{addr}.c"
        self.built = None
        self.cache_file = build_dir.parent / (build_dir.name + "_runs.json")
        self.runtime = "".join(f"{p.name}{p.stat().st_mtime_ns}" for d in (rcheck.RUNTIME_RD, paths.RUNTIME)
                               for p in sorted(d.glob("*.[ch]")))

    def available(self):
        return self.raw.exists()

    def run(self, vec):
        """The run's V/OB/OT text, or None (no raw C, or it does not build or run)."""
        from . import readable_check as rcheck
        if not self.raw.exists():
            return None
        key = hashlib.sha1(b"ob5\0" + self.raw.read_bytes() + b"\0" + vec.read_bytes() + b"\0"
                           + ",".join(self.sets).encode() + self.runtime.encode()).hexdigest()
        cache = paths.read_json(self.cache_file, {}) if self.cache_file.exists() else {}
        if key in cache:
            return cache[key]
        if self.built is None:
            self.built, _ = rcheck.build(self.cfg, [self.raw], check.gen_thunk(self.meta), {}, self.build_dir)
        if not self.built:
            return None
        out, err = rcheck.run(self.cfg, self.harness, self.build_dir, self.sets, vec)
        if err or not out.exists():
            return None
        text = "".join(ln for ln in out.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                       if ln.startswith(("V ", "OB ", "OT ")))
        cache = {k: v for k, v in cache.items() if k != key}
        cache[key] = text
        if len(cache) > 8:  # the latest runs only (another harness or raw C makes new keys)
            cache = dict(list(cache.items())[-8:])
        self.cache_file.write_text(json.dumps(cache), encoding="utf-8")
        return text


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
    need = {s[2] for s in specs if s[2] != "void"}
    sizes = {}
    if lines_for(exp, specs, lambda b: 1):  # some call passes a guest stack address there: size the pointees
        if need:
            sizes = type_sizes(cfg, need, build_dir.parent / (build_dir.name + "_outsz"), include_dirs)
    else:
        return src, 0
    # the raw build folder is wiped by each build: its vectors live in the readable build folder
    runner = raw_runs(cfg, addr, meta, harness, build_dir.parent / (build_dir.name + "_orig"), sets) if addr else None
    extents = {}
    tl = trace_lines(exp, specs)
    if tl and runner and runner.available():
        text = runner.run(with_lines(src, tl, build_dir / "vectors_t.txt", after_v=True))
        extents = parse_ot(text) if text else {}
    extra = lines_for(exp, specs, sizes.get, extents)
    if not extra:
        return src, 0
    dst = build_dir / "vectors_out.txt"
    runs = original_bytes(runner, src, extra, build_dir) if runner and runner.available() else None
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
    with_lines(src, extra, dst)
    return dst, sum(len(v) for v in extra.values())
