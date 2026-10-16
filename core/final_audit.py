"""The checks of the goal's final audit (tools/final_audit.py runs them in one process, in this order).

Each check takes an Out (detail lines go to the output file only) and returns (ok, one-line summary).
The function count: N = the catalog's functions inside the game ranges minus the excluded hardware/system ones
(tier "excluded": core/coverage.excluded_functions). Those keep an intent note only (project.json "intent_notes",
default HARDWARE_FUNCTIONS.md): they are never prepared, matched or rewritten, so they are neither missing readable
files nor counted in N, and check_excluded proves each has its note. Catalog functions outside the game ranges (the
SDK and library code the game links) are not goal functions, except the project's extra functions (project.json
"extra_functions", core/scope.py): they are in scope, held to the same standard, and counted apart (check_extras):
open while not matched or without a readable file (the verdict is then INCOMPLETE, never PASS), an error when their
readable file fails. Open extras are not counted by coverage (as functions not rewritten yet).
"""
import collections
import pathlib
import re
import shutil
import sys
import tempfile

from . import paths


class Out:
    def __init__(self, emit):
        self.emit, self.lines = emit, []

    def detail(self, line):
        self.lines.append(str(line))

    def result(self, ok, name, text):
        line = f"{'PASS' if ok is True else 'FAIL' if ok is False else 'INFO'} {name}: {text}"
        self.lines.append(line)
        self.emit(line)


def function_sets(ws):
    """(functions to decompile = game ranges + extras, excluded ones, required ones = game-range functions minus
    excluded (verify's N), matched ones)."""
    from . import coverage, scope
    sc = scope.of(ws)
    game = set(scope.scope_functions(ws))
    excluded = coverage.excluded_functions() & game
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    matched = {a for a, r in led.items() if r.get("status") == "matched" and a in game}
    return game, excluded, {a for a in game - excluded if not sc.is_extra(a)}, matched


def extra_sets(ws):
    """(extra functions, their excluded ones, the matched ones, the open ones = not excluded, no readable file)."""
    from . import readable, scope
    game, excluded, _, matched = function_sets(ws)
    extras = set(scope.of(ws).extras) & game
    files = {p.stem for p in readable.SRC.glob("*.c")}
    return extras, extras & excluded, extras & matched, {a for a in extras - excluded if a not in files}


def check_oracle(cfg, out, recount, workers):
    from . import oracle_fresh
    ok, f = oracle_fresh.report(cfg, recount_too=recount, workers=workers, log=out.detail)
    text = f"{f['matched'] - f['stale']}/{f['matched']} matched harnesses carry today's oracle stamp" + "".join(
        f"; {k} {n}" for k, n in sorted(f["stale_by_kind"].items()))
    if recount:
        text += f"; raw recount {f['recount_passed']}/{f['matched']} pass (catalog matched count {f['matched']})"
    else:
        text += "; raw recount not run (--recount runs it)"
    return ok, text


def check_headers(cfg, out):
    """(ok, text, snapshot or None): the header lint and game.h build of `refine.py header`, on a run-local snapshot
    (the live readable/include/game.h is compared, never written)."""
    from . import header_snapshot
    snap, problems = header_snapshot.take(cfg)
    live = header_snapshot.live_dirs()[0] / "game.h"
    gen = (snap.include / "game.h").read_text(encoding="utf-8")
    if not problems and (not live.exists() or live.read_text(encoding="utf-8") != gen):
        problems.append("readable/include/game.h is not what the names registry generates now: run refine.py header")
    for p in problems:
        out.detail(f"HEADER PROBLEM: {p}")
    if problems:
        snap.release()
        return False, f"{len(problems)} problem(s): " + "; ".join(problems[:3])[:300], None
    out.detail(f"headers OK: snapshot {snap.hash}; no name defined twice, game.h builds at /W4 /WX")
    return True, "headers OK: no name defined twice, game.h builds at /W4 /WX, readable/include/game.h current", snap


def check_verify(cfg, ws, out, workers, sync):
    from . import readable, readable_verify, refine
    game, excluded, required, matched = function_sets(ws)
    files = sorted(p.stem for p in readable.SRC.glob("*.c") if re.fullmatch(r"[0-9a-f]{8}", p.stem))
    logs = []
    final, _ = readable_verify.verify(cfg, files, workers=workers, sync=sync,
                                      log=lambda m: (logs.append(m), out.detail(m)))
    out.verify_final = final  # check_extras reads the extras' verdicts
    xs, x_excl, _, x_open = extra_sets(ws)
    count = collections.Counter(m.split(" ", 1)[0] for m in logs)
    passed = [a for a in required if (final.get(a) or {}).get("verdict") == "PASS"]
    rd = refine.load_ledger()
    missing = sorted(a for a in required if a not in final)
    why = collections.Counter((rd.get(a) or {}).get("status", "no row") for a in missing)
    verdicts = collections.Counter(r.get("verdict") for r in final.values())
    failed = sum(n for v, n in verdicts.items() if v not in ("PASS", "RAW_NOT_MATCHED", "ERROR"))  # judged, not passed
    extra = [a for a in files if a not in required and a not in xs]
    if missing:
        out.detail(f"required functions without a readable file: {len(missing)}: " + " ".join(missing))
    if extra:
        out.detail(f"readable files of functions outside the required set: {' '.join(extra)}")
    n = len(required)
    game, excluded, matched = game - xs, excluded - xs, matched - xs  # N: the game ranges; extras apart
    x_pass = sum(1 for a in xs if (final.get(a) or {}).get("verdict") == "PASS")
    text = (f"{len(passed)}/{n} pass (N = {len(game)} catalog game functions - {len(excluded)} excluded hw/sys; "
            f"{len(matched)} matched); missing files {len(missing)}"
            + (" (" + ", ".join(f"{k} {v}" for k, v in why.most_common()) + ")" if missing else "")
            + f"; failed {failed} (STALE {count['STALE']}, HARNESS_STALE {count['HARNESS_STALE']}), "
              f"FLAKY {count['FLAKY']}, RAW_NOT_MATCHED {verdicts['RAW_NOT_MATCHED']}, errors {verdicts['ERROR']}"
            + (f"; {len(extra)} file(s) outside the required set" if extra else "")
            + (f"; extra functions: {x_pass}/{len(xs - x_excl)} pass, {len(x_open)} open (no readable file)"
               if xs else "")
            + ("" if sync else "; names not synced (--no-sync)"))
    ok = len(passed) == n and not (missing or extra or failed or count["STALE"] or count["HARNESS_STALE"]
                                   or count["FLAKY"] or verdicts["RAW_NOT_MATCHED"] or verdicts["ERROR"])
    return ok, text


def check_coverage(cfg, ws, out):
    from . import coverage, systems
    _, sys_list = systems.load()
    x_open = extra_sets(ws)[3]  # extras not rewritten yet: open work, counted by check_extras
    targets = sorted({x for s in sys_list for x in s["functions"]} - x_open)
    f = {}
    coverage.report(cfg, targets, log=out.detail, facts=f)
    keys = ("functions", "callees", "globals", "overlaps", "unsized", "pointers", "data_functions")
    text = (f"{f['functions']} functions, {f['callees']} callees, {f['globals']} globals without a name or prototype; "
            f"{f['pointers']} pointer constants, {f['data_functions']} functions used as data without a name; "
            f"{f['overlaps']} overlaps; {f['unsized']} unsized (over the {len(targets)} functions of systems.json; "
            f"{f['excluded']} excluded hw/sys among them not counted"
            + (f"; {len(x_open)} open extra function(s) not counted" if x_open else "") + ")")
    return not any(f[k] for k in keys), text


def check_calls(out):
    """Call-site coverage (core/call_coverage): FAIL while a matched function has a call site no vector reaches that
    the reach pass has not tried (its harness predates it: tools/call_coverage.py reach); sites the pass tried are
    listed with its reason and do not fail."""
    from . import call_coverage as cc
    rows, f = cc.report(cc.matched_functions(), log=out.detail)
    return not f["untried"], cc.summary_line(f)


def check_raw_names(ws, out):
    from . import raw_names, readable
    game, _, _, _ = function_sets(ws)
    cur = {p.stem for p in readable.SRC.glob("*.c")} & game
    bad, counts = raw_names.scan(ws, cur)
    fn = {a: {r: n for r, n in m.items() if r.startswith("fn_")} for a, m in bad.items()}
    fn = {a: m for a, m in fn.items() if m}
    for a, m in sorted(bad.items()):
        out.detail(f"raw names in {a}: " + " ".join(f"{r} ({n})" for r, n in sorted(m.items())))
    out.detail(raw_names.unnamed_line(counts))
    globs = sum(1 for m in bad.values() if any(not r.startswith("fn_") for r in m))
    return not fn, (f"{len(fn)} readable file(s) call a named function by its raw fn_ name"
                    + (": " + " ".join(sorted(fn)[:12]) if fn else "")
                    + f"; {globs} file(s) use a raw g_/DAT_ name of a named global; {counts[0]} raw use(s) of "
                      f"{counts[1]} unnamed function(s) (coverage)")


def check_library(cfg, out, workers):
    from . import library, readable
    ok, f = library.build(cfg, paths.BUILD / "library", workers=workers, log=out.detail)
    top = sorted(f["kinds"].items(), key=lambda kv: -kv[1])[:3]
    xs = paths.scope().extras
    x_files = sorted(p.stem for p in readable.SRC.glob("*.c") if p.stem in xs)
    x_bad = [a for a in f["failing"] if a in xs]
    return ok, (f"{f['clean']}/{f['files']} files compile together with game.h at /W4 /WX; {f['archive']} archive and "
                f"{f['declarations']} declaration diagnostic(s)"
                + (f"; extra functions: {len(x_files) - len(x_bad)}/{len(x_files)} files clean" if xs else "")
                + ("; top kinds: " + "; ".join(f"{k[:70]} ({n} files)" for k, n in top) if top else ""))


def check_extras(ws, out):
    """The extra functions (project.json extra_functions): (True, ...) when every one not excluded is matched and its
    readable file passes verify; (None, ...) while some are open (not matched, or no readable file yet): the verdict
    is then INCOMPLETE; (False, ...) when a readable file of one does not pass. Excluded ones keep an intent note."""
    from . import scope
    extras, excl, matched, open_ = extra_sets(ws)
    todo = extras - excl
    final = getattr(out, "verify_final", None)
    unmatched = sorted(todo - matched, key=lambda a: int(a, 16))
    no_file = sorted(open_ & matched, key=lambda a: int(a, 16))
    judged = {a: (final or {}).get(a) for a in todo - open_}
    passed = sorted(a for a, r in judged.items() if (r or {}).get("verdict") == "PASS")
    bad = sorted(a for a, r in judged.items() if r is not None and r.get("verdict") != "PASS")
    unjudged = sorted(a for a, r in judged.items() if r is None)
    listed = scope.listed(paths.PROJECT, ws)
    out.detail(f"extra functions: {len(listed)} listed ({scope.describe(paths.PROJECT)}), {len(extras)} catalog "
               f"functions outside the game ranges, {len(excl)} excluded hw/sys")
    for label, xs in (("open, not matched", unmatched), ("open, matched without a readable file", no_file),
                      ("readable file does not pass", bad), ("readable file not verified", unjudged)):
        if xs:
            out.detail(f"extra functions {label} ({len(xs)}): {' '.join(xs)}")
    text = (f"{len(passed)}/{len(todo)} extra function(s) pass verify; open {len(unmatched) + len(no_file)} "
            f"({len(unmatched)} not matched, {len(no_file)} matched without a readable file); {len(matched)} matched"
            + (f"; {len(excl)} excluded hw/sys" if excl else "")
            + (f"; FAILING {len(bad)}: {' '.join(bad[:8])}" if bad else "")
            + (f"; not verified {len(unjudged)} (verify did not run)" if unjudged else ""))
    if bad or unjudged:
        return False, text
    return (True if len(passed) == len(todo) else None), text


def check_completeness(ws, out):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="final_audit_")) / "COMPLETENESS.md"
    rc, text, reason = paths.run_bounded([sys.executable, str(paths.TOOLS / "completeness.py"), "--project", str(ws),
                                          "--out", str(tmp)], 900)
    for ln in text.splitlines():
        out.detail(ln)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    m = re.search(r"unexplained ([\d,]+) in (\d+) runs \| stale (\d+)", text)
    if reason or rc != 0 or not m:
        return False, f"completeness.py did not finish ({reason or f'exit {rc}'}): " + text.strip()[-200:]
    unexplained, stale = int(m.group(1).replace(",", "")), int(m.group(3))
    return not unexplained and not stale, (f"{unexplained:,} unexplained byte(s) in {m.group(2)} run(s), {stale} stale "
                                           "known_runs.tsv entr(ies)")


def check_doctor(ws, out):
    from . import doctor
    ok, lines, _ = doctor.check(ws)
    for ln in lines:
        out.detail(ln)
    first = [ln.strip() for ln in lines[1:] if ln.strip() and not ln.strip().startswith("note:")][:2]
    return ok, (lines[0].split(": ", 1)[-1] if lines else "?") + "".join(f"; {_clip(x, 160)}" for x in first)


def _clip(text, n):
    """text cut at a word boundary within n characters, marked with '...' when cut."""
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(" ;,(") + " ..."


def check_excluded(ws, out):
    from . import readable
    _, excluded, _, _ = function_sets(ws)
    note = ws / (paths.PROJECT.get("intent_notes") or "HARDWARE_FUNCTIONS.md")
    text = note.read_text(encoding="utf-8", errors="replace").lower() if note.exists() else ""
    unnoted = sorted(a for a in excluded if a not in text)
    rewritten = sorted(a for a in excluded if (readable.SRC / f"{a}.c").exists())
    out.detail(f"excluded hw/sys functions: {' '.join(sorted(excluded))}")
    ok = (note.exists() or not excluded) and not unnoted and not rewritten
    return ok, (f"{len(excluded)} excluded hw/sys function(s), {len(excluded) - len(unnoted)} with an intent note in "
                f"{note.name}" + ("" if note.exists() or not excluded else " (missing)")
                + (f"; no note: {' '.join(unnoted)}" if unnoted else "")
                + (f"; with a readable file: {' '.join(rewritten)}" if rewritten else "")
                + "; not counted in N, never missing files")


def usage(ws, out):
    rc, text, reason = paths.run_bounded([sys.executable, str(paths.TOOLS / "usage_report.py"), "readable",
                                          "--project", str(ws)], 600)
    out.detail("== usage report (tools/usage_report.py readable)")
    for ln in text.splitlines():
        out.detail(ln)
    head = next((ln.replace(str(ws), "").lstrip(": ") for ln in text.splitlines() if "readable attempts" in ln),
                text.strip()[:160])
    return None, (head.strip() if not reason and rc == 0 else f"usage_report.py failed ({reason or rc})")
