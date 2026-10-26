"""Raw-judge PASS verdicts remembered within one wave (tools/wave.py), so step 5 does not judge again what step 4 just
judged: the same fact (matched/ADDR.c passes check.run_check on its harness) on the same inputs in one run.

Step 4 (tools/reprepare_stale.py --verdicts FILE) records every PASS its judge (refine.harness_stale) gives a
re-prepared function. Step 5 (wave.py _rejudge: oracle_fresh.recount with memo=FILE) takes a recorded PASS only while
its key still matches, and judges everything else as before (a FAIL is never taken from here: it is judged again,
with its detail). The key is a sha256 over every input run_check reads:
- the function's inputs: matched/ADDR.c, every file in harness/ADDR/, its packet's asm (frame_args lint);
- the workspace's: state/fr0_results.txt (core/ret_reg), the code ranges, the binary's sha256 (config);
- the judge itself: every core/*.py, every file of the target (runtime, hooks, target.json) but its translator/
  (core/translate_stage alone reads it, and the C it writes is the src above; its out/ held 88,679 files, 14 s a
  stamp), the whole config (paths.config(): config.json, config.local.json, target.json, project.json), the
  compiler's environment and the compiler itself: for each cached environment, the full version line of the cl.exe
  it runs, its Windows SDK and its toolset (compiler_identity; config "vcvars_args" pins them). A judge file's sha256 is kept while its size and mtime stay (_digest):
  a stamp, taken twice per function (core/refine.raw_pass_kept), re-reads only what changed.
A re-prepare after the PASS (the cascade), an edited matched C or a changed judge gives another key, so it is judged
again. tools/final_audit.py and tools/fresh_oracle.py never pass a memo: their recount judges every function.
"""
import hashlib
import json
import pathlib
import threading

from . import paths, ret_reg, toolcfg

_LOCK = threading.Lock()


def _files(h, label, files):
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            data = None
        h.update(f"{label} {p.name} ".encode() + (hashlib.sha256(data).hexdigest().encode() if data is not None
                                                 else b"-") + b"\n")


_DIGESTS = {}  # path -> (size, mtime_ns, sha256 hex) of the judge's own files (judge_stamp)


def compiler_identity(env_file):
    """The compiler a cached Visual Studio environment (env_file: state/msvc_env.json, msvc_env_x86.json) gives
    (core/toolcfg.compiler_identity): cl's full version line (its key file's while cl.exe is unchanged), the Windows
    SDK and the toolset; '-' without the cache."""
    env = paths.read_json(env_file)
    if not env:
        return "-"
    key = paths.read_json(toolcfg.key_path(env_file))
    return toolcfg.compiler_identity(env, key if isinstance(key, dict) else None)


def _digest(p):
    """p's sha256 (hex), read again only when its size or mtime changed; "-" when it cannot be read."""
    try:
        st = p.stat()
        was = _DIGESTS.get(p)
        if was and was[:2] == (st.st_size, st.st_mtime_ns):
            return was[2]
        d = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return "-"
    _DIGESTS[p] = (st.st_size, st.st_mtime_ns, d)
    return d


def _code(h, label, files):  # the same bytes as _files
    for p in files:
        h.update(f"{label} {p.name} {_digest(p)}\n".encode())


def _target_files():
    """Every file of the target the judge can read: not __pycache__, not translator/ (translate_stage's)."""
    out = []
    for p in paths.TARGET.iterdir():
        if p.is_file():
            out.append(p)
        elif p.is_dir() and p.name not in ("translator", "__pycache__"):
            out.extend(q for q in p.rglob("*") if q.is_file() and "__pycache__" not in q.parts)
    return sorted(out, key=lambda p: p.relative_to(paths.TARGET).as_posix())


def judge_stamp():
    """The hash of the judge and the workspace inputs every function shares."""
    h = hashlib.sha256()
    _code(h, "core", sorted((paths.TOOL / "core").glob("*.py")))
    _code(h, "target", _target_files())
    _files(h, "state", [paths.STATE / ret_reg.LIST, paths.CODE_RANGES, paths.MSVC_ENV])
    h.update(json.dumps(paths.config(), sort_keys=True, default=str).encode())
    h.update(f"compiler x64 {compiler_identity(paths.MSVC_ENV)} "
             f"x86 {compiler_identity(paths.STATE / 'msvc_env_x86.json')}\n".encode())
    return h.hexdigest()


def key(addr, src, stamp):
    """The key of judging src as addr's raw C now (stamp: judge_stamp())."""
    h = hashlib.sha256(f"judge {stamp} function {addr}\n".encode())
    _files(h, "src", [pathlib.Path(src)])
    harness = paths.HARNESS / addr
    _files(h, "harness", sorted(p for p in harness.iterdir() if p.is_file()) if harness.is_dir() else [])
    _files(h, "packet", sorted((paths.PACKETS / addr).glob("asm*.txt")))
    return h.hexdigest()


class Memo:
    """A JSON-lines file of {"addr", "key", "total"}: record() appends a PASS, passed() looks one up."""

    def __init__(self, path):
        self.path, self.stamp, self.seen, self.hits = pathlib.Path(path), judge_stamp(), None, 0

    def key_of(self, addr, src):
        """The key of judging src as addr now: take it before the judge runs and pass it to record()."""
        return key(addr, src, self.stamp)

    def record(self, addr, src, total, key_before=None):
        """key_before: key_of() taken before the judge read its inputs, so an input changed while it judged gives a
        key no later lookup matches (judged again). Without it, the key of the inputs now."""
        line = json.dumps({"addr": addr, "key": key_before or self.key_of(addr, src), "total": total}) + "\n"
        with _LOCK, open(self.path, "a", encoding="utf-8") as f:
            f.write(line)

    def passed(self, addr, src):
        """The vector count of a recorded PASS of src as addr whose key matches now, else None. The file is read once
        (step 5 only reads it)."""
        with _LOCK:
            if self.seen is None:
                try:
                    lines = self.path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                seen = {}
                for line in lines:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue  # a line cut short by a kill
                    seen[(r.get("addr"), r.get("key"))] = r.get("total")
                self.seen = seen
        total = self.seen.get((addr, key(addr, src, self.stamp)))
        if total is not None:
            with _LOCK:
                self.hits += 1
        return total


class RawPass:
    """Raw-judge PASSes kept across runs (state/raw_pass.jsonl, lines of {"addr", "key", "total"}), for the readable
    stage's first fact: matched/ADDR.c still passes its harness (core/refine.harness_stale, memo=). A readable run,
    a redo and the draft each judged it again on inputs nothing had changed since (0.62 s on GameCube, every run).
    A lookup takes the key now, judge_stamp() included, so a changed C, harness, packet, judge file or config judges
    again; a FAIL is never kept. The file is read again when its size or mtime changed (another process appended);
    a line cut short (a kill, two processes appending at once) is skipped: that function is judged again."""

    def __init__(self, path=None):
        self.path, self.seen, self.at, self.hits = pathlib.Path(path or paths.STATE / "raw_pass.jsonl"), {}, None, 0

    def _load(self):
        try:
            st = self.path.stat()
        except OSError:
            return
        if (st.st_size, st.st_mtime_ns) == self.at:
            return
        seen = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            seen[(r.get("addr"), r.get("key"))] = r.get("total")
        self.seen, self.at = seen, (st.st_size, st.st_mtime_ns)

    def key_of(self, addr, src):
        """The key of judging src as addr on the inputs as they are now."""
        return key(addr, src, judge_stamp())

    def passed(self, addr, k):
        """The vector count of a kept PASS of addr under key k (key_of), else None."""
        with _LOCK:
            self._load()
            total = self.seen.get((addr, k))
            if total is not None:
                self.hits += 1
        return total

    def record(self, addr, key_before, total):
        """key_before: key_of() taken before the judge read its inputs (an input changed meanwhile: no match)."""
        with _LOCK:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"addr": addr, "key": key_before, "total": total}) + "\n")
            self.seen[(addr, key_before)] = total
