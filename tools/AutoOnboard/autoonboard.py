"""New-game onboarding in one resumable command: from a disc image in a game folder to a scaffolded project, an
extracted workspace and a function census (later: symbols, estimate, first translation pass, gaps, boot try), ending
with a generated overview of where the project is and what comes next (docs/NEW_GAME.md, "One command").

usage: py -3.12 tools/AutoOnboard/autoonboard.py --project GAME_DIR [--console dreamcast|saturn|gamecube|xbox360] [--name NAME]
           [--disc IMAGE] [--inputs WS] [--from STAGE | --only STAGE] [--until STAGE] [--dry-run]
           [--here | --host build box B|Build box A --remote-project DIR [--remote-dp DIR]] [--seconds N] [--changed WHAT]
       py -3.12 tools/AutoOnboard/autoonboard.py prove --project GAME_DIR ...   (onboard_prove.py: replay, title, launcher)
Stages, in order: project, machine, disc, extract, census, symbols, boot, register, estimate, translate, gaps, then
  overview. Boot and register come before the long translate pass: the title screen and the launcher first.
  Each writes GAME_DIR/.work/onboard/<stage>.json: status ok | error | not-applicable | stale, numbers, paths, what is
  missing and what would supply it. A run resumes at the first stage without an ok (or not-applicable) verdict;
  --from STAGE runs from there, --only STAGE runs that one (both make the verdicts of it and every later stage stale:
  kept on disk with what they proved, boot's "launch" among them, but run again); --until STAGE stops after it.
  machine (this machine's settings) runs first in every run that reaches a later stage (not with --only), and never
  makes others stale; its error stops no stage that does not need what it lacks;
  each stage needs the verdicts its contract names (onboard_stages.STAGES). --dry-run prints what each would call.
  --changed WHAT names, for tools/jobreg.py, an input the heavy stages' job keys cannot see (a fixed tool, a new limit).
  A missing tool, input or console support is a hard error (exit 2) that names what is missing: no silent skips.
  The first run needs --console; the original Xbox and arcade are refused (no translator target).
  The disc image is the one in GAME_DIR itself (--disc picks another). Xbox 360: a retail-encrypted XEX needs the
  console's retail key on the machine (xex_retail_key), or --inputs WS, a workspace of the same title made where the
  key is (its catalog inputs are copied after an identity check; onboard_x360.py). The overview stage always runs last, also
  after an error: GAME_DIR/.work/onboard/OVERVIEW.md, printed, generated from the verdict files only.
Machines: heavy stages (census, estimate, translate, boot) run only on a build box: --here there (refused on the
  machine machines.local.json "target_machine" names, the user's laptop), or from the main laptop --host build box B with
  the remote copy of the project (--remote-project): every stage after project then runs there in one ssh call
  (tools/AutoOnboard/onboard_remote.py) and its verdicts are copied back here. Before that the remote DecompPipeline is brought to this commit
  (git bundle over scp) and the disc files are copied to the remote project root if missing (--no-sync skips both).
  After an ok remote boot the build it proved (its verdict's "launch": folder, files, arguments) is copied into the
  same folder here (one scp) and Start Game.bat is rewritten to start it with those arguments.

Nearest existing: tools/wave_game.py - resumable steps over a workspace that already has a catalog (prepare, translate,
agents, readable); it starts after this tool's census. tools/game_setup.py - one console's setup steps, with no state,
no hard error per missing piece and no overview. The stages call both instead of repeating them.
"""
import argparse
import json
import os
import posixpath
import pathlib
import shutil
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]  # this folder's modules, then tools/ (wave_game, game_setup, ...)
import onboard_remote  # noqa: E402
import onboard_stages as S  # noqa: E402

DONE = ("ok", "not-applicable")


class Ctx:
    def __init__(self, a, project, console, name):
        self.tool, self.project, self.console, self.name = S.TOOL, project, console, name
        self.disc, self.here, self.host, self.dry = a.disc, a.here, a.host, a.dry_run
        self.inputs = getattr(a, "inputs", None)
        self.changed = getattr(a, "changed", None)  # jobreg --changed for the heavy stages: an input its key cannot see
        self.seconds = getattr(a, "seconds", 2900)  # a stage's own bound stays under it (the translate stage's runs)
        self.late_seconds = getattr(a, "late_seconds", None)  # translate's retry pass limit; 0 holds the pass back
        # Saturn boot's run flags; None (not given) = boot tries its variants and keeps the first that reaches the title
        # (onboard_sat.variants), then reuses it from port/game.json "onboard"
        self.fallback = getattr(a, "fallback", None)  # untranslated code runs in the kit's interpreter, counted
        self.pointers = getattr(a, "pointers", None)  # the translator follows code addresses held as data
        self.overlays = getattr(a, "overlays", None)  # code files loaded at run time get their own translations
        self.t0 = time.time()  # a stage's own time bound counts from the run's start (boot's seed rounds)
        # boot: addresses of untranslated code the game reached in play here (a remote boot gets them from --host)
        self.seeds = [x for x in (getattr(a, "seeds", None) or "").split(",") if x]
        self.verdicts = {}


def state_dir(project):
    return project / S.STATE_DIR


def load(project):
    out = {}
    for name in S.NAMES:
        f = state_dir(project) / f"{name}.json"
        if f.exists():
            try:
                out[name] = json.loads(f.read_text(encoding="utf-8"))
            except ValueError as e:  # never resume past a verdict nobody can read
                out[name] = {"stage": name, "status": "error", "message": f"{f} unreadable ({e}): fix or delete it"}
    return out


def save(project, v):
    d = state_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{v['stage']}.json").write_text(json.dumps(v, indent=1) + "\n", encoding="utf-8", newline="\n")


def drop_from(project, verdicts, name):
    """Makes the verdicts of `name` and every later stage stale: a re-run stage makes them out of date. A stale verdict
    is not done (its stage runs again) but stays on disk with what it proved: boot's "launch" names the build the
    launcher files start until a new boot proves another. Saturn Bomberman, 2026-11-17: --only translate deleted
    boot.json, the bats were rewritten with no --fallback on, and the user's battle had no enemies. A verdict that
    proved nothing (error, partial) is deleted."""
    for later in S.NAMES[S.NAMES.index(name):-1]:
        verdicts.pop(later, None)
        f = state_dir(project) / f"{later}.json"
        try:
            old = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict) and old.get("status") in DONE + ("stale",):
            old.update(status="stale", was=old.get("was") or old.get("status"),
                       stale=f"{S.stamp()}: {name} ran again")
            f.write_text(json.dumps(old, indent=1) + "\n", encoding="utf-8", newline="\n")
        else:
            f.unlink(missing_ok=True)


def verdict(ctx, stage, status, message, **more):
    v = {"stage": stage, "status": status, "console": ctx.console, "name": ctx.name, "at": S.stamp(),
         "machine": S.machine(), "message": message, "numbers": {}, "paths": {}, "missing": [], "supply": "",
         "next": []}
    v.update(more)
    return v


def heavy_refusal(ctx, st):
    if not ctx.here:
        return (f"{st.name} is heavy work ({st.does}): it runs only on a build box. Run it there with --here, or from "
                "the main laptop with --host build box B --remote-project DIR")
    try:
        main_laptop = json.loads((S.TOOL / "machines.local.json").read_text(encoding="utf-8")).get("target_machine")
    except (OSError, ValueError):
        main_laptop = None
    if main_laptop and str(main_laptop).lower() == S.machine().lower():
        return f"--here on {S.machine()}, the user's laptop: heavy work stays off it (use --host)"
    pids = onboard_remote.busy()
    if pids:
        return f"a -benchmark process runs here (pid {', '.join(pids[:5])}): heavy jobs wait for it"
    free = shutil.disk_usage(ctx.project).free / 2**30
    if free < onboard_remote.FREE_GB:
        return f"{free:.1f} GB free under {ctx.project}, under {onboard_remote.FREE_GB} GB"
    return None


def execute(ctx, st):
    t0 = time.time()
    ad = st.adapters.get(ctx.console)
    if ad is None:
        return verdict(ctx, st.name, "error", f"{st.name}: no adapter for {ctx.console} (the stage table lacks it)",
                       missing=[f"onboard_stages.STAGES {st.name} for {ctx.console}"], supply="a DP change")
    if ad.kind == "not-applicable":
        return verdict(ctx, st.name, "not-applicable", ad.reason)
    if ad.kind == "not-built":
        return verdict(ctx, st.name, "error", f"not built yet: {ad.what}", tool=ad.what,
                       missing=[f"the {ctx.console} adapter of stage {st.name}"],
                       supply=f"a later phase of tools/AutoOnboard/autoonboard.py, calling {ad.what}")
    gone = [n for n in st.needs if ctx.verdicts.get(n, {}).get("status") not in DONE]
    if gone:
        return verdict(ctx, st.name, "error", f"{st.name} needs {', '.join(gone)} done first",
                       missing=[f"an ok verdict of {n}" for n in gone], supply=f"autoonboard.py --from {gone[0]}")
    if st.heavy:
        why = heavy_refusal(ctx, st)
        if why:
            return verdict(ctx, st.name, "error", why, missing=["a build box"], supply="--here on build box B or build box A")
    try:
        S.need_tools(ctx, ad.tools)
        got = ad.run(ctx)
    except S.StageError as e:
        return verdict(ctx, st.name, e.status, str(e), missing=e.missing, supply=e.supply, numbers=e.numbers,
                       seconds=round(time.time() - t0, 1), tool="; ".join(ad.calls(ctx)))
    except Exception as e:  # a fault in an adapter or a tool it calls: reported as it is, never guessed past
        return verdict(ctx, st.name, "error", f"{type(e).__name__}: {e}", missing=["a working adapter"],
                       supply="fix tools/AutoOnboard/onboard_stages.py: " + traceback.format_exc().splitlines()[-3].strip(),
                       seconds=round(time.time() - t0, 1))
    return verdict(ctx, st.name, "ok", got["message"], numbers=got["numbers"], paths=got["paths"], next=got["next"],
                   seconds=round(time.time() - t0, 1), tool="; ".join(ad.calls(ctx)),
                   **({"launch": got["launch"]} if "launch" in got else {}))


def plan(verdicts, a):
    """The stages this run runs, in order. machine is left out of the resume point (main puts it first itself), so a
    project made before the stage existed resumes where it was."""
    if a.only:
        return [a.only]
    if a.from_:
        start = a.from_
    else:
        start = next((n for n in S.NAMES[:-1] if n != "machine" and verdicts.get(n, {}).get("status") not in DONE), None)
    names = [] if start is None else S.NAMES[S.NAMES.index(start):-1]
    until = getattr(a, "until", None)
    return names[:names.index(until) + 1] if until in names else names


def symbol_files(ctx, cap=100 * 2**20):
    """The symbol-set files the symbols stage would read here (onboard_symbols.folders), for the build box: files
    directly in each folder, archives left out, at most `cap` bytes in all; none when this machine has no folder."""
    import onboard_symbols
    try:
        dirs = [d for d, _ in onboard_symbols.folders(ctx)]
    except S.StageError:
        return []
    out, total = [], 0
    for d in dirs:
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() not in onboard_symbols.ARCHIVES and total + f.stat().st_size <= cap:
                out.append(f)
                total += f.stat().st_size
    return out


def bring_build(ctx, a, v):
    """After an ok remote boot: the files of the build it proved (v["launch"]) from the build box into the same folder
    here, through a staging folder so a failed copy leaves the old build whole. None, or why not. Dragon Force,
    2026-11-16: nothing did this, the exe was copied by hand and the bat started it without the proven flags."""
    launch = v.get("launch") or {}
    rel, files = launch.get("dir"), launch.get("files") or []
    if not rel or not files:
        return 'the boot verdict names no proven build (no "launch": a build box with an older DecompPipeline?)'
    stage = state_dir(ctx.project) / "fetch"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        err = onboard_remote.fetch(a.host, a.remote_project.replace("\\", "/").rstrip("/") + "/" + rel, files, stage)
        if err:
            return err
        dest = ctx.project / rel
        dest.mkdir(parents=True, exist_ok=True)
        for n in files:
            os.replace(stage / n, dest / n)
        S.place_fonts(ctx.project / "game_src", dest)  # the game's fonts are here already (game_src): beside the build
        cfg = launch.get("config")  # the game.json the build was made from (the boot's menu probe writes confirm and cancel)
        if cfg:
            cfg_dir = posixpath.dirname(cfg)
            err = onboard_remote.fetch(a.host, a.remote_project.replace("\\", "/").rstrip("/") + "/" + cfg_dir,
                                       [posixpath.basename(cfg)], stage / "config")
            if err:
                return err
            (ctx.project / cfg_dir).mkdir(parents=True, exist_ok=True)
            os.replace(stage / "config" / posixpath.basename(cfg), ctx.project / cfg)
    except OSError as e:
        return f"{e} (is the game running from {ctx.project / rel}?)"
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return None


# A partial verdict means "run again to go on" (boot's seed rounds keep their seeds and translations): the run does that
# itself, up to PARTIAL_AGAIN times, each a fresh bound (a remote batch is one more ssh call, under 50 minutes), so one
# command still goes from the disc to the launcher. Saturn Bomberman, 2026-11-19: 28 seeds from the start check's runs,
# the whole image translated again with them, and round 2 would have passed the bound.
PARTIAL_AGAIN = 3


def partial_at(names, lo, hi, verdicts):
    """The index in names[lo:hi] of the first stage whose verdict is partial, else None."""
    return next((k for k in range(lo, hi) if verdicts.get(names[k], {}).get("status") == "partial"), None)


def remote_args(a, names):
    """What the remote run is told to run: exactly `names` (a run of consecutive remote stages). machine is never the
    --from (it would make every later remote verdict stale); the remote run puts it first itself."""
    if a.only:
        return ["--only", a.only]
    rest = [n for n in names if n != "machine"]
    if not rest:
        return ["--only", "machine"]
    return ["--from", rest[0], "--until", names[-1]]


def remote_batch(ctx, a, names, verdicts):
    """A run of consecutive remote stages of the plan in one ssh call; the remote verdicts are copied here."""
    lead = next((n for n in names if n != "machine"), names[0])  # a failure is the stage asked for, not machine's
    try:
        dp = onboard_remote.remote_dp(a.host, a.remote_dp, S.TOOL)
    except LookupError as e:
        v = verdict(ctx, lead, "error", str(e), missing=["the remote DecompPipeline path"], supply=str(e))
        save(ctx.project, v)
        return False
    disc = a.disc
    # the launcher files belong in the local project and the game folder above it, which the remote copy has not got
    hook = getattr(S.BY_NAME["project"].adapters.get(ctx.console), "local", None)
    if hook:
        for m in hook(ctx):
            print(f"onboard: here: made {m}", flush=True)
    if not getattr(a, "no_sync", False):
        got = onboard_remote.sync(a.host, S.TOOL, dp, ctx.project, a.remote_project, a.disc,
                                  S.CONSOLES[ctx.console][2], refs=symbol_files(ctx),
                                  game_src=sorted(f for g in ("*.c", "*.h") + S.GAME_FONTS for f in (ctx.project / "game_src").glob(g))
                                  if (ctx.project / "game_src").is_dir() else None)  # onboard_sat.GAME_SRC_FILES, and the fonts
        if "error" in got:
            save(ctx.project, verdict(ctx, lead, "error", got["error"], missing=got["missing"],
                                      supply=got["supply"], machine=a.host))
            return False
        disc = got["disc"]  # the remote's own path: a local --disc path is never sent as it is
    args = ["--console", ctx.console, "--name", ctx.name] + (["--disc", disc] if disc else [])
    args += ["--inputs", a.inputs] if getattr(a, "inputs", None) else []
    args += ["--changed", a.changed] if getattr(a, "changed", None) else []
    args += remote_args(a, names)
    # the remote autoonboard.py runs under bounded.py at a.seconds; without its own --seconds it took the 2900 s default,
    # its translate bound (2700 s) outlived that wrapper and the kill left no partial verdict (2026-11-16, chunk 2)
    args += ["--seconds", str(a.seconds)]
    args += ["--late-seconds", str(a.late_seconds)] if getattr(a, "late_seconds", None) is not None else []
    for flag in ("fallback", "pointers", "overlays"):  # only those given: else the remote boot chooses (onboard_sat.variants)
        if getattr(a, flag, None):
            args += [f"--{flag}", getattr(a, flag)]
    seeder = getattr(S.BY_NAME["boot"].adapters.get(ctx.console), "local_seeds", None)
    seeds = seeder(ctx) if seeder and "boot" in names else []
    args += ["--seeds", ",".join(seeds)] if seeds else []  # the play logs are here, not on the build box
    res = onboard_remote.run(a.host, dp, a.remote_project, args, a.seconds)
    if "error" in res:
        save(ctx.project, verdict(ctx, lead, "error", res["error"], missing=res["missing"], supply=res["supply"],
                                  machine=a.host))
        return False
    remote = res["verdicts"]
    if remote.get("project", {}).get("status") != "ok":
        p = remote.get("project", {})
        save(ctx.project, verdict(ctx, lead, "error", f"the remote scaffold on {a.host}: {p.get('message')}",
                                  missing=p.get("missing", []), supply=p.get("supply", ""), machine=a.host))
        return False
    ok = True
    for n in names:
        if n in remote:
            v = dict(remote[n], machine=a.host, remote_project=a.remote_project, remote_log=res["log"])
            save(ctx.project, v)
            verdicts[n] = v
            ok = ok and v["status"] in DONE
    b = verdicts.get("boot")
    if "boot" in names and "boot" in remote and b and b.get("status") == "ok":
        err = bring_build(ctx, a, b)
        if err:
            b = dict(b, status="error", message=f"proven on {a.host} but not brought here: {err}. " + b["message"],
                     missing=["the proven build in the local project"], supply="scp from the build box; --only boot again")
            ok = False
        else:
            b = dict(b, here=f"{', '.join(b['launch']['files'])} copied to {ctx.project / b['launch']['dir']}")
            print(f"onboard: here: {b['here']}", flush=True)
        save(ctx.project, b)
        verdicts["boot"] = b
        for m in (hook(ctx) if hook else []):  # the bats now start the proven build with its arguments
            print(f"onboard: here: made {m}", flush=True)
    return ok and res["rc"] == 0


CELL_ITEMS, CELL_VALUE = 6, 24  # the table's numbers cell: short values only, the rest under "Per stage"


def cell(numbers):
    """The table's numbers cell: at most CELL_ITEMS short values (none cut), then "+N more"."""
    short = [f"{k} {x}" for k, x in numbers.items() if x not in (None, "") and len(str(x)) <= CELL_VALUE]
    more = sum(1 for x in numbers.values() if x not in (None, "")) - min(len(short), CELL_ITEMS)
    return ", ".join(short[:CELL_ITEMS]).replace("|", "/") + (f" (+{more} more below)" if more > 0 else "")


def overview(project, verdicts, a):
    """OVERVIEW.md from the verdict files: the stage table (short numbers), each stage's message and numbers in full
    under it (never cut), what is missing, the next steps."""
    rows, missing, nexts, first_bad, per = [], [], [], None, []
    for n in S.NAMES[:-1]:
        v = verdicts.get(n)
        st = v["status"] if v else "pending"
        where = (v or {}).get("machine", "")
        msg = (v or {}).get("message", "")
        rows.append(f"| {n} | {S.PLAIN[n]} | {st} | {where} | {cell((v or {}).get('numbers', {}))} |")
        if v:
            full = "; ".join(f"{k} {x}" for k, x in v.get("numbers", {}).items() if x not in (None, ""))
            per.append(f"- **{n}**: {msg}" + (f" Numbers: {full}." if full else ""))
        if v and st in ("error", "partial"):
            first_bad = first_bad or n
            missing.append(f"- {n}: {msg}. Missing: {'; '.join(v.get('missing') or ['?'])}. "
                           f"Supplied by: {v.get('supply') or '?'}")
        if v and st == "ok":
            nexts += v.get("next", [])
    any_v = next((v for v in verdicts.values() if v.get("console")), {})
    done = [n for n in S.NAMES[:-1] if verdicts.get(n, {}).get("status") in DONE]
    where = (f"stopped at **{first_bad}**: {verdicts[first_bad]['message']}" if first_bad else
             "nothing has failed")
    head = [f"# Onboarding: {any_v.get('name', project.name)} ({any_v.get('console', '?')})", "",
            f"Project `{project}`; generated {S.stamp()} by AutoOnboard (tools/AutoOnboard/autoonboard.py) from `.work/onboard/*.json`. "
            "Not hand-written: run the tool again to refresh it.", "",
            "The tool takes a disc image to a first translation of the game: it sets up the project, checks this machine's "
            "settings, reads the disc, "
            "finds the game's functions, boots it to the title and puts it in the launcher, estimates the work, runs the translator over every function (no AI agents), "
            "lists what it could not translate, and tries to boot the result. A stage is ok, not-applicable (it does "
            "not apply to this console), error (it stopped, and says what is missing), or pending (not run yet).", "",
            f"**Where it stands:** {len(done)} of {len(S.NAMES) - 1} stages done; {where}", "",
            "| stage | what it does | verdict | machine | numbers |", "|---|---|---|---|---|"]
    steps = []
    cmd = f"py -3.12 tools/AutoOnboard/autoonboard.py --project \"{project}\""
    if first_bad:
        steps.append(f"1. Fix {first_bad} (above), then `{cmd}` resumes there.")
    else:
        pend = next((n for n in S.NAMES[:-1] if verdicts.get(n, {}).get("status") not in DONE), None)
        steps.append(f"1. `{cmd}` continues at {pend}." if pend else "1. Every stage is done.")
    later = [n for n in S.NAMES[:-1] if n not in verdicts or verdicts[n]["status"] in ("error", "partial", "stale")]
    built = [n for n in later if any_v.get("console") in S.CONSOLES and
             S.BY_NAME[n].adapters[any_v["console"]].kind == "not-built"]
    if built:
        steps.append(f"2. Not built yet for this console: {', '.join(built)} (later phases of the tool).")
    steps += [f"{i}. {t}" for i, t in enumerate(dict.fromkeys(nexts), len(steps) + 1)]
    text = "\n".join(head + rows + ["", "## Per stage", ""] + (per or ["No stage has run."]) +
                     ["", "## Missing", ""] + (missing or ["Nothing."]) +
                     ["", "## Next steps", ""] + steps) + "\n"
    state_dir(project).mkdir(parents=True, exist_ok=True)
    (state_dir(project) / "OVERVIEW.md").write_text(text, encoding="utf-8", newline="\n")
    save(project, {"stage": "overview", "status": "ok", "at": S.stamp(), "machine": S.machine(),
                   "message": f"{sum(1 for v in verdicts.values() if v['status'] in DONE)} stage(s) done",
                   "numbers": {}, "paths": {"overview": str(state_dir(project) / "OVERVIEW.md")}})
    return text


def dry_run(ctx, names):
    for n in names:
        st = S.BY_NAME[n]
        ad = st.adapters.get(ctx.console)
        have = ctx.verdicts.get(n, {}).get("status", "pending")
        kind = ad.kind if ad else "no adapter"
        where = ("build box" if st.heavy else "here") if not (ctx.host and st.remote) else ctx.host
        print(f"{n:9} [{have}] {kind:14} on {where}: " + ("; ".join(ad.calls(ctx)) if ad and ad.kind != "not-applicable"
                                                          else getattr(ad, "reason", "")))
        print(f"{'':9} needs {', '.join(st.needs) or '-'}; writes {st.writes}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["prove"]:  # the shared proof command (onboard_prove.py): replay, title frame, launcher check
        import onboard_prove
        return onboard_prove.main(argv[1:])
    ap = argparse.ArgumentParser(prog="autoonboard.py", description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--console")
    ap.add_argument("--name")
    ap.add_argument("--disc")
    ap.add_argument("--inputs")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--from", dest="from_", choices=S.NAMES[:-1])
    g.add_argument("--only", choices=S.NAMES[:-1])
    ap.add_argument("--until", choices=S.NAMES[:-1])  # stop after this stage (--until register: title and launcher only)
    ap.add_argument("--dry-run", action="store_true")
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--here", action="store_true")
    m.add_argument("--host", choices=("Build box B", "Build box A"))
    ap.add_argument("--remote-project")
    ap.add_argument("--remote-dp")
    ap.add_argument("--no-sync", action="store_true",  # with --host: leave the remote DecompPipeline and disc as they are
                    help="skip copying DecompPipeline (git bundle) and the disc image to the build box")
    ap.add_argument("--seconds", type=int, default=2900)  # one wait under 50 minutes (the conversation cache)
    # the translate stage's retry pass for functions killed at the first limit: its limit, 0 = no retry pass in this run
    # (held until someone sees how many it covers: Saturn Bomberman's could take hours, 2026-11-16)
    ap.add_argument("--late-seconds", type=int)
    ap.add_argument("--changed")  # jobreg --changed for the heavy stages (CLAUDE.md, "No redundant work")
    # Saturn boot: --fallback off makes a missing function a STUB again (port/saturn/sat_interp.c); --pointers off
    # translates only the list and its seeds, the test of whether the fallback replaces table discovery (own build dirs)
    # Not given: boot tries the variants itself (onboard_sat.variants) and keeps the first that reaches the title
    ap.add_argument("--fallback", choices=("on", "off"))
    ap.add_argument("--pointers", choices=("on", "off"))
    # Saturn boot: --overlays off leaves the overlays (Dragon Force's DF.BIN, FIELD0..7, READY0..7) to the interpreter
    ap.add_argument("--overlays", choices=("on", "off"))
    ap.add_argument("--seeds")  # boot: comma-separated addresses of untranslated code reached in play (sent by --host)
    a = ap.parse_args(argv)
    if a.host and not a.remote_project:
        ap.error("--host needs --remote-project (the project's folder on that machine)")
    project = pathlib.Path(a.project).resolve()
    verdicts = load(project)
    rec = verdicts.get("project", {})
    console = (a.console or rec.get("console") or "").lower()
    name = a.name or rec.get("name") or project.name
    fail = None
    if not console:
        fail = "the first run needs --console (" + ", ".join(S.CONSOLES) + ")"
    elif console in S.REFUSED:
        fail = f"refused: {S.REFUSED[console]}"
    elif console not in S.CONSOLES:
        fail = f"{console}: not a console this tool knows ({', '.join(S.CONSOLES)}; refused: {', '.join(S.REFUSED)})"
    elif rec.get("console") and rec["console"] != console:
        fail = f"{project} was onboarded as {rec['console']}, not {console}"
    ctx = Ctx(a, project, console, name)
    if fail:
        print(f"onboard: {fail}", file=sys.stderr)
        if a.dry_run:
            return 2
        save(project, verdict(ctx, "project", "error", fail, missing=["a console with a DP translator target"],
                              supply="--console " + "|".join(S.CONSOLES)))
        print(overview(project, load(project), a))
        return 2
    names = plan(verdicts, a)
    ctx.verdicts = verdicts
    if a.dry_run:
        dry_run(ctx, names)
        return 0
    if names:
        drop_from(project, verdicts, names[0])
        if not a.only and any(S.NAMES.index(n) > S.NAMES.index("machine") for n in names) and "machine" not in names:
            names = ["machine"] + names  # this machine's settings first, every time (cheap; never makes others stale)
        if names[0] != "project" and verdicts.get("project", {}).get("status") != "ok":
            names = ["project"] + names  # every run stands on the scaffold (a remote copy's first run included)
    rc, i, again = 0, 0, 0
    while i < len(names):
        n = names[i]
        st = S.BY_NAME[n]
        if a.host and st.remote:  # a run of remote stages in one ssh call; a local stage (register) comes back here
            j = i
            while j < len(names) and S.BY_NAME[names[j]].remote:
                j += 1
            ok = remote_batch(ctx, a, names[i:j], verdicts)
            verdicts.update(load(project))
            ctx.verdicts = verdicts
            k = None if ok else partial_at(names, i, j, verdicts)
            if k is not None and again < PARTIAL_AGAIN:
                again += 1
                print(f"onboard: {names[k]}: partial, running it again ({again} of {PARTIAL_AGAIN})", flush=True)
                i = k
                continue
            if not ok:
                rc = 2
                break
            i = j
            continue
        v = execute(ctx, st)
        save(project, v)
        verdicts[n] = v
        print(f"onboard: {n}: {v['status']}: {v['message'][:200]}", flush=True)
        i += 1
        if n == "machine" and v["status"] not in DONE:
            rc = 2  # its verdict says what to set; the stages that do not need it still run (disc, extract)
            continue
        if v["status"] == "partial" and again < PARTIAL_AGAIN:
            again += 1
            print(f"onboard: {n}: partial, running it again ({again} of {PARTIAL_AGAIN})", flush=True)
            ctx.t0 = time.time()  # a fresh time bound for the stage
            i -= 1
            continue
        if v["status"] not in DONE:
            rc = 2
            break
    hook = getattr(S.BY_NAME["project"].adapters.get(console), "local", None)
    if "boot" in names and not a.host and hook:  # a boot run here: the bats start the build it proved
        hook(ctx)
    print(overview(project, load(project), a))
    return rc


if __name__ == "__main__":
    sys.exit(main())
