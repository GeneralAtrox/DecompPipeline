"""The translator stage (docs/TRANSLATOR_STAGE.md): a target's own machine-code-to-C translator writes the raw C of
a function before any agent attempt, and DP's judge, unchanged, decides it.

Where: core/job.investigate, once the harness exists (prepared or reused), after an import or a requeued function's
own C (attempt 0, judged first, as before) and before agent attempt 1.
What: target.json "translator" names the command (a list; placeholders below); the translator itself is part of DP,
in targets/<target>/translator/ (its origin in ORIGIN.md there). config.local.json "translators" ({name: path}) may
point a translator at another folder instead (a development checkout). No entry, no folder, config
"translate_first" false, or a project.json "translator": null: the stage is off and the job runs exactly as before.
Record: a translated attempt is attempt 0 with model "translated" (cost 0, its translator's name and revision, its
seconds), kept in runs/ADDR/translated/ (work.c, check.json); a PASS is a match with no agent run whose ledger row has
"source": "translated" (an agent's match: "agent"; an import's: "import"; a requeued C's: "previous").
On a FAIL: agent attempt 1 starts from the translated C (work.c) with its official result and first differences
(agent.PREVIOUS) and NOTE, unless an import or the function's own earlier C failed with a better score.
Translator gave no C (exit status, timeout): r["translator"] says why; the agent starts as before, from scratch.
A FAIL is kept in r["translated_fail"] under the key of all the translator and the judge read (_fail_key): a later
job of the same function (a requeue, or tools/translate_backlog.py --record's FAIL) on the very same inputs takes it
instead of translating and judging the same C again. A PASS, a tamper, a "-dirty" or unknown revision keep none.
"""
import datetime
import hashlib
import pathlib
import shutil
import subprocess
import sys
import time

from . import busylog, paths, verdict_memo

NO_TRANSLATION = 3  # the translator's exit status for "this function cannot be translated"
SOURCES = {"translated": "translated", "import": "import", "previous": "previous"}  # attempt model -> ledger source
NOTE = ("\nwork.c starts as this function's C written by the target's machine-code translator (no agent wrote it): it "
        "translates every instruction, so most of it is right. Find the instructions behind the differences above "
        "and fix those; keep the rest.\n")
_REVS = {}


def source_of(att):
    """The ledger "source" of a match whose passing attempt is att."""
    return SOURCES.get((att or {}).get("model"), "agent")


def settings(cfg):
    """(name, command list, repo folder, timeout s) of the target's translator, or (None, why) when the stage is off."""
    t = cfg.get("translator")
    if not cfg.get("translate_first", True) or not t:
        return None, "off"
    name = t.get("name")
    repo = (cfg.get("translators") or {}).get(name)
    if not repo and (paths.TARGET / "translator").is_dir():
        repo = paths.TARGET / "translator"  # the target's own translator, part of DP
    if not repo:
        return None, (f"no folder for translator {name!r}: no {paths.TARGET.name}/translator/ in DP and no "
                      f"config.local.json \"translators\": {{\"{name}\": ...}}")
    return (name, list(t["command"]), str(repo), float(t.get("timeout_s", 120))), None


def revision(repo):
    """The translator's git revision (short, "-dirty" with local changes in its folder), else its REVISION file, else
    "unknown". For a translator inside DP that is DP's revision. Once per process and folder."""
    if repo not in _REVS:
        rev = None
        try:
            git = ["git", "-C", repo]
            rev = subprocess.run(git + ["rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
                                 timeout=15).stdout.strip() or None
            if rev and subprocess.run(git + ["status", "--porcelain", "--untracked-files=no", "--", "."],
                                      capture_output=True, text=True, timeout=15).stdout.strip():
                rev += "-dirty"
        except (OSError, subprocess.SubprocessError):
            rev = None
        if not rev:
            f = pathlib.Path(repo) / "REVISION"
            rev = f.read_text(encoding="utf-8").strip() if f.is_file() else "unknown"
        _REVS[repo] = rev
    return _REVS[repo]


def command(cfg, cmd, repo, addr, out):
    """cmd with its placeholders filled: {python} {tool} {target} {repo} {workspace} {binary} {addr} {meta} {packet}
    {target_json} {bodies} {out}."""
    vals = {"python": sys.executable, "tool": str(paths.TOOL), "target": str(paths.TARGET), "repo": repo,
            "workspace": str(paths.ROOT),
            "binary": str(cfg["binary_path"]), "addr": addr, "meta": str(paths.HARNESS / addr / "meta.json"),
            "packet": str(paths.PACKETS / addr), "target_json": str(paths.TARGET / "target.json"),
            "bodies": str(paths.FUNCTION_BODIES), "out": str(out)}
    return [part.format(**vals) for part in cmd]


def translate(cfg, addr, out, stop_flag=None):
    """Run the translator for addr (bounded: its timeout_s, the whole process tree killed past it), writing out.
    Returns {"name", "rev", "ok", "why", "translate_s"}; why says what went wrong when ok is False."""
    st, why = settings(cfg)
    if st is None:
        return {"ok": False, "why": why}
    name, cmd, repo, timeout = st
    info = {"name": name, "rev": revision(repo), "ok": False, "why": None}
    out.unlink(missing_ok=True)
    t0 = time.monotonic()
    try:
        rc, text, reason = paths.run_bounded(command(cfg, cmd, repo, addr, out), timeout, stop_flag=stop_flag)
    except OSError as e:
        return dict(info, why=f"could not start: {e}", translate_s=0.0)
    info["translate_s"] = round(time.monotonic() - t0, 3)
    last = (text.strip().splitlines() or [""])[-1][:300]
    if reason:
        return dict(info, why=f"{reason} after {timeout:.0f} s" if reason == "timeout" else reason)
    if rc != 0:
        return dict(info, why=("no translation" if rc == NO_TRANSLATION else f"translator exited {rc}") + f": {last}")
    if not out.is_file() or not out.read_text(encoding="utf-8", errors="replace").strip():
        return dict(info, why="translator wrote no C")
    return dict(info, ok=True)


def _fail_key(addr, work, rev):
    """The key of a translated FAIL: the translator's revision, every input the judge reads (core/verdict_memo.key:
    the C, the harness, the packet's asm, the judge's code, the config with the binary's sha256) and what the
    translator reads besides (every packet file, the function bodies)."""
    h = hashlib.sha256(f"translated {rev}\n{verdict_memo.key(addr, work, verdict_memo.judge_stamp())}\n".encode())
    for p in [paths.FUNCTION_BODIES, *sorted(q for q in (paths.PACKETS / addr).glob("*") if q.is_file())]:
        h.update(f"{p.name} {paths.sha256_file(p) if p.is_file() else '-'}\n".encode())
    return h.hexdigest()


def _kept_fail(r, addr, rev, work):
    """The attempt of a FAIL this translator gave on the very inputs it would read now, else None."""
    k = r.get("translated_fail") or {}
    if (not k or k.get("rev") != rev or rev == "unknown" or rev.endswith("-dirty") or not work.is_file()
            or k.get("key") != _fail_key(addr, work, rev)):
        return None
    return dict(k["att"], reused=k["att"].get("started"), started=datetime.datetime.now().isoformat(timespec="seconds"),
                duration_s=0.0, judge_s=0.0)


def attempt(o, w, addr, judge):
    """Attempt 0 by the target's translator, judged by judge (core/job.judge: the official check, unchanged); None
    when the stage is off or the translator gave no C (r["translator"] then says why)."""
    from .job import HARNESS_FILES  # (core/job imports this module)
    r = o.rec(addr)
    st, why = settings(o.cfg)
    if st is None:
        r.pop("translator", None)
        return None
    if w.stop_flag.is_set():  # a stop asked for before the translator ran: no attempt, the job records the stop
        return None
    run_dir = paths.RUNS / addr / "translated"
    kept = _kept_fail(r, addr, revision(st[2]), run_dir / "work.c")
    if kept is not None:
        r["translator"] = r["translated_fail"]["translator"]
        o.log(f"[W{w.id}] {addr}: {st[0]} {kept['translator_rev']} failed on these very inputs before "
              f"({kept.get('summary')}): not translated or judged again")
        return kept
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    work_file = run_dir / "work.c"
    w.set_phase(addr, f"translating with {st[0]}")
    harness = lambda: {nm: paths.sha256_file(paths.HARNESS / addr / nm) for nm in HARNESS_FILES}  # noqa: E731
    # the translator is given meta.json's path: as for an agent, a harness changed since the job took its hashes
    # (core/job, before any attempt) is a FAIL; those hashes, not a second read of the same files
    before = r.get("harness_sha256") or harness()
    with busylog.busy(busylog.of(w), "t"):
        info = translate(o.cfg, addr, work_file, w.stop_flag)
    r["translator"] = {k: info.get(k) for k in ("name", "rev", "ok", "why", "translate_s")}
    if not info["ok"]:
        o.log(f"[W{w.id}] {addr}: translator {st[0]} gave no C ({info['why']}); the agent starts from scratch")
        return None
    o.log(f"[W{w.id}] {addr}: translated by {st[0]} {info['rev']} in {info['translate_s']:.2f} s; judging")
    att = {"n": 0, "model": "translated", "translator": st[0], "translator_rev": info["rev"],
           "started": datetime.datetime.now().isoformat(timespec="seconds"), "duration_s": info["translate_s"],
           "cost_usd": 0.0, "work_file": str(work_file), "run_dir": str(run_dir)}
    if harness() != before:
        att["tamper"] = "harness files changed while translating"
    key = _fail_key(addr, work_file, info["rev"])  # before the judge reads its inputs
    t0 = time.monotonic()
    att = judge(o, w, addr, 0, att, work_file, run_dir)
    att["judge_s"] = round(time.monotonic() - t0, 2)
    if att["verdict"] != "PASS" and not att.get("tamper"):
        r["translated_fail"] = {"rev": info["rev"], "key": key, "translator": r["translator"], "att": dict(att)}
    else:
        r.pop("translated_fail", None)
    return att


def seed(atts):
    """Of the failed zero-token attempts 0 (import or earlier C, translated), the one agent attempt 1 starts from:
    the lowest score, the earliest on a tie."""
    atts = [a for a in atts if a]
    return min(atts, key=lambda a: float("inf") if a.get("score") is None else a["score"]) if atts else None
