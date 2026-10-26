"""Job registry: every heavy job (a Ghidra prepare run, a rejudge, a mutation audit, the full test suite, a re-check, a
wave, a regress gate) goes through one wrapper that refuses the redundant ones (CLAUDE.md "No redundant work").

Nearest existing: core/verdict_memo.py - it keys one function's judge PASS within a wave; nothing keys a whole job.
core/oplog.py records a driver's start per workspace but nothing reads it back, and core/fn_claim.py claims one
function. The registry is machine-wide and is read back before a job starts. It lives in tools/, not core/, because
core/verdict_memo.judge_stamp hashes every core/*.py: a core module would change the judge's stamp.

usage:
  py -3.12 tools/jobreg.py run   --why "WHAT IT COMPUTES" [--cross-check "WHAT IT CROSS-CHECKS"] [--changed "WHAT"]
                                 [--commit SHA] [--stamp S] [--fn-list FILE] [--input FILE ...] [--result PATH]
                                 [--timeout S] -- CMD [ARG ...]
  py -3.12 tools/jobreg.py check [the same options] -- CMD [ARG ...]     dry run: would the job run? exit 0 yes, 3 no
  py -3.12 tools/jobreg.py list  [--days N] [--all] [--json]
  py -3.12 tools/jobreg.py hook  [--mode warn|block]                     Claude Code PreToolUse hook (stdin JSON)

The key: a sha256 over labelled lines, the way core/verdict_memo keys a judge: the DP commit (plus a hash of the
uncommitted changes) or --commit/--stamp, the workspace (a local one's matched/, harness/, ledger and project.json, by
name, size and mtime), the function list (@FILE, --list FILE, --fn-list: by content), the checkout's config.local.json
(by content; it is never printed), --input files, the command and the limits. Two keys are kept:
  inputs  everything but the limits (bounded.py N, timeout N, --timeout, --time-cap, --slots, --workers, --jobs,
          --brake, --slot-wait, the wrapper's --timeout): what decides the result;
  key     inputs plus the limits.
Before a job starts, under a lock on the registry (core/filelock), the wrapper
  refuses     (exit 3) a job whose inputs are running now (an OS claim the running wrapper holds: core/filelock.claim);
  refuses     (exit 3) a job whose key failed or timed out before: the same job with the same limits;
  points      (exit 0) to a job whose inputs finished (done): its result, log and run time; nothing runs;
  allows      everything else. --cross-check "REASON" is the named exception (CLAUDE.md): it runs whatever was done or
              failed before (never beside a running one) and the reason is logged. --changed "WHAT" names an input
              the key cannot see (laptop2's workspace changed, a network fault fixed): it joins the key, so the
              job is a new one, and it is logged.
A job that ends 0 is done; 124 (tools/bounded.py, GNU timeout) or the wrapper's own --timeout is timeout; ssh's 255
(the connection, not the job) and Ctrl+C are lost (never refused); anything else failed. A wrapper that died leaves a
start with no end: list shows it as lost.
A command run on laptop2 (`ssh laptop2 "..."`) is keyed here too: every heavy job starts from this laptop. Its DP
commit is asked from the remote checkout the command names (`cd /d DIR`, `Set-Location DIR`, a DIR\\tools\\X.py),
else --commit is required. The registry: config "job_registry" "dir", else DP_JOBS_DIR, else
%LOCALAPPDATA%/DecompPipeline/job_registry (jobs.jsonl, running/*.lock, logs/), shared by every clone and worktree.
Config "job_registry" ({"dir", "hook_mode", "hook_exempt"}) has its defaults here (DEFAULTS), not in config.json:
core/verdict_memo.judge_stamp hashes the whole config, so a key there would change the judge's stamp. Set it in
config.local.json.
Records are JSON lines: {"event": start | end | reuse | refuse | outside, ...} (outside: a heavy job the hook saw
start outside the wrapper); tools/scorecard.py reads them (per-day waste).

The hook (Bash and PowerShell; .claude/settings.json) finds a heavy job in a command (also inside `ssh HOST "..."`,
`powershell -Command`, `cmd /c`, `bash -c`, tools/bounded.py and timeout, and in a local .sh/.ps1/.bat script the
command runs, at most 256 KB) that does not go through `jobreg.py run`.
warn (config "job_registry" "hook_mode", the default): the command runs, the agent is told to use the wrapper. block:
it is refused, except the commands config "hook_exempt" names (the Dreamcast re-check and the Saturn driver). Any
error in the hook lets the command run: the hook never stops or slows a job.
"""
import datetime
import hashlib
import json
import os
import pathlib
import platform
import posixpath
import re
import shlex
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL))

DIR_ENV = "DP_JOBS_DIR"
DEFAULTS = {"dir": "", "hook_mode": "warn", "hook_exempt": [r"recheck_driver\.py", r"saturn_driver\.sh"]}
REFUSED = 3

# ---------------------------------------------------------------------------------------------------------- heavy jobs
# script basename -> (kind, a test of its arguments: True = heavy). Arguments that only print, plan or report are light.
_DRY = {"--dry", "--dry-run", "-h", "--help", "--version", "dry", "status", "--report-only"}


def _not_dry(args):
    return not _DRY.intersection(args)


def _sub(*subs):
    return lambda args: _not_dry(args) and any(a in subs for a in args)


def _few(args):
    """Heavy unless it names at most 3 functions (--only A,B,C or addresses; dp_judge's --all only widens the set
    --only narrows), from no list file."""
    if not _not_dry(args):
        return False
    if any(a.startswith("@") for a in args):
        return True
    named = []
    for i, a in enumerate(args):
        if a == "--only" and i + 1 < len(args):
            named += args[i + 1].split(",") if not re.search(r"[$@]", args[i + 1]) else ["?"] * 9
        elif re.fullmatch(r"(0x)?[0-9a-fA-F]{8}", a):
            named.append(a)
    return not named or len(named) > 3


def _pytest_full(args):
    """A pytest run with no test file, node or -k named, or the whole of tests/test_tooling.py: the whole suite."""
    skip_next = {"-p", "-k", "-m", "-o", "-c", "--deselect", "--rootdir", "--basetemp", "--maxfail", "-n", "--tb",
                 "--timeout", "--ignore", "-W", "--durations", "--junitxml", "--log-file"}
    i, named, tooling = 0, False, False
    while i < len(args):
        a = args[i]
        if a == "-k" or a.startswith("-k="):
            return False
        if a in skip_next:
            i += 2
            continue
        if "::" in a:
            return False
        if not a.startswith("-") and (re.search(r"(^|[/\\])test_|\.py$", a) or "$" in a):
            named = True  # a test file, or a shell variable naming some (unknown: not the suite)
            tooling = tooling or bool(re.search(r"(^|[/\\])test_tooling(\.py)?$", a))
        i += 1
    return (tooling or not named) and _not_dry(args) and "--collect-only" not in args and "--co" not in args


HEAVY = {
    "decomp.py": ("prepare", _sub("prepare", "serve", "setup", "catalog", "investigate")),  # divergence (below)
    "refine.py": ("rejudge", lambda a: "verify" in a and _few(a)),  # "run" and "library": agent run, build (below)
    "prepare_list.py": ("prepare", _not_dry),
    "reprepare_stale.py": ("prepare", _not_dry),
    "idle_prepare.py": ("prepare", _not_dry),
    "oracle_probe.py": ("prepare", _not_dry),
    "selective_restamp.py": ("prepare", _not_dry),
    "land_smoke.py": ("prepare", _not_dry),
    "call_coverage.py": ("prepare", _sub("reach")),
    "fresh_oracle.py": ("rejudge", _sub("--recount")),
    "final_audit.py": ("rejudge", _not_dry),
    "dp_judge.py": ("rejudge", _few),
    "translate_backlog.py": ("rejudge", _few),
    "readable_tx.py": ("rejudge", _few),
    "readable_parity.py": ("rejudge", _few),
    "pcode_parity.py": ("rejudge", _few),
    "coverage.py": ("mutation audit", _not_dry),  # a translator's coverage tool (vectors and mutants)
    "compile_all.py": ("build", _not_dry),
    "mutation_check.py": ("mutation audit", _not_dry),
    "mutation_audit.py": ("mutation audit", _sub("run")),
    "regress.py": ("regress gate", _not_dry),
    "regress_project.py": ("regress gate", _not_dry),
    "wave.py": ("wave", _sub("run")),
    "recheck_driver.py": ("re-check", _not_dry),
    "saturn_driver.sh": ("re-check", lambda a: True),
    "test_tooling.py": ("test suite", _not_dry),
    "runsuite.py": ("test suite", lambda a: _not_dry(a) and ("--only" not in a or "test_tooling.py" in " ".join(a))),
    "pytest_msvc.py": ("test suite", lambda a: _pytest_full(a)),
    "analyzeHeadless": ("ghidra run", lambda a: True),
    "analyzeHeadless.bat": ("ghidra run", lambda a: True),
}
PYTHONS = {"python", "python3", "py", "pythonw", "python3.12"}
SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "time", "exec", "nohup", "env", "command",
                  "builtin", "!", "{", "(", "call", "start", "&", ".", "xargs", "sudo", "-"}
LIMIT_FLAGS = ("--timeout", "--time-cap", "--slots", "--workers", "--jobs", "--brake", "--slot-wait")


def _base(tok):
    b = re.split(r"[/\\]", tok.strip("\"'"))[-1]
    return b[:-4] if b.lower().endswith(".exe") else b


def _strip_heredocs(text, written=None):
    """The text without here-document bodies (a file being written, not a command run). A body written to a file
    (`cat > FILE <<EOF`) goes in written[FILE's base name]: the hook runs before the file exists. Any other body is
    its command's stdin (`ssh HOST "powershell -Command -" <<EOF`): it goes in written["heredoc-N"] and the line reads
    `< heredoc-N`, so the command that runs its stdin scans it."""
    out, end, body, target = [], None, [], None
    for line in text.splitlines():
        if end is not None:
            if line.strip() == end:
                if written is not None:
                    written[_base(target).lower()] = "\n".join(body)
                end = None
            else:
                body.append(line)
            continue
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1", line)
        if m:
            end, body = m.group(2), []
            t = re.search(r"(?:cat|tee)\s*(?:-a\s*)?>?>?\s*(\"[^\"]+\"|'[^']+'|[^\s<>|&;]+)", line[:m.start()])
            target = t.group(1) if t else f"heredoc-{len(written or ())}"
            if not t:
                line = line[:m.start()] + f"< {target}" + line[m.end():]
        out.append(line)
    return "\n".join(out)


def _path(path, cx):
    """A script path with the command's own variables (S=... earlier in it), the environment's and its `cd` applied;
    None if a variable is still unknown."""
    p = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: cx["vars"].get(m.group(1), os.environ.get(m.group(1), m.group(0))),
               path.strip("\"'"))
    if "$" in p or "%" in p:
        return None
    p = re.sub(r"^/([a-zA-Z])/", r"\1:/", p)  # Git Bash's /c/... on Windows
    if cx["cwd"] and not re.match(r"([a-zA-Z]:)?[/\\]", p):
        p = cx["cwd"].rstrip("/\\") + "/" + p
    return posixpath.normpath(p.replace("\\", "/"))


def _segments(text):
    """Split a command line into simple commands on unquoted ; && || | & and newlines."""
    segs, cur, q, i = [], [], None, 0
    while i < len(text):
        c = text[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c in ";|&\n()":
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()]


def _tokens(seg):
    seg = re.sub(r"\\(?=[\w.%-])", "/", seg)  # Windows path separators; \" and \$ stay escapes
    try:
        toks = shlex.split(seg, posix=True)
    except ValueError:
        toks = seg.split()
    out, skip = [], False
    for t in toks:  # redirections and their targets are not arguments
        if skip:
            skip = False
        elif re.fullmatch(r"\d*>>?|<|\d*>&\d*", t):
            skip = not t.endswith("&")
        elif not re.match(r"\d*>>?\S|<\S", t):
            out.append(t)
    return out


def _ssh_remote(toks):
    """(host, remote command) of an ssh token list, or (None, None)."""
    i, opts_arg = 1, set("bcDEeFIiJLlmOopQRSWw")
    while i < len(toks) and (toks[i].startswith("-") or toks[i].startswith("$")):  # $Q: a variable of options
        i += 2 if toks[i][0] == "-" and toks[i][-1] in opts_arg and len(toks[i]) == 2 else 1
    if i >= len(toks):
        return None, None
    return toks[i].split("@")[-1], " ".join(toks[i + 1:])


def _script(path, depth, host, cx):
    """Scan a shell or PowerShell script file the command runs: one a here-document in the command wrote, else
    cx["read"](path) (path -> text or None)."""
    if re.search(r"\.(sh|bash|ps1|bat|cmd)$", _path(path, cx) or path, re.I) and not host:
        text = _read(path, cx)
        if text:
            _scan(text, depth + 1, host, cx)


def _read(path, cx):
    p = _path(path, cx)
    text = cx["written"].get(_base(p or path).lower())
    if text is None and p:
        text = cx["read"](p)
    return text


def _find(toks, depth, host, cx, stdin=None):
    """Append (kind, host, text) to cx["found"] for each heavy invocation in one simple command's tokens."""
    found = cx["found"]
    while toks and (re.fullmatch(r"[A-Za-z_]\w*=.*", toks[0]) or toks[0] in SHELL_KEYWORDS
                    or toks[0].startswith("$env:")):
        if re.fullmatch(r"DRY=[1-9a-zA-Z]\S*", toks[0]):
            return  # a driver's dry run
        m = re.fullmatch(r"(?:\$env:)?([A-Za-z_]\w*)=(.*)", toks[0])
        if m:
            cx["vars"][m.group(1)] = m.group(2)
        toks = toks[1:]
    if not toks:
        return
    b = _base(toks[0]).lower()
    if b in ("cd", "set-location", "pushd") and len(toks) == 2 and not host:
        cx["cwd"] = _path(toks[1], cx)
        return
    if b in ("timeout", "timeout.exe"):
        i = 1
        while i < len(toks) and toks[i].startswith("-"):
            i += 1
        return _find(toks[i + 1:], depth, host, cx, stdin)
    if b == "ssh":
        h, rest = _ssh_remote(toks)
        if rest:
            _scan(rest, depth + 1, h, cx, stdin)
        return
    if b in ("bash", "sh"):
        if toks[1:2] == ["-n"]:
            return  # a syntax check
        if toks[1:2] == ["-c"] and len(toks) > 2:
            return _scan(toks[2], depth + 1, host, cx)
        args = [t for t in toks[1:] if not t.startswith("-")]
        if args and not _hit(_base(args[0]), toks[2:], toks, host, found):
            _script(args[0], depth, host, cx)
        elif not args and stdin:  # the script comes on stdin: `bash -s < FILE`, `ssh HOST bash <<EOF`
            text = _read(stdin, cx)
            if text:
                _scan(text, depth + 1, host, cx)
        return
    if b in ("powershell", "pwsh"):
        for j, t in enumerate(toks[1:], 1):
            if t.lower() in ("-command", "-c") and j + 1 < len(toks):
                if toks[j + 1] == "-" and stdin:  # the script comes on stdin: `... -Command - < FILE`
                    text = _read(stdin, cx)
                    return _scan(text, depth + 1, host, cx) if text else None
                return _scan(" ".join(toks[j + 1:]), depth + 1, host, cx)
            if t.lower() == "-file" and j + 1 < len(toks):
                if not _hit(_base(toks[j + 1]), toks[j + 2:], toks, host, found):
                    _script(toks[j + 1], depth, host, cx)
                return
        return
    if b == "cmd" and len(toks) > 2 and toks[1].lower() in ("/c", "/k"):
        return _scan(" ".join(toks[2:]), depth + 1, host, cx)
    if b == "pytest":
        if _pytest_full(toks[1:]):
            found.append(("test suite", host, " ".join(toks)))
        return
    if b in PYTHONS or re.fullmatch(r"python3?(\.\d+)?", b):
        i = 1
        while i < len(toks) and toks[i].startswith("-") and toks[i] not in ("-m", "-c", "-"):
            i += 2 if toks[i] in ("-W", "-X") else 1
        if i >= len(toks) or toks[i] in ("-c", "-"):
            return
        if toks[i] == "-m":
            mod, rest = (toks[i + 1] if i + 1 < len(toks) else ""), toks[i + 2:]
            if mod in ("cProfile", "profile", "trace") and rest:  # a profiled run: the script after the options
                j = 0
                while j < len(rest) and rest[j].startswith("-"):
                    j += 2 if rest[j] in ("-o", "-s", "--outfile", "--sort") else 1
                return _find([toks[0]] + rest[j:], depth, host, cx, stdin)
            if (mod == "pytest" and _pytest_full(rest)) or (mod == "unittest" and (
                    "test_tooling" in rest or ("discover" in rest and "-p" not in rest) or not rest)):
                found.append(("test suite", host, " ".join(toks)))
            return
        script, args = _base(toks[i]), toks[i + 1:]
        if script == "jobreg.py":
            return  # wrapped: the registry decides
        if script == "bounded.py":
            j = 1 if args and re.fullmatch(r"[\d.]+", args[0]) else 0
            if j < len(args) and args[j] == "--":
                j += 1
            return _find(args[j:], depth, host, cx, stdin)
        if _hit(script, args, toks, host, found):
            return
        for j, t in enumerate(args):  # a wrapper script (a RAM guard, a pass setter) running a command
            if _base(t).lower() in PYTHONS or _base(t).lower() in ("timeout", "bash", "ssh"):
                return _find(args[j:], depth, host, cx, stdin)
        return
    if not _hit(_base(toks[0]), toks[1:], toks, host, found):
        _script(toks[0], depth, host, cx)  # for example ./run.sh, & C:\x\l2_prep.ps1


def _hit(script, args, toks, host, found):
    rule = HEAVY.get(script)
    if script == "refine.py" and _not_dry(args) and {"run", "library"}.intersection(args):
        rule = ("agent run" if "run" in args else "build", None)
    elif script == "decomp.py" and _not_dry(args) and "divergence" in args:
        rule = ("divergence run", None)  # a headless emulator run against the port
    elif rule and not rule[1](args):
        rule = None
    if rule:
        kind = rule[0]
        if script == "regress.py" and {"--games", "--ws"}.intersection(a.split("=")[0] for a in args):
            kind = "translator regress"  # a translator's own regress (targets/*/translator/tools), not DP's gate
        found.append((kind, host, " ".join(toks)))
        return True
    return False


def _scan(text, depth, host, cx, stdin=None):
    if depth > 4:
        return
    text = re.sub(r"\\\r?\n", " ", _strip_heredocs(text, cx["written"]))  # line continuations
    for seg in _segments(text):
        m = re.search(r"(?:^|\s)<\s*(\"[^\"]+\"|'[^']+'|[^\s<>|&;]+)", seg)
        _find(_tokens(seg), depth, host, cx, m.group(1).strip("\"'") if m else stdin)


def read_script(path, limit=262144):
    """classify's default reader: a local script file's text (at most `limit` bytes), or None."""
    try:
        p = pathlib.Path(path)
        if p.is_file() and p.stat().st_size <= limit:
            return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass
    return None


def classify(command, read=None, cwd=None):
    """[(kind, host or None, the simple command)] of every heavy job the command line starts outside the wrapper.
    read: path -> text of a script file the command runs (read_script), or None to look at the command alone; cwd:
    where the command starts (a relative script path)."""
    cx = {"found": [], "read": read or (lambda path: None), "vars": {}, "cwd": cwd, "written": {}}
    try:
        _scan(command or "", 0, None, cx)
    except RecursionError:
        pass
    return cx["found"]


# ------------------------------------------------------------------------------------------------------ the registry
def settings(cfg=None):
    """(job_registry config with defaults, folder)."""
    if cfg is None:
        from core import toolcfg
        cfg = toolcfg.load(TOOL)
    s = dict(DEFAULTS, **(cfg.get("job_registry") or {}))
    import tempfile
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    folder = pathlib.Path(s["dir"] or os.environ.get(DIR_ENV) or pathlib.Path(base) / "DecompPipeline" / "job_registry")
    return s, folder


def records(folder):
    """Every record of the registry, in order (a line cut short by a kill is skipped)."""
    out = []
    try:
        lines = (pathlib.Path(folder) / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(folder, rec, wait=60):
    from core import filelock
    path = pathlib.Path(folder) / "jobs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.locked(path, wait=wait):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def jobs(recs):
    """{job id: the start record with "end" (its end record, or None)} in start order."""
    out = {}
    for r in recs:
        if r.get("event") == "start":
            out[r["id"]] = dict(r, end=None)
        elif r.get("event") == "end" and r.get("id") in out:
            out[r["id"]]["end"] = r
    return out


def status_of(job):
    """done | failed | timeout | lost | running (no end yet: the caller decides running or lost by the claim)."""
    return job["end"]["status"] if job.get("end") else "running"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------------------ the key
def _split_limits(argv):
    """(the command without its limits, the limits) of an argv list; a limit inside a remote string is found too."""
    text = " ".join(argv)
    limits = []
    pats = [r"bounded\.py\s+([\d.]+)", r"(?:^|\s)timeout(?:\.exe)?\s+(?:-\S+\s+)*([\d.]+[smhd]?)"] + \
           [re.escape(f) + r"[=\s]+(\S+)" for f in LIMIT_FLAGS]
    for p in pats:
        for m in re.finditer(p, text):
            limits.append(m.group(0).strip())
        text = re.sub(p, lambda m: m.group(0)[:m.start(1) - m.start(0)] + "N", text)
    return re.sub(r"\s+", " ", text).strip(), sorted(limits)


def _git(args, cwd, timeout=30):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def local_commit(folder):
    """'SHA' or 'SHA+dirty:HASH' of the git checkout holding folder, or None."""
    top = _git(["rev-parse", "--show-toplevel"], folder)
    if not top:
        return None
    top = top.decode().strip()
    head = (_git(["rev-parse", "HEAD"], top) or b"").decode().strip()
    diff = _git(["diff", "HEAD", "--binary"], top, 120) or b""
    others = (_git(["ls-files", "--others", "--exclude-standard", "--", "core", "tools", "targets", "decomp.py",
                    "refine.py"], top) or b"").decode().split("\n")
    h = hashlib.sha256(diff)
    for name in sorted(o for o in others if o):
        h.update(f"untracked {name} {_file_digest(pathlib.Path(top) / name)}\n".encode())
    return head + (f"+dirty:{h.hexdigest()[:16]}" if diff or any(others) else "")


def _file_digest(p):
    try:
        return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
    except OSError:
        return "-"


def _ws_fingerprint(ws):
    """A local workspace's state as the job finds it: matched/, harness/ (name, size, mtime), ledger, project.json."""
    h, n = hashlib.sha256(), 0
    ws = pathlib.Path(ws)
    for sub in ("matched", "harness"):
        root = ws / sub
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                try:
                    st = os.stat(os.path.join(dirpath, f))
                except OSError:
                    continue
                h.update(f"{os.path.relpath(os.path.join(dirpath, f), ws)} {st.st_size} {st.st_mtime_ns}\n".encode())
                n += 1
    for f in ("state/ledger.json", "project.json"):
        h.update(f"{f} {_file_digest(ws / f)}\n".encode())
    return f"{h.hexdigest()} ({n} files)"


def _remote_dirs(text):
    dirs = []
    for p in (r"cd\s+(?:/d\s+)?(\"[^\"]+\"|\S+)", r"Set-Location\s+(?:-Path\s+)?(\"[^\"]+\"|'[^']+'|[^;\s]+)",
              r"(\S+?)[\\/]tools[\\/]\w+\.py"):
        dirs += [m.group(1).strip("\"'") for m in re.finditer(p, text)]
    return dirs


def remote_commit(host, text):
    """The DP commit of the checkout the remote command runs in ('SHA' or 'SHA+dirty'), or None."""
    for d in _remote_dirs(text):
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f'git -C "{d}" rev-parse HEAD && git -C "{d}" '
                                f'status --porcelain --untracked-files=no'], capture_output=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return None
        lines = [ln for ln in r.stdout.decode(errors="replace").splitlines() if ln.strip()]
        if r.returncode == 0 and lines and re.fullmatch(r"[0-9a-f]{40}", lines[0].strip()):
            return lines[0].strip() + ("+dirty" if len(lines) > 1 else "")
    return None


def _argv_host(argv):
    """The ssh host the command runs on, or None (this machine)."""
    toks = list(argv)
    while toks and (_base(toks[0]).lower() in ("timeout", "timeout.exe") or re.fullmatch(r"[\d.]+[smhd]?", toks[0])
                    or toks[0].startswith("-")):
        toks = toks[1:]
    if toks and _base(toks[0]).lower() == "ssh":
        return _ssh_remote(toks)
    return None, None


def _arg_values(argv, flags):
    out = []
    for i, a in enumerate(argv):
        for f in flags:
            if a == f and i + 1 < len(argv):
                out.append(argv[i + 1])
            elif a.startswith(f + "="):
                out.append(a[len(f) + 1:])
    return out


def job_key(argv, a, cwd=None):
    """{"inputs", "key", "host", "commit", "lines"} of a job, or raises ValueError (what is missing)."""
    cwd = pathlib.Path(cwd or os.getcwd())
    host, remote = _argv_host(argv)
    text, limits = _split_limits(argv)
    if a.timeout:
        limits.append(f"wrapper timeout {a.timeout:g}")
    lines = [f"command {text}"]
    commit = a.commit or a.stamp
    if not commit:
        if host:
            commit = remote_commit(host, remote)
            if not commit:
                raise ValueError(f"the DP commit on {host} could not be read from the command (no cd /d DIR, "
                                 "Set-Location DIR or DIR\\tools\\X.py): give --commit SHA (or --stamp)")
        else:
            script = next((t for t in argv if re.search(r"(^|[/\\])(tools[/\\]\w+\.py|decomp\.py|refine\.py)$", t)),
                          None)
            where = (cwd / script).resolve().parent if script else cwd
            commit = local_commit(where if where.is_dir() else cwd) or "no git checkout"
    lines.append(f"dp {commit}")
    if a.stamp:
        lines.append(f"stamp {a.stamp}")
    if not host:
        for ws in _arg_values(argv, ("--project", "--ws")):
            ws = ws.split("=", 1)[-1] if "=" in ws and not pathlib.Path(ws).exists() else ws
            if (cwd / ws).is_dir():
                lines.append(f"workspace {(cwd / ws).resolve()} {_ws_fingerprint(cwd / ws)}")
        conf = TOOL / "config.local.json"
        lines.append(f"config.local {_file_digest(conf)}")
    lists = [t[1:] for t in argv if t.startswith("@")] + _arg_values(argv, ("--list",)) + (a.fn_list or [])
    for f in lists:
        p = cwd / f
        lines.append(f"functions {f} {_file_digest(p) if p.is_file() else '(remote or missing)'}")
    for f in a.input or []:
        lines.append(f"input {f} {_file_digest(cwd / f)}")
    if a.changed:
        lines.append(f"changed {a.changed}")
    inputs = hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()
    key = hashlib.sha256(f"inputs {inputs}\nlimits {' | '.join(limits)}\n".encode()).hexdigest()
    return {"inputs": inputs, "key": key, "host": host or platform.node(), "commit": commit, "limits": limits,
            "lines": lines}


# ----------------------------------------------------------------------------------------------------------- decide
def _claim_path(folder, inputs):
    return pathlib.Path(folder) / "running" / f"{inputs[:24]}.lock"


def held(folder, inputs):
    """The owner record of a running job with these inputs (its wrapper holds the claim), else None."""
    from core import filelock
    try:
        with filelock.claim(_claim_path(folder, inputs), wait=0):
            return None
    except filelock.Busy:
        return filelock.owner_of(_claim_path(folder, inputs)) or {"pid": "?"}


def decide(recs, k, cross_check=None):
    """(verdict, the record it rests on): run | done (reuse) | failed (refuse). A running job is the claim's to say."""
    js = list(jobs(recs).values())
    if not cross_check:
        done = [j for j in js if j["inputs"] == k["inputs"] and status_of(j) == "done"]
        if done:
            return "done", done[-1]
        bad = [j for j in js if j["key"] == k["key"] and status_of(j) in ("failed", "timeout")]
        if bad:
            return "failed", bad[-1]
    return "run", None


def _describe(j):
    end = j.get("end") or {}
    out = (f"job {j['id']} on {j['host']}, started {j['started']}: {status_of(j)}"
           + (f" (exit {end.get('exit')}, {end.get('seconds', 0) / 3600:.2f} h)" if end else "")
           + f"\n  why: {j.get('why')}\n  command: {j.get('command')}")
    if j.get("result"):
        out += f"\n  result: {j['result']}"
    if j.get("log"):
        out += f"\n  log: {j['log']}"
    return out


# --------------------------------------------------------------------------------------------------------- commands
def _run_cmd(argv, log, timeout):
    """Run argv, its output to the console and to log; (exit code, status)."""
    from tools.bounded import kill_tree
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as f:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        deadline = time.monotonic() + timeout if timeout else None
        try:
            import threading

            def pump():
                for chunk in iter(lambda: p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(4096),
                                  b""):
                    f.write(chunk)
                    f.flush()
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.flush()
            t = threading.Thread(target=pump, daemon=True)
            t.start()
            while p.poll() is None:
                if deadline and time.monotonic() > deadline:
                    kill_tree(p.pid)
                    p.wait(timeout=60)
                    t.join(5)
                    return 124, "timeout"
                time.sleep(0.5)
            t.join(30)
        except KeyboardInterrupt:
            kill_tree(p.pid)
            return 130, "lost"
    rc = p.returncode
    host, _ = _argv_host(argv)
    if rc == 0:
        return rc, "done"
    if rc == 124:
        return rc, "timeout"
    if rc == 255 and host:
        return rc, "lost"
    return rc, "failed"


def cmd_run(a, dry=False):
    s, folder = settings()
    argv = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not argv:
        print("jobreg: no command after --", file=sys.stderr)
        return 2
    try:
        k = job_key(argv, a)
    except ValueError as e:
        print(f"jobreg: REFUSED (no key): {e}", file=sys.stderr)
        return REFUSED
    base = {"key": k["key"], "inputs": k["inputs"], "host": k["host"], "command": " ".join(argv), "why": a.why,
            "commit": k["commit"], "limits": k["limits"], "pid": os.getpid()}
    from core import filelock
    owner = held(folder, k["inputs"])
    if owner:
        msg = (f"REFUSED: the same job is running now ({filelock.describe(owner)}). Wait for it and use its "
               "result; run `jobreg.py list` to see it")
        if not dry:
            _append(folder, dict(base, event="refuse", reason="running", at=_now(), cross_check=a.cross_check))
        print(f"jobreg: {msg}", file=sys.stderr)
        return REFUSED
    verdict, j = decide(records(folder), k, a.cross_check)
    if verdict == "done":
        print(f"jobreg: NOT RUN: this job already finished; its result is reused.\n{_describe(j)}\n"
              "If an input the key cannot see changed, say what with --changed \"...\"; a deliberate cross-check "
              "runs with --cross-check \"...\".")
        if not dry:
            _append(folder, dict(base, event="reuse", of=j["id"], saved_s=(j["end"] or {}).get("seconds", 0),
                                 at=_now()))
        return 0
    if verdict == "failed":
        print(f"jobreg: REFUSED: this job {status_of(j)} before with the same limits; it would {status_of(j)} "
              f"again.\n{_describe(j)}\nChange the limits or the inputs (a fix: a new commit), name an outside "
              "change with --changed \"...\", or run a named cross-check with --cross-check \"...\".",
              file=sys.stderr)
        if not dry:
            _append(folder, dict(base, event="refuse", reason=status_of(j), of=j["id"], at=_now()))
        return REFUSED
    if dry:
        print(f"jobreg: would run ({'cross-check: ' + a.cross_check if a.cross_check else 'no earlier result'})"
              f"\n  key {k['key'][:16]}  inputs {k['inputs'][:16]}  host {k['host']}  dp {k['commit']}"
              + "".join(f"\n  {ln}" for ln in k["lines"][1:]))
        return 0
    if not a.why:
        print("jobreg: --why \"what it computes\" is required", file=sys.stderr)
        return 2
    try:
        with filelock.claim(_claim_path(folder, k["inputs"]), wait=2,  # past a list or check probing the claim
                            owner={"purpose": "job " + (a.why or "")[:80], "command": base["command"][:300]}):
            jid = f"{time.strftime('%Y%m%d-%H%M%S')}-{k['key'][:8]}-{os.urandom(2).hex()}"  # a cross-check: same key
            log = folder / "logs" / f"{jid}.log"
            _append(folder, dict(base, event="start", id=jid, started=_now(), cross_check=a.cross_check,
                                 changed=a.changed, result=a.result, log=str(log)))
            t0 = time.time()
            rc, status = _run_cmd(argv, log, a.timeout)
            _append(folder, {"event": "end", "id": jid, "at": _now(), "exit": rc, "status": status,
                             "seconds": round(time.time() - t0, 1)})
            print(f"jobreg: job {jid} {status} (exit {rc}); log {log}", file=sys.stderr)
            return rc
    except filelock.Busy as e:  # a rival started the same job between the check and the claim
        print(f"jobreg: REFUSED: {e}", file=sys.stderr)
        return REFUSED


def cmd_list(a):
    _, folder = settings()
    recs = records(folder)
    js = list(jobs(recs).values())
    cut = (datetime.datetime.now() - datetime.timedelta(days=a.days)).isoformat() if not a.all else ""
    js = [j for j in js if j["started"] >= cut]
    for j in js:
        st = status_of(j)
        if st == "running" and not held(folder, j["inputs"]):
            j["status"] = "lost"
        else:
            j["status"] = st
    if a.json:
        print(json.dumps(js, indent=1))
        return 0
    print(f"job registry {folder}: {len(js)} job(s)" + ("" if a.all else f" in {a.days:g} day(s)"))
    for j in js:
        end = j.get("end") or {}
        print(f"  {j['id']}  {j['status']:8} {j['host']:>12}  {end.get('seconds', 0) / 3600:6.2f} h  "
              f"{'[cross-check] ' if j.get('cross_check') else ''}{(j.get('why') or '')[:70]}")
    return 0


def hook_reply(payload, mode, exempt, folder=None, record=False):
    """The hook's stdout (a JSON string) for one PreToolUse payload, or "" to say nothing. record: also log the heavy
    job seen outside the wrapper ({"event": "outside"}: tools/scorecard.py counts them and their repeats); a busy
    registry is skipped after 2 s, never waited on."""
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return ""
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    hits = classify(cmd, read_script, payload.get("cwd"))
    if not hits:
        return ""
    kinds = ", ".join(sorted({h[0] + (f" on {h[1]}" if h[1] else "") for h in hits}))
    seen = ""
    if folder is not None:
        same = [j for j in jobs(records(folder)).values() if j.get("command") and j["command"] in cmd]
        if same:
            seen = f" The registry already has this command: {same[-1]['id']} ({status_of(same[-1])})."
    msg = (f"Heavy job ({kinds}) started outside the job registry. CLAUDE.md 'No redundant work': run it through "
           f"`py -3.12 {TOOL.as_posix()}/tools/jobreg.py run --why \"<what it computes>\" -- <command>`; it refuses "
           "a duplicate of a running job or of one that failed with the same limits, and points to a finished "
           "one's result. Check first with `jobreg.py check -- <command>`; a deliberate cross-check is "
           "--cross-check \"<what>\"." + seen)
    out = {"hookEventName": "PreToolUse"}
    deny = mode == "block" and not any(re.search(x, cmd) for x in exempt)
    if deny:
        out.update(permissionDecision="deny", permissionDecisionReason=msg)
    else:
        out["additionalContext"] = msg
    if record and folder is not None:
        try:
            _append(folder, {"event": "outside", "at": _now(), "kinds": sorted({h[0] for h in hits}),
                             "hosts": sorted({h[1] or platform.node() for h in hits}), "denied": deny,
                             "session": payload.get("session_id"), "cwd": payload.get("cwd"),
                             "jobs": [h[2][:300] for h in hits]}, wait=2)
        except Exception:  # the registry busy or unwritable: the reply still goes out
            pass
    return json.dumps({"hookSpecificOutput": out})


def cmd_hook(a):
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not classify((payload.get("tool_input") or {}).get("command") or "", read_script, payload.get("cwd")):
            return 0  # a light command: no config read, no registry
        s, folder = settings()
        reply = hook_reply(payload, a.mode or s["hook_mode"], s["hook_exempt"], folder, record=True)
        if reply:
            print(reply)
    except Exception:  # never stop a command because the hook failed
        pass
    return 0


def main(argv=None):
    import argparse  # here, not at the top: the hook runs before every command and needs none of it
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="action", required=True)
    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("--why")
        p.add_argument("--cross-check")
        p.add_argument("--changed")
        p.add_argument("--commit")
        p.add_argument("--stamp")
        p.add_argument("--fn-list", action="append")
        p.add_argument("--input", action="append")
        p.add_argument("--result")
        p.add_argument("--timeout", type=float)
        p.add_argument("cmd", nargs=argparse.REMAINDER)
    p = sub.add_parser("list")
    p.add_argument("--days", type=float, default=3)
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("hook")
    p.add_argument("--mode", choices=("warn", "block"))
    a = ap.parse_args(argv)
    if a.action == "hook":
        return cmd_hook(a)
    if a.action == "list":
        return cmd_list(a)
    return cmd_run(a, dry=a.action == "check")


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"]:  # the hook's fast path: no argument parser
        sys.exit(cmd_hook(type("A", (), {"mode": next((m for m in ("warn", "block") if m in sys.argv[2:]), None)})))
    sys.exit(main())
